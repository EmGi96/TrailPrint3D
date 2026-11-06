// Shape Extras (Plate/Shell + Text Layout + medal handle), shared by the map
// generator pages (map_generator.html, premium/map_generator_pe.html): a
// "Backplate" button in the sidebar opens a popup with the settings and a 2D
// top view of the result (see tp3dBuildShapeExtrasSection). Mirrors panels.py's own "Shape Extras" box
// and utils.shape_capabilities' PLATE_MODES_BY_SHAPE/TEXT_LAYOUTS_BY_SHAPE/
// valid_layouts -- which extras a shape offers, and which text layouts a
// given plate/shell mode can host, comes from those tables.
//
// The two selections are page state (saved with the page's own saveState
// snapshot and sent in the /confirm payload as 'plate_mode'/'text_layout',
// applied Blender-side only once props.shape is set -- both item lists
// depend on it, see operators.py's TP3D_OT_map_generator._apply_shape_extra).
// Every popup field is a plain scene property instead, pushed live through
// /update_setting (utils.ui_state._SETTINGS_ROW_FIELDS) and read back from
// SETTINGS_STATE, same as the Settings popup's Map tab.
//
// Expects the page to define currentShape, geojsonPaths, svgShapePath, PORT,
// SETTINGS_STATE, saveState(), clearSvgShapeImport() (rect_editor.js) and
// TP3D_SHAPE_EXTRAS_PREMIUM before this runs, plus an empty
// #shapeExtrasSection, #importGeojson, #clearGeojson and #importSvgShape in
// the sidebar. Call tp3dRefreshShapeExtras() whenever the shape or the
// GeoJSON/SVG import changes -- it also updates which shape is highlighted.

// Keyed by the page's own data-shape values ('rectangle'/'square' both map
// to SQUARE Blender-side, see TP3D_OT_map_generator._TYPE_MAP). An imported
// 'svg' / 'geojson' outline (see tp3dSelectedShape) has no entry, so the
// section hides for it: the text/plate extras are drawn for one specific
// regular shape, and Shell (utils_pe.build_map_shell) only supports convex
// outlines, which an imported one often isn't.
// Mirrors utils.shape_capabilities.PLATE_MODES_BY_SHAPE.
var PLATE_MODES_BY_SHAPE = {
    hexagon: ['NONE', 'SOLID_PLATE', 'SHELL'],
    octagon: ['NONE', 'SOLID_PLATE', 'SHELL'],
    circle: ['NONE', 'SOLID_PLATE', 'SHELL'],
    square: ['NONE', 'SOLID_PLATE', 'SHELL'],
    rectangle: ['NONE', 'SOLID_PLATE', 'SHELL']
};
// Mirrors utils.shape_capabilities.TEXT_LAYOUTS_BY_SHAPE.
var TEXT_LAYOUTS_BY_SHAPE = {
    hexagon: ['NONE', 'ON_MAP', 'OUTER_EDGE', 'FRONT_FACE'],
    octagon: ['NONE', 'OUTER_EDGE', 'FRONT_FACE'],
    circle: ['NONE', 'CURVED'],
    square: ['NONE', 'OUTER_EDGE', 'FRONT_FACE'],
    rectangle: ['NONE', 'OUTER_EDGE', 'FRONT_FACE']
};
// Mirrors utils.shape_capabilities._LAYOUTS_BY_PLATE_MODE.
var LAYOUTS_BY_PLATE_MODE = {
    NONE: { NONE: true, ON_MAP: true },
    SOLID_PLATE: { NONE: true, ON_MAP: true, OUTER_EDGE: true, FRONT_FACE: true, CURVED: true },
    SHELL: { NONE: true, ON_MAP: true, FRONT_FACE: true }
};
var LAYOUTS_REQUIRING_PLATE = { OUTER_EDGE: true, FRONT_FACE: true, CURVED: true };
var PLATE_LABELS = { NONE: 'None', SOLID_PLATE: 'Solid Plate', SHELL: 'Shell' };
var LAYOUT_LABELS = { NONE: 'None', ON_MAP: 'On Map', OUTER_EDGE: 'Outer Edge', FRONT_FACE: 'Front Face', CURVED: 'Curved' };

// Mirrors utils.text_layouts' *_FIELDS tables + _FIELD_TO_PROP/fields_for_layout,
// in the same physical (counter-clockwise) order.
var TEXT_FIELDS_BY_LAYOUT_SHAPE = {
    OUTER_EDGE: {
        HEXAGON: ['titlefield', 'textfield1', 'textfield2', 'textfield3', 'textfield4', 'textfield5'],
        OCTAGON: ['titlefield', 'textfield5', 'textfield6', 'textfield1', 'textfield2', 'textfield3', 'textfield7', 'textfield4'],
        SQUARE: ['titlefield', 'textfield1', 'textfield2', 'textfield3']
    },
    FRONT_FACE: {
        HEXAGON: ['titlefield', 'textfield1', 'textfield2', 'textfield3', 'textfield4', 'textfield5'],
        OCTAGON: ['titlefield', 'textfield1', 'textfield2', 'textfield3'],
        SQUARE: ['titlefield', 'textfield1', 'textfield2', 'textfield3']
    },
    CURVED: {
        CIRCLE: ['titlefield', 'textfield1', 'textfield2', 'textfield3']
    },
    ON_MAP: {
        HEXAGON: ['titlefield', 'textfield1', 'textfield2', 'textfield3', 'textfield4', 'textfield5']
    }
};
var FIELD_TO_ICON = {
    titlefield: 'titleIcon', textfield1: 'iconText1', textfield2: 'iconText2',
    textfield3: 'iconText3', textfield4: 'iconText4', textfield5: 'iconText5',
    textfield6: 'iconText6', textfield7: 'iconText7'
};

var SHAPE_EXTRA_TITLE_ICONS = [
    ['cycling', 'Cycling'], ['hiking', 'Hiking'], ['running', 'Running'], ['swimming', 'Swimming'],
    ['skiing', 'Skiing'], ['snowboarding', 'Snowboarding'], ['kajak', 'Kayak'], ['no', 'No Icon']
];
var SHAPE_EXTRA_TEXT_ICONS = [
    ['distance', 'Distance'], ['elevation', 'Elevation'], ['time', 'Time'],
    ['speed', 'Speed'], ['date', 'Date'], ['no', 'No Icon']
];

var plateMode = 'NONE';
var textLayout = 'NONE';

// What's actually selected: an imported GeoJSON boundary replaces the drawn
// shape outright (operators.py builds the tile from it whenever one is
// loaded), otherwise the current shape -- 'svg' after an SVG import.
function tp3dSelectedShape() {
    return (geojsonPaths && geojsonPaths.length) ? 'geojson' : currentShape;
}

function tp3dShapeExtraBase() {
    var s = tp3dSelectedShape();
    if (s === 'rectangle') s = 'square';
    return String(s || '').toUpperCase();
}

// Mirrors utils.shape_capabilities.valid_layouts.
function tp3dValidLayouts(shape, mode) {
    var shapeLayouts = TEXT_LAYOUTS_BY_SHAPE[String(shape).toLowerCase()] || ['NONE'];
    var allowed = LAYOUTS_BY_PLATE_MODE[mode] || LAYOUTS_BY_PLATE_MODE.NONE;
    return shapeLayouts.filter(function(layout) { return allowed[layout]; });
}

// Highlights whichever of the shape buttons / Import GeoJSON / Import SVG
// Shape is the current selection -- only one at a time, like the shape row.
function tp3dSyncShapeSelection() {
    var sel = tp3dSelectedShape();
    document.querySelectorAll('.shape-btn[data-shape]').forEach(function(b) {
        b.classList.toggle('active', b.getAttribute('data-shape') === sel);
    });
    document.getElementById('importGeojson').classList.toggle('import-shape-active', sel === 'geojson');
    document.getElementById('importSvgShape').classList.toggle('import-shape-active', sel === 'svg');
}

function tp3dPushShapeSetting(key, value) {
    SETTINGS_STATE[key] = value;
    tp3dDrawBackplatePreview();
    fetch('http://127.0.0.1:' + PORT + '/update_setting', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: key, value: value })
    }).catch(function() {});
}

// Installed fonts ([{name, path}]) from /list_fonts, fetched once per page
// (the first scan of the system font folders takes a moment).
var _tp3dFontList = null;
function tp3dLoadFontList() {
    if (!_tp3dFontList) {
        _tp3dFontList = fetch('http://127.0.0.1:' + PORT + '/list_fonts', { cache: 'no-store' })
            .then(function(r) { if (!r.ok) throw new Error(r.status); return r.json(); })
            .catch(function(err) { _tp3dFontList = null; throw err; });
    }
    return _tp3dFontList;
}

// The sidebar only holds a "Backplate" button (with a one-line summary); the
// settings themselves live in a popup: plate/shell + handle on the left, a 2D
// top view of the result in the middle (tp3dDrawBackplatePreview), text
// layout + texts on the right. Every field still applies immediately, same
// as before -- the popup is just where they're shown.
function tp3dBuildShapeExtrasSection() {
    var section = document.getElementById('shapeExtrasSection');
    section.innerHTML = '';

    var openBtn = document.createElement('button');
    openBtn.type = 'button';
    openBtn.id = 'backplateBtn';
    openBtn.className = 'btn-plate-settings backplate-open-btn';
    openBtn.innerHTML = '<span class="backplate-btn-title">Backplate</span><span class="backplate-btn-summary" id="backplateSummary"></span>';
    openBtn.title = 'Plate, shell, text and medal handle around the map';
    openBtn.addEventListener('click', tp3dOpenBackplateModal);
    section.appendChild(openBtn);

    var old = document.getElementById('backplateModal');
    if (old) old.remove();
    var modal = document.createElement('div');
    modal.id = 'backplateModal';
    modal.className = 'tp3d-modal backplate-modal';
    modal.innerHTML =
        '<div class="tp3d-modal-box backplate-box">' +
            '<div class="modal-header"><span>Backplate</span>' +
                '<button type="button" class="tp3d-modal-close-btn" title="Close">✕</button></div>' +
            '<div class="backplate-body shape-extras-ui">' +
                '<div class="backplate-col" id="backplateLeft"></div>' +
                '<div class="backplate-preview">' +
                    '<div class="bp-view-label">Top view</div>' +
                    '<svg id="backplateSvg" role="img" aria-label="Top view of the backplate"></svg>' +
                    '<div class="bp-view-label">Side view, from the front <span id="backplateSideNote"></span></div>' +
                    '<svg id="backplateSideSvg" role="img" aria-label="Side view of the backplate"></svg>' +
                    '<div class="backplate-caption" id="backplateCaption"></div></div>' +
                '<div class="backplate-col" id="backplateRight"></div>' +
            '</div>' +
            '<div class="backplate-footer"><span class="se-hint">Changes apply right away. Texts show sample values for codes.</span>' +
                '<button type="button" class="btn-send backplate-done">Done</button></div>' +
        '</div>';
    modal.addEventListener('click', function(ev) {
        if (ev.target === modal) modal.classList.remove('open');  // dimmed backdrop
    });
    modal.querySelector('.tp3d-modal-close-btn').addEventListener('click', function() { modal.classList.remove('open'); });
    modal.querySelector('.backplate-done').addEventListener('click', function() { modal.classList.remove('open'); });
    document.body.appendChild(modal);

    var left = modal.querySelector('#backplateLeft');
    var right = modal.querySelector('#backplateRight');

    function label(parent, text) {
        var el = document.createElement('div');
        el.className = 'section-label';
        el.textContent = text;
        parent.appendChild(el);
    }
    function box(parent, id, className, aria) {
        var el = document.createElement('div');
        el.id = id;
        el.className = className;
        if (aria) {
            el.setAttribute('role', 'group');
            el.setAttribute('aria-label', aria);
        }
        parent.appendChild(el);
        return el;
    }

    label(left, 'Plate / Shell');
    box(left, 'plateModeOptions', 'shape-extra-switch', 'Plate or shell');
    box(left, 'plateModeSettings', 'shape-extra-settings');
    box(left, 'handleSettings', 'shape-extra-settings');

    label(right, 'Text Layout');
    box(right, 'textLayoutOptions', 'shape-extra-switch', 'Text layout');
    box(right, 'textLayoutSettings', 'shape-extra-settings');
}

function tp3dOpenBackplateModal() {
    var modal = document.getElementById('backplateModal');
    if (!modal) return;
    modal.classList.add('open');
    tp3dDrawBackplatePreview();  // the map's shape/size may have changed since
}

function tp3dUpdateBackplateSummary() {
    var el = document.getElementById('backplateSummary');
    if (!el) return;
    var parts = [];
    if (plateMode !== 'NONE') parts.push(PLATE_LABELS[plateMode]);
    if (textLayout !== 'NONE') parts.push(LAYOUT_LABELS[textLayout] + ' text');
    el.textContent = parts.length ? parts.join(' · ') : 'Off';
    document.getElementById('backplateBtn').classList.toggle('backplate-on', parts.length > 0);
}

// Rebuilds both option groups for the current shape, keeping each selection if
// still valid (else None -- same rule as props.shape_update/plate_mode_update),
// and shows the Text Settings button only when a text layout is active.
function tp3dRefreshShapeExtras() {
    tp3dSyncShapeSelection();
    var section = document.getElementById('shapeExtrasSection');
    if (!section) return;
    if (!document.getElementById('plateModeOptions')) tp3dBuildShapeExtrasSection();

    var shape = tp3dShapeExtraBase();
    var validPlates = PLATE_MODES_BY_SHAPE[tp3dSelectedShape()];
    section.style.display = validPlates ? 'flex' : 'none';
    if (!validPlates) {
        var modal = document.getElementById('backplateModal');
        if (modal) modal.classList.remove('open');
        return;
    }

    if (validPlates.indexOf(plateMode) === -1 || (plateMode === 'SHELL' && !TP3D_SHAPE_EXTRAS_PREMIUM)) {
        plateMode = 'NONE';
    }
    var validLayouts = tp3dValidLayouts(shape, plateMode);
    if (validLayouts.indexOf(textLayout) === -1) textLayout = 'NONE';

    function renderOptions(container, options, selected, labels, onSelect, disabledOptions) {
        container.innerHTML = '';
        container.classList.toggle('shape-extra-grid', options.length === 4);
        options.forEach(function(ident) {
            var button = document.createElement('button');
            button.type = 'button';
            button.className = 'element-mode-btn' + (ident === selected ? ' active' : '');
            button.textContent = labels[ident];
            button.setAttribute('aria-pressed', ident === selected ? 'true' : 'false');
            button.title = ident === 'SHELL' ? 'Protective shell around the map' : labels[ident];
            if (disabledOptions && disabledOptions[ident]) {
                button.disabled = true;
                button.textContent = '🔒 ' + labels[ident];
                button.title = labels[ident] + ' is a Premium feature';
            }
            button.addEventListener('click', function() { onSelect(ident); });
            container.appendChild(button);
        });
    }

    renderOptions(document.getElementById('plateModeOptions'), validPlates, plateMode, PLATE_LABELS, function(value) {
        plateMode = value;
        tp3dRefreshShapeExtras();
        saveState();
    }, TP3D_SHAPE_EXTRAS_PREMIUM ? null : { SHELL: true });

    renderOptions(document.getElementById('textLayoutOptions'), validLayouts, textLayout, LAYOUT_LABELS, function(value) {
        textLayout = value;
        // Auto-upgrade only for layouts that need a plate to sit on.
        if (LAYOUTS_REQUIRING_PLATE[textLayout] && plateMode === 'NONE') plateMode = 'SOLID_PLATE';
        tp3dRefreshShapeExtras();
        saveState();
    });

    tp3dRenderPlateSettings(document.getElementById('plateModeSettings'));
    tp3dRenderTextSettings(document.getElementById('textLayoutSettings'));
    tp3dRenderHandleSettings(document.getElementById('handleSettings'));
    tp3dUpdateBackplateSummary();
    tp3dDrawBackplatePreview();
}

function tp3dShapeExtraNumberRow(parent, key, labelText, opts) {
    var row = document.createElement('div');
    row.className = 'field-row';
    var label = document.createElement('label');
    label.textContent = labelText;
    if (opts.title) label.title = opts.title;
    var input = document.createElement('input');
    input.type = 'number';
    input.step = opts.step;
    if (opts.min != null) input.min = opts.min;
    if (opts.max != null) input.max = opts.max;
    if (SETTINGS_STATE[key] != null) input.value = SETTINGS_STATE[key];
    input.addEventListener('change', function() {
        var v = opts.integer ? parseInt(this.value, 10) : parseFloat(this.value);
        if (isNaN(v)) { this.value = SETTINGS_STATE[key]; return; }
        if (opts.min != null) v = Math.max(opts.min, v);
        if (opts.max != null) v = Math.min(opts.max, v);
        this.value = v;
        tp3dPushShapeSetting(key, v);
    });
    // Redraw the top view while typing; Blender still gets it on 'change'.
    input.addEventListener('input', function() {
        var v = parseFloat(this.value);
        if (isNaN(v)) return;
        if (opts.min != null && v < opts.min) return;
        if (opts.max != null && v > opts.max) return;
        SETTINGS_STATE[key] = v;
        tp3dDrawBackplatePreview();
    });
    row.appendChild(label);
    row.appendChild(input);
    parent.appendChild(row);
}

function tp3dRenderTextSettings(body) {
    body.innerHTML = '';
    var shape = tp3dShapeExtraBase();
    var fields = ((TEXT_FIELDS_BY_LAYOUT_SHAPE[textLayout] || {})[shape]) || [];
    body.style.display = fields.length ? 'flex' : 'none';

    if (fields.length) {
        // Font -- a dropdown of the installed fonts (picker_server.py's
        // /list_fonts; a browser file dialog can't browse the Windows Fonts
        // folder -- it's a shell virtual folder that shows up empty there).
        var fontRow = document.createElement('div');
        fontRow.className = 'field-row';
        var fontLabel = document.createElement('label');
        fontLabel.textContent = 'Font';
        var fontSelect = document.createElement('select');
        fontSelect.className = 'se-font-select';
        function fontOption(name, path) {
            var opt = document.createElement('option');
            opt.value = path;
            opt.textContent = name;
            opt.title = path || 'Default font';
            return opt;
        }
        // Keeps the current font selectable even before/without the list (or
        // when it isn't an installed one), under its file name.
        function ensureFontOption(path) {
            if (!path) return;
            for (var i = 0; i < fontSelect.options.length; i++) {
                if (fontSelect.options[i].value === path) return;
            }
            var label = path.split(/[\\/]/).pop().replace(/\.[^.]+$/, '');
            fontSelect.insertBefore(fontOption(label, path), fontSelect.options[1] || null);
        }
        function syncFontSelect() {
            var current = SETTINGS_STATE.textFont || '';
            ensureFontOption(current);
            fontSelect.value = current;
            fontSelect.title = current || 'Default font';
        }
        fontSelect.appendChild(fontOption('Default font', ''));
        syncFontSelect();
        tp3dLoadFontList().then(function(fonts) {
            var current = SETTINGS_STATE.textFont || '';
            fonts.forEach(function(f) {
                if (f.path !== current) fontSelect.appendChild(fontOption(f.name, f.path));
                else fontSelect.options[1].textContent = f.name;  // real name for the current one
            });
            syncFontSelect();
        }).catch(function() {});
        fontSelect.addEventListener('change', function() {
            tp3dPushShapeSetting('textFont', this.value);
            this.title = this.value || 'Default font';
        });
        fontRow.appendChild(fontLabel);
        fontRow.appendChild(fontSelect);
        body.appendChild(fontRow);

        tp3dShapeExtraNumberRow(body, 'textSizeTitle', 'Title Text Size',
            { step: 1, min: 0, max: 1000, integer: true, title: "Keep 0 to use value from 'Text Size'" });
        tp3dShapeExtraNumberRow(body, 'textSize', 'Text Size', { step: 1, min: 0, max: 1000, integer: true });

        if (textLayout === 'OUTER_EDGE' || textLayout === 'CURVED' || textLayout === 'FRONT_FACE') {
            var insetRow = document.createElement('div');
            insetRow.className = 'field-row';
            var insetLabel = document.createElement('label');
            insetLabel.textContent = 'Inset Text';
            insetLabel.title = 'Carve text into the plate surface instead of raising it';
            var insetInput = document.createElement('input');
            insetInput.type = 'checkbox';
            insetInput.checked = !!SETTINGS_STATE.textPlacement;
            insetInput.addEventListener('change', function() { tp3dPushShapeSetting('textPlacement', this.checked); });
            insetRow.appendChild(insetLabel);
            insetRow.appendChild(insetInput);
            body.appendChild(insetRow);
        }

        var textLabel = document.createElement('div');
        textLabel.className = 'se-section-label';
        textLabel.textContent = 'Text (Goes Counter-Clockwise) ↺';
        body.appendChild(textLabel);

        fields.forEach(function(textProp) {
            var iconProp = FIELD_TO_ICON[textProp];
            var icons = textProp === 'titlefield' ? SHAPE_EXTRA_TITLE_ICONS : SHAPE_EXTRA_TEXT_ICONS;
            var row = document.createElement('div');
            row.className = 'se-text-row';
            if (TP3D_SHAPE_EXTRAS_PREMIUM) {
                var iconSel = document.createElement('select');
                icons.forEach(function(ic) {
                    var opt = document.createElement('option');
                    opt.value = ic[0];
                    opt.textContent = ic[1];
                    iconSel.appendChild(opt);
                });
                iconSel.value = SETTINGS_STATE[iconProp] || 'no';
                iconSel.addEventListener('change', function() { tp3dPushShapeSetting(iconProp, this.value); });
                row.appendChild(iconSel);
            } else {
                var locked = document.createElement('button');
                locked.type = 'button';
                locked.className = 'se-icon-locked';
                locked.disabled = true;
                locked.title = 'Text icons are a Premium feature';
                locked.textContent = '🔒 Icon';
                row.appendChild(locked);
            }
            var textInput = document.createElement('input');
            textInput.type = 'text';
            textInput.value = SETTINGS_STATE[textProp] != null ? SETTINGS_STATE[textProp] : '';
            textInput.title = 'Codes = {name} | {length} | {elevation} | {duration} | {date} | {speed} | {scale}';
            textInput.addEventListener('change', function() { tp3dPushShapeSetting(textProp, this.value); });
            textInput.addEventListener('input', function() {
                SETTINGS_STATE[textProp] = this.value;  // pushed on 'change'
                tp3dDrawBackplatePreview();
            });
            row.appendChild(textInput);
            body.appendChild(row);
        });

        var hint = document.createElement('div');
        hint.className = 'se-hint';
        hint.textContent = 'Codes: {name} {length} {elevation} {duration} {date} {speed} {scale}';
        body.appendChild(hint);
    }

}

function tp3dRenderPlateSettings(body) {
    body.innerHTML = '';
    body.style.display = plateMode === 'NONE' ? 'none' : 'flex';
    if (plateMode === 'NONE') return;

    if (plateMode === 'SOLID_PLATE') {
        tp3dShapeExtraNumberRow(body, 'plateThickness', 'Plate Height (mm)', { step: 0.5 });
        tp3dShapeExtraNumberRow(body, 'outerBorderSize', 'Border Thickness (%)',
            { step: 1, min: 0, max: 1000, integer: true, title: 'How thick the border is, as a percentage of the plate size' });
        tp3dShapeExtraNumberRow(body, 'plateInsertValue', 'Map Inset Depth (mm)',
            { step: 0.5, title: 'How far down the map object should be in the plate' });
        tp3dShapeExtraNumberRow(body, 'plateBevel', 'Plate Bevel',
            { step: 0.1, min: 0, max: 50, title: 'Bevel the top and bottom edges of the plate (0 to disable)' });
    } else {
        tp3dShapeExtraNumberRow(body, 'plateThickness', 'Shell Extra Height', { step: 0.5 });
        tp3dShapeExtraNumberRow(body, 'shellWallThickness', 'Shell Width', { step: 0.1, min: 0.1 });
    }

}

function tp3dRenderHandleSettings(body) {
    body.innerHTML = '';
    body.style.display = plateMode === 'NONE' ? 'none' : 'flex';
    if (plateMode === 'NONE' || plateMode === 'SHELL') return;

    var handleLabel = document.createElement('div');
    handleLabel.className = 'se-section-label';
    handleLabel.textContent = 'Medal Handle';
    body.appendChild(handleLabel);

    var handleRow = document.createElement('div');
    handleRow.className = 'field-row';
    var handleSwitch = document.createElement('div');
    handleSwitch.className = 'se-handle-switch';
    var current = TP3D_SHAPE_EXTRAS_PREMIUM ? (SETTINGS_STATE.handleStyle || 'NONE') : 'NONE';
    [['NONE', 'None'], ['ROUND', 'Round'], ['FLAT', 'Flat']].forEach(function(h) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'element-mode-btn' + (current === h[0] ? ' active' : '');
        b.textContent = h[1];
        if (h[0] !== 'NONE' && !TP3D_SHAPE_EXTRAS_PREMIUM) {
            b.disabled = true;
            b.textContent = '🔒 ' + h[1];
            b.title = 'Medal handles are a Premium feature';
        }
        b.addEventListener('click', function() {
            tp3dPushShapeSetting('handleStyle', h[0]);
            handleSwitch.querySelectorAll('button').forEach(function(x) { x.classList.toggle('active', x === b); });
        });
        handleSwitch.appendChild(b);
    });
    handleRow.appendChild(handleSwitch);
    body.appendChild(handleRow);
}

// ── Backplate top view ───────────────────────────────────────────────────
// A 2D sketch of what Shape Extras will build, in mm, seen from above:
// the map outline, the plate/shell around it, the texts at their spots and
// the medal handle. Mirrors the Blender side's own geometry -- the outlines
// of utils.primitives, plate.create_generic_plate's _expand_outline, and
// utils.text_layouts' field angles/radii (incl. its _polygon_radius_at_angle).

// prop -> [angle (deg, CCW from +X), flip] per layout and shape, from
// text_layouts' OUTER_EDGE_FIELDS / FRONT_FACE_FIELDS / CURVED_RING_FIELDS /
// ON_MAP_FIELDS.
var BACKPLATE_TEXT_SPOTS = {
    OUTER_EDGE: {
        HEXAGON: { titlefield: [90, 1], textfield1: [150, 1], textfield2: [210, 0], textfield3: [270, 0], textfield4: [330, 0], textfield5: [30, 1] },
        OCTAGON: { titlefield: [90, 1], textfield5: [135, 1], textfield6: [180, 0], textfield1: [225, 0], textfield2: [270, 0],
                   textfield3: [315, 0], textfield7: [0, 0], textfield4: [45, 1] },
        SQUARE: { titlefield: [90, 1], textfield1: [180, 0], textfield2: [270, 0], textfield3: [0, 0] }
    },
    FRONT_FACE: {
        HEXAGON: { titlefield: [90], textfield1: [150], textfield2: [210], textfield3: [270], textfield4: [330], textfield5: [30] },
        OCTAGON: { titlefield: [90], textfield1: [225], textfield2: [270], textfield3: [315] },
        SQUARE: { titlefield: [90], textfield1: [180], textfield2: [270], textfield3: [0] }
    },
    CURVED: { CIRCLE: { titlefield: [90], textfield1: [180], textfield2: [270], textfield3: [0] } },
    ON_MAP: { HEXAGON: { titlefield: [90, 0], textfield1: [150, 1], textfield2: [210, 0], textfield3: [270, 0],
                         textfield4: [330, 0], textfield5: [30, 1] } }
};
var BACKPLATE_FIELD_LABELS = {
    titlefield: 'Title', textfield1: 'Text 1', textfield2: 'Text 2', textfield3: 'Text 3',
    textfield4: 'Text 4', textfield5: 'Text 5', textfield6: 'Text 6', textfield7: 'Text 7'
};
// Example values for the codes -- the real ones come from the trail. Like
// text_objects.replaceShapeText, only the first code found (in this order)
// is filled in.
var BACKPLATE_CODE_SAMPLES = [
    ['{length}', '42.20km'], ['{elevation}', '1250.00m'], ['{duration}', '3:45:12'], ['{date}', '2026-05-17'],
    ['{speed}', '11.25 km/h'], ['{scale}', '1:50000'], ['{name}', 'My Trail']
];

function tp3dSampleText(text) {
    for (var i = 0; i < BACKPLATE_CODE_SAMPLES.length; i++) {
        if (text.indexOf(BACKPLATE_CODE_SAMPLES[i][0]) !== -1) {
            return text.split(BACKPLATE_CODE_SAMPLES[i][0]).join(BACKPLATE_CODE_SAMPLES[i][1]);
        }
    }
    return text;
}

// The map's outline in mm, centered, y up -- same construction per shape as
// utils.primitives. A drawn rectangle keeps its aspect, long side = Size.
function tp3dBackplateMapOutline(base, size) {
    var pts = [], i, a, h = size / 2;
    if (base === 'HEXAGON') {
        for (i = 0; i < 6; i++) { a = Math.PI / 3 * i; pts.push([h * Math.cos(a), h * Math.sin(a)]); }
    } else if (base === 'OCTAGON') {
        var t = h * (Math.SQRT2 - 1);
        pts = [[h, t], [t, h], [-t, h], [-h, t], [-h, -t], [-t, -h], [t, -h], [h, -t]];
    } else if (base === 'CIRCLE') {
        for (i = 0; i < 96; i++) { a = 2 * Math.PI * i / 96; pts.push([h * Math.cos(a), h * Math.sin(a)]); }
    } else {
        var w = size, ht = size;
        if (tp3dSelectedShape() === 'rectangle' && typeof coords !== 'undefined' && coords) {
            var midLat = (coords.north + coords.south) / 2 * Math.PI / 180;
            var dx = Math.abs(coords.east - coords.west) * Math.cos(midLat);
            var dy = Math.abs(coords.north - coords.south);
            if (dx > 0 && dy > 0) {
                if (dx >= dy) ht = size * dy / dx; else w = size * dx / dy;
            }
        }
        pts = [[-w / 2, -ht / 2], [w / 2, -ht / 2], [w / 2, ht / 2], [-w / 2, ht / 2]];
    }
    return pts;
}

function tp3dBounds(pts) {
    var b = { minx: Infinity, miny: Infinity, maxx: -Infinity, maxy: -Infinity };
    pts.forEach(function(p) {
        b.minx = Math.min(b.minx, p[0]); b.maxx = Math.max(b.maxx, p[0]);
        b.miny = Math.min(b.miny, p[1]); b.maxy = Math.max(b.maxy, p[1]);
    });
    return b;
}

var BACKPLATE_INRADIUS_COS = { HEXAGON: Math.cos(Math.PI / 6), OCTAGON: Math.cos(Math.PI / 8), CIRCLE: 1 };

// plate.measured_inradius
function tp3dMeasuredInradius(pts, base) {
    var b = tp3dBounds(pts);
    var r = Math.max((b.maxx - b.minx) / 2, (b.maxy - b.miny) / 2);
    return base in BACKPLATE_INRADIUS_COS ? r * BACKPLATE_INRADIUS_COS[base] : r;
}

// plate._expand_outline: radial shapes scale uniformly so every edge moves
// out by `grow`; squares/rectangles scale per axis.
function tp3dExpandOutline(pts, grow, base) {
    if (grow <= 0) return pts;
    var b = tp3dBounds(pts);
    var cx = (b.minx + b.maxx) / 2, cy = (b.miny + b.maxy) / 2;
    var kx, ky;
    if (base in BACKPLATE_INRADIUS_COS) {
        var inr = tp3dMeasuredInradius(pts, base);
        kx = ky = (inr + grow) / inr;
    } else {
        kx = (b.maxx - b.minx + 2 * grow) / (b.maxx - b.minx);
        ky = (b.maxy - b.miny + 2 * grow) / (b.maxy - b.miny);
    }
    return pts.map(function(p) { return [cx + (p[0] - cx) * kx, cy + (p[1] - cy) * ky]; });
}

// text_layouts._polygon_radius_at_angle (same corner assumption included).
// *rect* ([halfW, halfH] of a SQUARE map, text_layouts._rect_half_extents)
// makes it a box: inradius is the long side's distance, and the same offset
// past the map's edge applies to both sides.
function tp3dRadiusAtAngle(inradius, base, a, rect) {
    if (rect) {
        var grow = inradius - Math.max(rect[0], rect[1]);
        var c = Math.abs(Math.cos(a)), s = Math.abs(Math.sin(a));
        return Math.min(c > 1e-9 ? (rect[0] + grow) / c : Infinity, s > 1e-9 ? (rect[1] + grow) / s : Infinity);
    }
    var n = { HEXAGON: 6, OCTAGON: 8 }[base];
    if (!n) return inradius;
    var half = Math.PI / n;
    var dev = (a % (2 * half)) - half;
    return inradius / Math.cos(dev);
}

// The chosen font file, loaded so the top view draws text in the font Blender
// will use (picker_server's /font_file; '' = Blender's default text font).
// {path: {family, loading}}, family null until loaded -- or for good, if the
// browser can't read that file (e.g. a .pfb); the page font is used then.
var _tp3dPreviewFonts = {};
function tp3dPreviewFontFamily(path) {
    var key = path || '';
    if (!_tp3dPreviewFonts[key]) {
        var entry = { family: null, loading: true };
        _tp3dPreviewFonts[key] = entry;
        if (typeof FontFace === 'undefined') return null;
        var family = 'tp3dPreviewFont' + Object.keys(_tp3dPreviewFonts).length;
        new FontFace(family, 'url("/font_file?path=' + encodeURIComponent(key) + '")').load()
            .then(function(face) {
                document.fonts.add(face);
                entry.family = family;
            })
            .catch(function() {})
            .then(function() {
                entry.loading = false;
                tp3dDrawBackplatePreview();
            });
    }
    return _tp3dPreviewFonts[key].family;
}

function tp3dSvgPoints(pts) {
    return pts.map(function(p) { return p[0].toFixed(2) + ',' + (-p[1]).toFixed(2); }).join(' ');
}

// The page's shape rotation (deg, CCW), as Blender applies it -- 0 where the
// page has none or can't rotate the current shape (circle, GeoJSON).
function tp3dPreviewRotation() {
    if (typeof TP3D_SHAPE_ROTATION_DEG === 'undefined') return 0;
    if (typeof tp3dCanRotateShape === 'function' && !tp3dCanRotateShape()) return 0;
    return Number(TP3D_SHAPE_ROTATION_DEG) || 0;
}

function tp3dRotatePts(pts, deg) {
    if (!pts || !deg) return pts;
    var a = deg * Math.PI / 180, c = Math.cos(a), s = Math.sin(a);
    return pts.map(function(p) { return [p[0] * c - p[1] * s, p[0] * s + p[1] * c]; });
}

function tp3dDrawBackplatePreview() {
    var svg = document.getElementById('backplateSvg');
    var modal = document.getElementById('backplateModal');
    // Only kept current while it's visible -- opening redraws anyway.
    if (!svg || !modal || !modal.classList.contains('open')) return;
    var NS = 'http://www.w3.org/2000/svg';
    var base = tp3dShapeExtraBase();
    var sizeInput = document.getElementById('objSizeInput');
    var size = parseFloat(sizeInput && sizeInput.value) || parseFloat(SETTINGS_STATE.objSize) || 100;
    var st = SETTINGS_STATE;
    var num = function(key, dflt) { var v = parseFloat(st[key]); return isFinite(v) ? v : dflt; };

    var mapPts = tp3dBackplateMapOutline(base, size);
    var outerPts = null, cavityPts = null;
    if (plateMode === 'SOLID_PLATE') {
        outerPts = tp3dExpandOutline(mapPts, size * num('outerBorderSize', 20) / 200, base);
    } else if (plateMode === 'SHELL') {
        var tol = num('singleColorModeTolerance', 0.2);
        outerPts = tp3dExpandOutline(mapPts, tol + num('shellWallThickness', 2), base);
        cavityPts = tp3dExpandOutline(mapPts, tol, base);
    }

    while (svg.firstChild) svg.removeChild(svg.firstChild);
    var fontFamily = tp3dPreviewFontFamily(st.textFont);
    svg.classList.toggle('bp-real-font', !!fontFamily);
    svg.style.setProperty('--bp-font', fontFamily ? '"' + fontFamily + '"' : 'inherit');
    function el(tag, attrs, parent) {
        var e = document.createElementNS(NS, tag);
        Object.keys(attrs).forEach(function(k) { e.setAttribute(k, attrs[k]); });
        (parent || svg).appendChild(e);
        return e;
    }

    // Keep the top-down preview aligned to the map frame; rotation remains
    // reflected in the side-view dimensions and in the generated model.
    var rotDeg = 0;
    var sideViewRotation = tp3dPreviewRotation();
    var shownMap = tp3dRotatePts(mapPts, sideViewRotation);
    var shownOuter = tp3dRotatePts(outerPts, sideViewRotation);
    var shownCavity = tp3dRotatePts(cavityPts, sideViewRotation);
    var all = mapPts.concat(outerPts || []);
    var b = tp3dBounds(all);
    var top = b.maxy;

    // Plate / shell, then the map on top.
    if (outerPts) el('polygon', { points: tp3dSvgPoints(outerPts), class: 'bp-plate' });
    if (cavityPts) el('polygon', { points: tp3dSvgPoints(cavityPts), class: 'bp-cavity' });
    var insetMap = plateMode === 'SOLID_PLATE' && num('plateInsertValue', 0) > 0;
    el('polygon', { points: tp3dSvgPoints(mapPts), class: 'bp-map' + (insetMap ? ' bp-map-inset' : '') });
    var mapLabel = el('text', { x: 0, y: 0, class: 'bp-map-label', 'font-size': (size * 0.06).toFixed(2) });
    mapLabel.textContent = 'Map';

    // Medal handle (Premium) on the plate's top edge -- sizes from
    // utils_pe.add_medal_handle.
    var handle = TP3D_SHAPE_EXTRAS_PREMIUM ? (st.handleStyle || 'NONE') : 'NONE';
    if (outerPts && plateMode === 'SOLID_PLATE' && handle !== 'NONE') {
        var y0 = -(top - 1);  // overlaps the plate a little, like the real one
        if (handle === 'ROUND') {
            el('path', { class: 'bp-plate', d: 'M -8 ' + y0 + ' L -8 ' + (y0 - 8) + ' A 8 8 0 0 1 8 ' + (y0 - 8) + ' L 8 ' + y0 + ' Z' });
            el('circle', { cx: 0, cy: y0 - 8, r: 5, class: 'bp-hole' });
            top += 15;
        } else {
            el('rect', { x: -23, y: y0 - 9, width: 46, height: 9, class: 'bp-plate' });
            el('rect', { x: -20, y: y0 - 6, width: 40, height: 3, class: 'bp-hole' });
            top += 8;
        }
    }

    // Texts.
    var spots = (BACKPLATE_TEXT_SPOTS[textLayout] || {})[base] || {};
    var inner = tp3dMeasuredInradius(mapPts, base);
    var outer = outerPts ? tp3dMeasuredInradius(outerPts, base) : inner;
    var mb = tp3dBounds(mapPts);
    var rect = base === 'SQUARE' ? [(mb.maxx - mb.minx) / 2, (mb.maxy - mb.miny) / 2] : null;
    var bodySize = num('textSize', 5) || 5;
    var titleSize = num('textSizeTitle', 0) || bodySize;
    var inset = !!st.textPlacement && textLayout !== 'ON_MAP';
    var extra = 0;  // room needed outside the plate (Front Face labels)
    Object.keys(spots).forEach(function(prop, i) {
        var angle = spots[prop][0], flip = spots[prop][1];
        var a = angle * Math.PI / 180;
        var raw = String(st[prop] == null ? '' : st[prop]);
        var empty = !raw.trim();
        var text = empty ? BACKPLATE_FIELD_LABELS[prop] : tp3dSampleText(raw);
        var mm = prop === 'titlefield' ? titleSize : bodySize;
        var cls = 'bp-text' + (empty ? ' bp-text-empty' : '') + (inset ? ' bp-text-inset' : '');
        var fontSize = (mm * 1.4).toFixed(2);  // letters ~0.7 em tall
        // The icon text_objects.appendTextIcon adds in front of the text
        // (Premium only -- textIcon is a no-op without it): scaled to
        // size/5, and the text shifted along to make room. An empty field
        // keeps its icon in Blender, so it's drawn without the label then.
        var iconName = TP3D_SHAPE_EXTRAS_PREMIUM ? st[FIELD_TO_ICON[prop]] : 'no';
        var icon = typeof TP3D_TEXT_ICON_SHAPES !== 'undefined' && iconName && iconName !== 'no'
            ? TP3D_TEXT_ICON_SHAPES[iconName] : null;
        var s = mm / 5;
        var iconW = icon ? (icon.maxx - icon.minx) * s : 0;
        var iconCls = 'bp-icon' + (inset ? ' bp-text-inset' : '');
        function iconPath(transform) {
            el('path', { d: icon.d, class: iconCls, transform: transform + ' scale(' + s.toFixed(4) + ' ' + (-s).toFixed(4) + ')' });
        }
        // Measured text width (mm), 0 for an empty field with an icon.
        function textWidth(textEl) {
            try { return textEl.getComputedTextLength(); } catch (e) { return 0; }
        }

        var x, y, rot;
        if (textLayout === 'CURVED') {
            var r = (inner + outer) / 2, span = Math.PI / 2.2;
            var upper = Math.sin(a) > 0;  // same test as apply_curved_ring_layout
            var a0 = upper ? a + span : a - span, a1 = upper ? a - span : a + span;
            var pathId = 'bpArc' + i;
            el('path', { id: pathId, fill: 'none', d: 'M ' + (r * Math.cos(a0)).toFixed(2) + ' ' + (-r * Math.sin(a0)).toFixed(2) +
                ' A ' + r + ' ' + r + ' 0 0 ' + (upper ? 1 : 0) + ' ' + (r * Math.cos(a1)).toFixed(2) + ' ' + (-r * Math.sin(a1)).toFixed(2) });
            var tw = 0;
            if (!(empty && icon)) {
                var tEl = el('text', { class: cls, 'font-size': fontSize });
                var tp = el('textPath', { href: '#' + pathId, startOffset: '50%' }, tEl);
                tp.textContent = text;
                tw = empty ? 0 : textWidth(tEl);
            }
            if (icon) {
                // The text stays centered on its angle (apply_curved_ring_layout
                // measures its anchor before the icon joins); the icon sits
                // that far before it along the ring, upright like the text.
                var dist = tw / 2 + 0.5 * s + iconW / 2 + 0.5;
                var ia = upper ? a + dist / r : a - dist / r;
                var deg = ia * 180 / Math.PI + (upper ? -90 : 90);
                iconPath('translate(' + (r * Math.cos(ia)).toFixed(2) + ' ' + (-r * Math.sin(ia)).toFixed(2) + ') rotate(' + (-deg).toFixed(2) + ')');
            }
            return;
        }
        var reversed = false;  // drawn turned 180 deg (readability): icon on the other side
        if (textLayout === 'ON_MAP') {
            var rm = (size / 2 * 0.9) * Math.cos(Math.PI / 6);
            x = prop === 'titlefield' ? 0 : Math.cos(a) * rm;
            y = prop === 'titlefield' ? rm : Math.sin(a) * rm;
            rot = (prop === 'titlefield' ? 0 : angle + 90 + (flip ? 180 : 0)) + rotDeg;
        } else if (textLayout === 'FRONT_FACE') {
            // On the side wall -- seen from above, just outside the edge.
            var rf = tp3dRadiusAtAngle(outer, base, a, rect) + mm * 0.9;
            x = Math.cos(a) * rf; y = Math.sin(a) * rf;
            rot = angle + 90 + rotDeg;
            if (Math.cos(rot * Math.PI / 180) < -1e-6) { rot += 180; reversed = true; }  // keep it readable
            cls += ' bp-text-side';
            extra = Math.max(extra, mm * 1.8);
        } else {
            var rr = tp3dRadiusAtAngle((inner + outer) / 2, base, a, rect);
            x = Math.cos(a) * rr; y = Math.sin(a) * rr;
            rot = angle + 90 + (flip ? 180 : 0) + rotDeg;
        }
        var turned = tp3dRotatePts([[x, y]], rotDeg)[0];
        x = turned[0]; y = turned[1];
        // Text and icon in the text's own frame (x = reading direction).
        var g = el('g', { transform: 'translate(' + x.toFixed(2) + ' ' + (-y).toFixed(2) + ') rotate(' + (-rot) + ')' });
        var dir = reversed ? -1 : 1;
        var w = 0;
        if (!(empty && icon)) {
            var t = el('text', { class: cls, 'font-size': fontSize }, g);
            t.textContent = text;
            w = empty ? 0 : textWidth(t);
            if (icon) t.setAttribute('x', (dir * (iconW / 2 + 0.5)).toFixed(2));
        }
        if (icon) {
            var ix = -dir * (w / 2 + 0.5 * s);
            el('path', { d: icon.d, class: iconCls,
                transform: 'translate(' + ix.toFixed(2) + ' 0) scale(' + s.toFixed(4) + ' ' + (-s).toFixed(4) + ')' }, g);
        }
    });

    // Fit the view around everything, with a margin.
    var pad = Math.max(b.maxx - b.minx, top - b.miny) * 0.08 + extra;
    var vx = b.minx - pad, vy = -top - pad, vw = b.maxx - b.minx + 2 * pad, vh = top - b.miny + 2 * pad;
    svg.setAttribute('viewBox', [vx, vy, vw, vh].map(function(v) { return v.toFixed(2); }).join(' '));

    tp3dDrawBackplateSide({ mapPts: shownMap, outerPts: shownOuter, cavityPts: shownCavity, spots: spots,
                            bodySize: bodySize, titleSize: titleSize, inset: inset });

    var caption = document.getElementById('backplateCaption');
    if (caption) {
        var ob = tp3dBounds(shownOuter || shownMap);
        var dims = (ob.maxx - ob.minx).toFixed(0) + ' × ' + (ob.maxy - ob.miny).toFixed(0) + ' mm';
        caption.textContent = plateMode === 'NONE'
            ? 'No plate — map only (' + dims + ')'
            : PLATE_LABELS[plateMode] + ' ' + dims + (plateMode === 'SOLID_PLATE' ? ', ' + num('plateThickness', 5) + ' mm high' : '')
              + (insetMap ? ', map sits ' + num('plateInsertValue', 0) + ' mm deep' : '');
    }
}

// Side view, looking at the plate from the front (-Y), x = left/right in mm,
// z = height. The plate is ~20x wider than it is tall, so heights are drawn
// stretched by k (shown in the label) -- every number printed is real mm.
// Shows the plate profile (bevel), the map pocket as hidden (dashed) lines
// with the map sitting in it, raised/inset top text, and a Front Face text
// on the wall facing the viewer. The map's terrain is only sketched -- its
// real height comes from the elevation data.
function tp3dDrawBackplateSide(v) {
    var svg = document.getElementById('backplateSideSvg');
    if (!svg) return;
    var NS = 'http://www.w3.org/2000/svg';
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    // Same font as the top view (tp3dPreviewFontFamily, already loading).
    var topSvg = document.getElementById('backplateSvg');
    svg.classList.toggle('bp-real-font', topSvg.classList.contains('bp-real-font'));
    svg.style.setProperty('--bp-font', topSvg.style.getPropertyValue('--bp-font') || 'inherit');
    function el(tag, attrs, parent) {
        var e = document.createElementNS(NS, tag);
        Object.keys(attrs).forEach(function(key) { e.setAttribute(key, attrs[key]); });
        (parent || svg).appendChild(e);
        return e;
    }
    var st = SETTINGS_STATE;
    var num = function(key, dflt) { var x = parseFloat(st[key]); return isFinite(x) ? x : dflt; };
    var mb = tp3dBounds(v.mapPts), ob = tp3dBounds(v.outerPts || v.mapPts);
    var W = ob.maxx - ob.minx;
    var solid = plateMode === 'SOLID_PLATE', shell = plateMode === 'SHELL';
    var t = solid || shell ? Math.max(0.1, num('plateThickness', 5)) : 0;
    var d = solid ? Math.min(Math.max(0, num('plateInsertValue', 0)), t) : 0;
    var tol = num('singleColorModeTolerance', 0.2);
    var minT = Math.max(0.5, num('minThickness', 2));
    var mapBottom = solid ? t - d : t;
    var mapH = minT + 4;  // sketch only -- the terrain decides
    // Shell walls rise with the map's edge; drawn up to its lowest height.
    var wallTop = shell ? t + minT : t;

    var anyText = Object.keys(v.spots).some(function(prop) { return String(st[prop] || '').trim(); });
    var topText = (textLayout === 'OUTER_EDGE' || textLayout === 'CURVED') && solid && anyText;
    var relief = topText && !v.inset ? 0.8 : 0;  // create_text's 0.4 extrude, both ways
    var grooveDepth = num('plateInsertValue', 0) || 0.8;  // inset_depth, as elements.py passes it
    var topZ = Math.max(wallTop + relief, mapBottom + mapH);
    var k = Math.max(1, Math.min(12, Math.round(W * 0.22 / topZ)));
    var Y = function(z) { return -z * k; };
    var fs = W * 0.034;  // label size

    // Map, sketched terrain on its own base (drawn first: the plate's front
    // wall hides the part sitting in the pocket).
    var terrain = [];
    for (var i = 0; i <= 48; i++) {
        var x = mb.minx + (mb.maxx - mb.minx) * i / 48;
        var h = minT + (mapH - minT) * (0.5 + 0.28 * Math.sin(x * 0.11) + 0.18 * Math.sin(x * 0.29 + 1.3));
        terrain.push(x.toFixed(2) + ',' + Y(mapBottom + h).toFixed(2));
    }
    el('polygon', { class: 'bp-map', points: mb.minx.toFixed(2) + ',' + Y(mapBottom).toFixed(2) + ' ' + terrain.join(' ') +
        ' ' + mb.maxx.toFixed(2) + ',' + Y(mapBottom).toFixed(2) });

    if (solid || shell) {
        var L = ob.minx, R = ob.maxx;
        var bv = solid ? Math.min(num('plateBevel', 0), t / 2, W / 4) : 0;
        var prof = [[L + bv, 0], [R - bv, 0], [R, bv], [R, wallTop - bv], [R - bv, wallTop], [L + bv, wallTop], [L, wallTop - bv], [L, bv]];
        el('polygon', { class: 'bp-plate', points: prof.map(function(p) { return p[0].toFixed(2) + ',' + Y(p[1]).toFixed(2); }).join(' ') });
        if (solid && d > 0) {
            // The pocket, hidden behind the front wall.
            var px0 = mb.minx - tol, px1 = mb.maxx + tol;
            el('polyline', { class: 'bp-hidden', points: [px0, Y(t), px0, Y(t - d), px1, Y(t - d), px1, Y(t)].map(function(n) { return n.toFixed(2); }).join(' ') });
        }
        if (shell && v.cavityPts) {
            var cb = tp3dBounds(v.cavityPts);
            el('polyline', { class: 'bp-hidden', points: [cb.minx, Y(wallTop), cb.minx, Y(t), cb.maxx, Y(t), cb.maxx, Y(wallTop)].map(function(n) { return n.toFixed(2); }).join(' ') });
        }
        // Top-ring text: raised 0.8 mm, or carved in (hidden lines).
        if (topText) {
            var bw = Math.max(0.5, (mb.minx - ob.minx) * 0.6);
            [(ob.minx + mb.minx) / 2, (ob.maxx + mb.maxx) / 2].forEach(function(cx) {
                if (relief) {
                    el('rect', { class: 'bp-relief', x: (cx - bw / 2).toFixed(2), y: Y(t + relief).toFixed(2),
                                 width: bw.toFixed(2), height: (relief * k).toFixed(2) });
                } else {
                    el('rect', { class: 'bp-hidden', x: (cx - bw / 2).toFixed(2), y: Y(t).toFixed(2),
                                 width: bw.toFixed(2), height: (Math.min(grooveDepth, t) * k).toFixed(2) });
                }
            });
        }
        // Front Face: the text on the wall facing us (the 270 deg field).
        var note = '';
        if (textLayout === 'FRONT_FACE') {
            var front = Object.keys(v.spots).filter(function(p) { return v.spots[p][0] === 270; })[0];
            var raw = front ? String(st[front] || '') : '';
            if (raw.trim()) {
                var mm = front === 'titlefield' ? v.titleSize : v.bodySize;
                var g = el('g', { transform: 'translate(0 ' + Y(t / 2).toFixed(2) + ') scale(1 ' + k + ')' });
                var txt = el('text', { class: 'bp-text' + (v.inset ? ' bp-text-inset' : ''), 'font-size': (mm * 1.4).toFixed(2) }, g);
                txt.textContent = tp3dSampleText(raw);
                var iconName = TP3D_SHAPE_EXTRAS_PREMIUM ? st[FIELD_TO_ICON[front]] : 'no';
                var icon = typeof TP3D_TEXT_ICON_SHAPES !== 'undefined' && iconName && iconName !== 'no' ? TP3D_TEXT_ICON_SHAPES[iconName] : null;
                if (icon) {
                    var s = mm / 5, w = 0;
                    try { w = txt.getComputedTextLength(); } catch (e) { w = 0; }
                    txt.setAttribute('x', ((icon.maxx - icon.minx) * s / 2 + 0.5).toFixed(2));
                    el('path', { d: icon.d, class: 'bp-icon' + (v.inset ? ' bp-text-inset' : ''),
                        transform: 'translate(' + (-(w / 2 + 0.5 * s)).toFixed(2) + ' 0) scale(' + s.toFixed(4) + ' ' + (-s).toFixed(4) + ')' }, g);
                }
                if (mm > t) note = ' — text is taller than the plate';
            }
        }
    }

    // Dimensions: height (right), pocket depth (left), width (below).
    function dimLine(x0, y0, x1, y1) { el('line', { class: 'bp-dim', x1: x0, y1: y0, x2: x1, y2: y1 }); }
    function dimText(x, y, text, anchor) {
        var e = el('text', { class: 'bp-dim-text', x: x.toFixed(2), y: y.toFixed(2), 'font-size': fs.toFixed(2), 'text-anchor': anchor });
        e.textContent = text;
    }
    var gap = W * 0.03, tick = W * 0.012;
    if (t > 0) {
        var rx = ob.maxx + gap;
        dimLine(rx, Y(0), rx, Y(wallTop));
        dimLine(rx - tick, Y(0), rx + tick, Y(0));
        dimLine(rx - tick, Y(wallTop), rx + tick, Y(wallTop));
        dimText(rx + gap * 0.6, Y(wallTop / 2), (shell ? 'floor ' + t + ' mm' : t + ' mm'), 'start');
    }
    if (d > 0) {
        var lx = ob.minx - gap;
        dimLine(lx, Y(t - d), lx, Y(t));
        dimLine(lx - tick, Y(t - d), lx + tick, Y(t - d));
        dimLine(lx - tick, Y(t), lx + tick, Y(t));
        dimText(lx - gap * 0.6, Y(t - d / 2), d + ' mm', 'end');
    }
    var wy = Y(0) + fs * 1.4;
    dimLine(ob.minx, wy, ob.maxx, wy);
    dimLine(ob.minx, wy - tick, ob.minx, wy + tick);
    dimLine(ob.maxx, wy - tick, ob.maxx, wy + tick);
    var wLabel = el('text', { class: 'bp-dim-text', x: 0, y: (wy + fs * 0.9).toFixed(2), 'font-size': fs.toFixed(2), 'text-anchor': 'middle' });
    wLabel.textContent = W.toFixed(0) + ' mm';

    var padX = W * 0.2;
    var vyTop = Y(topZ) - fs * 1.5, vyBottom = wy + fs * 1.8;
    svg.setAttribute('viewBox', [ob.minx - padX, vyTop, W + 2 * padX, vyBottom - vyTop].map(function(n) { return n.toFixed(2); }).join(' '));

    var sideNote = document.getElementById('backplateSideNote');
    if (sideNote) {
        sideNote.textContent = (k > 1 ? '(heights ×' + k + ')' : '') + (note || '') +
            (shell ? ' — walls rise with the map\'s edge' : '') + (plateMode === 'NONE' ? ' — no plate' : '');
    }
}

// The page's own shape-button handlers change currentShape first; refresh
// right after them. Registered here so the pages don't each need a hook.
// Picking a shape drops any imported GeoJSON boundary / SVG shape, since
// only one shape is selected at a time: the boundary would otherwise keep
// winning over the picked shape at Send time, and the SVG would stay listed
// in the sidebar after its outline already left the map. Both clears
// refresh on their own (via updateStatus); clearSvgShapeImport
// (rect_editor.js) leaves currentShape alone since it's no longer 'svg'.
document.querySelectorAll('.shape-btn[data-shape]').forEach(function(btn) {
    btn.addEventListener('click', function() {
        setTimeout(function() {
            if (svgShapePath && currentShape !== 'svg') clearSvgShapeImport();
            if (geojsonPaths && geojsonPaths.length) document.getElementById('clearGeojson').click();
            tp3dRefreshShapeExtras();
        }, 0);
    });
});
