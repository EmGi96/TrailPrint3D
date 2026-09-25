// Builds the "History" tab button (fixed to the right edge of the window)
// and the sliding drawer it opens, reused by every 2D-map picker page via
// __HISTORY_PANEL_JS__ in picker_server.py. Requires PORT to already be
// defined. Entirely self-contained -- none of the picker pages need any of
// this markup in their own HTML.
//
// Each page pairs with this in two ways:
//  - It sets window.tp3dApplyHistoryEntry to a function that applies a past
//    entry's settings blob to the page's own fields -- normally the same
//    "apply a saved state blob" logic its restoreState() already uses,
//    factored out so both can call it.
//  - Its own "Send to Blender" handler calls window.tp3dPushHistory(settings,
//    summary, thumbnail) right before POSTing to /confirm, to record what was
//    actually generated. settings is expected to be that same state-blob
//    shape; summary is a short one-line human-readable description; thumbnail
//    is an optional PNG data URL from tp3dRenderShapeThumbnail() below.
//
// tp3dRenderShapeThumbnail draws a small vector "shape outline" sketch (the
// drawn area's real-world aspect ratio, plus a rows x cols grid for
// puzzle/tile pages) rather than a screenshot of the actual Leaflet map --
// the base map tiles are cross-origin images the canvas can't read back
// (canvas-tainting), so this only ever draws paths/fills it made itself,
// which never taints the canvas.
//
// If a GPX trail is currently imported, its (decimated) track is overlaid on
// top of the shape, normalized against the page's own `coords` bbox --
// tp3dGpxTrailSegments reads the page's own `gpxLayer` FeatureGroup directly
// (every picker page uses that exact global name), so no page needs to pass
// trail data in explicitly. When there's no drawn area at all (e.g. Map
// Generator's trail-only "Generate Just Trail" send -- opts.width/height are
// 0 in that case), the trail's own extent becomes the box instead, so the
// thumbnail still shows something meaningful rather than a phantom shape.
//
// tp3dPushHistory also records the live ELEMENT_SOURCE ('OSM'/'WORLDCOVER')
// and which element categories were enabled (TP3D_ELEMENT_STATE, both from
// element_status.js) at Send time, purely by reading those globals itself --
// no page needs to pass them in. Rendered back as a small source badge + row
// of element icons under the thumbnail, using the same ELEMENT_ICONS SVGs
// the on-page status strip uses; TP3D_ELEMENT_LABELS below merges both the
// OSM and WorldCover orders so an old entry's keys resolve to a label
// regardless of which source is active in the *current* session.
var TP3D_ELEMENT_LABELS = {};
(function() {
    var orders = []
        .concat(typeof ELEMENT_STATUS_ORDER_OSM !== 'undefined' ? ELEMENT_STATUS_ORDER_OSM : [])
        .concat(typeof ELEMENT_STATUS_ORDER_WORLDCOVER !== 'undefined' ? ELEMENT_STATUS_ORDER_WORLDCOVER : []);
    orders.forEach(function(entry) { TP3D_ELEMENT_LABELS[entry[0]] = entry[1]; });
})();
// Caps how many points of a (possibly huge) GPX track actually get drawn --
// the thumbnail is ~150x110px, so anything beyond a couple hundred points
// per segment is wasted work with no visible difference.
function tp3dDecimatePoints(points, maxPoints) {
    if (points.length <= maxPoints) return points;
    var stride = Math.ceil(points.length / maxPoints);
    var out = [];
    for (var i = 0; i < points.length; i += stride) out.push(points[i]);
    var last = points[points.length - 1];
    if (out[out.length - 1] !== last) out.push(last);
    return out;
}

// Whatever GPX polyline(s) are currently drawn in the page's own `gpxLayer`
// FeatureGroup -- every picker page uses that exact variable name -- as
// plain [[lat,lng], ...] arrays (Leaflet's own already-parsed geometry, no
// need to re-parse GPX XML). Returns [] on pages/moments with nothing
// imported.
function tp3dGpxTrailSegments() {
    if (typeof gpxLayer === 'undefined' || !gpxLayer || typeof gpxLayer.eachLayer !== 'function') return [];
    var segments = [];
    gpxLayer.eachLayer(function(layer) {
        if (typeof layer.getLatLngs !== 'function') return;
        var latlngs = layer.getLatLngs();
        // A plain polyline is a flat LatLng array; a multi-segment one nests
        // one array per segment -- flatten either shape into one point list.
        var flat = (latlngs.length && Array.isArray(latlngs[0])) ? [].concat.apply([], latlngs) : latlngs;
        var pts = flat.map(function(ll) { return [ll.lat, ll.lng]; });
        if (pts.length > 1) segments.push(tp3dDecimatePoints(pts, 150));
    });
    return segments;
}

function tp3dRenderShapeThumbnail(opts) {
    opts = opts || {};
    var trail = tp3dGpxTrailSegments();
    var w = opts.width, h = opts.height;
    var hasShape = w > 0 && h > 0;
    if (!hasShape && !trail.length) return null;
    var shape = (opts.shape === 'hex' || opts.shape === 'circle') ? opts.shape : 'rect';
    var rows = opts.rows > 0 ? opts.rows : 0;
    var cols = opts.cols > 0 ? opts.cols : 0;

    // tp3d.shapeRotation (Settings modal's Map tab -- see
    // SETTINGS_MODAL_MAP_FIELDS in settings_modal.js) rotates the generated
    // shape CCW around its center in true north-up map space, same
    // convention as regularPolygonPoints()/rotatePointsDeg() in
    // map_generator.html and Blender-side shapely rotate() calls.
    // MAP_TAB_CONTROL_VALUES (settings_modal.js, global) is read directly
    // here rather than threaded through *opts* -- it's kept live-updated by
    // every picker page already, including the ones with no visible Shape
    // Rotation control of their own.
    var rotationDeg = 0;
    if (typeof MAP_TAB_CONTROL_VALUES !== 'undefined') {
        var rv = parseFloat(MAP_TAB_CONTROL_VALUES.shapeRotation);
        if (!isNaN(rv)) rotationDeg = rv;
    }
    var rotationRad = rotationDeg * Math.PI / 180;

    // Square to match both the real render (customThumbnail's own square
    // resolution x resolution output) and the .history-entry-thumb CSS box
    // it shares a display slot with -- a non-square canvas here would look
    // fine on its own but get cropped top/bottom (or squeezed) once shown
    // next to/instead of that square render.
    var W = 150, H = 150, pad = 14;
    var canvas = document.createElement('canvas');
    canvas.width = W;
    canvas.height = H;
    var ctx = canvas.getContext('2d');
    if (!ctx) return null;
    ctx.fillStyle = '#242424';
    ctx.fillRect(0, 0, W, H);

    // Fallback box for a trail with no drawn shape at all (see the header
    // comment) -- overwritten below whenever hasShape is true.
    var x0 = pad, y0 = pad, bw = W - pad * 2, bh = H - pad * 2;

    if (hasShape) {
        var scale = Math.min((W - pad * 2) / w, (H - pad * 2) / h);
        bw = w * scale; bh = h * scale;
        x0 = (W - bw) / 2; y0 = (H - bh) / 2;

        ctx.fillStyle = 'rgba(0,123,255,0.15)';
        ctx.strokeStyle = '#E97826';
        ctx.lineWidth = 2;

        if (shape === 'hex') {
            // Vertex 0 sits due east of center (flat top/bottom, points left/
            // right) at rotation 0 -- matches regularPolygonPoints(..., 0)'s
            // own angle=0 vertex (pure +lng offset, no lat component), not an
            // arbitrary "pointy-top" hexagon. The `-ry*sin` (rather than +)
            // is the canvas-Y-is-down flip needed so a positive rotationDeg
            // (CCW in real north-up map space) still turns counter-clockwise
            // on screen.
            var cx = x0 + bw / 2, cy = y0 + bh / 2, rx = bw / 2, ry = bh / 2;
            ctx.beginPath();
            for (var i = 0; i < 6; i++) {
                var a = (i * Math.PI / 3) + rotationRad;
                var px = cx + rx * Math.cos(a), py = cy - ry * Math.sin(a);
                if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
            }
            ctx.closePath();
        } else if (shape === 'circle') {
            ctx.beginPath();
            ctx.ellipse(x0 + bw / 2, y0 + bh / 2, bw / 2, bh / 2, 0, 0, Math.PI * 2);
        } else {
            ctx.beginPath();
            ctx.rect(x0, y0, bw, bh);
        }
        ctx.fill();
        ctx.stroke();

        if (shape === 'rect' && rows > 0 && cols > 0 && rows * cols <= 400) {
            ctx.strokeStyle = 'rgba(255,255,255,0.35)';
            ctx.lineWidth = 1;
            ctx.beginPath();
            for (var c = 1; c < cols; c++) {
                var gx = x0 + (bw * c) / cols;
                ctx.moveTo(gx, y0);
                ctx.lineTo(gx, y0 + bh);
            }
            for (var r = 1; r < rows; r++) {
                var gy = y0 + (bh * r) / rows;
                ctx.moveTo(x0, gy);
                ctx.lineTo(x0 + bw, gy);
            }
            ctx.stroke();
        }
    }

    if (trail.length) {
        // Normalize against the same geographic bbox the shape itself was
        // drawn from, so the trail lines up with it -- unless there IS no
        // shape (trail-only send), in which case derive a bbox from the
        // trail's own lat/lng extent instead and use the full canvas as the
        // box (computed above as the hasShape=false fallback).
        var tb = null;
        if (typeof coords !== 'undefined' && coords &&
            coords.north > coords.south && coords.east > coords.west) {
            tb = coords;
        } else {
            var north = -90, south = 90, east = -180, west = 180;
            trail.forEach(function(seg) {
                seg.forEach(function(p) {
                    if (p[0] > north) north = p[0];
                    if (p[0] < south) south = p[0];
                    if (p[1] > east) east = p[1];
                    if (p[1] < west) west = p[1];
                });
            });
            var padLat = Math.max((north - south) * 0.08, 1e-6);
            var padLng = Math.max((east - west) * 0.08, 1e-6);
            tb = { north: north + padLat, south: south - padLat, east: east + padLng, west: west - padLng };
        }
        var spanLng = (tb.east - tb.west) || 1e-9;
        var spanLat = (tb.north - tb.south) || 1e-9;
        ctx.strokeStyle = 'red';
        ctx.lineWidth = 1.5;
        trail.forEach(function(seg) {
            ctx.beginPath();
            seg.forEach(function(p, i) {
                var nx = (p[1] - tb.west) / spanLng, ny = (p[0] - tb.south) / spanLat;
                var px = x0 + nx * bw, py = y0 + (1 - ny) * bh;
                if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
            });
            ctx.stroke();
        });
    }

    return canvas.toDataURL('image/png');
}

// Pushes a saved history entry's Settings-popup snapshot (elementSource,
// every Map-tab field in entry.settingsState, every Elements-tab field in
// entry.advancedSettings, and which element categories were on/off in
// entry.elementStates) back to Blender, so picking a history entry restores
// the *whole* generation, not just the page-specific shape/resolution/coords
// fields tp3dApplyHistoryEntry already covers. Mirrors
// tp3dBuildElementSourceSwitch's own push-then-poll-then-resync pattern
// (settings_modal.js) -- POST every field, wait for Blender's modal timer to
// actually drain and apply them (~0.5s), then re-fetch /get_source_state to
// resync this page's own globals and repaint the (possibly not-currently-
// open) Settings modal. A no-op for entries saved before this existed (they
// have none of these three fields).
function tp3dApplyHistorySettings(entry) {
    if (!entry || (!entry.settingsState && !entry.advancedSettings && !entry.elementStates)) return;
    var puts = [];
    if (entry.settingsState) {
        Object.keys(entry.settingsState).forEach(function(key) {
            puts.push(fetch('http://127.0.0.1:' + PORT + '/update_setting', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key: key, value: entry.settingsState[key] })
            }).catch(function() {}));
        });
    }
    if (entry.advancedSettings) {
        // '_compositeRemembered' is this page's own bookkeeping (see
        // element_status.js's tp3dToggleElement), never a real scene field
        // -- apply_advanced_setting_update's whitelist would just silently
        // drop it, but skip it here rather than send a request for nothing.
        Object.keys(entry.advancedSettings).forEach(function(key) {
            if (key === '_compositeRemembered') return;
            puts.push(fetch('http://127.0.0.1:' + PORT + '/update_advanced_setting', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key: key, value: entry.advancedSettings[key] })
            }).catch(function() {}));
        });
    }

    function resync() {
        return fetch('http://127.0.0.1:' + PORT + '/get_source_state', { cache: 'no-store' })
            .then(function(r) { return r.json(); })
            .then(function(s) {
                ELEMENT_SOURCE = s.elementSource;
                SETTINGS_STATE = s.settingsState;
                ADVANCED_SETTINGS_STATE = s.advancedSettings;
                ELEMENT_STATUS_ORDER = tp3dIsWorldCover() ? ELEMENT_STATUS_ORDER_WORLDCOVER : ELEMENT_STATUS_ORDER_OSM;
                TP3D_ELEMENT_STATE = {};
                ELEMENT_STATUS_ORDER.forEach(function(e) { TP3D_ELEMENT_STATE[e[0]] = !!s.elementStates[e[0]]; });
                tp3dRenderElementStatus();
                if (window.tp3dRebuildElementsTab) window.tp3dRebuildElementsTab();
                if (window.tp3dRebuildMapTab) window.tp3dRebuildMapTab();
                return s;
            });
    }

    Promise.all(puts)
        .then(function() { return new Promise(function(resolve) { setTimeout(resolve, 700); }); })
        .then(resync)
        .then(function(s) {
            // Element on/off has no direct "set" route server-side, only
            // /toggle_element (a flip) -- diff the live state just fetched
            // above (which already reflects any elementSource switch from
            // entry.settingsState) against the entry's own saved
            // elementStates and toggle only the categories that differ.
            var desired = entry.elementStates || {};
            var toggles = [];
            Object.keys(desired).forEach(function(key) {
                if (!(key in TP3D_ELEMENT_STATE)) return;
                if (!!TP3D_ELEMENT_STATE[key] !== !!desired[key]) {
                    TP3D_ELEMENT_STATE[key] = !!desired[key];
                    toggles.push(fetch('http://127.0.0.1:' + PORT + '/toggle_element', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ key: key })
                    }).catch(function() {}));
                }
            });
            tp3dRenderElementStatus();
            if (window.tp3dRebuildElementsTab) window.tp3dRebuildElementsTab();
            if (!toggles.length) {
                if (typeof saveState === 'function') saveState();
                return;
            }
            Promise.all(toggles)
                .then(function() { return new Promise(function(resolve) { setTimeout(resolve, 700); }); })
                .then(resync)
                .then(function() { if (typeof saveState === 'function') saveState(); })
                .catch(function() {});
        })
        .catch(function() {});
}

(function renderHistoryPanel() {
    var toggleBtn = document.createElement('button');
    toggleBtn.type = 'button';
    toggleBtn.className = 'history-toggle-btn';
    toggleBtn.title = 'Generation history';
    toggleBtn.textContent = 'History';

    var panel = document.createElement('div');
    panel.id = 'historyPanel';

    var header = document.createElement('div');
    header.className = 'modal-header';
    var title = document.createElement('span');
    title.textContent = 'Generation History';
    var closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'tp3d-modal-close-btn';
    closeBtn.title = 'Close';
    closeBtn.textContent = '✕';
    header.appendChild(title);
    header.appendChild(closeBtn);
    panel.appendChild(header);

    var list = document.createElement('div');
    list.className = 'history-list';
    panel.appendChild(list);

    function fmtTime(ts) {
        try { return new Date(ts * 1000).toLocaleString(); } catch (e) { return ''; }
    }

    function renderEntries(entries) {
        list.innerHTML = '';
        if (!entries || !entries.length) {
            var empty = document.createElement('div');
            empty.className = 'history-empty';
            empty.textContent = 'No generations yet.';
            list.appendChild(empty);
            return;
        }
        entries.forEach(function(entry) {
            var row = document.createElement('div');
            row.className = 'history-entry';

            var top = document.createElement('div');
            top.className = 'history-entry-top';
            row.appendChild(top);

            // entry.render is a real top-down Blender screenshot
            // (export.save_history_thumbnail), only ever added by the server
            // once that generation has actually finished -- which usually
            // happens well after the session that recorded this entry
            // already closed, so it never exists yet on the very same
            // Send. entry.thumbnail (the vector sketch, always present
            // immediately) is the fallback until then.
            var thumbSrc = entry.render
                ? 'http://127.0.0.1:' + PORT + entry.render
                : entry.thumbnail;
            if (thumbSrc) {
                var thumb = document.createElement('img');
                thumb.className = 'history-entry-thumb';
                thumb.src = thumbSrc;
                thumb.alt = '';
                top.appendChild(thumb);
            }

            var text = document.createElement('div');
            text.className = 'history-entry-text';
            var time = document.createElement('div');
            time.className = 'history-entry-time';
            time.textContent = fmtTime(entry.timestamp);
            var summary = document.createElement('div');
            summary.className = 'history-entry-summary';
            summary.textContent = entry.summary || '';
            text.appendChild(time);
            text.appendChild(summary);
            top.appendChild(text);

            var del = document.createElement('button');
            del.type = 'button';
            del.className = 'history-entry-delete';
            del.title = 'Remove from history';
            del.textContent = '✕';
            del.addEventListener('click', function(e) {
                e.stopPropagation();
                fetch('http://127.0.0.1:' + PORT + '/delete_history_entry', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id: entry.id })
                })
                .then(function(r) { return r.json(); })
                .then(renderEntries)
                .catch(function() {});
            });
            top.appendChild(del);

            // Below the thumbnail: which element source (OSM/ESA) was active
            // and tiny icons for every element category that was enabled.
            // Missing on entries saved before this existed -- just omitted.
            if (entry.elementSource || (entry.enabledElements && entry.enabledElements.length)) {
                var elemsRow = document.createElement('div');
                elemsRow.className = 'history-entry-elements';

                if (entry.elementSource) {
                    var src = document.createElement('span');
                    src.className = 'history-entry-source';
                    src.textContent = entry.elementSource === 'WORLDCOVER' ? 'ESA' : 'OSM';
                    src.title = entry.elementSource === 'WORLDCOVER' ? 'ESA WorldCover' : 'OpenStreetMap';
                    elemsRow.appendChild(src);
                }
                (entry.enabledElements || []).forEach(function(key) {
                    var svg = typeof ELEMENT_ICONS !== 'undefined' ? ELEMENT_ICONS[key] : null;
                    if (!svg) return;
                    var icon = document.createElement('span');
                    icon.className = 'history-entry-elem-icon';
                    icon.title = TP3D_ELEMENT_LABELS[key] || key;
                    icon.innerHTML = svg;
                    elemsRow.appendChild(icon);
                });
                row.appendChild(elemsRow);
            }

            row.addEventListener('click', function() {
                if (typeof window.tp3dApplyHistoryEntry === 'function' && entry.settings) {
                    window.tp3dApplyHistoryEntry(entry.settings);
                }
                tp3dApplyHistorySettings(entry);
                closePanel();
            });

            list.appendChild(row);
        });
    }

    function loadEntries() {
        fetch('http://127.0.0.1:' + PORT + '/get_history', { cache: 'no-store' })
            .then(function(r) { return r.json(); })
            .then(renderEntries)
            .catch(function() { renderEntries([]); });
    }

    function openPanel() {
        panel.classList.add('open');
        toggleBtn.classList.add('active');
        loadEntries();
    }
    function closePanel() {
        panel.classList.remove('open');
        toggleBtn.classList.remove('active');
    }

    toggleBtn.addEventListener('click', function() {
        if (panel.classList.contains('open')) closePanel(); else openPanel();
    });
    closeBtn.addEventListener('click', closePanel);

    document.body.appendChild(toggleBtn);
    document.body.appendChild(panel);

    // Never blocks the actual Send-to-Blender request chained after it (see
    // each page's onclick) -- resolves to null on any failure instead of
    // rejecting, same as the old fire-and-forget behavior, just with a
    // value now: the saved entry's id, so the page can thread it into its
    // own /confirm payload as history_id. Blender's own generation operator
    // reads that back out of the payload once generation actually finishes,
    // to render the real thumbnail (export.save_history_thumbnail) under
    // that same id -- see _with_render_urls in picker_server.py and
    // entry.render's handling in renderEntries above.
    window.tp3dPushHistory = function(settings, summary, thumbnail) {
        // /get_source_state is the same snapshot the OSM/ESA WorldCover
        // switch re-syncs from (settings_modal.js's
        // tp3dBuildElementSourceSwitch) -- refreshed every ~0.5s by
        // Blender's own modal timer (picker_server.refresh_state_snapshots),
        // so it's a more reliable source for "what's actually live right
        // now" than the page's own ELEMENT_SOURCE/TP3D_ELEMENT_STATE globals,
        // which a Settings-modal field edit doesn't always keep in sync
        // (see settings_modal.js's MAP_TAB_CONTROL_VALUES comment). Falls
        // back to those globals if the fetch fails, same values either way
        // for the badge row, just possibly a request-cycle stale.
        return fetch('http://127.0.0.1:' + PORT + '/get_source_state', { cache: 'no-store' })
            .then(function(r) { return r.json(); })
            .catch(function() { return null; })
            .then(function(s) {
                var elementSource = s ? s.elementSource
                    : (typeof ELEMENT_SOURCE !== 'undefined' ? ELEMENT_SOURCE : null);
                var elementStates = s ? s.elementStates
                    : (typeof TP3D_ELEMENT_STATE !== 'undefined' ? TP3D_ELEMENT_STATE : null);
                var enabledElements = [];
                if (typeof ELEMENT_STATUS_ORDER !== 'undefined' && elementStates) {
                    enabledElements = ELEMENT_STATUS_ORDER
                        .filter(function(entry) { return !!elementStates[entry[0]]; })
                        .map(function(entry) { return entry[0]; });
                }
                return fetch('http://127.0.0.1:' + PORT + '/save_history_entry', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        settings: settings, summary: summary || '', thumbnail: thumbnail || null,
                        elementSource: elementSource, enabledElements: enabledElements,
                        elementStates: elementStates,
                        settingsState: s ? s.settingsState : null,
                        advancedSettings: s ? s.advancedSettings : null
                    })
                });
            })
            .then(function(r) { return r.json(); })
            .then(function(resp) { return resp && resp.id; })
            .catch(function() { return null; });
    };
})();
