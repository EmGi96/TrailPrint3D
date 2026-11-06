"""Tests for magnet-hole placement (utils/magnet_layout.py), the shared
outline lookup (utils/outline.py) and the Magnet Holes operator.

Run with:
  blender --background --factory-startup --python-exit-code 1 -P tests/test_magnet_layout.py
  or as part of tests/run_all_tests.py.
"""

import math
import os
import sys
import traceback

import bpy  # type: ignore

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

if "TrailPrint3D" not in bpy.context.preferences.addons:
    bpy.ops.preferences.addon_enable(module="TrailPrint3D")

from shapely.affinity import rotate
from shapely.geometry import Point, box

from TrailPrint3D.utils import magnet_layout as ml
from TrailPrint3D.utils import outline as ol
from TrailPrint3D.utils.primitives import heart_polygon, hexagon_polygon

_passed = 0
_failed = 0


def _run(name, fn):
    global _passed, _failed
    try:
        fn()
        print(f"  PASS  {name}")
        _passed += 1
    except Exception as e:  # noqa: BLE001 - keep the runner going
        print(f"  FAIL  {name} - exception occurred: {e}")
        traceback.print_exc()
        _failed += 1


def _assert_all_passed():
    print(f"\n{'='*60}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"{'='*60}\n")
    if _failed:
        raise SystemExit(1)


def _check_valid(outline, centers, diameter, margin, gap=1.0):
    r = diameter / 2
    inner = outline.buffer(-margin)
    for x, y in centers:
        assert inner.buffer(1e-6).contains(Point(x, y).buffer(r, quad_segs=16)), (x, y)
    for i, a in enumerate(centers):
        for b in centers[i + 1:]:
            assert math.dist(a, b) >= diameter + gap - 1e-6, (a, b)


# ---------------------------------------------------------------------------
# magnet_layout
# ---------------------------------------------------------------------------


def test_hexagon_corners():
    hexa = hexagon_polygon(50)
    centers = ml.magnet_layout(hexa, 6, 6.3, 3.0, shape="HEXAGON")
    assert len(centers) == 6
    _check_valid(hexa, centers, 6.3, 3.0)
    for k, (x, y) in enumerate(centers):
        assert abs(math.degrees(math.atan2(y, x)) % 360 - (60 * k) % 360) < 1e-6


def test_rotated_square():
    sq = rotate(box(-50, -50, 50, 50), 30, origin=(0, 0))
    centers = ml.magnet_layout(sq, 4, 6.3, 3.0, shape="SQUARE", rotation_deg=30)
    assert len(centers) == 4
    _check_valid(sq, centers, 6.3, 3.0)
    angles = sorted(round(math.degrees(math.atan2(y, x)) % 360, 3) for x, y in centers)
    assert angles == [75.0, 165.0, 255.0, 345.0], angles
    # Pushed into the corners, not sitting halfway in.
    assert all(math.hypot(x, y) > 50 for x, y in centers)


def test_concave_heart():
    heart = heart_polygon(50)
    centers = ml.magnet_layout(heart, 4, 6.3, 3.0, shape="HEART")
    assert len(centers) == 4
    _check_valid(heart, centers, 6.3, 3.0)


def test_too_small_plate():
    tiny = box(-4, -4, 4, 4)
    assert ml.magnet_layout(tiny, 4, 6.3, 3.0, shape="SQUARE") == []


def test_count_8_small_object():
    small = box(-10, -10, 10, 10)
    centers = ml.magnet_layout(small, 8, 6.3, 2.0, shape="SQUARE")
    assert 0 < len(centers) < 8, len(centers)
    _check_valid(small, centers, 6.3, 2.0)


def test_dovetails_are_avoided():
    sq = box(-50, -50, 50, 50)
    blocked = ml.dovetail_footprints(100, "SQUARE")
    centers = ml.magnet_layout(sq, 8, 6.3, 3.0, blocked=blocked, shape="SQUARE")
    assert len(centers) == 8
    for x, y in centers:
        assert Point(x, y).distance(blocked) >= 6.3 / 2 - 1e-6, (x, y)


def test_medal_holder_rays_unchanged():
    # place_on_rays keeps the medal holder's original march semantics.
    allowed = box(-10, -10, 10, 10)
    got = ml.place_on_rays(lambda x, y: allowed.contains(Point(x, y)), [0.0], 20.0)
    assert got and abs(got[0][0] - 9.5) < 1e-9, got


# ---------------------------------------------------------------------------
# outline
# ---------------------------------------------------------------------------


class _FakeObj(dict):
    data = None

    def __init__(self, bounds=None, **props):
        super().__init__(**props)
        if bounds:
            x0, y0, x1, y1 = bounds
            self.bound_box = [(x, y, 0.0) for x in (x0, x1) for y in (y0, y1)]


def test_map_outline_rotation_detection():
    sq = box(-50, -50, 50, 50)
    rot = rotate(sq, 30, origin=(0, 0))
    baked = _FakeObj(rot.bounds, map_polygon_wkt=sq.wkt, shapeRotation=30)
    assert ol.map_outline_local(baked).equals_exact(rot, 1e-6)
    # Rotation still in the object transform: mesh bounds match the raw WKT.
    unbaked = _FakeObj(sq.bounds, map_polygon_wkt=sq.wkt, shapeRotation=30)
    assert ol.map_outline_local(unbaked).equals_exact(sq, 1e-6)


def test_lookup_order():
    piece = box(0, 0, 1, 1)
    obj = _FakeObj(
        (0, 0, 1, 1),
        outline_wkt=piece.wkt,
        map_polygon_wkt=box(-50, -50, 50, 50).wkt,
    )
    assert ol.get_bottom_outline(obj).equals(piece)


def test_shift_outline_keys():
    obj = _FakeObj(plate_wkt=box(0, 0, 2, 2).wkt, map_polygon_wkt=box(0, 0, 2, 2).wkt)
    ol.shift_outline_keys(obj, 1.0, -1.0)
    assert ol._load(obj["plate_wkt"]).equals(box(1, -1, 3, 1))
    # map_polygon_wkt is not a local-frame key that moves with the origin.
    assert ol._load(obj["map_polygon_wkt"]).equals(box(0, 0, 2, 2))


# ---------------------------------------------------------------------------
# Operator (real Blender objects)
# ---------------------------------------------------------------------------


def test_operator_cuts_plate():
    from TrailPrint3D.utils.mesh_ops import recalculateNormals
    from TrailPrint3D.utils.plate import _build_prism

    bpy.ops.object.select_all(action="DESELECT")
    hexa = hexagon_polygon(40)
    plate = _build_prism(hexa, 5.0, 0.0, "MagnetTestPlate")
    recalculateNormals(plate)
    plate.location = (300.0, 0.0, 0.0)
    plate["Object type"] = "PLATE"
    plate["Shape"] = "HEXAGON"
    plate["shapeRotation"] = 0
    plate["plate_wkt"] = hexa.wkt
    plate["MagnetHoles"] = False
    bpy.context.view_layer.update()

    tp3d = bpy.context.scene.tp3d
    tp3d.magnetCount = 6
    tp3d.magnetDiameter = 6.0
    tp3d.magnetMargin = 2.0
    tp3d.magnetHeight = 2.5

    plate.select_set(True)
    bpy.context.view_layer.objects.active = plate
    assert bpy.ops.tp3d.magnet_holes() == {"FINISHED"}
    assert plate["MagnetHoles"]

    centers = ml.magnet_layout(hexa, 6, 6.0, 2.0, shape="HEXAGON")
    from mathutils import Vector

    up = Vector((0, 0, 1))
    for x, y in centers:
        hit, loc, _n, _i = plate.ray_cast(Vector((x, y, -1.0)), up)
        assert hit and abs(loc.z - 2.5) < 1e-3, (x, y, hit, loc)
    hit, loc, _n, _i = plate.ray_cast(Vector((0, 0, -1.0)), up)
    assert hit and abs(loc.z) < 1e-3, loc

    # A second run must not cut again.
    assert bpy.ops.tp3d.magnet_holes() == {"CANCELLED"}
    bpy.data.objects.remove(plate, do_unlink=True)


def test_operator_clamps_depth_on_thin_plate():
    from TrailPrint3D.utils.plate import _build_prism

    bpy.ops.object.select_all(action="DESELECT")
    sq = box(-30, -30, 30, 30)
    from TrailPrint3D.utils.mesh_ops import recalculateNormals

    plate = _build_prism(sq, 2.0, 0.0, "MagnetThinPlate")
    recalculateNormals(plate)
    plate["Object type"] = "PLATE"
    plate["Shape"] = "SQUARE"
    plate["plate_wkt"] = sq.wkt
    bpy.context.view_layer.update()

    tp3d = bpy.context.scene.tp3d
    tp3d.magnetCount = 4
    tp3d.magnetDiameter = 6.0
    tp3d.magnetMargin = 2.0
    tp3d.magnetHeight = 5.0

    plate.select_set(True)
    bpy.context.view_layer.objects.active = plate
    assert bpy.ops.tp3d.magnet_holes() == {"FINISHED"}
    from mathutils import Vector

    (x, y) = ml.magnet_layout(sq, 4, 6.0, 2.0, shape="SQUARE")[0]
    hit, loc, _n, _i = plate.ray_cast(Vector((x, y, -1.0)), Vector((0, 0, 1)))
    assert hit and abs(loc.z - (2.0 - 0.6)) < 1e-3, loc
    bpy.data.objects.remove(plate, do_unlink=True)


for _name, _fn in list(globals().items()):
    if _name.startswith("test_") and callable(_fn):
        _run(_name, _fn)

_assert_all_passed()
