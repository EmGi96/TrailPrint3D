"""Unit tests for the map generator's "Prefetch Elements" backend.

Covers:
  - exclusions.filter_excluded() — drops excluded ways/relations, drops the
    orphaned skeleton member ways of an excluded relation, keeps ones still
    referenced elsewhere, never mutates its input
  - prefetch.features_from_tiles() — polygons vs lines, relation rings incl.
    holes, skeleton-way skipping, cross-tile de-duplication
  - prefetch.tile_bbox() — matches the drawn bounds for a rectangle, grows
    with shapeRotation, and is symmetric/square for the aspect-locked shapes
  - bbox_snap — the generator's float32-jittered tile bbox resolves to the
    same Overpass cache file the prefetch wrote

Run with:
  blender --background --factory-startup --python-exit-code 1 -P tests/test_prefetch.py
  or, as part of the full suite:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --factory-startup --python-exit-code 1 -P tests/run_all_tests.py
"""

import copy
import os
import sys
import traceback

import bpy  # type: ignore  — provided by Blender's Python

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

if "TrailPrint3D" not in bpy.context.preferences.addons:
    bpy.ops.preferences.addon_enable(module="TrailPrint3D")

from TrailPrint3D.utils.osm import bbox_snap, exclusions, prefetch

_passed = 0
_failed = 0


def _run(name, fn):
    global _passed, _failed
    try:
        fn()
        print(f"  PASS  {name}")
        _passed += 1
    except Exception as e:  # noqa: BLE001 - wide exception needed to keep test runner going
        print(f"  FAIL  {name} - exception occurred: {e}")
        traceback.print_exc()
        _failed += 1


def _assert_all_passed():
    print(f"\n{'='*60}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"{'='*60}\n")
    if _failed:
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Synthetic Overpass data
# ---------------------------------------------------------------------------

def _node(nid, lat, lon):
    return {"type": "node", "id": nid, "lat": lat, "lon": lon}


def _square_nodes(base_id, lat, lon, size):
    return [
        _node(base_id, lat, lon),
        _node(base_id + 1, lat, lon + size),
        _node(base_id + 2, lat + size, lon + size),
        _node(base_id + 3, lat + size, lon),
    ]


def _ring_way(way_id, base_id, tags=None):
    way = {"type": "way", "id": way_id, "nodes": [base_id, base_id + 1, base_id + 2, base_id + 3, base_id]}
    if tags is not None:
        way["tags"] = tags
    return way


def _sample_data():
    """A tagged forest way, a road way, and a relation (outer + hole) whose
    member ways are untagged skeleton ways."""
    elements = []
    elements += _square_nodes(1, 10.0, 20.0, 0.01)      # forest way ring
    elements += _square_nodes(11, 10.1, 20.0, 0.02)     # relation outer
    elements += _square_nodes(21, 10.105, 20.005, 0.01) # relation hole
    elements += [_node(31, 10.0, 20.5), _node(32, 10.01, 20.51)]  # road
    elements.append(_ring_way(100, 1, {"natural": "wood", "name": "Mini Wood"}))
    elements.append(_ring_way(201, 11))
    elements.append(_ring_way(202, 21))
    elements.append({"type": "way", "id": 300, "nodes": [31, 32], "tags": {"highway": "primary"}})
    elements.append({
        "type": "relation", "id": 500,
        "members": [
            {"type": "way", "ref": 201, "role": "outer"},
            {"type": "way", "ref": 202, "role": "inner"},
        ],
        "tags": {"natural": "wood"},
    })
    return {"elements": elements}


def _ids(data):
    return {f"{e['type']}/{e['id']}" for e in data["elements"] if e["type"] != "node"}


# ---------------------------------------------------------------------------
# exclusions.filter_excluded
# ---------------------------------------------------------------------------

def test_filter_no_exclusions_returns_same_object():
    data = _sample_data()
    assert exclusions.filter_excluded(data, frozenset()) is data


def test_filter_drops_excluded_way():
    filtered = exclusions.filter_excluded(_sample_data(), {"way/100"})
    assert "way/100" not in _ids(filtered)
    assert {"way/300", "relation/500", "way/201", "way/202"} <= _ids(filtered)


def test_filter_drops_relation_and_orphaned_skeleton_members():
    filtered = exclusions.filter_excluded(_sample_data(), {"relation/500"})
    remaining = _ids(filtered)
    assert "relation/500" not in remaining
    assert "way/201" not in remaining and "way/202" not in remaining
    assert {"way/100", "way/300"} <= remaining


def test_filter_keeps_member_way_shared_with_surviving_relation():
    data = _sample_data()
    data["elements"].append({
        "type": "relation", "id": 501,
        "members": [{"type": "way", "ref": 201, "role": "outer"}],
        "tags": {"natural": "wood"},
    })
    remaining = _ids(exclusions.filter_excluded(data, {"relation/500"}))
    assert "way/201" in remaining      # still used by relation/501
    assert "way/202" not in remaining  # only relation/500 used it


def test_filter_keeps_tagged_member_way():
    data = _sample_data()
    for el in data["elements"]:
        if el.get("id") == 201:
            el["tags"] = {"natural": "wood"}
    remaining = _ids(exclusions.filter_excluded(data, {"relation/500"}))
    assert "way/201" in remaining and "way/202" not in remaining


def test_filter_does_not_mutate_input():
    data = _sample_data()
    before = copy.deepcopy(data)
    exclusions.filter_excluded(data, {"way/100", "relation/500"})
    assert data == before


def test_set_and_clear_excluded_global():
    exclusions.set_excluded(["way/100"])
    try:
        assert "way/100" not in _ids(exclusions.filter_excluded(_sample_data()))
    finally:
        exclusions.clear_excluded()
    assert exclusions.get_excluded() == frozenset()
    assert "way/100" in _ids(exclusions.filter_excluded(_sample_data()))


# ---------------------------------------------------------------------------
# prefetch.features_from_tiles
# ---------------------------------------------------------------------------

_BBOX = (10.0, 20.0, 12.0, 22.0)


def _tiles(kind, data=None):
    return {kind: {_BBOX: (data or _sample_data(), True)}}


def test_features_polygon_and_relation():
    features, truncated = prefetch.features_from_tiles(_tiles("FOREST"))
    by_id = {f["id"]: f for f in features}
    assert not truncated
    assert "way/100" in by_id and "relation/500" in by_id
    assert by_id["way/100"]["name"] == "Mini Wood" and not by_id["way/100"]["line"]
    assert len(by_id["relation/500"]["rings"]) == 2  # outer + hole in ONE feature


def test_features_skip_skeleton_member_ways():
    ids = {f["id"] for f in prefetch.features_from_tiles(_tiles("FOREST"))[0]}
    assert "way/201" not in ids and "way/202" not in ids


def test_features_open_way_is_line_only_for_line_kinds():
    streets = prefetch.features_from_tiles(_tiles("STREETS"))[0]
    road = next(f for f in streets if f["id"] == "way/300")
    assert road["line"] and len(road["rings"][0]) == 2
    forest_ids = {f["id"] for f in prefetch.features_from_tiles(_tiles("FOREST"))[0]}
    assert "way/300" not in forest_ids  # unclosed non-line way can't be a polygon


def test_features_road_gets_tier_sub():
    streets = prefetch.features_from_tiles(_tiles("STREETS"))[0]
    assert next(f for f in streets if f["id"] == "way/300")["sub"] == "major"  # highway=primary
    forest = prefetch.features_from_tiles(_tiles("FOREST"))[0]
    assert all(f["sub"] == "" for f in forest)


def _road(way_id, node_ids, highway="residential"):
    return {"type": "way", "id": way_id, "nodes": node_ids, "tags": {"highway": highway}}


def test_road_links_follow_junctions_and_tiers():
    # 1-2-3 chain (ways 10,11), way 12 leaves node 3 (junction), way 13 is a
    # different tier (primary) also meeting node 1, way 14 passes THROUGH node 2.
    nodes = [_node(i, 10.0 + i * 0.001, 20.0) for i in range(1, 8)]
    data = {"elements": nodes + [
        _road(10, [1, 2]), _road(11, [2, 3]), _road(12, [3, 4]),
        _road(13, [1, 5], "primary"), _road(14, [6, 2, 7]),
    ]}
    by_id = {f["id"]: f for f in prefetch.features_from_tiles(_tiles("STREETS", data))[0]}
    assert by_id["way/10"]["links"] == [[], ["way/11", "way/14"]]   # primary way/13 ignored; 14 touches node 2
    assert by_id["way/11"]["links"] == [["way/10", "way/14"], ["way/12"]]
    assert by_id["way/12"]["links"] == [["way/11"], []]
    assert by_id["way/13"]["sub"] == "major" and by_id["way/13"]["links"] == [[], []]
    assert "links" not in prefetch.features_from_tiles(_tiles("FOREST"))[0][0]


def test_features_waterway_is_line():
    data = {"elements": [
        _node(1, 10.0, 20.0), _node(2, 10.0, 20.01), _node(3, 10.01, 20.02),
        {"type": "way", "id": 7, "nodes": [1, 2, 3], "tags": {"waterway": "stream"}},
    ]}
    features = prefetch.features_from_tiles(_tiles("WATER", data))[0]
    assert len(features) == 1 and features[0]["line"]


def test_features_deduplicated_across_tiles():
    data = _sample_data()
    other_bbox = (10.0, 22.0, 12.0, 24.0)
    fetched = {"FOREST": {_BBOX: (data, True), other_bbox: (data, False)}}
    features, _ = prefetch.features_from_tiles(fetched)
    ids = [f["id"] for f in features]
    assert len(ids) == len(set(ids))


def test_features_match_filter_keys():
    """Keys the picker sends back must be exactly what filter_excluded drops."""
    data = _sample_data()
    keys = {f["id"] for f in prefetch.features_from_tiles(_tiles("FOREST", data))[0]}
    remaining = _ids(exclusions.filter_excluded(data, keys))
    assert "way/100" not in remaining and "relation/500" not in remaining


def test_features_cap_sets_truncated():
    old = prefetch.MAX_FEATURES
    prefetch.MAX_FEATURES = 1
    try:
        features, truncated = prefetch.features_from_tiles(_tiles("FOREST"))
    finally:
        prefetch.MAX_FEATURES = old
    assert len(features) == 1 and truncated


# ---------------------------------------------------------------------------
# prefetch.tile_bbox
# ---------------------------------------------------------------------------

_DRAWN = {"south": 47.0, "north": 47.1, "west": 8.0, "east": 8.2}


def test_tile_bbox_rectangle_matches_drawn_bounds():
    min_lat, min_lon, max_lat, max_lon = prefetch.tile_bbox(_DRAWN, "rectangle", 0)
    assert abs(min_lat - 47.0) < 1e-9 and abs(max_lat - 47.1) < 1e-9
    assert abs(min_lon - 8.0) < 1e-9 and abs(max_lon - 8.2) < 1e-9


def test_tile_bbox_rotation_grows_area():
    base = prefetch.tile_bbox(_DRAWN, "rectangle", 0)
    rotated = prefetch.tile_bbox(_DRAWN, "rectangle", 45)
    assert rotated[0] < base[0] and rotated[2] > base[2]
    assert rotated[1] < base[1] and rotated[3] > base[3]


def test_tile_bbox_circle_ignores_rotation():
    a = prefetch.tile_bbox(_DRAWN, "circle", 0)
    b = prefetch.tile_bbox(_DRAWN, "circle", 45)
    assert all(abs(x - y) < 1e-9 for x, y in zip(a, b))


def test_tile_bbox_exact_and_geojson_ignore_rotation():
    for shape in ("exact", "geojson"):
        a = prefetch.tile_bbox(_DRAWN, shape, 0)
        b = prefetch.tile_bbox(_DRAWN, shape, 45)
        assert all(abs(x - y) < 1e-9 for x, y in zip(a, b))
        assert abs(a[0] - 47.0) < 1e-9 and abs(a[2] - 47.1) < 1e-9
        assert abs(a[1] - 8.0) < 1e-9 and abs(a[3] - 8.2) < 1e-9


def test_tile_bbox_square_shape_is_centered_on_drawn_center():
    min_lat, min_lon, max_lat, max_lon = prefetch.tile_bbox(_DRAWN, "octagon", 0)
    assert abs((min_lon + max_lon) / 2 - 8.1) < 1e-9
    assert min_lat < 47.0 + 1e-6 and max_lat > 47.1 - 1e-6  # covers the longer side


# ---------------------------------------------------------------------------
# bbox_snap + cache-key parity
# ---------------------------------------------------------------------------

def test_snap_within_tolerance_and_outside():
    bbox_snap.clear()
    try:
        target = (47.0, 8.0, 47.1, 8.2)
        bbox_snap.register([target])
        assert bbox_snap.snap((47.0 + 1.5e-6, 8.0 - 2e-6, 47.1, 8.2 + 1e-6)) == target
        far = (47.0 + 1e-3, 8.0, 47.1, 8.2)
        assert bbox_snap.snap(far) == far
        assert bbox_snap.snap((1.0, 2.0, 3.0, 4.0)) == (1.0, 2.0, 3.0, 4.0)
    finally:
        bbox_snap.clear()


def test_cache_path_of_jittered_bbox_matches_after_register():
    from TrailPrint3D.utils.osm.fetch_group import _make_cache_path
    from TrailPrint3D.utils.osm.fetch_utils import OsmFetchSettings

    settings = OsmFetchSettings(0, 1, 10.0, {"highways": True}, True, False, False)
    exact = (47.0, 8.0, 47.1, 8.2)
    jittered = (47.0 + 1.2e-6, 8.0 - 1.9e-6, 47.1 + 9e-7, 8.2 + 1.4e-6)
    bbox_snap.clear()
    try:
        assert _make_cache_path(exact, "FOREST", settings) != _make_cache_path(jittered, "FOREST", settings)
        bbox_snap.register([exact])
        for kind in ("FOREST", "WATER", "STREETS", "BUILDINGS"):
            assert _make_cache_path(exact, kind, settings) == _make_cache_path(jittered, kind, settings), kind
    finally:
        bbox_snap.clear()


def _build_blank_tile(b, rot, subdivisions):
    """Build the blank tile the way TP3D_OT_map_generator does (octagon, rotated)."""
    import math

    from TrailPrint3D import utils

    tp3d = bpy.context.scene.tp3d
    tp3d.objSize, tp3d.num_subdivisions = 100, subdivisions
    nx1, ny1, _ = utils.convert_to_neutral_coordinates(b["south"], b["west"], 0, 0)
    nx2, ny2, _ = utils.convert_to_neutral_coordinates(b["north"], b["east"], 0, 0)
    tp3d["sScaleHor"] = tp3d.objSize / max(abs(nx2 - nx1), abs(ny2 - ny1))
    x1, y1, _ = utils.convert_to_blender_coordinates(b["south"], b["west"], 0, 0)
    x2, y2, _ = utils.convert_to_blender_coordinates(b["north"], b["east"], 0, 0)
    diameter = max(abs(x2 - x1), abs(y2 - y1))
    r = math.radians(rot)
    gen_diameter = diameter * (abs(math.cos(r)) + abs(math.sin(r))) * 1.002
    blank = utils.create_octagon(gen_diameter / 2, subdivisions)
    blank.location = ((x1 + x2) / 2, (y1 + y2) / 2, 0)
    bpy.context.view_layer.objects.active = blank
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    return blank


def test_generator_bbox_resolves_to_prefetch_cache_file():
    """The real thing: check a real blank tile's mesh-derived bounds hit the
    cache file a prefetch of the same selection wrote."""
    from TrailPrint3D import utils
    from TrailPrint3D.utils.osm.fetch_group import _make_cache_path
    from TrailPrint3D.utils.osm.fetch_utils import OsmFetchSettings

    b = {"south": 47.0, "north": 47.13, "west": 8.0, "east": 8.31}
    rot = 30
    blank = _build_blank_tile(b, rot, 2)
    *_, min_lat, max_lat, min_lon, max_lon = utils.compute_and_store_tile_bounds(blank)
    generator_bbox = (min_lat, min_lon, max_lat, max_lon)

    settings = OsmFetchSettings(0, 1, 10.0, {"highways": True}, True, False, False)
    bbox_snap.clear()
    try:
        prefetch_bbox = prefetch.tile_bbox(b, "octagon", rot)
        bbox_snap.register([prefetch_bbox])
        assert _make_cache_path(generator_bbox, "BUILDINGS", settings) == _make_cache_path(prefetch_bbox, "BUILDINGS", settings)
    finally:
        bbox_snap.clear()


def test_elevation_plan_covers_every_tile_the_mesh_samples():
    """A real blank tile at the page's resolution: the prefetch plan must hold
    every tile the generation will open, or it would download them again."""
    from TrailPrint3D import utils
    from TrailPrint3D.utils import elevation

    tp3d = bpy.context.scene.tp3d
    tp3d.api, tp3d.disableCache = "MAPTERHORN", False
    b = {"south": 47.0, "north": 47.13, "west": 8.0, "east": 8.31}
    for shape_rotation, resolution in ((0, 4), (30, 5), (30, 7)):
        tp3d.shapeRotation = shape_rotation
        blank = _build_blank_tile(b, shape_rotation, resolution)
        world_verts, _subs, _cache, *bounds = utils.compute_and_store_tile_bounds(blank)
        zoom = elevation.terrain_tile_zoom(bounds, resolution)
        sampled = set()
        for v in world_verts:
            lat, lon = utils.convert_to_geo(v.x, v.y)
            sampled.add((zoom, *elevation.lonlat_to_tilexy(lon, lat, zoom)))

        tp3d.num_subdivisions = 1  # the slider hasn't reached the scene yet
        plan = prefetch.plan_elevation_prefetch(
            tp3d, {"bounds": b, "type": "octagon", "resolution": resolution}
        )
        missing = sampled - set(plan["tiles"])
        assert not missing, f"rot={shape_rotation} res={resolution}: not prefetched: {missing}"


def test_elevation_plan_uses_each_tile_box_for_the_zoom():
    from TrailPrint3D.utils.elevation import terrain_tile_zoom

    tp3d = bpy.context.scene.tp3d
    tp3d.api, tp3d.disableCache, tp3d.shapeRotation = "MAPTERHORN", False, 0
    whole = {"south": 47.0, "north": 47.4, "west": 8.0, "east": 8.8}
    boxes = [
        {"south": 47.0, "north": 47.4, "west": 8.0 + i * 0.2, "east": 8.2 + i * 0.2}
        for i in range(4)
    ]
    payload = {"bounds": whole, "type": "exact", "resolution": 5}
    whole_zoom = prefetch.plan_elevation_prefetch(tp3d, payload)["tiles"][0][0]
    plan = prefetch.plan_elevation_prefetch(tp3d, {**payload, "elevationBoxes": boxes})
    assert plan["tiles"][0][0] > whole_zoom
    assert plan["tiles"][0][0] == terrain_tile_zoom((47.0, 47.4, 8.0, 8.2), 5)
    assert len(plan["tiles"]) == len(set(plan["tiles"]))  # boxes share edge tiles


def test_elevation_plan_skips_what_prefetch_cannot_help():
    tp3d = bpy.context.scene.tp3d
    payload = {"bounds": _DRAWN, "type": "rectangle", "resolution": 5}
    tp3d.api, tp3d.disableCache = "OPENTOPODATA", False
    assert prefetch.plan_elevation_prefetch(tp3d, payload) is None
    tp3d.api, tp3d.disableCache = "MAPTERHORN", True
    assert prefetch.plan_elevation_prefetch(tp3d, payload) is None
    tp3d.disableCache = False
    assert prefetch.plan_elevation_prefetch(tp3d, payload)["api"] == "MAPTERHORN"


def _with_temp_tile_cache(fn):
    import tempfile
    from unittest.mock import patch

    from TrailPrint3D import constants

    with tempfile.TemporaryDirectory() as folder:
        with patch.object(constants, "terrarium_cache_dir", folder):
            fn(folder)


def _fake_session(handler):
    from unittest.mock import MagicMock, patch

    session = MagicMock()
    session.__enter__.return_value = session
    session.get.side_effect = handler
    return session, patch("TrailPrint3D.utils.elevation.requests.Session", return_value=session)


def _tile_response(url, timeout=None):
    import requests

    response = requests.Response()
    response.status_code = 200
    response._content = url.encode()
    return response


def test_elevation_prefetch_skips_cached_and_writes_atomically():
    from TrailPrint3D.utils import elevation

    def check(folder):
        with open(os.path.join(folder, "mapterhorn_10_1_1.webp"), "wb") as f:
            f.write(b"cached")
        session, patched = _fake_session(_tile_response)
        with patched:
            seen = []
            failed = elevation.prefetch_elevation_tiles(
                "MAPTERHORN", [(10, 1, 1), (10, 2, 1)], seen.append
            )
        assert failed == 0 and seen == [1, 2]
        assert session.get.call_count == 1
        assert sorted(os.listdir(folder)) == ["mapterhorn_10_1_1.webp", "mapterhorn_10_2_1.webp"]

    _with_temp_tile_cache(check)


def test_elevation_prefetch_falls_back_like_the_generation():
    import requests

    from TrailPrint3D.utils import elevation

    def handler(url, timeout=None):
        if "/14/" in url:
            response = requests.Response()
            response.status_code = 404
            return response
        return _tile_response(url)

    def check(folder):
        _session, patched = _fake_session(handler)
        with patched:
            assert elevation.prefetch_elevation_tiles("MAPTERHORN", [(14, 8501, 5763)]) == 0
        assert os.listdir(folder) == ["mapterhorn_13_4250_2881.webp"]

    _with_temp_tile_cache(check)


def test_elevation_prefetch_gives_up_after_three_failures_in_a_row():
    import requests

    from TrailPrint3D.utils import elevation

    def handler(url, timeout=None):
        raise requests.ConnectionError("offline")

    def check(_folder):
        session, patched = _fake_session(handler)
        with patched:
            tiles = [(10, x, 1) for x in range(50)]
            assert elevation.prefetch_elevation_tiles("TERRAIN-TILES", tiles) == 3
        assert session.get.call_count == 3

    _with_temp_tile_cache(check)


def test_run_prefetch_reports_done_only_after_elevation():
    from unittest.mock import patch

    from TrailPrint3D.utils import elevation

    finished = []

    def slow_tiles(api, tiles, progress_cb=None):
        import time

        time.sleep(0.4)
        finished.append(api)

    job = {}

    def snapshot(**fields):
        job.update(fields)
        if fields.get("status") == "done":
            job["elevation_was_finished"] = bool(finished)

    class Job(dict):
        def update(self, **fields):
            snapshot(**fields)

    plan = {
        "kind_tasks": [], "settings": None, "skipped": {}, "bbox": [0, 0, 1, 1],
        "elevation": {"api": "MAPTERHORN", "tiles": [(10, 1, 1)]},
    }
    with patch.object(elevation, "prefetch_elevation_tiles", slow_tiles):
        prefetch.run_prefetch(plan, Job())
    assert job["status"] == "done" and job["elevation_was_finished"]


def test_tile_decode_and_sampling_match_the_pixels():
    """parse_webp_rgb_data + the vectorized lookup against a tile with known
    pixels, compared with the per-point formula they replaced."""
    import math
    import tempfile
    from unittest.mock import patch

    import numpy as np

    from TrailPrint3D.utils import elevation

    size = 512
    rng = np.random.default_rng(1)
    expected = rng.integers(0, 256, (size, size, 3), dtype=np.uint8)  # top-down
    rgba = np.ones((size, size, 4), dtype=np.float32)
    rgba[:, :, :3] = expected[::-1] / 255.0  # Blender stores bottom-up

    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "tile.png")
        image = bpy.data.images.new("tile", size, size)
        image.pixels.foreach_set(rgba.ravel())
        image.filepath_raw = path
        image.file_format = "PNG"
        image.save()
        bpy.data.images.remove(image)
        decoded = elevation.parse_webp_rgb_data(path)
    assert decoded.dtype == np.uint8 and decoded.shape == (size, size, 3)
    assert np.array_equal(decoded, expected)

    zoom = 11
    bpy.context.scene.tp3d.num_subdivisions = 6
    coords = [
        (47.0 + float(a) * 0.1, 8.0 + float(b) * 0.1)
        for a, b in rng.random((400, 2))
    ]
    with (
        patch.object(elevation, "terrain_tile_zoom", return_value=zoom),
        patch.object(elevation, "fetch_mapterhorn_tile_path", return_value="unused"),
        patch.object(elevation, "parse_webp_rgb_data", return_value=decoded),
    ):
        result = elevation.get_elevation_Mapterhorn((47.0, 47.1, 8.0, 8.1), coords)

    assert isinstance(result, list) and isinstance(result[0], float)
    for (lat, lon), value in zip(coords, result):
        lat_rad = math.radians(lat)
        n = 2.0 ** zoom
        px = int(((lon + 180.0) / 360.0 * n * size) % size)
        py = int(((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n * size) % size)
        r, g, b = (int(c) for c in decoded[py, px])
        assert value == elevation.terrarium_pixel_to_elevation(r, g, b)


if __name__ == "__main__":
    print("\n=== test_prefetch ===\n")
    _run("filter: no exclusions returns input", test_filter_no_exclusions_returns_same_object)
    _run("filter: drops excluded way", test_filter_drops_excluded_way)
    _run("filter: drops relation + orphaned skeleton members", test_filter_drops_relation_and_orphaned_skeleton_members)
    _run("filter: keeps member shared with surviving relation", test_filter_keeps_member_way_shared_with_surviving_relation)
    _run("filter: keeps tagged member way", test_filter_keeps_tagged_member_way)
    _run("filter: does not mutate input", test_filter_does_not_mutate_input)
    _run("filter: set/clear global", test_set_and_clear_excluded_global)

    _run("features: polygon + relation (outer+hole)", test_features_polygon_and_relation)
    _run("features: skeleton member ways skipped", test_features_skip_skeleton_member_ways)
    _run("features: open way only for line kinds", test_features_open_way_is_line_only_for_line_kinds)
    _run("features: waterway is a line", test_features_waterway_is_line)
    _run("features: road gets tier sub-category", test_features_road_gets_tier_sub)
    _run("features: road links across junctions/tiers", test_road_links_follow_junctions_and_tiers)
    _run("features: de-duplicated across tiles", test_features_deduplicated_across_tiles)
    _run("features: ids match filter keys", test_features_match_filter_keys)
    _run("features: cap sets truncated", test_features_cap_sets_truncated)

    _run("tile_bbox: rectangle == drawn bounds", test_tile_bbox_rectangle_matches_drawn_bounds)
    _run("tile_bbox: rotation grows area", test_tile_bbox_rotation_grows_area)
    _run("tile_bbox: circle ignores rotation", test_tile_bbox_circle_ignores_rotation)
    _run("tile_bbox: exact/geojson ignore rotation", test_tile_bbox_exact_and_geojson_ignore_rotation)
    _run("tile_bbox: octagon centered on drawn center", test_tile_bbox_square_shape_is_centered_on_drawn_center)

    _run("snap: within tolerance / outside", test_snap_within_tolerance_and_outside)
    _run("snap: jittered bbox -> same cache file", test_cache_path_of_jittered_bbox_matches_after_register)
    _run("snap: generator's mesh bbox -> prefetch cache file", test_generator_bbox_resolves_to_prefetch_cache_file)

    _run("elevation: plan covers every tile the mesh samples", test_elevation_plan_covers_every_tile_the_mesh_samples)
    _run("elevation: zoom comes from each tile box", test_elevation_plan_uses_each_tile_box_for_the_zoom)
    _run("elevation: skipped for per-point APIs / Disable Cache", test_elevation_plan_skips_what_prefetch_cannot_help)
    _run("elevation: skips cached, atomic write", test_elevation_prefetch_skips_cached_and_writes_atomically)
    _run("elevation: 404 falls back to parent tile", test_elevation_prefetch_falls_back_like_the_generation)
    _run("elevation: gives up after 3 failures", test_elevation_prefetch_gives_up_after_three_failures_in_a_row)
    _run("elevation: done only after elevation", test_run_prefetch_reports_done_only_after_elevation)
    _run("elevation: numpy decode + sampling match pixels", test_tile_decode_and_sampling_match_the_pixels)

    _assert_all_passed()
