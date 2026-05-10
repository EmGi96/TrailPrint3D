#  Copyright (C) 2026  EmGi

import re
import threading
import os
import zipfile
import tempfile

import requests

from . import constants as const

_GITHUB_OWNER = "EmGi96"
_GITHUB_REPO = "TrailPrint3D"
_GITHUB_BRANCH = "main"

_RAW_INIT_URL = (
    f"https://raw.githubusercontent.com/{_GITHUB_OWNER}/{_GITHUB_REPO}"
    f"/{_GITHUB_BRANCH}/TrailPrint3D/__init__.py"
)
_ZIP_URL = (
    f"https://github.com/{_GITHUB_OWNER}/{_GITHUB_REPO}"
    f"/archive/refs/heads/{_GITHUB_BRANCH}.zip"
)
# Path to the addon folder inside the downloaded archive
_ADDON_FOLDER_IN_ZIP = f"{_GITHUB_REPO}-{_GITHUB_BRANCH}/TrailPrint3D"

# --- Module-level state (read from the UI draw call) ---
status = "idle"         # idle | checking | update_available | up_to_date | error
latest_version = None   # tuple e.g. (3, 1, 0), or None
error_message = ""


def _parse_version(text):
    m = re.search(r'"version"\s*:\s*\((\d+),\s*(\d+),\s*(\d+)\)', text)
    return tuple(int(x) for x in m.groups()) if m else None


def _check_worker():
    global status, latest_version, error_message
    try:
        resp = requests.get(_RAW_INIT_URL, timeout=10)
        resp.raise_for_status()
        ver = _parse_version(resp.text)
        if ver is None:
            status = "error"
            error_message = "Could not parse version from GitHub"
            return
        latest_version = ver
        status = "update_available" if ver > const.ADDON_VERSION else "up_to_date"
    except Exception as e:
        status = "error"
        error_message = str(e)


def start_check():
    """Start a background version check. Non-blocking."""
    global status
    status = "checking"
    threading.Thread(target=_check_worker, daemon=True).start()


def download_and_install():
    """
    Download the latest ZIP from GitHub, repack it so Blender's addon installer
    expects it, and install via bpy.ops.preferences.addon_install.

    Must be called from the main thread (uses bpy.ops).
    Returns (success: bool, error_msg: str | None).
    """
    import bpy

    try:
        resp = requests.get(_ZIP_URL, timeout=120, stream=True)
        resp.raise_for_status()

        with tempfile.TemporaryDirectory() as tmpdir:
            raw_zip = os.path.join(tmpdir, "raw.zip")
            with open(raw_zip, "wb") as f:
                for chunk in resp.iter_content(chunk_size=32768):
                    f.write(chunk)

            # Extract only the addon subfolder from the archive
            extract_dir = os.path.join(tmpdir, "extracted")
            os.makedirs(extract_dir)
            prefix = _ADDON_FOLDER_IN_ZIP.replace("\\", "/") + "/"
            with zipfile.ZipFile(raw_zip, "r") as zf:
                members = [m for m in zf.namelist()
                           if m.replace("\\", "/").startswith(prefix)]
                zf.extractall(extract_dir, members=members)

            # Locate the extracted addon directory
            addon_src = os.path.join(extract_dir, *_ADDON_FOLDER_IN_ZIP.split("/"))

            # Repack with TrailPrint3D/ at the zip root so Blender installs it correctly
            install_zip = os.path.join(tmpdir, "TrailPrint3D.zip")
            parent_dir = os.path.dirname(addon_src)
            with zipfile.ZipFile(install_zip, "w", zipfile.ZIP_DEFLATED) as out:
                for root, _, files in os.walk(addon_src):
                    for fname in files:
                        full = os.path.join(root, fname)
                        arc = os.path.relpath(full, parent_dir).replace(os.sep, "/")
                        out.write(full, arc)

            bpy.ops.preferences.addon_install(filepath=install_zip, overwrite=True)

        return True, None

    except Exception as e:
        return False, str(e)
