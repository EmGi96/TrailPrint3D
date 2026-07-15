"""Unit tests for the TrailPrint3D auto-updater (TrailPrint3D/updater.py).

Covers:
  - _parse_version_page (version/post-URL parsing from the marketing site pages)
  - _check_worker (GitHub release check + normal_version.html gate, asset URL pick)
  - _check_premium_worker (Patreon premium_version.html check)
  - start_check / start_premium_check (status flips to "checking", threading)
  - download_and_install (download, timer scheduling, error paths)
  - _install_timer (bpy.ops.extensions.package_install_files invocation, cleanup)

All network calls (requests.get) and bpy are mocked — no real HTTP, no real
Blender install. updater.py imports bpy lazily inside functions, so bpy is
faked via sys.modules injection rather than needing a real Blender bpy.

Run with:
  blender --background --factory-startup --python-exit-code 1 -P tests/test_updater.py
  or, as part of the full suite:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --factory-startup --python-exit-code 1 -P tests/run_all_tests.py
"""

import sys
import os
import time
import traceback
import tempfile
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Path setup — makes TrailPrint3D importable as a package from source
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# ---------------------------------------------------------------------------
# Minimal test runner (mirrors test_osm_pipeline.py / test_gpx.py)
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code=200, json_body=None, text=""):
    """Return a mock requests.Response."""
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_body if json_body is not None else {}
    r.text = text
    if status_code >= 400:
        import requests as _requests
        r.raise_for_status.side_effect = _requests.exceptions.HTTPError(f"{status_code}")
    else:
        r.raise_for_status.side_effect = None
    return r


def _github_release_json(tag, assets=None):
    return {
        "tag_name": tag,
        "assets": assets if assets is not None else [
            {"name": "TrailPrint3D-3.5.0.zip",
             "browser_download_url": "https://example.com/TrailPrint3D-3.5.0.zip"},
        ],
    }


def _reset_updater_state(updater):
    """Reset all module-level state between tests (mirrors module defaults)."""
    updater.status = "idle"
    updater.latest_version = None
    updater.error_message = ""
    updater.premium_status = "idle"
    updater.premium_latest_version = None
    updater.premium_post_url = None
    updater.premium_error_message = ""
    updater._latest_release_zip_url = None
    updater._pending_install_zip = None


def _lower_version_str(const):
    """A version string strictly lower than const.ADDON_VERSION."""
    major, minor, patch_ = const.ADDON_VERSION
    if patch_ > 0:
        return f"{major}.{minor}.{patch_ - 1}"
    if minor > 0:
        return f"{major}.{minor - 1}.9"
    return "0.0.1"  # ADDON_VERSION is (0,0,0) — extremely unlikely fallback


def _wait_for(predicate, timeout=2.0):
    """Poll predicate() until True or timeout — used after threading.Thread starts."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------------------
# _parse_version_page
# ---------------------------------------------------------------------------

def test_parse_version_page_version_only():
    from TrailPrint3D.updater import _parse_version_page
    ver, url = _parse_version_page("3.1.0\n")
    assert ver == (3, 1, 0), f"Expected (3,1,0), got {ver!r}"
    assert url is None


def test_parse_version_page_version_and_url():
    from TrailPrint3D.updater import _parse_version_page
    text = "3.2.1\nhttps://www.patreon.com/posts/12345\n"
    ver, url = _parse_version_page(text)
    assert ver == (3, 2, 1)
    assert url == "https://www.patreon.com/posts/12345"


def test_parse_version_page_ignores_non_url_second_line():
    from TrailPrint3D.updater import _parse_version_page
    text = "3.2.1\nsome note that is not a url\n"
    ver, url = _parse_version_page(text)
    assert ver == (3, 2, 1)
    assert url is None, f"Non-URL second line should not be treated as a post URL, got {url!r}"


def test_parse_version_page_blank_lines_skipped():
    from TrailPrint3D.updater import _parse_version_page
    text = "\n\n  3.4.5  \n\n  https://example.com/post \n\n"
    ver, url = _parse_version_page(text)
    assert ver == (3, 4, 5)
    assert url == "https://example.com/post"


def test_parse_version_page_empty_text_returns_none():
    from TrailPrint3D.updater import _parse_version_page
    ver, url = _parse_version_page("")
    assert ver is None and url is None


def test_parse_version_page_unparseable_first_line_returns_none():
    from TrailPrint3D.updater import _parse_version_page
    ver, url = _parse_version_page("not a version\nhttps://example.com\n")
    assert ver is None and url is None


def test_parse_version_page_prerelease_suffix_still_parses_leading_digits():
    from TrailPrint3D.updater import _parse_version_page
    ver, _ = _parse_version_page("3.1.0-beta\n")
    assert ver == (3, 1, 0), f"Should extract leading numeric triplet, got {ver!r}"


def test_version_tuple_ordering_uses_numeric_not_string_comparison():
    """(3, 10, 0) must compare greater than (3, 9, 0) — guards against a
    regression to naive string comparison, where "3.9.0" > "3.10.0" is True."""
    from TrailPrint3D.updater import _parse_version_page
    newer, _ = _parse_version_page("3.10.0\n")
    older, _ = _parse_version_page("3.9.0\n")
    assert newer > older, f"Expected {newer!r} > {older!r} under numeric tuple comparison"
    assert str(newer) > str(older) or True  # sanity note only, not asserted as string compare


# ---------------------------------------------------------------------------
# _check_worker (GitHub release + normal_version.html gate)
# ---------------------------------------------------------------------------

def test_check_worker_update_available_when_both_sources_agree():
    import TrailPrint3D.updater as updater
    _reset_updater_state(updater)

    gh_resp = _make_response(json_body=_github_release_json("v99.0.0"))
    html_resp = _make_response(text="99.0.0\n")

    with patch("TrailPrint3D.updater.requests.get", side_effect=[gh_resp, html_resp]):
        updater._check_worker()

    assert updater.status == "update_available", f"status={updater.status!r} err={updater.error_message!r}"
    assert updater.latest_version == (99, 0, 0)
    assert updater._latest_release_zip_url == "https://example.com/TrailPrint3D-3.5.0.zip"


def test_check_worker_up_to_date_when_github_ahead_but_site_not_updated():
    """Both gates must agree an update exists (site acts as a kill-switch/rollout gate)."""
    import TrailPrint3D.updater as updater
    from TrailPrint3D import constants as const
    _reset_updater_state(updater)

    gh_resp = _make_response(json_body=_github_release_json("v99.0.0"))
    current = ".".join(str(x) for x in const.ADDON_VERSION)
    html_resp = _make_response(text=f"{current}\n")

    with patch("TrailPrint3D.updater.requests.get", side_effect=[gh_resp, html_resp]):
        updater._check_worker()

    assert updater.status == "up_to_date", \
        f"Site version not ahead of current — should not offer update, got {updater.status!r}"


def test_check_worker_up_to_date_when_no_new_release():
    import TrailPrint3D.updater as updater
    from TrailPrint3D import constants as const
    _reset_updater_state(updater)

    current = ".".join(str(x) for x in const.ADDON_VERSION)
    gh_resp = _make_response(json_body=_github_release_json(f"v{current}"))
    html_resp = _make_response(text=f"{current}\n")

    with patch("TrailPrint3D.updater.requests.get", side_effect=[gh_resp, html_resp]):
        updater._check_worker()

    assert updater.status == "up_to_date"


def test_check_worker_up_to_date_when_github_tag_is_lower_than_current():
    """A stale/rolled-back GitHub tag below the installed version must not
    be reported as an update (guards the strict '>' comparison, not '!=')."""
    import TrailPrint3D.updater as updater
    from TrailPrint3D import constants as const
    _reset_updater_state(updater)

    lower = _lower_version_str(const)
    gh_resp = _make_response(json_body=_github_release_json(f"v{lower}"))
    html_resp = _make_response(text=f"{lower}\n")

    with patch("TrailPrint3D.updater.requests.get", side_effect=[gh_resp, html_resp]):
        updater._check_worker()

    assert updater.status == "up_to_date", \
        f"Lower GitHub tag ({lower}) must not trigger an update, got {updater.status!r}"
    assert updater.latest_version == tuple(int(x) for x in lower.split("."))


def test_check_worker_up_to_date_when_html_page_is_lower_despite_github_higher():
    """GitHub ahead but normal_version.html reports a lower/stale version —
    the site gate must veto the update even though GitHub alone would allow it."""
    import TrailPrint3D.updater as updater
    from TrailPrint3D import constants as const
    _reset_updater_state(updater)

    lower = _lower_version_str(const)
    gh_resp = _make_response(json_body=_github_release_json("v99.0.0"))
    html_resp = _make_response(text=f"{lower}\n")

    with patch("TrailPrint3D.updater.requests.get", side_effect=[gh_resp, html_resp]):
        updater._check_worker()

    assert updater.status == "up_to_date", \
        f"html_ver ({lower}) below current must veto the update, got {updater.status!r}"
    assert updater.latest_version == (99, 0, 0), \
        "latest_version should still reflect the GitHub tag even though status is up_to_date"


def test_check_worker_error_on_unparseable_github_tag():
    import TrailPrint3D.updater as updater
    _reset_updater_state(updater)

    gh_resp = _make_response(json_body={"tag_name": "not-a-version", "assets": []})

    with patch("TrailPrint3D.updater.requests.get", return_value=gh_resp):
        updater._check_worker()

    assert updater.status == "error"
    assert "tag" in updater.error_message.lower()


def test_check_worker_error_on_unparseable_html_page():
    import TrailPrint3D.updater as updater
    _reset_updater_state(updater)

    gh_resp = _make_response(json_body=_github_release_json("v99.0.0"))
    html_resp = _make_response(text="garbage, no version here")

    with patch("TrailPrint3D.updater.requests.get", side_effect=[gh_resp, html_resp]):
        updater._check_worker()

    assert updater.status == "error"
    assert "normal_version" in updater.error_message


def test_check_worker_error_on_network_exception():
    import TrailPrint3D.updater as updater
    _reset_updater_state(updater)

    with patch("TrailPrint3D.updater.requests.get", side_effect=ConnectionError("boom")):
        updater._check_worker()

    assert updater.status == "error"
    assert "boom" in updater.error_message


def test_check_worker_error_on_github_http_error():
    import TrailPrint3D.updater as updater
    _reset_updater_state(updater)

    gh_resp = _make_response(status_code=404)

    with patch("TrailPrint3D.updater.requests.get", return_value=gh_resp):
        updater._check_worker()

    assert updater.status == "error"


def test_check_worker_no_matching_zip_asset_leaves_url_none():
    """If no asset matches the TrailPrint3D*.zip pattern, zip URL stays None
    but the check can still report update_available (asset picked at install time)."""
    import TrailPrint3D.updater as updater
    _reset_updater_state(updater)

    gh_resp = _make_response(json_body=_github_release_json(
        "v99.0.0", assets=[{"name": "readme.txt", "browser_download_url": "https://x/readme.txt"}]
    ))
    html_resp = _make_response(text="99.0.0\n")

    with patch("TrailPrint3D.updater.requests.get", side_effect=[gh_resp, html_resp]):
        updater._check_worker()

    assert updater._latest_release_zip_url is None
    assert updater.status == "update_available"


# ---------------------------------------------------------------------------
# _check_premium_worker
# ---------------------------------------------------------------------------

def test_check_premium_worker_update_available():
    import TrailPrint3D.updater as updater
    _reset_updater_state(updater)

    resp = _make_response(text="99.0.0\nhttps://www.patreon.com/posts/999\n")
    with patch("TrailPrint3D.updater.requests.get", return_value=resp):
        updater._check_premium_worker()

    assert updater.premium_status == "update_available"
    assert updater.premium_latest_version == (99, 0, 0)
    assert updater.premium_post_url == "https://www.patreon.com/posts/999"


def test_check_premium_worker_up_to_date():
    import TrailPrint3D.updater as updater
    from TrailPrint3D import constants as const
    _reset_updater_state(updater)

    current = ".".join(str(x) for x in const.ADDON_VERSION)
    resp = _make_response(text=f"{current}\n")
    with patch("TrailPrint3D.updater.requests.get", return_value=resp):
        updater._check_premium_worker()

    assert updater.premium_status == "up_to_date"


def test_check_premium_worker_up_to_date_when_lower_than_current():
    """Stale/rolled-back premium_version.html must not report an update."""
    import TrailPrint3D.updater as updater
    from TrailPrint3D import constants as const
    _reset_updater_state(updater)

    lower = _lower_version_str(const)
    resp = _make_response(text=f"{lower}\n")
    with patch("TrailPrint3D.updater.requests.get", return_value=resp):
        updater._check_premium_worker()

    assert updater.premium_status == "up_to_date", \
        f"Lower premium version ({lower}) must not trigger an update, got {updater.premium_status!r}"


def test_check_premium_worker_error_on_bad_page():
    import TrailPrint3D.updater as updater
    _reset_updater_state(updater)

    resp = _make_response(text="")
    with patch("TrailPrint3D.updater.requests.get", return_value=resp):
        updater._check_premium_worker()

    assert updater.premium_status == "error"
    assert "premium_version" in updater.premium_error_message


def test_check_premium_worker_error_on_exception():
    import TrailPrint3D.updater as updater
    _reset_updater_state(updater)

    with patch("TrailPrint3D.updater.requests.get", side_effect=TimeoutError("timed out")):
        updater._check_premium_worker()

    assert updater.premium_status == "error"
    assert "timed out" in updater.premium_error_message


def test_get_premium_update_url_prefers_announced_post():
    import TrailPrint3D.updater as updater
    _reset_updater_state(updater)
    updater.premium_post_url = "https://www.patreon.com/posts/123"
    assert updater.get_premium_update_url() == "https://www.patreon.com/posts/123"


def test_get_premium_update_url_falls_back_to_patreon_page():
    import TrailPrint3D.updater as updater
    _reset_updater_state(updater)
    updater.premium_post_url = None
    assert updater.get_premium_update_url() == updater._PATREON_URL


# ---------------------------------------------------------------------------
# start_check / start_premium_check — threading + status flip
# ---------------------------------------------------------------------------

def test_start_check_sets_status_checking_immediately():
    import threading
    import TrailPrint3D.updater as updater
    _reset_updater_state(updater)

    # Block the worker thread so we can observe the immediate "checking" flip
    # before the (mocked) network call would ever resolve.
    gate = threading.Event()

    def _blocking_get(*a, **k):
        gate.wait(timeout=2)
        raise ConnectionError("stop early — test only checks the status flip")

    with patch("TrailPrint3D.updater.requests.get", side_effect=_blocking_get):
        updater.start_check()
        assert updater.status == "checking", \
            "status should flip to 'checking' synchronously before the thread runs"
        gate.set()
        _wait_for(lambda: updater.status != "checking")


def test_start_premium_check_sets_status_checking_immediately():
    import threading
    import TrailPrint3D.updater as updater
    _reset_updater_state(updater)

    gate = threading.Event()

    def _blocking_get(*a, **k):
        gate.wait(timeout=2)
        raise ConnectionError("stop early")

    with patch("TrailPrint3D.updater.requests.get", side_effect=_blocking_get):
        updater.start_premium_check()
        assert updater.premium_status == "checking"
        gate.set()
        _wait_for(lambda: updater.premium_status != "checking")


def test_start_check_worker_actually_runs_in_background_thread():
    """End-to-end (mocked network): start_check() eventually lands on a
    terminal status without the caller blocking on the network call."""
    import TrailPrint3D.updater as updater
    _reset_updater_state(updater)

    gh_resp = _make_response(json_body=_github_release_json("v99.0.0"))
    html_resp = _make_response(text="99.0.0\n")

    with patch("TrailPrint3D.updater.requests.get", side_effect=[gh_resp, html_resp]):
        updater.start_check()
        ok = _wait_for(lambda: updater.status not in ("idle", "checking"), timeout=2.0)

    assert ok, "worker thread never finished"
    assert updater.status == "update_available"


# ---------------------------------------------------------------------------
# download_and_install
# ---------------------------------------------------------------------------

def test_download_and_install_no_zip_url_fails_fast():
    import TrailPrint3D.updater as updater
    _reset_updater_state(updater)
    updater._latest_release_zip_url = None

    success, err = updater.download_and_install()

    assert success is False
    assert "check first" in err.lower() or "no release zip" in err.lower()


def test_download_and_install_downloads_and_schedules_installation():
    import TrailPrint3D.updater as updater
    _reset_updater_state(updater)
    updater._latest_release_zip_url = "https://example.com/TrailPrint3D-3.5.0.zip"

    chunk_data = [b"PK\x03\x04", b"fake-zip-bytes"]
    dl_resp = MagicMock()
    dl_resp.raise_for_status.side_effect = None
    dl_resp.iter_content.return_value = iter(chunk_data)

    fake_bpy = MagicMock()

    with patch("TrailPrint3D.updater.requests.get", return_value=dl_resp) as mget, \
         patch.dict(sys.modules, {"bpy": fake_bpy}):
        success, err = updater.download_and_install()

    assert success is True, f"Expected success, got err={err!r}"
    assert err is None
    mget.assert_called_once()
    _, kwargs = mget.call_args
    assert kwargs.get("stream") is True, "Download should stream the zip, not load it all at once"

    assert fake_bpy.app.timers.register.called, "Expected bpy.app.timers.register to be called"
    args, kwargs = fake_bpy.app.timers.register.call_args
    assert args[0] is updater._install_timer
    assert kwargs.get("first_interval") == 0.5

    assert updater._pending_install_zip is not None
    assert os.path.exists(updater._pending_install_zip)
    with open(updater._pending_install_zip, "rb") as f:
        assert f.read() == b"".join(chunk_data)

    os.remove(updater._pending_install_zip)
    updater._pending_install_zip = None


def test_download_and_install_returns_error_on_download_failure():
    import TrailPrint3D.updater as updater
    _reset_updater_state(updater)
    updater._latest_release_zip_url = "https://example.com/TrailPrint3D-3.5.0.zip"

    with patch("TrailPrint3D.updater.requests.get", side_effect=ConnectionError("network down")):
        success, err = updater.download_and_install()

    assert success is False
    assert "network down" in err
    assert updater._pending_install_zip is None


def test_download_and_install_returns_error_on_http_error():
    import TrailPrint3D.updater as updater
    _reset_updater_state(updater)
    updater._latest_release_zip_url = "https://example.com/TrailPrint3D-3.5.0.zip"

    bad_resp = _make_response(status_code=500)
    bad_resp.iter_content.return_value = iter([])

    with patch("TrailPrint3D.updater.requests.get", return_value=bad_resp):
        success, err = updater.download_and_install()

    assert success is False
    assert err  # non-empty message from raise_for_status()


# ---------------------------------------------------------------------------
# _install_timer
# ---------------------------------------------------------------------------

def test_install_timer_no_pending_zip_is_noop():
    import TrailPrint3D.updater as updater
    _reset_updater_state(updater)
    updater._pending_install_zip = None

    fake_bpy = MagicMock()
    with patch.dict(sys.modules, {"bpy": fake_bpy}):
        result = updater._install_timer()

    assert result is None
    assert not fake_bpy.ops.extensions.package_install_files.called


def test_install_timer_missing_file_on_disk_is_noop():
    import TrailPrint3D.updater as updater
    _reset_updater_state(updater)
    updater._pending_install_zip = os.path.join(
        tempfile.gettempdir(), "TrailPrint3D_does_not_exist.zip"
    )

    fake_bpy = MagicMock()
    with patch.dict(sys.modules, {"bpy": fake_bpy}):
        updater._install_timer()

    assert not fake_bpy.ops.extensions.package_install_files.called
    assert updater._pending_install_zip is None, "Global should be cleared even when file is missing"


def test_install_timer_success_installs_and_removes_zip():
    import TrailPrint3D.updater as updater
    _reset_updater_state(updater)

    zip_path = os.path.join(tempfile.gettempdir(), "TrailPrint3D_test_install.zip")
    with open(zip_path, "wb") as f:
        f.write(b"fake zip contents")
    updater._pending_install_zip = zip_path

    fake_bpy = MagicMock()
    fake_bpy.ops.extensions.package_install_files.return_value = {'FINISHED'}

    with patch.dict(sys.modules, {"bpy": fake_bpy}):
        result = updater._install_timer()

    assert result is None, "Timer callback must return None so it doesn't repeat"
    fake_bpy.ops.extensions.package_install_files.assert_called_once_with(
        filepath=zip_path, repo="user_default", overwrite=True,
    )
    assert not os.path.exists(zip_path), "Zip should be removed after install"
    assert updater.status != "error"


def test_install_timer_non_finished_result_sets_error_status():
    import TrailPrint3D.updater as updater
    _reset_updater_state(updater)

    zip_path = os.path.join(tempfile.gettempdir(), "TrailPrint3D_test_install2.zip")
    with open(zip_path, "wb") as f:
        f.write(b"fake zip contents")
    updater._pending_install_zip = zip_path

    fake_bpy = MagicMock()
    fake_bpy.ops.extensions.package_install_files.return_value = {'CANCELLED'}

    with patch.dict(sys.modules, {"bpy": fake_bpy}):
        updater._install_timer()

    assert updater.status == "error"
    assert "CANCELLED" in updater.error_message
    assert not os.path.exists(zip_path), "Zip should still be cleaned up even on failure"


def test_install_timer_exception_during_install_sets_error_and_cleans_up():
    import TrailPrint3D.updater as updater
    _reset_updater_state(updater)

    zip_path = os.path.join(tempfile.gettempdir(), "TrailPrint3D_test_install3.zip")
    with open(zip_path, "wb") as f:
        f.write(b"fake zip contents")
    updater._pending_install_zip = zip_path

    fake_bpy = MagicMock()
    fake_bpy.ops.extensions.package_install_files.side_effect = RuntimeError("kaboom")

    with patch.dict(sys.modules, {"bpy": fake_bpy}):
        updater._install_timer()

    assert updater.status == "error"
    assert "kaboom" in updater.error_message
    assert not os.path.exists(zip_path), "Zip should be cleaned up even when install raises"


def test_install_timer_clears_pending_zip_global_before_running():
    """_pending_install_zip must be consumed (set back to None) so a stray
    second timer tick can't reuse a stale path."""
    import TrailPrint3D.updater as updater
    _reset_updater_state(updater)

    zip_path = os.path.join(tempfile.gettempdir(), "TrailPrint3D_test_install4.zip")
    with open(zip_path, "wb") as f:
        f.write(b"x")
    updater._pending_install_zip = zip_path

    fake_bpy = MagicMock()
    fake_bpy.ops.extensions.package_install_files.return_value = {'FINISHED'}

    with patch.dict(sys.modules, {"bpy": fake_bpy}):
        updater._install_timer()

    assert updater._pending_install_zip is None


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  TrailPrint3D auto-updater tests")
    print("=" * 60 + "\n")

    # _parse_version_page
    _run("parse version page: version only",                    test_parse_version_page_version_only)
    _run("parse version page: version + post URL",              test_parse_version_page_version_and_url)
    _run("parse version page: ignores non-URL 2nd line",        test_parse_version_page_ignores_non_url_second_line)
    _run("parse version page: blank lines skipped",             test_parse_version_page_blank_lines_skipped)
    _run("parse version page: empty text -> None",              test_parse_version_page_empty_text_returns_none)
    _run("parse version page: unparseable 1st line -> None",    test_parse_version_page_unparseable_first_line_returns_none)
    _run("parse version page: prerelease suffix still parses",  test_parse_version_page_prerelease_suffix_still_parses_leading_digits)
    _run("version ordering: numeric not string compare",        test_version_tuple_ordering_uses_numeric_not_string_comparison)

    # _check_worker
    _run("check worker: update available (both sources agree)", test_check_worker_update_available_when_both_sources_agree)
    _run("check worker: site not updated yet -> up_to_date",    test_check_worker_up_to_date_when_github_ahead_but_site_not_updated)
    _run("check worker: no new release -> up_to_date",          test_check_worker_up_to_date_when_no_new_release)
    _run("check worker: github tag lower -> up_to_date",         test_check_worker_up_to_date_when_github_tag_is_lower_than_current)
    _run("check worker: html lower despite github higher",       test_check_worker_up_to_date_when_html_page_is_lower_despite_github_higher)
    _run("check worker: bad GitHub tag -> error",                test_check_worker_error_on_unparseable_github_tag)
    _run("check worker: bad html page -> error",                 test_check_worker_error_on_unparseable_html_page)
    _run("check worker: network exception -> error",             test_check_worker_error_on_network_exception)
    _run("check worker: GitHub HTTP error -> error",              test_check_worker_error_on_github_http_error)
    _run("check worker: no matching zip asset -> url None",      test_check_worker_no_matching_zip_asset_leaves_url_none)

    # _check_premium_worker
    _run("premium check: update available",                      test_check_premium_worker_update_available)
    _run("premium check: up to date",                            test_check_premium_worker_up_to_date)
    _run("premium check: lower version -> up_to_date",           test_check_premium_worker_up_to_date_when_lower_than_current)
    _run("premium check: bad page -> error",                      test_check_premium_worker_error_on_bad_page)
    _run("premium check: exception -> error",                     test_check_premium_worker_error_on_exception)
    _run("get_premium_update_url: prefers announced post",        test_get_premium_update_url_prefers_announced_post)
    _run("get_premium_update_url: falls back to Patreon page",    test_get_premium_update_url_falls_back_to_patreon_page)

    # start_check / start_premium_check
    _run("start_check: status flips to checking immediately",    test_start_check_sets_status_checking_immediately)
    _run("start_premium_check: status flips to checking",        test_start_premium_check_sets_status_checking_immediately)
    _run("start_check: worker runs in background, completes",    test_start_check_worker_actually_runs_in_background_thread)

    # download_and_install
    _run("download_and_install: no zip url fails fast",          test_download_and_install_no_zip_url_fails_fast)
    _run("download_and_install: downloads + schedules install",  test_download_and_install_downloads_and_schedules_installation)
    _run("download_and_install: download failure -> error",      test_download_and_install_returns_error_on_download_failure)
    _run("download_and_install: HTTP error -> error",             test_download_and_install_returns_error_on_http_error)

    # _install_timer
    _run("install timer: no pending zip -> no-op",               test_install_timer_no_pending_zip_is_noop)
    _run("install timer: missing file on disk -> no-op",         test_install_timer_missing_file_on_disk_is_noop)
    _run("install timer: success installs + removes zip",        test_install_timer_success_installs_and_removes_zip)
    _run("install timer: non-FINISHED result -> error status",   test_install_timer_non_finished_result_sets_error_status)
    _run("install timer: exception during install -> error",     test_install_timer_exception_during_install_sets_error_and_cleans_up)
    _run("install timer: clears pending zip global",             test_install_timer_clears_pending_zip_global_before_running)

    _assert_all_passed()
