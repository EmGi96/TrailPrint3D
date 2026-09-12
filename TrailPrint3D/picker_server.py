#  Copyright (C) 2026  EmGi
# Map picker window — launched by TrailPrint3D to select a geographic area.
# Starts a local HTTP server and opens Edge/Chrome in --app mode with a
# Leaflet.js map.  The user draws a rectangle; clicking "Confirm → Blender"
# POSTs the coordinates to /confirm and writes them to a temp JSON file that
# the Blender operator polls via a modal timer.
import json
import pathlib
import queue
import shutil
import socket
import subprocess as sp
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import cast

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


_HTML_PATH = pathlib.Path(__file__).parent / 'premium' / 'multitile_generator.html'

# Markup shared by every picker page (puzzleGenerator.html,
# premium/puzzleGenerator_pe.html, premium/multitile_generator.html) --
# the CSS "look" and the base-map/go-to-location JS boilerplate are
# byte-identical across all three, so they live here once and get inlined
# into each page's own <style>/<script> block at serve time, the same way
# __PORT__ already gets substituted below.
_ASSETS_DIR = pathlib.Path(__file__).parent / 'assets'
_COMMON_CSS_PATH = _ASSETS_DIR / 'picker_common.css'
_MAP_INIT_JS_PATH = _ASSETS_DIR / 'map_init.js'
_LOCATION_PANEL_JS_PATH = _ASSETS_DIR / 'location_panel.js'
_ELEMENT_STATUS_JS_PATH = _ASSETS_DIR / 'element_status.js'
_SETTINGS_MODAL_JS_PATH = _ASSETS_DIR / 'settings_modal.js'
_RECT_EDITOR_JS_PATH = _ASSETS_DIR / 'rect_editor.js'

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

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('127.0.0.1', _PREFERRED_PORT))
            return _PREFERRED_PORT
        except OSError:
            s.bind(('', 0))
            return s.getsockname()[1]


def _find_chromium() -> str | None:
    import os
    if sys.platform == 'win32':
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
    obj_size: float = 100.0
    html_path: pathlib.Path = _HTML_PATH
    state_path: pathlib.Path = _STATE_PATH

    def log_message(self, *args):
        pass

    def do_GET(self):
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
            .replace('__MAP_INIT_JS__', _MAP_INIT_JS_PATH.read_text(encoding='utf-8'))
            .replace('__LOCATION_PANEL_JS__', _LOCATION_PANEL_JS_PATH.read_text(encoding='utf-8'))
            .replace('__ELEMENT_ICONS_JS__', _element_icons_js())
            .replace('__ELEMENT_STATES_JS__', 'var ELEMENT_STATES = ' + self.element_states_json.decode('utf-8') + ';')
            .replace('__ELEMENT_STATUS_JS__', _ELEMENT_STATUS_JS_PATH.read_text(encoding='utf-8'))
            .replace('__SETTINGS_STATE_JS__', 'var SETTINGS_STATE = ' + self.settings_state_json.decode('utf-8') + ';')
            .replace('__ADVANCED_SETTINGS_STATE_JS__', 'var ADVANCED_SETTINGS_STATE = ' + self.advanced_settings_json.decode('utf-8') + ';')
            .replace('__SETTINGS_MODAL_JS__', _SETTINGS_MODAL_JS_PATH.read_text(encoding='utf-8'))
            .replace('__RECT_EDITOR_JS__', _RECT_EDITOR_JS_PATH.read_text(encoding='utf-8'))
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
            out_path.write_bytes(body)
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
        pathlib.Path(self.result_path).write_text(body.decode('utf-8'), encoding='utf-8')
        # Must run before the response is sent: the picker page calls
        # window.close() as soon as it sees the response, and this function
        # AttachThreadInput's onto that browser window's thread. Doing that
        # while the window is mid-teardown (racing the client's close())
        # is a known way to wedge Windows' shared input queue -- symptom is
        # Blender's own window silently stops taking clicks/keys, which only
        # becomes noticeable once generation finishes and the user tries to
        # interact again. Running it first, against a still-open window,
        # removes the race.
        _bring_blender_to_foreground()
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(b'ok')
        threading.Thread(target=self.server.shutdown, daemon=True).start()


def start_picker(result_path: str, existing_maps: list | None = None, existing_trails: list | None = None,
                  obj_size: float = 100.0, html_path: 'pathlib.Path | str | None' = None,
                  element_states: dict | None = None, settings_state: dict | None = None,
                  advanced_settings: dict | None = None) -> HTTPServer:
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
    pages never clobber each other's saved view/selection.
    """
    global _active_server, _pending_toggles, _pending_settings, _pending_advanced_settings
    if _active_server is not None:
        try:
            _active_server.shutdown()
        except OSError as e:
            print(f"[TP3D picker] Failed to shut down previous server: {e}")
        _active_server = None
    _pending_toggles = queue.Queue()
    _pending_settings = queue.Queue()
    _pending_advanced_settings = queue.Queue()

    html_path = pathlib.Path(html_path) if html_path else _HTML_PATH
    # Keep the original state filename for multitile_generator.html itself (exact
    # backward compatibility); other pages get their own, keyed by filename,
    # so two different picker pages never clobber each other's saved state.
    state_path = (
        _STATE_PATH if html_path == _HTML_PATH
        else pathlib.Path(tempfile.gettempdir()) / f'trailprint_picker_state_{html_path.stem}.json'
    )

    print(f"[TP3D picker] starting session: html_path={html_path} state_path={state_path} "
          f"state_exists={state_path.exists()}")

    port = _free_port()
    _Handler.result_path = result_path
    _Handler.existing_maps_json = json.dumps(existing_maps or []).encode('utf-8')
    _Handler.existing_trails_json = json.dumps(existing_trails or []).encode('utf-8')
    _Handler.element_states_json = json.dumps(element_states or {}).encode('utf-8')
    _Handler.settings_state_json = json.dumps(settings_state or {}).encode('utf-8')
    _Handler.advanced_settings_json = json.dumps(advanced_settings or {}).encode('utf-8')
    _Handler.obj_size = obj_size or 100.0
    _Handler.html_path = html_path
    _Handler.state_path = state_path

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
        proc = sp.Popen(
            [browser, f'--app={url}',
             f'--user-data-dir={profile_dir}',
             '--window-size=1870,1030', '--window-position=25,5',
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
