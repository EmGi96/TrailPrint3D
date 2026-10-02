// "Prefetch Elements" for the picker pages (map_generator.html, puzzleGenerator.html and
// premium/{map_generator_pe,puzzleGenerator_pe,slidingPuzzleGenerator,multitile_generator}.html),
// inlined via __PREFETCH_JS__ in picker_server.py.
//
// Clicking #prefetchBtn asks Blender (POST /prefetch) to fetch -- and disk-cache,
// exactly like a real generation would -- every currently enabled OSM element for
// the drawn area, then polls GET /prefetch_status and draws the result on the map.
// Every drawn feature is clickable: a disabled one is greyed out and its OSM key
// (e.g. "way/123") is sent with /confirm as excluded_ids, so the generator leaves
// it out (see utils/osm/exclusions.py). The on-disk cache is never touched.
// Holding Shift or Alt and dragging a box over the map bulk-toggles every
// currently visible prefetched element the box overlaps (see "Shift/Alt+drag
// region toggle"). A plain Alt+click (no drag) on a road keeps its existing,
// separate meaning instead -- toggle that road and its connected run.
//
// Requires (all defined earlier on the page): PORT, map, coords, currentShape,
// and the #prefetchInfo / #clearPrefetch sidebar elements. effectiveBounds is optional.
// A page whose fetch area isn't simply its drawn shape defines
// tp3dPrefetchArea() -> { bounds: {north,south,east,west}, type } | null, which
// takes precedence (type 'exact' = use the bounds as-is, no shape/rotation padding).
var PREFETCH_KIND_STYLE = {
    WATER:      { label: 'Water',      color: '#2f7fd1' },
    FOREST:     { label: 'Forest',     color: '#2e8b3d' },
    SCREE:      { label: 'Scree',      color: '#a08d78' },
    CITY:       { label: 'City',       color: '#b56cc9' },
    GREENSPACE: { label: 'Greenspace', color: '#7bc96f' },
    FARMLAND:   { label: 'Farmland',   color: '#d8b84a' },
    GLACIER:    { label: 'Glacier',    color: '#7fd6e8' },
    BUILDINGS:  { label: 'Buildings',  color: '#e07b39' },
    STREETS:    { label: 'Roads',      color: '#d81b3c' },
    COASTLINE:  { label: 'Coastline',  color: '#1e5aa8' }
};
var PREFETCH_DISABLED_COLOR = '#e04040';
// How far off dead-straight (in degrees) a road at a junction may still be to
// count as "continuing the same direction" for Alt+click's follow-the-road
// (see prefetchFollowRoad below).
var PREFETCH_FOLLOW_ANGLE_TOLERANCE_DEG = 10;

var prefetchFeatures = {};       // id -> { layer, kind, sub, line }
var prefetchExcluded = {};       // id -> true, survives re-prefetching so choices aren't lost
var prefetchKindCounts = {};
var prefetchKindsHidden = {};    // legend group key ("STREETS" or "STREETS/minor") -> true while hidden from the map
var prefetchLonShift = 0;
var prefetchPollTimer = null;
var prefetchWanted = false;      // a prefetch is (or should be) showing -- what gets persisted as "active"

// Above overlayPane (400) so a drawn rectangle's own fill can't swallow the clicks,
// below markerPane (600) so leaflet-draw's edit handles stay draggable.
map.createPane('prefetchPane');
map.getPane('prefetchPane').style.zIndex = 450;
var prefetchRenderer = L.canvas({ pane: 'prefetchPane', padding: 0.5, tolerance: 6 });
var prefetchLayer = L.layerGroup().addTo(map);

// Above prefetchPane so the Shift+drag selection box's outline is never
// hidden under a filled prefetch polygon; pointerEvents 'none' so the box
// itself never swallows hover/click on the features underneath it (the
// selection is computed from the drag's start/end latlng, not from clicks
// on the box).
map.createPane('prefetchSelectPane');
map.getPane('prefetchSelectPane').style.zIndex = 455;
map.getPane('prefetchSelectPane').style.pointerEvents = 'none';
var prefetchTip = document.createElement('div');
prefetchTip.id = 'prefetchTip';
map.getContainer().appendChild(prefetchTip);

// The Prefetch/Reset buttons live at the right end of the element status bar.
// tp3dRenderElementStatus only removes .element-chip-wrap nodes, so they survive a
// re-render (same reason the Settings gear does).
(function buildPrefetchBar() {
    var bar = document.createElement('div');
    bar.className = 'prefetch-bar';
    bar.innerHTML =
        '<button type="button" id="prefetchBtn" class="prefetch-bar-btn" title="Fetch and cache the enabled map elements for the drawn area, then show them on the map. Click an element to leave it out.">Prefetch Elements</button>'
        + '<button type="button" id="resetPrefetch" class="prefetch-bar-btn" title="Enable every disabled element again" style="display:none">Reset</button>';
    document.getElementById('elementStatus').appendChild(bar);
})();

// Prefetch only understands OSM data: grey the button out under ESA WorldCover.
// Called from tp3dRenderElementStatus, i.e. on load and after every source switch.
var PREFETCH_BTN_TITLE = document.getElementById('prefetchBtn').title;   // (assigned here, so only valid after the bar exists)
function prefetchSyncSource() {
    var btn = document.getElementById('prefetchBtn');
    if (!btn) return;   // hoisted: element_status.js can call this before the bar is built
    var wc = typeof tp3dIsWorldCover === 'function' && tp3dIsWorldCover();
    btn.disabled = wc;
    btn.classList.toggle('source-disabled', wc);
    btn.title = wc ? 'Prefetch only works with OpenStreetMap selected as the element source.' : PREFETCH_BTN_TITLE;
}
prefetchSyncSource();

function tp3dPrefetchExcludedIds() {
    return Object.keys(prefetchExcluded);
}

// An imported GeoJSON boundary replaces the drawn area (coords is null then), and
// the generator builds its tile from the polygon's own bounding box -- so that
// box is what gets prefetched, sent as type 'geojson' (no shape/rotation padding).
function prefetchBounds() {
    var eb, type = currentShape;
    if (typeof tp3dPrefetchArea === 'function') {
        var area = tp3dPrefetchArea();
        if (!area) return null;
        eb = area.bounds;
        type = area.type;
    } else if (typeof geojsonPaths !== 'undefined' && geojsonPaths.length && geojsonLayer.getBounds().isValid()) {
        var gb = geojsonLayer.getBounds();
        eb = { north: gb.getNorth(), south: gb.getSouth(), east: gb.getEast(), west: gb.getWest() };
        type = 'geojson';
    } else if (coords) {
        eb = typeof effectiveBounds === 'function' ? effectiveBounds(coords) : coords;
    } else {
        return null;
    }
    var centerLngRaw = (eb.east + eb.west) / 2;
    var shift = (((centerLngRaw + 180) % 360 + 360) % 360 - 180) - centerLngRaw; // wrap into -180..180
    return {
        bounds: { north: eb.north, south: eb.south, east: eb.east + shift, west: eb.west + shift },
        shift: shift,
        type: type
    };
}

// Sub-categories stay in their kind's colour family (Roads = reds/crimsons/corals)
// so they still read as "the same element", but each one is distinguishable.
var PREFETCH_SUB_COLORS = {
    STREETS: {
        highways: '#ff2a2a', major: '#d81b3c', minor: '#ff6b6b', residential: '#c62828',
        service: '#a11d33', footway: '#ff9e9e', pedestrian: '#ff8c69', cycle_bridle: '#ff7a5c', track: '#8f2a2a', path: '#ff8a80'
    }
};

function prefetchGroupColor(kind, sub) {
    var subColors = PREFETCH_SUB_COLORS[kind];
    if (sub && subColors && subColors[sub]) return subColors[sub];
    return PREFETCH_KIND_STYLE[kind] ? PREFETCH_KIND_STYLE[kind].color : '#ffffff';
}

function prefetchFeatureStyle(id, kind, isLine, sub) {
    var color = prefetchGroupColor(kind, sub);
    if (prefetchExcluded[id]) {
        return { renderer: prefetchRenderer, color: PREFETCH_DISABLED_COLOR, weight: isLine ? 3 : 1,
                 opacity: 0.85, dashArray: '3 4', fill: !isLine, fillColor: PREFETCH_DISABLED_COLOR,
                 fillOpacity: 0.06 };
    }
    // dashArray: null -- Leaflet's setStyle merges options, so without this a
    // re-enabled element would keep the disabled style's dashes.
    return { renderer: prefetchRenderer, color: color, weight: isLine ? 4 : 1, opacity: isLine ? 1 : 0.9, dashArray: null,
             fill: !isLine, fillColor: color, fillOpacity: 0.35 };
}

function prefetchRestyle(id) {
    var f = prefetchFeatures[id];
    if (f) f.layer.setStyle(prefetchFeatureStyle(id, f.kind, f.line, f.sub));
}

function prefetchToggle(id) {
    if (prefetchExcluded[id]) delete prefetchExcluded[id]; else prefetchExcluded[id] = true;
    prefetchRestyle(id);
    renderPrefetchInfo();
    prefetchPersist();
}

// Legend groups: a whole kind ("STREETS") or one of its sub-categories
// ("STREETS/minor"). Both can be hidden from the map and switched off for
// generation independently -- a sub-category is just a subset of its kind.
var PREFETCH_SUB_LABELS = {
    STREETS: {
        highways: 'Highways', major: 'Major Roads', minor: 'Minor Roads', residential: 'Residential Roads',
        service: 'Service Roads', footway: 'Footways/Sidewalks', pedestrian: 'Pedestrian Streets', cycle_bridle: 'Cycle/Bridle Paths',
        track: 'Tracks', path: 'Trails/Paths'
    }
};

function prefetchGroupKey(kind, sub) { return sub ? kind + '/' + sub : kind; }

function prefetchGroupIds(kind, sub) {
    return Object.keys(prefetchFeatures).filter(function(id) {
        var f = prefetchFeatures[id];
        return f.kind === kind && (!sub || f.sub === sub);
    });
}

function prefetchFeatureVisible(f) {
    return !prefetchKindsHidden[f.kind] && !(f.sub && prefetchKindsHidden[prefetchGroupKey(f.kind, f.sub)]);
}

function prefetchApplyVisibility(f) {
    var shown = prefetchLayer.hasLayer(f.layer);
    var want = prefetchFeatureVisible(f);
    if (want && !shown) prefetchLayer.addLayer(f.layer);
    else if (!want && shown) prefetchLayer.removeLayer(f.layer);
}

// Smallest angle (0-180) between two compass bearings, ignoring direction of wrap.
function prefetchAngleDiff(a, b) {
    var d = Math.abs(a - b) % 360;
    return d > 180 ? 360 - d : d;
}

// Alt+click on a road: switch it, then keep following the road in both
// directions. At each end:
//  - Exactly one other same-tier road there -> always continue onto it (an
//    unambiguous connection, same as before this feature existed).
//  - More than one (a real junction) -> continue onto whichever of them keep
//    heading roughly the same direction (within
//    PREFETCH_FOLLOW_ANGLE_TOLERANCE_DEG of dead straight, using the
//    "headings" the server computed alongside "links") instead of stopping
//    outright -- more than one can qualify (e.g. a fork), and each is
//    followed onward independently. A candidate whose direction can't be
//    determined (missing coordinate, edge of the fetched data) is treated
//    like it didn't qualify.
// Either way, stops at a dead end, at a road already in the target state
// (e.g. already disabled), and where the next road only touches mid-way (a
// T-junction) or loops back onto itself. Everything ends up in the same
// state as the clicked road's new state: Alt+click on a disabled road
// re-enables the whole run.
function prefetchFollowRoad(startId) {
    var disable = !prefetchExcluded[startId];
    var setState = function(id) {
        if (disable) prefetchExcluded[id] = true; else delete prefetchExcluded[id];
        prefetchRestyle(id);
    };
    var visited = {};
    visited[startId] = true;
    setState(startId);
    var changed = 1;

    // Which end of `next` (0=start, 1=end) touches `cur`, or null if it only
    // touches mid-way (a T-junction) or touches both (a loop) -- shared by
    // both the single-link and junction branches below.
    function touchingEnd(cur, next) {
        var atStart = next.links[0].indexOf(cur) >= 0, atEnd = next.links[1].indexOf(cur) >= 0;
        return atStart === atEnd ? null : (atStart ? 0 : 1);
    }

    function tryAdvance(cur, nextId) {
        var next = prefetchFeatures[nextId];
        if (!next || !next.links || visited[nextId]) return null;  // not drawn (over the cap) / already done
        var touchEnd = touchingEnd(cur, next);
        if (touchEnd === null) return null;
        if (!!prefetchExcluded[nextId] === disable) return null;   // already in the target state: stop here
        visited[nextId] = true;
        setState(nextId);
        changed++;
        return touchEnd;
    }

    // Stack of { id, end } "continue following from this road, exiting via
    // this end" tasks -- a plain array/loop rather than the two-direction
    // for-loops this replaced, since a junction can now fan out onto more
    // than one continuation and each needs to be walked independently.
    var stack = [{ id: startId, end: 0 }, { id: startId, end: 1 }];
    var guard = 0;
    while (stack.length && guard++ < 5000) {
        var task = stack.pop();
        var cur = task.id, end = task.end;
        var curFeat = prefetchFeatures[cur];
        var links = curFeat.links && curFeat.links[end];
        if (!links || !links.length) continue;                     // dead end

        if (links.length === 1) {
            var touchEnd = tryAdvance(cur, links[0]);
            if (touchEnd !== null) stack.push({ id: links[0], end: touchEnd === 0 ? 1 : 0 });
            continue;
        }

        // A real junction -- only keep going onto same-tier roads whose own
        // direction, leaving this node, is within tolerance of the direction
        // we're arriving on (the reverse of curFeat's own outward heading at
        // `end`, since that heading points back into curFeat, away from the
        // node).
        var arriveHeading = curFeat.headings && curFeat.headings[end] != null
            ? (curFeat.headings[end] + 180) % 360 : null;
        if (arriveHeading == null) continue;  // can't tell direction here -- stop, like a plain dead end
        links.forEach(function(nextId) {
            var next = prefetchFeatures[nextId];
            if (!next || !next.headings) return;
            var touchEnd = next.links ? touchingEnd(cur, next) : null;
            if (touchEnd === null) return;
            var outHeading = next.headings[touchEnd];
            if (outHeading == null || prefetchAngleDiff(arriveHeading, outHeading) > PREFETCH_FOLLOW_ANGLE_TOLERANCE_DEG) return;
            var landedEnd = tryAdvance(cur, nextId);
            if (landedEnd !== null) stack.push({ id: nextId, end: landedEnd === 0 ? 1 : 0 });
        });
    }

    renderPrefetchInfo();
    prefetchPersist();
    document.getElementById('status').textContent =
        (disable ? 'Disabled ' : 'Enabled ') + changed + ' connected road segment' + (changed === 1 ? '' : 's') + '.';
    return changed;
}

function prefetchSetGroupHidden(kind, sub, hidden) {
    var key = prefetchGroupKey(kind, sub);
    if (hidden) prefetchKindsHidden[key] = true; else delete prefetchKindsHidden[key];
    prefetchGroupIds(kind, sub).forEach(function(id) { prefetchApplyVisibility(prefetchFeatures[id]); });
    renderPrefetchInfo();
}

function prefetchLegendRow(kind, sub, label, color, indent) {
    var ids = prefetchGroupIds(kind, sub);
    var off = ids.filter(function(id) { return prefetchExcluded[id]; }).length;
    var key = prefetchGroupKey(kind, sub);
    // A sub-row dims when its own group OR its whole kind is hidden.
    var hidden = !!prefetchKindsHidden[key] || (!!sub && !!prefetchKindsHidden[kind]);
    return '<div class="prefetch-row' + (indent ? ' prefetch-subrow' : '') + '" style="opacity:' + (hidden ? 0.45 : 1) + '">'
        + '<span class="prefetch-kind" data-kind="' + kind + '" data-sub="' + (sub || '') + '" title="Show/hide on map">'
        + '<span class="prefetch-swatch" style="background:' + color + '"></span>' + label + '</span>'
        + '<span class="prefetch-count" data-kind="' + kind + '" data-sub="' + (sub || '') + '" title="Toggle all for generation">'
        + (ids.length - off) + '/' + ids.length + '</span></div>';
}

// Legend: one row per kind -- click the swatch/label to hide it from the map,
// click the count to switch every element of it off/on for generation. Kinds
// with sub-categories (Roads) get an indented row per sub-category below.
function renderPrefetchInfo() {
    document.getElementById('resetPrefetch').style.display = Object.keys(prefetchExcluded).length ? '' : 'none';
    var info = document.getElementById('prefetchInfo');
    var kinds = Object.keys(prefetchKindCounts);
    if (!kinds.length) { info.style.display = 'none'; info.innerHTML = ''; return; }
    var html = '';
    kinds.forEach(function(kind) {
        var st = PREFETCH_KIND_STYLE[kind] || { label: kind, color: '#fff' };
        html += prefetchLegendRow(kind, '', st.label, st.color, false);
        var labels = PREFETCH_SUB_LABELS[kind];
        if (!labels) return;
        Object.keys(labels).forEach(function(sub) {
            if (prefetchGroupIds(kind, sub).length) html += prefetchLegendRow(kind, sub, labels[sub], prefetchGroupColor(kind, sub), true);
        });
    });
    info.innerHTML = html;
    info.style.display = 'block';
}

document.getElementById('prefetchInfo').addEventListener('click', function(e) {
    var count = e.target.closest('.prefetch-count');
    var label = e.target.closest('.prefetch-kind');
    if (count) {
        var ids = prefetchGroupIds(count.getAttribute('data-kind'), count.getAttribute('data-sub'));
        var anyOn = ids.some(function(id) { return !prefetchExcluded[id]; });
        ids.forEach(function(id) {
            if (anyOn) prefetchExcluded[id] = true; else delete prefetchExcluded[id];
            prefetchRestyle(id);
        });
        renderPrefetchInfo();
        prefetchPersist();
    } else if (label) {
        var kind = label.getAttribute('data-kind'), sub = label.getAttribute('data-sub');
        prefetchSetGroupHidden(kind, sub, !prefetchKindsHidden[prefetchGroupKey(kind, sub)]);
    }
});

function clearPrefetch() {
    prefetchLayer.clearLayers();
    prefetchFeatures = {};
    prefetchKindCounts = {};
    prefetchKindsHidden = {};
    renderPrefetchInfo();
    document.getElementById('clearPrefetch').style.display = 'none';
}

function prefetchShift(ring) {
    var s = prefetchLonShift;
    return s ? ring.map(function(p) { return [p[0], p[1] + s]; }) : ring;
}

function prefetchEscape(text) {
    return String(text).replace(/[&<>"']/g, function(c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
}

// Fixed hover info (bottom centre of the map). OSM names are arbitrary user
// text and this is set as HTML, hence the escaping.
function prefetchShowTip(feat) {
    var st = PREFETCH_KIND_STYLE[feat.kind] || { label: feat.kind };
    var off = !!prefetchExcluded[feat.id];
    prefetchTip.innerHTML = st.label + (feat.name ? ': ' + prefetchEscape(feat.name) : '')
        + (off ? ' (disabled)' : '')
        + '<br><small>' + feat.id + ' — click to ' + (off ? 'enable' : 'disable')
        + (feat.links ? ', Alt+click to ' + (off ? 'enable' : 'disable') + ' the connected road' : '') + '</small>';
    prefetchTip.style.display = 'block';
}

function drawPrefetchResult(result) {
    clearPrefetch();
    var features = result.features || [];
    features.forEach(function(feat) {
        var rings = feat.rings.map(prefetchShift);
        var style = prefetchFeatureStyle(feat.id, feat.kind, feat.line, feat.sub);
        var layer = feat.line
            ? L.polyline(rings.length === 1 ? rings[0] : rings, style)
            : L.polygon(rings, style);
        layer.on('click', function(e) {
            L.DomEvent.stopPropagation(e);
            if (e.originalEvent && e.originalEvent.altKey && feat.links) prefetchFollowRoad(feat.id);
            else prefetchToggle(feat.id);
            prefetchShowTip(feat);
        });
        layer.on('mouseover', function() { prefetchShowTip(feat); });
        layer.on('mouseout', function() { prefetchTip.style.display = 'none'; });
        prefetchLayer.addLayer(layer);
        prefetchFeatures[feat.id] = {
            layer: layer, kind: feat.kind, sub: feat.sub || '', line: feat.line,
            links: feat.links || null, headings: feat.headings || null
        };
    });
    prefetchKindCounts = result.counts || {};
    renderPrefetchInfo();
    prefetchPersist();
    document.getElementById('clearPrefetch').style.display = features.length ? 'block' : 'none';

    var notes = [];
    var skipped = result.skipped || {};
    Object.keys(skipped).forEach(function(k) {
        notes.push((PREFETCH_KIND_STYLE[k] ? PREFETCH_KIND_STYLE[k].label : k) + ' skipped (' + skipped[k] + ')');
    });
    var skippedLabels = Object.keys(skipped).map(function(k) { return PREFETCH_KIND_STYLE[k] ? PREFETCH_KIND_STYLE[k].label : k; });
    if (skippedLabels.length) prefetchShowToast('The area is too big to generate these elements: ' + skippedLabels.join(', '));
    if (result.truncated) notes.push('too many elements — only the first ' + features.length + ' are clickable');
    document.getElementById('status').textContent = features.length
        ? 'Prefetched ' + features.length + ' elements — click one to disable it.' + (notes.length ? ' ' + notes.join('; ') + '.' : '')
        : 'Nothing found for the enabled elements.' + (notes.length ? ' ' + notes.join('; ') + '.' : '');
}

function setPrefetchBusy(busy, label) {
    var btn = document.getElementById('prefetchBtn');
    btn.disabled = busy || btn.classList.contains('source-disabled');
    btn.textContent = label || 'Prefetch Elements';
    if (busy) {
        var spinner = document.createElement('span');
        spinner.className = 'prefetch-spinner';
        btn.insertBefore(spinner, btn.firstChild);
    }
}

function pollPrefetch(startedAt) {
    fetch('http://127.0.0.1:' + PORT + '/prefetch_status', { cache: 'no-store' })
        .then(function(r) { return r.json(); })
        .then(function(job) {
            if (job.status === 'done') {
                setPrefetchBusy(false);
                drawPrefetchResult(job.result || {});
            } else if (job.status === 'error') {
                setPrefetchBusy(false);
                document.getElementById('status').textContent = job.message || 'Prefetch failed.';
                prefetchShowToast(job.message || 'Prefetch failed.');
            } else if (Date.now() - startedAt > 15 * 60 * 1000) {
                setPrefetchBusy(false);
                document.getElementById('status').textContent = 'Prefetch timed out.';
            } else {
                setPrefetchBusy(true, job.message || 'Fetching…');
                prefetchPollTimer = setTimeout(function() { pollPrefetch(startedAt); }, 700);
            }
        })
        .catch(function() {
            setPrefetchBusy(false);
            document.getElementById('status').textContent = 'Lost connection to Blender.';
        });
}

function startPrefetch() {
    var pb = prefetchBounds();
    if (!pb) {
        document.getElementById('status').textContent = 'Draw an area or import a GeoJSON boundary first, then prefetch its elements.';
        return;
    }
    prefetchWanted = true;
    prefetchLonShift = -pb.shift;
    setPrefetchBusy(true, 'Fetching…');
    fetch('http://127.0.0.1:' + PORT + '/prefetch', {
        method: 'POST',
        mode: 'cors',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bounds: pb.bounds, type: pb.type })
    })
    .then(function() { pollPrefetch(Date.now()); })
    .catch(function() {
        setPrefetchBusy(false);
        document.getElementById('status').textContent = 'Could not connect to Blender.';
    });
}

// Small self-dismissing popup over the map (bottom centre, like #prefetchTip).
var prefetchToast = document.createElement('div');
prefetchToast.id = 'prefetchToast';
map.getContainer().appendChild(prefetchToast);
var prefetchToastTimer = null;

function prefetchShowToast(text) {
    prefetchToast.textContent = text;
    prefetchToast.classList.add('show');
    clearTimeout(prefetchToastTimer);
    prefetchToastTimer = setTimeout(function() { prefetchToast.classList.remove('show'); }, 3500);
}

document.getElementById('prefetchBtn').addEventListener('click', function() {
    var anyEnabled = typeof TP3D_ELEMENT_STATE === 'undefined'
        || Object.keys(TP3D_ELEMENT_STATE).some(function(k) { return TP3D_ELEMENT_STATE[k]; });
    if (!anyEnabled) {
        prefetchShowToast('No elements are enabled — enable at least one element to prefetch.');
        return;
    }
    startPrefetch();
});
document.getElementById('clearPrefetch').addEventListener('click', function() {
    prefetchExcluded = {};   // an explicit Clear also forgets what was disabled
    prefetchWanted = false;
    clearPrefetch();
    prefetchPersist();
});

// Reset: switch every disabled element back on.
function prefetchReset() {
    var ids = Object.keys(prefetchExcluded);
    prefetchExcluded = {};
    ids.forEach(prefetchRestyle);
    renderPrefetchInfo();
    prefetchPersist();
}

document.getElementById('resetPrefetch').addEventListener('click', prefetchReset);

// ---- Shift/Alt+drag region toggle ---------------------------------------------
// Hold Shift OR Alt and drag a box over the map to bulk-toggle every
// currently VISIBLE prefetched element (i.e. not hidden via the legend --
// see prefetchFeatureVisible) that box overlaps. Uses the same "anything in
// the set currently on? turn the whole set off, else turn it all on" rule as
// the legend's own per-kind count toggle (renderPrefetchInfo's click handler
// above), so a drag over a mixed on/off area gives one predictable result
// instead of leaving it half-toggled.
//
// Alt is also already overloaded for a PLAIN CLICK (no drag) on a road --
// that's prefetchFollowRoad, wired up in drawPrefetchResult's own 'click'
// handler on each feature layer, and is left completely alone here. The two
// don't conflict: a click with zero drag distance leaves prefetchSelectBox
// at zero size and this code bails out below without touching anything,
// while the feature layer's own 'click' event still fires independently and
// runs prefetchFollowRoad as before. Only an actual drag reaches the
// region-toggle path.
//
// Leaflet's own default map options include a "boxZoom" handler that also
// listens for Shift+drag and zooms the map into the drawn box on release --
// it was firing alongside this feature (same gesture), so it's disabled
// here in favor of our own Shift+drag meaning.
map.boxZoom.disable();
var prefetchSelectBox = null;        // the temporary rubber-band L.Rectangle while dragging
var prefetchSelectStart = null;      // LatLng where the drag started
var prefetchShiftHeld = false;
var prefetchAltHeld = false;
var prefetchRegionModeActive = false; // mirrors (prefetchShiftHeld || prefetchAltHeld); tracked separately so re-applying it is a no-op once already set

function prefetchOnSelectMouseMove(e) {
    if (!prefetchSelectBox) return;
    prefetchSelectBox.setBounds(L.latLngBounds(prefetchSelectStart, e.latlng));
}

function prefetchOnSelectMouseUp(e) {
    map.off('mousemove', prefetchOnSelectMouseMove);
    map.off('mouseup', prefetchOnSelectMouseUp);
    if (!prefetchSelectBox) return;
    var bounds = prefetchSelectBox.getBounds();
    map.removeLayer(prefetchSelectBox);
    prefetchSelectBox = null;
    prefetchSelectStart = null;
    // A plain click without dragging (zero-size box) toggles nothing -- a
    // single feature is already reachable with a normal click.
    if (bounds.getNorth() === bounds.getSouth() && bounds.getEast() === bounds.getWest()) return;
    var ids = Object.keys(prefetchFeatures).filter(function(id) {
        var f = prefetchFeatures[id];
        return prefetchFeatureVisible(f) && bounds.intersects(f.layer.getBounds());
    });
    if (!ids.length) return;
    var anyOn = ids.some(function(id) { return !prefetchExcluded[id]; });
    ids.forEach(function(id) {
        if (anyOn) prefetchExcluded[id] = true; else delete prefetchExcluded[id];
        prefetchRestyle(id);
    });
    renderPrefetchInfo();
    prefetchPersist();
    document.getElementById('status').textContent =
        (anyOn ? 'Disabled ' : 'Enabled ') + ids.length + ' element' + (ids.length === 1 ? '' : 's') + ' in the selected area.';
}

function prefetchOnSelectMouseDown(e) {
    if (!prefetchShiftHeld && !prefetchAltHeld) return;
    // Don't fight leaflet-draw's own rectangle tool (or this page's own
    // center-mode draw handler) while either is still armed waiting for the
    // user to place the generation-area shape.
    if (typeof drawArmed !== 'undefined' && drawArmed) return;
    L.DomEvent.preventDefault(e.originalEvent);
    prefetchSelectStart = e.latlng;
    prefetchSelectBox = L.rectangle([e.latlng, e.latlng], {
        pane: 'prefetchSelectPane', color: '#ffffff', weight: 1.5, opacity: 0.9,
        fill: true, fillOpacity: 0.08, dashArray: '4 4'
    }).addTo(map);
    map.on('mousemove', prefetchOnSelectMouseMove);
    map.on('mouseup', prefetchOnSelectMouseUp);
}
map.on('mousedown', prefetchOnSelectMouseDown);

// Applies the combined (Shift OR Alt) held state to map dragging/cursor.
// Called after either key's state changes; a no-op once the combined state
// already matches, so releasing Shift while Alt is still held (or vice
// versa) correctly leaves region-select mode active.
function prefetchApplyRegionModeHeld() {
    var held = prefetchShiftHeld || prefetchAltHeld;
    if (held === prefetchRegionModeActive) return;
    prefetchRegionModeActive = held;
    if (held) {
        map.dragging.disable();
        map.getContainer().style.cursor = 'crosshair';
    } else if (!(typeof drawArmed !== 'undefined' && drawArmed)) {
        // Don't clobber another tool's own dragging/cursor state (e.g.
        // center-mode draw already disables dragging itself) -- only restore
        // the normal pan/cursor if nothing else currently has the map armed.
        map.dragging.enable();
        map.getContainer().style.cursor = '';
    }
}

document.addEventListener('keydown', function(e) {
    if (e.key !== 'Shift' && e.key !== 'Alt') return;
    // Ignore while a text field has focus (e.g. Shift+Home to select text in
    // the resolution/object-size inputs, or Alt+Tab-adjacent focus chrome)
    // so it doesn't unexpectedly disable map panning for someone who isn't
    // trying to draw a box.
    var tag = document.activeElement && document.activeElement.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (e.key === 'Shift') prefetchShiftHeld = true; else prefetchAltHeld = true;
    prefetchApplyRegionModeHeld();
});
document.addEventListener('keyup', function(e) {
    if (e.key === 'Shift') prefetchShiftHeld = false;
    else if (e.key === 'Alt') prefetchAltHeld = false;
    else return;
    prefetchApplyRegionModeHeld();
});
// A keyup is missed if the window loses focus while a key is still held
// (e.g. Alt-tab, fittingly) -- drop both held states on blur too, or
// dragging/cursor would otherwise stay stuck until the key is pressed and
// released again.
window.addEventListener('blur', function() {
    prefetchShiftHeld = false;
    prefetchAltHeld = false;
    prefetchApplyRegionModeHeld();
});

// ---- Persistence ------------------------------------------------------------
// The disabled list and "a prefetch is showing" ride along in the page's own
// saved state (saveState/restoreState, written to a temp file by the server), so
// they come back when the picker is reopened. The elements themselves are NOT
// stored: on restore the prefetch simply runs again, and is answered from the
// Overpass disk cache (no network unless that cache expired or is disabled).
function prefetchPersist() {
    if (typeof saveState === 'function') saveState();
}

function tp3dPrefetchState() {
    // saveState can fire (map moveend) before this file's vars are initialised.
    if (typeof prefetchExcluded === 'undefined') return null;
    return { active: prefetchWanted, excluded: Object.keys(prefetchExcluded) };
}

function tp3dRestorePrefetch(saved) {
    if (!saved) return;
    (saved.excluded || []).forEach(function(id) { prefetchExcluded[id] = true; });
    renderPrefetchInfo();
    if (saved.active && prefetchBounds()) startPrefetch();
}
