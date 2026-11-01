"""Tests for the Medal Holder / Backplate materials on a scene that has never
had a map generated (so setupColors hasn't run yet).

Run with:
  blender --background --factory-startup --python-exit-code 1 -P tests/test_accessory_materials.py
  or as part of tests/run_all_tests.py.
"""

import os
import sys
import traceback

import bpy  # type: ignore

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

if "TrailPrint3D" not in bpy.context.preferences.addons:
    bpy.ops.preferences.addon_enable(module="TrailPrint3D")

from TrailPrint3D.utils.accessories import backplate, medal_holder
from TrailPrint3D.utils.color import material_to_srgb

_passed = 0
_failed = 0

_WHITE = (255, 255, 255)
_BLACK = (0, 0, 0)


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


def _clear_materials():
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat)


def _build_medal_holder(text):
    """Same steps as TP3D_OT_medal_holder_generator._apply_result."""
    raw = {"texts": {"top": {"text": "TEST"}}} if text else {}
    params = medal_holder.sanitize_params(raw)
    plate, texts = medal_holder.build_objects(params, bpy.context.collection, name="TestMedal")
    assert bool(texts) == text, texts
    medal_holder.assign_scene_materials(plate, texts)
    return plate, [obj for _slot, obj in texts]


def _build_backplate():
    raw = {"texts": {"titlefield": {"text": "TEST", "icon": "no"}}}
    params = backplate.sanitize_params(raw, bpy.context.scene.tp3d.textFont)
    plate, text = backplate.build_objects(params, name="TestBackplate")
    assert text is not None
    return plate, text


def _remove(objs):
    for obj in objs:
        medal_holder.remove_object(obj)


def _assert_only_material(obj, name, srgb):
    mats = list(obj.data.materials)
    assert len(mats) == 1 and mats[0] is not None, mats
    assert mats[0].name == name, mats[0].name
    assert material_to_srgb(mats[0]) == srgb, material_to_srgb(mats[0])


def test_medal_holder_text_is_white_without_a_map():
    _clear_materials()
    plate, texts = _build_medal_holder(text=True)
    _assert_only_material(plate, "BLACK", _BLACK)
    _assert_only_material(texts[0], "WHITE", _WHITE)
    _remove([plate] + texts)


def test_textless_medal_holder_still_creates_white():
    _clear_materials()
    plate, _texts = _build_medal_holder(text=False)
    white = bpy.data.materials.get("WHITE")
    assert white is not None
    assert material_to_srgb(white) == _WHITE
    _remove([plate])


def test_backplate_text_is_white_after_medal_holder_with_text():
    _clear_materials()
    medal_plate, medal_texts = _build_medal_holder(text=True)
    plate, text = _build_backplate()
    _assert_only_material(text, "WHITE", _WHITE)
    _remove([medal_plate] + medal_texts + [plate, text])


def test_backplate_text_is_white_after_textless_medal_holder():
    _clear_materials()
    medal_plate, _texts = _build_medal_holder(text=False)
    plate, text = _build_backplate()
    _assert_only_material(text, "WHITE", _WHITE)
    _remove([medal_plate, plate, text])


for _name, _fn in list(globals().items()):
    if _name.startswith("test_") and callable(_fn):
        _run(_name, _fn)

_assert_all_passed()
