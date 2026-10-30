#  Copyright (C) 2026  EmGi
# Map picker window — launched by TrailPrint3D to select a geographic area.
# Starts a local HTTP server and opens Edge/Chrome in --app mode with a
# Leaflet.js map.  The user draws a rectangle; clicking "Confirm → Blender"
# POSTs the coordinates to /confirm and writes them to a temp JSON file that
# the Blender operator polls via a modal timer.
import json
import os
import pathlib
import queue
import re
import shutil
import socket
import subprocess as sp
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import cast

from . import constants as const

# Element-status chip clicks (POST /toggle_element, see the picker pages'
# element_status.js) land on this HTTP server's own background thread --
# same process as Blender, but not the main thread, so bpy scene data can't
# be touched directly here. Queued instead and drained by the calling
# operator's modal() on Blender's own main-thread timer tick (see
# drain_pending_toggles / utils.apply_element_toggle), the same pattern
# already used for /confirm's result_path. Reset per start_picker() call so
# a previous session's leftover requests can't bleed into a new one.
_pending_toggles: 'queue.Queue[str]' = queue.Queue()

# Same idea as _pending_toggles, for the Settings popup's Map-tab field
# edits (POST /update_setting, see settings_modal.js) -- carries (key,
# value) pairs instead of bare keys, drained via drain_pending_settings /
# utils.apply_setting_update.
_pending_settings: 'queue.Queue[tuple]' = queue.Queue()

# Same idea again, for the Settings popup's Elements-tab field edits (POST
# /update_advanced_setting, see settings_modal.js) -- kept as its
# own queue/route rather than reusing _pending_settings so the two whitelists
# (utils._SETTINGS_ROW_FIELDS vs utils._ADVANCED_SETTINGS_FIELDS) stay fully
# independent on the applying side too.
_pending_advanced_settings: 'queue.Queue[tuple]' = queue.Queue()

# ── COLOR STYLES ─────────────────────────────────────────────────────
# Color-style operations from the Settings popup's Other tab (POST
# /update_color_setting, /apply_color_style, /save_color_style,
# /delete_color_style, see settings_modal.js). A dedicated queue because
# apply touches several tp3d properties at once, and save/delete need the
# helpers from ui.color_styles that aren't on the generic /update_setting
# whitelist. Drained on the main thread via drain_pending_color_styles and
# utils.apply_color_update.
_pending_color_styles: 'queue.Queue[dict]' = queue.Queue()
# ─────────────────────────────────────────────────────────────────────

# Settings popup's Other tab "3MF Print Profile" card (settings_modal.js):
# POST /select_slicer_profile {name} and POST /upload_slicer_profile (the raw
# .3mf as body, names in the X-Profile-Name / X-File-Name headers) are
# queued here and handled on the main thread by
# process_slicer_profile_requests, which also rebuilds the GET
# /slicer_profiles snapshot. Kept out of SETTINGS_STATE like the export
# folder -- restoring a history entry shouldn't switch the printer profile.
_pending_slicer_profiles: 'queue.Queue[dict]' = queue.Queue()

# The map generator pages' "Prefetch" button (POST /prefetch, see
# assets/prefetch_layer.js). Planning it needs scene settings (main thread
# only, hence the queue drained via drain_pending_prefetch from the calling
# operator's modal()), but the fetch itself runs on a worker thread that
# reports into _prefetch_job -- polled by the page via GET /prefetch_status.
# _prefetch_job is a plain dict whose values are only ever replaced key-wise
# (job.update), so readers on the HTTP thread see a consistent-enough
# snapshot without a lock.
_pending_prefetch: 'queue.Queue[dict]' = queue.Queue()
_prefetch_job: dict = {'status': 'idle', 'message': '', 'result': None}

# POST /install_3mf_addon queued here -- drained by each generator operator's
# modal() on the main thread (bpy.ops cannot run on the HTTP server's thread).
_pending_3mf_install: 'queue.Queue[bool]' = queue.Queue()

# 3D-preview pages (medalHolderGenerator.html): POST /request_model_preview
# queues the page's current parameters; the calling operator's modal() builds
# the model on the main thread (drain_latest_model_preview), writes it to
# _MODEL_PREVIEW_PATH as GLB and bumps _model_preview_job['version'], which
# the page polls via GET /model_preview_status before fetching
# GET /model_preview.glb. Same key-wise-update-only rule as _prefetch_job.
_pending_model_previews: 'queue.Queue[dict]' = queue.Queue()
_model_preview_job: dict = {'status': 'idle', 'version': 0, 'message': ''}
_MODEL_PREVIEW_PATH = pathlib.Path(tempfile.gettempdir()) / 'trailprint_model_preview.glb'

# Every generator page's export-folder button (assets/export_folder.js).
# 'scene' is the scene's own Export Path and 'default' the Default Export
# Folder preference (both read from Blender in start_picker); exports use
# 'scene' if set, else 'default' (addon_preferences.get_export_folder). A
# pick only ever replaces 'default'. 'status' is 'picking' while the OS
# folder dialog is open. POST /pick_export_folder runs the (blocking) dialog
# on its own thread so this single-threaded server keeps answering the page
# meanwhile, and queues the choice as an 'exportFolder' update on
# _pending_settings (utils.apply_setting_update sets it on the main thread).
# Same key-wise-update-only rule as _prefetch_job.
_export_folder_job: dict = {'status': 'idle', 'scene': '', 'default': ''}

# Settings popup's Local DEM File row (assets/settings_modal.js): POST
# /pick_dem_path {kind: 'file'|'folder'} opens the OS dialog the same way as
# /pick_export_folder and queues the choice as a 'demFilePath' update on
# _pending_settings; GET /dem_path_status reports it back. 'path' is seeded
# from the scene's demFilePath in start_picker and kept in sync with it by
# _sync_dem_path ('scene' is the raw, possibly '//'-relative, value last
# seen). Same key-wise-update-only rule as _prefetch_job.
_dem_path_job: dict = {'status': 'idle', 'path': '', 'scene': ''}


def _effective_export_folder() -> str:
    return _export_folder_job.get('scene') or _export_folder_job.get('default') or ''


def _pick_folder_dialog(initial: str, title: str = 'Select the TrailPrint3D export folder',
                        job: dict = _export_folder_job, kind: str = 'folder') -> str:
    """Show the OS's own folder picker (blocking) and return the chosen
    folder, or '' if cancelled/unavailable. Never touches bpy -- runs on a
    worker thread. The start folder goes in via an environment variable so
    no path ever needs quoting into a script. The dialog's process id is
    kept in job['pid'] while it's open (see _raise_dialog). *kind* 'dem_file'
    shows a GeoTIFF file picker instead and returns the chosen file."""
    pick_file = kind == 'dem_file'
    env = dict(os.environ, TP3D_INITIAL_DIR=initial if initial and os.path.isdir(initial) else '')
    flags = 0
    if sys.platform == 'win32':
        env['TP3D_FOLDER_PICKER_SOURCE'] = str(
            pathlib.Path(__file__).parent / 'assets' / 'windows_folder_picker.cs')
        # Windows won't let a background process bring a window to the front,
        # so the dialog used to open behind the browser now and then. Owning
        # it by a *shown* TopMost window fixes that: owned windows always sit
        # above their owner. The owner is fully transparent and off the
        # taskbar, centered on screen so the dialog opens there too.
        # Forms' OpenFileDialog is already the modern Common Item Dialog;
        # only its FolderBrowserDialog is the legacy tree (hence the .cs).
        if pick_file:
            show = (
                "$d = New-Object System.Windows.Forms.OpenFileDialog; "
                f"$d.Title = '{title}'; "
                "$d.Filter = 'GeoTIFF (*.tif;*.tiff)|*.tif;*.tiff'; "
                "if ($env:TP3D_INITIAL_DIR) { $d.InitialDirectory = $env:TP3D_INITIAL_DIR }; "
                "if ($d.ShowDialog($owner) -eq 'OK') { $path = $d.FileName } else { $path = '' }; "
            )
        else:
            show = f"$path = [TP3DFolderPicker]::Pick($owner.Handle, $env:TP3D_INITIAL_DIR, '{title}'); "
        ps = (
            "$ErrorActionPreference = 'Stop'; "
            "Add-Type -AssemblyName System.Windows.Forms; "
            "Add-Type -Path $env:TP3D_FOLDER_PICKER_SOURCE; "
            "$owner = New-Object System.Windows.Forms.Form; "
            "$owner.TopMost = $true; $owner.ShowInTaskbar = $false; $owner.Opacity = 0; "
            "$owner.FormBorderStyle = 'None'; $owner.StartPosition = 'CenterScreen'; "
            "$owner.Width = 1; $owner.Height = 1; "
            "try { "
            "$owner.Show(); $owner.Activate(); "
            + show +
            "if ($path) { Write-Output $path } "
            "} finally { $owner.Dispose() }"
        )
        cmd = ['powershell', '-NoProfile', '-NonInteractive', '-STA', '-Command',
               '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; ' + ps]
        flags = sp.CREATE_NO_WINDOW
    elif sys.platform == 'darwin':
        if pick_file:
            script = f'POSIX path of (choose file with prompt "{title}" of type {{"tif", "tiff", "public.tiff"}}'
        else:
            script = f'POSIX path of (choose folder with prompt "{title}"'
        if env['TP3D_INITIAL_DIR']:
            script += ' default location (POSIX file (system attribute "TP3D_INITIAL_DIR"))'
        cmd = ['osascript', '-e', script + ')']
    elif shutil.which('zenity'):
        cmd = ['zenity', '--file-selection', f'--title={title}']
        cmd.append('--file-filter=GeoTIFF | *.tif *.tiff' if pick_file else '--directory')
        if env['TP3D_INITIAL_DIR']:
            cmd.append('--filename=' + os.path.join(env['TP3D_INITIAL_DIR'], ''))
    elif shutil.which('kdialog'):
        start = env['TP3D_INITIAL_DIR'] or os.path.expanduser('~')
        cmd = (['kdialog', '--getopenfilename', start, 'GeoTIFF (*.tif *.tiff)'] if pick_file
               else ['kdialog', '--getexistingdirectory', start])
        cmd += ['--title', title]
    else:
        print("[TP3D picker] no folder dialog available (install zenity or kdialog)")
        return ''
    try:
        proc = sp.Popen(cmd, stdout=sp.PIPE, stderr=sp.PIPE, env=env, creationflags=flags)
    except OSError as e:
        print(f"[TP3D picker] folder dialog failed: {e}")
        return ''
    job.update(pid=proc.pid)
    try:
        out, err = proc.communicate()
    finally:
        job.update(pid=None)
    if proc.returncode:
        print(f"[TP3D picker] folder dialog exited with code {proc.returncode}: "
              f"{err.decode('utf-8', errors='replace').strip()}")
        return ''
    path = out.decode('utf-8', errors='replace').strip()
    exists = os.path.isfile if pick_file else os.path.isdir
    return path if path and exists(path) else ''


def _raise_dialog(job: dict = _export_folder_job) -> None:
    """Bring the already-open folder dialog back to the front (Windows) --
    e.g. after the user clicked into the browser and it went behind. Called
    when the page's button is clicked again while a dialog is still open,
    instead of ignoring that click."""
    pid = job.get('pid')
    if not pid or sys.platform != 'win32':
        return
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    hwnds = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _collect(hwnd, _lparam):
        owner_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if owner_pid.value == pid and user32.IsWindowVisible(hwnd):
            hwnds.append(hwnd)
        return True

    user32.EnumWindows(_collect, 0)
    HWND_TOPMOST, SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW = -1, 0x2, 0x1, 0x40
    for hwnd in hwnds:
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        user32.SetForegroundWindow(hwnd)


def _run_export_folder_pick() -> None:
    chosen = _pick_folder_dialog(_export_folder_job.get('default') or _effective_export_folder())
    if not chosen:
        _export_folder_job.update(status='cancelled')
        return
    # Trailing separator: export.py appends file names to it directly.
    chosen = os.path.join(chosen, '')
    _pending_settings.put(('exportFolder', chosen))
    _export_folder_job.update(status='done', default=chosen)


def _sync_dem_path(scene_value: str | None) -> None:
    """Follow a demFilePath change made outside /pick_dem_path (the sidebar,
    /update_setting) so /dem_path_status doesn't keep reporting the old path.
    Only acts when the scene value differs from the last one seen ('scene'),
    so a dialog choice not yet applied by the modal timer isn't overwritten.
    Main thread only (bpy.path.abspath) -- called from refresh_state_snapshots."""
    if scene_value is None or scene_value == _dem_path_job.get('scene'):
        return
    import bpy  # type: ignore

    _dem_path_job.update(scene=scene_value, path=bpy.path.abspath(scene_value) if scene_value else '')


def _dem_path_valid(path: str) -> bool:
    return bool(path) and (os.path.isdir(path) or os.path.isfile(path))


def _run_dem_path_pick(kind: str) -> None:
    current = _dem_path_job.get('path') or ''
    initial = current if os.path.isdir(current) else os.path.dirname(current)
    if kind == 'folder':
        chosen = _pick_folder_dialog(initial, 'Select a folder of GeoTIFF DEM tiles', _dem_path_job)
    else:
        chosen = _pick_folder_dialog(initial, 'Select a GeoTIFF DEM file', _dem_path_job, 'dem_file')
    if not chosen:
        _dem_path_job.update(status='cancelled')
        return
    _pending_settings.put(('demFilePath', chosen))
    _dem_path_job.update(status='done', path=chosen)


def _open_folder(path: str) -> bool:
    """Open *path* in the OS file manager. False if it isn't a folder."""
    if not path or not os.path.isdir(path):
        return False
    try:
        if sys.platform == 'win32':
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == 'darwin':
            sp.Popen(['open', path])
        else:
            sp.Popen(['xdg-open', path])
    except OSError as e:
        print(f"[TP3D picker] could not open folder {path}: {e}")
        return False
    return True


def is_active_server(server) -> bool:
    """False once a newer start_picker() replaced *server* (the user opened
    another generator page). The queues and page-load snapshots in this
    module are shared, so the replaced page's operator must end its modal
    then -- otherwise it keeps draining the new page's queued updates and
    overwriting its SETTINGS_STATE via refresh_state_snapshots."""
    return server is not None and server is _active_server


def drain_latest_model_preview() -> dict | None:
    """Pop every queued preview request and return only the newest -- older
    ones are already stale by the time the main thread gets to them."""
    latest = None
    while True:
        try:
            latest = _pending_model_previews.get_nowait()
        except queue.Empty:
            break
    return latest


def model_preview_job() -> dict:
    return _model_preview_job


def model_preview_path() -> pathlib.Path:
    return _MODEL_PREVIEW_PATH


def drain_pending_prefetch() -> list:
    """Pop every prefetch request payload queued since the last call."""
    requests = []
    while True:
        try:
            requests.append(_pending_prefetch.get_nowait())
        except queue.Empty:
            break
    return requests


def prefetch_job() -> dict:
    """The live job dict (see _prefetch_job) -- for the worker to update."""
    return _prefetch_job


def prefetch_is_running() -> bool:
    return _prefetch_job.get('status') in ('queued', 'running')


def drain_pending_toggles() -> list:
    """Pop every element-toggle key queued since the last call, in order."""
    keys = []
    while True:
        try:
            keys.append(_pending_toggles.get_nowait())
        except queue.Empty:
            break
    return keys


def drain_pending_settings() -> list:
    """Pop every (key, value) settings-row update queued since the last call."""
    updates = []
    while True:
        try:
            updates.append(_pending_settings.get_nowait())
        except queue.Empty:
            break
    return updates


def drain_pending_advanced_settings() -> list:
    """Pop every (key, value) Advanced Settings popup update queued since the last call."""
    updates = []
    while True:
        try:
            updates.append(_pending_advanced_settings.get_nowait())
        except queue.Empty:
            break
    return updates


# ── COLOR STYLES ─────────────────────────────────────────────────────
def drain_pending_color_styles() -> list:
    """Pop every queued color-style operation since the last call, in order.

    Each entry is a dict like:
        {'action': 'set',    'key': 'element_color_base', 'value': '#RRGGBB'}
        {'action': 'apply',  'style': 'FOREST' or 'USER:My Palette'}
        {'action': 'save',   'name': 'My Palette'}
        {'action': 'delete', 'style': 'USER:My Palette'}
    Applied on the main thread by utils.apply_color_update, then passed to
    finish_color_style_updates. 'save'/'delete' entries also carry a 'done'
    threading.Event their POST handler is waiting on.
    """
    updates = []
    while True:
        try:
            updates.append(_pending_color_styles.get_nowait())
        except queue.Empty:
            break
    return updates


def finish_color_style_updates(updates: list, tp3d) -> None:
    """Rebuild the COLOR_STYLES snapshot after the calling operator's modal()
    tick applied *updates* (drain_pending_color_styles' return value), then
    release the POST handlers waiting on them -- so the page's follow-up
    /get_color_styles already lists a just-saved (or no longer lists a
    just-deleted) user style. Main thread only, like refresh_state_snapshots.

    *tp3d* is ``context.scene.tp3d``.
    """
    if not updates:
        return
    refresh_state_snapshots(color_styles=build_color_styles_state(tp3d))
    for update in updates:
        done = update.get('done')
        if done is not None:
            done.set()


def build_color_styles_state(tp3d) -> dict:
    """COLOR_STYLES snapshot for the picker page (inlined as
    __COLOR_STYLES_JS__). Main thread only -- reads the scene's material
    colors.

    *tp3d* is ``bpy.context.scene.tp3d``.
    """
    try:
        from .utils import color_styles
        current = color_styles.scene_colors(tp3d)
        built_in = []
        for ident, (label, description, colors) in color_styles.BUILT_IN_STYLES.items():
            built_in.append({
                'id': ident,
                'label': label,
                'description': description,
                'colors': color_styles.role_hex_map(color_styles.normalize_colors(colors)),
            })
        user = []
        for name in color_styles.list_user_styles():
            try:
                colors = color_styles.load_user_style(name)
            except (OSError, ValueError):
                continue
            user.append({'name': name, 'colors': color_styles.role_hex_map(colors)})
        return {
            'builtIn': built_in,
            'user': user,
            'currentStyle': getattr(tp3d, 'color_style', color_styles.DEFAULT_STYLE),
            'currentColors': color_styles.role_hex_map(current),
            'isModified': color_styles.style_is_modified(tp3d),
        }
    except (AttributeError, ImportError, KeyError) as e:
        print(f"[TP3D picker] could not build color styles state: {e}")
        return {'builtIn': [], 'user': [], 'currentStyle': '',
                'currentColors': {}, 'isModified': False}
# ─────────────────────────────────────────────────────────────────────


def build_slicer_profiles_state(tp3d) -> dict:
    """GET /slicer_profiles snapshot: the same items as the sidebar's 3MF
    Export Profile dropdown (props.slicer_profile_name) plus the selected
    one. Main thread only -- the 3MF add-on is looked up through bpy."""
    from .props import _slicer_profile_items_uncached
    from .threemf_discovery import get_threemf_api

    try:
        items = _slicer_profile_items_uncached()
    except (ImportError, AttributeError, OSError) as e:
        print(f"[TP3D picker] could not list slicer profiles: {e}")
        items = []
    return {
        'available': get_threemf_api() is not None,
        'profiles': [{'id': i, 'label': label, 'description': desc} for i, label, desc in items],
        'current': getattr(tp3d, 'slicer_profile_name', 'NONE'),
    }


def _import_slicer_profile(path: str, name: str, source_file: str):
    """Save the slicer settings inside the .3mf at *path* as print profile
    *name* -- what the 3MF add-on's own "Load Profile from 3MF" button
    (Preferences → Add-ons → 3MF Format → Advanced) does. Returns (ok, message)."""
    import importlib
    import zipfile

    from .threemf_discovery import get_threemf_api

    api = get_threemf_api()
    if api is None:
        return False, "The 3MF add-on is not installed."
    sp = importlib.import_module(".slicer_profiles", package=api.__package__)
    try:
        vendor, machine, configs, _labels = sp.extract_from_3mf(path)
    except zipfile.BadZipFile:
        return False, "Not a valid 3MF file."
    except (OSError, ValueError, KeyError) as e:
        return False, f"Could not read the file: {e}"
    if not configs:
        return False, ("This 3MF has no slicer settings. Save it as a project "
                       "from your slicer (Bambu Studio, OrcaSlicer, PrusaSlicer).")
    sp.save_profile(name, vendor or "Unknown", source_file, configs, machine=machine)
    return True, f'Saved print profile "{name}".'


def process_slicer_profile_requests(tp3d) -> None:
    """Handle the queued /select_slicer_profile and /upload_slicer_profile
    requests, rebuild the /slicer_profiles snapshot and release the waiting
    POST handlers with each request's result. Call from every generator
    operator's modal() tick (main thread). An uploaded profile becomes the
    selected one; a select with an empty name only refreshes the snapshot.

    *tp3d* is ``context.scene.tp3d``.
    """
    requests = []
    while True:
        try:
            requests.append(_pending_slicer_profiles.get_nowait())
        except queue.Empty:
            break
    if not requests:
        return

    from .props import _slicer_profile_items_uncached

    for req in requests:
        if req['action'] == 'select':
            if not req['name']:
                # Just a refresh -- e.g. the 3MF add-on was installed or a
                # profile added in its preferences since the page opened.
                req['result'] = {'ok': True, 'message': ''}
            elif req['name'] in {item[0] for item in _slicer_profile_items_uncached()}:
                tp3d.slicer_profile_name = req['name']
                req['result'] = {'ok': True, 'message': ''}
            else:
                req['result'] = {'ok': False, 'message': 'Unknown print profile.'}
        elif req['action'] == 'upload':
            ok, message = _import_slicer_profile(req['path'], req['name'], req['source_file'])
            if ok:
                tp3d.slicer_profile_name = req['name']
            req['result'] = {'ok': ok, 'message': message}
            try:
                os.remove(req['path'])
            except OSError:
                pass

    _Handler.slicer_profiles_json = json.dumps(build_slicer_profiles_state(tp3d)).encode('utf-8')
    if any(req['name'] for req in requests):  # a page opening just refreshes
        from .utils.ui_state import save_generator_settings

        save_generator_settings(tp3d)
    # The sidebar's own dropdown doesn't redraw on its own from a modal timer.
    import bpy  # type: ignore

    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()
    for req in requests:
        req['done'].set()


def drain_pending_3mf_install() -> bool:
    """True if the generator page asked for a 3MF addon install since the last
    call (duplicate requests are collapsed -- only one install fires)."""
    requested = False
    while True:
        try:
            _pending_3mf_install.get_nowait()
            requested = True
        except queue.Empty:
            break
    return requested


def refresh_state_snapshots(element_states: dict | None = None, settings_state: dict | None = None,
                             advanced_settings: dict | None = None, element_source: str | None = None,
                             color_styles: dict | None = None) -> None:
    """Re-point the running server's cached page-load snapshots (ELEMENT_STATES/
    SETTINGS_STATE/ADVANCED_SETTINGS_STATE/ELEMENT_SOURCE/COLOR_STYLES, see
    start_picker's own docstring) at fresh dicts, so a later browser reload of
    '/' serves current scene state instead of whatever was true when
    start_picker() was first called.

    Needed for every picker page's OSM/ESA WorldCover switch
    (settings_modal.js): flipping tp3d.elementSource via /update_setting only
    changes the scene property (applied on the calling operator's modal()
    timer tick, via apply_setting_update) -- it does NOT by itself update
    what a fresh GET '/' would serve, since those were snapshotted once at
    start_picker() time. The switch's own JS reloads the page a moment after
    posting the change; call this from the same modal() tick that drains the
    pending update (right after applying it) so that reload actually sees
    the new source's chips/cards instead of the stale ones.

    Same main-thread-only reasoning as apply_element_toggle et al. -- call
    only from the calling operator's modal() timer tick, never from the HTTP
    server's own background thread. Any argument left None keeps that
    snapshot as it was.

    *color_styles*, if given, is picker_server.build_color_styles_state(tp3d)'s
    return value -- see that function's docstring.
    """
    if element_states is not None:
        _Handler.element_states_json = json.dumps(element_states).encode('utf-8')
    if settings_state is not None:
        _Handler.settings_state_json = json.dumps(settings_state).encode('utf-8')
        _sync_dem_path(settings_state.get('demFilePath'))
    if advanced_settings is not None:
        _Handler.advanced_settings_json = json.dumps(advanced_settings).encode('utf-8')
    if element_source is not None:
        _Handler.element_source = element_source
    if color_styles is not None:
        _Handler.color_styles_json = json.dumps(color_styles).encode('utf-8')


def merge_settings_snapshot(changes: dict) -> None:
    """Update only *changes*' keys in the SETTINGS_STATE snapshot -- for pages
    whose snapshot isn't build_settings_row_state()'s (the medal holder and
    backplate generators), where refresh_state_snapshots would drop the page's
    own keys. Same main-thread-only rule as refresh_state_snapshots."""
    state = json.loads(_Handler.settings_state_json.decode('utf-8'))
    state.update(changes)
    _Handler.settings_state_json = json.dumps(state).encode('utf-8')


_HTML_PATH = pathlib.Path(__file__).parent / 'premium' / 'generators' / 'multitile_generator.html'

# Markup shared by every picker page (puzzleGenerator.html,
# premium/puzzleGenerator_pe.html, premium/multitile_generator.html) --
# the CSS "look" and the base-map/go-to-location JS boilerplate are
# byte-identical across all three, so they live here once and get inlined
# into each page's own <style>/<script> block at serve time, the same way
# __PORT__ already gets substituted below.
_ASSETS_DIR = pathlib.Path(__file__).parent / 'assets'
_COMMON_CSS_PATH = _ASSETS_DIR / 'picker_common.css'
_PICKER_UI_JS_PATH = _ASSETS_DIR / 'picker_ui.js'
_MAP_INIT_JS_PATH = _ASSETS_DIR / 'map_init.js'
_LOCATION_PANEL_JS_PATH = _ASSETS_DIR / 'location_panel.js'
_ELEMENT_STATUS_JS_PATH = _ASSETS_DIR / 'element_status.js'
_SETTINGS_MODAL_JS_PATH = _ASSETS_DIR / 'settings_modal.js'
_RECT_EDITOR_JS_PATH = _ASSETS_DIR / 'rect_editor.js'
_PREFETCH_JS_PATH = _ASSETS_DIR / 'prefetch_layer.js'
_SHAPE_EXTRAS_JS_PATH = _ASSETS_DIR / 'shape_extras.js'
# Generated icon outlines (generate_text_icons.py) the Backplate top view in
# shape_extras.js draws -- served as part of __SHAPE_EXTRAS_JS__.
_TEXT_ICONS_JS_PATH = _ASSETS_DIR / 'text_icons.js'
_HISTORY_PANEL_JS_PATH = _ASSETS_DIR / 'history_panel.js'
_TUTORIAL_JS_PATH = _ASSETS_DIR / 'tutorial.js'
_MAP_TUTORIALS_JS_PATH = _ASSETS_DIR / 'map_tutorials.js'
_MULTITILE_TUTORIALS_JS_PATH = _ASSETS_DIR / 'multitile_tutorials.js'
_EXPORT_FOLDER_JS_PATH = _ASSETS_DIR / 'export_folder.js'
_THREEMF_PROMO_JS_PATH = _ASSETS_DIR / 'threemf_promo.js'
_TRANSLATE_JS_PATH = _ASSETS_DIR / 'translate.js'

_element_icons_js_cache: str | None = None


def _element_icons_js() -> str:
    """`var ELEMENT_ICONS = {...};` -- the 10 progress-bar element icons
    (see progress_win._ICON_MAP), recolored to `currentColor` so the
    element-status strip's CSS controls the actual green/gray shade per
    enabled state. Built once and cached: the SVG files themselves don't
    change while Blender is running.
    """
    global _element_icons_js_cache
    if _element_icons_js_cache is None:
        from . import progress_win
        icons = progress_win._load_icons(color='currentColor')
        _element_icons_js_cache = 'var ELEMENT_ICONS = ' + json.dumps(icons) + ';'
    return _element_icons_js_cache


_PREFERRED_PORT = 27373
_active_server: HTTPServer | None = None
_STATE_PATH = pathlib.Path(tempfile.gettempdir()) / 'trailprint_picker_state.json'

# Per-generator generation history (assets/history_panel.js's right-hand
# drawer) -- one JSON file per picker page, keyed by html_path.stem the same
# way _STATE_PATH is keyed for the multi-page-aware state_path below (see
# _history_key). Kept in the addon's persistent CONFIG dir (unlike the
# session-only state files above, which live in the OS temp dir) since the
# whole point of a history is to survive across Blender restarts.
_HISTORY_DIR = pathlib.Path(const.generation_history_dir)
_HISTORY_MAX_ENTRIES = 50


def _history_key(html_path: pathlib.Path) -> str:
    """History-file key for a picker page. Free/premium page pairs (e.g.
    map_generator.html / premium/map_generator_pe.html, puzzleGenerator.html /
    premium/puzzleGenerator_pe.html) share one history file -- a map or
    puzzle generated in one should show up in the other's history drawer --
    so the trailing '_pe' that otherwise distinguishes the premium filename
    is stripped before it's used as the history/state key. Premium-only
    pages (multitile_generator.html, slidingPuzzleGenerator.html) don't have
    a '_pe' suffix to begin with and keep their own history as before.
    """
    stem = html_path.stem
    return stem.removesuffix('_pe')

# Real top-down Blender renders (export.save_history_thumbnail), written well
# after this server has usually already shut down -- see /get_history_render
# and _read_history's own render-lookup below. Entry ids are uuid4().hex (32
# lowercase hex chars); this regex doubles as the path-traversal guard for
# both the render lookup and the on-disk filename.
_HISTORY_THUMBNAILS_DIR = pathlib.Path(const.generation_history_thumbnails_dir)
_HISTORY_ID_RE = re.compile(r'^[0-9a-f]{32}$')


def _history_render_path(entry_id: str) -> 'pathlib.Path | None':
    if not isinstance(entry_id, str) or not _HISTORY_ID_RE.match(entry_id):
        return None
    return _HISTORY_THUMBNAILS_DIR / f'{entry_id}.png'


def _read_history(path: pathlib.Path) -> list:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _with_render_urls(entries: list) -> list:
    """Copy of *entries* with a 'render' URL added wherever a real top-down
    Blender render (export.save_history_thumbnail) now exists on disk for
    that entry -- checked fresh on every call (GET /get_history only) rather
    than cached in the JSON file itself, so a picker page that's still open
    when generation finishes picks it up on its very next poll instead of
    only after a reopen. Deliberately not folded into _read_history, whose
    result also feeds straight back into _write_history elsewhere -- this
    derived field must never actually get persisted.
    """
    out = []
    for entry in entries:
        if not isinstance(entry, dict):
            out.append(entry)
            continue
        render_path = _history_render_path(entry.get('id'))
        if render_path is not None and render_path.exists():
            entry = dict(entry, render=f'/get_history_render?id={entry["id"]}')
        out.append(entry)
    return out


def _write_history(path: pathlib.Path, entries: list) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entries), encoding='utf-8')
    except OSError as e:
        print(f"[TP3D picker] Failed to write history {path}: {e}")


_TUTORIAL_PROGRESS_PATH = pathlib.Path(const.tutorial_progress_path)
_THREEMF_PROMO_DECLINED_PATH = pathlib.Path(const.threemf_promo_declined_path)

# Sample files bundled with the addon (e.g. sample GPX tracks for tutorials),
# served read-only by GET /get_sample_file?name=<file name>.
_SAMPLE_FILES_DIR = pathlib.Path(__file__).parent / 'sample_files'
_SAMPLE_FILE_TYPES = {'.gpx': 'application/gpx+xml', '.geojson': 'application/geo+json',
                      '.json': 'application/json', '.svg': 'image/svg+xml'}


def _sample_file_path(name: str) -> pathlib.Path | None:
    """The bundled sample file called *name*, or None if there isn't one.
    Only plain file names directly inside sample_files/ resolve -- anything
    with a directory part is rejected, so this can't read outside it."""
    if not name or pathlib.Path(name).name != name:
        return None
    path = _SAMPLE_FILES_DIR / name
    return path if path.is_file() else None


def _read_tutorial_progress() -> dict:
    """{tutorial id: True} for every tutorial the user has completed (see
    assets/tutorial.js). Shared by every picker page, so a tutorial shared
    by a free/premium page pair counts as done in both."""
    try:
        data = json.loads(_TUTORIAL_PROGRESS_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _mark_tutorial_complete(tutorial_id: str) -> dict:
    progress = _read_tutorial_progress()
    progress[tutorial_id] = True
    try:
        _TUTORIAL_PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _TUTORIAL_PROGRESS_PATH.write_text(json.dumps(progress), encoding='utf-8')
    except OSError as e:
        print(f"[TP3D picker] Failed to write tutorial progress {_TUTORIAL_PROGRESS_PATH}: {e}")
    return progress


def _read_threemf_promo_declined() -> bool:
    """True once the user has clicked 'No thanks' on any generator page."""
    try:
        data = json.loads(_THREEMF_PROMO_DECLINED_PATH.read_text(encoding='utf-8'))
        return bool(data.get('declined'))
    except (OSError, json.JSONDecodeError):
        return False


def _mark_threemf_promo_declined() -> None:
    try:
        _THREEMF_PROMO_DECLINED_PATH.parent.mkdir(parents=True, exist_ok=True)
        _THREEMF_PROMO_DECLINED_PATH.write_text(json.dumps({'declined': True}), encoding='utf-8')
    except OSError as e:
        print(f"[TP3D picker] Failed to write threemf promo state: {e}")


# Saved "Defaults" per page (e.g. the Backplate Generator's): a list of
# {'name', 'settings'} kept in one JSON file per page, keyed like the history
# (_history_key). The page owns the list -- it sends the whole edited list
# back -- this only validates and caps it.
_DEFAULTS_DIR = pathlib.Path(const.generator_defaults_dir)
_DEFAULTS_MAX_ENTRIES = 200
_DEFAULTS_NAME_MAX = 60


_FONT_TYPES = {'.ttf': 'font/ttf', '.otf': 'font/otf', '.ttc': 'font/collection', '.woff': 'font/woff'}


def _servable_font_path(requested: str, settings_state_json: bytes) -> pathlib.Path | None:
    """The font file GET /font_file may send for *requested*, or None.

    The path comes from the browser, so only fonts the page could have
    picked anyway are served: an installed one from /list_fonts, Blender's
    default text font (for '' -- text_objects.default_font_path), or the
    scene's own textFont from the page's SETTINGS_STATE snapshot. Never an
    arbitrary file."""
    from .utils import font_list
    from .utils.text_objects import default_font_path

    if not requested:
        requested = default_font_path()
    if not requested:
        return None
    allowed = {f['path'] for f in font_list.list_fonts()}
    allowed.add(default_font_path())
    try:
        scene_font = json.loads(settings_state_json.decode('utf-8')).get('textFont')
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        scene_font = None
    if scene_font:
        allowed.add(scene_font)
    if requested not in allowed:
        return None
    path = pathlib.Path(requested)
    if path.suffix.lower() not in _FONT_TYPES or not path.is_file():
        return None
    return path


def _read_page_defaults(path: pathlib.Path) -> list:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return []
    return _clean_page_defaults(data)


def _clean_page_defaults(entries) -> list:
    """Only well-formed entries, unique by name (case-insensitive, the
    later one wins), sorted by name, capped."""
    by_name = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get('settings'), dict):
            continue
        name = ' '.join(str(entry.get('name') or '').split())[:_DEFAULTS_NAME_MAX]
        if name:
            by_name[name.casefold()] = {'name': name, 'settings': entry['settings']}
    cleaned = sorted(by_name.values(), key=lambda e: e['name'].casefold())
    return cleaned[:_DEFAULTS_MAX_ENTRIES]


def _write_page_defaults(path: pathlib.Path, entries) -> list:
    cleaned = _clean_page_defaults(entries)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cleaned, indent=1), encoding='utf-8')
    except OSError as e:
        print(f"[TP3D picker] Failed to write defaults {path}: {e}")
        return _read_page_defaults(path)
    return cleaned


def _delete_history_render(entry_id: str) -> None:
    render_path = _history_render_path(entry_id)
    if render_path is not None:
        try:
            render_path.unlink(missing_ok=True)
        except OSError as e:
            print(f"[TP3D picker] Failed to delete history render {render_path}: {e}")


def _bring_blender_to_foreground() -> None:
    """Raise Blender's own window above the browser the picker runs in.

    Windows-only. Runs on the HTTP server's background thread (this is pure
    OS window-manager API, not bpy, so that's safe), right as the "Send to
    Blender" click hits /confirm -- no need to wait for the modal timer that
    actually picks up and processes the result file.

    Plain SetForegroundWindow (even with AttachThreadInput) isn't reliable
    enough on its own -- Windows' foreground-lock can still refuse it. This
    combines three well-known workarounds: a synthetic Alt key tap (makes
    Windows treat the call as input-driven), AttachThreadInput around the
    call, and an independent SetWindowPos topmost/non-topmost toggle that
    forces the z-order regardless of focus rules. Also specifically targets
    Blender's actual editor window (class "GHOST_WindowClass") rather than
    just the first window owned by this process, in case Blender has more
    than one top-level window open.
    """
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        target_pid = kernel32.GetCurrentProcessId()
        candidates = []  # (hwnd, is_ghost_window_class, area)

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum_proc(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value != target_pid:
                return True
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
            candidates.append((hwnd, cls.value == 'GHOST_WindowClass', area))
            return True

        user32.EnumWindows(_enum_proc, 0)
        if not candidates:
            print("[TP3D picker_server] No window found for this process -- can't raise Blender to foreground.")
            return

        ghost = [c for c in candidates if c[1]]
        pool = ghost if ghost else candidates
        hwnd = max(pool, key=lambda c: c[2])[0]

        SW_RESTORE = 9
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)

        try:
            user32.AllowSetForegroundWindow(target_pid)
        except AttributeError:
            pass

        VK_MENU = 0x12
        KEYEVENTF_KEYUP = 0x0002
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)

        fg_hwnd = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None)
        cur_thread = kernel32.GetCurrentThreadId()
        attached = False
        if fg_thread and fg_thread != cur_thread:
            attached = bool(user32.AttachThreadInput(fg_thread, cur_thread, True))
        try:
            HWND_TOPMOST = wintypes.HWND(-1)
            HWND_NOTOPMOST = wintypes.HWND(-2)
            SWP_NOSIZE, SWP_NOMOVE = 0x0001, 0x0002
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE)
            user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(fg_thread, cur_thread, False)

        # The above is still a best-effort race against Windows' foreground
        # lock and intermittently loses it. Minimize-then-restore is the one
        # technique that reliably forces foreground regardless of the lock
        # (Windows specifically grants it to a window un-minimizing itself),
        # so use it as a fallback -- but only when the gentler attempt above
        # actually failed, to avoid a visible flicker on the common case
        # where it already worked.
        if user32.GetForegroundWindow() != hwnd:
            print("[TP3D picker_server] Gentle foreground attempt didn't take -- falling back to minimize/restore.")
            SW_MINIMIZE = 6
            user32.ShowWindow(hwnd, SW_MINIMIZE)
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
            if user32.GetForegroundWindow() != hwnd:
                print("[TP3D picker_server] Minimize/restore fallback also didn't take foreground.")
    except (OSError, ImportError, AttributeError) as e:
        print(f"[TP3D picker_server] _bring_blender_to_foreground failed: {e}")

def _apply_picker_window_icon() -> None:
    """Give the picker's browser window a crisp taskbar icon (Windows only).

    In --app mode Chromium builds the window/taskbar icon from the page's
    ~16-32px favicon, which Windows then upscales -- visibly pixelated. This
    instead sends WM_SETICON with the multi-size assets/icon.ico. Chromium
    can re-set its own icon when the page finishes loading, so the icon is
    re-applied for a while after launch rather than once. Runs on its own
    daemon thread (pure OS window API, no bpy).
    """
    if sys.platform != 'win32':
        return
    icon_path = _ASSETS_DIR / 'icon.ico'
    if not icon_path.exists():
        return
    try:
        import ctypes
        import time
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

        IMAGE_ICON, LR_LOADFROMFILE = 1, 0x0010
        WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
        SM_CXSMICON = 49

        def _load(size):
            return user32.LoadImageW(None, str(icon_path), IMAGE_ICON, size, size, LR_LOADFROMFILE)

        small = _load(user32.GetSystemMetrics(SM_CXSMICON) or 16)
        big = _load(256)
        if not big:
            return

        def _find_windows():
            found = []

            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def _enum_proc(hwnd, _lparam):
                if not user32.IsWindowVisible(hwnd):
                    return True
                cls = ctypes.create_unicode_buffer(64)
                user32.GetClassNameW(hwnd, cls, 64)
                if cls.value != 'Chrome_WidgetWin_1':
                    return True
                title = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, title, 256)
                if title.value.startswith('TrailPrint3D'):
                    found.append(hwnd)
                return True

            user32.EnumWindows(_enum_proc, 0)
            return found

        for _ in range(20):
            for hwnd in _find_windows():
                if small:
                    user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small)
                user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big)
            time.sleep(1.0)
    except (OSError, ImportError, AttributeError) as e:
        print(f"[TP3D picker_server] _apply_picker_window_icon failed: {e}")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('127.0.0.1', _PREFERRED_PORT))
            return _PREFERRED_PORT
        except OSError:
            s.bind(('', 0))
            return s.getsockname()[1]


_CHROMIUM_EXE_NAMES = {
    'msedge.exe', 'chrome.exe', 'brave.exe', 'vivaldi.exe', 'opera.exe', 'chromium.exe',
}


def _default_chromium_browser() -> str | None:
    """The user's Windows default browser, if it is Chromium-based (so it
    supports --app), else None. Reads the https UserChoice ProgId and that
    ProgId's open command."""
    import os
    import shlex
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice',
        ) as key:
            prog_id, _ = winreg.QueryValueEx(key, 'ProgId')
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf'{prog_id}\shell\open\command') as key:
            command, _ = winreg.QueryValueEx(key, '')
    except OSError:
        return None
    try:
        exe = shlex.split(command, posix=False)[0].strip('"')
    except (ValueError, IndexError):
        return None
    exe = os.path.expandvars(exe)
    if os.path.basename(exe).lower() in _CHROMIUM_EXE_NAMES and os.path.isfile(exe):
        return exe
    return None


def _find_chromium() -> str | None:
    import os
    if sys.platform == 'win32':
        default = _default_chromium_browser()
        if default:
            return default
        candidates = [
            os.path.expandvars(r'%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe'),
            os.path.expandvars(r'%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe'),
            os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe'),
            os.path.expandvars(r'%PROGRAMFILES%\Google\Chrome\Application\chrome.exe'),
            os.path.expandvars(r'%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe'),
            os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
        ]
    elif sys.platform == 'darwin':
        candidates = [
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
        ]
    else:
        candidates = [
            '/usr/bin/google-chrome',
            '/usr/bin/chromium-browser',
            '/usr/bin/chromium',
        ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


class _Handler(BaseHTTPRequestHandler):
    result_path: str = ''
    existing_maps_json: bytes = b'[]'
    existing_trails_json: bytes = b'[]'
    element_states_json: bytes = b'{}'
    settings_state_json: bytes = b'{}'
    advanced_settings_json: bytes = b'{}'
    color_styles_json: bytes = b'{}'  # ── COLOR STYLES
    slicer_profiles_json: bytes = b'{"available": false, "profiles": [], "current": "NONE"}'
    element_source: str = 'OSM'
    dem_bounds_json: bytes = b'null'
    obj_size: float = 100.0
    html_path: pathlib.Path = _HTML_PATH
    state_path: pathlib.Path = _STATE_PATH
    history_path: pathlib.Path = _HISTORY_DIR / f'{_history_key(_HTML_PATH)}.json'
    defaults_path: pathlib.Path = _DEFAULTS_DIR / f'{_history_key(_HTML_PATH)}.json'

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == '/get_source_state':
            # Every picker page's OSM/ESA WorldCover switch
            # (settings_modal.js) polls this after posting the switch to
            # /update_setting, instead of reloading the whole page (which
            # closed the Settings modal it was clicked from -- unintuitive,
            # since the point was to keep tweaking settings). Combines the
            # 3 snapshots refresh_state_snapshots keeps current into one
            # response so the page can patch its own ELEMENT_SOURCE/
            # ELEMENT_STATES/SETTINGS_STATE/ADVANCED_SETTINGS_STATE in place
            # and just re-render the chip strip + Elements tab.
            body = json.dumps({
                'elementSource': self.element_source,
                'elementStates': json.loads(self.element_states_json.decode('utf-8')),
                'settingsState': json.loads(self.settings_state_json.decode('utf-8')),
                'advancedSettings': json.loads(self.advanced_settings_json.decode('utf-8')),
            }).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == '/get_color_styles':
            # ── COLOR STYLES ────────────────────────────────────────
            # Serves the main-thread-built snapshot -- see
            # build_color_styles_state / refresh_state_snapshots. Never
            # reads bpy directly here; this is the HTTP server's own thread.
            body = self.color_styles_json
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == '/slicer_profiles':
            # Main-thread-built snapshot, see process_slicer_profile_requests.
            body = self.slicer_profiles_json
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == '/list_fonts':
            # Text Settings popup's font dropdown (assets/shape_extras.js) --
            # installed system fonts. The page can't browse the Windows Fonts
            # folder itself: it's a shell virtual folder that shows up empty
            # in a browser file dialog.
            from .utils import font_list
            body = json.dumps(font_list.list_fonts()).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith('/font_file'):
            # The font file itself, so a page can draw text in the font
            # Blender will use (shape_extras.js's Backplate top view). An
            # empty path means Blender's default text font.
            from urllib.parse import parse_qs, urlparse
            path = _servable_font_path(parse_qs(urlparse(self.path).query).get('path', [''])[0],
                                       self.settings_state_json)
            if path is None:
                self.send_response(404)
                self.end_headers()
                return
            try:
                body = path.read_bytes()
            except OSError:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-Type', _FONT_TYPES.get(path.suffix.lower(), 'application/octet-stream'))
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'max-age=3600')
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == '/prefetch_status':
            body = json.dumps(_prefetch_job).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == '/export_folder_status':
            path = _effective_export_folder()
            body = json.dumps({
                'status': _export_folder_job.get('status'),
                'path': path,
                'valid': bool(path) and os.path.isdir(path),
                # The scene's own Export Path is in effect, not the default.
                'sceneOverride': bool(_export_folder_job.get('scene')),
            }).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == '/dem_path_status':
            path = _dem_path_job.get('path') or ''
            body = json.dumps({
                'status': _dem_path_job.get('status'),
                'path': path,
                'valid': _dem_path_valid(path),
            }).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == '/model_preview_status':
            body = json.dumps(_model_preview_job).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == '/model_preview.glb' or self.path.startswith('/model_preview.glb?'):
            try:
                body = _MODEL_PREVIEW_PATH.read_bytes()
            except OSError:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-Type', 'model/gltf-binary')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == '/get_existing_maps':
            body = self.existing_maps_json
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == '/get_existing_trails':
            body = self.existing_trails_json
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == '/get_state':
            try:
                body = self.state_path.read_bytes()
                print(f"[TP3D picker] /get_state read {len(body)} bytes from {self.state_path}")
            except FileNotFoundError:
                body = b'{}'
                print(f"[TP3D picker] /get_state: {self.state_path} not found, returning empty state")
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == '/get_history':
            body = json.dumps(_with_render_urls(_read_history(self.history_path))).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith('/get_sample_file?'):
            # Lets a page load a bundled sample (e.g. the GPX tutorial's
            # "Use sample GPX file" button, assets/map_tutorials.js) --
            # browser pages can't read the addon folder directly.
            from urllib.parse import parse_qs, urlparse
            query = parse_qs(urlparse(self.path).query)
            sample_path = _sample_file_path(query.get('name', [''])[0])
            if sample_path is None:
                self.send_response(404)
                self.end_headers()
                return
            try:
                body = sample_path.read_bytes()
            except OSError:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-Type', _SAMPLE_FILE_TYPES.get(sample_path.suffix.lower(), 'application/octet-stream'))
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == '/get_page_defaults':
            body = json.dumps(_read_page_defaults(self.defaults_path)).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == '/get_tutorial_progress':
            body = json.dumps(_read_tutorial_progress()).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == '/get_threemf_promo_state':
            body = json.dumps({'declined': _read_threemf_promo_declined()}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith('/get_history_render?'):
            from urllib.parse import parse_qs, urlparse
            query = parse_qs(urlparse(self.path).query)
            render_path = _history_render_path(query.get('id', [''])[0])
            if render_path is None:
                self.send_response(400)
                self.end_headers()
                return
            try:
                body = render_path.read_bytes()
            except OSError:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith('/get_gpx_content?'):
            from urllib.parse import parse_qs, urlparse
            query = parse_qs(urlparse(self.path).query)
            raw_path = query.get('path', [''])[0]
            # Only ever re-serves a file /upload_gpx itself just wrote (same
            # temp dir, same 'trailprint_' name prefix) -- not an arbitrary
            # local-file read. Used so a picker page can redraw a
            # previously-imported trail on reopen (saveState/restoreState
            # only persist the path/name, not the raw GPX content itself).
            candidate = pathlib.Path(raw_path)
            expected_dir = pathlib.Path(tempfile.gettempdir())
            if candidate.parent != expected_dir or not candidate.name.startswith('trailprint_'):
                self.send_response(403)
                self.end_headers()
                return
            try:
                body = candidate.read_bytes()
            except OSError:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/gpx+xml')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path != '/':
            self.send_response(404)
            self.end_headers()
            return
        body = (
            self.html_path.read_text(encoding='utf-8')
            .replace('__PORT__', str(cast(tuple[str, int], self.server.server_address)[1]))
            .replace('__OBJSIZE__', str(self.obj_size))
            .replace('__COMMON_CSS__', _COMMON_CSS_PATH.read_text(encoding='utf-8'))
            .replace('__PICKER_UI_JS__', _PICKER_UI_JS_PATH.read_text(encoding='utf-8'))
            .replace('__DEM_BOUNDS_JS__', 'var DEM_BOUNDS = ' + self.dem_bounds_json.decode('utf-8') + ';')
            .replace('__MAP_INIT_JS__', _MAP_INIT_JS_PATH.read_text(encoding='utf-8'))
            .replace('__LOCATION_PANEL_JS__', _LOCATION_PANEL_JS_PATH.read_text(encoding='utf-8'))
            .replace('__ELEMENT_ICONS_JS__', _element_icons_js())
            .replace('__ELEMENT_STATES_JS__', 'var ELEMENT_STATES = ' + self.element_states_json.decode('utf-8') + ';')
            .replace('__ELEMENT_SOURCE_JS__', 'var ELEMENT_SOURCE = ' + json.dumps(self.element_source) + ';')
            .replace('__ELEMENT_STATUS_JS__', _ELEMENT_STATUS_JS_PATH.read_text(encoding='utf-8'))
            .replace('__SETTINGS_STATE_JS__', 'var SETTINGS_STATE = ' + self.settings_state_json.decode('utf-8') + ';')
            .replace('__ADVANCED_SETTINGS_STATE_JS__', 'var ADVANCED_SETTINGS_STATE = ' + self.advanced_settings_json.decode('utf-8') + ';')
            .replace('__COLOR_STYLES_JS__', 'var TP3D_COLOR_STYLES = ' + self.color_styles_json.decode('utf-8') + ';')  # ── COLOR STYLES
            .replace('__SETTINGS_MODAL_JS__', _SETTINGS_MODAL_JS_PATH.read_text(encoding='utf-8'))
            .replace('__HISTORY_PANEL_JS__', _HISTORY_PANEL_JS_PATH.read_text(encoding='utf-8'))
            .replace('__RECT_EDITOR_JS__', _RECT_EDITOR_JS_PATH.read_text(encoding='utf-8'))
            .replace('__PREFETCH_JS__', _PREFETCH_JS_PATH.read_text(encoding='utf-8'))
            .replace('__SHAPE_EXTRAS_JS__', _TEXT_ICONS_JS_PATH.read_text(encoding='utf-8') + '\n'
                     + _SHAPE_EXTRAS_JS_PATH.read_text(encoding='utf-8'))
            .replace('__TUTORIAL_JS__', _TUTORIAL_JS_PATH.read_text(encoding='utf-8'))
            .replace('__MAP_TUTORIALS_JS__', _MAP_TUTORIALS_JS_PATH.read_text(encoding='utf-8'))
            .replace('__MULTITILE_TUTORIALS_JS__', _MULTITILE_TUTORIALS_JS_PATH.read_text(encoding='utf-8'))
            .replace('__EXPORT_FOLDER_JS__', _EXPORT_FOLDER_JS_PATH.read_text(encoding='utf-8'))
            .replace('__THREEMF_PROMO_JS__', _THREEMF_PROMO_JS_PATH.read_text(encoding='utf-8'))
            .replace('__TRANSLATE_JS__', _TRANSLATE_JS_PATH.read_text(encoding='utf-8'))
            .encode('utf-8')
        )
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        if self.path in ('/pick_export_folder', '/open_export_folder'):
            length = int(self.headers.get('Content-Length', 0))
            self.rfile.read(length)
            if self.path == '/open_export_folder':
                ok = _open_folder(_effective_export_folder())
            elif _export_folder_job.get('status') == 'picking':
                # A dialog is already open (probably behind another window)
                # -- show it again rather than ignoring the click.
                _raise_dialog()
                ok = True
            else:
                ok = True
                _export_folder_job.update(status='picking')
                threading.Thread(target=_run_export_folder_pick, daemon=True).start()
            body = json.dumps({'ok': ok}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == '/pick_dem_path':
            length = int(self.headers.get('Content-Length', 0))
            try:
                kind = json.loads(self.rfile.read(length) or b'{}').get('kind')
            except (json.JSONDecodeError, AttributeError):
                kind = None
            if _dem_path_job.get('status') == 'picking':
                _raise_dialog(_dem_path_job)
            else:
                _dem_path_job.update(status='picking')
                threading.Thread(target=_run_dem_path_pick,
                                 args=('folder' if kind == 'folder' else 'file',), daemon=True).start()
            body = json.dumps({'ok': True}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == '/toggle_element':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                key = json.loads(body).get('key')
            except (json.JSONDecodeError, AttributeError):
                key = None
            if key:
                _pending_toggles.put(key)
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'ok')
            return
        if self.path == '/prefetch':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict) and not prefetch_is_running():
                _prefetch_job.update(status='queued', message='Queued…', result=None)
                _pending_prefetch.put(payload)
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'ok')
            return
        if self.path == '/request_model_preview':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                _model_preview_job.update(status='queued', message='')
                _pending_model_previews.put(payload)
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'ok')
            return
        if self.path == '/update_setting':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                key = data.get('key')
                value = data.get('value')
            except (json.JSONDecodeError, AttributeError):
                key = None
                value = None
            if key is not None:
                _pending_settings.put((key, value))
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'ok')
            return
        if self.path == '/update_advanced_setting':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                key = data.get('key')
                value = data.get('value')
            except (json.JSONDecodeError, AttributeError):
                key = None
                value = None
            if key is not None:
                _pending_advanced_settings.put((key, value))
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'ok')
            return
        # ── COLOR STYLES ─────────────────────────────────────────────
        if self.path in ('/update_color_setting', '/apply_color_style',
                         '/save_color_style', '/delete_color_style'):
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                data = {}
            # Save/delete change the style list the page re-fetches right
            # after -- hold the response until the main thread has applied
            # the change and rebuilt the snapshot (finish_color_style_updates).
            done = threading.Event()
            if self.path == '/update_color_setting':
                key = data.get('key')
                value = data.get('value')
                if key is not None:
                    _pending_color_styles.put({'action': 'set', 'key': key, 'value': value})
            elif self.path == '/apply_color_style':
                style_id = data.get('style')
                if style_id:
                    _pending_color_styles.put({'action': 'apply', 'style': style_id})
            elif self.path == '/save_color_style':
                name = data.get('name')
                if name:
                    _pending_color_styles.put({'action': 'save', 'name': name, 'done': done})
                    done.wait(timeout=3)
            elif self.path == '/delete_color_style':
                style_id = data.get('style')
                if style_id:
                    _pending_color_styles.put({'action': 'delete', 'style': style_id, 'done': done})
                    done.wait(timeout=3)
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'ok')
            return
        # ─────────────────────────────────────────────────────────────
        if self.path in ('/select_slicer_profile', '/upload_slicer_profile'):
            # Answered once the main thread has handled the request
            # (process_slicer_profile_requests): {ok, message, state}.
            from urllib.parse import unquote

            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            request = {'done': threading.Event()}
            if self.path == '/select_slicer_profile':
                try:
                    data = json.loads(body) if body else {}
                except json.JSONDecodeError:
                    data = {}
                request.update(action='select', name=str(data.get('name') or ''))
                timeout = 5
            else:
                source_file = os.path.basename(unquote(self.headers.get('X-File-Name', '')))
                name = unquote(self.headers.get('X-Profile-Name', '')).strip()
                name = name or os.path.splitext(source_file)[0] or 'Print Profile'
                # mkstemp imported by name: do_POST imports tempfile locally
                # further down, which makes `tempfile` local to all of it.
                from tempfile import mkstemp

                fd, path = mkstemp(suffix='.3mf', prefix='tp3d_profile_')
                with os.fdopen(fd, 'wb') as f:
                    f.write(body)
                request.update(action='upload', path=path, name=name[:64],
                               source_file=source_file or 'profile.3mf')
                timeout = 30
            _pending_slicer_profiles.put(request)
            if request['done'].wait(timeout=timeout):
                reply = dict(request['result'])
            else:
                reply = {'ok': False, 'message': 'Blender did not respond, please try again.'}
            reply['state'] = json.loads(self.slicer_profiles_json)
            out = json.dumps(reply).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return
        if self.path == '/save_state':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                self.state_path.write_bytes(body)
                print(f"[TP3D picker] /save_state wrote {len(body)} bytes to {self.state_path}")
            except OSError as e:
                print(f"[TP3D picker] /save_state FAILED to write {self.state_path}: {e}")
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'ok')
            return
        if self.path == '/save_page_defaults':
            # The page's whole edited Defaults list ({'defaults': [...]}) --
            # see _write_page_defaults. Answers with what was actually saved.
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                entries = json.loads(body).get('defaults')
            except (json.JSONDecodeError, AttributeError):
                entries = None
            saved = (_write_page_defaults(self.defaults_path, entries) if isinstance(entries, list)
                     else _read_page_defaults(self.defaults_path))
            resp = json.dumps(saved).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
            return
        if self.path == '/complete_tutorial':
            # assets/tutorial.js's menu -- a tutorial was run to its last
            # step. Sent with fetch keepalive, since a tutorial's last step
            # can be the very "Send to Blender" click that closes the window.
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                tutorial_id = json.loads(body).get('id')
            except (json.JSONDecodeError, AttributeError):
                tutorial_id = None
            if isinstance(tutorial_id, str) and tutorial_id:
                progress = _mark_tutorial_complete(tutorial_id)
            else:
                progress = _read_tutorial_progress()
            resp = json.dumps(progress).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
            return
        if self.path == '/set_threemf_promo_declined':
            length = int(self.headers.get('Content-Length', 0))
            self.rfile.read(length)
            _mark_threemf_promo_declined()
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'ok')
            return
        if self.path == '/install_3mf_addon':
            length = int(self.headers.get('Content-Length', 0))
            self.rfile.read(length)
            _pending_3mf_install.put(True)
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'ok')
            return
        if self.path == '/save_history_entry':
            # assets/history_panel.js's tp3dPushHistory -- called by each
            # picker page's own "Send to Blender" handler right before
            # /confirm, with the same settings blob shape its saveState()
            # already builds, plus a fresh /get_source_state snapshot (the
            # Settings popup's Map/Elements tab fields, and which element
            # source/categories were on) so a history entry can restore
            # everything that went into that generation, not just the
            # page's own shape/resolution/coords fields. Newest entry first,
            # capped to _HISTORY_MAX_ENTRIES so the file can't grow unbounded.
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                settings = data.get('settings')
                summary = data.get('summary') or ''
                thumbnail = data.get('thumbnail')
                element_source = data.get('elementSource')
                enabled_elements = data.get('enabledElements')
                element_states = data.get('elementStates')
                settings_state = data.get('settingsState')
                advanced_settings = data.get('advancedSettings')
                color_style = data.get('colorStyle')
            except (json.JSONDecodeError, AttributeError):
                settings = None
                summary = ''
                thumbnail = None
                element_source = None
                enabled_elements = None
                element_states = None
                settings_state = None
                advanced_settings = None
                color_style = None
            if isinstance(settings, dict):
                entry = {
                    'id': uuid.uuid4().hex,
                    'timestamp': time.time(),
                    'summary': str(summary)[:200],
                    'settings': settings,
                }
                # tp3dRenderShapeThumbnail (assets/history_panel.js) draws a
                # small vector "shape outline" sketch client-side and hands
                # it over as a data: URL -- sanity-checked and size-capped
                # here rather than trusted outright, since it's coming from
                # the browser same as any other POST body.
                if (isinstance(thumbnail, str) and thumbnail.startswith('data:image/')
                        and len(thumbnail) < 300_000):
                    entry['thumbnail'] = thumbnail
                # Which element source/categories were live at Send time
                # (read straight off ELEMENT_SOURCE/TP3D_ELEMENT_STATE by
                # tp3dPushHistory) -- shown as a source badge + icon row under
                # the thumbnail (history_panel.js's renderEntries).
                if element_source in ('OSM', 'WORLDCOVER'):
                    entry['elementSource'] = element_source
                if isinstance(enabled_elements, list):
                    entry['enabledElements'] = [k for k in enabled_elements if isinstance(k, str)][:20]
                # Full Settings-popup snapshot (Map tab + Elements tab +
                # every element category's on/off, not just the enabled
                # ones above) -- used to actually restore those settings on
                # click (tp3dApplyHistorySettings, assets/history_panel.js),
                # not just to render the badge row. Stored as opaque dicts;
                # picker_server.py never interprets their keys itself --
                # they're only ever replayed back through the same
                # /update_setting, /update_advanced_setting and
                # /toggle_element routes that produced them, which already
                # whitelist/validate every key server-side
                # (utils.ui_state.apply_setting_update /
                # apply_advanced_setting_update / apply_element_toggle).
                if isinstance(element_states, dict):
                    entry['elementStates'] = element_states
                if isinstance(settings_state, dict):
                    entry['settingsState'] = settings_state
                if isinstance(advanced_settings, dict):
                    entry['advancedSettings'] = advanced_settings
                # Color palette at Send time: {style, colors: {ROLE: '#RRGGBB'}}
                # -- replayed through /apply_color_style and
                # /update_color_setting (tp3dApplyHistoryColors), which
                # validate it again.
                if (isinstance(color_style, dict) and isinstance(color_style.get('style'), str)
                        and isinstance(color_style.get('colors'), dict)):
                    entry['colorStyle'] = {
                        'style': color_style['style'][:100],
                        'colors': {
                            str(role)[:20]: hex_value
                            for role, hex_value in color_style['colors'].items()
                            if isinstance(hex_value, str) and re.fullmatch(r'#[0-9A-Fa-f]{6}', hex_value)
                        },
                    }
                entries = _read_history(self.history_path)
                entries.insert(0, entry)
                # Entries pushed off the end by the cap may already have a
                # real render on disk (a generation from well before this one
                # can easily have finished by now) -- delete it too, or it'd
                # sit there orphaned forever.
                for dropped in entries[_HISTORY_MAX_ENTRIES:]:
                    if isinstance(dropped, dict):
                        _delete_history_render(dropped.get('id'))
                del entries[_HISTORY_MAX_ENTRIES:]
                _write_history(self.history_path, entries)
                entry_id = entry['id']
            else:
                entry_id = None
            resp = json.dumps({'id': entry_id}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
            return
        if self.path == '/delete_history_entry':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                entry_id = json.loads(body).get('id')
            except (json.JSONDecodeError, AttributeError):
                entry_id = None
            entries = _read_history(self.history_path)
            if entry_id is not None:
                entries = [e for e in entries if e.get('id') != entry_id]
                _write_history(self.history_path, entries)
                _delete_history_render(entry_id)
            resp = json.dumps(_with_render_urls(entries)).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
            return
        if self.path in ('/upload_gpx', '/upload_geojson', '/upload_svg'):
            import tempfile
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            default_names = {
                '/upload_gpx': 'trail.gpx',
                '/upload_geojson': 'boundary.geojson',
                '/upload_svg': 'shape.svg',
            }
            raw_name = self.headers.get('X-Filename', default_names[self.path])
            safe = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in raw_name)
            out_path = pathlib.Path(tempfile.gettempdir()) / f'trailprint_{safe}'
            try:
                out_path.write_bytes(body)
            except OSError as e:
                # Without this, an OSError here (locked file, permissions,
                # disk full, ...) propagates out of do_POST uncaught --
                # socketserver logs it to the console but the connection is
                # dropped mid-response, so the browser's fetch() rejects with
                # a generic network error instead of a clean HTTP failure.
                # The client already treats any rejected upload as "not
                # imported" (see its .catch()), so a clean error response is
                # enough; the print gives the actual reason for next time.
                print(f"[TP3D picker] {self.path} FAILED to write {out_path}: {e}")
                self.send_response(500)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                return
            resp = json.dumps({'path': str(out_path)}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(resp)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(resp)
            return
        if self.path != '/confirm':
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        # Must run before the response is sent: the picker page calls
        # window.close() as soon as it sees the response, and this function
        # AttachThreadInput's onto that browser window's thread. Doing that
        # while the window is mid-teardown (racing the client's close())
        # is a known way to wedge Windows' shared input queue -- symptom is
        # Blender's own window silently stops taking clicks/keys, which only
        # becomes noticeable once generation finishes and the user tries to
        # interact again. Running it first, against a still-open window,
        # removes the race.
        # Must also run before the result file is written: its window calls
        # (SetWindowPos, ShowWindow, ...) block until Blender's main thread
        # handles them, and the operator's modal timer starts the (blocking)
        # generation as soon as the file exists. Written first, a timer tick
        # landing in between leaves this thread stuck -- still attached to the
        # browser's input -- for the whole generation: the page never gets its
        # response (stays open), and Blender freezes once generation finishes.
        _bring_blender_to_foreground()
        pathlib.Path(self.result_path).write_text(body.decode('utf-8'), encoding='utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(b'ok')
        threading.Thread(target=self.server.shutdown, daemon=True).start()


def start_picker(result_path: str, existing_maps: list | None = None, existing_trails: list | None = None,
                  obj_size: float = 100.0, html_path: 'pathlib.Path | str | None' = None,
                  element_states: dict | None = None, settings_state: dict | None = None,
                  advanced_settings: dict | None = None, dem_bounds: dict | None = None,
                  element_source: str | None = None) -> HTTPServer:
    """Start the HTTP server, open the page in the browser, and return the server.

    The server writes confirmed coordinate JSON to *result_path* on POST /confirm,
    then shuts itself down.  The caller is responsible for removing the result file
    and stopping the server on cancel.

    *existing_maps*, if given, is a list of {"shape", "bounds", "name"} dicts for
    maps already present in the Blender scene; it's served on GET /get_existing_maps
    so the page can draw them on the 2D map for reference.

    *obj_size* is the scene's current tile size (mm) — used client-side to
    estimate the Horizontal Scale a fresh (non-extending) batch would get, so
    the Draw-mode grid preview can show an accurate tile-spacing gap before
    the real scale is computed server-side at generation time.

    *existing_trails*, if given, is a list of {"name", "points"} dicts (points
    being [lat, lon] pairs) for trail curves already present in the Blender
    scene; served on GET /get_existing_trails so the page can draw them for
    reference instead of re-importing/re-sending them as new GPX trails.

    *element_states*, if given, is a dict like utils.build_element_toggle_states()'s
    return value -- which of the 10 progress-bar element categories (water,
    forest, roads, ...) are currently toggled on in the scene. Inlined into
    the page as ELEMENT_STATES so it can render the enabled/disabled icon
    strip above the map. A snapshot taken when the picker opens; it does not
    live-update if the caller changes a toggle while the picker stays open.
    Each chip is also clickable -- see _pending_toggles/drain_pending_toggles
    above for how that reaches Blender's actual scene properties.

    *settings_state*, if given, is a dict like utils.build_settings_row_state()'s
    return value -- current values for the Settings popup's Map tab
    (Elevation Scale, Path Thickness, ...), reached via the gear icon next
    to the element-status strip. Inlined into the page as SETTINGS_STATE.
    Editing a field there POSTs to /update_setting, queued in
    _pending_settings and drained via drain_pending_settings/
    utils.apply_setting_update the same way chip clicks are.

    *advanced_settings*, if given, is a dict like utils.build_advanced_settings_state()'s
    return value -- current values for the Settings popup's Elements tab,
    covering the individual sub-checkboxes and per-category thresholds the
    element-status row only summarizes. Inlined into the page as
    ADVANCED_SETTINGS_STATE. Editing a field there POSTs to
    /update_advanced_setting, queued in _pending_advanced_settings and
    drained via drain_pending_advanced_settings/utils.apply_advanced_setting_update.

    *html_path*, if given, serves that HTML file instead of multitile_generator.html
    (e.g. puzzleGenerator.html) -- the rest of this server (GPX upload, state
    save/restore, existing-maps/trails reference data, /confirm) is schema-
    agnostic, so other picker pages can reuse it as-is. State is persisted to
    a path keyed off the served HTML file's name so two different picker
    pages never clobber each other's saved view/selection. The generation
    history (assets/history_panel.js) is keyed the same way (via
    _history_key), but into const.generation_history_dir instead -- see
    history_path below. Free/premium page pairs share one history key (see
    _history_key), so generations from either show up in both.

    *dem_bounds*, if given, is a {"footprint", "name"} dict for a single DEM file, or
    {"tiles": [{"footprint", "name"}, ...], "name"} for a folder of tiles (see
    operators._dem_coverage_overlay()) describing the lat/lon footprint(s) of the
    currently selected Local DEM File/folder. Inlined into the page as DEM_BOUNDS and
    drawn as dashed reference polygon(s) by assets/map_init.js, so the user can see
    which area the file has data for while drawing their own selection. None (the
    default) draws nothing -- either a different elevation API is active, or nothing
    could be read.

    *element_source*, if given, is the scene's tp3d.elementSource ("OSM" or
    "WORLDCOVER") -- inlined into the page as ELEMENT_SOURCE. Every picker
    page carries the __ELEMENT_SOURCE_JS__ token in its script block (see
    element_status.js/settings_modal.js), so this drives their
    element-status strip and Settings modal Elements tab, switching between
    the OSM and WorldCover chip/card sets.

    The color-styles state (COLOR_STYLES) is built here too, via
    build_color_styles_state -- see that function's docstring.
    """
    global _active_server, _pending_toggles, _pending_settings, _pending_advanced_settings, _pending_prefetch
    global _pending_model_previews, _pending_3mf_install, _pending_color_styles, _pending_slicer_profiles
    if _active_server is not None:
        try:
            _active_server.shutdown()
        except OSError as e:
            print(f"[TP3D picker] Failed to shut down previous server: {e}")
        _active_server = None
    _pending_toggles = queue.Queue()
    _pending_settings = queue.Queue()
    _pending_advanced_settings = queue.Queue()
    _pending_color_styles = queue.Queue()  # ── COLOR STYLES
    _pending_slicer_profiles = queue.Queue()
    _pending_prefetch = queue.Queue()
    _pending_model_previews = queue.Queue()
    _pending_3mf_install = queue.Queue()
    _model_preview_job.update(status='idle', message='', seq=None)
    # An in-flight worker from a previous session keeps a reference to the
    # OLD job dict only if it was handed the dict itself -- it's handed the
    # live one, so replace contents instead of rebinding (see prefetch_job()).
    _prefetch_job.update(status='idle', message='', result=None)
    # start_picker runs on Blender's main thread (an operator's invoke), so
    # the export folders can be read here directly.
    try:
        import bpy  # type: ignore

        from .addon_preferences import get_prefs

        scene_folder = bpy.path.abspath(bpy.context.scene.tp3d.export_path) if bpy.context.scene.tp3d.export_path else ''
        default_folder = get_prefs().default_export_folder
        default_folder = bpy.path.abspath(default_folder) if default_folder else ''
        dem_scene = bpy.context.scene.tp3d.demFilePath
        dem_path = bpy.path.abspath(dem_scene) if dem_scene else ''
    except (AttributeError, KeyError, ImportError) as e:
        print(f"[TP3D picker] could not read the export folder: {e}")
        scene_folder = default_folder = dem_scene = dem_path = ''
    _export_folder_job.update(status='idle', scene=scene_folder, default=default_folder, pid=None)
    _dem_path_job.update(status='idle', scene=dem_scene, path=dem_path, pid=None)

    html_path = pathlib.Path(html_path) if html_path else _HTML_PATH
    # Keep the original state filename for multitile_generator.html itself (exact
    # backward compatibility); other pages get their own, keyed by filename,
    # so two different picker pages never clobber each other's saved state.
    state_path = (
        _STATE_PATH if html_path == _HTML_PATH
        else pathlib.Path(tempfile.gettempdir()) / f'trailprint_picker_state_{html_path.stem}.json'
    )
    history_path = _HISTORY_DIR / f'{_history_key(html_path)}.json'

    print(f"[TP3D picker] starting session: html_path={html_path} state_path={state_path} "
          f"state_exists={state_path.exists()}")

    port = _free_port()
    _Handler.result_path = result_path
    _Handler.existing_maps_json = json.dumps(existing_maps or []).encode('utf-8')
    _Handler.existing_trails_json = json.dumps(existing_trails or []).encode('utf-8')
    _Handler.element_states_json = json.dumps(element_states or {}).encode('utf-8')
    _Handler.settings_state_json = json.dumps(settings_state or {}).encode('utf-8')
    _Handler.advanced_settings_json = json.dumps(advanced_settings or {}).encode('utf-8')
    # ── COLOR STYLES ─────────────────────────────────────────────────
    # Built here on the main thread -- build_color_styles_state reads the
    # scene's material colors, which is main-thread only.
    try:
        import bpy  # type: ignore
        _Handler.color_styles_json = json.dumps(
            build_color_styles_state(bpy.context.scene.tp3d)).encode('utf-8')
    except (AttributeError, ImportError) as e:
        print(f"[TP3D picker] could not build color styles state: {e}")
        _Handler.color_styles_json = json.dumps({
            'builtIn': [], 'user': [], 'currentStyle': '',
            'currentColors': {}, 'isModified': False,
        }).encode('utf-8')
    # ─────────────────────────────────────────────────────────────────
    try:
        import bpy  # type: ignore
        _Handler.slicer_profiles_json = json.dumps(
            build_slicer_profiles_state(bpy.context.scene.tp3d)).encode('utf-8')
    except (AttributeError, ImportError) as e:
        print(f"[TP3D picker] could not build slicer profiles state: {e}")
    _Handler.element_source = element_source or 'OSM'
    _Handler.dem_bounds_json = json.dumps(dem_bounds).encode('utf-8')
    _Handler.obj_size = obj_size or 100.0
    _Handler.html_path = html_path
    _Handler.state_path = state_path
    _Handler.history_path = history_path
    _Handler.defaults_path = _DEFAULTS_DIR / f'{_history_key(html_path)}.json'

    server = HTTPServer(('127.0.0.1', port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _active_server = server

    url = f'http://127.0.0.1:{port}/'
    browser = _find_chromium()
    if browser:
        # A fresh, unique --user-data-dir forces a genuinely new browser
        # process. Without it, if Edge/Chrome already has a process running
        # (very common -- Edge in particular tends to stay resident), this
        # Popen just forwards the URL to that existing process via IPC, and
        # window-size/window-position (along with most other switches) are
        # silently dropped since they only apply to a process's initial launch.
        # Sweep leftover profile dirs from prior launches whose cleanup thread
        # never got to run (e.g. Blender was closed before the browser was).
        for stale in pathlib.Path(tempfile.gettempdir()).glob('trailprint_picker_profile_*'):
            shutil.rmtree(stale, ignore_errors=True)

        profile_dir = pathlib.Path(tempfile.gettempdir()) / f'trailprint_picker_profile_{port}'
        # --disable-features=Translate doesn't reliably suppress Edge's own
        # "translate this page?" prompt, so seed the fresh profile's own
        # Preferences file: disabling the translate feature outright, and
        # separately marking English as an accepted language so the
        # language-mismatch heuristic that triggers the prompt never fires.
        default_dir = profile_dir / 'Default'
        default_dir.mkdir(parents=True, exist_ok=True)
        (default_dir / 'Preferences').write_text(
            json.dumps({
                'translate': {'enabled': False},
                'intl': {'accept_languages': 'en-US,en'},
            }),
            encoding='utf-8',
        )
        # Brave shows its product-analytics (P3A) notice on every fresh
        # profile; Brave settings live in the root 'Local State' file, so
        # seed P3A as off and the notice as already acknowledged.
        if pathlib.Path(browser).name.lower() == 'brave.exe':
            (profile_dir / 'Local State').write_text(
                json.dumps({
                    'brave': {
                        'p3a': {'enabled': False, 'notice_acknowledged': True},
                        'stats': {'reporting_enabled': False},
                    },
                }),
                encoding='utf-8',
            )
        proc = sp.Popen(
            [browser, f'--app={url}',
             f'--user-data-dir={profile_dir}',
             '--start-maximized',
             '--no-first-run', '--no-default-browser-check',
             '--disable-extensions', '--disable-background-networking',
             '--disable-features=Translate,TranslateUI',
             '--disable-sync'],
            stdout=sp.DEVNULL, stderr=sp.DEVNULL,
        )

        def _cleanup_profile():
            import time
            proc.wait()
            # The process Popen'd above is often just Chromium's launcher --
            # it re-execs/forks the real browser process and exits almost
            # immediately, well before the actual window (and its lock on
            # profile_dir) is gone. Retry for a while so the directory still
            # gets removed promptly in the common case; if the window stays
            # open longer than that, the stale-dir sweep on the next launch
            # will catch it instead.
            for _ in range(30):
                shutil.rmtree(profile_dir, ignore_errors=True)
                if not profile_dir.exists():
                    return
                time.sleep(2)

        threading.Thread(target=_cleanup_profile, daemon=True).start()
        threading.Thread(target=_apply_picker_window_icon, daemon=True).start()
    else:
        import bpy  # type: ignore
        bpy.ops.wm.url_open(url=url)
        # if sys.platform == 'win32':
        #     sp.Popen(['cmd', '/c', 'start', '', url])
        # elif sys.platform == 'darwin':
        #     sp.Popen(['open', url])
        # else:
        #     sp.Popen(['xdg-open', url])

    return server