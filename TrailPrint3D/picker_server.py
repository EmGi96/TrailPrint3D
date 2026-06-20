#  Copyright (C) 2026  EmGi
# Map picker window — launched by TrailPrint3D to select a geographic area.
# Starts a local HTTP server and opens Edge/Chrome in --app mode with a
# Leaflet.js map.  The user draws a rectangle; clicking "Confirm → Blender"
# POSTs the coordinates to /confirm and writes them to a temp JSON file that
# the Blender operator polls via a modal timer.

import json
import pathlib
import socket
import subprocess as sp
import sys
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer


_HTML_PATH = pathlib.Path(__file__).parent / 'map_picker.html'


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
    except Exception as e:
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
            os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe'),
            os.path.expandvars(r'%PROGRAMFILES%\Google\Chrome\Application\chrome.exe'),
            os.path.expandvars(r'%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe'),
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
        if self.path != '/':
            self.send_response(404)
            self.end_headers()
            return
        body = (
            self.html_path.read_text(encoding='utf-8')
            .replace('__PORT__', str(self.server.server_address[1]))
            .replace('__OBJSIZE__', str(self.obj_size))
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
        if self.path == '/save_state':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                self.state_path.write_bytes(body)
                print(f"[TP3D picker] /save_state wrote {len(body)} bytes to {self.state_path}")
            except Exception as e:
                print(f"[TP3D picker] /save_state FAILED to write {self.state_path}: {e}")
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'ok')
            return
        if self.path == '/upload_gpx':
            import tempfile
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            raw_name = self.headers.get('X-Filename', 'trail.gpx')
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
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(b'ok')
        _bring_blender_to_foreground()
        threading.Thread(target=self.server.shutdown, daemon=True).start()


def start_picker(result_path: str, existing_maps: list | None = None, existing_trails: list | None = None,
                  obj_size: float = 100.0, html_path: 'pathlib.Path | str | None' = None) -> HTTPServer:
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

    *html_path*, if given, serves that HTML file instead of map_picker.html
    (e.g. puzzleGenerator.html) -- the rest of this server (GPX upload, state
    save/restore, existing-maps/trails reference data, /confirm) is schema-
    agnostic, so other picker pages can reuse it as-is. State is persisted to
    a path keyed off the served HTML file's name so two different picker
    pages never clobber each other's saved view/selection.
    """
    global _active_server
    if _active_server is not None:
        try:
            _active_server.shutdown()
        except Exception:
            pass
        _active_server = None

    html_path = pathlib.Path(html_path) if html_path else _HTML_PATH
    # Keep the original state filename for map_picker.html itself (exact
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
    _Handler.obj_size = obj_size or 100.0
    _Handler.html_path = html_path
    _Handler.state_path = state_path

    server = HTTPServer(('127.0.0.1', port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _active_server = server

    url = f'http://127.0.0.1:{port}/'
    browser = _find_chromium()
    if browser:
        sp.Popen(
            [browser, f'--app={url}',
             '--window-size=1000,660', '--window-position=100,80',
             '--no-first-run', '--no-default-browser-check',
             '--disable-extensions', '--disable-background-networking'],
            stdout=sp.DEVNULL, stderr=sp.DEVNULL,
        )
    else:
        if sys.platform == 'win32':
            sp.Popen(['cmd', '/c', 'start', '', url])
        elif sys.platform == 'darwin':
            sp.Popen(['open', url])
        else:
            sp.Popen(['xdg-open', url])

    return server
