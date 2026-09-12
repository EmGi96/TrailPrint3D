"""UI/state bridge helpers for the map picker and puzzle picker web front-ends.

These build/apply state dictionaries (fetch progress chips, element toggles,
settings rows, advanced settings) from/to the Scene property group. They are
kept separate from the generation pipeline modules since they don't run as
part of runGeneration()'s phase sequence.
"""

import bpy  # type: ignore

from .. import constants as const

# ---------------------------------------------------------------------------
# Coloring-element definitions — used by both the OSM prefetch helper and the
# main terrain-element builder.  Tuple layout:
#   (result_key, active_flag_attr, max_size_const, phase_label, fetch_message)
# ---------------------------------------------------------------------------
COLORING_ELEMENTS = [
    (
        "forest",
        "col_fActive",
        const.FOREST_MAXSIZE,
        "Forest",
        "Fetching forest data\u2026",
    ),
    (
        "water",
        lambda t: (
            t.col_wBodiesActive or t.col_wMinorActive or t.col_wMajorActive
        ),
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
        items.append({"key": "landcover", "icon": "L", "label": "Land Cover"})
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
                tp3d.col_wBodiesActive
                or tp3d.col_wMinorActive
                or tp3d.col_wMajorActive
            ) and map_km <= const.WATER_MAXSIZE
            active = water_feats or (
                tp3d.el_oActive == 1 and map_km <= const.COASTLINE_MAXSIZE
            )
            max_size = None
        elif key == "roads":
            from ..props import get_road_active
            from .osm.fetch_utils import (
                allowed_road_tiers,
            )
            from .osm.roads import TIER_TAGS

            _allowed = allowed_road_tiers(map_km)
            active = any(get_road_active(tp3d, t) and t in _allowed for t in TIER_TAGS)
            max_size = None  # mapsize already folded into `active` via allowed_road_tiers
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
_ELEMENT_COMPOSITE_FLAGS = {
    "water": (
        (
            "col_wBodiesActive",
            "col_wMinorActive",
            "col_wMajorActive",
            "el_oActive",
        ),
        "col_wBodiesActive",
    ),
}


def _road_subflags():
    """Road tier ids in display order -- the 'roads' composite's sub-flags.
    Deferred import: see utils.osm.roads.TIER_TAGS."""
    from .osm.roads import TIER_TAGS

    return tuple(TIER_TAGS.keys())


# Bootstrap sub-flag for the 'roads' composite's first-ever toggle-ON (see
# apply_element_toggle's docstring) -- residential roads are the closest
# equivalent to the old scheme's "Small Roads" default.
_ROADS_BOOTSTRAP = "residential"


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


def build_element_toggle_states(tp3d=None):
    """Which of the 10 progress-icon element categories are currently
    toggled on in the scene settings, keyed the same as build_fetch_items /
    progress_win._ICON_MAP.

    Unlike build_fetch_items, this ignores per-category map-size cutoffs
    (const.FOREST_MAXSIZE etc.) -- those only matter once a map_km is known,
    but this is read by the picker pages *before* an area has been drawn, to
    show which elements the current settings would include.
    """
    if tp3d is None:
        tp3d = bpy.context.scene.tp3d
    states = {"elevation": True}
    for key, attr in _ELEMENT_SINGLE_FLAGS.items():
        states[key] = bool(getattr(tp3d, attr))
    for key, (subflags, _) in _ELEMENT_COMPOSITE_FLAGS.items():
        states[key] = any(getattr(tp3d, f) for f in subflags)
    from ..props import any_road_active

    states["roads"] = any_road_active(tp3d)
    return states


def apply_element_toggle(tp3d, key):
    """Flip one element category's enabled state -- applied on Blender's
    main thread from a picker page's element-status chip click (queued by
    picker_server.py's /toggle_element, drained via drain_pending_toggles
    from the calling operator's modal() timer tick; the HTTP server itself
    runs on a background thread and can't safely touch scene data).

    'elevation' has no toggle and is ignored. For the single-flag
    categories this just inverts the one BoolProperty. For the composites
    (water, roads), toggling OFF remembers the exact sub-flag combination in
    a scene custom property before zeroing them, and toggling back ON
    restores that same combination -- so a mix fine-tuned in the N-panel
    (e.g. only Tracks + Footways) survives a quick off/on from the picker
    instead of resetting to some fixed default. First-ever toggle-ON with
    nothing remembered (and nothing already set) falls back to enabling
    just the category's single most common sub-flag.
    """
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
    if any(_get_composite_flag(tp3d, key, f) for f in subflags):
        tp3d[remember_key] = [bool(_get_composite_flag(tp3d, key, f)) for f in subflags]
        for f in subflags:
            _set_composite_flag(tp3d, key, f, False)
    else:
        remembered = tp3d.get(remember_key)
        if remembered and any(remembered):
            for f, v in zip(subflags, remembered):
                _set_composite_flag(tp3d, key, f, bool(v))
        else:
            _set_composite_flag(tp3d, key, bootstrap, True)


# The Settings popup's Map tab -- a small, fixed whitelist of scene fields
# exposed for direct editing from the picker pages (see picker_server.py's
# /update_setting). Keyed by the same name on both sides so
# build_settings_row_state / apply_setting_update stay in sync by
# construction.
_SETTINGS_ROW_FIELDS = {
    "scaleElevation": ("scaleElevation", float),
    "elevationMode": ("elevationMode", str),
    "fixedHeightMM": ("fixedHeightMM", float),
    "pathThickness": ("pathThickness", float),
    "overwritePathElevation": ("overwritePathElevation", bool),
    "objSize": ("objSize", int),
    "resolution": ("num_subdivisions", int),
    "singleColorMode": ("singleColorMode", bool),
    "singleColorModeTolerance": ("tolerance", float),
}


def build_settings_row_state(tp3d=None):
    """Current values of the Settings popup's Map tab fields, for the
    picker pages' SETTINGS_STATE snapshot."""
    if tp3d is None:
        tp3d = bpy.context.scene.tp3d
    return {key: getattr(tp3d, attr) for key, (attr, _) in _SETTINGS_ROW_FIELDS.items()}


def apply_setting_update(tp3d, key, value):
    """Apply one Settings popup Map-tab field update -- applied on Blender's
    main thread from a picker page's field edit, queued by
    picker_server.py's /update_setting and drained via
    drain_pending_settings from the calling operator's modal() timer tick
    (see apply_element_toggle's docstring for why this can't happen
    directly on the HTTP server's own background thread).

    *key* is checked against the _SETTINGS_ROW_FIELDS whitelist -- silently
    ignored if unrecognized -- rather than setattr'ing whatever name the
    page sent, since the request body is client-controlled.
    """
    field = _SETTINGS_ROW_FIELDS.get(key)
    if not field:
        return
    attr, caster = field
    try:
        setattr(tp3d, attr, caster(value))
    except (TypeError, ValueError):
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
    {"key": "colFArea", "attr": "col_fArea", "type": float, "group": "Forest"},
    {"key": "colScrArea", "attr": "col_scrArea", "type": float, "group": "Scree"},
    {"key": "colCArea", "attr": "col_cArea", "type": float, "group": "City Boundaries"},
    {"key": "colGrArea", "attr": "col_grArea", "type": float, "group": "Greenspace"},
    {"key": "colFaArea", "attr": "col_faArea", "type": float, "group": "Farmland"},
    {"key": "colGlArea", "attr": "col_glArea", "type": float, "group": "Glacier"},
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
    {"key": "elSResidentialActive", "road_id": "residential", "type": bool, "group": "Roads"},
    {"key": "elSServiceActive", "road_id": "service", "type": bool, "group": "Roads"},
    {"key": "elSFootwayActive", "road_id": "footway", "type": bool, "group": "Roads"},
    {"key": "elSCycleBridleActive", "road_id": "cycle_bridle", "type": bool, "group": "Roads"},
    {"key": "elSTrackActive", "road_id": "track", "type": bool, "group": "Roads"},
    {"key": "elSPathActive", "road_id": "path", "type": bool, "group": "Roads"},
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
]
_ADVANCED_SETTINGS_BY_KEY = {f["key"]: f for f in _ADVANCED_SETTINGS_FIELDS}
_ATTR_TO_ADVANCED_KEY = {f["attr"]: f["key"] for f in _ADVANCED_SETTINGS_FIELDS if "attr" in f}
_ROAD_ID_TO_ADVANCED_KEY = {f["road_id"]: f["key"] for f in _ADVANCED_SETTINGS_FIELDS if "road_id" in f}


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
    category is off. Mirrors apply_element_toggle's own remember/restore
    logic: the live combo if any sub-flag is currently on, else whatever
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
        result[cat_key] = {_ATTR_TO_ADVANCED_KEY[f]: v for f, v in values.items() if f in _ATTR_TO_ADVANCED_KEY}

    road_ids = _road_subflags()
    current = {r: _get_composite_flag(tp3d, "roads", r) for r in road_ids}
    if any(current.values()):
        road_values = current
    else:
        remembered = tp3d.get("_toggle_remember_roads")
        road_values = (
            {r: bool(v) for r, v in zip(road_ids, remembered)} if remembered else current
        )
    result["roads"] = {
        _ROAD_ID_TO_ADVANCED_KEY[r]: v for r, v in road_values.items() if r in _ROAD_ID_TO_ADVANCED_KEY
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
    return state


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


