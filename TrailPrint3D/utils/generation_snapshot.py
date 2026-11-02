import json
import re
from dataclasses import dataclass
from typing import Any

from .. import constants as const

_SNAPSHOT_SECTIONS = (
    (
        "Object",
        (
            "Object type",
            "Addon",
            "Version",
            "name",
        ),
    ),
    (
        "Map",
        (
            "Shape",
            "Resolution",
            "objSize",
            "rectangleHeight",
            "ellipseRatio",
            "shapeRotation",
            "Map Size in Km",
            "Map Scale Ratio",
            "latitude",
            "longitude",
            "Horizontal Scale",
            "mapScale",
            "centerx",
            "centery",
        ),
    ),
    (
        "Terrain and Elevation",
        (
            "Elevation Scale",
            "Elevation Range (m)",
            "elevationMode",
            "fixedHeightMM",
            "minThickness",
            "smoothTerrainTop",
            "smoothTerrainStrength",
            "disableElevationOutlierFix",
            "lowestZ",
            "highestZ",
            "AdditionalExtrusion",
            "dataset",
            "openTopographyDataset",
            "api",
            "selfHosted",
            "xTerrainOffset",
            "yTerrainOffset",
        ),
    ),
    (
        "Trail",
        (
            "pathThickness",
            "overwritePathElevation",
            "pathScale",
            "pathVertices",
            "singleColorModeTrail",
            "singleColorModeHeight",
        ),
    ),
    (
        "Map Features",
        (
            "Generate Water",
            "MinWaterSize",
            "Keep Non-Manifold",
            "col_wBodiesActive",
            "col_wMinorActive",
            "col_wMajorActive",
            "col_wArea",
            "col_wStreamWidth",
            "col_wFlattenTop",
            "col_wInsert",
            "col_fActive",
            "col_fArea",
            "col_cActive",
            "col_cArea",
            "col_glActive",
            "col_glArea",
            "col_scrActive",
            "col_scrArea",
            "col_faActive",
            "col_faArea",
            "col_grActive",
            "col_grArea",
            "col_lcForestActive",
            "col_lcGreenspaceActive",
            "col_lcFarmlandActive",
            "col_lcCityActive",
            "col_lcWaterActive",
            "col_lcMountainActive",
            "col_lcGlacierActive",
            "el_bActive",
            "el_sActive",
            "el_sMultiplier",
            "el_Smoothing",
            "el_sHeight",
            "el_sCutTolerance",
            "el_sCutDepth",
            "el_oActive",
            "elementChoice",
            "elementSource",
            "show_water",
            "elementMode",
            "tolerance",
            "toleranceElements",
            "elementModeInset",
            "singleColorMode",
        ),
    ),
    (
        "Area Selection",
        (
            "generation_mode",
            "mapmode",
            "jMapLat",
            "jMapLon",
            "jMapRadius",
            "jMapLat1",
            "jMapLat2",
            "jMapLon1",
            "jMapLon2",
            "indipendendTiles",
            "tileSpacing",
            "MultitileIndex",
            "tileSize",
        ),
    ),
    (
        "Plate, Text, and Shell",
        (
            "plateMode",
            "shapeExtrasActive",
            "plateThickness",
            "plateBevel",
            "plateInsertValue",
            "outerBorderSize",
            "textFont",
            "textSize",
            "text1",
            "text2",
            "text3",
            "textAngle",
            "textLayout",
            "textPlacement",
            "wallThickness",
            "floorThickness",
        ),
    ),
    (
        "Texture",
        (
            "tex_use_texture",
            "tex_include_roads",
            "tex_include_trail",
            "tex_resolution",
        ),
    ),
    (
        "Post Processing",
        (
            "Dovetail",
            "MagnetHoles",
            "BottomMark",
            "cl_thickness",
            "cl_distance",
            "cl_offset",
        ),
    ),
    (
        "Cache and Export",
        (
            "disableCache",
            "ccacheSize",
            "apiRetries",
            "exportformat",
            "disable_auto_export",
            "disable_3mf_export",
            "ExportGroup",
        ),
    ),
)

_SNAPSHOT_HIDDEN_PROPERTIES = frozenset(
    {
        "map_polygon_wkt",
        "outline_wkt",
        "plate_wkt",
        "insert_wkt",
        "shell_outer_wkt",
        "shell_map_wkt",
        "mapVertices",
    }
)


@dataclass(frozen=True)
class GenerationSetting:
    key: str
    value: str | int | float | bool | None


@dataclass(frozen=True)
class GenerationSettingsSection:
    title: str
    settings: tuple[GenerationSetting, ...]


@dataclass(frozen=True)
class GenerationSnapshot:
    """Versioned, display-ready generation metadata persisted on an object."""

    schema_version: int
    object_type: str
    sections: tuple[GenerationSettingsSection, ...]

    @property
    def setting_count(self) -> int:
        return sum(len(section.settings) for section in self.sections)

    @property
    def has_legacy_display_artifacts(self) -> bool:
        return any(
            setting.key == "Version"
            and isinstance(setting.value, str)
            and setting.value.startswith("<bpy id property array")
            for section in self.sections
            for setting in section.settings
        )

    @classmethod
    def from_mapping(cls, source: Any) -> "GenerationSnapshot":
        values = {
            str(key): _snapshot_value(source[key], str(key))
            for key in source.keys()
            if _include_snapshot_property(str(key))
        }
        sections = []
        assigned = set()

        for title, keys in _SNAPSHOT_SECTIONS:
            section_keys = list(keys)
            if title == "Map Features":
                section_keys.extend(
                    sorted(
                        (key for key in values if key.startswith("road_")),
                        key=str.casefold,
                    )
                )
            settings = tuple(
                GenerationSetting(key, values[key])
                for key in section_keys
                if key in values
            )
            if settings:
                sections.append(GenerationSettingsSection(title, settings))
                assigned.update(setting.key for setting in settings)

        other = tuple(
            GenerationSetting(key, values[key])
            for key in sorted(values.keys() - assigned, key=str.casefold)
        )
        if other:
            sections.append(GenerationSettingsSection("Other", other))

        return cls(
            schema_version=const.GENERATION_SNAPSHOT_VERSION,
            object_type=str(values.get("Object type", "")),
            sections=tuple(sections),
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema_version": self.schema_version,
                "object_type": self.object_type,
                "sections": [
                    {
                        "title": section.title,
                        "settings": [
                            {"key": setting.key, "value": setting.value}
                            for setting in section.settings
                        ],
                    }
                    for section in self.sections
                ],
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )

    def to_markdown(self) -> str:
        lines = ["# TrailPrint3D Generation Settings"]
        for section in self.sections:
            lines.extend(("", f"## {_markdown_text(section.title)}"))
            lines.extend(
                f"- **{_markdown_text(format_generation_setting_label(setting.key))}:** "
                f"{_markdown_text(format_generation_setting_value(setting.value))}"
                for setting in section.settings
            )
        return "\n".join(lines)

    @classmethod
    def from_json(cls, value: str) -> "GenerationSnapshot":
        data = json.loads(value)
        if not isinstance(data, dict):
            raise TypeError("Generation snapshot must be a JSON object.")
        if data.get("schema_version") != const.GENERATION_SNAPSHOT_VERSION:
            raise ValueError(
                f"Unsupported generation snapshot version: {data.get('schema_version')!r}"
            )
        raw_sections = data.get("sections")
        if not isinstance(raw_sections, list):
            raise TypeError("Generation snapshot sections must be a list.")

        sections = []
        for raw_section in raw_sections:
            if not isinstance(raw_section, dict):
                raise TypeError("Generation snapshot section must be an object.")
            title = raw_section.get("title")
            raw_settings = raw_section.get("settings")
            if not isinstance(title, str) or not isinstance(raw_settings, list):
                raise TypeError("Generation snapshot section is malformed.")
            settings = []
            for raw_setting in raw_settings:
                if not isinstance(raw_setting, dict):
                    raise TypeError("Generation snapshot setting must be an object.")
                key = raw_setting.get("key")
                setting_value = raw_setting.get("value")
                if not isinstance(key, str) or not _is_snapshot_value(setting_value):
                    raise TypeError("Generation snapshot setting is malformed.")
                settings.append(GenerationSetting(key, setting_value))
            sections.append(GenerationSettingsSection(title, tuple(settings)))

        object_type = data.get("object_type", "")
        if not isinstance(object_type, str):
            raise TypeError("Generation snapshot object type must be a string.")
        return cls(
            schema_version=const.GENERATION_SNAPSHOT_VERSION,
            object_type=object_type,
            sections=tuple(sections),
        )


def format_generation_setting_label(key: str) -> str:
    label = key.replace("_", " ")
    label = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", label)
    return label[:1].upper() + label[1:]


def format_generation_setting_value(
    value: str | int | float | bool | None,
    *,
    enabled: str = "Enabled",
    disabled: str = "Disabled",
    not_set: str = "Not set",
) -> str:
    if isinstance(value, bool):
        return enabled if value else disabled
    if value is None or value == "":
        return not_set
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _include_snapshot_property(key: str) -> bool:
    return (
        not key.startswith("_")
        and key != const.GENERATION_SNAPSHOT_PROPERTY
        and key not in _SNAPSHOT_HIDDEN_PROPERTIES
    )


def _is_snapshot_value(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _snapshot_value(
    value: Any, key: str = ""
) -> str | int | float | bool | None:
    if _is_snapshot_value(value):
        return value
    if key.casefold() == "version":
        try:
            return ".".join(str(part) for part in value)
        except TypeError:
            pass
    return str(value)


def _markdown_text(value: str) -> str:
    return " ".join(value.splitlines()).replace("\\", "\\\\").replace("*", "\\*")
