#  Copyright (C) 2026  EmGi
"""
Local HTTP configuration UI for TrailPrint3D headless mode.

Starts a web server in a daemon thread and opens the browser.  The main
Blender thread calls wait_for_generate() which blocks until the user clicks
"Generate" in the browser, then returns the config dict.  Progress feedback
reuses the trailprint_progress.json that SubprocessProgress already writes.
"""

import json
import pathlib
import socket
import subprocess
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

_PROGRESS_JSON = pathlib.Path(tempfile.gettempdir()) / "trailprint_progress.json"

# ── Shared state (main thread writes, server thread reads) ────────────────────
_lock           = threading.Lock()
_status         = {"status": "idle", "message": "Ready", "last_export": ""}
_generate_event = threading.Event()
_quit_event     = threading.Event()
_pending_config: dict = {}

# ── HTML ──────────────────────────────────────────────────────────────────────
_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>TrailPrint3D — Headless Generator</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#1a1a1a;color:#dedede;
  font-size:13px;line-height:1.5;padding:20px;min-height:100vh}
.app{max-width:680px;margin:0 auto}
header{display:flex;align-items:center;gap:10px;margin-bottom:20px;
  padding-bottom:14px;border-bottom:1px solid #2e2e2e}
header h1{font-size:16px;font-weight:700;color:#fff;letter-spacing:.01em}
header .sub{color:#666;font-size:11px;margin-left:auto}
.card{background:#242424;border-radius:8px;padding:16px 18px;margin-bottom:14px}
.card-title{font-size:10px;font-weight:700;text-transform:uppercase;
  letter-spacing:.09em;color:#666;margin-bottom:12px}
.field{margin-bottom:10px}
.field:last-child{margin-bottom:0}
label.lbl{display:block;font-size:11px;color:#888;margin-bottom:3px}
input[type=text],input[type=number],select{
  width:100%;background:#2e2e2e;border:1px solid #3a3a3a;border-radius:5px;
  color:#dedede;padding:6px 10px;font-size:13px;outline:none;
  transition:border-color .15s;font-family:inherit}
input:focus,select:focus{border-color:#e87e04}
select option{background:#2e2e2e}
.inp-row{display:flex;gap:6px}
.inp-row input{flex:1}
.btn-browse{background:#2e2e2e;border:1px solid #3a3a3a;border-radius:5px;
  color:#aaa;padding:6px 14px;cursor:pointer;white-space:nowrap;font-size:12px;
  transition:background .15s,border-color .15s;font-family:inherit}
.btn-browse:hover{background:#363636;border-color:#555;color:#dedede}
.btn-browse:disabled{opacity:.5;cursor:wait}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
/* toggles */
.toggles{display:flex;flex-wrap:wrap;gap:7px}
.tgl{display:inline-flex;align-items:center;gap:5px;background:#2a2a2a;
  border:1px solid #363636;border-radius:4px;padding:4px 9px;
  cursor:pointer;user-select:none;transition:border-color .15s,background .15s}
.tgl:hover{border-color:#555}
.tgl.on{border-color:#e87e04;background:#2d2010}
.tgl input{display:none}
.tgl span{font-size:12px}
/* collapsible */
.adv-head{display:flex;align-items:center;gap:8px;cursor:pointer;
  color:#666;font-size:11px;user-select:none;margin-bottom:0}
.adv-head::before,.adv-head::after{content:'';flex:1;border-top:1px solid #2e2e2e}
.adv-body{display:none;margin-top:12px}
.adv-body.open{display:block}
/* buttons */
.btn-row{display:flex;gap:10px;margin-top:4px}
.btn{flex:1;padding:10px;border:none;border-radius:6px;font-size:14px;
  font-weight:600;cursor:pointer;transition:background .15s,opacity .15s;
  font-family:inherit}
.btn-gen{background:#e87e04;color:#fff}
.btn-gen:hover:not(:disabled){background:#f08c14}
.btn-gen:disabled{opacity:.4;cursor:not-allowed}
.btn-quit{background:#2e2e2e;color:#aaa;border:1px solid #3a3a3a}
.btn-quit:hover{background:#363636;color:#dedede}
/* status */
.status{display:flex;align-items:flex-start;gap:12px;background:#242424;
  border-radius:8px;padding:14px 18px;margin-top:14px;
  border:1px solid #2e2e2e;transition:border-color .3s}
.status.generating{border-color:#e87e04}
.status.done{border-color:#4caf50}
.status.error{border-color:#e53935}
.dot{width:9px;height:9px;border-radius:50%;background:#444;
  flex-shrink:0;margin-top:4px}
.status.idle .dot{background:#444}
.status.generating .dot{background:#e87e04;animation:pulse 1s infinite}
.status.done .dot{background:#4caf50}
.status.error .dot{background:#e53935}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}
.st-inner{flex:1;min-width:0}
.st-lbl{font-size:10px;text-transform:uppercase;letter-spacing:.07em;color:#555}
.st-msg{margin-top:2px;color:#ccc;word-break:break-word}
.prog-wrap{height:3px;background:#333;border-radius:2px;margin-top:8px;display:none}
.prog-fill{height:100%;background:#e87e04;border-radius:2px;transition:width .4s}
.prog-wrap.vis{display:block}
.st-phase{font-size:11px;color:#888;margin-top:3px}
</style>
</head>
<body>
<div class="app">

<header>
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
    <path d="M12 2L3 7v10l9 5 9-5V7z" fill="#e87e04" opacity=".85"/>
    <path d="M12 2L3 7l9 5 9-5z" fill="#f08c14"/>
  </svg>
  <h1>TrailPrint3D</h1>
  <span class="sub">Headless Generator</span>
</header>

<!-- Files -->
<div class="card">
  <div class="card-title">Files</div>
  <div class="field">
    <label class="lbl">GPX file</label>
    <div class="inp-row">
      <input type="text" id="gpx_file" placeholder="C:\\path\\to\\track.gpx">
      <button class="btn-browse" onclick="browse('gpx_file','file')">Browse…</button>
    </div>
  </div>
  <div class="field">
    <label class="lbl">Export folder</label>
    <div class="inp-row">
      <input type="text" id="export_path" placeholder="C:\\path\\to\\output\\">
      <button class="btn-browse" onclick="browse('export_path','folder')">Browse…</button>
    </div>
  </div>
</div>

<!-- Map settings -->
<div class="card">
  <div class="card-title">Map Settings</div>
  <div class="row2">
    <div class="field">
      <label class="lbl">Shape</label>
      <select id="shape">
        <option value="HEXAGON">Hexagon</option>
        <option value="SQUARE">Rectangle</option>
        <option value="CIRCLE">Circle</option>
        <option value="OCTAGON">Octagon</option>
        <option value="ELLIPSE">Ellipse</option>
        <option value="HEART">Heart</option>
      </select>
    </div>
    <div class="field">
      <label class="lbl">Map Size (mm)</label>
      <input type="number" id="obj_size" value="100" min="5" max="10000">
    </div>
  </div>
  <div class="row3">
    <div class="field">
      <label class="lbl">Elevation Scale</label>
      <input type="number" id="elev_scale" value="1.5" min="0" step="0.1">
    </div>
    <div class="field">
      <label class="lbl">Resolution (1–10)</label>
      <input type="number" id="resolution" value="4" min="1" max="10">
    </div>
    <div class="field">
      <label class="lbl">Min Thickness (mm)</label>
      <input type="number" id="min_thickness" value="2" min="0.5" step="0.5">
    </div>
  </div>
  <div class="field">
    <label class="lbl">Elevation API</label>
    <select id="api">
      <option value="MAPTERHORN" selected>Mapterhorn</option>
      <option value="TERRAIN-TILES">Terrain Tiles (fastest)</option>
      <option value="OPENTOPODATA">OpenTopoData</option>
      <option value="OPEN-ELEVATION">Open-Elevation</option>
      <option value="OPENTOPOGRAPHY">OpenTopography (needs API key)</option>
    </select>
  </div>
</div>

<!-- OSM elements -->
<div class="card">
  <div class="card-title">OSM Elements</div>
  <div class="toggles">
    <label class="tgl" data-id="water"><input type="checkbox" id="water"><span>Water</span></label>
    <label class="tgl" data-id="rivers_big"><input type="checkbox" id="rivers_big"><span>Big Rivers</span></label>
    <label class="tgl" data-id="rivers_small"><input type="checkbox" id="rivers_small"><span>Small Rivers</span></label>
    <label class="tgl" data-id="forest"><input type="checkbox" id="forest"><span>Forest</span></label>
    <label class="tgl" data-id="cities"><input type="checkbox" id="cities"><span>City Bounds</span></label>
    <label class="tgl" data-id="greenspace"><input type="checkbox" id="greenspace"><span>Greenspaces</span></label>
    <label class="tgl" data-id="buildings"><input type="checkbox" id="buildings"><span>Buildings</span></label>
    <label class="tgl" data-id="roads_big"><input type="checkbox" id="roads_big"><span>Major Roads</span></label>
    <label class="tgl" data-id="roads_med"><input type="checkbox" id="roads_med"><span>Secondary Roads</span></label>
    <label class="tgl" data-id="roads_small"><input type="checkbox" id="roads_small"><span>Small Roads</span></label>
  </div>
</div>

<!-- Advanced (collapsible) -->
<div class="card">
  <div class="adv-head" onclick="toggleAdv()"><span id="adv-lbl">Advanced</span></div>
  <div class="adv-body" id="adv-body">
    <div class="row2">
      <div class="field">
        <label class="lbl">Element Mode</label>
        <select id="element_mode">
          <option value="PAINT">Paint on Map</option>
          <option value="SINGLECOLORMODE_REMESH">Single Color Mode</option>
          <option value="SEPARATE">Separate Objects</option>
        </select>
      </div>
      <div class="field" style="display:flex;align-items:flex-end;padding-bottom:1px">
        <label class="tgl" data-id="scm" style="width:100%;justify-content:center">
          <input type="checkbox" id="single_color_mode">
          <span>Single Color Trail</span>
        </label>
      </div>
    </div>
  </div>
</div>

<div class="btn-row">
  <button class="btn btn-gen" id="btn-gen" onclick="generate()">Generate Map</button>
  <button class="btn btn-quit" onclick="quit()">Quit</button>
</div>

<!-- Status -->
<div class="status idle" id="status-card">
  <div class="dot"></div>
  <div class="st-inner">
    <div class="st-lbl">Status</div>
    <div class="st-msg" id="st-msg">Idle — fill in the settings above and click Generate</div>
    <div class="st-phase" id="st-phase"></div>
    <div class="prog-wrap" id="prog-wrap"><div class="prog-fill" id="prog-fill" style="width:0%"></div></div>
  </div>
</div>

</div><!-- .app -->
<script>
async function browse(inputId, type){
  const btn=event.currentTarget;
  const prev=btn.textContent;
  btn.textContent='…';btn.disabled=true;
  try{
    const r=await fetch('/browse/'+type);
    const d=await r.json();
    if(d.path) document.getElementById(inputId).value=d.path;
  }catch(e){alert('File picker failed: '+e);}
  finally{btn.textContent=prev;btn.disabled=false;}
}
function toggleAdv(){
  const b=document.getElementById('adv-body');
  const l=document.getElementById('adv-lbl');
  b.classList.toggle('open');
  l.textContent=b.classList.contains('open')?'Advanced ▾':'Advanced ▸';
}
// toggle buttons
document.querySelectorAll('.tgl').forEach(el=>{
  const cb=el.querySelector('input[type=checkbox]');
  cb.addEventListener('change',()=>el.classList.toggle('on',cb.checked));
});
function getConfig(){
  return{
    gpx_file:document.getElementById('gpx_file').value.trim(),
    export_path:document.getElementById('export_path').value.trim(),
    shape:document.getElementById('shape').value,
    obj_size:parseInt(document.getElementById('obj_size').value)||100,
    elev_scale:parseFloat(document.getElementById('elev_scale').value)||1.0,
    resolution:parseInt(document.getElementById('resolution').value)||4,
    min_thickness:parseFloat(document.getElementById('min_thickness').value)||2.0,
    api:document.getElementById('api').value,
    water:document.getElementById('water').checked,
    rivers_big:document.getElementById('rivers_big').checked,
    rivers_small:document.getElementById('rivers_small').checked,
    forest:document.getElementById('forest').checked,
    cities:document.getElementById('cities').checked,
    greenspace:document.getElementById('greenspace').checked,
    buildings:document.getElementById('buildings').checked,
    roads_big:document.getElementById('roads_big').checked,
    roads_med:document.getElementById('roads_med').checked,
    roads_small:document.getElementById('roads_small').checked,
    element_mode:document.getElementById('element_mode').value,
    single_color_mode:document.getElementById('single_color_mode').checked,
  };
}
function generate(){
  const cfg=getConfig();
  if(!cfg.gpx_file){alert('GPX file path is required.');return;}
  if(!cfg.export_path){alert('Export folder path is required.');return;}
  document.getElementById('btn-gen').disabled=true;
  setStatus('generating','Sending to Blender…','');
  fetch('/generate',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(cfg)})
  .catch(e=>{setStatus('error','Could not reach Blender: '+e,'');
    document.getElementById('btn-gen').disabled=false;});
}
function quit(){
  fetch('/quit',{method:'POST'}).catch(()=>{});
  document.getElementById('btn-quit').textContent='Shutting down…';
  document.getElementById('btn-quit').disabled=true;
}
function setStatus(st,msg,phase){
  const card=document.getElementById('status-card');
  card.className='status '+st;
  document.getElementById('st-msg').textContent=msg;
  document.getElementById('st-phase').textContent=phase||'';
  const pw=document.getElementById('prog-wrap');
  pw.className='prog-wrap'+(st==='generating'?' vis':'');
}
function poll(){
  fetch('/status').then(r=>r.json()).then(d=>{
    const btn=document.getElementById('btn-gen');
    if(d.status==='generating'){
      fetch('/progress').then(r=>r.json()).then(p=>{
        const pct=((p.percent||0)*100).toFixed(0);
        document.getElementById('prog-fill').style.width=pct+'%';
        const phase=p.phase||'';
        const detail=p.message||'';
        setStatus('generating',phase+(detail?' — '+detail:''),pct+'%');
      }).catch(()=>{});
    }else if(d.status==='done'){
      document.getElementById('prog-fill').style.width='100%';
      setStatus('done','✓ '+d.message+(d.last_export?' → '+d.last_export:''),'');
      btn.disabled=false;
    }else if(d.status==='error'){
      setStatus('error','✗ '+d.message,'');
      btn.disabled=false;
    }else{
      setStatus('idle','Idle — fill in the settings above and click Generate','');
      btn.disabled=false;
    }
  }).catch(()=>{});
}
setInterval(poll,800);
</script>
</body>
</html>
"""

# ── HTTP handler ──────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self._send(200, "text/html; charset=utf-8", _HTML.encode("utf-8"))
        elif self.path == "/status":
            with _lock:
                data = dict(_status)
            self._send(200, "application/json", json.dumps(data).encode())
        elif self.path == "/progress":
            try:
                body = _PROGRESS_JSON.read_bytes()
            except FileNotFoundError:
                body = b"{}"
            self._send(200, "application/json", body)
        elif self.path == "/browse/file":
            path = _pick_file()
            self._send(200, "application/json", json.dumps({"path": path}).encode())
        elif self.path == "/browse/folder":
            path = _pick_folder()
            self._send(200, "application/json", json.dumps({"path": path}).encode())
        else:
            self._send(404, "text/plain", b"Not found")

    def do_POST(self):
        global _pending_config
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        if self.path == "/generate":
            try:
                config = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                config = {}
            with _lock:
                _pending_config = config
                _status.update({"status": "generating", "message": "Starting…", "last_export": ""})
            _generate_event.set()
            self._send(200, "application/json", b'{"ok":true}')

        elif self.path == "/quit":
            _quit_event.set()
            self._send(200, "application/json", b'{"ok":true}')

        else:
            self._send(404, "text/plain", b"Not found")

    def _send(self, code, content_type, body):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # silence access log


# ── Public class ──────────────────────────────────────────────────────────────

class HeadlessConfigServer:
    """
    Start a local config UI, then loop:

        server = HeadlessConfigServer()
        server.start()
        while True:
            config = server.wait_for_generate()
            if config is None:   # user clicked Quit
                break
            # apply config, run generation ...
            server.notify_done(export_path)
        server.stop()
    """

    def __init__(self):
        self._port   = _free_port()
        self._server = HTTPServer(("127.0.0.1", self._port), _Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="tp3d-headless-ui"
        )

    def start(self):
        self._thread.start()
        url = f"http://127.0.0.1:{self._port}/"
        print(f"TrailPrint3D headless UI → {url}")
        webbrowser.open(url)

    def wait_for_generate(self) -> dict | None:
        """Block the main thread until Generate or Quit is clicked.

        Returns the config dict, or None on Quit.
        """
        while True:
            if _quit_event.is_set():
                return None
            if _generate_event.is_set():
                _generate_event.clear()
                with _lock:
                    return dict(_pending_config)
            time.sleep(0.2)

    def notify_done(self, export_path: str = ""):
        with _lock:
            _status.update({"status": "done", "message": "Export complete!", "last_export": export_path})

    def notify_error(self, message: str):
        with _lock:
            _status.update({"status": "error", "message": message})

    def stop(self):
        self._server.shutdown()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_ps(command: str) -> str:
    """Run a PowerShell snippet and return its stdout, stripped."""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; " + command],
        capture_output=True,
        check=False,
    )
    return result.stdout.decode("utf-8", errors="replace").strip()


def _pick_file() -> str:
    """Open the Windows file picker filtered to GPX/IGC files."""
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$f = New-Object System.Windows.Forms.OpenFileDialog; "
        "$f.Title = 'Select GPX or IGC file'; "
        "$f.Filter = 'GPX/IGC files (*.gpx;*.igc)|*.gpx;*.igc|All files (*.*)|*.*'; "
        "if ($f.ShowDialog() -eq 'OK') { Write-Output $f.FileName }"
    )
    return _run_ps(ps)


def _pick_folder() -> str:
    """Open the Windows folder picker and return the selected path with trailing backslash."""
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$b = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$b.Description = 'Select export folder'; "
        "if ($b.ShowDialog() -eq 'OK') { Write-Output $b.SelectedPath }"
    )
    path = _run_ps(ps)
    if path and not path.endswith("\\"):
        path += "\\"
    return path
