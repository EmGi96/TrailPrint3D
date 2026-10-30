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
from unittest.mock import MagicMock, patch

import bpy

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

if "TrailPrint3D" not in bpy.context.preferences.addons:
    bpy.ops.preferences.addon_enable(module="TrailPrint3D")

from TrailPrint3D import addon_preferences, constants
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


class _StubScene(dict):
    """Stands in for a scene's tp3d where the test needs a profile the 3MF
    add-on would provide (the real enum only accepts installed profiles).
    Setting the profile runs the real update callback, like the property does."""

    _profile = "NONE"
    on_save = staticmethod(lambda tp3d: None)  # stands in for save_generator_settings

    @property
    def slicer_profile_name(self):
        return self._profile

    @slicer_profile_name.setter
    def slicer_profile_name(self, value):
        self._profile = value
        with patch.object(ui_state, "save_generator_settings", self.on_save):
            ui_state.slicer_profile_changed(self)


def _write_remembered(profile, generator="map", mtime=None):
    os.makedirs(constants.generator_settings_dir, exist_ok=True)
    path = os.path.join(constants.generator_settings_dir, f"{generator}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"slicerProfile": profile}, f)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def _with_preferred(stored, available):
    """Patch the Default 3MF Export Profile preference: *stored* is what the
    preference holds, *available* the profile names that currently exist."""
    prefs = MagicMock(default_slicer_profile_id=stored)
    items = [(n, n, "") for n in available]
    return (
        patch.object(addon_preferences, "get_prefs", return_value=prefs),
        patch("TrailPrint3D.props._slicer_profile_items_uncached", return_value=items),
    )


def test_default_profile_pref_applies_once_to_a_new_scene():
    scene = _StubScene()
    p1, p2 = _with_preferred("My Printer", ["NONE", "My Printer"])
    with p1, p2:
        ui_state.apply_default_slicer_profile(scene)
        assert scene.slicer_profile_name == "My Printer"
        scene.slicer_profile_name = "NONE"  # a later sidebar pick must stick
        ui_state.apply_default_slicer_profile(scene)
        assert scene.slicer_profile_name == "NONE"


def test_remember_last_used_with_nothing_remembered_changes_nothing():
    scene = _StubScene()
    p1, p2 = _with_preferred("", ["NONE", "My Printer"])
    with p1, p2:
        ui_state.apply_default_slicer_profile(scene)
    assert scene.slicer_profile_name == "NONE"


def test_default_profile_pref_waits_for_an_unavailable_profile():
    scene = _StubScene()
    p1, p2 = _with_preferred("My Printer", ["NONE"])  # 3MF add-on not loaded yet
    with p1, p2:
        ui_state.apply_default_slicer_profile(scene)
        assert scene.slicer_profile_name == "NONE"
    p1, p2 = _with_preferred("My Printer", ["NONE", "My Printer"])
    with p1, p2:
        ui_state.apply_default_slicer_profile(scene)
    assert scene.slicer_profile_name == "My Printer"


def test_default_profile_pref_skips_saved_files():
    scene = _StubScene()
    p1, p2 = _with_preferred("My Printer", ["NONE", "My Printer"])
    with p1, p2, patch.object(ui_state.bpy, "data", MagicMock(filepath="/tmp/x.blend")):
        ui_state.apply_default_slicer_profile(scene)
    assert scene.slicer_profile_name == "NONE"


def test_remembered_profile_is_overridden_only_by_an_explicit_pref():
    snapshot = {"slicerProfile": "Remembered"}
    scene = _StubScene()
    scene.slicer_profile_name = "NONE"
    p1, p2 = _with_preferred("My Printer", ["NONE", "My Printer", "Remembered"])
    with p1, p2:
        ui_state.apply_generator_settings_snapshot(scene, snapshot)
    assert scene.slicer_profile_name == "NONE"  # pref set: remembered one ignored

    p1, p2 = _with_preferred("", ["NONE", "My Printer", "Remembered"])
    with p1, p2:
        ui_state.apply_generator_settings_snapshot(scene, snapshot)
    assert scene.slicer_profile_name == "Remembered"  # pref unset: as before


def test_remember_last_used_applies_the_remembered_profile_at_startup():
    _write_remembered("My Printer")
    scene = _StubScene()
    p1, p2 = _with_preferred("", ["NONE", "My Printer"])
    with p1, p2:
        ui_state.apply_default_slicer_profile(scene)
    assert scene.slicer_profile_name == "My Printer"


def test_remember_last_used_takes_the_most_recently_saved_generator():
    _write_remembered("Older", "map", mtime=1000)
    _write_remembered("Newer", "puzzle", mtime=2000)
    scene = _StubScene()
    p1, p2 = _with_preferred("", ["NONE", "Older", "Newer"])
    with p1, p2:
        ui_state.apply_default_slicer_profile(scene)
    assert scene.slicer_profile_name == "Newer"


def test_explicit_pref_beats_the_remembered_profile_at_startup():
    _write_remembered("Remembered")
    scene = _StubScene()
    p1, p2 = _with_preferred("My Printer", ["NONE", "My Printer", "Remembered"])
    with p1, p2:
        ui_state.apply_default_slicer_profile(scene)
    assert scene.slicer_profile_name == "My Printer"


def test_sidebar_pick_is_saved_but_addon_writes_are_not():
    save = MagicMock()
    _StubScene.on_save = staticmethod(save)
    try:
        scene = _StubScene()
        scene.slicer_profile_name = "Picked"
        assert save.call_count == 1
        save.reset_mock()
        with ui_state._suppress_slicer_profile_save():
            scene.slicer_profile_name = "Restored"
        assert save.call_count == 0
        _write_remembered("My Printer")
        p1, p2 = _with_preferred("", ["NONE", "My Printer"])
        with p1, p2:
            ui_state.apply_default_slicer_profile(_StubScene())
        assert save.call_count == 0  # startup restore doesn't rewrite the file
    finally:
        _StubScene.on_save = staticmethod(lambda tp3d: None)


def test_default_profile_pref_stores_the_name_not_an_index():
    prefs = addon_preferences.get_prefs()
    prefs.default_slicer_profile = "NONE"
    assert prefs.default_slicer_profile_id == "NONE"
    prefs.default_slicer_profile = "__UNSET__"
    assert prefs.default_slicer_profile_id == ""
    # A profile that has gone away shows as "Remember last used" but is kept for when it returns.
    prefs.default_slicer_profile_id = "Gone Printer"
    assert prefs.default_slicer_profile == "__UNSET__"
    assert prefs.default_slicer_profile_id == "Gone Printer"
    assert addon_preferences.get_default_slicer_profile() is None
    prefs.default_slicer_profile_id = ""


_run("page edits survive into a new session", test_page_edits_survive_into_a_new_session)
_run("WorldCover keeps its forced element mode", test_worldcover_keeps_its_forced_element_mode)
_run("reopening a generator keeps sidebar changes", test_reopening_a_generator_keeps_sidebar_changes)
_run("generators keep separate settings across sessions", test_generators_keep_separate_settings_across_sessions)
_run("switching generators in one scene swaps their settings", test_switching_generators_in_one_scene_swaps_their_settings)
_run("opening a page doesn't overwrite saved settings", test_opening_a_page_does_not_overwrite_saved_settings)
_run("missing or broken file keeps defaults", test_missing_or_broken_file_keeps_defaults)
_run("unusable values are skipped", test_unusable_values_are_skipped)
_run("default profile pref applies once to a new scene", test_default_profile_pref_applies_once_to_a_new_scene)
_run("remember last used with nothing remembered changes nothing", test_remember_last_used_with_nothing_remembered_changes_nothing)
_run("default profile pref waits for an unavailable profile", test_default_profile_pref_waits_for_an_unavailable_profile)
_run("default profile pref skips saved files", test_default_profile_pref_skips_saved_files)
_run("remembered profile overridden only by an explicit pref", test_remembered_profile_is_overridden_only_by_an_explicit_pref)
_run("remember last used applies the remembered profile at startup", test_remember_last_used_applies_the_remembered_profile_at_startup)
_run("remember last used takes the most recently saved generator", test_remember_last_used_takes_the_most_recently_saved_generator)
_run("explicit pref beats the remembered profile at startup", test_explicit_pref_beats_the_remembered_profile_at_startup)
_run("sidebar pick is saved but add-on writes are not", test_sidebar_pick_is_saved_but_addon_writes_are_not)
_run("default profile pref stores the name, not an index", test_default_profile_pref_stores_the_name_not_an_index)

_TEMP_DIR.cleanup()
print(f"\n{_passed} passed, {_failed} failed")
if _failed:
    raise SystemExit(1)
