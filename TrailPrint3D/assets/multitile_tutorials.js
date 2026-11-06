// Tutorial content for the Multi-Tile Generator page
// (premium/generators/multitile_generator.html), built on the engine in
// assets/tutorial.js -- same structure as assets/map_tutorials.js for the
// map generators. Must be inlined *after* the page has created `map` and its
// own Draw/Extend/Send handlers, and requires a #tutorialBtn button in the
// page's own markup.

// Leaflet-draw's default rectangle style while a corner-to-corner draw is in
// progress, vs. the orange the page uses for the finished area.
var TP3D_MT_DRAWING_BLUE = '#3388ff';

// Zermatt and the Matterhorn in the Swiss Alps -- the tutorial's example area.
var TP3D_MT_MATTERHORN_BOUNDS = [[45.94, 7.60], [46.05, 7.80]];

// The grid the tutorial asks for: 2 x 2 hexagons, 5 mm apart.
var TP3D_MT_SEGMENTS = 2;
var TP3D_MT_SPACING = 5;
// Lower than the page's default 8 so the example generates quickly.
var TP3D_MT_RESOLUTION = 6;

function tp3dMtHasDrawnArea() {
    return typeof coords !== 'undefined' && !!coords;
}

function tp3dMtSegmentsValue() {
    return parseInt(document.getElementById('subdivisionSlider').value) || 1;
}
function tp3dMtSpacingValue() {
    return parseFloat(document.getElementById('tileSpacingValue').value) || 0;
}

// The drawn area is split into exactly 2 rows of 2 hexagons. With 2
// segments, buildHexGridSegments only produces 4 tiles for a 2 x 2 layout
// (fewer rows give 2-3, more give 6+), so the count alone is enough.
function tp3dMtIs2x2() {
    return typeof currentShape !== 'undefined' && currentShape === 'hexagon' &&
        tp3dMtSegmentsValue() === TP3D_MT_SEGMENTS && segments.length === 4;
}

function tp3dMtSelectedSlotCount() {
    return typeof selectedSlots !== 'undefined' ? Object.keys(selectedSlots).length : 0;
}

// Screen height a drawn box *widthPx* wide needs for 2 rows of hexagons --
// buildHexGridSegments' own layout, solved for the height. Its (mx, my)
// space is a linear rescale of screen pixels at a fixed zoom, so the ratio
// carries over directly. 2 rows come out for a height of 1..1.5 row steps;
// 1.25 aims at the middle so the demo's box lands safely inside. Uses the
// tutorial's target spacing, not the slider's: the area is drawn before the
// spacing step, and the grid has to come out right once it's set.
function tp3dMtHexBoxHeight(widthPx) {
    var n = TP3D_MT_SEGMENTS;
    var objSize = parseFloat(document.getElementById('objSizeInput').value) || OBJ_SIZE;
    var spacingRatio = objSize > 0 ? 2 * TP3D_MT_SPACING / objSize : 0;
    var R = 2 * widthPx / ((3 * n - 1) + 2 * spacingRatio * (n - 1));
    return 1.25 * (Math.sqrt(3) + spacingRatio) * R;
}

// Screen rect of the drawn area's orange box, or null when nothing is drawn.
function tp3dMtDrawnBoxRect() {
    if (!tp3dMtHasDrawnArea()) return null;
    var el = typeof rectLayer !== 'undefined' && rectLayer && rectLayer.getElement && rectLayer.getElement();
    return el && el.getBoundingClientRect ? el.getBoundingClientRect() : null;
}

// Demo for "draw your area": a drag across the middle of the map, shaped
// so the grid comes out as 2 x 2 hexagons once the following steps have set
// the shape, segments and spacing.
function tp3dMtDemoDrawArea() {
    var box = map.getContainer().getBoundingClientRect();
    var cx = box.left + box.width / 2, cy = box.top + box.height / 2;
    var dx = Math.min(140, box.width / 5);
    var dy = Math.min(tp3dMtHexBoxHeight(2 * dx), box.height - 40) / 2;
    return [{ x: cx - dx, y: cy - dy }, { x: cx + dx, y: cy + dy }];
}

// Demo for "2 rows of 2": drags the bottom-right corner up or down to the
// height that gives 2 rows -- nothing once the grid is already right.
function tp3dMtDemoFixRows() {
    if (tp3dMtIs2x2()) return null;
    // Resizing can't help with the wrong segments/shape (see tp3dMtRowHint).
    if (currentShape !== 'hexagon' || tp3dMtSegmentsValue() !== TP3D_MT_SEGMENTS) return null;
    var r = tp3dMtDrawnBoxRect();
    if (!r) return null;
    var targetBottom = r.top + tp3dMtHexBoxHeight(r.width);
    if (Math.abs(targetBottom - r.bottom) < 4) return null;
    return [{ x: r.right, y: r.bottom }, { x: r.right, y: targetBottom }];
}

// Tile count buildHexGridSegments gives for the drawn area with its north
// edge moved to *north* -- the page's own grid code, on a scratch copy of
// coords/segments that are put back afterwards.
function tp3dMtTilesForNorth(north) {
    var savedCoords = coords, savedSegments = segments;
    try {
        coords = { north: north, south: savedCoords.south, east: savedCoords.east, west: savedCoords.west, type: savedCoords.type };
        segments = [];
        buildHexGridSegments();
        return segments.length;
    } finally {
        coords = savedCoords;
        segments = savedSegments;
    }
}

// "Fix it for me" on the 2-rows step: keeps the width and the bottom edge,
// and moves the top edge to the middle of the range of heights that give 4
// tiles. Resized through the rectangle itself, then its own 'edit' handler
// (bindLiveEdit) updates coords, grid and state.
function tp3dMtFixRows() {
    if (!tp3dMtHasDrawnArea() || typeof rectLayer === 'undefined' || !rectLayer) return;
    // 4 tiles only exist for hexagons with 2 segments -- put those back
    // first if they were changed after their own steps (through the page's
    // own controls, so the grid redraws as if the user did it).
    if (currentShape !== 'hexagon') document.querySelector('.shape-btn[data-shape="hexagon"]').click();
    if (tp3dMtSegmentsValue() !== TP3D_MT_SEGMENTS) {
        var slider = document.getElementById('subdivisionSlider');
        slider.value = TP3D_MT_SEGMENTS;
        slider.dispatchEvent(new Event('input', { bubbles: true }));
        slider.dispatchEvent(new Event('change', { bubbles: true }));
    }
    var south = coords.south;
    // Same height units as the page's grid: north/south span in degrees,
    // scanned from very flat to very tall relative to the width.
    var lonSpan = coords.east - coords.west;
    var lo = null, hi = null;
    for (var f = 0.2; f <= 3; f += 0.01) {
        var north = south + lonSpan * f;
        if (tp3dMtTilesForNorth(north) === 4) {
            if (lo === null) lo = north;
            hi = north;
        } else if (lo !== null) {
            break;
        }
    }
    if (lo === null) return;
    var target = (lo + hi) / 2;
    rectLayer.setBounds(L.latLngBounds([south, coords.west], [target, coords.east]));
    if (rectLayer.editing && rectLayer.editing.enabled()) {
        // Moves leaflet-draw's corner handles to the new corners.
        rectLayer.editing.disable();
        rectLayer.editing.enable();
    }
    rectLayer.fire('edit');
}

// Live hint under the 2-rows step's text: how many tiles there are now and
// which way to drag. Refreshed by the step's own timer.
var tp3dMtRowHintTimer = null;
function tp3dMtRowHint() {
    var el = document.getElementById('tp3dMtRowHint');
    if (!el) return;
    var n = typeof segments !== 'undefined' ? segments.length : 0;
    if (typeof currentShape !== 'undefined' && currentShape !== 'hexagon') {
        el.innerHTML = 'The shape isn\'t <b>hexagon</b> anymore — click <b>Fix it for me</b> to switch it back.';
    } else if (tp3dMtSegmentsValue() !== TP3D_MT_SEGMENTS) {
        el.innerHTML = '<b>Segments</b> is set to <b>' + tp3dMtSegmentsValue() + '</b> now, so 2 x 2 can\'t come ' +
            'out — click <b>Fix it for me</b> to set it back to 2.';
    } else if (tp3dMtIs2x2()) {
        el.innerHTML = '<b>4 tiles</b> — that\'s 2 x 2.';
    } else {
        el.innerHTML = 'Now: <b>' + n + ' tiles</b> — drag the corner <b>' +
            (n < 4 ? 'down' : 'up') + '</b> (' + (n < 4 ? 'taller' : 'shorter') + ').';
    }
}

// Slider steps only count once the mouse is let go: a waitUntil alone fires
// the moment a drag passes the target value, moving on while the user is
// still dragging it somewhere else.
var tp3dMtPointerDown = false;
document.addEventListener('pointerdown', function() { tp3dMtPointerDown = true; }, true);
document.addEventListener('pointerup', function() { tp3dMtPointerDown = false; }, true);
document.addEventListener('pointercancel', function() { tp3dMtPointerDown = false; }, true);

function tp3dMtWaitForSlider(predicate) {
    return tp3dTutorial.waitUntil(function() { return !tp3dMtPointerDown && predicate(); });
}

// A slider keeps keyboard focus after its step, and the tutorial's overlay
// only blocks the mouse -- arrow keys would keep changing it. Dropped on
// leaving a slider step.
function tp3dMtBlurSlider() {
    var el = document.activeElement;
    if (el && el.type === 'range') el.blur();
}

// Demo for a slider step: drags the slider's thumb from its current value
// to *value*. The thumb's center runs from half a thumb in from either end.
function tp3dMtDemoSlider(id, value) {
    return function() {
        var el = document.getElementById(id);
        if (!el || parseFloat(el.value) === value) return null;
        var r = el.getBoundingClientRect();
        var min = parseFloat(el.min) || 0, max = parseFloat(el.max) || 100, thumb = 16;
        function x(v) {
            var f = (Math.min(Math.max(v, min), max) - min) / (max - min);
            return r.left + thumb / 2 + f * (r.width - thumb);
        }
        var y = r.top + r.height / 2;
        return [{ x: x(parseFloat(el.value)), y: y }, { x: x(value), y: y }];
    };
}

// Demo for "add a tile": clicks the + slot nearest the map's center.
function tp3dMtDemoClickPlus() {
    var box = map.getContainer().getBoundingClientRect();
    var cx = box.left + box.width / 2, cy = box.top + box.height / 2;
    var best = null, bestDist = Infinity;
    document.querySelectorAll('#map .expand-plus').forEach(function(el) {
        var r = el.getBoundingClientRect();
        if (!r.width) return;
        var p = { x: r.left + r.width / 2, y: r.top + r.height / 2 };
        var d = Math.hypot(p.x - cx, p.y - cy);
        if (d < bestDist) { best = p; bestDist = d; }
    });
    return best ? [best] : null;
}

// Puts the page back to what a first launch of the generator shows, so the
// tutorial always starts from the same known state. Same approach as
// map_tutorials.js's tp3dResetMapGenerator: drives the page's own controls
// (mode / delete / Clear buttons, shape and draw-mode buttons, sliders,
// layer radios) so every side effect their handlers take care of happens
// exactly as if the user had clicked them. Extend-mode selections have no
// control of their own to clear them, so those are dropped directly.
function tp3dResetMultitileGenerator() {
    function clickIfShown(id) {
        var el = document.getElementById(id);
        if (el && el.style.display !== 'none') el.click();
    }
    function setInput(id, value) {
        var el = document.getElementById(id);
        if (!el || String(el.value) === String(value)) return;
        el.value = value;
        el.dispatchEvent(new Event('input', { bubbles: true }));
    }

    if (typeof drawArmed !== 'undefined' && drawArmed) disarmDraw();
    if (mode !== 'draw') document.getElementById('modeDrawBtn').click();
    selectedSlots = {};
    if (tp3dMtHasDrawnArea()) document.getElementById('deleteBtn').click();
    clickIfShown('clearGpx');
    clickIfShown('clearGeojson');
    clickIfShown('clearPrefetch');

    var rectBtn = document.querySelector('.shape-btn[data-shape="rectangle"]');
    if (rectBtn && !rectBtn.classList.contains('active')) rectBtn.click();
    var cornerBtn = document.querySelector('.draw-mode-btn[data-mode="corner"]');
    if (cornerBtn && !cornerBtn.classList.contains('active')) cornerBtn.click();
    var dovetailBtn = document.getElementById('dovetailToggle');
    if (dovetailBtn.classList.contains('active')) dovetailBtn.click();

    // Same defaults the page itself starts with: the static HTML's slider
    // values, and Size from the scene's Object Size (OBJ_SIZE, as on load).
    setInput('subdivisionSlider', document.getElementById('subdivisionSlider').defaultValue || 3);
    setInput('tileSpacing', document.getElementById('tileSpacing').defaultValue || 0);
    setInput('resolutionSlider', document.getElementById('resolutionSlider').defaultValue || 8);
    setInput('objSizeInput', typeof OBJ_SIZE !== 'undefined' ? OBJ_SIZE : 100);

    // Base layer back to OpenStreetMap via the layers control's own radio,
    // so Leaflet's control and the page's activeBaseLayerName stay in sync.
    document.querySelectorAll('.leaflet-control-layers-base label').forEach(function(label) {
        var input = label.querySelector('input');
        if (input && !input.checked && label.textContent.trim() === 'OpenStreetMap') input.click();
    });
}

// ---- "Create a multi-tile map" ----

function tp3dMultitileTutorialCreate() {
    return [
        {
            target: '#map',
            title: 'Create a multi-tile map',
            text: 'A multi-tile map is one big map split into several tiles. Each tile is printed on its own, ' +
                  'and together they show the whole area.<br><br>' +
                  'We moved the map to <b>Zermatt and the Matterhorn</b> in the Swiss Alps.<br><br>' +
                  'You can exit this tutorial at any time with <b>✕</b> or <b>Esc</b>.'
        },
        {
            target: '#drawBtn',
            placement: 'right',
            title: 'Draw your area',
            text: 'Draw your area first, so you can see every setting change on the map right away.<br><br>' +
                  'Click the <b>pencil</b> to start drawing.',
            waitFor: tp3dTutorial.waitForClick('#drawBtn'),
            waitText: 'Click the pencil',
            skipIf: tp3dMtHasDrawnArea
        },
        {
            target: '#map',
            title: 'Draw your area',
            text: 'Click and drag over the Matterhorn to draw your area, then let go of the mouse.<br><br>' +
                  'Draw it <b>about as tall as it is wide</b>.<br><br>' +
                  'If nothing happens, click the pencil again.',
            demo: tp3dMtDemoDrawArea,
            demoShape: 'rect',
            demoGhostColor: TP3D_MT_DRAWING_BLUE,
            waitFor: tp3dTutorial.waitForEvent(map, L.Draw.Event.CREATED),
            waitText: 'Draw on the map',
            skipIf: tp3dMtHasDrawnArea
        },
        {
            target: '.shape-btn[data-shape="hexagon"]',
            placement: 'right',
            title: 'Select the hexagon',
            text: 'Click the <b>hexagon</b> to split your map into hexagon tiles. The blue shapes on the map ' +
                  'are your tiles.',
            waitFor: tp3dTutorial.waitUntil(function() { return currentShape === 'hexagon'; }),
            waitText: 'Click the hexagon',
            skipIf: function() { return currentShape === 'hexagon'; }
        },
        {
            target: function() { return document.getElementById('subdivisionSlider').parentElement; },
            placement: 'right',
            title: 'Set the segments',
            text: '<b>Segments</b> is the number of tiles across your map.<br><br>' +
                  'Drag the slider to <b>2</b>.',
            demo: tp3dMtDemoSlider('subdivisionSlider', TP3D_MT_SEGMENTS),
            waitFor: tp3dMtWaitForSlider(function() { return tp3dMtSegmentsValue() === TP3D_MT_SEGMENTS; }),
            waitText: 'Set Segments to 2',
            skipIf: function() { return tp3dMtSegmentsValue() === TP3D_MT_SEGMENTS; },
            onExit: tp3dMtBlurSlider
        },
        {
            target: function() { return document.getElementById('tileSpacing').parentElement; },
            placement: 'right',
            title: 'Set the tile spacing',
            text: '<b>Tile Spacing</b> leaves a gap between neighboring tiles. The map skips the part in ' +
                  'between, so everything still lines up when you mount the tiles with that gap.<br><br>' +
                  'Set it to <b>5 mm</b>.',
            demo: tp3dMtDemoSlider('tileSpacing', TP3D_MT_SPACING),
            waitFor: tp3dMtWaitForSlider(function() { return tp3dMtSpacingValue() === TP3D_MT_SPACING; }),
            waitText: 'Set Tile Spacing to 5',
            skipIf: function() { return tp3dMtSpacingValue() === TP3D_MT_SPACING; },
            onExit: tp3dMtBlurSlider
        },
        {
            target: function() { return document.getElementById('objSizeInput').closest('.field-row'); },
            interactive: true,
            placement: 'right',
            title: 'Set the tile size',
            text: 'This is the size of <b>each tile</b> in <b>mm</b>, not of the whole map.<br><br>' +
                  'The size of the whole map is shown at the bottom of the sidebar.'
        },
        {
            target: '#map',
            title: 'Get 2 rows of 2 hexagons',
            text: '<b>Segments</b> sets the <b>columns</b>: 2 across. The number of <b>rows</b> depends on how ' +
                  '<b>tall</b> the orange box is.<br><br>' +
                  'Drag its <b>bottom-right corner straight down or up</b> (keep the width the same) until you ' +
                  'see <b>2 rows of 2 hexagons</b>, 4 tiles in total.<br><br>' +
                  '<span id="tp3dMtRowHint"></span>',
            demo: tp3dMtDemoFixRows,
            waitFor: tp3dTutorial.waitUntil(tp3dMtIs2x2),
            waitText: 'Make it 2 x 2',
            skippable: true,
            skipIf: tp3dMtIs2x2,
            buttons: [{ label: 'Fix it for me', onClick: tp3dMtFixRows }],
            onEnter: function() {
                tp3dMtBlurSlider();
                tp3dMtRowHint();
                tp3dMtRowHintTimer = setInterval(tp3dMtRowHint, 200);
            },
            onExit: function() {
                clearInterval(tp3dMtRowHintTimer);
            }
        },
        {
            target: '#totalSizeInfo',
            placement: 'right',
            title: 'Size of the whole map',
            text: 'Here you can see how big the finished map is, with all tiles and the gaps between them.'
        },
        {
            target: '#modeExtendBtn',
            placement: 'right',
            title: 'Extend your map',
            text: 'You can add single tiles next to your map, to grow it in any direction.<br><br>' +
                  'Click <b>Extend</b>.',
            waitFor: tp3dTutorial.waitUntil(function() { return mode === 'extend'; }),
            waitText: 'Click Extend',
            skipIf: function() { return mode === 'extend'; }
        },
        {
            target: '#map',
            title: 'Add a tile',
            text: 'Your 4 tiles are shown in blue. Every dashed hexagon with a <b>+</b> is a free spot ' +
                  'next to them.<br><br>' +
                  'Click a <b>+</b> to add a tile there.',
            demo: tp3dMtDemoClickPlus,
            waitFor: tp3dTutorial.waitUntil(function() { return tp3dMtSelectedSlotCount() > 0; }),
            waitText: 'Click a +',
            skippable: true
        },
        {
            target: '#map',
            interactive: true,
            title: 'Keep growing',
            text: 'The new tile is shown in orange, and new <b>+</b> spots appear around it, so you can keep ' +
                  'adding tiles. Click an orange tile to remove it again.<br><br>' +
                  'Extend also works for maps you already generated in Blender. Their free spots show up here ' +
                  'once nothing is drawn in <b>Draw</b> mode.'
        },
        {
            target: function() { return document.getElementById('resolutionSlider').parentElement; },
            placement: 'right',
            title: 'Set the resolution',
            text: '<b>Resolution</b> sets how much terrain detail each tile gets. Higher values look sharper ' +
                  'but take longer to generate. Lower values generate faster but show less detail.<br><br>' +
                  'Set it to <b>6</b> so this example generates quickly.',
            demo: tp3dMtDemoSlider('resolutionSlider', TP3D_MT_RESOLUTION),
            waitFor: tp3dMtWaitForSlider(function() {
                return parseInt(document.getElementById('resolutionSlider').value) === TP3D_MT_RESOLUTION;
            }),
            waitText: 'Set Resolution to 6',
            skipIf: function() {
                return parseInt(document.getElementById('resolutionSlider').value) === TP3D_MT_RESOLUTION;
            },
            onExit: tp3dMtBlurSlider
        },
        {
            // Whichever is visible: Select Export Folder stands in for Send until a folder is set (export_folder.js).
            target: ['#send', '.export-folder-pick'],
            placement: 'right',
            title: 'Generate your map',
            text: 'Click <b>Send to Blender</b> to generate all tiles.',
            waitFor: tp3dTutorial.waitForClick('#send'),
            waitText: 'Click Send to Blender',
            skippable: true
        }
    ];
}

// ---- Shared sample-file helpers ----

// Fetches a bundled sample file from the server and hands it to an
// <input type="file"> element, same approach as map_tutorials.js.
function tp3dMtSampleFileLoader(fileName, inputId, mimeType, what) {
    return function(button) {
        var label = button.textContent;
        button.disabled = true;
        button.textContent = 'Loading…';
        fetch('http://127.0.0.1:' + PORT + '/get_sample_file?name=' + encodeURIComponent(fileName))
            .then(function(r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.text();
            })
            .then(function(text) {
                var transfer = new DataTransfer();
                transfer.items.add(new File([text], fileName, { type: mimeType }));
                var input = document.getElementById(inputId);
                input.files = transfer.files;
                input.dispatchEvent(new Event('change', { bubbles: true }));
            })
            .catch(function() {
                button.disabled = false;
                button.textContent = label;
                tp3dAlert('Could not load the sample ' + what + '.');
            });
    };
}

var tp3dMtLoadSampleGpx = tp3dMtSampleFileLoader('BetweenLakes.gpx', 'gpxInput', 'application/gpx+xml', 'GPX file');
var tp3dMtLoadSampleGeojson = tp3dMtSampleFileLoader('germany.json', 'geojsonInput', 'application/geo+json', 'GeoJSON file');

// Switches every element off except elevation (through the chips' own toggle,
// so Blender gets the same /toggle_element calls a click sends), whatever the
// element source -- same as map_tutorials.js's tp3dAllElementsOff, which this
// page doesn't load.
function tp3dMtAllElementsOff() {
    if (typeof TP3D_ELEMENT_STATE === 'undefined') return;
    Object.keys(TP3D_ELEMENT_STATE).forEach(function(key) {
        if (key !== 'elevation' && TP3D_ELEMENT_STATE[key]) tp3dToggleElement(key);
    });
}

function tp3dHasMtGpx() { return typeof gpxPaths !== 'undefined' && gpxPaths.length > 0; }
// This page holds a single boundary (geojsonPath), unlike the map generator's list.
function tp3dHasMtGeojson() { return typeof geojsonPath !== 'undefined' && !!geojsonPath; }

// Lake Como / Ceresio area -- the region the sample GPX trail passes through.
var TP3D_MT_LAKE_COMO_BOUNDS = [[45.8, 8.8], [46.1, 9.4]];

// Germany -- starting view for the country-map tutorial.
var TP3D_MT_GERMANY_BOUNDS = [[47.3, 5.9], [55.1, 15.0]];

// ---- "Spread a Trail (GPX file) across multiple tiles" ----

function tp3dMultitileTutorialGpx() {
    return [
        {
            target: '#map',
            title: 'Spread a trail across tiles',
            text: 'A GPX file holds a recorded route. Import it here and the grid automatically ' +
                  'wraps around the whole trail — so your route spreads across as many tiles ' +
                  'as it needs.<br><br>' +
                  'You can exit this tutorial at any time with <b>✕</b> or <b>Esc</b>.'
        },
        {
            target: '#importGpx',
            placement: 'right',
            title: 'Import a GPX file',
            text: 'Click <b>Import GPX</b> and pick your file.<br><br>' +
                  'No file at hand? Use our sample route between the two lakes.',
            buttons: [{ label: 'Use sample GPX file', onClick: tp3dMtLoadSampleGpx }],
            waitFor: tp3dTutorial.waitUntil(tp3dHasMtGpx),
            waitText: 'Import a GPX file'
        },
        {
            target: '#map',
            title: 'Grid placed around the trail',
            text: 'The orange box and the grid were placed automatically around the full extent of the trail.<br><br>' +
                  'The rectangle shape works well for linear routes — each tile covers a different section.'
        },
        {
            target: function() { return document.getElementById('subdivisionSlider').parentElement; },
            placement: 'right',
            title: 'Set the segments',
            text: '<b>Segments</b> controls how many tiles the trail is split into.<br><br>' +
                  'Drag the slider to <b>2</b> to get two tiles: one for each half of the route.',
            demo: tp3dMtDemoSlider('subdivisionSlider', TP3D_MT_SEGMENTS),
            waitFor: tp3dMtWaitForSlider(function() { return tp3dMtSegmentsValue() === TP3D_MT_SEGMENTS; }),
            waitText: 'Set Segments to 2',
            skipIf: function() { return tp3dMtSegmentsValue() === TP3D_MT_SEGMENTS; },
            onExit: tp3dMtBlurSlider
        },
        {
            target: function() { return document.getElementById('tileSpacing').parentElement; },
            placement: 'right',
            title: 'Set the tile spacing',
            text: '<b>Tile Spacing</b> leaves a gap between neighboring tiles. The map skips the part in ' +
                  'between, so the trail is cut where it crosses from one tile to the next.<br><br>' +
                  'Set it to <b>5 mm</b> to see how the trail gets cut at the tile edge.',
            demo: tp3dMtDemoSlider('tileSpacing', TP3D_MT_SPACING),
            waitFor: tp3dMtWaitForSlider(function() { return tp3dMtSpacingValue() === TP3D_MT_SPACING; }),
            waitText: 'Set Tile Spacing to 5',
            skipIf: function() { return tp3dMtSpacingValue() === TP3D_MT_SPACING; },
            onExit: tp3dMtBlurSlider
        },
        {
            target: '#totalSizeInfo',
            placement: 'right',
            title: 'Total map size',
            text: 'The total display size of both tiles together is shown here. ' +
                  'Adjust <b>Size (mm)</b> to change how big each individual tile is.'
        },
        {
            target: function() { return document.getElementById('resolutionSlider').parentElement; },
            placement: 'right',
            title: 'Set the resolution',
            text: '<b>Resolution</b> sets how much terrain detail each tile gets. Higher values look sharper ' +
                  'but take longer to generate. Lower values generate faster but show less detail.<br><br>' +
                  'Set it to <b>6</b> so this example generates quickly.',
            demo: tp3dMtDemoSlider('resolutionSlider', TP3D_MT_RESOLUTION),
            waitFor: tp3dMtWaitForSlider(function() {
                return parseInt(document.getElementById('resolutionSlider').value) === TP3D_MT_RESOLUTION;
            }),
            waitText: 'Set Resolution to 6',
            skipIf: function() {
                return parseInt(document.getElementById('resolutionSlider').value) === TP3D_MT_RESOLUTION;
            },
            onExit: tp3dMtBlurSlider
        },
        {
            target: ['#send', '.export-folder-pick'],
            placement: 'right',
            title: 'Generate your tiles',
            text: 'Click <b>Send to Blender</b> to generate both trail tiles.',
            waitFor: tp3dTutorial.waitForClick('#send'),
            waitText: 'Click Send to Blender',
            skippable: true
        }
    ];
}

// ---- "Create Multitile Country Maps" ----

function tp3dMultitileTutorialCountry() {
    return [
        {
            target: '#map',
            title: 'Create a country map',
            text: 'A GeoJSON file holds a real geographic boundary — a country, a region, a park. ' +
                  'Import it and the grid is clipped to that exact outline, so each tile follows the border.<br><br>' +
                  'We will split <b>Germany</b> into two tiles.<br><br>' +
                  'You can exit this tutorial at any time with <b>✕</b> or <b>Esc</b>.'
        },
        {
            target: '#importGeojson',
            placement: 'right',
            title: 'Import a GeoJSON boundary',
            text: 'Click <b>GeoJSON</b> to pick your file.<br><br>' +
                  'No file at hand? Use our sample of Germany.',
            buttons: [{ label: 'Use sample Germany GeoJSON', onClick: tp3dMtLoadSampleGeojson }],
            waitFor: tp3dTutorial.waitUntil(tp3dHasMtGeojson),
            waitText: 'Import a GeoJSON file'
        },
        {
            target: '#map',
            title: 'Germany on the map',
            text: 'The outline of Germany is now drawn on the map. The grid will be clipped to this border — ' +
                  'tiles that fall outside are left out, so the finished set follows the country\'s shape exactly.'
        },
        {
            target: function() { return document.getElementById('subdivisionSlider').parentElement; },
            placement: 'right',
            title: 'Set the segments',
            text: '<b>Segments</b> sets how many tiles wide the country is split.<br><br>' +
                  'Drag it to <b>2</b>.',
            demo: tp3dMtDemoSlider('subdivisionSlider', TP3D_MT_SEGMENTS),
            waitFor: tp3dMtWaitForSlider(function() { return tp3dMtSegmentsValue() === TP3D_MT_SEGMENTS; }),
            waitText: 'Set Segments to 2',
            skipIf: function() { return tp3dMtSegmentsValue() === TP3D_MT_SEGMENTS; },
            onExit: tp3dMtBlurSlider
        },
        {
            target: '#map',
            title: 'Tiles clipped to Germany',
            text: 'The grid is now split into two columns. Tiles that lie fully outside Germany\'s border are ' +
                  'removed, and the rest follow its outline exactly.<br><br>' +
                  'You can drag the orange box to reposition or resize it if you want a different crop.'
        },
        {
            target: function() { return document.getElementById('resolutionSlider').parentElement; },
            placement: 'right',
            title: 'Set the resolution',
            text: '<b>Resolution</b> sets how much terrain detail each tile gets. Higher values look sharper ' +
                  'but take longer to generate. Lower values generate faster but show less detail.<br><br>' +
                  'Set it to <b>6</b> so this example generates quickly.',
            demo: tp3dMtDemoSlider('resolutionSlider', TP3D_MT_RESOLUTION),
            waitFor: tp3dMtWaitForSlider(function() {
                return parseInt(document.getElementById('resolutionSlider').value) === TP3D_MT_RESOLUTION;
            }),
            waitText: 'Set Resolution to 6',
            skipIf: function() {
                return parseInt(document.getElementById('resolutionSlider').value) === TP3D_MT_RESOLUTION;
            },
            onExit: tp3dMtBlurSlider
        },
        {
            target: ['#send', '.export-folder-pick'],
            placement: 'right',
            title: 'Generate your country map',
            text: 'Click <b>Send to Blender</b> to generate all tiles.',
            waitFor: tp3dTutorial.waitForClick('#send'),
            waitText: 'Click Send to Blender',
            skippable: true
        }
    ];
}

tp3dTutorialMenu({
    button: '#tutorialBtn',
    title: 'Multi-Tile Generator Tutorials',
    offerId: 'offer-multitile-generator',
    tutorials: [
        {
            id: 'multitile-create',
            title: 'Create a multi-tile map',
            description: 'Split a map of the Alps into 2 x 2 hexagon tiles and extend it with more.',
            prepare: function() {
                tp3dResetMultitileGenerator();
                map.fitBounds(TP3D_MT_MATTERHORN_BOUNDS);
            },
            steps: tp3dMultitileTutorialCreate
        },
        {
            id: 'multitile-gpx',
            title: 'Spread a Trail (GPX file) across multiple tiles',
            description: 'Import a recorded route and let the grid automatically span all the tiles it covers.',
            prepare: function() {
                tp3dResetMultitileGenerator();
                tp3dMtAllElementsOff();
            },
            steps: tp3dMultitileTutorialGpx
        },
        {
            id: 'multitile-country',
            title: 'Create Multitile Country Maps',
            description: 'Import a GeoJSON country border and split it into tiles that follow its outline.',
            prepare: function() {
                tp3dResetMultitileGenerator();
                map.fitBounds(TP3D_MT_GERMANY_BOUNDS);
            },
            steps: tp3dMultitileTutorialCountry
        }
    ]
});
