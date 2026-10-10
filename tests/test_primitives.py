"""Tests for the Circle / Ellipse map outlines in utils/primitives.py.

The outline used to stay a 64-sided polygon however fine the grid was
(subdividing only adds vertices along the straight sides), leaving visible
facets on big maps. These tests check that the outline now lies on the true
circle / ellipse and that the grid itself is not changed.

Run with:
  blender --background --factory-startup --python-exit-code 1 -P tests/test_primitives.py
  or, as part of the full suite:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --factory-startup --python-exit-code 1 -P tests/run_all_tests.py
"""

import math
import os
import sys
import traceback

import bmesh  # type: ignore
import bpy  # type: ignore  — provided by Blender's Python

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

if "TrailPrint3D" not in bpy.context.preferences.addons:
    bpy.ops.preferences.addon_enable(module="TrailPrint3D")

from TrailPrint3D.utils import primitives

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


def _outline(obj):
    """World-space (x, y) of the vertices on the open boundary of the flat disk (its outline)."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    mw = obj.matrix_world  # create_ellipse keeps its stretch on the object, so use world space
    edge = sorted({((mw @ v.co).x, (mw @ v.co).y) for e in bm.edges if e.is_boundary for v in e.verts})
    bm.free()
    return edge


def _cleanup(obj):
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.meshes.remove(mesh)


# ---------------------------------------------------------------------------

def test_circle_outline_is_round():
    radius = 200.0
    obj = primitives.create_circle(radius, num_subdivisions=6, name="TestCircle")
    edge = _outline(obj)
    worst = max(abs(math.hypot(x, y) - radius) for x, y in edge)
    _cleanup(obj)
    assert worst < 1e-4, f"outline deviates {worst:.6f} mm from the true circle"


def test_circle_outline_was_faceted_without_the_fix():
    # Guards the test above: with the fix disabled the deviation really is the
    # polygon's sagitta, r * (1 - cos(pi / 64)).
    original = primitives._snap_outline_to_circle
    primitives._snap_outline_to_circle = lambda *a, **k: None
    try:
        obj = primitives.create_circle(200.0, num_subdivisions=6, name="TestCircleOld")
    finally:
        primitives._snap_outline_to_circle = original
    edge = _outline(obj)
    worst = max(abs(200.0 - math.hypot(x, y)) for x, y in edge)
    _cleanup(obj)
    expected = 200.0 * (1 - math.cos(math.pi / 64))
    assert abs(worst - expected) < 0.01, f"expected ~{expected:.3f} mm of faceting, got {worst:.3f}"


def test_circle_topology_unchanged():
    original = primitives._snap_outline_to_circle
    primitives._snap_outline_to_circle = lambda *a, **k: None
    try:
        old = primitives.create_circle(100.0, num_subdivisions=5, name="TestCircleTopoOld")
    finally:
        primitives._snap_outline_to_circle = original
    new = primitives.create_circle(100.0, num_subdivisions=5, name="TestCircleTopoNew")
    same_counts = (len(old.data.vertices) == len(new.data.vertices)
                   and len(old.data.polygons) == len(new.data.polygons)
                   and len(old.data.edges) == len(new.data.edges))
    all_up = all(p.normal.z > 0 for p in new.data.polygons)
    _cleanup(old)
    _cleanup(new)
    assert same_counts, "vertex / face / edge counts changed"
    assert all_up, "a face is flipped or collapsed"


def test_circle_corners_do_not_move():
    # The 64 original polygon corners already lie on the circle; they must stay put.
    radius = 150.0
    obj = primitives.create_circle(radius, num_subdivisions=3, name="TestCircleCorners")
    verts = [(v.co.x, v.co.y) for v in obj.data.vertices]
    ok = all(
        min(math.hypot(x - radius * math.cos(math.radians(360 * i / 64)),
                       y - radius * math.sin(math.radians(360 * i / 64))) for x, y in verts) < 1e-3
        for i in range(64)
    )
    _cleanup(obj)
    assert ok, "an original corner vertex moved"


def test_ellipse_outline_is_round():
    radius, ratio = 120.0, 0.75
    obj = primitives.create_ellipse(radius, num_subdivisions=6, name="TestEllipse", aspect_ratio=ratio)
    # On the true ellipse, (x/a)^2 + (y/b)^2 == 1.
    edge = _outline(obj)
    worst = max(abs(math.sqrt((x / radius) ** 2 + (y / (radius * ratio)) ** 2) - 1.0) for x, y in edge) * radius
    _cleanup(obj)
    assert worst < 1e-4, f"outline deviates {worst:.6f} mm from the true ellipse"


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  TrailPrint3D primitives (Circle / Ellipse outline) tests")
    print("=" * 60 + "\n")

    _run("circle: outline lies on the true circle", test_circle_outline_is_round)
    _run("circle: without the fix the outline is the 64-gon", test_circle_outline_was_faceted_without_the_fix)
    _run("circle: vertex/face/edge counts unchanged, faces still up", test_circle_topology_unchanged)
    _run("circle: original polygon corners stay in place", test_circle_corners_do_not_move)
    _run("ellipse: outline lies on the true ellipse", test_ellipse_outline_is_round)

    _assert_all_passed()
