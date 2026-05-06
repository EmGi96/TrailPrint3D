#  Copyright (C) 2026  EmGi
# Standalone progress window — spawned as a subprocess by TrailPrint3D.
# Starts a local HTTP server and opens Edge/Chrome in --app mode.
# After the window opens, Win32 ctypes strips the native frame so the result
# is a fully frameless card.  Falls back to ANSI console when no Chromium
# browser is found.

import sys
import json
import re
import pathlib
import time
import socket
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler


# ── HTML template ─────────────────────────────────────────────────────────────
# __PORT__ is replaced at runtime with the actual port number.

_HTML = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>TrailPrint3D</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{overflow:hidden;background:#1e1e1e;color:#dedede;
  font-family:'Segoe UI',system-ui,sans-serif;-webkit-font-smoothing:antialiased}
.card{padding:13px 15px 12px}
.crow{display:block;min-width:0}
.crow.wide{display:flex;gap:10px;align-items:flex-start}
.main-col{flex:1;min-width:0}
#map-prev{display:none;flex-shrink:0;align-self:flex-start}
#map-prev svg{display:block;border-radius:6px;width:100%;height:100%}
.r1{display:flex;align-items:center;margin-bottom:10px}
.title{font-size:13px;font-weight:700;color:#f27a0d;letter-spacing:.02em}
.elapsed{font-size:11px;color:#505050;margin-left:7px;font-variant-numeric:tabular-nums}
.ptmr{font-size:10px;color:#333;font-variant-numeric:tabular-nums;margin-left:auto;margin-right:7px;white-space:nowrap}
.x{font-size:12px;color:#484848;cursor:pointer;padding:1px 4px;
   border-radius:3px;line-height:1.3;transition:color .12s,background .12s}
.x:hover{color:#e05050;background:rgba(200,50,50,.13)}
.bar-row{display:flex;align-items:center;gap:9px;margin-bottom:12px}
.track{flex:1;height:6px;background:#111;border-radius:3px;overflow:hidden}
.fill{height:100%;background:linear-gradient(90deg,#c96200,#f27a0d);
      border-radius:3px;width:0%;transition:width .25s cubic-bezier(.4,0,.2,1)}
.pct{font-size:11px;color:#505050;flex-shrink:0;font-variant-numeric:tabular-nums;
     min-width:34px;text-align:right}
#fstrip{display:none;justify-content:space-evenly;flex-wrap:wrap;
        gap:6px 0;margin-bottom:10px}
.chip{display:inline-flex;align-items:center;gap:8px;min-width:56px}
.badge{display:inline-flex;align-items:center;justify-content:center;
       width:36px;height:34px;border-radius:5px;font-size:12px;font-weight:700;
       color:#fff;flex-shrink:0;line-height:1;position:relative}
.badge svg{width:20px;height:20px;display:block;flex-shrink:0}
.pdot{position:absolute;top:4px;right:4px;width:6px;height:6px;border-radius:50%;
      background:#f27a0d;animation:tp3d-pulse 1.4s ease-in-out infinite}
@keyframes tp3d-pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.2;transform:scale(.6)}}
.chip-st{font-size:18px;font-weight:400;font-variant-numeric:tabular-nums;
         color:#555;white-space:nowrap;min-width:44px}
.chip-st.ok{color:#3db85a}
.chip-st.fail{color:#c03030}
.chip-st.act{color:#d0d0d0}
#info-wrap{background:#181818;border-radius:6px;padding:8px 10px;display:none;
           height:150px;overflow-y:auto;scrollbar-width:thin;scrollbar-color:#333 transparent}
.phase{font-size:10.5px;color:#858585;overflow:hidden;text-overflow:ellipsis;
       white-space:nowrap;margin-bottom:2px}
.msg{font-size:10px;color:#484848;overflow:hidden;text-overflow:ellipsis;
     white-space:nowrap}
#sub{display:none;margin-top:6px}
.r-sub{display:flex;align-items:baseline;margin-bottom:3px}
.sl{font-size:10px;color:#484848;flex:1}
.sp{font-size:10px;color:#484848}
.sub-track{height:3px;background:#111;border-radius:2px;overflow:hidden}
.sub-fill{height:100%;background:#5aaade;border-radius:2px;width:0%;
          transition:width .2s cubic-bezier(.4,0,.2,1)}
#sep{display:none;height:1px;background:#252525;margin:6px 0 4px}
#steps .step{display:flex;align-items:baseline;margin-bottom:2px;font-size:10.5px;color:#858585}
#steps .ok{color:#3db85a;margin-right:5px;flex-shrink:0}
#cancel-wrap{display:none;margin-top:8px;text-align:center}
#cancel-btn{background:#2a2a2a;border:1px solid #3a3a3a;color:#aaa;font-size:11px;
            padding:4px 16px;border-radius:4px;cursor:pointer;transition:border-color .12s,color .12s;
            font-family:inherit}
#cancel-btn:not(:disabled):hover{border-color:#f27a0d;color:#f27a0d}
#cancel-btn:disabled{color:#555;border-color:#2a2a2a;cursor:default}
</style>
</head>
<body>
<div class="card" id="card">
  <div class="r1">
    <span class="title">TrailPrint3D</span>
    <span class="elapsed" id="el">00:00</span>
    <span class="ptmr" id="ptmr">0:00</span>
    <span class="x" onclick="window.close()">&#x2715;</span>
  </div>
  <div class="crow" id="crow">
    <div class="main-col">
      <div class="bar-row">
        <div class="track"><div class="fill" id="bar"></div></div>
        <span class="pct" id="pc">0&#x202f;%</span>
      </div>
      <div id="fstrip"></div>
      <div id="info-wrap">
        <div class="phase" id="ph">Starting&#x2026;</div>
        <div class="msg"   id="ms"></div>
        <div id="sub">
          <div class="r-sub">
            <span class="sl" id="sl"></span>
            <span class="sp" id="sp">0 %</span>
          </div>
          <div class="sub-track"><div class="sub-fill" id="sb"></div></div>
        </div>
        <div id="sep"></div>
        <div id="steps"></div>
      </div>
      <div id="cancel-wrap">
        <button id="cancel-btn" onclick="requestCancel()">Cancel</button>
      </div>
    </div>
    <div id="map-prev"></div>
  </div>
</div>
<script>
const PORT = __PORT__;
const t0   = Date.now();

var _lastPhase        = null;
var _phaseStart       = Date.now();
var _winW             = 360;
var _mapPrevH         = 0;   // cached square size for the tile preview; set once, never cleared
var _cancelRequested  = false;

function requestCancel() {
  if (_cancelRequested) return;
  _cancelRequested = true;
  var btn = document.getElementById('cancel-btn');
  btn.textContent = 'Canceling after this tile…';
  btn.disabled = true;
  fetch('http://127.0.0.1:'+PORT+'/cancel', {method:'POST'}).catch(function(){});
}

function _fmtPhaseAge() {
  var s = Math.floor((Date.now() - _phaseStart) / 1000);
  return Math.floor(s/60)+':'+String(s%60).padStart(2,'0')+' since last change';
}

__ICONS__

var BADGE_COLORS = {
  elevation:'#3d72b2', forest:'#2d8f3d', water:'#2d78cc',
  scree:'#7a6248',     city:'#8844aa',   greenspace:'#4daa3d',
  farmland:'#aa9928',  glacier:'#7dc0e8',ocean:'#1244aa',
  buildings:'#cc8222', roads:'#444'
};

// ── position ──────────────────────────────────────────────────────────────────
// POSITION options: 'bottom-right'  'bottom-left'  'top-right'  'top-left'  'center'
var POSITION  = 'bottom-left';
var MARGIN_X  = 80;
var MARGIN_Y  = 160;

function reposition(H) {
  var W = _winW, aw = screen.availWidth, ah = screen.availHeight, x, y;
  if      (POSITION==='bottom-right'){x=aw-W-MARGIN_X; y=ah-H-MARGIN_Y;}
  else if (POSITION==='bottom-left') {x=MARGIN_X;       y=ah-H-MARGIN_Y;}
  else if (POSITION==='top-right')   {x=aw-W-MARGIN_X;  y=MARGIN_Y;}
  else if (POSITION==='top-left')    {x=MARGIN_X;        y=MARGIN_Y;}
  else                               {x=(aw-W)/2;        y=(ah-H)/2;}
  window.moveTo(x, y);
}

// chrome is recalculated on every fit so it automatically becomes 0
// after the native frame is stripped by Win32.
var _rafId = null;
function fitWindow() {
  if (_rafId) cancelAnimationFrame(_rafId);
  _rafId = requestAnimationFrame(function() {
    var chrome = window.outerHeight - window.innerHeight;
    var h = document.getElementById('card').offsetHeight;
    var newH = h + chrome;
    if (Math.abs(window.outerHeight - newH) > 1 || window.outerWidth !== _winW) {
      window.resizeTo(_winW, newH);
      reposition(newH);
    }
  });
}

window.addEventListener('load', function() {
  var chrome = window.outerHeight - window.innerHeight;
  var h = document.getElementById('card').offsetHeight;
  window.resizeTo(_winW, h + chrome);
  reposition(h + chrome);
});

// ── elapsed timer ─────────────────────────────────────────────────────────────
setInterval(function() {
  var e = Math.floor((Date.now()-t0)/1000);
  document.getElementById('el').textContent =
    String(Math.floor(e/60)).padStart(2,'0')+':'+String(e%60).padStart(2,'0');
  document.getElementById('ptmr').textContent = _fmtPhaseAge();
}, 1000);

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function renderFetchItems(items) {
  var el = document.getElementById('fstrip');
  if (!items || items.length === 0) { el.style.display = 'none'; return; }
  el.style.display = 'flex';
  el.innerHTML = '';
  items.forEach(function(item) {
    var status = item.status;
    var bc = BADGE_COLORS[item.key] || '#555';
    var bdr = 'none';
    if (status === 'fetching') { bc = '#2d78cc'; }
    if (status === 'done')    { bc = '#2a9940'; }
    if (status === 'failed')  { bc = '#aa2222'; }
    if (status === 'empty')   { bc = '#2a2a2a'; bdr = '1.5px solid #484848'; }
    if (status === 'pending') { bc = 'transparent'; bdr = '1.5px solid #555'; }
    var pct = Math.round((item.percent || 0) * 100);
    var stTxt, stCls;
    if      (status === 'done')    { stTxt = '&#x2713;'; stCls = 'ok'; }
    else if (status === 'failed')  { stTxt = '&#x2717;'; stCls = 'fail'; }
    else if (status === 'empty')   { stTxt = '0';         stCls = ''; }
    else if (status === 'pending') { stTxt = '&ndash;';   stCls = ''; }
    else                           { stTxt = pct + '%';   stCls = 'act'; }
    var badgeContent = (ICONS && ICONS[item.key]) ? ICONS[item.key] : esc(item.icon || '?');
    var chip = document.createElement('span');
    chip.className = 'chip';
    chip.innerHTML =
      '<span class="badge" style="background:' + bc + ';border:' + bdr + '">' + badgeContent + '</span>' +
      '<span class="chip-st ' + stCls + '">' + stTxt + '</span>';
    el.appendChild(chip);
  });
  fitWindow();
}

// ── mini-map preview ──────────────────────────────────────────────────────────
function _shapePts(n, cx, cy, r, startAngle) {
  var pts = [];
  for (var i = 0; i < n; i++) {
    var a = startAngle + i * (2 * Math.PI / n);
    pts.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]);
  }
  return pts;
}
function _ptsToPath(pts) {
  return pts.map(function(p, i) {
    return (i===0?'M':'L')+p[0].toFixed(1)+','+p[1].toFixed(1);
  }).join(' ')+' Z';
}
function makeShapeSvg(mp) {
  var s = (mp.shape||'').toLowerCase();
  var rot = (mp.rotation||0)*Math.PI/180;
  var cx=40, cy=40, r=32, aspect=mp.aspect||1;
  var fill='#141414', stk='#f27a0d', sw='1.5';
  var d;
  if (s.indexOf('hexagon')>=0) {
    d = _ptsToPath(_shapePts(6, cx, cy, r, rot-Math.PI/2));
  } else if (s.indexOf('octagon')>=0) {
    d = _ptsToPath(_shapePts(8, cx, cy, r, rot-Math.PI/8));
  } else if (s==='square') {
    var rh=Math.min(r*aspect, 38);
    d = _ptsToPath([[cx-r,cy-rh],[cx+r,cy-rh],[cx+r,cy+rh],[cx-r,cy+rh]]);
  } else if (s==='ellipse') {
    var ry=Math.min(r*aspect,38);
    return '<ellipse cx="'+cx+'" cy="'+cy+'" rx="'+r+'" ry="'+ry+'" fill="'+fill+'" stroke="'+stk+'" stroke-width="'+sw+'"/>';
  } else {
    return '<circle cx="'+cx+'" cy="'+cy+'" r="'+r+'" fill="'+fill+'" stroke="'+stk+'" stroke-width="'+sw+'"/>';
  }
  return '<path d="'+d+'" fill="'+fill+'" stroke="'+stk+'" stroke-width="'+sw+'"/>';
}
function makeTrailSvg(trail) {
  if (!trail||trail.length<2) return '';
  var cx=40, cy=40, r=29, d='';
  for (var i=0; i<trail.length; i++) {
    d += (i===0?'M':'L')+(cx+trail[i][0]*r).toFixed(1)+','+(cy-trail[i][1]*r).toFixed(1);
  }
  return '<path d="'+d+'" fill="none" stroke="#fff" stroke-width="1.5" stroke-opacity=".6" stroke-linecap="round" stroke-linejoin="round"/>';
}
function _drawTileShape(s, px, py, r, fill, stk, sw) {
  var d;
  if (s.indexOf('hexagon') >= 0) {
    d = _ptsToPath(_shapePts(6, px, py, r, 0));
  } else if (s.indexOf('octagon') >= 0) {
    d = _ptsToPath(_shapePts(8, px, py, r, -Math.PI/8));
  } else if (s === 'ellipse' || s === 'circle') {
    return '<circle cx="'+px.toFixed(1)+'" cy="'+py.toFixed(1)+'" r="'+r+
           '" fill="'+fill+'" stroke="'+stk+'" stroke-width="'+sw+'"/>';
  } else {
    return '<rect x="'+(px-r).toFixed(1)+'" y="'+(py-r).toFixed(1)+
           '" width="'+(r*2)+'" height="'+(r*2)+
           '" rx="1" fill="'+fill+'" stroke="'+stk+'" stroke-width="'+sw+'"/>';
  }
  return '<path d="'+d+'" fill="'+fill+'" stroke="'+stk+'" stroke-width="'+sw+'"/>';
}
function renderTileSvg(tiles, tileSize) {
  var n = tiles.length;
  var ts = tileSize || 1.0;

  // Bounding box of tile centres in Blender world space
  var minBx = Infinity, maxBx = -Infinity, minBy = Infinity, maxBy = -Infinity;
  for (var i = 0; i < n; i++) {
    var bx = tiles[i].bx || 0, by = tiles[i].by || 0;
    if (bx < minBx) minBx = bx; if (bx > maxBx) maxBx = bx;
    if (by < minBy) minBy = by; if (by > maxBy) maxBy = by;
  }

  // ts is diameter; radius = ts/2
  var tr = ts / 2;

  // World extent including one radius on each side
  var worldW = (maxBx - minBx) + 2 * tr;
  var worldH = (maxBy - minBy) + 2 * tr;
  var worldSize = Math.max(worldW, worldH);

  // Scale to fit inside the 80×80 viewBox with a small outer pad, then centre
  var OUTER = 4, canvas = 80 - 2 * OUTER;
  var scale = canvas / worldSize;
  var r = Math.max(3, Math.min(tr * scale, 30));

  var groupW = worldW * scale, groupH = worldH * scale;
  var ox0 = OUTER + (canvas - groupW) / 2 + tr * scale;
  var oy0 = OUTER + (canvas - groupH) / 2 + tr * scale;

  var pendingOut = '', doneOut = '', activeOut = '';
  for (var i = 0; i < n; i++) {
    var t = tiles[i];
    var px = ox0 + (t.bx - minBx) * scale;
    var py = oy0 + (maxBy - t.by) * scale;  // flip Y (Blender Y-up → SVG Y-down)
    var fill, stk, sw;
    if (t.status === 'active') {
      fill='#1c3a5e'; stk='#f27a0d'; sw=2;
      activeOut += _drawTileShape(t.shape || '', px, py, r, fill, stk, sw);
    } else if (t.status === 'done') {
      fill='#1a3a1e'; stk='#3a8a3a'; sw=1.5;
      doneOut += _drawTileShape(t.shape || '', px, py, r, fill, stk, sw);
    } else {
      fill='#222'; stk='#444'; sw=1;
      pendingOut += _drawTileShape(t.shape || '', px, py, r, fill, stk, sw);
    }
  }
  return pendingOut + doneOut + activeOut;
}
function renderMapPreview(d) {
  var el = document.getElementById('map-prev');
  var cr = document.getElementById('crow');
  var cw = document.getElementById('cancel-wrap');
  if (!d.map_preview) {
    el.style.display = 'none';
    cw.style.display = 'none';
    cr.className = 'crow';
    if (_winW !== 360) { _winW = 360; fitWindow(); }
    return;
  }
  el.style.display = 'block';
  cr.className = 'crow wide';
  var mp = d.map_preview;
  var showCancel = !!(mp.tiles && mp.tiles.length > 1);
  cw.style.display = showCancel ? 'block' : 'none';
  var inner = mp.tiles ? renderTileSvg(mp.tiles, mp.tile_size) : (makeShapeSvg(mp) + makeTrailSvg(mp.trail));
  el.innerHTML =
    '<svg viewBox="0 0 80 80" xmlns="http://www.w3.org/2000/svg">' +
    '<rect width="80" height="80" rx="6" fill="#111"/>' +
    inner + '</svg>';
  if (_mapPrevH) {
    // Already measured — just apply the cached size
    el.style.width  = _mapPrevH + 'px';
    el.style.height = _mapPrevH + 'px';
    var needed = 360 + 10 + _mapPrevH;
    if (_winW !== needed) { _winW = needed; fitWindow(); }
  } else {
    // First time shown — measure after layout and cache
    requestAnimationFrame(function() {
      _mapPrevH = document.querySelector('.main-col').offsetHeight;
      el.style.width  = _mapPrevH + 'px';
      el.style.height = _mapPrevH + 'px';
      _winW = 360 + 10 + _mapPrevH;
      fitWindow();
    });
  }
}

// ── state renderer ────────────────────────────────────────────────────────────
function applyState(d) {
  var phase = d.phase || '';
  if (phase !== _lastPhase) { _lastPhase = phase; _phaseStart = Date.now(); }
  document.getElementById('ph').textContent  = phase;
  document.getElementById('ms').textContent  = d.message || '';
  document.getElementById('pc').textContent  = Math.round((d.percent||0)*100)+' %';
  document.getElementById('bar').style.width = ((d.percent||0)*100)+'%';

  renderFetchItems(d.fetch_items || null);
  renderMapPreview(d);

  // Show the info card whenever there is content to display
  var hasInfo = !!(d.phase || d.message || d.sub_percent != null ||
                  (d.steps && d.steps.length > 0));
  document.getElementById('info-wrap').style.display = hasInfo ? 'block' : 'none';

  var steps  = d.steps || [];
  var sepEl  = document.getElementById('sep');
  var listEl = document.getElementById('steps');
  if (steps.length > 0) {
    sepEl.style.display = 'block';
    listEl.innerHTML = '';
    steps.slice().reverse().forEach(function(s) {
      var div = document.createElement('div');
      div.className = 'step';
      div.innerHTML = '<span class="ok">&#x2713;</span>' + esc(s);
      listEl.appendChild(div);
    });
  } else {
    sepEl.style.display = 'none';
    listEl.innerHTML = '';
  }

  var subEl = document.getElementById('sub');
  if (d.sub_percent != null) {
    subEl.style.display = 'block';
    document.getElementById('sl').textContent = d.sub_label || '';
    document.getElementById('sp').textContent = Math.round(d.sub_percent*100)+' %';
    document.getElementById('sb').style.width = (d.sub_percent*100)+'%';
  } else {
    subEl.style.display = 'none';
  }

  fitWindow();
}

// ── poll loop ─────────────────────────────────────────────────────────────────
function poll() {
  fetch('http://127.0.0.1:'+PORT+'/state',{cache:'no-store'})
    .then(function(r){return r.json()})
    .then(function(d) {
      if (!d.active) {
        d.phase = 'Done'; d.percent = 1; d.sub_percent = null;
        applyState(d);
        setTimeout(function(){window.close()}, 480);
        return;
      }
      applyState(d);
      setTimeout(poll, 150);
    })
    .catch(function(){ setTimeout(poll, 300); });
}
poll();
</script>
</body>
</html>
"""


# ── helpers ───────────────────────────────────────────────────────────────────

def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _find_chromium():
    """Return path to Edge or Chrome executable, or None."""
    import os
    if sys.platform == 'darwin':
        candidates = [
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            '/Applications/Chromium.app/Contents/MacOS/Chromium',
            '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
        ]
        for p in candidates:
            if os.path.exists(p):
                return p
        return None

    candidates = [
        r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    ]
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe')
        path, _ = winreg.QueryValueEx(key, '')
        winreg.CloseKey(key)
        if os.path.exists(path):
            return path
    except Exception:
        pass
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


# ── SVG icon loading ──────────────────────────────────────────────────────────

_ICON_MAP = {
    'elevation':  'prog_elevation.svg',
    'water':      'prog_water.svg',
    'forest':     'prog_forest.svg',
    'roads':      'prog_road.svg',
    'buildings':  'prog_building.svg',
    'scree':      'prog_scree.svg',
    'greenspace': 'prog_greenspace.svg',
    'farmland':   'prog_farmland.svg',
    'glacier':    'prog_glacier.svg',
    'city':       'prog_cityBoundaries.svg',
}

def _process_svg(content):
    """Strip boilerplate and recolor all fills/strokes to white."""
    content = re.sub(r'<\?xml[^>]*\?>', '', content)
    content = re.sub(r'<!DOCTYPE[^>]*>', '', content)
    content = re.sub(r'<!--.*?-->', '', content, flags=re.DOTALL)
    # Remove fixed pixel width/height from <svg> so CSS controls size
    content = re.sub(r'(<svg\b[^>]*?)\s+width="[^"]*"', r'\1', content)
    content = re.sub(r'(<svg\b[^>]*?)\s+height="[^"]*"', r'\1', content)
    # Recolor: presentation attributes and inline CSS
    content = re.sub(r'fill="#[0-9a-fA-F]{3,6}"', 'fill="white"', content)
    content = re.sub(r'stroke="#[0-9a-fA-F]{3,6}"', 'stroke="white"', content)
    content = re.sub(r'fill:\s*#[0-9a-fA-F]{3,6}', 'fill:white', content)
    content = re.sub(r'stroke:\s*#[0-9a-fA-F]{3,6}', 'stroke:white', content)
    return content.strip()

def _load_icons():
    here = pathlib.Path(__file__).parent / 'assets'
    icons = {}
    for key, fname in _ICON_MAP.items():
        p = here / fname
        if p.exists():
            try:
                icons[key] = _process_svg(p.read_text(encoding='utf-8'))
            except Exception:
                pass
    return icons


# ── HTTP + browser window ─────────────────────────────────────────────────────

def _run_browser(json_path):
    import subprocess as sp

    port = _free_port()
    icons_js = 'var ICONS = ' + json.dumps(_load_icons()) + ';'
    html = _HTML.replace('__PORT__', str(port)).replace('__ICONS__', icons_js).encode('utf-8')

    cancel_path = json_path.parent / 'trailprint_cancel.flag'
    try:
        cancel_path.unlink()
    except Exception:
        pass

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/':
                self._respond(200, 'text/html; charset=utf-8', html)
            elif self.path == '/state':
                try:
                    body = json_path.read_bytes()
                except Exception:
                    body = b'{"active":true,"percent":0,"phase":"Starting...","message":""}'
                self._respond(200, 'application/json', body)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if self.path == '/cancel':
                try:
                    cancel_path.write_text('1', encoding='utf-8')
                except Exception:
                    pass
                self._respond(200, 'text/plain', b'ok')
            else:
                self.send_response(404)
                self.end_headers()

        def _respond(self, code, ctype, body):
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', len(body))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_): pass
        def log_error(self, *_):   pass

    server = HTTPServer(('127.0.0.1', port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    url     = f'http://127.0.0.1:{port}/'
    browser = _find_chromium()

    if browser:
        sp.Popen([
            browser,
            f'--app={url}',
            '--window-size=360,160',
            '--no-first-run',
            '--no-default-browser-check',
            '--disable-extensions',
            '--disable-background-networking',
        ])
    else:
        import webbrowser
        webbrowser.open(url)

    # Wait until generation finishes
    while True:
        time.sleep(0.25)
        try:
            if not json.loads(json_path.read_text(encoding='utf-8')).get('active', True):
                time.sleep(0.9)   # let browser receive the final state & close
                break
        except Exception:
            pass

    server.shutdown()


# ── ANSI console fallback ─────────────────────────────────────────────────────

def _run_console(json_path):
    if sys.platform == 'win32':
        try:
            import ctypes
            h = ctypes.windll.kernel32.GetStdHandle(-11)
            m = ctypes.c_ulong()
            ctypes.windll.kernel32.GetConsoleMode(h, ctypes.byref(m))
            ctypes.windll.kernel32.SetConsoleMode(h, m.value | 4)
            ctypes.windll.kernel32.SetConsoleTitleW('TrailPrint3D — Generating')
        except Exception:
            pass

    ORANGE = '\033[38;5;214m'
    GREY   = '\033[90m'
    RESET  = '\033[0m'
    CLEAR  = '\033[2J\033[H'

    _start = time.time()
    while True:
        time.sleep(0.15)
        try:
            data = json.loads(json_path.read_text(encoding='utf-8'))
            if not data.get('active', True):
                sys.stdout.write(f'{CLEAR}{ORANGE}  TrailPrint3D{RESET}  —  Done!\n\n')
                sys.stdout.flush()
                time.sleep(0.8)
                break
            pct     = float(data.get('percent', 0))
            phase   = data.get('phase', '')
            message = data.get('message', '')
            m, s    = divmod(int(time.time() - _start), 60)
            filled  = int(pct * 20)
            bar     = '█' * filled + '░' * (20 - filled)
            sys.stdout.write(
                f'{CLEAR}{ORANGE}  TrailPrint3D{RESET}  {GREY}{m:02d}:{s:02d}{RESET}\n\n'
                f'  {ORANGE}{phase}{RESET}\n'
                f'  {ORANGE}{bar}{RESET}  {int(pct * 100)} %\n\n'
                f'  {GREY}{message}{RESET}\n'
            )
            sys.stdout.flush()
        except Exception:
            pass


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(1)
    _path = pathlib.Path(sys.argv[1])
    if sys.platform in ('win32', 'darwin'):
        try:
            _run_browser(_path)
        except Exception as e:
            print(f'TrailPrint3D progress: browser failed ({e}), using console', file=sys.stderr)
            _run_console(_path)
    else:
        _run_console(_path)
