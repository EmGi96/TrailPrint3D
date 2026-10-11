"""Unit tests for the Shapely-based 2D geometry helpers in utils/geometry2d.py.

Covers:
  - validate() — make_valid repair, pass-through for valid/empty/None
  - iter_polygons() — Polygon / MultiPolygon / GeometryCollection flattening,
    min_area filtering
  - union() — merging, None/empty filtering
  - subtract() — difference, None/empty short-circuits
  - line_to_ribbon() / polylines_to_ribbon() — polyline buffering
  - xy_ring_to_polygon() — ring -> Polygon construction
  - map_footprint_polygon() / footprint_with_holes() — bmesh-object-driven
    footprint extraction (needs real bpy mesh objects)
  - _earcut_triangulate() / polygon_to_mesh() — polygon-with-holes
    triangulation and Blender mesh creation

Run with:
  blender --background --factory-startup --python-exit-code 1 -P tests/test_geometry2d.py
  or, as part of the full suite:
  & "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" --background --factory-startup --python-exit-code 1 -P tests/run_all_tests.py
"""  # noqa: W605

import os
import sys
import traceback

import bmesh  # type: ignore
import bpy  # type: ignore  — provided by Blender's Python

# ---------------------------------------------------------------------------
# Path setup — makes TrailPrint3D importable as a package from source
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

if "TrailPrint3D" not in bpy.context.preferences.addons:
    bpy.ops.preferences.addon_enable(module="TrailPrint3D")

from shapely.geometry import (
    GeometryCollection,
    MultiPolygon,
    Polygon,
)

from TrailPrint3D.utils import geometry2d as g2d

# ---------------------------------------------------------------------------
# Minimal test runner (matches the pattern used by the other tests/*.py files)
# ---------------------------------------------------------------------------
_passed = 0
_failed = 0


def _run(name, fn):
    global _passed, _failed
    try:
        fn()
        print(f"  PASS  {name}")
        _passed += 1
    except Exception as e:  # noqa: BLE001 - wide exception needeed to keep test runner going
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
# Blender object helpers
# ---------------------------------------------------------------------------

def _make_flat_plane_object(name, coords_xy):
    """Create a single flat n-gon face mesh object at z=0 from an (x, y) ring."""
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bm = bmesh.new()
    verts = [bm.verts.new((x, y, 0.0)) for x, y in coords_xy]
    bm.faces.new(verts)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return obj


def _make_square_ring_with_hole_object(name):
    """Create a mesh object whose footprint is a 10x10 square with a 2x2
    square hole in the middle, built from 4 quad faces around the hole
    (like a picture frame) so footprint_with_holes() has to union real
    faces and recover the interior hole."""
    # Outer 10x10 square, centered hole from (4,4) to (6,6).
    outer = [(0, 0), (10, 0), (10, 10), (0, 10)]
    inner = [(4, 4), (6, 4), (6, 6), (4, 6)]

    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bm = bmesh.new()

    # Frame verts: outer ring (4) + inner ring (4).
    ov = [bm.verts.new((x, y, 0.0)) for x, y in outer]
    iv = [bm.verts.new((x, y, 0.0)) for x, y in inner]

    # 4 quad faces forming the frame between outer[i]/outer[i+1] and
    # inner[i]/inner[i+1].
    n = 4
    for i in range(n):
        a = ov[i]
        b = ov[(i + 1) % n]
        c = iv[(i + 1) % n]
        d = iv[i]
        bm.faces.new((a, b, c, d))

    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return obj


def _cleanup_object(obj):
    if obj is None:
        return
    try:
        data = obj.data
    except ReferenceError:
        return
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
    except ReferenceError:
        pass
    if data is not None and data.users == 0 and isinstance(data, bpy.types.Mesh):
        bpy.data.meshes.remove(data)


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------

def test_validate_valid_geometry_passes_through():
    poly = Polygon([(0, 0), (4, 0), (4, 4), (0, 4)])
    assert poly.is_valid
    result = g2d.validate(poly)
    assert result.equals(poly)


def test_validate_repairs_bowtie():
    # Self-intersecting "bowtie" quad — classic invalid polygon.
    bowtie = Polygon([(0, 0), (4, 4), (4, 0), (0, 4)])
    assert not bowtie.is_valid
    result = g2d.validate(bowtie)
    assert result.is_valid
    assert not result.is_empty


def test_validate_none_passes_through():
    assert g2d.validate(None) is None


def test_validate_empty_passes_through():
    empty = Polygon()
    result = g2d.validate(empty)
    assert result.is_empty


# ---------------------------------------------------------------------------
# iter_polygons()
# ---------------------------------------------------------------------------

def test_iter_polygons_single_polygon():
    poly = Polygon([(0, 0), (4, 0), (4, 4), (0, 4)])
    result = list(g2d.iter_polygons(poly))
    assert len(result) == 1
    assert result[0].equals(poly)


def test_iter_polygons_multipolygon():
    p1 = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
    p2 = Polygon([(10, 10), (12, 10), (12, 12), (10, 12)])
    mp = MultiPolygon([p1, p2])
    result = list(g2d.iter_polygons(mp))
    assert len(result) == 2


def test_iter_polygons_geometry_collection():
    p1 = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
    p2 = Polygon([(10, 10), (12, 10), (12, 12), (10, 12)])
    gc = GeometryCollection([p1, p2])
    result = list(g2d.iter_polygons(gc))
    assert len(result) == 2


def test_iter_polygons_min_area_filters_small_parts():
    big = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])   # area 100
    small = Polygon([(20, 20), (21, 20), (21, 21), (20, 21)])  # area 1
    mp = MultiPolygon([big, small])
    result = list(g2d.iter_polygons(mp, min_area=5.0))
    assert len(result) == 1
    assert result[0].area == 100


def test_iter_polygons_empty_yields_nothing():
    assert list(g2d.iter_polygons(None)) == []
    assert list(g2d.iter_polygons(Polygon())) == []


# ---------------------------------------------------------------------------
# union()
# ---------------------------------------------------------------------------

def test_union_merges_overlapping_polygons():
    p1 = Polygon([(0, 0), (6, 0), (6, 6), (0, 6)])
    p2 = Polygon([(4, 4), (10, 4), (10, 10), (4, 10)])
    result = g2d.union([p1, p2])
    assert result is not None
    assert not result.is_empty
    # Overlapping squares should merge to more area than either alone but
    # less than the naive sum (36 + 36) since they overlap.
    assert result.area > 36
    assert result.area < 72


def test_union_filters_none_and_empty():
    p1 = Polygon([(0, 0), (4, 0), (4, 4), (0, 4)])
    result = g2d.union([p1, None, Polygon()])
    assert result is not None
    assert result.equals(p1)


def test_union_all_none_returns_none():
    assert g2d.union([None, None]) is None
    assert g2d.union([]) is None


# ---------------------------------------------------------------------------
# subtract()
# ---------------------------------------------------------------------------

def test_subtract_basic_difference():
    outer = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    hole = Polygon([(4, 4), (6, 4), (6, 6), (4, 6)])
    result = g2d.subtract(outer, hole)
    assert result is not None
    assert result.area == 100 - 4


def test_subtract_none_neg_geom_returns_original():
    outer = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    result = g2d.subtract(outer, None)
    assert result is outer


def test_subtract_empty_neg_geom_returns_original():
    outer = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    result = g2d.subtract(outer, Polygon())
    assert result is outer


def test_subtract_none_geom_returns_none():
    hole = Polygon([(4, 4), (6, 4), (6, 6), (4, 6)])
    assert g2d.subtract(None, hole) is None


# ---------------------------------------------------------------------------
# line_to_ribbon()
# ---------------------------------------------------------------------------

def test_line_to_ribbon_basic_buffer():
    coords = [(0, 0), (10, 0)]
    result = g2d.line_to_ribbon(coords, half_width=1.0)
    assert result is not None
    assert not result.is_empty
    # A 10-unit line buffered by 1.0 makes a ~10x2 rectangle plus round caps,
    # so area should be comfortably more than the bare rectangle (20) due to
    # the caps, but in a sane ballpark.
    assert result.area > 20
    assert result.area < 30


def test_line_to_ribbon_degenerate_single_point_returns_none():
    assert g2d.line_to_ribbon([(0, 0)], half_width=1.0) is None


def test_line_to_ribbon_empty_input_returns_none():
    assert g2d.line_to_ribbon([], half_width=1.0) is None


# ---------------------------------------------------------------------------
# polylines_to_ribbon()
# ---------------------------------------------------------------------------

def test_polylines_to_ribbon_disjoint_lines_both_present():
    lines = [
        [(0, 0), (10, 0)],
        [(0, 100), (10, 100)],
    ]
    result = g2d.polylines_to_ribbon(lines, half_width=1.0)
    assert result is not None
    assert not result.is_empty
    # Two disjoint ribbons far apart should end up as a MultiPolygon (or at
    # least contain both regions) — bbox height should span both lines.
    _minx, miny, _maxx, maxy = result.bounds
    assert maxy - miny > 90


def test_polylines_to_ribbon_overlapping_lines_merge():
    lines = [
        [(0, 0), (10, 0)],
        [(5, 0), (15, 0)],
    ]
    result = g2d.polylines_to_ribbon(lines, half_width=1.0)
    assert result is not None
    minx, _miny, maxx, _maxy = result.bounds
    # Should merge into one continuous ribbon spanning 0..15, not two
    # separate short segments.
    assert minx <= 0.5
    assert maxx >= 14.5


def test_polylines_to_ribbon_empty_input_returns_none():
    assert g2d.polylines_to_ribbon([], half_width=1.0) is None
    assert g2d.polylines_to_ribbon([[(0, 0)]], half_width=1.0) is None


def test_polylines_to_ribbon_simplify_and_precision_do_not_crash():
    lines = [[(0, 0), (5, 0.001), (10, 0)]]
    result = g2d.polylines_to_ribbon(
        lines, half_width=1.0, simplify_tol=0.01, precision=0.001
    )
    assert result is not None
    assert not result.is_empty


# ---------------------------------------------------------------------------
# xy_ring_to_polygon()
# ---------------------------------------------------------------------------

def test_xy_ring_to_polygon_valid_square():
    result = g2d.xy_ring_to_polygon([(0, 0), (4, 0), (4, 4), (0, 4)])
    assert result is not None
    assert result.area == 16


def test_xy_ring_to_polygon_too_few_points_returns_none():
    assert g2d.xy_ring_to_polygon([(0, 0), (4, 0)]) is None
    assert g2d.xy_ring_to_polygon([]) is None


def test_xy_ring_to_polygon_self_intersecting_repaired():
    # Bowtie ring — invalid as constructed, should come back validated.
    result = g2d.xy_ring_to_polygon([(0, 0), (4, 4), (4, 0), (0, 4)])
    assert result is not None
    assert result.is_valid


# ---------------------------------------------------------------------------
# map_footprint_polygon() — needs a real bpy mesh object
# ---------------------------------------------------------------------------

def test_map_footprint_polygon_simple_square():
    obj = _make_flat_plane_object(
        "TP3D_TestFootprintSquare", [(0, 0), (10, 0), (10, 10), (0, 10)]
    )
    try:
        result = g2d.map_footprint_polygon(obj)
        assert result is not None
        assert abs(result.area - 100) < 1e-6
    finally:
        _cleanup_object(obj)


def test_map_footprint_polygon_non_mesh_object_returns_none():
    curve_data = bpy.data.curves.new("TP3D_TestCurve", type='CURVE')
    curve_obj = bpy.data.objects.new("TP3D_TestCurveObj", curve_data)
    bpy.context.collection.objects.link(curve_obj)
    try:
        assert g2d.map_footprint_polygon(curve_obj) is None
        assert g2d.map_footprint_polygon(None) is None
    finally:
        bpy.data.objects.remove(curve_obj, do_unlink=True)
        bpy.data.curves.remove(curve_data)


# ---------------------------------------------------------------------------
# footprint_with_holes() — needs a real bpy mesh object
# ---------------------------------------------------------------------------

def test_footprint_with_holes_preserves_interior_hole():
    obj = _make_square_ring_with_hole_object("TP3D_TestFrameWithHole")
    try:
        result = g2d.footprint_with_holes(obj)
        assert result is not None
        assert not result.is_empty
        # 10x10 outer minus 2x2 inner hole = 96.
        assert abs(result.area - 96) < 1e-6
    finally:
        _cleanup_object(obj)


def test_footprint_with_holes_non_mesh_returns_none():
    assert g2d.footprint_with_holes(None) is None


# ---------------------------------------------------------------------------
# _earcut_triangulate() / polygon_to_mesh()
# ---------------------------------------------------------------------------

def test_earcut_triangulate_square_no_holes():
    exterior = [(0, 0), (10, 0), (10, 10), (0, 10)]
    result = g2d._earcut_triangulate(exterior, [])
    assert result is not None
    verts2d, tris = result
    assert len(verts2d) == 4
    assert len(tris) == 2  # a square earcuts into exactly 2 triangles


def test_earcut_triangulate_with_hole():
    exterior = [(0, 0), (10, 0), (10, 10), (0, 10)]
    hole = [(4, 4), (6, 4), (6, 6), (4, 6)]
    result = g2d._earcut_triangulate(exterior, [hole])
    assert result is not None
    verts2d, tris = result
    assert len(verts2d) == 8  # 4 outer + 4 hole verts, shared by index
    assert len(tris) > 2  # more triangles needed to route around the hole


def test_polygon_to_mesh_simple_polygon():
    poly = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    obj = g2d.polygon_to_mesh("TP3D_TestPolyMesh", poly)
    try:
        assert obj is not None
        assert obj.type == 'MESH'
        assert len(obj.data.vertices) == 4
        assert len(obj.data.polygons) == 2
    finally:
        _cleanup_object(obj)


def test_polygon_to_mesh_polygon_with_hole():
    outer = [(0, 0), (10, 0), (10, 10), (0, 10)]
    hole = [(4, 4), (6, 4), (6, 6), (4, 6)]
    poly = Polygon(outer, [hole])
    obj = g2d.polygon_to_mesh("TP3D_TestPolyMeshHole", poly)
    try:
        assert obj is not None
        assert obj.type == 'MESH'
        assert len(obj.data.vertices) == 8
        assert len(obj.data.polygons) > 2
    finally:
        _cleanup_object(obj)


def test_polygon_to_mesh_empty_polygon_returns_none():
    assert g2d.polygon_to_mesh("TP3D_TestEmptyPoly", Polygon()) is None
    assert g2d.polygon_to_mesh("TP3D_TestNonePoly", None) is None


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  TrailPrint3D geometry2d.py unit tests")
    print("=" * 60 + "\n")

    _run("validate: valid geometry passes through", test_validate_valid_geometry_passes_through)
    _run("validate: repairs bowtie polygon", test_validate_repairs_bowtie)
    _run("validate: None passes through", test_validate_none_passes_through)
    _run("validate: empty passes through", test_validate_empty_passes_through)

    _run("iter_polygons: single Polygon", test_iter_polygons_single_polygon)
    _run("iter_polygons: MultiPolygon", test_iter_polygons_multipolygon)
    _run("iter_polygons: GeometryCollection", test_iter_polygons_geometry_collection)
    _run("iter_polygons: min_area filters small parts", test_iter_polygons_min_area_filters_small_parts)
    _run("iter_polygons: empty/None yields nothing", test_iter_polygons_empty_yields_nothing)

    _run("union: merges overlapping polygons", test_union_merges_overlapping_polygons)
    _run("union: filters None/empty entries", test_union_filters_none_and_empty)
    _run("union: all-None input returns None", test_union_all_none_returns_none)

    _run("subtract: basic difference", test_subtract_basic_difference)
    _run("subtract: None neg_geom returns original", test_subtract_none_neg_geom_returns_original)
    _run("subtract: empty neg_geom returns original", test_subtract_empty_neg_geom_returns_original)
    _run("subtract: None geom returns None", test_subtract_none_geom_returns_none)

    _run("line_to_ribbon: basic buffer", test_line_to_ribbon_basic_buffer)
    _run("line_to_ribbon: degenerate single point -> None", test_line_to_ribbon_degenerate_single_point_returns_none)
    _run("line_to_ribbon: empty input -> None", test_line_to_ribbon_empty_input_returns_none)

    _run("polylines_to_ribbon: disjoint lines both present", test_polylines_to_ribbon_disjoint_lines_both_present)
    _run("polylines_to_ribbon: overlapping lines merge", test_polylines_to_ribbon_overlapping_lines_merge)
    _run("polylines_to_ribbon: empty input -> None", test_polylines_to_ribbon_empty_input_returns_none)
    _run("polylines_to_ribbon: simplify+precision don't crash", test_polylines_to_ribbon_simplify_and_precision_do_not_crash)

    _run("xy_ring_to_polygon: valid square", test_xy_ring_to_polygon_valid_square)
    _run("xy_ring_to_polygon: too few points -> None", test_xy_ring_to_polygon_too_few_points_returns_none)
    _run("xy_ring_to_polygon: self-intersecting repaired", test_xy_ring_to_polygon_self_intersecting_repaired)

    _run("map_footprint_polygon: simple square", test_map_footprint_polygon_simple_square)
    _run("map_footprint_polygon: non-mesh/None -> None", test_map_footprint_polygon_non_mesh_object_returns_none)

    _run("footprint_with_holes: preserves interior hole", test_footprint_with_holes_preserves_interior_hole)
    _run("footprint_with_holes: non-mesh -> None", test_footprint_with_holes_non_mesh_returns_none)

    _run("_earcut_triangulate: square, no holes", test_earcut_triangulate_square_no_holes)
    _run("_earcut_triangulate: with hole", test_earcut_triangulate_with_hole)
    _run("polygon_to_mesh: simple polygon", test_polygon_to_mesh_simple_polygon)
    _run("polygon_to_mesh: polygon with hole", test_polygon_to_mesh_polygon_with_hole)
    _run("polygon_to_mesh: empty/None -> None", test_polygon_to_mesh_empty_polygon_returns_none)

    _assert_all_passed()
