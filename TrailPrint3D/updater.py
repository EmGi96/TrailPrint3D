#  Copyright (C) 2026  EmGi

import re
import threading
import os
import tempfile

import requests

from . import constants as const

_GITHUB_OWNER = "EmGi96"
_GITHUB_REPO = "TrailPrint3D"

_GITHUB_RELEASES_API_URL = (
    f"https://api.github.com/repos/{_GITHUB_OWNER}/{_GITHUB_REPO}/releases/latest"
)

_latest_release_zip_url = None  # populated by _check_worker from the release asset URL
_pending_install_zip = None     # set by download_and_install; consumed by _install_timer

_NORMAL_VERSION_URL = "https://trailprint3d.com/normal_version.html"
_PREMIUM_VERSION_URL = "https://trailprint3d.com/premium_version.html"
_PATREON_URL = "https://www.patreon.com/c/EmGi3D"

# --- Module-level state (read from the UI draw call) ---
status = "idle"         # idle | checking | update_available | up_to_date | error
latest_version = None   # tuple e.g. (3, 1, 0), or None
error_message = ""

# --- Premium (Patreon) version check state ---
premium_status = "idle"         # idle | checking | update_available | up_to_date | error
premium_latest_version = None   # tuple e.g. (3, 1, 0), or None
premium_post_url = None         # specific Patreon post URL announced for this update, or None
premium_error_message = ""


def _check_worker():
    global status, latest_version, error_message, _latest_release_zip_url
    try:
        resp = requests.get(
            _GITHUB_RELEASES_API_URL,
            timeout=10,
            headers={"Accept": "application/vnd.github+json"},
        )
        resp.raise_for_status()
        data = resp.json()
        tag = data.get("tag_name", "")
        m = re.search(r'(\d+)\.(\d+)\.(\d+)', tag)
        if not m:
            status = "error"
            error_message = "Could not parse version from GitHub release tag"
            return
        ver = tuple(int(x) for x in m.groups())
        latest_version = ver
        assets = data.get("assets", [])
        asset = next(
            (a for a in assets
             if "TrailPrint3D" in a.get("name", "") and a.get("name", "").endswith(".zip")),
            None,
        )
        _latest_release_zip_url = asset["browser_download_url"] if asset else None

        html_resp = requests.get(_NORMAL_VERSION_URL, timeout=10)
        html_resp.raise_for_status()
        html_ver, _ = _parse_version_page(html_resp.text)
        if html_ver is None:
            status = "error"
            error_message = "Could not parse version from normal_version.html"
            return

        status = (
            "update_available"
            if ver > const.ADDON_VERSION and html_ver > const.ADDON_VERSION
            else "up_to_date"
        )
    except Exception as e:
        status = "error"
        error_message = str(e)


def start_check():
    """Start a background version check. Non-blocking."""
    global status
    status = "checking"
    threading.Thread(target=_check_worker, daemon=True).start()


def _parse_version_page(text):
    """
    normal_version.html / premium_version.html hold the latest version number
    on their own line (e.g. "3.1.0"), optionally followed by a line with a
    link (e.g. to the Patreon post announcing that update).
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None, None
    m = re.search(r'(\d+)\.(\d+)\.(\d+)', lines[0])
    if not m:
        return None, None
    version = tuple(int(x) for x in m.groups())
    post_url = lines[1] if len(lines) > 1 and lines[1].startswith(("http://", "https://")) else None
    return version, post_url


def _check_premium_worker():
    global premium_status, premium_latest_version, premium_post_url, premium_error_message
    try:
        resp = requests.get(_PREMIUM_VERSION_URL, timeout=10)
        resp.raise_for_status()
        ver, post_url = _parse_version_page(resp.text)
        if ver is None:
            premium_status = "error"
            premium_error_message = "Could not parse version from premium_version.html"
            return
        premium_latest_version = ver
        premium_post_url = post_url
        premium_status = "update_available" if ver > const.ADDON_VERSION else "up_to_date"
    except Exception as e:
        premium_status = "error"
        premium_error_message = str(e)


def start_premium_check():
    """Start a background version check for the premium (Patreon) version. Non-blocking."""
    global premium_status
    premium_status = "checking"
    threading.Thread(target=_check_premium_worker, daemon=True).start()


def get_premium_update_url():
    """Link to open for the premium update: the announced Patreon post if any, else the Patreon page."""
    return premium_post_url or _PATREON_URL


def _install_timer():
    """
    Timer callback that runs after the calling operator has fully returned,
    so our extension's RNA types are no longer on the call stack when
    package_install_files unregisters/re-registers us.
    """
    global _pending_install_zip, status, error_message
    import bpy
    path = _pending_install_zip
    _pending_install_zip = None
    if not path or not os.path.exists(path):
        print(f"TrailPrint3D updater: no pending zip found at {path!r}")
        return None
    try:
        result = bpy.ops.extensions.package_install_files(
            filepath=path,
            repo="user_default",
            overwrite=True,
        )
        print(f"TrailPrint3D updater: package_install_files result: {result}")
        if 'FINISHED' not in result:
            status = "error"
            error_message = f"Install operator returned {result}"
    except Exception as e:
        status = "error"
        error_message = str(e)
        print(f"TrailPrint3D updater: install exception: {e}")
    finally:
        try:
            os.remove(path)
        except Exception as e:
            print(f"TrailPrint3D updater: could not remove temp zip: {e}")
    return None  # don't repeat


def download_and_install():
    """
    Download the TrailPrint3D.zip asset from the latest GitHub release, then
    schedule installation via a timer so it runs after the calling operator's
    execute() has returned (avoids an RNA crash from self-unregistering).

    Returns (success: bool, error_msg: str | None).
    """
    global _pending_install_zip
    import bpy

    zip_url = _latest_release_zip_url
    if not zip_url:
        return False, "No release zip URL available — run a version check first"

    try:
        resp = requests.get(zip_url, timeout=120, stream=True)
        resp.raise_for_status()

        install_zip = os.path.join(tempfile.gettempdir(), "TrailPrint3D_update.zip")
        with open(install_zip, "wb") as f:
            for chunk in resp.iter_content(chunk_size=32768):
                f.write(chunk)

        _pending_install_zip = install_zip
        bpy.app.timers.register(_install_timer, first_interval=0.5)
        return True, None

    except Exception as e:
        return False, str(e)
