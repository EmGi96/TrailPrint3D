"""Tests for the GeoJSON boundary reader (utils/io_geojson.py).

Uses a real département boundary from tests/Resources/:
  - departement-73-savoie.geojson — bare Feature, single Polygon, 339 pts,
    no holes (france-geojson.gregoiredavid.fr export)

Run with:
  & "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" --background --factory-startup --python-exit-code 1 -P tests/test_geojson_import.py
"""

import sys
import os
import json
import traceback

import bpy  # type: ignore

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_RESOURCES = os.path.join(_REPO_ROOT, "tests", "Resources")
_SAVOIE = os.path.join(_RESOURCES, "departement-73-savoie.geojson")

# ---------------------------------------------------------------------------
# Minimal test runner
# ---------------------------------------------------------------------------
_passed = 0
_failed = 0

def _run(name, fn):
    global _passed, _failed
    try:
        fn()
        print(f"  PASS  {name}")
        _passed += 1
    except Exception:
        print(f"  FAIL  {name}")
        traceback.print_exc()
        _failed += 1

def _assert_all_passed():
    print(f"\n{'='*60}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"{'='*60}\n")
    if _failed:
        raise SystemExit(1)


# ===========================================================================
# Shared fixture — parse once, reuse across tests
# ===========================================================================
_savoie_polygon = None

def _load():
    global _savoie_polygon
    from TrailPrint3D.utils.io_geojson import read_geojson_file
    _savoie_polygon = read_geojson_file(_SAVOIE)


# ---------------------------------------------------------------------------
# read_geojson_file — structural invariants
# ---------------------------------------------------------------------------

def test_returns_valid_polygon():
    assert _savoie_polygon is not None, "read_geojson_file must return a polygon"
    assert _savoie_polygon.is_valid, "returned polygon must be valid"
    assert not _savoie_polygon.is_empty, "returned polygon must not be empty"


def test_point_count_matches_source_file():
    with open(_SAVOIE, encoding="utf-8") as f:
        data = json.load(f)
    # Bare Feature -> geometry -> Polygon -> exterior ring (closing point included)
    raw_ring = data["geometry"]["coordinates"][0]
    # Shapely drops nothing from a Polygon's own ring; -1 for the closing duplicate.
    assert len(_savoie_polygon.exterior.coords) - 1 == len(raw_ring) - 1, \
        "unsimplified read should preserve the source ring's point count"


def test_bbox_in_savoie_area():
    # Savoie sits roughly within lon [5.6, 7.2], lat [45.1, 45.95]
    west, south, east, north = _savoie_polygon.bounds
    assert 5.0 < west < 6.0, f"west={west} unexpected"
    assert 7.0 < east < 7.5, f"east={east} unexpected"
    assert 45.0 < south < 45.5, f"south={south} unexpected"
    assert 45.5 < north < 46.0, f"north={north} unexpected"


def test_no_holes_in_source():
    assert len(_savoie_polygon.interiors) == 0, \
        "the sample Savoie boundary has no holes"


# ---------------------------------------------------------------------------
# Top-level GeoJSON structure handling (Feature / FeatureCollection / bare geometry)
# ---------------------------------------------------------------------------

def test_bare_geometry_top_level():
    from TrailPrint3D.utils import geometry2d as g2d
    from TrailPrint3D.utils.io_geojson import _geometry_to_polygons
    bare = {"type": "Polygon", "coordinates": [[[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]]]}
    polys = _geometry_to_polygons(bare)
    assert len(polys) == 1, "bare Polygon geometry must yield exactly one polygon"
    assert abs(polys[0].area - 16.0) < 1e-9, f"expected area 16.0, got {polys[0].area}"


def test_feature_collection_picks_largest():
    import tempfile
    from TrailPrint3D.utils.io_geojson import read_geojson_file
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {}, "geometry":
                {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}},
            {"type": "Feature", "properties": {}, "geometry":
                {"type": "Polygon", "coordinates": [[[10, 10], [20, 10], [20, 20], [10, 20], [10, 10]]]}},
        ],
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".geojson", delete=False, encoding="utf-8") as f:
        json.dump(fc, f)
        tmp_path = f.name
    try:
        result = read_geojson_file(tmp_path)
        assert abs(result.area - 100.0) < 1e-6, f"expected the larger (10x10) polygon, got area={result.area}"
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# simplify_boundary
# ---------------------------------------------------------------------------

def test_simplify_reduces_point_count_and_stays_valid():
    from TrailPrint3D.utils.io_geojson import simplify_boundary
    original_pts = len(_savoie_polygon.exterior.coords)
    simplified = simplify_boundary(_savoie_polygon, 0.01)
    assert simplified.is_valid, "simplified polygon must remain valid"
    assert len(simplified.exterior.coords) < original_pts, \
        "a positive tolerance should reduce the point count on a 339-point boundary"


def test_simplify_zero_tolerance_is_noop():
    from TrailPrint3D.utils.io_geojson import simplify_boundary
    result = simplify_boundary(_savoie_polygon, 0.0)
    assert result is _savoie_polygon, "tolerance <= 0 must return the same object unchanged"


# ---------------------------------------------------------------------------
# Interior subdivision (no scene/addon registration needed -- pure bmesh)
#
# Regression guard for a real bug hit during development: bmesh.ops.subdivide
# _edges() on an earcut-triangulated cap silently adds boundary vertices
# without adding any new faces UNLESS use_grid_fill=True is passed -- the
# vertex count goes up but the face count doesn't, leaving a mesh with almost
# no real interior for elevation to drape onto. This checks the ratio stays
# sane after subdividing a triangulated polygon cap, the same operation
# build_tile_from_polygon performs.
# ---------------------------------------------------------------------------

def test_subdivide_grid_fill_adds_faces_not_just_verts():
    import bmesh
    from TrailPrint3D.utils import geometry2d as g2d

    # A simple pentagon cap, triangulated via the same polygon_to_mesh() path
    # build_tile_from_polygon uses.
    ring = [(0, 0), (10, 0), (12, 6), (5, 10), (-2, 6)]
    polygon = g2d.xy_ring_to_polygon(ring)
    tile = g2d.polygon_to_mesh("SubdivTest", polygon)
    assert tile is not None, "polygon_to_mesh should succeed on a simple convex pentagon"

    bm = bmesh.new()
    bm.from_mesh(tile.data)
    verts_before, faces_before = len(bm.verts), len(bm.faces)

    bmesh.ops.subdivide_edges(bm, edges=list(bm.edges), cuts=7, use_grid_fill=True)
    verts_after, faces_after = len(bm.verts), len(bm.faces)
    bm.free()

    assert verts_after > verts_before, "subdivide should add vertices"
    assert faces_after > faces_before * 10, \
        (f"face count should grow roughly with the square of cuts, not stay flat "
         f"(before={faces_before}, after={faces_after}) -- use_grid_fill must be True")

    import bpy
    bpy.data.meshes.remove(tile.data, do_unlink=True)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_missing_file_raises():
    from TrailPrint3D.utils.io_geojson import read_geojson_file
    try:
        read_geojson_file("/nonexistent/path/file.geojson")
        assert False, "Expected an exception for missing file"
    except Exception:
        pass  # any exception is acceptable (FileNotFoundError, JSONDecodeError, etc.)


def test_malformed_json_raises():
    import tempfile
    from TrailPrint3D.utils.io_geojson import read_geojson_file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".geojson", delete=False, encoding="utf-8") as f:
        f.write("{ this is not valid json ]")
        tmp_path = f.name
    try:
        try:
            read_geojson_file(tmp_path)
            assert False, "Expected an exception for malformed JSON"
        except Exception:
            pass
    finally:
        os.unlink(tmp_path)


def test_non_polygon_geometry_raises():
    import tempfile
    from TrailPrint3D.utils.io_geojson import read_geojson_file
    line = {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".geojson", delete=False, encoding="utf-8") as f:
        json.dump(line, f)
        tmp_path = f.name
    try:
        try:
            read_geojson_file(tmp_path)
            assert False, "Expected a ValueError for a file with no Polygon/MultiPolygon geometry"
        except Exception:
            pass
    finally:
        os.unlink(tmp_path)


# ===========================================================================
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  TrailPrint3D GeoJSON boundary import tests")
    print("=" * 60 + "\n")

    _load()

    _run("structure: returns a valid, non-empty polygon",     test_returns_valid_polygon)
    _run("structure: point count matches source ring",        test_point_count_matches_source_file)
    _run("structure: bbox falls within Savoie's area",        test_bbox_in_savoie_area)
    _run("structure: no holes in the sample boundary",        test_no_holes_in_source)

    _run("top-level: bare Polygon geometry",                  test_bare_geometry_top_level)
    _run("top-level: FeatureCollection picks the largest",    test_feature_collection_picks_largest)

    _run("simplify: reduces point count, stays valid",        test_simplify_reduces_point_count_and_stays_valid)
    _run("simplify: zero tolerance is a no-op",                test_simplify_zero_tolerance_is_noop)

    _run("subdivide: grid_fill adds faces, not just verts",    test_subdivide_grid_fill_adds_faces_not_just_verts)

    _run("error: missing file raises exception",              test_missing_file_raises)
    _run("error: malformed JSON raises exception",             test_malformed_json_raises)
    _run("error: non-polygon geometry raises exception",       test_non_polygon_geometry_raises)

    _assert_all_passed()
