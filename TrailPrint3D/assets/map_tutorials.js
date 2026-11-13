// Tutorial content for the Map Generator pages (generators/map_generator.html
// and premium/generators/map_generator_pe.html), built on the engine in
// assets/tutorial.js. Both pages share every element id used here, so one
// set of steps covers both. Must be inlined *after* the page has created
// `map` and its own Draw/Send handlers, and requires a #tutorialBtn button
// in the page's own markup.
//
// The Jigsaw Puzzle Generator pages (generators/puzzleGenerator.html and
// premium/generators/puzzleGenerator_pe.html) inline this file too: they
// share the drawn-area, layers-control and Settings-popup plumbing the
// helpers below work with, and get their own tutorial menu instead of the
// map one (see the bottom of this file, keyed on TP3D_IS_PUZZLE_PAGE).

// Viewport (clientX/Y) position of a lat/lng on the page's Leaflet map --
// what the tutorial engine's `demo` cursor points are expressed in.
function tp3dMapToViewport(lat, lng) {
    var p = map.latLngToContainerPoint([lat, lng]);
    var box = map.getContainer().getBoundingClientRect();
    return { x: box.left + p.x, y: box.top + p.y };
}

// Demo for "draw your area": a drag across the middle of the map -- corner
// to corner, or from the center outward when the page is in 'center' draw
// mode (rect_editor.js's drawMode).
function tp3dDemoDrawArea() {
    var box = map.getContainer().getBoundingClientRect();
    var cx = box.left + box.width / 2, cy = box.top + box.height / 2;
    var dx = Math.min(130, box.width / 5), dy = Math.min(90, box.height / 5);
    if (typeof drawMode !== 'undefined' && drawMode === 'center') {
        return [{ x: cx, y: cy }, { x: cx + dx, y: cy + dy }];
    }
    return [{ x: cx - dx, y: cy - dy }, { x: cx + dx, y: cy + dy }];
}

// Leaflet-draw's default rectangle style while a corner-to-corner draw is in
// progress (the page passes no shapeOptions of its own), vs. the orange the
// page itself uses for center-mode drawing and every finished area.
var TP3D_DRAWING_BLUE = '#3388ff';
var TP3D_DRAWN_ORANGE = '#E97826';

// Screen rect of the drawn area's orange box (its bounding box for every
// shape, not the inscribed blue preview), or null when nothing is drawn.
// Read from the rendered element -- what's actually on screen -- rather
// than converting coords, so demos land exactly on it.
function tp3dDrawnBoxRect() {
    if (typeof coords === 'undefined' || !coords) return null;
    var el = typeof rectLayer !== 'undefined' && rectLayer && rectLayer.getElement && rectLayer.getElement();
    if (el && el.getBoundingClientRect) return el.getBoundingClientRect();
    var nw = tp3dMapToViewport(coords.north, coords.west), se = tp3dMapToViewport(coords.south, coords.east);
    return { left: nw.x, top: nw.y, right: se.x, bottom: se.y, width: se.x - nw.x, height: se.y - nw.y };
}

// Demo for "adjust your shape": grab the bottom-right corner exactly and
// drag it outward diagonally.
function tp3dDemoDragCorner() {
    var r = tp3dDrawnBoxRect();
    if (!r) return null;
    return [{ x: r.right, y: r.bottom }, { x: r.right + 70, y: r.bottom + 45 }];
}

// Demo for "move your shape": grab the move handle in the middle of the box
// (rect_editor.js's bindMoveHandle) and drag it; the ghost is the box itself
// riding along with the cursor.
function tp3dDemoMoveShape() {
    var r = tp3dDrawnBoxRect();
    if (!r) return null;
    var cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    return [{ x: cx, y: cy }, { x: cx + 90, y: cy + 40 }];
}
function tp3dDemoMoveGhost(start, current) {
    var r = tp3dDrawnBoxRect();
    if (!r) return null;
    return { left: r.left + current.x - start.x, top: r.top + current.y - start.y, width: r.width, height: r.height };
}

// Leaflet's layers control (map_init.js, bottom-left) only lists its layers
// while hovered and collapses again on mouseout. The tutorial's layer step
// keeps it open so the list itself can be highlighted -- map_init.js's
// tp3dLayersHeldOpen blocks Leaflet's own collapse meanwhile.
function tp3dHoldLayersOpen() {
    tp3dLayersHeldOpen = true;
    tp3dLayersControl.expand();
}
function tp3dReleaseLayersOpen() {
    tp3dLayersHeldOpen = false;
    tp3dLayersControl.collapse();
}

// Puts the page back to what a first launch of the generator shows, so a
// tutorial always starts from the same known state. Works by driving the
// page's own controls (delete / Clear buttons, shape and draw-mode buttons,
// layer radios) rather than poking its variables, so every side effect those
// handlers already take care of -- preview layers, sidebar lists, status
// text, saveState -- happens exactly as if the user had clicked them, on both
// the free and premium page. Leaves the map's position alone, and doesn't
// touch the Blender-side Settings popup (those are scene settings, not
// generator state).
function tp3dResetMapGenerator() {
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

    if (tp3dHasDrawnArea()) document.getElementById('deleteBtn').click();
    clickIfShown('clearGpx');
    clickIfShown('clearGeojson');
    clickIfShown('clearSvgShape');
    clickIfShown('clearPrefetch');

    var rectBtn = document.querySelector('.shape-btn[data-shape="rectangle"]');
    if (rectBtn && !rectBtn.classList.contains('active')) rectBtn.click();
    var cornerBtn = document.querySelector('.draw-mode-btn[data-mode="corner"]');
    if (cornerBtn && !cornerBtn.classList.contains('active')) cornerBtn.click();

    plateMode = 'NONE';
    textLayout = 'NONE';
    tp3dRefreshShapeExtras();
    saveState();

    // Same defaults the page itself starts with: the static HTML's slider
    // value, and Size from the scene's Object Size (OBJ_SIZE, as on load).
    setInput('resolutionSlider', document.getElementById('resolutionSlider').defaultValue || 8);
    setInput('objSizeInput', typeof OBJ_SIZE !== 'undefined' ? OBJ_SIZE : 100);

    // Base layer back to OpenStreetMap via the layers control's own radio,
    // so Leaflet's control and the page's activeBaseLayerName stay in sync.
    document.querySelectorAll('.leaflet-control-layers-base label').forEach(function(label) {
        var input = label.querySelector('input');
        if (input && !input.checked && label.textContent.trim() === 'OpenStreetMap') input.click();
    });
}

// waitFor for the "adjust" steps: snapshots the drawn area's bounds when the
// step opens, then re-checks after every mouse release (deferred a moment,
// so the page's own drag-end handlers have updated coords first) and
// advances once the area's size changed. Copies the values, since the move
// handle mutates coords in place.
function tp3dWaitForResize() {
    return function(done) {
        function snap() {
            if (!tp3dHasDrawnArea()) return null;
            return { n: coords.north, s: coords.south, e: coords.east, w: coords.west };
        }
        var start = snap();
        function onUp() {
            setTimeout(function() {
                var now = snap();
                if (!now) return;
                if (!start) { start = now; return; }
                var tol = 1e-9;
                var sizeChanged = Math.abs((now.n - now.s) - (start.n - start.s)) > tol ||
                    Math.abs((now.e - now.w) - (start.e - start.w)) > tol;
                if (sizeChanged) done();
                else start = now;
            }, 60);
        }
        document.addEventListener('pointerup', onUp, true);
        return function() { document.removeEventListener('pointerup', onUp, true); };
    };
}

// waitFor for the "move" steps: advances on the release after the shape was
// actually dragged by a move handle -- rect_editor.js's own, or leaflet-draw's
// (on top of it while its corner editing is active). Watches the gesture
// rather than comparing bounds: tp3dSquareCoords re-squares every shape but
// Rectangle as it changes latitude, so a real move changes the size too.
function tp3dWaitForMove() {
    return function(done) {
        var dragged = false;
        function onDrag() { dragged = true; }
        function onUp() {
            if (dragged) setTimeout(done, 60);
        }
        var events = 'tp3d:shapemove ' + L.Draw.Event.EDITMOVE;
        map.on(events, onDrag);
        document.addEventListener('pointerup', onUp, true);
        return function() {
            map.off(events, onDrag);
            document.removeEventListener('pointerup', onUp, true);
        };
    };
}

function tp3dHasDrawnArea() {
    return typeof coords !== 'undefined' && !!coords;
}

// Bundled sample files (sample_files/ in the addon, served by
// picker_server.py's /get_sample_file) for users without a file of their
// own. Returns a popup-button onClick that hands the sample to one of the
// page's own file inputs (#gpxInput, #geojsonInput, #svgShapeInput) and
// fires 'change', so it goes through exactly the same import code as a
// file picked by hand, on both the free and premium page.
function tp3dSampleFileLoader(fileName, inputId, mimeType, what) {
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

var tp3dLoadSampleGpx = tp3dSampleFileLoader('BetweenLakes.gpx', 'gpxInput', 'application/gpx+xml', 'GPX file');
var tp3dLoadSampleGeojson = tp3dSampleFileLoader('germany.json', 'geojsonInput', 'application/geo+json', 'GeoJSON file');
var tp3dLoadSampleSvg = tp3dSampleFileLoader('star.svg', 'svgShapeInput', 'image/svg+xml', 'SVG file');

function tp3dHasGpx() {
    return typeof gpxPaths !== 'undefined' && gpxPaths.length > 0;
}

function tp3dMapTutorialNoGpx() { return tp3dMapGenerateSteps(false); }
function tp3dMapTutorialGpx() { return tp3dMapGenerateSteps(true); }

// Steps shared by both "Generate Map" tutorials. The GPX one assumes the
// first was done: it starts at the GPX import (no size/shape/layer steps)
// and adds center mode instead. Importing a trail auto-places a shape
// around it (the page's gpxInput handler), so the two "Draw your area"
// steps then skip themselves.
function tp3dMapGenerateSteps(withGpx) {
    var intro = [
        {
            target: function() { return document.getElementById('objSizeInput').closest('.field-row'); },
            interactive: true,
            placement: 'right',
            title: 'Set the size',
            text: 'Set the size of the finished map in <b>mm</b>.<br><br>' +
                  'You can exit this tutorial at any time with <b>✕</b> or <b>Esc</b>.'
        },
        {
            target: '#shapeToggle',
            interactive: true,
            placement: 'right',
            title: 'Select a shape'
        }
    ];
    var gpx = [
        {
            target: '#importGpx',
            placement: 'right',
            title: 'Import a GPX file',
            text: 'Click <b>Import GPX</b> and pick the GPX file of your route.<br><br>' +
                  'No GPX file at hand? Use our sample route instead.<br><br>' +
                  'We picked the <b>hexagon</b> shape for this tutorial.',
            buttons: [{ label: 'Use sample GPX file', onClick: tp3dLoadSampleGpx }],
            waitFor: tp3dTutorial.waitUntil(tp3dHasGpx),
            waitText: 'Import a GPX file'
        }
    ];
    var rest = [
        // Draw an area -- skipped when one already exists, since the steps
        // after it adjust that area.
        {
            target: '#drawBtn',
            placement: 'right',
            title: 'Draw your area',
            text: 'Move the map to the place you want to print, then click the <b>pencil</b> to start drawing.',
            waitFor: tp3dTutorial.waitForClick('#drawBtn'),
            waitText: 'Click the pencil',
            skipIf: tp3dHasDrawnArea
        },
        {
            target: '#map',
            title: 'Draw your area',
            text: 'Click and drag on the map to draw your area, then let go of the mouse.<br><br>' +
                  'If nothing happens, click the pencil again.',
            demo: tp3dDemoDrawArea,
            demoShape: function() {
                return typeof drawMode !== 'undefined' && drawMode === 'center' ? 'rect-center' : 'rect';
            },
            demoGhostColor: function() {
                return typeof drawMode !== 'undefined' && drawMode === 'center' ? TP3D_DRAWN_ORANGE : TP3D_DRAWING_BLUE;
            },
            waitFor: tp3dTutorial.waitForEvent(map, L.Draw.Event.CREATED),
            waitText: 'Draw on the map',
            skipIf: tp3dHasDrawnArea
        },
        // This and the move step below are left out of the GPX tutorial --
        // the import already sized and placed the shape around the trail.
        withGpx ? null : {
            target: '#map',
            title: 'Adjust your shape',
            text: 'Drag a corner of the orange box to adjust your shape.',
            demo: tp3dDemoDragCorner,
            waitFor: tp3dWaitForResize(),
            waitText: 'Drag a corner',
            skippable: true
        },
        withGpx ? null : {
            target: '#map',
            title: 'Move your shape',
            text: 'Drag the handle in the middle of the box to move the whole shape without changing its size.',
            demo: tp3dDemoMoveShape,
            waitFor: tp3dWaitForMove(),
            waitText: 'Drag the middle handle',
            skippable: true,
            demoGhost: tp3dDemoMoveGhost,
            demoGhostColor: TP3D_DRAWN_ORANGE
        },
        // Center mode is only taught in the GPX tutorial: resizing around the
        // center keeps the trail centered in the shape the import placed.
        withGpx ? {
            target: '.draw-mode-btn[data-mode="center"]',
            placement: 'right',
            title: 'Switch to center mode',
            text: 'Click <b>Draw from a center point</b>. In this mode the center of your shape stays fixed.',
            waitFor: tp3dTutorial.waitForClick('.draw-mode-btn[data-mode="center"]'),
            waitText: 'Click the center button',
            skipIf: function() { return typeof drawMode !== 'undefined' && drawMode === 'center'; }
        } : null,
        withGpx ? {
            target: '#map',
            title: 'Adjust your shape',
            text: 'Drag a corner. The opposite side moves with it, so the shape grows or shrinks ' +
                  'around its center and your trail stays in the middle.',
            demo: tp3dDemoDragCorner,
            waitFor: tp3dWaitForResize(),
            waitText: 'Drag a corner',
            skippable: true
        } : null,
        // Already explained in the first tutorial -- left out of the GPX one.
        withGpx ? null : {
            target: '.leaflet-control-layers',
            interactive: true,
            title: 'Select a different layer',
            text: 'Pick a different map background, like <b>Satellite</b> or <b>Topographic</b>, to see ' +
                  'your area better.',
            onEnter: tp3dHoldLayersOpen,
            onExit: tp3dReleaseLayersOpen
        },
        // Already shown at the end of the first tutorial -- left out of the GPX one.
        withGpx ? null : {
            // Whichever is visible: Select Export Folder stands in for Send until a folder is set (export_folder.js).
            target: ['#send', '.export-folder-pick'],
            placement: 'right',
            title: 'Generate your map',
            text: 'Once you\'re happy, click <b>Generate</b> to generate the map.',
            waitFor: tp3dTutorial.waitForClick('#send'),
            waitText: 'Click Generate',
            skippable: true
        }
    ];
    // The GPX tutorial skips size and shape too -- the first tutorial
    // already covers them.
    return (withGpx ? gpx : intro).concat(rest).filter(Boolean);
}

// ---- "Colorize your Map" ----

// OSM vs ESA example pictures, hosted on trailprint3d.com rather than
// bundled (keeps the addon small). An image that fails to load just
// removes itself, so the step still reads fine before they're uploaded.
var TP3D_TUTORIAL_IMG_OSM = 'https://trailprint3d.com/images/howto/TutorialOSM.webp';
var TP3D_TUTORIAL_IMG_ESA = 'https://trailprint3d.com/images/howto/TutorialESA.webp';

// The sample track's extent (sample_files/BetweenLakes.gpx) -- lakes,
// rivers, towns and roads in one small area, good for showing elements.
// Only the area is placed; the trail itself isn't imported.
var TP3D_SAMPLE_AREA_BOUNDS = [[45.94103, 9.069148], [46.042722, 9.239247]];

function tp3dPlaceSampleArea() {
    placeShapeAroundBounds(L.latLngBounds(TP3D_SAMPLE_AREA_BOUNDS), 0.8);
    if (typeof rectLayer !== 'undefined' && rectLayer) map.fitBounds(rectLayer.getBounds(), { padding: [50, 50] });
}

function tp3dSettingsModal() { return document.getElementById('settingsModal'); }
function tp3dSettingsOpen() {
    var m = tp3dSettingsModal();
    return !!m && m.classList.contains('open');
}
function tp3dInSettings(selector) {
    return function() {
        var m = tp3dSettingsModal();
        return m ? m.querySelector(selector) : null;
    };
}
// The Elements tab card whose toggle icon is *key* ('water', 'roads', ...).
function tp3dSettingsCard(key) {
    return function() {
        var m = tp3dSettingsModal();
        var icon = m && m.querySelector('.card-icon[data-element-toggle="' + key + '"]');
        return icon ? icon.closest('.element-card') : null;
    };
}

function tp3dIsOsm() {
    return typeof ELEMENT_SOURCE === 'undefined' || ELEMENT_SOURCE !== 'WORLDCOVER';
}

// Same reset as the other tutorials, plus: Settings popup closed, element
// source on OSM (the steps explain OSM's Water/Roads options) with every
// element off, so "Turn elements on or off" starts from a clean strip, and
// the sample area placed as a hexagon. Switching reloads OSM's own element
// states from Blender, so they're turned off once that's done.
function tp3dPrepareColorTutorial() {
    var m = tp3dSettingsModal();
    if (m) m.classList.remove('open');
    tp3dResetMapGenerator();
    var switching = tp3dIsOsm() || typeof tp3dSwitchElementSource !== 'function'
        ? null : tp3dSwitchElementSource('OSM');
    if (switching) switching.then(function() { tp3dAllElementsOff(); });
    else tp3dAllElementsOff();
    // Hexagon sample shape -- picked through its own button (like a user
    // would) before placing, since placeShapeAroundBounds uses the current shape.
    var hexBtn = document.querySelector('.shape-btn[data-shape="hexagon"]');
    if (hexBtn) hexBtn.click();
    tp3dPlaceSampleArea();
}

// Advances once any element is switched on or off (chip, card or flyout).
function tp3dWaitForElementToggle() {
    return function(done) {
        var start = JSON.stringify(TP3D_ELEMENT_STATE);
        var id = setInterval(function() {
            if (JSON.stringify(TP3D_ELEMENT_STATE) !== start) done();
        }, 200);
        return function() { clearInterval(id); };
    };
}

function tp3dElementChipWrap(key) {
    var chip = document.querySelector('#elementStatus .element-chip[data-element-toggle="' + key + '"]');
    return chip ? chip.closest('.element-chip-wrap') : null;
}

// Center of the Water chip -- where the sample cursor clicks.
function tp3dDemoClickWater() {
    var chip = document.querySelector('#elementStatus .element-chip[data-element-toggle="water"]');
    if (!chip) return null;
    var r = chip.getBoundingClientRect();
    return [{ x: r.left + 18, y: r.top + r.height / 2 }];
}

function tp3dCompareHtml() {
    function col(img, name, lines) {
        return '<div class="tp3d-tut-compare-col">' +
            '<img src="' + img + '" alt="" onerror="this.remove()">' +
            '<b>' + name + '</b><br>' + lines + '</div>';
    }
    return '<div class="tp3d-tut-compare">' +
        col(TP3D_TUTORIAL_IMG_OSM, 'OSM', 'More functionality.<br>Sharp shapes, includes roads &amp; buildings.<br>Best for smaller maps; gets slower the larger the map.') +
        col(TP3D_TUTORIAL_IMG_ESA, 'ESA', 'Made from satellite images.<br>Softer shapes, no roads or buildings.<br>No map size limit.') +
        '</div>';
}

function tp3dMapTutorialColors() {
    return [
        {
            target: '#elementStatus',
            placement: 'bottom',
            title: 'Map elements',
            text: 'These are the parts of your map that get their own color, like water, forest or roads.<br><br>' +
                  'We placed a sample area for you to try them on.'
        },
        {
            target: '.element-source-switch-compact',
            placement: 'bottom',
            width: 380,
            title: 'OSM or ESA',
            text: tp3dCompareHtml() + '<div class="tp3d-tut-note">This tutorial uses OSM.</div>'
        },
        {
            target: function() { return Array.prototype.slice.call(document.querySelectorAll('#elementStatus .element-chip-wrap')); },
            placement: 'bottom',
            title: 'Turn elements on or off',
            text: 'Click an element to switch it on or off. Green means on.',
            demo: tp3dDemoClickWater,
            waitFor: tp3dWaitForElementToggle(),
            waitText: 'Click an element',
            skippable: true
        },
        {
            target: function() {
                var wrap = tp3dElementChipWrap('water');
                return wrap ? [wrap, wrap.querySelector('.element-flyout')] : null;
            },
            interactive: true,
            placement: 'right',
            title: 'Pick the kind of water',
            text: 'Hover over <b>Water</b> to choose which kinds to include.<br><br>' +
                  '<b>Small Rivers</b> and <b>Big Rivers</b> are only needed if one of your rivers is missing.',
            // Holds the flyout open for the step, and closes it again after.
            onEnter: function() { tp3dHoldFlyoutOpen('water'); },
            onExit: function() { tp3dReleaseFlyout('water'); },
            skipIf: function() { return !tp3dIsOsm(); }
        },
        {
            target: '.settings-gear-btn',
            placement: 'bottom',
            title: 'All element options',
            text: 'Click <b>Settings</b> to see every option for each element.',
            waitFor: tp3dTutorial.waitForClick('.settings-gear-btn'),
            waitText: 'Click Settings',
            skipIf: tp3dSettingsOpen
        },
        {
            target: tp3dSettingsCard('water'),
            interactive: true,
            title: 'Water settings',
            text: '<ul class="tp3d-tut-list">' +
                  '<li><b>Checkboxes:</b> which water to include</li>' +
                  '<li><b>Lake/Pond Threshold:</b> filters out elements with a surface area smaller than the value</li>' +
                  '<li><b>River Width:</b> thicker or thinner rivers (only for <b>Small Rivers</b>)</li>' +
                  '<li><b>Min Island Area:</b> islands in the ocean smaller than this are covered with water ' +
                  'instead of being left out</li>' +
                  '<li><b>Flatten Water Surface:</b> keeps the water flat instead of following the terrain</li>' +
                  '<li><b>Insert:</b> sinks the water a bit lower</li>' +
                  '</ul>',
            onEnter: function() { if (window.tp3dActivateSettingsTab) window.tp3dActivateSettingsTab('elements'); },
            skipIf: function() { return !tp3dIsOsm(); }
        },
        {
            target: tp3dSettingsCard('roads'),
            interactive: true,
            title: 'Road settings',
            text: '<ul class="tp3d-tut-list">' +
                  '<li><b>Checkboxes:</b> which road types to include</li>' +
                  '<li><b>Width Multiplier:</b> thicker or thinner roads</li>' +
                  '<li><b>Height:</b> how far the roads stick out</li>' +
                  '<li><b>Cut Tolerance:</b> small gap around roads in Single Extruder mode, so the pieces fit</li>' +
                  '</ul>',
            skipIf: function() { return !tp3dIsOsm(); }
        },
        {
            target: tp3dInSettings('.tp3d-modal-close-btn'),
            title: 'Close the settings',
            text: 'Your changes are saved right away. Close the settings to continue.',
            waitFor: tp3dTutorial.waitUntil(function() { return !tp3dSettingsOpen(); }),
            waitText: 'Close the settings',
            skipIf: function() { return !tp3dSettingsOpen(); }
        },
        {
            // Whichever is visible: Select Export Folder stands in for Send until a folder is set (export_folder.js).
            target: ['#send', '.export-folder-pick'],
            placement: 'right',
            title: 'Generate your map',
            text: 'Click <b>Generate</b> to generate your colored map.',
            waitFor: tp3dTutorial.waitForClick('#send'),
            waitText: 'Click Generate',
            skippable: true
        }
    ];
}

// ---- "Use GeoJson or SVG as your Map Shape" ----

function tp3dHasGeojson() {
    return typeof geojsonPaths !== 'undefined' && geojsonPaths.length > 0;
}
function tp3dHasSvgShape() {
    return typeof svgShapePath !== 'undefined' && !!svgShapePath;
}

function tp3dMapTutorialShapes() {
    return [
        {
            target: '#importGeojson',
            placement: 'right',
            title: 'Import a GeoJSON boundary',
            text: 'A GeoJSON file holds a real outline, like a country, region or park border. ' +
                  'Your map takes exactly that shape.<br><br>' +
                  'Click <b>GeoJSON</b> to pick a file, or try our sample of Germany.',
            buttons: [{ label: 'Use sample GeoJSON', onClick: tp3dLoadSampleGeojson }],
            waitFor: tp3dTutorial.waitUntil(tp3dHasGeojson),
            waitText: 'Import a GeoJSON file'
        },
        {
            target: '#map',
            title: 'Your GeoJSON map',
            text: 'The blue outline is your map.<br><br>' +
                  '<b>A GeoJSON area can\'t be moved or resized</b>. It always sits exactly where the file puts it.'
        },
        {
            target: '#importSvgShape',
            placement: 'right',
            title: 'Import an SVG shape',
            text: 'An SVG file can be any outline you like, like a logo, an animal or a star. ' +
                  'It replaces the GeoJSON boundary.<br><br>' +
                  'Click <b>SVG</b> to pick a file, or try our sample star.',
            buttons: [{ label: 'Use sample SVG', onClick: tp3dLoadSampleSvg }],
            waitFor: tp3dTutorial.waitUntil(tp3dHasSvgShape),
            waitText: 'Import an SVG file'
        },
        {
            target: '#map',
            title: 'Place your shape',
            text: 'Unlike a GeoJSON boundary, an SVG shape can be placed anywhere. ' +
                  'Drag the handle in the middle to move it, or a corner to resize it.',
            demo: tp3dDemoMoveShape,
            demoGhost: tp3dDemoMoveGhost,
            demoGhostColor: TP3D_DRAWN_ORANGE,
            waitFor: tp3dWaitForMove(),
            waitText: 'Move the shape',
            skippable: true
        },
        {
            target: function() {
                return [document.getElementById('svgShapeList'), document.getElementById('clearSvgShape')];
            },
            placement: 'right',
            title: 'Remove an imported file',
            text: 'To go back to a normal shape, click <b>Clear SVG Shape</b> or pick another shape. ' +
                  'An imported GeoJSON has a <b>Clear GeoJSON</b> button in the same place.'
        }
    ];
}

// ---- "Prefetch and edit Elements" ----

// Munich's old town just south-east of Marienplatz, reaching over to the Isar
// -- dense roads and buildings plus the river in a small area, so a prefetch
// is quick and has lots to click.
var TP3D_MUNICH_ALTSTADT_BOUNDS = [[48.1308, 11.5785], [48.1368, 11.5875]];

// Exactly which sub-categories the tutorial asks for (ADVANCED_SETTINGS_STATE
// keys, see COMPOSITE_ELEMENTS in settings_modal.js) -- true = ticked, every
// other one unticked, so the prefetch stays small.
var TP3D_PREFETCH_WATER = { colWPondsActive: true, colWSmallRiversActive: false, colWBigRiversActive: false, elOActive: false };
var TP3D_PREFETCH_ROADS = {
    elSHighwaysActive: false, elSMajorActive: false, elSMinorActive: false, elSResidentialActive: true,
    elSServiceActive: false, elSFootwayActive: false, elSPedestrianActive: true, elSCycleBridleActive: false,
    elSTrackActive: false, elSPathActive: false, elSRacewayActive: false
};

function tp3dZoomToMunich() {
    placeShapeAroundBounds(L.latLngBounds(TP3D_MUNICH_ALTSTADT_BOUNDS), 0.8);
    if (typeof rectLayer !== 'undefined' && rectLayer) map.fitBounds(rectLayer.getBounds(), { padding: [40, 40] });
}

// Zooms onto the drawn area's top-left quarter, so single prefetched
// elements are big enough to click.
function tp3dZoomToTopLeftQuarter() {
    var b = typeof rectLayer !== 'undefined' && rectLayer ? rectLayer.getBounds()
        : L.latLngBounds(TP3D_MUNICH_ALTSTADT_BOUNDS);
    var c = b.getCenter();
    map.fitBounds(L.latLngBounds([c.lat, b.getWest()], [b.getNorth(), c.lng]), { padding: [20, 20] });
}

// Switches every OSM element off (through the chips' own toggle, so Blender
// gets the same /toggle_element calls a click sends), so the "Turn on some
// elements" step starts from nothing. Only for OSM -- WorldCover's chips are
// a different set, so this runs again once the tutorial has switched to OSM.
// anySource switches off the current source's elements, OSM or ESA (the
// puzzle tutorial, which turns ESA ones on later).
function tp3dAllElementsOff(anySource) {
    if ((!anySource && !tp3dIsOsm()) || typeof TP3D_ELEMENT_STATE === 'undefined') return;
    Object.keys(TP3D_ELEMENT_STATE).forEach(function(key) {
        if (key !== 'elevation' && TP3D_ELEMENT_STATE[key]) tp3dToggleElement(key);
    });
}

// Forgets every prefetch choice, even when no prefetch is drawn (the reset's
// Clear click only fires while that button is showing).
function tp3dResetPrefetch() {
    if (typeof prefetchExcluded === 'undefined') return;
    prefetchExcluded = {};
    prefetchWanted = false;
    clearPrefetch();
    prefetchPersist();
}

function tp3dHasPrefetch() {
    return typeof prefetchFeatures !== 'undefined' && Object.keys(prefetchFeatures).length > 0;
}

// *key* is on and, if *wanted* is given, its sub-checkboxes match it exactly.
function tp3dElementMatches(key, wanted) {
    if (typeof TP3D_ELEMENT_STATE === 'undefined' || !TP3D_ELEMENT_STATE[key]) return false;
    return !wanted || Object.keys(wanted).every(function(f) {
        return !!ADVANCED_SETTINGS_STATE[f] === wanted[f];
    });
}

// Sample cursor for one element step: the chip while the element is off,
// then each sub-checkbox (in the chip's hover list) that still differs from
// *wanted*, one at a time.
function tp3dDemoSetupElement(key, wanted) {
    return function() {
        var wrap = tp3dElementChipWrap(key);
        if (!wrap) return null;
        var el = null;
        if (!TP3D_ELEMENT_STATE[key]) {
            el = wrap.querySelector('.element-chip');
        } else if (wanted) {
            var f = Object.keys(wanted).filter(function(k) { return !!ADVANCED_SETTINGS_STATE[k] !== wanted[k]; })[0];
            if (f) el = wrap.querySelector('.element-flyout input[data-advanced-checkbox="' + f + '"]');
        }
        if (!el) return null;
        var r = el.getBoundingClientRect();
        return [{ x: r.left + Math.min(18, r.width / 2), y: r.top + r.height / 2 }];
    };
}

// Keeps a chip's hover list (element_status.js's tp3dAttachSubFlyout) open
// for the step: held, so the real mouse leaving or a press on the popup's
// buttons doesn't close it (which would shrink the focus box and move the
// popup out from under the click). Held even when it's already open (the
// real mouse usually hovers the chip right then); the timer re-holds it if
// the chips get re-rendered meanwhile.
var tp3dFlyoutHoldTimer = null;
function tp3dHoldFlyoutOpen(key) {
    tp3dReleaseFlyout(null);
    function open() {
        var wrap = tp3dElementChipWrap(key);
        if (wrap) wrap.dispatchEvent(new CustomEvent('tp3d-hold-flyout', { detail: true }));
    }
    open();
    tp3dFlyoutHoldTimer = setInterval(open, 250);
}
function tp3dReleaseFlyout(key) {
    if (tp3dFlyoutHoldTimer) { clearInterval(tp3dFlyoutHoldTimer); tp3dFlyoutHoldTimer = null; }
    var wrap = key && tp3dElementChipWrap(key);
    if (wrap) wrap.dispatchEvent(new CustomEvent('tp3d-hold-flyout', { detail: false }));
}

// Chip plus its hover list (when open) -- the focus box for an element step.
function tp3dChipAndFlyout(key) {
    return function() {
        var wrap = tp3dElementChipWrap(key);
        return wrap ? [wrap, wrap.querySelector('.element-flyout')].filter(Boolean) : null;
    };
}

// One "turn this element on" action step for the prefetch tutorial.
function tp3dPrefetchElementStep(key, title, text, waitText, wanted) {
    return {
        target: tp3dChipAndFlyout(key),
        placement: 'bottom',
        title: title,
        text: text,
        demo: tp3dDemoSetupElement(key, wanted),
        waitFor: tp3dTutorial.waitUntil(function() { return tp3dElementMatches(key, wanted); }),
        waitText: waitText,
        skippable: true,
        onEnter: wanted ? function() { tp3dHoldFlyoutOpen(key); } : undefined,
        onExit: wanted ? function() { tp3dReleaseFlyout(key); } : undefined
    };
}

// Sample cursor clicks the prefetched building nearest the map's center
// (any element if there are no buildings).
function tp3dDemoClickPrefetched() {
    if (!tp3dHasPrefetch()) return null;
    var box = map.getContainer().getBoundingClientRect();
    var center = { x: box.width / 2, y: box.height / 2 };
    var ids = Object.keys(prefetchFeatures);
    var buildings = ids.filter(function(id) { return prefetchFeatures[id].kind === 'BUILDINGS'; });
    if (buildings.length) ids = buildings;
    var best = null, bestDist = Infinity;
    ids.forEach(function(id) {
        var p = map.latLngToContainerPoint(prefetchFeatures[id].layer.getBounds().getCenter());
        var d = Math.hypot(p.x - center.x, p.y - center.y);
        if (d < bestDist) { best = p; bestDist = d; }
    });
    return best ? [{ x: box.left + best.x, y: box.top + best.y }] : null;
}

function tp3dMapTutorialPrefetch() {
    return [
        {
            target: '.element-source-switch-compact',
            placement: 'bottom',
            title: 'Prefetch and edit Elements',
            text: 'Prefetching shows the map elements on the map before you generate, ' +
                  'so you can pick which ones to keep.<br><br>' +
                  '<b>It only works with OSM selected.</b>'
        },
        {
            target: '.element-source-switch-compact',
            placement: 'bottom',
            title: 'Select OSM',
            text: 'Click <b>OSM</b> to switch the element source.',
            waitFor: tp3dTutorial.waitUntil(tp3dIsOsm),
            waitText: 'Click OSM',
            skipIf: tp3dIsOsm
        },
        {
            target: '#map',
            title: 'Munich old town',
            text: 'We zoomed into Munich\'s old town, right next to the river Isar. ' +
                  'The orange box is the area whose elements get prefetched.',
            onEnter: function() {
                tp3dAllElementsOff();
                tp3dZoomToMunich();
            }
        },
        tp3dPrefetchElementStep('buildings', 'Turn on Buildings',
            'Click <b>Buildings</b> to switch it on. Green means on.',
            'Turn on Buildings'),
        tp3dPrefetchElementStep('water', 'Turn on Water',
            'Click <b>Water</b> to switch it on, then tick only <b>Ponds &amp; Lakes</b> in its list.<br><br>' +
            'It also includes wide rivers drawn as water areas, like the Isar.',
            'Water: only Ponds & Lakes', TP3D_PREFETCH_WATER),
        tp3dPrefetchElementStep('roads', 'Turn on Roads',
            'Click <b>Roads</b> to switch it on, then tick only <b>Residential Roads</b> and ' +
            '<b>Pedestrian Streets</b> in its list.',
            'Roads: Residential + Pedestrian', TP3D_PREFETCH_ROADS),
        {
            target: '#prefetchBtn',
            placement: 'bottom',
            title: 'Prefetch',
            text: 'Click <b>Prefetch Elements</b> to load them for your area.',
            waitFor: tp3dTutorial.waitForClick('#prefetchBtn'),
            waitText: 'Click Prefetch Elements'
        },
        {
            target: '#prefetchBtn',
            placement: 'bottom',
            title: 'Loading…',
            text: 'This can take a moment. The elements appear on the map once they are loaded.',
            waitFor: tp3dTutorial.waitUntil(tp3dHasPrefetch),
            waitText: 'Loading elements',
            skippable: true,
            skipIf: tp3dHasPrefetch
        },
        {
            target: '#map',
            title: 'Turn single elements off',
            text: 'Click an element on the map to leave it out. Its fill disappears and only a ' +
                  '<b>dashed outline</b> is left. Click it again to bring it back.',
            // Not an action step: the user can try it as often as they like
            // and moves on with Next.
            interactive: true,
            onEnter: tp3dZoomToTopLeftQuarter,
            demo: tp3dDemoClickPrefetched
        },
        {
            target: '#prefetchInfo',
            interactive: true,
            placement: 'right',
            title: 'The element list',
            text: 'Click a <b>name</b> to hide it on the map. Hidden elements are still generated, ' +
                  'they just aren\'t shown here.<br>' +
                  'Click a <b>number</b> to switch all of that kind off or on.',
            skipIf: function() { return !tp3dHasPrefetch(); }
        },
        {
            target: '#map',
            interactive: true,
            title: 'Shortcuts',
            text: '<ul class="tp3d-tut-list">' +
                  '<li><b>Shift + drag</b> (or <b>Alt + drag</b>): draw a box to switch every element in it off. ' +
                  'If they are all off already, they are switched back on.</li>' +
                  '<li><b>Alt + click</b> on a road: switches the whole road off, not just the clicked segment. ' +
                  'It follows the road through junctions as long as it keeps going straight.</li>' +
                  '</ul>' +
                  'Elements hidden in the list are not affected by the box.',
            skipIf: function() { return !tp3dHasPrefetch(); }
        }
    ];
}

// ---- "Use local Datasets (GeoTiff)" ----

// Bundled sample DEM: the BetweenLakes.gpx trail's area plus a margin
// (~105 m pixels, under 20 KB), so TP3D_SAMPLE_AREA_BOUNDS sits inside it.
var TP3D_SAMPLE_DEM = 'BetweenLakesArea.tif';

function tp3dApiSelect() { return document.getElementById('setting-api'); }
function tp3dApiRow() {
    var select = tp3dApiSelect();
    return select ? select.closest('.adv-field-row') : null;
}
function tp3dIsLocalDem() {
    var select = tp3dApiSelect();
    return !!select && select.value === 'LOCAL_DEM';
}
function tp3dDemPathRow() {
    var cell = document.querySelector('#settingsModal .dem-path-cell');
    return cell ? cell.closest('.adv-field-row') : null;
}
function tp3dShowOtherTab() { if (window.tp3dActivateSettingsTab) window.tp3dActivateSettingsTab('other'); }
function tp3dOtherTabActive() {
    var tab = document.getElementById('settings-tab-other');
    return !!tab && tab.classList.contains('active');
}

// A readable DEM is selected and map_init.js has drawn its coverage.
function tp3dHasDemCoverage() {
    var c = window.tp3dDemCoverage;
    return !!c && !!(c.footprint || c.tiles);
}

// Picks the bundled sample as the DEM File without the OS dialog (POST
// /pick_dem_path's 'sample' kind); the DEM row and the coverage overlay
// then follow the change like after any other pick.
function tp3dUseSampleDem(button) {
    var label = button.textContent;
    button.disabled = true;
    button.textContent = 'Loading…';
    fetch('/pick_dem_path', { method: 'POST', body: JSON.stringify({ kind: 'sample', name: TP3D_SAMPLE_DEM }) })
        .then(function(r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            if (window.tp3dCheckDemCoverage) tp3dCheckDemCoverage();
        })
        .catch(function() {
            button.disabled = false;
            button.textContent = label;
            tp3dAlert('Could not load the sample GeoTIFF file.');
        });
}

// Same reset as the other tutorials, plus: Settings popup closed, every
// element off (OSM or ESA), Resolution 5, the Elevation API back on
// Mapterhorn and no DEM File selected, so the steps start from scratch.
// The API goes through its own Settings select (same /update_setting call
// and row visibility as a user's pick); the DEM path is cleared directly,
// since the popup has no control for that.
function tp3dPrepareLocalDemTutorial() {
    var m = tp3dSettingsModal();
    if (m) m.classList.remove('open');
    tp3dResetMapGenerator();
    tp3dAllElementsOff(true);

    var slider = document.getElementById('resolutionSlider');
    if (slider && String(slider.value) !== '5') {
        slider.value = 5;
        slider.dispatchEvent(new Event('input', { bubbles: true }));
        saveState();
    }

    var select = tp3dApiSelect();
    if (select && select.value !== 'MAPTERHORN') {
        select.value = 'MAPTERHORN';
        select.dispatchEvent(new Event('change'));
    }
    if (typeof SETTINGS_STATE !== 'undefined' && SETTINGS_STATE.demFilePath) {
        fetch('http://127.0.0.1:' + PORT + '/update_setting', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: 'demFilePath', value: '' })
        }).catch(function() {});
        SETTINGS_STATE.demFilePath = '';
    }
}

function tp3dZoomToDemCoverage() {
    if (typeof tp3dDemLayer !== 'undefined' && tp3dDemLayer.getLayers().length) {
        map.fitBounds(tp3dDemLayer.getBounds(), { padding: [40, 40] });
    }
}

function tp3dMapTutorialLocalDem() {
    return [
        {
            target: '.settings-gear-btn',
            placement: 'bottom',
            title: 'Use local Datasets (GeoTiff)',
            text: 'A <b>GeoTIFF DEM</b> is an image file where every pixel holds a <b>height</b> instead of a color, ' +
                  'and that knows exactly where on Earth it lies.<br><br>' +
                  'Many national mapping agencies offer them for free, often far more detailed than the online ' +
                  'elevation services. With your own file, the terrain is read from it instead of being downloaded.'
        },
        {
            target: '.settings-gear-btn',
            placement: 'bottom',
            title: 'Open the settings',
            text: 'Click <b>Settings</b>.',
            waitFor: tp3dTutorial.waitForClick('.settings-gear-btn'),
            waitText: 'Click Settings',
            skipIf: tp3dSettingsOpen
        },
        {
            target: function() { return document.getElementById('settings-tab-other'); },
            placement: 'bottom',
            title: 'The Other tab',
            text: 'The elevation source is set on the <b>Other</b> tab. Click it.',
            waitFor: tp3dTutorial.waitUntil(tp3dOtherTabActive),
            waitText: 'Click Other',
            skipIf: tp3dOtherTabActive
        },
        {
            target: tp3dApiRow,
            placement: 'bottom',
            title: 'Select Local DEM File',
            text: '<b>Elevation API</b> picks where the terrain heights come from. ' +
                  'Set it to <b>Local DEM File</b>.',
            onEnter: tp3dShowOtherTab,
            waitFor: tp3dTutorial.waitUntil(tp3dIsLocalDem),
            waitText: 'Select Local DEM File',
            skipIf: tp3dIsLocalDem
        },
        {
            target: tp3dDemPathRow,
            placement: 'bottom',
            title: 'Pick your file',
            text: 'Click <b>File…</b> to pick a GeoTIFF (.tif) file, or <b>Folder…</b> for a folder of tiles ' +
                  'covering a larger area. Only the tiles your map needs are read.<br><br>' +
                  'Supported are WGS84 lat/lon, UTM, and Swiss LV95 files.<br><br>' +
                  'No file at hand? Try our sample between Lake Como and Lake Lugano.',
            onEnter: tp3dShowOtherTab,
            buttons: [{ label: 'Use sample GeoTIFF', onClick: tp3dUseSampleDem }],
            waitFor: tp3dTutorial.waitUntil(tp3dHasDemCoverage),
            waitText: 'Pick a GeoTIFF file'
        },
        {
            target: tp3dDemPathRow,
            placement: 'bottom',
            title: 'Format check',
            text: 'Below the path you see whether the file can be used. <b>Supported format</b> means it is ready.',
            onEnter: tp3dShowOtherTab
        },
        {
            target: tp3dInSettings('.tp3d-modal-close-btn'),
            title: 'Close the settings',
            text: 'Your changes are saved right away. Close the settings to continue.',
            waitFor: tp3dTutorial.waitUntil(function() { return !tp3dSettingsOpen(); }),
            waitText: 'Close the settings',
            skipIf: function() { return !tp3dSettingsOpen(); }
        },
        {
            target: '#map',
            title: 'The covered area',
            text: 'The <b>green dashed outline</b> shows the area your file covers. ' +
                  'It is also listed as <b>DEM coverage</b> in the legend.<br><br>' +
                  'Keep your map inside it: <b>outside the file, the terrain comes out flat</b>.',
            onEnter: tp3dZoomToDemCoverage,
            skipIf: function() { return !tp3dHasDemCoverage(); }
        },
        {
            target: '#map',
            title: 'Draw your area inside',
            text: 'We placed an area inside the coverage for you. Move or resize it as you like, ' +
                  'just keep it within the green outline.',
            onEnter: function() {
                if (!tp3dHasDrawnArea()) tp3dPlaceSampleArea();
                tp3dZoomToDemCoverage();
            },
            interactive: true
        },
        {
            target: ['#send', '.export-folder-pick'],
            placement: 'right',
            title: 'Generate your map',
            text: 'Click <b>Generate</b>. The terrain now comes from your file.<br><br>' +
                  'The setting stays on until you switch the <b>Elevation API</b> back in the settings.',
            waitFor: tp3dTutorial.waitForClick('#send'),
            waitText: 'Click Generate',
            skippable: true
        }
    ];
}

// ---- Puzzle Generator: "Create a 4 x 4 Jigsaw Puzzle" ----

// Mount Fuji -- a single big cone with its summit crater, centered in the
// puzzle. VIEW is where the map starts, AREA the ~20 km square "Draw it for
// me" places around the summit.
var TP3D_FUJI_VIEW_BOUNDS = [[35.20, 138.52], [35.52, 138.94]];
var TP3D_FUJI_AREA_BOUNDS = [[35.27, 138.617], [35.45, 138.837]];

// The grid the tutorial asks for -- not the page's 3 x 3 default, so the
// grid step has something to do.
var TP3D_PUZZLE_COLS = 4;
var TP3D_PUZZLE_ROWS = 4;

function tp3dPuzzleGridValue(id) {
    return parseInt(document.getElementById(id).value) || 0;
}
function tp3dPuzzleIs4x4() {
    return tp3dPuzzleGridValue('colsInput') === TP3D_PUZZLE_COLS &&
        tp3dPuzzleGridValue('rowsInput') === TP3D_PUZZLE_ROWS;
}

// Square pieces: always on the free page (its other shapes are a locked
// preview); on the premium page, whichever piece-shape button is active.
function tp3dPuzzleIsSquare() {
    return typeof puzzleShape === 'undefined' || puzzleShape === 'square';
}

function tp3dPuzzleSetInput(id, value) {
    var el = document.getElementById(id);
    if (!el || String(el.value) === String(value)) return;
    el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
}

// Same idea as tp3dResetMapGenerator, for the puzzle pages: drives the
// page's own controls (delete / Clear buttons, piece-shape and draw-mode
// buttons, inputs, layer radios) back to what a first launch shows. Leaves
// the Settings popup's Puzzle tab values (tab size, jitter, ...) alone, like
// the map reset leaves the scene settings alone.
function tp3dResetPuzzleGenerator() {
    function clickIfShown(id) {
        var el = document.getElementById(id);
        if (el && el.style.display !== 'none') el.click();
    }

    var m = tp3dSettingsModal();
    if (m) m.classList.remove('open');
    if (typeof drawArmed !== 'undefined' && drawArmed) disarmDraw();
    if (tp3dHasDrawnArea()) document.getElementById('deleteBtn').click();
    clickIfShown('clearGpx');
    clickIfShown('clearPrefetch');

    if (!tp3dPuzzleIsSquare()) document.getElementById('shapeSquareBtn').click();
    var cornerBtn = document.querySelector('.draw-mode-btn[data-mode="corner"]');
    if (cornerBtn && !cornerBtn.classList.contains('active')) cornerBtn.click();
    // The sliding puzzle page has no holder checkbox -- its frame is always generated.
    var holder = document.getElementById('holderEnabled');
    if (holder && holder.checked) holder.click();
    // Start on OSM with no colored elements, so the ESA step has a switch to
    // teach. Switching reloads OSM's own element states from Blender, so
    // they're turned off once that's done.
    var switching = tp3dIsOsm() ? null : tp3dSwitchElementSource('OSM');
    if (switching) switching.then(function() { tp3dAllElementsOff(true); });
    else tp3dAllElementsOff(true);

    // Same defaults the page itself starts with: the static HTML's values.
    var inputs = ['lengthInput', 'widthInput', 'colsInput', 'rowsInput', 'resolutionSlider'];
    if (tp3dIsSlidingPuzzlePage()) inputs.push('cornerRadiusInput', 'toleranceInput');
    inputs.forEach(function(id) {
        tp3dPuzzleSetInput(id, document.getElementById(id).defaultValue);
    });

    document.querySelectorAll('.leaflet-control-layers-base label').forEach(function(label) {
        var input = label.querySelector('input');
        if (input && !input.checked && label.textContent.trim() === 'OpenStreetMap') input.click();
    });
}

// "Draw it for me": places the area around the summit through the page's
// own placeShapeAroundBounds (rect_editor.js), same as a GPX import does.
function tp3dPlaceFujiArea() {
    if (typeof drawArmed !== 'undefined' && drawArmed) disarmDraw();
    placeShapeAroundBounds(L.latLngBounds(TP3D_FUJI_AREA_BOUNDS), 1);
    if (rectLayer) map.fitBounds(rectLayer.getBounds(), { padding: [50, 50] });
}

// waitFor for the pencil step: the pencil click, or an area appearing
// through "Draw it for me" instead (tutorial.js ignores the second done()).
function tp3dWaitForPencilOrArea() {
    var click = tp3dTutorial.waitForClick('#drawBtn');
    var area = tp3dTutorial.waitUntil(tp3dHasDrawnArea);
    return function(done, api) {
        var stopClick = click(done, api), stopArea = area(done, api);
        return function() { stopClick(); stopArea(); };
    };
}

// ESA WorldCover elements the puzzle tutorial asks for: forest on Fuji's
// lower slopes, bare rock ("Mountain") on the upper cone.
var TP3D_PUZZLE_ELEMENTS = ['forest', 'mountain'];

function tp3dPuzzleElementsOn() {
    return !tp3dIsOsm() && TP3D_PUZZLE_ELEMENTS.every(function(key) { return !!TP3D_ELEMENT_STATE[key]; });
}

// Sample cursor clicks the first of those chips that's still off.
function tp3dDemoPuzzleElements() {
    for (var i = 0; i < TP3D_PUZZLE_ELEMENTS.length; i++) {
        var key = TP3D_PUZZLE_ELEMENTS[i];
        if (TP3D_ELEMENT_STATE[key]) continue;
        var chip = document.querySelector('#elementStatus .element-chip[data-element-toggle="' + key + '"]');
        if (!chip) return null;
        var r = chip.getBoundingClientRect();
        return [{ x: r.left + 18, y: r.top + r.height / 2 }];
    }
    return null;
}

// Center of the ESA side of the OSM | ESA switch.
function tp3dDemoClickEsa() {
    var btns = document.querySelectorAll('.element-source-switch-compact .source-switch-btn');
    var esa = btns[btns.length - 1];
    if (!esa) return null;
    var r = esa.getBoundingClientRect();
    return [{ x: r.left + r.width / 2, y: r.top + r.height / 2 }];
}

// The holder (jigsaw) / frame (sliding) fields, below the divider on the
// Settings popup's Puzzle tab (settings_modal.js's tp3dBuildPuzzleTab).
function tp3dPlateTabPanel() { return document.getElementById('plateTabFields'); }
function tp3dShowPlateTab() { if (window.tp3dActivateSettingsTab) window.tp3dActivateSettingsTab('puzzle'); }

function tp3dIsTopoLayer() {
    return typeof activeBaseLayerName !== 'undefined' && activeBaseLayerName === 'Topographic';
}

// Demo for the layer step: clicks the Topographic entry of the (held open)
// layers control.
function tp3dDemoClickTopo() {
    var pt = null;
    document.querySelectorAll('.leaflet-control-layers-base label').forEach(function(label) {
        if (label.textContent.trim() !== 'Topographic') return;
        var r = label.getBoundingClientRect();
        if (r.width) pt = { x: r.left + 12, y: r.top + r.height / 2 };
    });
    return pt ? [pt] : null;
}

// Through the page's own inputs, so the pieces on the map redraw as if the
// user typed it.
function tp3dPuzzleSet4x4() {
    tp3dPuzzleSetInput('colsInput', TP3D_PUZZLE_COLS);
    tp3dPuzzleSetInput('rowsInput', TP3D_PUZZLE_ROWS);
}

// Live hint under the grid step's text: the current grid and piece count.
// Refreshed by the step's own timer.
var tp3dPuzzleGridHintTimer = null;
function tp3dPuzzleGridHint() {
    var el = document.getElementById('tp3dPuzzleGridHint');
    if (!el) return;
    var c = tp3dPuzzleGridValue('colsInput'), r = tp3dPuzzleGridValue('rowsInput');
    el.innerHTML = 'Now: <b>' + c + ' × ' + r + ' = ' + (c * r) + ' pieces</b>';
}
function tp3dPuzzleStartGridHint() {
    tp3dPuzzleStopGridHint();
    tp3dPuzzleGridHint();
    tp3dPuzzleGridHintTimer = setInterval(tp3dPuzzleGridHint, 200);
}
function tp3dPuzzleStopGridHint() {
    if (tp3dPuzzleGridHintTimer) { clearInterval(tp3dPuzzleGridHintTimer); tp3dPuzzleGridHintTimer = null; }
}

function tp3dPuzzleTutorialCreate() {
    return [
        {
            target: '#map',
            title: 'Create a jigsaw puzzle',
            text: 'We moved the map to <b>Mount Fuji</b> in Japan. Its big cone makes for great terrain.<br><br>' +
                  'You can exit this tutorial at any time with <b>✕</b> or <b>Esc</b>.'
        },
        {
            target: function() { return document.getElementById('shapeSquareBtn').closest('.field-row'); },
            placement: 'right',
            title: 'Piece shape',
            text: '<b>Square</b> pieces make a classic jigsaw puzzle with tabs on every inner edge.<br><br>' +
                  'We use square pieces for this tutorial.'
        },
        {
            target: '#drawBtn',
            placement: 'right',
            title: 'Draw your area',
            text: 'Draw your area first, so you can see every setting change on the map right away.<br><br>' +
                  'Click the <b>pencil</b> to start drawing.',
            waitFor: tp3dWaitForPencilOrArea(),
            waitText: 'Click the pencil',
            buttons: [{ label: 'Draw it for me', onClick: tp3dPlaceFujiArea }],
            skipIf: tp3dHasDrawnArea
        },
        {
            target: '#map',
            title: 'Draw your area',
            text: 'Click and drag over the mountain to draw your area, then let go of the mouse. ' +
                  'Keep the summit in the middle.<br><br>' +
                  'If nothing happens, click the pencil again.',
            demo: tp3dDemoDrawArea,
            demoShape: 'rect',
            demoGhostColor: TP3D_DRAWING_BLUE,
            // Polled rather than waiting for the draw event, so "Draw it
            // for me" (which places the area without one) counts too.
            waitFor: tp3dTutorial.waitUntil(tp3dHasDrawnArea),
            waitText: 'Draw on the map',
            buttons: [{ label: 'Draw it for me', onClick: tp3dPlaceFujiArea }],
            skipIf: tp3dHasDrawnArea
        },
        {
            target: '.leaflet-control-layers',
            title: 'See the terrain',
            text: 'Click <b>Topographic</b> to see the mountains and valleys in your area.',
            demo: tp3dDemoClickTopo,
            waitFor: tp3dTutorial.waitUntil(tp3dIsTopoLayer),
            waitText: 'Click Topographic',
            skippable: true,
            skipIf: tp3dIsTopoLayer,
            onEnter: tp3dHoldLayersOpen,
            onExit: tp3dReleaseLayersOpen
        },
        {
            target: '#sizeRowRect',
            interactive: true,
            placement: 'right',
            title: 'Set the size',
            text: 'This is the size of the <b>whole puzzle</b> in <b>mm</b>, length × width.<br><br>' +
                  'We keep <b>100 × 100</b> for this tutorial.'
        },
        {
            target: '#gridRowRect',
            placement: 'right',
            title: 'Set the grid',
            text: '<b>Grid</b> sets how many pieces your puzzle has: columns × rows. The blue shapes on the ' +
                  'map are your pieces.<br><br>' +
                  'Set it to <b>4 × 4</b>.<br><br>' +
                  '<span id="tp3dPuzzleGridHint"></span>',
            waitFor: tp3dTutorial.waitUntil(tp3dPuzzleIs4x4),
            waitText: 'Set the grid to 4 × 4',
            buttons: [{ label: 'Set 4 × 4 for me', onClick: tp3dPuzzleSet4x4 }],
            skipIf: tp3dPuzzleIs4x4,
            onEnter: tp3dPuzzleStartGridHint,
            onExit: tp3dPuzzleStopGridHint
        },
        {
            target: '#map',
            interactive: true,
            title: 'Frame the peak',
            text: 'Drag the handle in the middle of the box to move your area, or a corner to resize it, ' +
                  'until the summit sits in the <b>middle</b> of the puzzle.',
            demo: tp3dDemoMoveShape,
            demoGhost: tp3dDemoMoveGhost,
            demoGhostColor: TP3D_DRAWN_ORANGE
        },
        {
            target: function() { return document.getElementById('resolutionSlider').parentElement; },
            placement: 'right',
            title: 'Resolution',
            text: '<b>Resolution</b> sets how much terrain detail your puzzle gets. Higher values look ' +
                  'sharper but take longer to generate.<br><br>' +
                  'We keep <b>8</b>, so the ridges of the mountain come out crispy clean.'
        },
        {
            target: '.element-source-switch-compact',
            placement: 'bottom',
            title: 'Color the terrain with ESA',
            text: 'Enabling elements adds colors to your maps based on real world data. For this example ' +
                  'we use <b>ESA</b> as a data source.<br><br>' +
                  '<b>ESA</b> is made from satellite images and works for areas of any size, great for ' +
                  'natural landscapes like this one.<br><br>' +
                  'Click <b>ESA</b>.',
            demo: tp3dDemoClickEsa,
            waitFor: tp3dTutorial.waitUntil(function() { return !tp3dIsOsm(); }),
            waitText: 'Click ESA',
            skippable: true,
            skipIf: function() { return !tp3dIsOsm(); }
        },
        {
            target: function() {
                return TP3D_PUZZLE_ELEMENTS.map(tp3dElementChipWrap).filter(Boolean);
            },
            placement: 'bottom',
            title: 'Turn on Forest and Mountain',
            text: 'Mount Fuji has <b>forest</b> on its lower slopes and bare rock, <b>Mountain</b>, on top.<br><br>' +
                  'Click <b>Forest</b> and <b>Mountain</b> to switch them on. Green means on.',
            // Starts from nothing, whatever ESA had switched on before.
            onEnter: function() { tp3dAllElementsOff(true); },
            demo: tp3dDemoPuzzleElements,
            waitFor: tp3dTutorial.waitUntil(tp3dPuzzleElementsOn),
            waitText: 'Turn on Forest and Mountain',
            skippable: true,
            skipIf: tp3dIsOsm
        },
        {
            target: function() { return document.getElementById('holderEnabled').closest('.control-stack'); },
            interactive: true,
            placement: 'right',
            title: 'Add a holder',
            text: 'Tick <b>Generate Holder</b> to also get a tray that holds the finished puzzle.<br><br>' +
                  'This is optional.'
        },
        {
            target: '.settings-gear-btn',
            placement: 'bottom',
            title: 'Holder settings',
            text: 'The holder\'s frame, corners and text are in the settings, on the <b>Puzzle</b> tab.<br><br>' +
                  'Click <b>Settings</b> to have a look. This is optional.',
            waitFor: tp3dTutorial.waitForClick('.settings-gear-btn'),
            waitText: 'Click Settings',
            skippable: true,
            skipIf: tp3dSettingsOpen
        },
        // Only shown when the settings were opened on the step above.
        {
            target: tp3dPlateTabPanel,
            interactive: true,
            title: 'Plate settings',
            text: 'Set the holder\'s frame thickness, corner radius and text here. They\'re greyed out ' +
                  'until <b>Generate Holder</b> is ticked.',
            onEnter: tp3dShowPlateTab,
            skipIf: function() { return !tp3dSettingsOpen(); }
        },
        {
            target: tp3dInSettings('.tp3d-modal-close-btn'),
            title: 'Close the settings',
            text: 'Your changes are saved right away. Close the settings to continue.',
            waitFor: tp3dTutorial.waitUntil(function() { return !tp3dSettingsOpen(); }),
            waitText: 'Close the settings',
            skipIf: function() { return !tp3dSettingsOpen(); }
        },
        {
            // Whichever is visible: Select Export Folder stands in for Send until a folder is set (export_folder.js).
            target: ['#send', '.export-folder-pick'],
            placement: 'right',
            title: 'Generate your puzzle',
            text: 'Click <b>Generate</b> to generate your 16 puzzle pieces.',
            waitFor: tp3dTutorial.waitForClick('#send'),
            waitText: 'Click Generate',
            skippable: true
        }
    ];
}

var TP3D_PUZZLE_TUTORIAL_MENU = {
    button: '#tutorialBtn',
    title: 'Puzzle Generator Tutorials',
    // Free + premium puzzle generator share it -- offered once for either.
    offerId: 'offer-puzzle-generator',
    offerTitle: 'Puzzle tutorial',
    offerText: '<b>New to the Puzzle Generator?</b> An <b>interactive tutorial</b> walks you through ' +
               'making a <b>4 × 4 jigsaw puzzle</b> of Mount Fuji: drawing the area, setting the grid, ' +
               'coloring the terrain and adding a holder, right on this page.<br><br>It\'s completely ' +
               '<b>optional</b> — click <b>Tutorials</b> to start it, or skip it and build your own ' +
               'puzzle. The button stays here at the top of the map for later.',
    tutorials: [
        {
            id: 'puzzle-create',
            title: 'Create a 4 x 4 Jigsaw Puzzle',
            description: 'Turn Mount Fuji into a 16-piece 3D terrain puzzle.',
            prepare: function() {
                tp3dResetPuzzleGenerator();
                map.fitBounds(TP3D_FUJI_VIEW_BOUNDS);
            },
            steps: tp3dPuzzleTutorialCreate
        }
    ]
};

// ---- Sliding Puzzle Generator: "Create a 3 x 3 Sliding Puzzle" ----
// Same flow as the jigsaw one above, on premium/generators/slidingPuzzleGenerator.html
// (flagged by TP3D_IS_SLIDING_PUZZLE_PAGE): no piece shapes, an empty slot
// instead, and a frame that's always generated instead of an optional holder.

function tp3dIsSlidingPuzzlePage() {
    return typeof TP3D_IS_SLIDING_PUZZLE_PAGE !== 'undefined' && TP3D_IS_SLIDING_PUZZLE_PAGE;
}

// The classic 8-tile puzzle -- not the page's 4 x 4 default, so the grid
// step has something to do.
var TP3D_SLIDING_COLS = 3;
var TP3D_SLIDING_ROWS = 3;

function tp3dSlidingIs3x3() {
    return tp3dPuzzleGridValue('colsInput') === TP3D_SLIDING_COLS &&
        tp3dPuzzleGridValue('rowsInput') === TP3D_SLIDING_ROWS;
}

function tp3dSlidingSet3x3() {
    tp3dPuzzleSetInput('colsInput', TP3D_SLIDING_COLS);
    tp3dPuzzleSetInput('rowsInput', TP3D_SLIDING_ROWS);
}

// Live hint under the grid step's text, like tp3dPuzzleGridHint: one cell
// always stays empty, so it's one tile less than the grid has cells.
var tp3dSlidingGridHintTimer = null;
function tp3dSlidingGridHint() {
    var el = document.getElementById('tp3dSlidingGridHint');
    if (!el) return;
    var c = tp3dPuzzleGridValue('colsInput'), r = tp3dPuzzleGridValue('rowsInput');
    el.innerHTML = 'Now: <b>' + c + ' × ' + r + ' = ' + Math.max(0, c * r - 1) + ' tiles</b> + 1 empty slot';
}
function tp3dSlidingStartGridHint() {
    tp3dSlidingStopGridHint();
    tp3dSlidingGridHint();
    tp3dSlidingGridHintTimer = setInterval(tp3dSlidingGridHint, 200);
}
function tp3dSlidingStopGridHint() {
    if (tp3dSlidingGridHintTimer) { clearInterval(tp3dSlidingGridHintTimer); tp3dSlidingGridHintTimer = null; }
}

// Sample cursor for the empty-slot step: clicks the center of the top-left
// tile (row 0 is the south row, so the top row is the highest index).
function tp3dDemoClickSlidingTile() {
    if (typeof pieces === 'undefined' || !pieces.length) return null;
    var top = Math.max.apply(null, pieces.map(function(p) { return p.row; }));
    var tile = pieces.filter(function(p) { return p.row === top && p.col === 0; })[0];
    if (!tile) return null;
    var x = 0, y = 0;
    tile.points.forEach(function(pt) { x += pt[0]; y += pt[1]; });
    var ll = normToLatLng(x / tile.points.length, y / tile.points.length);
    return [tp3dMapToViewport(ll[0], ll[1])];
}

function tp3dSlidingTutorialCreate() {
    return [
        {
            target: '#map',
            title: 'Create a sliding puzzle',
            text: 'We moved the map to <b>Mount Fuji</b> in Japan. Its big cone makes for great terrain.<br><br>' +
                  'A sliding puzzle has square tiles in a frame with one slot left empty, so the tiles can ' +
                  'slide around.<br><br>' +
                  'You can exit this tutorial at any time with <b>✕</b> or <b>Esc</b>.'
        },
        {
            target: '#drawBtn',
            placement: 'right',
            title: 'Draw your area',
            text: 'Draw your area first, so you can see every setting change on the map right away.<br><br>' +
                  'Click the <b>pencil</b> to start drawing.',
            waitFor: tp3dWaitForPencilOrArea(),
            waitText: 'Click the pencil',
            buttons: [{ label: 'Draw it for me', onClick: tp3dPlaceFujiArea }],
            skipIf: tp3dHasDrawnArea
        },
        {
            target: '#map',
            title: 'Draw your area',
            text: 'Click and drag over the mountain to draw your area, then let go of the mouse. ' +
                  'Keep the summit in the middle.<br><br>' +
                  'If nothing happens, click the pencil again.',
            demo: tp3dDemoDrawArea,
            demoShape: 'rect',
            demoGhostColor: TP3D_DRAWING_BLUE,
            waitFor: tp3dTutorial.waitUntil(tp3dHasDrawnArea),
            waitText: 'Draw on the map',
            buttons: [{ label: 'Draw it for me', onClick: tp3dPlaceFujiArea }],
            skipIf: tp3dHasDrawnArea
        },
        {
            target: '.leaflet-control-layers',
            title: 'See the terrain',
            text: 'Click <b>Topographic</b> to see the mountains and valleys in your area.',
            demo: tp3dDemoClickTopo,
            waitFor: tp3dTutorial.waitUntil(tp3dIsTopoLayer),
            waitText: 'Click Topographic',
            skippable: true,
            skipIf: tp3dIsTopoLayer,
            onEnter: tp3dHoldLayersOpen,
            onExit: tp3dReleaseLayersOpen
        },
        {
            target: function() { return document.getElementById('lengthInput').closest('.field-row'); },
            interactive: true,
            placement: 'right',
            title: 'Set the size',
            text: 'This is the size of the <b>whole puzzle</b> in <b>mm</b>, length × width. The frame ' +
                  'goes around it.<br><br>' +
                  'We keep <b>100 × 100</b> for this tutorial.'
        },
        {
            target: function() { return document.getElementById('colsInput').closest('.field-row'); },
            placement: 'right',
            title: 'Set the grid',
            text: '<b>Grid</b> sets how many tiles your puzzle has: columns × rows. The blue squares on the ' +
                  'map are your tiles.<br><br>' +
                  'Set it to <b>3 × 3</b>.<br><br>' +
                  '<span id="tp3dSlidingGridHint"></span>',
            waitFor: tp3dTutorial.waitUntil(tp3dSlidingIs3x3),
            waitText: 'Set the grid to 3 × 3',
            buttons: [{ label: 'Set 3 × 3 for me', onClick: tp3dSlidingSet3x3 }],
            skipIf: tp3dSlidingIs3x3,
            onEnter: tp3dSlidingStartGridHint,
            onExit: tp3dSlidingStopGridHint
        },
        {
            target: '#map',
            interactive: true,
            title: 'Pick the empty slot',
            text: 'The <b>red dashed</b> tile is the empty slot. It isn\'t generated, so the other tiles ' +
                  'have room to slide.<br><br>' +
                  'Click any tile to make it the empty one instead. This is optional.',
            demo: tp3dDemoClickSlidingTile
        },
        {
            target: '#map',
            interactive: true,
            title: 'Frame the peak',
            text: 'Drag the handle in the middle of the box to move your area, or a corner to resize it, ' +
                  'until the summit sits in the <b>middle</b> of the puzzle.',
            demo: tp3dDemoMoveShape,
            demoGhost: tp3dDemoMoveGhost,
            demoGhostColor: TP3D_DRAWN_ORANGE
        },
        {
            target: '.settings-gear-btn',
            placement: 'bottom',
            title: 'Corners and tolerance',
            text: '<b>Corner Radius</b> and <b>Tolerance</b> are in the settings.<br><br>' +
                  'Click <b>Settings</b>.',
            waitFor: tp3dTutorial.waitForClick('.settings-gear-btn'),
            waitText: 'Click Settings',
            skippable: true,
            skipIf: tp3dSettingsOpen
        },
        // Only shown when the settings were opened on the step above.
        {
            target: function() {
                return ['#cornerRadiusInput', '#toleranceInput'].map(function(sel) {
                    var input = tp3dInSettings(sel)();
                    return input && input.closest('.field-row');
                }).filter(Boolean);
            },
            interactive: true,
            title: 'Corners and tolerance',
            text: '<b>Corner Radius</b> rounds the corners of every tile, so they slide past each ' +
                  'other more smoothly.<br><br>' +
                  '<b>Tolerance</b> is the gap between the tiles and to the frame. Too small and they ' +
                  'stick; too big and they wobble. I use <b>0.3 mm</b> on my Bambu Lab printers for ' +
                  'the best results.<br><br>' +
                  'We keep the defaults for this tutorial.',
            onEnter: function() { if (window.tp3dActivateSettingsTab) window.tp3dActivateSettingsTab('puzzle'); },
            skipIf: function() { return !tp3dSettingsOpen(); }
        },
        {
            target: tp3dInSettings('.tp3d-modal-close-btn'),
            title: 'Close the settings',
            text: 'Your changes are saved right away. Close the settings to continue.',
            waitFor: tp3dTutorial.waitUntil(function() { return !tp3dSettingsOpen(); }),
            waitText: 'Close the settings',
            skipIf: function() { return !tp3dSettingsOpen(); }
        },
        {
            target: function() { return document.getElementById('resolutionSlider').parentElement; },
            placement: 'right',
            title: 'Resolution',
            text: '<b>Resolution</b> sets how much terrain detail your puzzle gets. Higher values look ' +
                  'sharper but take longer to generate.<br><br>' +
                  'We keep <b>8</b>, so the ridges of the mountain come out crispy clean.'
        },
        {
            target: '.element-source-switch-compact',
            placement: 'bottom',
            title: 'Color the terrain with ESA',
            text: 'Enabling elements adds colors to your maps based on real world data. For this example ' +
                  'we use <b>ESA</b> as a data source.<br><br>' +
                  '<b>ESA</b> is made from satellite images and works for areas of any size, great for ' +
                  'natural landscapes like this one.<br><br>' +
                  'Click <b>ESA</b>.',
            demo: tp3dDemoClickEsa,
            waitFor: tp3dTutorial.waitUntil(function() { return !tp3dIsOsm(); }),
            waitText: 'Click ESA',
            skippable: true,
            skipIf: function() { return !tp3dIsOsm(); }
        },
        {
            target: function() {
                return TP3D_PUZZLE_ELEMENTS.map(tp3dElementChipWrap).filter(Boolean);
            },
            placement: 'bottom',
            title: 'Turn on Forest and Mountain',
            text: 'Mount Fuji has <b>forest</b> on its lower slopes and bare rock, <b>Mountain</b>, on top.<br><br>' +
                  'Click <b>Forest</b> and <b>Mountain</b> to switch them on. Green means on.',
            onEnter: function() { tp3dAllElementsOff(true); },
            demo: tp3dDemoPuzzleElements,
            waitFor: tp3dTutorial.waitUntil(tp3dPuzzleElementsOn),
            waitText: 'Turn on Forest and Mountain',
            skippable: true,
            skipIf: tp3dIsOsm
        },
        {
            target: '.settings-gear-btn',
            placement: 'bottom',
            title: 'The frame',
            text: 'Every sliding puzzle comes with a <b>frame</b> that keeps the tiles together. The dashed ' +
                  'black line on the map is its outline.<br><br>' +
                  'Its thickness, corners and text are in the settings, on the <b>Puzzle</b> tab. ' +
                  'Click <b>Settings</b> to have a look. This is optional.',
            waitFor: tp3dTutorial.waitForClick('.settings-gear-btn'),
            waitText: 'Click Settings',
            skippable: true,
            skipIf: tp3dSettingsOpen
        },
        // Only shown when the settings were opened on the step above.
        {
            target: tp3dPlateTabPanel,
            interactive: true,
            title: 'Frame settings',
            text: 'Set the frame\'s thickness, corner radius and text here. <b>Terrain on Frame</b> ' +
                  'continues the map onto the frame.',
            onEnter: tp3dShowPlateTab,
            skipIf: function() { return !tp3dSettingsOpen(); }
        },
        {
            target: tp3dInSettings('.tp3d-modal-close-btn'),
            title: 'Close the settings',
            text: 'Your changes are saved right away. Close the settings to continue.',
            waitFor: tp3dTutorial.waitUntil(function() { return !tp3dSettingsOpen(); }),
            waitText: 'Close the settings',
            skipIf: function() { return !tp3dSettingsOpen(); }
        },
        {
            target: ['#send', '.export-folder-pick'],
            placement: 'right',
            title: 'Generate your puzzle',
            text: 'Click <b>Generate</b> to generate your 8 tiles and the frame.',
            waitFor: tp3dTutorial.waitForClick('#send'),
            waitText: 'Click Generate',
            skippable: true
        }
    ];
}

var TP3D_SLIDING_TUTORIAL_MENU = {
    button: '#tutorialBtn',
    title: 'Sliding Puzzle Tutorials',
    offerId: 'offer-sliding-puzzle-generator',
    offerTitle: 'Sliding puzzle tutorial',
    offerText: '<b>New to the Sliding Puzzle Generator?</b> An <b>interactive tutorial</b> walks you ' +
               'through making a <b>3 × 3 sliding puzzle</b> of Mount Fuji: drawing the area, setting the ' +
               'grid and empty slot, coloring the terrain and setting up the frame, right on this page.' +
               '<br><br>It\'s completely <b>optional</b> — click <b>Tutorials</b> to start it, or skip it ' +
               'and build your own puzzle. The button stays here at the top of the map for later.',
    tutorials: [
        {
            id: 'sliding-puzzle-create',
            title: 'Create a 3 x 3 Sliding Puzzle',
            description: 'Turn Mount Fuji into an 8-tile 3D terrain sliding puzzle.',
            prepare: function() {
                tp3dResetPuzzleGenerator();
                map.fitBounds(TP3D_FUJI_VIEW_BOUNDS);
            },
            steps: tp3dSlidingTutorialCreate
        }
    ]
};

var TP3D_MAP_TUTORIAL_MENU = {
    button: '#tutorialBtn',
    title: 'Map Generator Tutorials',
    // Free + premium map generator share it -- offered once for either.
    offerId: 'offer-map-generator',
    tutorials: [
        {
            id: 'map-generate',
            title: 'Map Generation Basics',
            description: 'Pick any place in the world and turn it into a 3D map.',
            prepare: tp3dResetMapGenerator,
            steps: tp3dMapTutorialNoGpx
        },
        {
            id: 'map-generate-gpx',
            title: 'Adding a GPX Trail',
            description: 'Import a recorded route and print it on the terrain.',
            // Hexagon picked before the import, so the shape the import
            // places around the trail is already a hexagon.
            prepare: function() {
                tp3dResetMapGenerator();
                var hexBtn = document.querySelector('.shape-btn[data-shape="hexagon"]');
                if (hexBtn) hexBtn.click();
            },
            steps: tp3dMapTutorialGpx
        },
        {
            id: 'map-colors',
            title: 'Colorize your Map',
            description: 'Color water, roads and other elements for multi-color printing.',
            prepare: tp3dPrepareColorTutorial,
            steps: tp3dMapTutorialColors
        },
        {
            id: 'map-shapes',
            title: 'Use GeoJson or SVG as your Map Shape',
            description: 'Shape your map like a country border, or any outline from a file.',
            prepare: tp3dResetMapGenerator,
            steps: tp3dMapTutorialShapes
        },
        {
            id: 'map-prefetch',
            title: 'Prefetch and edit Elements',
            description: 'Preview roads, buildings and water on the map and leave single ones out.',
            prepare: function() {
                var m = tp3dSettingsModal();
                if (m) m.classList.remove('open');
                tp3dResetMapGenerator();
                tp3dResetPrefetch();
                tp3dAllElementsOff();
            },
            steps: tp3dMapTutorialPrefetch
        },
        {
            id: 'map-local-dem',
            title: 'Use local Datasets (GeoTiff)',
            description: 'Use your own elevation file and see which area it covers.',
            prepare: tp3dPrepareLocalDemTutorial,
            steps: tp3dMapTutorialLocalDem
        }
    ]
};

// TP3D_IS_PUZZLE_PAGE is set by both jigsaw puzzle pages (also read by
// settings_modal.js for its Puzzle tab); the sliding puzzle page sets its
// own TP3D_IS_SLIDING_PUZZLE_PAGE instead, since that tab is jigsaw-only.
tp3dTutorialMenu(tp3dIsSlidingPuzzlePage() ? TP3D_SLIDING_TUTORIAL_MENU
    : typeof TP3D_IS_PUZZLE_PAGE !== 'undefined' && TP3D_IS_PUZZLE_PAGE
        ? TP3D_PUZZLE_TUTORIAL_MENU : TP3D_MAP_TUTORIAL_MENU);
