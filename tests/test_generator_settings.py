"""Tests for carrying generator settings over into a new Blender session.

Run with:
  blender --background --factory-startup --python-exit-code 1 -P tests/test_generator_settings.py
"""

import json
import os
import shutil
import sys
import tempfile
import traceback

import bpy

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

if "TrailPrint3D" not in bpy.context.preferences.addons:
    bpy.ops.preferences.addon_enable(module="TrailPrint3D")

from TrailPrint3D import constants
from TrailPrint3D.props import ensure_road_types, get_road_active
from TrailPrint3D.utils import color_styles, ui_state

_passed = 0
_failed = 0
_TEMP_DIR = tempfile.TemporaryDirectory()
constants.generator_settings_dir = os.path.join(_TEMP_DIR.name, "sub")
MAP_FILE = os.path.join(constants.generator_settings_dir, "map.json")


def _fresh_tp3d():
    """A scene with default settings, like a new Blender session's."""
    scene = bpy.data.scenes.new("generator_settings_test")
    ensure_road_types(scene.tp3d)
    return scene.tp3d


def _open_map_generator():
    tp3d = _fresh_tp3d()
    ui_state.restore_generator_settings(tp3d, "map")
    return tp3d


def _run(name, fn):
    global _passed, _failed
    shutil.rmtree(constants.generator_settings_dir, ignore_errors=True)
    ui_state._active_generator = None
    try:
        fn()
        print(f"  PASS  {name}")
        _passed += 1
    except Exception:
        print(f"  FAIL  {name}")
        traceback.print_exc()
        _failed += 1


def _edit_like_a_page(tp3d):
    ui_state.apply_setting_update(tp3d, "scaleElevation", 3.5)
    ui_state.apply_setting_update(tp3d, "smoothTerrainStrength", 7)
    ui_state.apply_setting_update(tp3d, "elementMode", "SINGLECOLORMODE_REMESH")
    ui_state.apply_advanced_setting_update(tp3d, "colFArea", 42.0)
    ui_state.apply_advanced_setting_update(tp3d, "elSTrackActive", True)
    ui_state.apply_element_toggle(tp3d, "forest")
    ui_state.apply_element_toggle(tp3d, "roads")
    ui_state.apply_color_update(tp3d, {"action": "set", "key": "element_color_base", "value": "#123456"})


def test_page_edits_survive_into_a_new_session():
    tp3d = _open_map_generator()
    forest_before = tp3d.col_fActive
    roads_before = tp3d.show_roads
    _edit_like_a_page(tp3d)

    restored = _open_map_generator()
    assert abs(restored.scaleElevation - 3.5) < 1e-6
    assert restored.smoothTerrainStrength == 7
    assert restored.elementMode == "SINGLECOLORMODE_REMESH"
    assert abs(restored.col_fArea - 42.0) < 1e-6
    assert get_road_active(restored, "track") is True
    assert restored.col_fActive != forest_before
    assert restored.show_roads != roads_before
    assert color_styles.scene_colors(restored)["BASE"] == color_styles.hex_to_srgb("#123456")


def test_worldcover_keeps_its_forced_element_mode():
    tp3d = _open_map_generator()
    ui_state.apply_setting_update(tp3d, "elementSource", "WORLDCOVER")

    restored = _fresh_tp3d()
    restored.elementMode = "SINGLECOLORMODE_REMESH"
    ui_state.restore_generator_settings(restored, "map")
    assert restored.elementSource == "WORLDCOVER"
    assert restored.elementMode == "PAINT"


def test_reopening_a_generator_keeps_sidebar_changes():
    tp3d = _open_map_generator()
    ui_state.apply_setting_update(tp3d, "scaleElevation", 3.5)

    restored = _fresh_tp3d()
    ui_state.restore_generator_settings(restored, "map")
    restored.scaleElevation = 1.25  # changed from the sidebar afterwards
    ui_state.restore_generator_settings(restored, "map")
    assert abs(restored.scaleElevation - 1.25) < 1e-6


def test_generators_keep_separate_settings_across_sessions():
    tp3d = _fresh_tp3d()
    ui_state.restore_generator_settings(tp3d, "map")
    ui_state.apply_setting_update(tp3d, "scaleElevation", 3.5)
    ui_state.apply_advanced_setting_update(tp3d, "colFArea", 42.0)
    tp3d = _fresh_tp3d()
    ui_state.restore_generator_settings(tp3d, "puzzle")
    ui_state.apply_setting_update(tp3d, "scaleElevation", 1.5)

    default_area = _fresh_tp3d().col_fArea
    restored = _fresh_tp3d()
    ui_state.restore_generator_settings(restored, "puzzle")
    assert abs(restored.scaleElevation - 1.5) < 1e-6
    assert restored.col_fArea == default_area
    restored = _fresh_tp3d()
    ui_state.restore_generator_settings(restored, "map")
    assert abs(restored.scaleElevation - 3.5) < 1e-6
    assert abs(restored.col_fArea - 42.0) < 1e-6


def test_switching_generators_in_one_scene_swaps_their_settings():
    tp3d = _fresh_tp3d()
    ui_state.restore_generator_settings(tp3d, "map")
    ui_state.apply_setting_update(tp3d, "scaleElevation", 3.5)
    ui_state.restore_generator_settings(tp3d, "puzzle")
    ui_state.apply_setting_update(tp3d, "scaleElevation", 1.5)

    ui_state.restore_generator_settings(tp3d, "map")
    assert abs(tp3d.scaleElevation - 3.5) < 1e-6
    ui_state.apply_setting_update(tp3d, "minThickness", 4.0)  # saved to map, not puzzle
    ui_state.restore_generator_settings(tp3d, "puzzle")
    assert abs(tp3d.scaleElevation - 1.5) < 1e-6
    assert tp3d.minThickness != 4.0


def test_opening_a_page_does_not_overwrite_saved_settings():
    """Every page refreshes its print profile list when it opens."""
    import threading

    from TrailPrint3D import picker_server

    tp3d = _open_map_generator()
    request = {"action": "select", "name": "", "done": threading.Event()}
    picker_server._pending_slicer_profiles.put(request)
    picker_server.process_slicer_profile_requests(tp3d)
    assert request["result"]["ok"] and request["done"].is_set()
    assert not os.path.exists(MAP_FILE)


def test_missing_or_broken_file_keeps_defaults():
    default = _fresh_tp3d().scaleElevation
    tp3d = _open_map_generator()
    assert tp3d.scaleElevation == default

    os.makedirs(constants.generator_settings_dir, exist_ok=True)
    with open(MAP_FILE, "w", encoding="utf-8") as f:
        f.write("{ not json")
    tp3d = _open_map_generator()
    assert tp3d.scaleElevation == default


def test_unusable_values_are_skipped():
    tp3d = _open_map_generator()
    ui_state.save_generator_settings(tp3d)
    with open(MAP_FILE, encoding="utf-8") as f:
        data = json.load(f)
    data["settings"]["api"] = "REMOVED_API"
    data["settings"]["scaleElevation"] = 3.5
    data["settings"]["notASetting"] = 1
    data["colorStyle"] = "USER:Deleted Style"
    with open(MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)

    restored = _open_map_generator()
    assert restored.api == tp3d.api
    assert abs(restored.scaleElevation - 3.5) < 1e-6


_run("page edits survive into a new session", test_page_edits_survive_into_a_new_session)
_run("WorldCover keeps its forced element mode", test_worldcover_keeps_its_forced_element_mode)
_run("reopening a generator keeps sidebar changes", test_reopening_a_generator_keeps_sidebar_changes)
_run("generators keep separate settings across sessions", test_generators_keep_separate_settings_across_sessions)
_run("switching generators in one scene swaps their settings", test_switching_generators_in_one_scene_swaps_their_settings)
_run("opening a page doesn't overwrite saved settings", test_opening_a_page_does_not_overwrite_saved_settings)
_run("missing or broken file keeps defaults", test_missing_or_broken_file_keeps_defaults)
_run("unusable values are skipped", test_unusable_values_are_skipped)

_TEMP_DIR.cleanup()
print(f"\n{_passed} passed, {_failed} failed")
if _failed:
    raise SystemExit(1)
