"""Tests for the OSM "Extras" element group (utils/osm/extras.py).

Covers:
  - the extras table is wired everywhere it has to be (color roles in every
    built-in style, texture material names / raster order, overlap
    priority, ui_state composite + advanced fields, the picker JS mirror)
  - Overpass union query + classification (extras ride on top of the
    classic kinds, tunnels / route relations / yard tracks are left out)
  - line vs. area rules and ribbon widths
  - the "extras" composite chip: toggle, bootstrap, fetch-progress item
  - Greenspace now includes vineyards / orchards (and its cache key moved)
  - end-to-end: a full offline generation (synthetic elevation + synthetic
    Overpass response) in every element mode builds every extra kind with
    its own material, and the line extras come out at Line Width

Run with:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --factory-startup --python-exit-code 1 -P tests/test_osm_extras.py
"""

import math
import os
import re
import sys
import tempfile
import traceback

import bpy  # type: ignore

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_RESOURCES_DIR = os.path.join(os.path.dirname(__file__), "Resources")

if "TrailPrint3D" not in bpy.context.preferences.addons:
    bpy.ops.preferences.addon_enable(module="TrailPrint3D")

from TrailPrint3D import constants as const
from TrailPrint3D.progress import WarningsOverlay
from TrailPrint3D.props import set_road_active
from TrailPrint3D.utils import color_styles, ui_state
from TrailPrint3D.utils import elevation as _elevation_module
from TrailPrint3D.utils.dataclasses import GenerationContext
from TrailPrint3D.utils.generation import elements as _elements
from TrailPrint3D.utils.osm import extras, fetch_group, fetch_utils
from TrailPrint3D.utils.osm.roads import TIER_TAGS

# Synthetic Overpass data must never land in the user's real cache.
_TEMP_DIR = tempfile.TemporaryDirectory()
const.overpass_cache_dir = os.path.join(_TEMP_DIR.name, "overpass")

_passed = 0
_failed = 0


def _run(name, fn):
    global _passed, _failed
    try:
        fn()
        print(f"  PASS  {name}")
        _passed += 1
    except Exception:  # noqa: BLE001 - keep the suite running
        print(f"  FAIL  {name}")
        traceback.print_exc()
        _failed += 1


def _tp3d():
    return bpy.context.scene.tp3d


def _all_extras_off(tp3d):
    tp3d.show_extras = False
    for flag in extras.SUB_FLAGS:
        setattr(tp3d, flag, False)
    tp3d.show_roads = False
    tp3d.el_sRailwayActive = False
    _clear_custom(tp3d)
    # The custom-slot tests set colors -- don't leave a modified palette for
    # whatever test file runs next.
    color_styles.set_scene_colors(tp3d, color_styles.style_colors(tp3d.color_style))


def _clear_custom(tp3d):
    ui_state.apply_custom_osm_update(
        tp3d, {"count": 0, "slots": [dict(ui_state._CUSTOM_OSM_DEFAULTS)] * 5}
    )


def _set_custom(tp3d, slots):
    """slots: list of dicts (name/geometry/tags/...) -> slots 0..n-1 in use."""
    _clear_custom(tp3d)
    for i, values in enumerate(slots):
        ui_state.apply_custom_osm_update(tp3d, {"action": "add"})
        for field, value in values.items():
            ui_state.apply_custom_osm_update(
                tp3d, {"action": "set", "index": i, "field": field, "value": value}
            )


# ---------------------------------------------------------------------------
# Table wiring
# ---------------------------------------------------------------------------


def test_every_kind_has_color_role_in_every_style():
    for kind in extras.EXTRA_KIND_NAMES:
        assert kind in color_styles.ROLE_NAMES, kind
        assert hasattr(_tp3d(), color_styles.ROLE_ATTRS[kind]), kind
        for style, (_label, _desc, colors) in color_styles.BUILT_IN_STYLES.items():
            assert kind in colors, (style, kind)


def test_every_kind_has_texture_material_and_raster_slot():
    for kind in extras.EXTRA_KIND_NAMES:
        assert const._KIND_MATERIAL_NAME[kind] == kind
        assert const._RASTER_ORDER.count(kind) == 1, kind
    order = const._RASTER_ORDER
    # Ski Area lowest, lines above water, roads/trail still on top.
    assert order[0] == "SKIAREA"
    assert order.index("SKISLOPE") > order.index("WATER")
    assert order.index("SKILIFT") < order.index("ROADS")


def test_every_kind_is_in_overlap_priority():
    order = _elements._terrain_priority_order()
    assert len(order) == len(set(order))
    for kind in extras.EXTRA_KIND_NAMES:
        assert kind.lower() in order, kind
    assert order.index("skilift") < order.index("water")
    assert order.index("skislope") < order.index("skiarea")
    assert order.index("forest") < order.index("skiarea")
    assert order[-1] == "ocean"


def test_sub_flags_and_ui_state_composite():
    assert set(extras.SUB_FLAGS) | {extras.RAILWAY_FLAG} == {x.flag for x in extras.EXTRA_KINDS}
    assert extras.RAILWAY_FLAG not in extras.SUB_FLAGS
    flags, bootstrap = ui_state._ELEMENT_COMPOSITE_FLAGS["extras"]
    assert tuple(flags) == extras.SUB_FLAGS
    assert tuple(bootstrap) == extras.BOOTSTRAP_FLAGS
    assert ui_state._COMPOSITE_MASTER_FLAGS["extras"] == extras.MASTER_FLAG
    for flag in extras.SUB_FLAGS:
        assert flag in ui_state._ATTR_TO_ADVANCED_KEY, flag
    for attr in ("el_xLineWidth", "el_xArea", "el_sRailwayWidth"):
        assert attr in ui_state._ATTR_TO_ADVANCED_KEY, attr


def test_picker_js_mirrors_sub_flags():
    root = os.path.join(_REPO_ROOT, "TrailPrint3D", "assets")
    expected = [ui_state._ATTR_TO_ADVANCED_KEY[f] for f in extras.SUB_FLAGS]
    with open(os.path.join(root, "element_status.js"), encoding="utf-8") as fh:
        status_js = fh.read()
    match = re.search(r"extras: \{ subflags: \[([^\]]*)\], bootstrap: \[([^\]]*)\]", status_js)
    assert match, "TP3D_COMPOSITE_FLAGS.extras missing"
    assert re.findall(r"'(\w+)'", match.group(1)) == expected
    assert re.findall(r"'(\w+)'", match.group(2)) == [
        ui_state._ATTR_TO_ADVANCED_KEY[f] for f in extras.BOOTSTRAP_FLAGS
    ]
    assert "['extras', 'Extras']" in status_js
    roads = re.search(r"roads: \{ subflags: \[([^\]]*)\]", status_js)
    assert re.findall(r"'(\w+)'", roads.group(1)) == [
        ui_state._ROAD_ID_TO_ADVANCED_KEY[r] for r in ui_state._road_subflags()
    ]
    with open(os.path.join(root, "settings_modal.js"), encoding="utf-8") as fh:
        modal_js = fh.read()
    block = modal_js[modal_js.index("    extras: {\n        checkboxes"):]
    block = block[: block.index("numberFields")]
    assert re.findall(r"key: '(\w+)'", block) == expected
    assert os.path.exists(os.path.join(root, "prog_extras.svg"))


# ---------------------------------------------------------------------------
# Query + classification
# ---------------------------------------------------------------------------


def _way(wid, tags, nodes=(1, 2)):
    return {"type": "way", "id": wid, "tags": tags, "nodes": list(nodes)}


def test_union_query_contains_extras_and_vineyards():
    settings = fetch_utils.OsmFetchSettings(0, 1, 10.0, {}, False, False, False)
    query = fetch_group._build_union_query(
        46, 7, 46.1, 7.1, ["GREENSPACE", "SKISLOPE", "RAILWAY", "RUNWAY"], settings
    )
    assert 'way["piste:type"="downhill"]' in query
    assert '["tunnel"!~' in query
    assert 'way["aeroway"="runway"]' in query
    assert 'way["landuse"~"^(vineyard|orchard)$"]' in query
    # Not requested -> not queried.
    assert "aerialway" not in query and "golf_course" not in query
    # Only extras requested still yields a query.
    assert fetch_group._build_union_query(46, 7, 46.1, 7.1, ["GOLF"], settings)


def test_classify_extras_on_top_of_classic_kinds():
    track_piste = _way(1, {"highway": "track", "piste:type": "downhill"})
    active = ["STREETS", "SKISLOPE", "SKINORDIC"]
    assert fetch_group._classify_element(track_piste, active) == "STREETS"
    assert extras.classify(track_piste, active) == ["SKISLOPE"]
    assert extras.classify(track_piste, ["STREETS"]) == []


def test_classify_exclusions():
    k = list(extras.EXTRA_KIND_NAMES)
    assert extras.classify(_way(1, {"railway": "rail"}), k) == ["RAILWAY"]
    assert extras.classify(_way(2, {"railway": "rail", "tunnel": "yes"}), k) == []
    assert extras.classify(_way(3, {"railway": "rail", "service": "yard"}), k) == []
    assert extras.classify(_way(4, {"railway": "abandoned"}), k) == []
    route = {"type": "relation", "id": 5, "tags": {"type": "route", "piste:type": "nordic"}}
    assert extras.classify(route, k) == []
    mp = {"type": "relation", "id": 6, "tags": {"type": "multipolygon", "piste:type": "downhill"}}
    assert extras.classify(mp, k) == ["SKISLOPE"]
    assert extras.classify(_way(7, {"aerialway": "chair_lift"}), k) == ["SKILIFT"]
    assert extras.classify(_way(8, {"aerialway": "goods"}), k) == []
    assert extras.classify(_way(9, {"aerialway": "zip_line"}), k) == []
    assert extras.classify(_way(10, {"leisure": "track"}), k) == ["SPORTS"]
    assert extras.classify(_way(11, {"natural": "sand"}), k) == ["BEACH"]
    assert extras.classify({"type": "node", "id": 12, "tags": {"natural": "beach"}}, k) == []


def test_greenspace_classifies_vineyards():
    for landuse in ("vineyard", "orchard"):
        el = _way(1, {"landuse": landuse})
        assert fetch_group._classify_element(el, ["GREENSPACE"]) == "GREENSPACE"


def test_greenspace_cache_key_versioned():
    assert fetch_utils.cache_kind_version("GREENSPACE") != "GREENSPACE"
    assert fetch_utils.cache_kind_version("FOREST") == "FOREST"
    settings = fetch_utils.OsmFetchSettings(0, 1, 10.0, {}, False, False, False)
    bbox = (46.0, 7.0, 46.1, 7.1)
    assert fetch_group._make_cache_path(bbox, "GREENSPACE", settings) != (
        fetch_group._make_cache_path(bbox, "FOREST", settings)
    )


def test_combined_fetch_splits_one_way_into_two_kinds():
    from unittest.mock import patch

    data = {
        "elements": [
            {"type": "node", "id": 1, "lat": 46.0, "lon": 7.0},
            {"type": "node", "id": 2, "lat": 46.01, "lon": 7.0},
            _way(10, {"highway": "track", "piste:type": "downhill"}),
        ]
    }
    settings = fetch_utils.OsmFetchSettings(
        1, 1, 10.0, {t: t == "track" for t in TIER_TAGS}, False, False, False
    )
    with patch.object(fetch_group, "_overpass_request", return_value=data):
        result = fetch_group.fetch_osm_combined(
            (46.0, 7.0, 46.1, 7.1), ["STREETS", "SKISLOPE"], settings=settings
        )
    for kind in ("STREETS", "SKISLOPE"):
        elements = result[kind][0]["elements"]
        assert any(e["id"] == 10 for e in elements if e["type"] == "way"), kind
        assert sum(e["type"] == "node" for e in elements) == 2, kind


# ---------------------------------------------------------------------------
# Geometry rules
# ---------------------------------------------------------------------------


def test_closed_way_rules():
    assert not extras.closed_way_is_area("SKISLOPE", {"piste:type": "downhill"})
    assert extras.closed_way_is_area("SKISLOPE", {"area": "yes"})
    assert extras.closed_way_is_area("BEACH", {})
    assert extras.is_line_way("RAILWAY", {}, closed=True)
    assert not extras.is_line_way("SKISLOPE", {"area": "yes"}, closed=True)
    assert extras.is_line_way("SPORTS", {"leisure": "track"}, closed=False)
    assert not extras.is_line_way("GOLF", {}, closed=True)
    assert not extras.is_line_way("FOREST", {}, closed=False)


def test_ribbon_half_width():
    assert extras.ribbon_half_width("SKISLOPE", {}, 10.0, 0.6) == 0.3
    # 60 m runway at 10 mm/km = 0.6 mm wide -> wider than a 0.4 mm line.
    assert math.isclose(extras.ribbon_half_width("RUNWAY", {"width": "60"}, 10.0, 0.4), 0.3)
    # Never thinner than Line Width.
    assert extras.ribbon_half_width("RUNWAY", {"width": "60"}, 1.0, 0.6) == 0.3
    # Missing / unparseable width -> 45 m default.
    assert math.isclose(extras.ribbon_half_width("RUNWAY", {"width": "wide"}, 100.0, 0.1), 2.25)
    assert math.isclose(extras.ribbon_half_width("RUNWAY", {"width": "50 m"}, 100.0, 0.1), 2.5)


# ---------------------------------------------------------------------------
# Composite chip
# ---------------------------------------------------------------------------


def test_extras_toggle_and_fetch_items():
    from unittest.mock import patch

    tp3d = _tp3d()
    _all_extras_off(tp3d)
    with patch.object(ui_state, "save_generator_settings", lambda _t: None):
        assert ui_state.build_element_toggle_states(tp3d)["extras"] is False
        ui_state.apply_element_toggle(tp3d, "extras")
        assert tp3d.show_extras and tp3d.el_xSkiActive  # bootstrap
        assert ui_state.build_element_toggle_states(tp3d)["extras"] is True
        tp3d.el_xGolfActive = True
        ui_state.apply_element_toggle(tp3d, "extras")  # off: remembers the mix
        assert not tp3d.show_extras and tp3d.el_xGolfActive
        remembered = ui_state.build_composite_remembered_state(tp3d)["extras"]
        assert remembered["elXGolfActive"] and remembered["elXSkiActive"]

    tp3d.show_extras = True
    keys = [i["key"] for i in ui_state.build_fetch_items(map_km=10)]
    assert keys.count("extras") == 1
    assert "extras" not in [i["key"] for i in ui_state.build_fetch_items(map_km=const.EXTRAS_MAXSIZE + 1)]
    tp3d.show_extras = False
    assert "extras" not in [i["key"] for i in ui_state.build_fetch_items(map_km=10)]
    _all_extras_off(tp3d)


def test_railways_are_a_roads_subcategory():
    from unittest.mock import patch

    from TrailPrint3D.props import any_road_active

    tp3d = _tp3d()
    _all_extras_off(tp3d)
    for road_id in TIER_TAGS:
        set_road_active(tp3d, road_id, False)
    assert ui_state._road_subflags()[-1] == extras.RAILWAY_ROAD_ID
    assert extras.chip_key("RAILWAY") == "roads" and extras.chip_key("SKILIFT") == "extras"
    tp3d.el_sRailwayActive = True
    assert not extras.kind_active(tp3d, "RAILWAY")  # Roads master off
    tp3d.show_extras = True
    assert not extras.kind_active(tp3d, "RAILWAY")  # Extras master is not its master
    tp3d.show_extras = False
    tp3d.show_roads = True
    assert extras.kind_active(tp3d, "RAILWAY")
    assert not any_road_active(tp3d)  # no road fetch just for railways
    keys = [i["key"] for i in ui_state.build_fetch_items(map_km=10)]
    assert "roads" in keys and "extras" not in keys
    state = ui_state.build_advanced_settings_state(tp3d)
    assert state["elSRailwayActive"] is True
    with patch.object(ui_state, "save_generator_settings", lambda _t: None):
        ui_state.apply_element_toggle(tp3d, "roads")  # off: remembers railways
        assert not tp3d.show_roads and tp3d.el_sRailwayActive
        assert ui_state.build_composite_remembered_state(tp3d)["roads"]["elSRailwayActive"]
    _all_extras_off(tp3d)


def test_coloring_elements_include_extras():
    tp3d = _tp3d()
    _all_extras_off(tp3d)
    rows = {r[0]: r for r in ui_state.coloring_elements()}
    for kind in extras.EXTRA_KIND_NAMES:
        assert kind.lower() in rows
    assert not rows["skislope"][1](tp3d)
    tp3d.show_extras = True
    tp3d.el_xSkiActive = True
    assert rows["skislope"][1](tp3d) and rows["skilift"][1](tp3d) and rows["skinordic"][1](tp3d)
    assert not rows["railway"][1](tp3d)
    tp3d.show_roads = True
    tp3d.el_sRailwayActive = True
    assert rows["railway"][1](tp3d)
    _all_extras_off(tp3d)


# ---------------------------------------------------------------------------
# Custom OSM elements
# ---------------------------------------------------------------------------


def test_custom_rule_parser():
    from TrailPrint3D.utils.osm import custom_osm as c

    parsed = c.parse_rules("natural=beach\nleisure=pitch, sport=tennis; landuse=vineyard|orchard\nhistoric, ruins!=no")
    assert parsed.ok and not parsed.warning, parsed
    assert len(parsed.rules) == 4
    filters = c.overpass_filters(parsed.rules, "AREA")
    assert 'way["natural"="beach"]' in filters
    assert 'relation["natural"="beach"]["type"="multipolygon"]' in filters
    assert 'way["leisure"="pitch"]["sport"="tennis"]' in filters
    assert 'way["landuse"~"^(vineyard|orchard)$"]' in filters
    assert 'way["historic"]["ruins"!="no"]' in filters
    assert c.overpass_filters(parsed.rules, "LINE") == [f for f in filters if f.startswith("way")]
    assert c.matches(parsed.rules, {"leisure": "pitch", "sport": "tennis"})
    assert not c.matches(parsed.rules, {"leisure": "pitch", "sport": "soccer"})
    assert c.matches(parsed.rules, {"historic": "castle"})  # ruins missing counts as != no
    assert not c.matches(parsed.rules, {"historic": "castle", "ruins": "no"})
    assert c.overpass_filters(c.parse_rules("name=St. Ann").rules, "LINE") == ['way["name"="St. Ann"]']
    assert c.overpass_filters(c.parse_rules("a=x.y|z").rules, "LINE") == ['way["a"~"^(x[.]y|z)$"]']
    # Errors -- including attempts to break out of the quoted filter.
    for bad in ("", "   ", 'natural="beach"', 'natural=beach"];out;', "a b=c",
                "railway!=abandoned", "natural=", "na[me]=x", "a=b\\c"):
        assert c.parse_rules(bad).error, bad
    assert "building" in c.parse_rules("building").warning
    assert not c.parse_rules("building=yes").warning
    assert c.signature(parsed.rules, "AREA") != c.signature(parsed.rules, "LINE")


def test_custom_slot_activation_and_registry():
    tp3d = _tp3d()
    _all_extras_off(tp3d)
    _set_custom(tp3d, [
        {"name": "Parking", "geometry": "AREA", "tags": "amenity=parking", "area": 2.5},
        {"name": "Fences", "geometry": "LINE", "tags": "barrier=fence", "width": 0.9},
        {"name": "Broken", "tags": 'natural="x"'},
    ])
    assert tp3d.custom_osm_count == 3
    assert not extras.kind_active(tp3d, "CUSTOM1")  # Extras master + Custom off
    tp3d.show_extras = True
    tp3d.el_xCustomActive = True
    assert extras.kind_active(tp3d, "CUSTOM1") and extras.kind_active(tp3d, "CUSTOM2")
    assert not extras.kind_active(tp3d, "CUSTOM3")  # invalid tags
    assert not extras.kind_active(tp3d, "CUSTOM4")  # slot not in use
    assert extras.display_label("CUSTOM1") == "Parking"
    assert not extras.is_line_kind("CUSTOM1") and extras.is_line_kind("CUSTOM2")
    assert not extras.open_ways_are_lines("CUSTOM1") and extras.open_ways_are_lines("CUSTOM2")
    assert extras.area_threshold(tp3d, "CUSTOM1") == 2.5
    assert extras.area_threshold(tp3d, "CUSTOM2") == 0.0
    assert math.isclose(extras.line_width(tp3d, "CUSTOM2"), 0.9, rel_tol=1e-6)
    assert extras.overpass_filters(["CUSTOM2"]) == ['way["barrier"="fence"]']
    assert extras.classify(_way(1, {"barrier": "fence"}), ["CUSTOM2"]) == ["CUSTOM2"]
    rel = {"type": "relation", "id": 2, "tags": {"type": "multipolygon", "amenity": "parking"}}
    assert extras.classify(rel, ["CUSTOM1"]) == ["CUSTOM1"]
    assert extras.classify({**rel, "tags": {"amenity": "parking"}}, ["CUSTOM1"]) == []
    keys = [i["key"] for i in ui_state.build_fetch_items(map_km=10)]
    assert keys.count("extras") == 1
    # Line customs rank above water, area customs below it.
    order = _elements._terrain_priority_order()
    assert order.index("custom2") < order.index("water") < order.index("custom1")
    # Cache key follows the rules.
    key_before = fetch_utils.cache_kind_version("CUSTOM1")
    assert key_before.startswith("CUSTOM1#")
    ui_state.apply_custom_osm_update(tp3d, {"action": "set", "index": 0, "field": "tags", "value": "amenity=parking|fuel"})
    assert extras.kind_active(tp3d, "CUSTOM1")
    assert fetch_utils.cache_kind_version("CUSTOM1") != key_before
    # Disabling a slot drops it from the registry.
    ui_state.apply_custom_osm_update(tp3d, {"action": "set", "index": 0, "field": "enabled", "value": False})
    assert not extras.kind_active(tp3d, "CUSTOM1") and extras.custom_def("CUSTOM1") is None
    _all_extras_off(tp3d)


def test_custom_slot_add_remove_and_state():
    tp3d = _tp3d()
    _all_extras_off(tp3d)
    _set_custom(tp3d, [{"name": "A", "tags": "a=1", "color": "#112233"},
                       {"name": "B", "tags": "b=2", "color": "#445566"},
                       {"name": "C", "tags": "c=3", "geometry": "LINE"}])
    state = ui_state.build_custom_osm_state(tp3d)
    assert state["count"] == 3 and len(state["slots"]) == 5
    assert state["slots"][0]["color"] == "#112233"
    ui_state.apply_custom_osm_update(tp3d, {"action": "remove", "index": 0})
    state = ui_state.build_custom_osm_state(tp3d)
    assert state["count"] == 2
    assert [s["name"] for s in state["slots"][:2]] == ["B", "C"]
    assert state["slots"][0]["color"] == "#445566"  # color moves with its slot
    assert state["slots"][1]["geometry"] == "LINE"
    for _ in range(5):
        ui_state.apply_custom_osm_update(tp3d, {"action": "add"})
    assert tp3d.custom_osm_count == 5  # capped
    ui_state.apply_custom_osm_update(tp3d, {"action": "set", "index": 4, "field": "tags", "value": "x="})
    assert ui_state.build_custom_osm_state(tp3d)["slots"][4]["error"]
    # Advanced-settings channel + page state + generator snapshot round trip.
    ui_state.apply_advanced_setting_update(tp3d, "_customOsm", {"action": "set", "index": 1, "field": "name", "value": "Lines"})
    assert ui_state.build_advanced_settings_state(tp3d)["_customOsm"]["slots"][1]["name"] == "Lines"
    snapshot = ui_state.build_generator_settings_snapshot(tp3d)
    _clear_custom(tp3d)
    assert tp3d.custom_osm_count == 0
    ui_state.apply_generator_settings_snapshot(tp3d, snapshot)
    restored = ui_state.build_custom_osm_state(tp3d)
    assert restored["count"] == 5 and restored["slots"][1]["name"] == "Lines"
    assert restored["slots"][0]["name"] == "B"
    _all_extras_off(tp3d)


# ---------------------------------------------------------------------------
# End-to-end generation (offline)
# ---------------------------------------------------------------------------

_original_get_tile_elevation = _elevation_module.get_tile_elevation


def _fake_get_tile_elevation(gen_or_obj, progress_cb=None):
    """Flat-ish synthetic heightfield (same contract as test_model_shape_matrix)."""
    gen = gen_or_obj if isinstance(gen_or_obj, GenerationContext) else None
    obj = gen.runtime.mapObject if gen is not None else gen_or_obj
    world_verts, *_ = _elevation_module.compute_and_store_tile_bounds(obj)
    elevations = [500.0 + 20.0 * math.sin(v.x / 7.0) for v in world_verts]
    tp3d = bpy.context.scene.tp3d
    tp3d["o_verticesMap"] = str(len(obj.data.vertices))
    tp3d.lowestElevation = min(elevations)
    tp3d.highestElevation = max(elevations)
    tp3d.sAdditionalExtrusion = min(elevations)
    diff = max(elevations) - min(elevations)
    if gen is not None:
        gen.runtime.tileVerts = elevations
        gen.runtime.elDiff = diff
    return elevations, diff


def _fake_overpass(query, *_args, **_kwargs):
    """One feature of every extra kind around the tile's center, plus a
    railway tunnel that must be dropped."""
    s, w, n, e = (float(v) for v in re.search(r"\[bbox:([^\]]+)\]", query).group(1).split(","))
    clat, clon = (s + n) / 2, (w + e) / 2
    dlat, dlon = (n - s) / 10, (e - w) / 10
    elements, nid = [], [0]

    def node(lat, lon):
        nid[0] += 1
        elements.append({"type": "node", "id": nid[0], "lat": lat, "lon": lon})
        return nid[0]

    def line(wid, tags, x, y0, y1):
        elements.append(_way(wid, tags, [node(clat + y0 * dlat, clon + x * dlon),
                                         node(clat + y1 * dlat, clon + x * dlon)]))

    def square(wid, tags, x, y, r):
        ids = [node(clat + (y + dy) * dlat, clon + (x + dx) * dlon)
               for dx, dy in ((-r, -r), (r, -r), (r, r), (-r, r))]
        elements.append(_way(wid, tags, ids + [ids[0]]))

    # N-S lines, so a line's printed width is its X extent.
    line(101, {"piste:type": "downhill", "highway": "track"}, -2.0, -1.5, 1.5)
    line(102, {"aerialway": "chair_lift"}, -1.0, -1.5, 1.5)
    line(103, {"piste:type": "nordic"}, 0.0, -1.5, 1.5)
    line(104, {"railway": "rail"}, 1.0, -1.5, 1.5)
    line(105, {"railway": "rail", "tunnel": "yes"}, 1.5, -1.5, 1.5)
    line(106, {"aeroway": "runway", "width": "60"}, 2.5, -1.5, 1.5)
    square(201, {"landuse": "winter_sports"}, -1.0, 0.0, 2.5)
    square(202, {"natural": "beach"}, 1.5, -2.5, 0.4)
    square(203, {"leisure": "golf_course"}, -1.5, -2.5, 0.4)
    square(204, {"leisure": "pitch"}, 0.0, 2.5, 0.4)
    square(205, {"landuse": "vineyard"}, 2.0, 2.5, 0.4)
    square(206, {"amenity": "parking"}, -2.5, 2.5, 0.4)  # custom area slot
    line(107, {"barrier": "fence"}, 3.5, -1.5, 1.5)  # custom line slot
    return {"elements": elements}


def _reset_generation_scene(mode):
    tp3d = _tp3d()
    tp3d.plateModeCache = "NONE"
    tp3d.textLayoutCache = "NONE"
    tp3d.shape = "SQUARE"
    tp3d.shapeExtrasActive = False
    tp3d.objSize = 100
    tp3d.num_subdivisions = 5
    tp3d.scaleElevation = 1.0
    tp3d.elevationMode = "PROPORTIONAL"
    tp3d.singleColorMode = False
    tp3d.elementChoice = True
    tp3d.elementSource = "OSM"
    tp3d.elementMode = mode
    tp3d.disableCache = True
    tp3d.disable_auto_export = True
    tp3d.disable_3mf_export = True
    tp3d.tex_use_texture_choice = False
    tp3d.api = "MAPTERHORN"
    for attr in ("col_fActive", "col_wBodiesActive", "col_wMinorActive", "col_wMajorActive",
                 "col_cActive", "col_scrActive", "col_faActive", "col_glActive", "el_bActive",
                 "el_oActive"):
        setattr(tp3d, attr, False)
    for road_id in TIER_TAGS:
        set_road_active(tp3d, road_id, False)
    tp3d.col_grActive = True  # vineyard square
    tp3d.col_grArea = 0
    tp3d.show_extras = True
    for flag in extras.SUB_FLAGS:
        setattr(tp3d, flag, True)
    tp3d.show_roads = True  # railways only -- every road tier stays off
    tp3d.el_sRailwayActive = True
    tp3d.el_sRailwayWidth = 0.8
    tp3d.el_xLineWidth = 0.6
    tp3d.el_xArea = 0
    _set_custom(tp3d, [
        {"name": "Parking", "geometry": "AREA", "tags": "amenity=parking", "area": 0},
        {"name": "Fences", "geometry": "LINE", "tags": "barrier=fence", "width": 0.7},
    ])
    tp3d.file_path = os.path.join(_RESOURCES_DIR, "3BergeTour.gpx")
    tp3d.trailName = f"extras_{mode.lower()}"
    export_dir = os.path.join(_TEMP_DIR.name, "export")
    os.makedirs(export_dir, exist_ok=True)
    tp3d.export_path = export_dir + os.sep


def _generate(mode):
    from unittest.mock import patch

    from TrailPrint3D.utils.generation.orchestrator import runGeneration

    _reset_generation_scene(mode)
    before = set(bpy.data.objects)
    _elevation_module.get_tile_elevation = _fake_get_tile_elevation
    try:
        with patch.object(fetch_group, "_overpass_request", side_effect=_fake_overpass):
            runGeneration(0)
    finally:
        _elevation_module.get_tile_elevation = _original_get_tile_elevation
    new = [o for o in bpy.data.objects if o not in before]
    errors = [m for m, icon in WarningsOverlay._messages if icon == "error"
              or m == "There was an error, see console for details."]
    return new, errors


def _cleanup(objects):
    for obj in objects:
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except ReferenceError:
            pass


def _check_solid_mode(mode):
    new, errors = _generate(mode)
    try:
        assert not errors, errors
        by_type = {o.get("Object type"): o for o in new if o.type == "MESH"}
        expected = (set(extras.EXTRA_KIND_NAMES) - {"CUSTOM3", "CUSTOM4", "CUSTOM5"}) | {"GREENSPACE"}
        missing = expected - set(by_type)
        assert not missing, f"{mode}: no object for {sorted(missing)}"
        scale = _tp3d().sScaleHor
        for kind in expected:
            obj = by_type[kind]
            assert obj.data.vertices, kind
            assert obj.data.materials[0].name == kind, (kind, obj.data.materials[0].name)
        for kind, line_w in (("SKILIFT", 0.6), ("SKINORDIC", 0.6), ("RAILWAY", 0.8), ("CUSTOM2", 0.7)):
            xs = [(by_type[kind].matrix_world @ v.co).x for v in by_type[kind].data.vertices]
            width = max(xs) - min(xs)
            assert 0.6 * line_w < width < 1.4 * line_w, (mode, kind, width)
        runway = by_type["RUNWAY"]
        xs = [(runway.matrix_world @ v.co).x for v in runway.data.vertices]
        expected_w = max(0.6, 0.060 * scale)
        assert 0.6 * expected_w < max(xs) - min(xs) < 1.5 * expected_w, (max(xs) - min(xs), expected_w)
    finally:
        _cleanup(new)


def test_generation_single_extruder():
    _check_solid_mode("SINGLECOLORMODE_REMESH")


def test_generation_multi_extruder():
    _check_solid_mode("MULTI_EXTRUDER")


def test_generation_paint():
    new, errors = _generate("PAINT")
    try:
        assert not errors, errors
        maps = [o for o in new if o.get("Object type") == "MAP" and o.type == "MESH"]
        assert maps, "no map generated"
        mesh = maps[0].data
        used = {mesh.materials[p.material_index].name for p in mesh.polygons
                if mesh.materials[p.material_index] is not None}
        for kind in (extras.EXTRA_KIND_NAMES - {"CUSTOM3", "CUSTOM4", "CUSTOM5"}) | {"GREENSPACE"}:
            assert kind in used, f"PAINT: {kind} not painted ({sorted(used)})"
    finally:
        _cleanup(new)


for _test in (
    test_every_kind_has_color_role_in_every_style,
    test_every_kind_has_texture_material_and_raster_slot,
    test_every_kind_is_in_overlap_priority,
    test_sub_flags_and_ui_state_composite,
    test_picker_js_mirrors_sub_flags,
    test_union_query_contains_extras_and_vineyards,
    test_classify_extras_on_top_of_classic_kinds,
    test_classify_exclusions,
    test_greenspace_classifies_vineyards,
    test_greenspace_cache_key_versioned,
    test_combined_fetch_splits_one_way_into_two_kinds,
    test_closed_way_rules,
    test_ribbon_half_width,
    test_extras_toggle_and_fetch_items,
    test_railways_are_a_roads_subcategory,
    test_coloring_elements_include_extras,
    test_custom_rule_parser,
    test_custom_slot_activation_and_registry,
    test_custom_slot_add_remove_and_state,
    test_generation_paint,
    test_generation_single_extruder,
    test_generation_multi_extruder,
):
    _run(_test.__name__, _test)

_all_extras_off(_tp3d())
_TEMP_DIR.cleanup()
print(f"\n{_passed} passed, {_failed} failed")
if _failed:
    raise SystemExit(1)
