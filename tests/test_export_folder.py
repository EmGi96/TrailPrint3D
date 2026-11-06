"""Export-folder dialog regression tests (no interactive dialogs).

Run with:
  blender --background --factory-startup --python-exit-code 1 -P tests/test_export_folder.py
"""

import os
import pathlib
import queue
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

from TrailPrint3D import picker_server as picker

_passed = 0
_failed = 0


def _run(name, fn):
    global _passed, _failed
    try:
        with patch.object(picker.sys, "platform", "win32"), \
                patch.object(picker.sp, "CREATE_NO_WINDOW", 0x08000000, create=True):
            fn()
        print(f"  PASS  {name}")
        _passed += 1
    except Exception:
        print(f"  FAIL  {name}")
        traceback.print_exc()
        _failed += 1


def _dialog_result(stdout, returncode=0, stderr=b""):
    proc = MagicMock(pid=12345, returncode=returncode)
    proc.communicate.return_value = (stdout, stderr)
    return proc


def test_windows_modern_picker_and_initial_path():
    with tempfile.TemporaryDirectory(prefix="TP3D ' folder ") as folder:
        proc = _dialog_result((folder + "\r\n").encode("utf-8"))
        with patch.object(picker.sp, "Popen", return_value=proc) as popen, \
                patch.dict(picker._export_folder_job, {}, clear=True):
            def communicate():
                assert picker._export_folder_job["pid"] == proc.pid
                return (folder + "\r\n").encode("utf-8"), b""

            proc.communicate.side_effect = communicate
            assert picker._pick_folder_dialog(folder) == folder
            args, kwargs = popen.call_args
            command = args[0]
            assert "-STA" in command
            assert "[TP3DFolderPicker]::Pick(" in command[-1]
            assert "FolderBrowserDialog" not in command[-1]
            assert "$owner.Dispose()" in command[-1]
            assert folder not in command[-1]
            assert kwargs["env"]["TP3D_INITIAL_DIR"] == folder
            source = pathlib.Path(kwargs["env"]["TP3D_FOLDER_PICKER_SOURCE"])
            assert source.is_file()
            assert source.name == "windows_folder_picker.cs"
            assert kwargs["creationflags"] == picker.sp.CREATE_NO_WINDOW
            assert picker._export_folder_job["pid"] is None


def test_cancel_returns_empty_and_clears_pid():
    with patch.object(picker.sp, "Popen", return_value=_dialog_result(b"")), \
            patch.dict(picker._export_folder_job, {}, clear=True):
        assert picker._pick_folder_dialog("") == ""
        assert picker._export_folder_job["pid"] is None


def test_process_failure_is_logged():
    with patch.object(picker.sp, "Popen", return_value=_dialog_result(
            b"", 1, b"COM dialog failed")), patch("builtins.print") as log:
        assert picker._pick_folder_dialog("") == ""
        assert "COM dialog failed" in log.call_args.args[0]
        assert "code 1" in log.call_args.args[0]


def test_launch_failure_is_logged():
    with patch.object(picker.sp, "Popen", side_effect=OSError("cannot launch")), \
            patch("builtins.print") as log:
        assert picker._pick_folder_dialog("") == ""
        assert "cannot launch" in log.call_args.args[0]


def test_nonexistent_selection_is_rejected():
    with tempfile.TemporaryDirectory() as folder:
        missing = os.path.join(folder, "does-not-exist")
        with patch.object(picker.sp, "Popen", return_value=_dialog_result(missing.encode())):
            assert picker._pick_folder_dialog("") == ""


def test_pick_updates_default_but_preserves_scene_override():
    with tempfile.TemporaryDirectory() as folder, \
            patch.object(picker, "_pick_folder_dialog", return_value=folder) as dialog, \
            patch.object(picker, "_pending_settings", queue.Queue()), \
            patch.dict(picker._export_folder_job, {
                "status": "picking", "scene": "scene-override", "default": "previous-default",
            }, clear=True):
        picker._run_export_folder_pick()
        dialog.assert_called_once_with("previous-default")
        expected = os.path.join(folder, "")
        assert picker._pending_settings.get_nowait() == ("exportFolder", expected)
        assert picker._export_folder_job["default"] == expected
        assert picker._export_folder_job["scene"] == "scene-override"
        assert picker._export_folder_job["status"] == "done"


def test_cancel_does_not_change_preferences():
    with patch.object(picker, "_pick_folder_dialog", return_value=""), \
            patch.object(picker, "_pending_settings", queue.Queue()), \
            patch.dict(picker._export_folder_job, {
                "status": "picking", "scene": "", "default": "previous-default",
            }, clear=True):
        picker._run_export_folder_pick()
        assert picker._pending_settings.empty()
        assert picker._export_folder_job["default"] == "previous-default"
        assert picker._export_folder_job["status"] == "cancelled"


for _test in (
    test_windows_modern_picker_and_initial_path,
    test_cancel_returns_empty_and_clears_pid,
    test_process_failure_is_logged,
    test_launch_failure_is_logged,
    test_nonexistent_selection_is_rejected,
    test_pick_updates_default_but_preserves_scene_override,
    test_cancel_does_not_change_preferences,
):
    _run(_test.__name__, _test)

print(f"\n{_passed} passed, {_failed} failed")
if _failed:
    raise SystemExit(1)
