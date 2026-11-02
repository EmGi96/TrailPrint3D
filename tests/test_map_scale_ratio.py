"""Tests for the "Map Scale Ratio" custom property, which has to match the
generator pages' Scale readout (rect_editor.js), and the map size it replaced
as the source of that value.

Run with:
  blender --background --factory-startup --python-exit-code 1 -P tests/test_map_scale_ratio.py
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

from TrailPrint3D import utils

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


def _page_readout(south, west, north, east, obj_size):
    """The map page's Scale value for a drawn frame, in the units it computes
    it in: Leaflet's zoom-0 pixels, then tp3dScaleDenominator."""

    def project(lat, lon):
        y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
        return 256 * (lon + 180) / 360, 128 - y * 256 / (2 * math.pi)

    x1, y1 = project(north, west)
    x2, y2 = project(south, east)
    scale = obj_size / max(abs(x2 - x1), abs(y2 - y1))
    center_y = (y1 + y2) / 2
    center_lat = math.degrees(2 * math.atan(math.exp((128 - center_y) * 2 * math.pi / 256)) - math.pi / 2)
    denominator = 6371000 * math.cos(math.radians(center_lat)) * 1000 / (scale * 256 / (2 * math.pi))
    magnitude = 10 ** max(0, math.floor(math.log10(denominator)) - 2)
    return f"1:{round(denominator / magnitude) * magnitude}"


def _ratio_for_frame(south, west, north, east, obj_size):
    """Same steps as the map picker: sScaleHor from the frame's larger side,
    the scene center from the frame's center, then writeMetadata."""
    tp3d = bpy.context.scene.tp3d
    nx1, ny1, _e = utils.convert_to_neutral_coordinates(south, west, 0, 0)
    nx2, ny2, _e = utils.convert_to_neutral_coordinates(north, east, 0, 0)
    tp3d["sScaleHor"] = obj_size / max(abs(nx2 - nx1), abs(ny2 - ny1))
    x1, y1, _e = utils.convert_to_blender_coordinates(south, west, 0, 0)
    x2, y2, _e = utils.convert_to_blender_coordinates(north, east, 0, 0)
    tp3d.o_centerx = (x1 + x2) / 2
    tp3d.o_centery = (y1 + y2) / 2
    tp3d.objSize = obj_size

    obj = bpy.data.objects.new("ScaleTest", bpy.data.meshes.new("ScaleTest"))
    bpy.context.collection.objects.link(obj)
    utils.writeMetadata(obj, "MAP")
    ratio = obj["Map Scale Ratio"]
    bpy.data.objects.remove(obj)
    return ratio


def test_matches_the_page_readout():
    for frame in (
        (49.75, 8.0, 50.0, 8.3, 100),  # mid latitude
        (59.1, 10.0, 59.13, 10.05, 100),  # small area, far north
        (-33.9, 18.3, -33.7, 18.6, 150),  # southern hemisphere
        (46.0, 7.0, 47.5, 9.5, 200),  # large area
    ):
        expected = _page_readout(*frame)
        assert _ratio_for_frame(*frame) == expected, frame


def test_changes_with_the_map():
    # Two maps used to share one stale value.
    assert _ratio_for_frame(49.75, 8.0, 50.0, 8.3, 100) != _ratio_for_frame(59.1, 10.0, 59.13, 10.05, 100)


def test_format_has_no_separator_or_decimals():
    ratio = _ratio_for_frame(46.0, 7.0, 47.5, 9.5, 200)
    assert ratio.startswith("1:") and ratio[2:].isdigit(), ratio


def test_scale_text_token_matches_the_property():
    frame = (49.75, 8.0, 50.0, 8.3, 100)
    ratio = _ratio_for_frame(*frame)
    curve = bpy.data.curves.new("ScaleText", "FONT")
    text = bpy.data.objects.new("ScaleText", curve)
    bpy.context.collection.objects.link(text)
    utils.replaceShapeText("{scale}", text)
    assert curve.body == ratio, curve.body
    bpy.data.objects.remove(text)


def _tile(south, west, north, east):
    x1, y1, _e = utils.convert_to_blender_coordinates(south, west, 0, 0)
    x2, y2, _e = utils.convert_to_blender_coordinates(north, east, 0, 0)
    mesh = bpy.data.meshes.new("SizeTest")
    mesh.from_pydata([(x1, y1, 0), (x2, y1, 0), (x2, y2, 0), (x1, y2, 0)], [], [(0, 1, 2, 3)])
    obj = bpy.data.objects.new("SizeTest", mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def test_map_size_follows_the_map():
    tp3d = bpy.context.scene.tp3d
    tp3d["sMapInKm"] = 36.1  # left over from an earlier map
    tile = _tile(49.75, 8.0, 50.0, 8.3)
    utils.compute_and_store_tile_bounds(tile)
    expected = utils.haversine(49.75, 8.0, 50.0, 8.3)
    assert math.isclose(tp3d.sMapInKm, expected, rel_tol=1e-3), tp3d.sMapInKm
    assert tp3d.sMapInKm != 36.1

    utils.writeMetadata(tile, "MAP")
    assert tile["Map Size in Km"] == round(tp3d.sMapInKm, 2)
    assert "sMapInKm" not in tile.keys()
    bpy.data.objects.remove(tile)


for _name, _fn in list(globals().items()):
    if _name.startswith("test_") and callable(_fn):
        _run(_name, _fn)

_assert_all_passed()
