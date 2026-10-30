"""UI/state bridge helpers for the map picker and puzzle picker web front-ends.

These build/apply state dictionaries (fetch progress chips, element toggles,
settings rows, advanced settings) from/to the Scene property group. They are
kept separate from the generation pipeline modules since they don't run as
part of runGeneration()'s phase sequence.
"""

import contextlib
import functools
import json
import os

import bpy  # type: ignore

from .. import constants as const


# ---------------------------------------------------------------------------
# Coloring-element definitions — used by both the OSM prefetch helper and the
# main terrain-element builder.  Tuple layout:
#   (result_key, active_flag_attr, max_size_const, phase_label, fetch_message)
# Built per call rather than at import time -- the size limits are user-
# adjustable in the Add-on Preferences.
# ---------------------------------------------------------------------------
def coloring_elements():
    return [
        (
            "forest",
            "col_fActive",
            const.FOREST_MAXSIZE,
            "Forest",
            "Fetching forest data\u2026",
        ),
        (
            "water",
            lambda t: t.col_wBodiesActive or t.col_wMinorActive or t.col_wMajorActive,
            const.WATER_MAXSIZE,
            "Water",
            "Fetching water data\u2026",
        ),
        (
            "scree",
            "col_scrActive",
            const.SCREE_MAXSIZE,
            "Scree",
            "Fetching scree data\u2026",
        ),
        ("city", "col_cActive", const.CITY_MAXSIZE, "City", "Fetching city data\u2026"),
        (
            "greenspace",
            "col_grActive",
            const.GREENSPACE_MAXSIZE,
            "Greenspace",
            "Fetching greenspace data\u2026",
        ),
        (
            "farmland",
            "col_faActive",
            const.FARMLAND_MAXSIZE,
            "Farmland",
            "Fetching farmland data\u2026",
        ),
        (
            "glacier",
            "col_glActive",
            const.GLACIER_MAXSIZE,
            "Glacier",
            "Fetching glacier data\u2026",
        ),
    ]


# ---------------------------------------------------------------------------
# Shared helper: build the fetch-item list for the progress chip strip
# ---------------------------------------------------------------------------


def build_fetch_items(map_km=None):
    """Return the list of fetch-item dicts for the active scene settings."""
    tp3d = bpy.context.scene.tp3d
    if map_km is None:
        map_km = round(tp3d.get("sMapInKm", 0), 1)
    items = [{"key": "elevation", "icon": "E", "label": "Elevation"}]
    if tp3d.elementSource == "WORLDCOVER":
        # WorldCover paints every category off one combined image fetch (see
        # terrain_gen.py's fetch_landcover_thread/paint_terrain_from_landcover)
        # and generation.py's element phase skips all of the OSM defs below
        # entirely when elementSource == "WORLDCOVER" -- so none of them are
        # ever actually fetched here either, regardless of leftover OSM flags.
        if any_landcover_active(tp3d):
            items.append({"key": "landcover", "icon": "L", "label": "Land Cover"})
        return items
    defs = [
        ("forest", "col_fActive", const.FOREST_MAXSIZE, "F", "Forest"),
        ("water", None, const.WATER_MAXSIZE, "W", "Water"),
        ("scree", "col_scrActive", const.SCREE_MAXSIZE, "S", "Scree"),
        ("city", "col_cActive", const.CITY_MAXSIZE, "C", "City"),
        ("greenspace", "col_grActive", const.GREENSPACE_MAXSIZE, "G", "Green"),
        ("farmland", "col_faActive", const.FARMLAND_MAXSIZE, "A", "Farm"),
        ("glacier", "col_glActive", const.GLACIER_MAXSIZE, "I", "Glacr"),
        ("buildings", "el_bActive", const.BUILDINGS_MAXSIZE, "B", "Build"),
        ("roads", None, const.ROADS_MAXSIZE, "R", "Roads"),
    ]
    for key, flag, max_size, icon, label in defs:
        if key == "water":
            water_feats = (
                tp3d.col_wBodiesActive or tp3d.col_wMinorActive or tp3d.col_wMajorActive
            ) and map_km <= const.WATER_MAXSIZE
            active = tp3d.show_water and (
                water_feats
                or (tp3d.el_oActive == 1 and map_km <= const.COASTLINE_MAXSIZE)
            )
            max_size = None
        elif key == "roads":
            from ..props import get_road_active
            from .osm.fetch_utils import (
                allowed_road_tiers,
            )
            from .osm.roads import TIER_TAGS

            _allowed = allowed_road_tiers(map_km)
            active = tp3d.show_roads and any(
                get_road_active(tp3d, t) and t in _allowed for t in TIER_TAGS
            )
            max_size = (
                None  # mapsize already folded into `active` via allowed_road_tiers
            )
        else:
            active = bool(flag and getattr(tp3d, flag, 0) == 1)
        if active and (max_size is None or map_km <= max_size):
            items.append({"key": key, "icon": icon, "label": label})
    return items


# 'water' and 'roads' are each an OR of several independent sub-checkboxes
# with no single master flag on the PropertyGroup; 'elevation' has no
# toggle at all (the base terrain height is always fetched). Shared by
# build_element_toggle_states and apply_element_toggle below so the two
# stay in sync by construction rather than by convention.
# 'water' is an OR of several independent sub-checkboxes with no single
# master flag on the PropertyGroup; 'roads' is the same idea but its 9
# sub-checkboxes live in the road_types CollectionProperty instead of plain
# attributes, so it can't share water's getattr/setattr-based tuple -- see
# _road_subflags/_get_composite_flag/_set_composite_flag below. 'elevation'
# has no toggle at all (the base terrain height is always fetched). Shared
# by build_element_toggle_states and apply_element_toggle below so the two
# stay in sync by construction rather than by convention.
_ELEMENT_SINGLE_FLAGS = {
    "forest": "col_fActive",
    "scree": "col_scrActive",
    "city": "col_cActive",
    "greenspace": "col_grActive",
    "farmland": "col_faActive",
    "glacier": "col_glActive",
    "buildings": "el_bActive",
}

# ESA WorldCover's own element flags -- each a single BoolProperty (unlike
# OSM's 'water', there's no composite/sub-checkbox breakdown per category),
# used instead of _ELEMENT_SINGLE_FLAGS/_ELEMENT_COMPOSITE_FLAGS whenever
# tp3d.elementSource == "WORLDCOVER". Shares 'water'/'forest'/'city'/
# 'greenspace'/'farmland'/'glacier' keys with the OSM side on purpose (same
# on-screen concept, different data source -- the picker page only ever
# shows one set of chips at a time, per ELEMENT_SOURCE) so ELEMENT_ICONS/
# ELEMENT_CARD_LABELS can be reused as-is; 'mountain' is WorldCover-only,
# mirrors panels.py's _LANDCOVER_COLOR_ROWS.
_LANDCOVER_SINGLE_FLAGS = {
    "water": "col_lcWaterActive",
    "forest": "col_lcForestActive",
    "mountain": "col_lcMountainActive",
    "city": "col_lcCityActive",
    "greenspace": "col_lcGreenspaceActive",
    "farmland": "col_lcFarmlandActive",
    "glacier": "col_lcGlacierActive",
}
_ELEMENT_COMPOSITE_FLAGS = {
    "water": (
        (
            "col_wBodiesActive",
            "col_wMinorActive",
            "col_wMajorActive",
            "el_oActive",
        ),
        ("col_wBodiesActive",),
    ),
}

# The N-panel's own master checkbox for each composite -- generation and
# prefetch only use a composite's sub-flags while this is on (e.g.
# props.any_road_active), so the picker's on/off for the category IS this
# checkbox; the sub-flags only pick what's included.
_COMPOSITE_MASTER_FLAGS = {"water": "show_water", "roads": "show_roads"}


def _road_subflags():
    """Road tier ids in display order -- the 'roads' composite's sub-flags.
    Deferred import: see utils.osm.roads.TIER_TAGS."""
    from .osm.roads import TIER_TAGS

    return tuple(TIER_TAGS.keys())


# Bootstrap sub-flags for the 'roads' composite's toggle-ON with nothing
# ticked (see apply_element_toggle's docstring).
_ROADS_BOOTSTRAP = ("major", "minor")


def _get_composite_flag(tp3d, key, subflag):
    if key == "roads":
        from ..props import get_road_active

        return get_road_active(tp3d, subflag)
    return bool(getattr(tp3d, subflag))


def _set_composite_flag(tp3d, key, subflag, value):
    if key == "roads":
        from ..props import set_road_active

        set_road_active(tp3d, subflag, value)
    else:
        setattr(tp3d, subflag, bool(value))


def any_landcover_active(tp3d):
    """At least one ESA WorldCover element is ticked -- with none, the
    land-cover fetch would paint nothing, so it's skipped entirely."""
    return any(getattr(tp3d, attr) for attr in _LANDCOVER_SINGLE_FLAGS.values())


def build_element_toggle_states(tp3d=None):
    """Which of the element categories are currently toggled on in the
    scene settings, keyed the same as build_fetch_items / progress_win._ICON_MAP
    for OSM, or _LANDCOVER_SINGLE_FLAGS's own keys when
    tp3d.elementSource == "WORLDCOVER" -- the picker pages' element-status
    strip and Settings modal Elements tab pick whichever set applies
    (element_status.js's ELEMENT_SOURCE).

    Unlike build_fetch_items, this ignores per-category map-size cutoffs
    (const.FOREST_MAXSIZE etc.) -- those only matter once a map_km is known,
    but this is read by the picker pages *before* an area has been drawn, to
    show which elements the current settings would include.
    """
    if tp3d is None:
        tp3d = bpy.context.scene.tp3d
    states = {"elevation": True}
    if tp3d.elementSource == "WORLDCOVER":
        for key, attr in _LANDCOVER_SINGLE_FLAGS.items():
            states[key] = bool(getattr(tp3d, attr))
        return states
    for key, attr in _ELEMENT_SINGLE_FLAGS.items():
        states[key] = bool(getattr(tp3d, attr))
    # Composites follow their N-panel master checkbox alone -- the sub-flags
    # only pick what's included, so unticking all of them leaves the
    # category on (just with nothing in it), same as in the N-panel.
    for key, attr in _COMPOSITE_MASTER_FLAGS.items():
        states[key] = bool(getattr(tp3d, attr))
    return states


def _saves_generator_settings(apply_update):
    """Persist the settings after each edit a generator page makes -- see
    save_generator_settings."""

    @functools.wraps(apply_update)
    def wrapper(tp3d, *args):
        apply_update(tp3d, *args)
        save_generator_settings(tp3d)

    return wrapper


@_saves_generator_settings
def apply_element_toggle(tp3d, key):
    """Flip one element category's enabled state -- applied on Blender's
    main thread from a picker page's element-status chip click (queued by
    picker_server.py's /toggle_element, drained via drain_pending_toggles
    from the calling operator's modal() timer tick; the HTTP server itself
    runs on a background thread and can't safely touch scene data).

    'elevation' has no toggle and is ignored. For the single-flag
    categories this just inverts the one BoolProperty. For the composites
    (water, roads), the toggle flips the N-panel master checkbox
    (_COMPOSITE_MASTER_FLAGS) and leaves the sub-flags alone, so a mix
    fine-tuned in the N-panel (e.g. only Tracks + Footways) survives a quick
    off/on from the picker. Toggling OFF also remembers that mix in a scene
    custom property (shown greyed out while off, see
    build_composite_remembered_state); toggling ON with no sub-flag ticked
    ticks the category's default sub-flags (Ponds & Lakes for water, Major +
    Minor Roads for roads).

    ESA WorldCover (tp3d.elementSource == "WORLDCOVER") has none of the OSM
    composites -- every _LANDCOVER_SINGLE_FLAGS category is a plain
    BoolProperty, so it's just inverted directly, same as an OSM single flag.
    """
    if tp3d.elementSource == "WORLDCOVER":
        attr = _LANDCOVER_SINGLE_FLAGS.get(key)
        if attr:
            setattr(tp3d, attr, not getattr(tp3d, attr))
        return

    if key in _ELEMENT_SINGLE_FLAGS:
        attr = _ELEMENT_SINGLE_FLAGS[key]
        setattr(tp3d, attr, not getattr(tp3d, attr))
        return

    if key == "roads":
        subflags, bootstrap = _road_subflags(), _ROADS_BOOTSTRAP
    elif key in _ELEMENT_COMPOSITE_FLAGS:
        subflags, bootstrap = _ELEMENT_COMPOSITE_FLAGS[key]
    else:
        return  # 'elevation' or an unrecognized key -- nothing to toggle

    remember_key = f"_toggle_remember_{key}"
    master = _COMPOSITE_MASTER_FLAGS[key]
    any_sub = any(_get_composite_flag(tp3d, key, f) for f in subflags)
    if getattr(tp3d, master):
        # Off = the master only; the sub-flags stay as they are (generation
        # ignores them while the master is off), so the N-panel keeps them.
        if any_sub:
            tp3d[remember_key] = [
                bool(_get_composite_flag(tp3d, key, f)) for f in subflags
            ]
        setattr(tp3d, master, False)
    else:
        setattr(tp3d, master, True)
        if any_sub:
            return
        for f in bootstrap:
            _set_composite_flag(tp3d, key, f, True)


# The Settings popup's Map tab -- a small, fixed whitelist of scene fields
# exposed for direct editing from the picker pages (see picker_server.py's
# /update_setting). Keyed by the same name on both sides so
# build_settings_row_state / apply_setting_update stay in sync by
# construction.
_SETTINGS_ROW_FIELDS = {
    "elementSource": ("elementSource", str),
    "elementMode": ("elementMode", str),
    "scaleElevation": ("scaleElevation", float),
    "elevationMode": ("elevationMode", str),
    "fixedHeightMM": ("fixedHeightMM", float),
    "minThickness": ("minThickness", float),
    "shapeRotation": ("shapeRotation", int),
    "smoothTerrainTop": ("smoothTerrainTop", bool),
    "smoothTerrainStrength": ("smoothTerrainStrength", int),
    "pathThickness": ("pathThickness", float),
    "overwritePathElevation": ("overwritePathElevation", bool),
    "objSize": ("objSize", int),
    "resolution": ("num_subdivisions", int),
    "singleColorMode": ("singleColorMode", bool),
    "singleColorModeHeight": ("singleColorModeHeight", float),
    "singleColorModeTolerance": ("tolerance", float),
    # Map generator pages' Shape Extras "Text Settings" popup
    # (assets/shape_extras.js) -- mirrors panels.py's Shape Extras box.
    "textFont": ("textFont", str),
    "textSize": ("textSize", int),
    "textSizeTitle": ("textSizeTitle", int),
    "titlefield": ("titlefield", str),
    "textfield1": ("textfield1", str),
    "textfield2": ("textfield2", str),
    "textfield3": ("textfield3", str),
    "textfield4": ("textfield4", str),
    "textfield5": ("textfield5", str),
    "textfield6": ("textfield6", str),
    "textfield7": ("textfield7", str),
    "titleIcon": ("titleIcon", str),
    "iconText1": ("iconText1", str),
    "iconText2": ("iconText2", str),
    "iconText3": ("iconText3", str),
    "iconText4": ("iconText4", str),
    "iconText5": ("iconText5", str),
    "iconText6": ("iconText6", str),
    "iconText7": ("iconText7", str),
    "textPlacement": ("textPlacement", bool),
    "plateThickness": ("plateThickness", float),
    "outerBorderSize": ("outerBorderSize", int),
    "plateInsertValue": ("plateInsertValue", float),
    "plateBevel": ("plateBevel", float),
    "shellWallThickness": ("shellWallThickness", float),
    "handleStyle": ("handleStyle", str),
    # Settings popup's Map tab "Paint Trail" and Other tab texture
    # resolution -- mirror panels.py's "6. Appearance" box and the Advanced
    # box. "Paint Roads" sits on the Elements tab's Roads card instead, see
    # _ADVANCED_SETTINGS_FIELDS.
    "texIncludeTrail": ("tex_include_trail", bool),
    "texResolution": ("tex_resolution", int),
    # Settings popup's Other tab, API section -- mirrors panels.py's API box.
    # demFilePath is set by picker_server's /pick_dem_path (OS dialog).
    "api": ("api", str),
    "dataset": ("dataset", str),
    "openTopographyDataset": ("openTopographyDataset", str),
    "demFilePath": ("demFilePath", str),
    "disableCache": ("disableCache", bool),
}


def build_settings_row_state(tp3d=None):
    """Current values of the Settings popup's Map tab fields, for the
    picker pages' SETTINGS_STATE snapshot."""
    if tp3d is None:
        tp3d = bpy.context.scene.tp3d
    from .. import temp

    state = {
        key: getattr(tp3d, attr) for key, (attr, _) in _SETTINGS_ROW_FIELDS.items()
    }
    state["has3mf"] = temp.has3mf
    return state


def _apply_export_folder(value):
    """Folder chosen with a generator page's export-folder button (picker_server's
    /pick_export_folder). Deliberately not in _SETTINGS_ROW_FIELDS: those make
    up SETTINGS_STATE, which the history panel replays on restore -- that
    shouldn't switch where exports go.

    Sets only the Default Export Folder add-on preference, never the scene's
    own Export Path (which, when set, still takes priority -- see
    addon_preferences.get_export_folder). Returns False for anything that
    isn't an existing folder.
    """
    from ..addon_preferences import (
        get_prefs,
    )  # deferred to avoid circular import at load time

    path = str(value or "")
    if not path or not os.path.isdir(path):
        return False
    get_prefs().default_export_folder = os.path.join(
        path, ""
    )  # export.py appends file names directly
    return True


def _redraw_all_areas():
    """Property changes made from a modal timer don't redraw the sidebar on
    their own -- without this it keeps showing the old value (e.g. ESA
    WorldCover after the page switched to OSM) until the mouse hovers it."""
    wm = getattr(bpy.context, "window_manager", None)
    if wm is not None:
        for window in wm.windows:
            for area in window.screen.areas:
                area.tag_redraw()


@_saves_generator_settings
def apply_setting_update(tp3d, key, value):
    """Apply one Settings popup Map-tab field update -- applied on Blender's
    main thread from a picker page's field edit, queued by
    picker_server.py's /update_setting and drained via
    drain_pending_settings from the calling operator's modal() timer tick
    (see apply_element_toggle's docstring for why this can't happen
    directly on the HTTP server's own background thread).

    *key* is checked against the _SETTINGS_ROW_FIELDS whitelist -- silently
    ignored if unrecognized -- rather than setattr'ing whatever name the
    page sent, since the request body is client-controlled. The extra keys
    are 'exportFolder' (see _apply_export_folder) and 'clearCache' (the
    Other tab's Clear Cache button, *value* ignored).
    """
    if key == "exportFolder":
        if not _apply_export_folder(value):
            return
    elif key == "clearCache":
        from .elevation import (
            clear_api_cache,
        )  # deferred to avoid circular import at load time

        clear_api_cache()
        return
    else:
        field = _SETTINGS_ROW_FIELDS.get(key)
        if not field:
            return
        attr, caster = field
        try:
            setattr(tp3d, attr, caster(value))
        except (TypeError, ValueError):
            return
    _redraw_all_areas()


@_saves_generator_settings
def apply_color_update(tp3d, update: dict) -> None:
    """Apply one queued color-style operation on the main thread.

    *tp3d* is ``context.scene.tp3d`` (same convention as
    ``apply_setting_update`` / ``apply_advanced_setting_update``).

    *update* is a dict like picker_server._pending_color_styles entries:
        {'action': 'set',    'key': 'element_color_base', 'value': '#RRGGBB'}
        {'action': 'apply',  'style': 'FOREST' or 'USER:My Palette'}
        {'action': 'save',   'name': 'My Palette'}
        {'action': 'delete', 'style': 'USER:My Palette'}
    """
    from . import color_styles

    # NOTE: no `import bpy` / `tp3d = bpy.context.scene.tp3d` here anymore
    # -- the caller passes tp3d in, matching the other apply_* helpers.

    action = update.get('action')
    if action == 'set':
        key = update.get('key') or ''
        if key.startswith('element_color_'):
            try:
                rgb = color_styles.hex_to_srgb(update.get('value'))
            except ValueError:
                return
            setattr(tp3d, key, color_styles.srgb_to_color(rgb))
    elif action == 'apply':
        style_id = update.get('style')
        if not style_id:
            return
        try:
            colors = color_styles.style_colors(style_id)
        except (OSError, ValueError):
            return
        color_styles.set_scene_colors(tp3d, colors)
        tp3d.color_style = style_id
    elif action == 'save':
        name = update.get('name')
        if not name:
            return
        colors = color_styles.scene_colors(tp3d)
        try:
            tp3d.color_style = color_styles.save_user_style(name, colors)
        except ValueError:
            return
    elif action == 'delete':
        style_id = update.get('style')
        if not style_id:
            return
        try:
            color_styles.delete_user_style(style_id)
        except (OSError, ValueError):
            return
        if tp3d.color_style == style_id:
            tp3d.color_style = color_styles.DEFAULT_STYLE
    # Repaint the scene's materials so the viewport / preview picks up the change.
    try:
        from . import color
        color.refresh_material_colors()
    except (AttributeError, ImportError):
        pass

# The Settings popup's Elements tab -- a much larger whitelist than
# _SETTINGS_ROW_FIELDS/_ELEMENT_SINGLE_FLAGS/_ELEMENT_COMPOSITE_FLAGS,
# exposing the individual sub-checkboxes and per-category thresholds those
# only summarize as a single on/off chip. 'group' matches the
# element-status row's own category labels (informational here -- the
# actual per-card grouping/layout is driven by settings_modal.js's own
# SIMPLE_ELEMENT_FIELDS/COMPOSITE_ELEMENTS tables, keyed the same way so
# the two stay in sync by construction); 'Elevation' covers the one deeper
# option that doesn't fit the Map tab's own fixed set. Field
# labels/min/max/step for rendering live in settings_modal.js (static, so
# no need to round-trip them through Python) -- this table is only the
# key -> attr/type mapping needed to read current values and
# validate+apply incoming updates.
_ADVANCED_SETTINGS_FIELDS = [
    {
        "key": "disableElevationOutlierFix",
        "attr": "disableElevationOutlierFix",
        "type": bool,
        "group": "Elevation",
    },
    {
        "key": "colOsmSmoothing",
        "attr": "col_osmSmoothing",
        "type": float,
        "group": "OSM",
    },
    {
        "key": "toleranceElements",
        "attr": "toleranceElements",
        "type": float,
        "group": "OSM",
    },
    {
        "key": "colWPondsActive",
        "attr": "col_wBodiesActive",
        "type": bool,
        "group": "Water",
    },
    {
        "key": "colWSmallRiversActive",
        "attr": "col_wMinorActive",
        "type": bool,
        "group": "Water",
    },
    {
        "key": "colWBigRiversActive",
        "attr": "col_wMajorActive",
        "type": bool,
        "group": "Water",
    },
    {"key": "elOActive", "attr": "el_oActive", "type": bool, "group": "Water"},
    {"key": "colWArea", "attr": "col_wArea", "type": float, "group": "Water"},
    {
        "key": "colWStreamWidth",
        "attr": "col_wStreamWidth",
        "type": float,
        "group": "Water",
    },
    {
        "key": "elOMinIslandArea",
        "attr": "el_oMinIslandArea",
        "type": float,
        "group": "Water",
    },
    {"key": "elORdpEpsilon", "attr": "el_oRdpEpsilon", "type": float, "group": "Water"},
    {
        "key": "colWFlattenTop",
        "attr": "col_wFlattenTop",
        "type": bool,
        "group": "Water",
    },
    {"key": "colWInsert", "attr": "col_wInsert", "type": float, "group": "Water"},
    {"key": "colFArea", "attr": "col_fArea", "type": float, "group": "Forest"},
    {"key": "colScrArea", "attr": "col_scrArea", "type": float, "group": "Scree"},
    {"key": "colCArea", "attr": "col_cArea", "type": float, "group": "City Boundaries"},
    {"key": "colGrArea", "attr": "col_grArea", "type": float, "group": "Greenspace"},
    {"key": "colFaArea", "attr": "col_faArea", "type": float, "group": "Farmland"},
    {"key": "colGlArea", "attr": "col_glArea", "type": float, "group": "Glacier"},
    {
        "key": "elWcMinFeatureArea",
        "attr": "el_wcMinFeatureArea",
        "type": float,
        "group": "Land Cover",
    },
    {
        "key": "elBHeightMultiplier",
        "attr": "el_bHeightMultiplier",
        "type": float,
        "group": "Buildings",
    },
    {
        "key": "elBMinPrintMM",
        "attr": "el_bMinPrintMM",
        "type": float,
        "group": "Buildings",
    },
    {"key": "elSHighwaysActive", "road_id": "highways", "type": bool, "group": "Roads"},
    {"key": "elSMajorActive", "road_id": "major", "type": bool, "group": "Roads"},
    {"key": "elSMinorActive", "road_id": "minor", "type": bool, "group": "Roads"},
    {
        "key": "elSResidentialActive",
        "road_id": "residential",
        "type": bool,
        "group": "Roads",
    },
    {"key": "elSServiceActive", "road_id": "service", "type": bool, "group": "Roads"},
    {"key": "elSFootwayActive", "road_id": "footway", "type": bool, "group": "Roads"},
    {
        "key": "elSPedestrianActive",
        "road_id": "pedestrian",
        "type": bool,
        "group": "Roads",
    },
    {
        "key": "elSCycleBridleActive",
        "road_id": "cycle_bridle",
        "type": bool,
        "group": "Roads",
    },
    {"key": "elSTrackActive", "road_id": "track", "type": bool, "group": "Roads"},
    {"key": "elSPathActive", "road_id": "path", "type": bool, "group": "Roads"},
    {"key": "elSRacewayActive", "road_id": "raceway", "type": bool, "group": "Roads"},
    {"key": "elSMultiplier", "attr": "el_sMultiplier", "type": float, "group": "Roads"},
    {"key": "elSHeight", "attr": "el_sHeight", "type": float, "group": "Roads"},
    {
        "key": "elSCutTolerance",
        "attr": "el_sCutTolerance",
        "type": float,
        "group": "Roads",
    },
    {
        "key": "elSCutDepth",
        "attr": "el_sCutDepth",
        "type": float,
        "group": "Roads",
    },
    {
        "key": "texIncludeRoads",
        "attr": "tex_include_roads",
        "type": bool,
        "group": "Roads",
    },
]
_ADVANCED_SETTINGS_BY_KEY = {f["key"]: f for f in _ADVANCED_SETTINGS_FIELDS}
_ATTR_TO_ADVANCED_KEY = {
    f["attr"]: f["key"] for f in _ADVANCED_SETTINGS_FIELDS if "attr" in f
}
_ROAD_ID_TO_ADVANCED_KEY = {
    f["road_id"]: f["key"] for f in _ADVANCED_SETTINGS_FIELDS if "road_id" in f
}


def _read_advanced_field(tp3d, field):
    if "road_id" in field:
        from ..props import get_road_active

        return get_road_active(tp3d, field["road_id"])
    return getattr(tp3d, field["attr"])


def _write_advanced_field(tp3d, field, value):
    if "road_id" in field:
        from ..props import set_road_active

        set_road_active(tp3d, field["road_id"], bool(value))
        return
    try:
        setattr(tp3d, field["attr"], field["type"](value))
    except (TypeError, ValueError):
        pass


def build_composite_remembered_state(tp3d=None):
    """Per composite category (water, roads), the sub-flag combination the
    Settings modal should show -- greyed out and unclickable -- while that
    category is off. Uses apply_element_toggle's remembered snapshot: the
    live combo if any sub-flag is currently on, else whatever
    was remembered from the last time it got toggled off via a picker
    chip/card-icon (or all-False if that's never happened). Keyed by the
    same camelCase field keys ADVANCED_SETTINGS_STATE itself uses, so the
    page can look values up directly by a checkbox's own field key.
    """
    if tp3d is None:
        tp3d = bpy.context.scene.tp3d
    result = {}
    for cat_key, (subflags, _bootstrap) in _ELEMENT_COMPOSITE_FLAGS.items():
        current = {f: bool(getattr(tp3d, f)) for f in subflags}
        if any(current.values()):
            values = current
        else:
            remembered = tp3d.get(f"_toggle_remember_{cat_key}")
            values = (
                {f: bool(v) for f, v in zip(subflags, remembered)}
                if remembered
                else current
            )
        result[cat_key] = {
            _ATTR_TO_ADVANCED_KEY[f]: v
            for f, v in values.items()
            if f in _ATTR_TO_ADVANCED_KEY
        }

    road_ids = _road_subflags()
    current = {r: _get_composite_flag(tp3d, "roads", r) for r in road_ids}
    if any(current.values()):
        road_values = current
    else:
        remembered = tp3d.get("_toggle_remember_roads")
        road_values = (
            {r: bool(v) for r, v in zip(road_ids, remembered)}
            if remembered
            else current
        )
    result["roads"] = {
        _ROAD_ID_TO_ADVANCED_KEY[r]: v
        for r, v in road_values.items()
        if r in _ROAD_ID_TO_ADVANCED_KEY
    }
    return result


def build_advanced_settings_state(tp3d=None):
    """Current values for every Advanced Settings popup field, for the
    picker pages' ADVANCED_SETTINGS_STATE snapshot. '_compositeRemembered'
    is a reserved key (no real field uses a leading underscore) carrying
    build_composite_remembered_state's per-category snapshot alongside it."""
    if tp3d is None:
        tp3d = bpy.context.scene.tp3d
    state = {f["key"]: _read_advanced_field(tp3d, f) for f in _ADVANCED_SETTINGS_FIELDS}
    state["_compositeRemembered"] = build_composite_remembered_state(tp3d)
    # Same reserved-key idea: the N-panel's per-category master checkbox
    # (see _COMPOSITE_MASTER_FLAGS), which the page's tp3dCompositeIsActive
    # needs to agree with build_element_toggle_states.
    state["_compositeMaster"] = {
        k: bool(getattr(tp3d, a)) for k, a in _COMPOSITE_MASTER_FLAGS.items()
    }
    return state


@_saves_generator_settings
def apply_advanced_setting_update(tp3d, key, value):
    """Apply one Advanced Settings popup field update -- same main-thread-only
    rationale as apply_setting_update (see its docstring), queued by
    picker_server.py's /update_advanced_setting and drained via
    drain_pending_advanced_settings. *key* is checked against
    _ADVANCED_SETTINGS_FIELDS -- silently ignored if unrecognized.
    """
    field = _ADVANCED_SETTINGS_BY_KEY.get(key)
    if not field:
        return
    _write_advanced_field(tp3d, field, value)


# A generator page's settings otherwise live only in the Scene property group,
# so a new Blender session starts over from the defaults while the page's own
# /save_state blob (the drawn map etc.) survives. Each generator keeps its own
# saved settings, but they all share the one scene: this custom property names
# the generator whose settings the scene currently holds, so reopening that
# generator doesn't undo changes made from the sidebar since.
_GENERATOR_SETTINGS_LOADED = "_generator_settings_loaded"
# Which generator's file edits are saved to, set when a generator opens. Not
# read from the scene property above, which an undo can roll back.
_active_generator = None
_GENERATOR_ELEMENT_ATTRS = (
    *_ELEMENT_SINGLE_FLAGS.values(),
    *_LANDCOVER_SINGLE_FLAGS.values(),
    *_COMPOSITE_MASTER_FLAGS.values(),
)


def build_generator_settings_snapshot(tp3d):
    """Every setting a generator page can edit, as JSON-ready values. Dict
    order is restore order, and it matters: elementSource must come before
    elementMode (its update callback can force elementMode), and colorStyle
    before colors (its update callback overwrites the swatches)."""
    from . import color_styles

    remembered = {}
    for key in _COMPOSITE_MASTER_FLAGS:
        values = tp3d.get(f"_toggle_remember_{key}")
        if values:
            remembered[key] = [bool(v) for v in values]
    return {
        "settings": {
            key: getattr(tp3d, attr) for key, (attr, _) in _SETTINGS_ROW_FIELDS.items()
        },
        "advanced": {
            f["key"]: _read_advanced_field(tp3d, f) for f in _ADVANCED_SETTINGS_FIELDS
        },
        "elements": {attr: getattr(tp3d, attr) for attr in _GENERATOR_ELEMENT_ATTRS},
        "remembered": remembered,
        "colorStyle": tp3d.color_style,
        "colors": color_styles.role_hex_map(color_styles.scene_colors(tp3d)),
        "slicerProfile": tp3d.slicer_profile_name,
    }


def apply_generator_settings_snapshot(tp3d, data):
    """Write a build_generator_settings_snapshot dict back to the scene. A
    value this build can't take (a since-deleted color style or print
    profile, an enum item that was removed) is skipped, not fatal."""
    from . import color_styles

    def assign(attr, value, caster=lambda v: v):
        try:
            setattr(tp3d, attr, caster(value))
        except (TypeError, ValueError):
            pass

    for key, value in data.get("settings", {}).items():
        field = _SETTINGS_ROW_FIELDS.get(key)
        if field:
            assign(field[0], value, field[1])
    for key, value in data.get("advanced", {}).items():
        field = _ADVANCED_SETTINGS_BY_KEY.get(key)
        if field:
            _write_advanced_field(tp3d, field, value)
    for attr, value in data.get("elements", {}).items():
        if attr in _GENERATOR_ELEMENT_ATTRS:
            assign(attr, value)
    for key, values in data.get("remembered", {}).items():
        if key in _COMPOSITE_MASTER_FLAGS:
            tp3d[f"_toggle_remember_{key}"] = [bool(v) for v in values]
    if data.get("colorStyle"):
        assign("color_style", data["colorStyle"])
    if data.get("colors"):
        try:
            color_styles.set_scene_colors(tp3d, color_styles.normalize_colors(data["colors"]))
        except (TypeError, ValueError) as e:
            print(f"[TP3D] Could not restore generator colors: {e}")
    # An explicit Default 3MF Export Profile preference wins over the
    # remembered one; with none set this is the last-used behaviour.
    if data.get("slicerProfile") and _preferred_slicer_profile() is None:
        assign("slicer_profile_name", data["slicerProfile"])


def _generator_settings_path(generator):
    return os.path.join(const.generator_settings_dir, f"{generator}.json")


def save_generator_settings(tp3d):
    """Write the scene's generator settings to the open generator's file, for
    restore_generator_settings in the next session. Written to a temp file
    first, so a crash mid-write can't leave a truncated JSON behind."""
    if _active_generator is None:
        return
    path = _generator_settings_path(_active_generator)
    tmp_path = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(build_generator_settings_snapshot(tp3d), f, indent=1)
        os.replace(tmp_path, path)
    except OSError as e:
        print(f"[TP3D] Failed to write {path}: {e}")


# Marks a scene that has had its starting 3MF export profile applied (or has
# none to apply), so later opens can't undo a sidebar change.
_DEFAULT_SLICER_PROFILE_APPLIED = "_default_slicer_profile_applied"

# Set while the add-on itself writes slicer_profile_name (restoring settings,
# applying the preference), so those writes aren't mistaken for the user's
# pick and saved as the new remembered profile.
_slicer_profile_save_suppressed = False


@contextlib.contextmanager
def _suppress_slicer_profile_save():
    global _slicer_profile_save_suppressed
    previous = _slicer_profile_save_suppressed
    _slicer_profile_save_suppressed = True
    try:
        yield
    finally:
        _slicer_profile_save_suppressed = previous


def slicer_profile_changed(tp3d):
    """Update callback of the sidebar's 3MF Export Profile dropdown: remember
    the pick like a generator page edit does, so the next session starts on it
    (restore_generator_settings / apply_default_slicer_profile)."""
    if not _slicer_profile_save_suppressed:
        save_generator_settings(tp3d)


def _preferred_slicer_profile():
    from .. import addon_preferences

    return addon_preferences.get_default_slicer_profile()


def _remembered_slicer_profile():
    """The profile in the most recently saved generator settings file, or
    None. A new scene has no generator open yet, so the newest file stands in
    for "last used" across the per-generator files."""
    folder = const.generator_settings_dir
    try:
        paths = [
            os.path.join(folder, name)
            for name in os.listdir(folder)
            if name.endswith(".json")
        ]
        paths.sort(key=os.path.getmtime, reverse=True)
    except OSError:
        return None
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        name = data.get("slicerProfile") if isinstance(data, dict) else None
        if isinstance(name, str) and name:
            return name
    return None


def apply_default_slicer_profile(tp3d):
    """Start a new, unsaved scene on its 3MF export profile: the one chosen in
    the Add-on Preferences, or with "Remember last used" the one last picked
    (sidebar or generator page). Runs once per scene and never for a saved
    .blend, whose own profile wins. Left unmarked while the profile isn't
    available yet (the 3MF add-on can load after this first runs) so a later
    call can still apply it."""
    if bpy.data.filepath or tp3d.get(_DEFAULT_SLICER_PROFILE_APPLIED):
        return
    from .. import addon_preferences

    try:
        stored = addon_preferences.get_prefs().default_slicer_profile_id
    except (KeyError, AttributeError):
        return
    name = addon_preferences.get_default_slicer_profile() if stored else _remembered_slicer_profile()
    if name is None:
        if stored:
            return  # chosen profile not available yet
        tp3d[_DEFAULT_SLICER_PROFILE_APPLIED] = True  # nothing remembered
        return
    try:
        with _suppress_slicer_profile_save():
            tp3d.slicer_profile_name = name
    except (TypeError, ValueError):
        return  # remembered profile not available (yet)
    tp3d[_DEFAULT_SLICER_PROFILE_APPLIED] = True


def restore_generator_settings(tp3d, generator):
    """Load *generator*'s last-used settings into the scene -- call from each
    generator operator's invoke(), before its page-load snapshots are built.
    *generator* names its settings file; a free/premium page pair shares one.
    Skipped when the scene already holds this generator's settings, and for a
    saved .blend, whose own settings win."""
    global _active_generator
    _active_generator = generator
    apply_default_slicer_profile(tp3d)
    if bpy.data.filepath or tp3d.get(_GENERATOR_SETTINGS_LOADED) == generator:
        return
    tp3d[_GENERATOR_SETTINGS_LOADED] = generator
    path = _generator_settings_path(generator)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return
    except (OSError, ValueError) as e:
        print(f"[TP3D] Could not read {path}: {e}")
        return
    if isinstance(data, dict):
        with _suppress_slicer_profile_save():
            apply_generator_settings_snapshot(tp3d, data)
        _redraw_all_areas()
