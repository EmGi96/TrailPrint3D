"""Tests for the online access check in front of the 3MF addon install.

Run with:
  blender --background --factory-startup --python-exit-code 1 -P tests/test_online_access.py
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


def _set_online(value):
    bpy.context.preferences.system.use_online_access = value


def test_state_follows_the_preference():
    _set_online(False)
    assert utils.build_online_access_state() == {"onlineAccess": False, "onlineAccessLocked": False}
    _set_online(True)
    assert utils.build_online_access_state() == {"onlineAccess": True, "onlineAccessLocked": False}
    _set_online(False)


def test_settings_row_state_carries_it():
    _set_online(False)
    state = utils.build_settings_row_state(bpy.context.scene.tp3d)
    assert state["onlineAccess"] is False, state
    assert state["onlineAccessLocked"] is False, state


def test_install_is_refused_without_raising_when_offline():
    # Called through bpy.ops like the generator modals, where a raise would
    # abort the modal.
    _set_online(False)
    assert bpy.ops.tp3d.install_three_mf() == {"CANCELLED"}


for _name, _fn in list(globals().items()):
    if _name.startswith("test_") and callable(_fn):
        _run(_name, _fn)

_assert_all_passed()
