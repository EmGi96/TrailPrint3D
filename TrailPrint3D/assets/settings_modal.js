// Builds the "⚙️ Settings" button (appended into #elementStatus) and the
// popup it opens, plus the standalone tp3dAlert() notice popup further
// down -- both reused by every 2D-map picker page via __SETTINGS_MODAL_JS__
// in picker_server.py. Requires PORT, SETTINGS_STATE
// (from __SETTINGS_STATE_JS__), ADVANCED_SETTINGS_STATE (from
// __ADVANCED_SETTINGS_STATE_JS__), ELEMENT_ICONS (from
// __ELEMENT_ICONS_JS__) and the shared toggle helpers TP3D_ELEMENT_STATE /
// tp3dToggleElement / tp3dRepaintElementToggle (from element_status.js,
// which must run first) to already be defined. Entirely self-contained --
// none of the 3 picker pages need any of this markup in their own HTML.
//
// Two tabs:
//  - Map: the handful of generation-wide fields that don't belong to any
//    one element category (Elevation Scale, Path Thickness, ...).
//  - Elements: every element category's individual sub-checkboxes and
//    per-category thresholds, grouped as cards -- the "much more detailed"
//    breakdown behind each #elementStatus chip's single on/off summary.
//    A simple category (Forest, Buildings, ...) is one card with its own
//    toggle icon and threshold field(s); Water and Roads are wider
//    composite cards with a checklist of sub-flags plus their own number
//    fields, alongside the same quick toggle icon.
//
// The right-hand panel shows a per-feature preview GIF/image when a field
// with a `preview` URL is clicked (tp3dShowPreview) -- most fields don't
// have one yet and stay plain text, not clickable.
//
// Which tab (Map/Elements) was last open is tracked in TP3D_SETTINGS_TAB and
// switchable from outside via tp3dActivateSettingsTab -- this file doesn't
// persist it anywhere itself (it's shared across all 3 pages and doesn't
// know their own saveState()/restoreState() shape), but each page's own
// saveState() can read TP3D_SETTINGS_TAB into its existing state blob, and
// its restoreState() can call tp3dActivateSettingsTab(s.settingsTab) once
// fetched, the same way it already restores the base layer, form fields, etc.
// Small reusable notice/error popup in the same .tp3d-modal/.tp3d-modal-box
// chrome the Settings popup above uses, so an in-page error reads as part
// of this UI instead of a jarring browser-native alert() box. Lazily builds
// and appends its DOM the first time it's called, then just updates and
// re-shows the same modal on every later call.
var _tp3dAlertModal = null;
function tp3dAlert(message, title) {
    if (!_tp3dAlertModal) {
        var modal = document.createElement('div');
        modal.className = 'tp3d-modal';

        var box = document.createElement('div');
        box.className = 'tp3d-modal-box';
        box.style.width = '320px';
        modal.appendChild(box);

        var header = document.createElement('div');
        header.className = 'modal-header';
        var titleEl = document.createElement('span');
        var closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.className = 'tp3d-modal-close-btn';
        closeBtn.title = 'Close';
        closeBtn.textContent = '✕';
        header.appendChild(titleEl);
        header.appendChild(closeBtn);
        box.appendChild(header);

        var body = document.createElement('div');
        body.style.cssText = 'font-size:13px; color:#ccc; line-height:1.4; white-space:pre-wrap;';
        box.appendChild(body);

        var okBtn = document.createElement('button');
        okBtn.type = 'button';
        okBtn.className = 'btn-send';
        okBtn.style.width = '100%';
        okBtn.textContent = 'OK';
        box.appendChild(okBtn);

        function close() { modal.classList.remove('open'); }
        closeBtn.addEventListener('click', close);
        okBtn.addEventListener('click', close);
        // Clicking the dimmed backdrop (i.e. anywhere that isn't the box
        // itself) closes it too, matching the Settings popup's own feel.
        modal.addEventListener('click', function(e) { if (e.target === modal) close(); });

        document.body.appendChild(modal);
        _tp3dAlertModal = { modal: modal, titleEl: titleEl, bodyEl: body };
    }
    _tp3dAlertModal.titleEl.textContent = title || 'Notice';
    _tp3dAlertModal.bodyEl.textContent = message;
    _tp3dAlertModal.modal.classList.add('open');
}

var TP3D_SETTINGS_TAB = 'elements';

// Resolution deliberately isn't in this list -- every picker page already
// has its own page-level #resolutionSlider (outside this modal), and that's
// the one the /confirm handler actually applies (see operators.py: it
// overwrites props.num_subdivisions from the payload's own 'resolution' at
// Send time regardless of anything pushed live via this modal). Having both
// meant two controls for one setting that could silently disagree.
//
// Grouped and ordered to mirror panels.py's own "5. Terrain" then "4. Trail"
// sections (in that order, since Elevation was already the modal's first
// field before this list grew) -- same fields, same conditional visibility
// (`showWhen`, see tp3dRefreshMapTabVisibility) as the sidebar's own
// `if props.smoothTerrainTop` / `if props.singleColorMode` branches in
// panels.py. Elevation Mode itself is deliberately left out -- Fixed Height
// mode is a niche option not worth the extra control here.
var SETTINGS_MODAL_MAP_FIELDS = [
    // -- Terrain (panels.py "5. Terrain") --
    { key: 'scaleElevation', source: 'SETTINGS_STATE', endpoint: 'update_setting',
      label: 'Elevation Scale', type: 'number', step: 0.1, min: 0,
      title: 'Multiplier to the Elevation',
      preview: 'https://trailprint3d.com/images/howto/ElevationScaleGif.webp' },
    { key: 'minThickness', source: 'SETTINGS_STATE', endpoint: 'update_setting',
      label: 'Extra Map Height', type: 'number', step: 0.5, min: 0.5,
      title: 'Extra height added to the map, below the terrain',
      preview: 'https://trailprint3d.com/images/howto/ExtraMapHeight.webp' },
    { key: 'shapeRotation', source: 'SETTINGS_STATE', endpoint: 'update_setting',
      label: 'Shape Rotation', type: 'number', step: 1, min: -360, max: 360,
      title: 'Rotate the shape around the trail/map center',
      preview: 'https://trailprint3d.com/images/howto/ShapeRotation.webp' },
    { key: 'smoothTerrainTop', source: 'SETTINGS_STATE', endpoint: 'update_setting',
      label: 'Smooth Terrain', type: 'checkbox',
      title: 'Smooth the terrain -- useful if it looks blocky or has grid lines',
      preview: 'https://trailprint3d.com/images/howto/SmoothTerrain.webp',
      previewCaption: 'If your Terrain looks blocky you probably reached the max detail the used Dataset is providing. You can try to smooth the blocky look using this function.',
      // Rendered as one field-and-strength unit on the same row (see
      // tp3dBuildFieldRow's `companion` handling) instead of its own
      // separate showWhen row, mirroring panels.py's own smoothRow, which
      // puts smoothTerrainTop and smoothTerrainStrength side by side.
      companion: { key: 'smoothTerrainStrength', source: 'SETTINGS_STATE', endpoint: 'update_setting',
                   step: 1, min: 1, max: 10,
                   title: 'Number of smoothing passes -- higher is smoother but less detailed' } },
    // Divider: spans both grid columns (see tp3dBuildMapTab/.map-tab-divider),
    // separating the terrain fields above from the trail-specific ones below.
    { type: 'divider' },
    // -- Trail (panels.py "4. Trail") --
    { key: 'pathThickness', source: 'SETTINGS_STATE', endpoint: 'update_setting',
      label: 'Trail Thickness', type: 'number', step: 0.01, min: 0.1, max: 5, decimals: 2,
      title: 'Thickness of the path in mm',
      preview: 'https://trailprint3d.com/images/howto/PathThicknessGif.webp' },
    { key: 'overwritePathElevation', source: 'SETTINGS_STATE', endpoint: 'update_setting',
      label: 'Snap Trail to Terrain', type: 'checkbox',
      title: 'Cast each point of the trail onto the Terrain Mesh',
      preview: 'https://trailprint3d.com/images/howto/SnapTrailToTerrain.jpg',
      previewCaption: [
          'GPX files usually have their own elevation data.. But sometimes they dont and sometimes they have their values from diffrent datasets.',
          'To make sure the Trail and Terrain match perfectly, enable this to Snap each point of the Trail to the Terrain'
      ] },
    { key: 'singleColorMode', source: 'SETTINGS_STATE', endpoint: 'update_setting',
      label: 'SingleColorMode Trail', type: 'checkbox',
      title: 'Enable this if you don\'t have a Multicolor printer',
      preview: 'https://trailprint3d.com/images/howto/SingleColorTrail.webp' },
    { key: 'singleColorModeHeight', source: 'SETTINGS_STATE', endpoint: 'update_setting',
      label: 'SingleColorMode Trail Height', type: 'number', step: 0.05, min: 0, max: 10,
      title: 'How far the SCM trail strip rises above the terrain surface (mm)',
      preview: 'https://trailprint3d.com/images/howto/SEM%20-%20Trail%20Height.webp',
      showWhen: { key: 'singleColorMode', equals: true } },
    { key: 'singleColorModeTolerance', source: 'SETTINGS_STATE', endpoint: 'update_setting',
      label: 'SingleColorMode Tolerance', type: 'number', step: 0.05, min: 0,
      title: 'Tolerance of the Trail for the SingleColorMode',
      showWhen: { key: 'singleColorMode', equals: true } }
];

// { label, preview? } per element card -- preview is optional, add a URL
// here to make that card's title clickable too, same as any other field.
// Shared by both the OSM and WorldCover card orders below (see
// element_status.js's ELEMENT_STATUS_ORDER_OSM/_WORLDCOVER for why the two
// share most of these keys).
var ELEMENT_CARD_LABELS = {
    water: { label: 'Water' },
    forest: { label: 'Forest' },
    mountain: { label: 'Mountain' },
    scree: { label: 'Scree' },
    city: { label: 'City Boundaries' },
    greenspace: { label: 'Greenspace' },
    farmland: { label: 'Farmland' },
    glacier: { label: 'Glacier' },
    buildings: { label: 'Buildings' },
    roads: { label: 'Roads' }
};

// 'buildings' is deliberately left out -- it's rendered as a simple card
// too (see tp3dBuildSimpleElementCard), just appended to the composite row
// instead (tp3dBuildElementsTab), sitting to the right of Roads rather
// than crowding the small single-field cards up top.
var SIMPLE_ELEMENT_ORDER = ['forest', 'scree', 'city', 'greenspace', 'farmland', 'glacier'];
var SIMPLE_ELEMENT_FIELDS = {
    forest: [{ key: 'colFArea', label: 'Area Threshold', step: 0.5, min: 0 }],
    scree: [{ key: 'colScrArea', label: 'Area Threshold', step: 0.5, min: 0 }],
    city: [{ key: 'colCArea', label: 'Area Threshold', step: 0.5, min: 0 }],
    greenspace: [{ key: 'colGrArea', label: 'Area Threshold', step: 0.5, min: 0 }],
    farmland: [{ key: 'colFaArea', label: 'Area Threshold', step: 0.5, min: 0 }],
    glacier: [{ key: 'colGlArea', label: 'Area Threshold', step: 0.5, min: 0 }],
    buildings: [
        { key: 'elBHeightMultiplier', label: 'Height Multiplier', step: 0.1, min: 0.01 },
        { key: 'elBMinPrintMM', label: 'Min Footprint (mm)', step: 0.01, min: 0 }
    ]
};

// ESA WorldCover's own card order -- no per-category threshold fields (see
// SIMPLE_ELEMENT_FIELDS not having entries for these keys below); a single
// shared field (WORLDCOVER_MIN_AREA_FIELD) covers all of them at once,
// mirroring panels.py's "6. Map Elements" layout (one el_wcMinFeatureArea
// row above the whole _LANDCOVER_COLOR_ROWS box, not one per category).
var LANDCOVER_ELEMENT_ORDER = ['water', 'forest', 'mountain', 'city', 'greenspace', 'farmland', 'glacier'];
var WORLDCOVER_MIN_AREA_FIELD = {
    key: 'elWcMinFeatureArea', source: 'ADVANCED_SETTINGS_STATE', endpoint: 'update_advanced_setting',
    label: 'Min Feature Area', type: 'number', step: 0.5, min: 0,
    title: 'Smallest land-cover feature area to keep before it gets discarded'
};

var COMPOSITE_ELEMENT_ORDER = ['water', 'roads'];
var COMPOSITE_ELEMENTS = {
    water: {
        checkboxes: [
            { key: 'colWPondsActive', label: 'Ponds & Lakes' },
            { key: 'colWSmallRiversActive', label: 'Small Rivers' },
            { key: 'colWBigRiversActive', label: 'Big Rivers' },
            { key: 'elOActive', label: 'Ocean' }
        ],
        numberFields: [
            { key: 'colWArea', label: 'Lake/Pond Threshold', step: 0.1, min: 0 },
            { key: 'colWStreamWidth', label: 'River Width', step: 0.1, min: 0.1, max: 100 },
            { key: 'elOMinIslandArea', label: 'Min Island Area', step: 0.5, min: 0 },
            { key: 'elORdpEpsilon', label: 'Coastline Simplify', step: 0.01, min: 0, max: 2 }
        ]
    },
    roads: {
        checkboxes: [
            { key: 'elSHighwaysActive', label: 'Highways' },
            { key: 'elSMajorActive', label: 'Major Roads' },
            { key: 'elSMinorActive', label: 'Minor Roads' },
            { key: 'elSResidentialActive', label: 'Residential Roads' },
            { key: 'elSServiceActive', label: 'Service Roads' },
            { key: 'elSFootwayActive', label: 'Footways/Sidewalks' },
            { key: 'elSCycleBridleActive', label: 'Cycle/Bridle Paths' },
            { key: 'elSTrackActive', label: 'Tracks' },
            { key: 'elSPathActive', label: 'Trails/Paths' }
        ],
        numberFields: [
            { key: 'elSMultiplier', label: 'Width Multiplier', step: 0.1, min: 0 },
            { key: 'elSHeight', label: 'Height', step: 0.05, min: 0 },
            { key: 'elSCutTolerance', label: 'Cut Tolerance', step: 0.05, min: 0 }
        ]
    }
};

function tp3dSendAdvancedUpdate(key, value) {
    fetch('http://127.0.0.1:' + PORT + '/update_advanced_setting', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: key, value: value })
    }).catch(function() {});
}

function tp3dShowPreview(url, caption) {
    var panel = document.getElementById('settingsPreviewPanel');
    if (!panel) return;
    // Grows the whole box open (see .settings-modal-box.preview-active) the
    // first time this runs -- harmless to call again on later clicks, the
    // class is already there and classList.add is a no-op.
    var box = panel.closest('.settings-modal-box');
    if (box) box.classList.add('preview-active');
    panel.innerHTML = '';
    panel.setAttribute('data-preview-url', url);
    var img = document.createElement('img');
    img.src = url;
    img.alt = 'Preview';
    panel.appendChild(img);

    // caption is optional -- a single string or an array of lines, shown
    // stacked below the image.
    if (caption) {
        var lines = Array.isArray(caption) ? caption : [caption];
        var captionEl = document.createElement('div');
        captionEl.className = 'preview-caption';
        lines.forEach(function(line) {
            var lineEl = document.createElement('div');
            lineEl.textContent = line;
            captionEl.appendChild(lineEl);
        });
        panel.appendChild(captionEl);
    }

    var closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'preview-close-btn';
    closeBtn.title = 'Close preview';
    closeBtn.textContent = '✕';
    closeBtn.addEventListener('click', function(e) {
        e.preventDefault();
        e.stopPropagation();
        tp3dHidePreview();
    });
    panel.appendChild(closeBtn);
}

function tp3dHidePreview() {
    var panel = document.getElementById('settingsPreviewPanel');
    if (!panel) return;
    var box = panel.closest('.settings-modal-box');
    if (box) box.classList.remove('preview-active');
    panel.removeAttribute('data-preview-url');
    panel.innerHTML = '';
    panel.textContent = 'Click a highlighted setting to preview it here';
}

// A standalone icon button that shows *url* in the preview panel --
// standalone (never nested inside a <label>) so it's safe to drop next to
// ANY control, including a checkbox's own label, without its click also
// forwarding to that control.
function tp3dMakePreviewButton(url, caption) {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'preview-btn';
    btn.title = 'Click to preview';
    btn.textContent = '?';
    btn.addEventListener('click', function(e) {
        e.preventDefault();
        e.stopPropagation();
        // Clicking the "?" of the preview that's already showing closes it.
        var panel = document.getElementById('settingsPreviewPanel');
        if (panel && panel.getAttribute('data-preview-url') === url) {
            tp3dHidePreview();
        } else {
            tp3dShowPreview(url, caption);
        }
    });
    return btn;
}

// The one reusable building block behind "make X clickable to preview a
// GIF": wraps *el* together with a preview button when *url* is set, or
// just returns *el* unchanged otherwise. Every field builder below calls
// this on its label/title element -- so adding a preview to any field,
// checkbox, or card title anywhere in this modal is just adding a
// `preview: 'url'` property (plus an optional `previewCaption`, a string
// or array of lines shown below the image) to its definition, nothing
// else changes.
function tp3dWithPreview(el, url, caption) {
    if (!url) return el;
    var wrap = document.createElement('span');
    wrap.className = 'preview-wrap';
    wrap.appendChild(el);
    wrap.appendChild(tp3dMakePreviewButton(url, caption));
    return wrap;
}

// Blender's FloatProperty is a 32-bit float internally, so a clean value
// like 1.3 can round-trip back as 1.2999999523... -- shown raw in a number
// input, a string that long can force its CSS grid cell to grow past its
// column (that's what "the box sticks out past the border" actually was).
// Rounds to *decimals* (falling back to a generous default that trims
// float32 noise without eating real precision) any time a value from
// SETTINGS_STATE/ADVANCED_SETTINGS_STATE is used to populate a field, so
// this can't recur on any numeric field, not just ones with an explicit
// `decimals` set.
function tp3dRoundForDisplay(value, decimals) {
    if (typeof value !== 'number' || isNaN(value)) return value;
    return parseFloat(value.toFixed(decimals != null ? decimals : 4));
}

function tp3dBuildNumberField(field) {
    var wrap = document.createElement('div');
    wrap.className = 'card-field';
    wrap.title = field.title || '';
    var label = document.createElement('label');
    label.textContent = field.label;
    var input = document.createElement('input');
    input.type = 'number';
    if (field.step != null) input.step = field.step;
    if (field.min != null) input.min = field.min;
    if (field.max != null) input.max = field.max;
    input.value = tp3dRoundForDisplay(ADVANCED_SETTINGS_STATE[field.key], field.decimals);
    input.addEventListener('change', function() {
        tp3dSendAdvancedUpdate(field.key, parseFloat(input.value));
    });
    wrap.appendChild(tp3dWithPreview(label, field.preview, field.previewCaption));
    wrap.appendChild(input);
    return wrap;
}

// *compositeKey*, when given, marks this checkbox as one of a composite
// category's own sub-flags (see TP3D_COMPOSITE_FLAGS in element_status.js)
// -- while that category is off, the checkbox shows its remembered value
// (ADVANCED_SETTINGS_STATE._compositeRemembered, from
// utils.build_composite_remembered_state) instead of the live False, and
// is locked (disabled) rather than editable. A field not present in that
// category's remembered map is never locked, same as a plain checkbox.
function tp3dBuildCheckboxField(field, compositeKey) {
    var label = document.createElement('label');
    label.title = field.title || '';
    var input = document.createElement('input');
    input.type = 'checkbox';
    input.setAttribute('data-advanced-checkbox', field.key);
    var remembered = compositeKey ? ((ADVANCED_SETTINGS_STATE._compositeRemembered || {})[compositeKey] || {}) : {};
    var locked = compositeKey && !tp3dCompositeIsActive(compositeKey) && remembered.hasOwnProperty(field.key);
    input.checked = locked ? !!remembered[field.key] : !!ADVANCED_SETTINGS_STATE[field.key];
    input.disabled = locked;
    if (locked) label.classList.add('locked');
    input.addEventListener('change', function() {
        tp3dSendAdvancedUpdate(field.key, input.checked);
    });
    label.appendChild(input);
    label.appendChild(document.createTextNode(field.label));
    return tp3dWithPreview(label, field.preview, field.previewCaption);
}

function tp3dBuildSimpleElementCard(key) {
    var meta = ELEMENT_CARD_LABELS[key] || { label: key };
    var card = document.createElement('div');
    card.className = 'element-card';

    var icon = document.createElement('span');
    icon.className = 'card-icon';
    icon.setAttribute('data-element-toggle', key);
    icon.innerHTML = ELEMENT_ICONS[key] || '';
    icon.title = meta.label + ' (click to toggle)';
    icon.addEventListener('click', function() { tp3dToggleElement(key); });
    card.appendChild(icon);

    var label = document.createElement('div');
    label.className = 'card-label';
    label.textContent = meta.label;
    card.appendChild(tp3dWithPreview(label, meta.preview, meta.previewCaption));

    // SIMPLE_ELEMENT_FIELDS is OSM-only -- WorldCover shares several of
    // these same keys (forest/city/greenspace/farmland/glacier) for its own
    // cards, which have no per-category threshold of their own (one shared
    // WORLDCOVER_MIN_AREA_FIELD covers all of them, see tp3dBuildElementsTab),
    // so the lookup must never apply here even though the key string matches.
    if (!tp3dIsWorldCover()) {
        (SIMPLE_ELEMENT_FIELDS[key] || []).forEach(function(field) {
            card.appendChild(tp3dBuildNumberField(field));
        });
    }

    tp3dRepaintElementToggle(key);
    return card;
}

function tp3dBuildCompositeElementCard(key) {
    var def = COMPOSITE_ELEMENTS[key];
    var meta = ELEMENT_CARD_LABELS[key] || { label: key };
    var card = document.createElement('div');
    card.className = 'element-card composite';

    var head = document.createElement('div');
    head.className = 'card-head';
    var icon = document.createElement('span');
    icon.className = 'card-icon';
    icon.setAttribute('data-element-toggle', key);
    icon.innerHTML = ELEMENT_ICONS[key] || '';
    icon.title = meta.label + ' (click to toggle all)';
    icon.addEventListener('click', function() { tp3dToggleElement(key); });
    var label = document.createElement('div');
    label.className = 'card-label';
    label.textContent = meta.label;
    head.appendChild(icon);
    head.appendChild(tp3dWithPreview(label, meta.preview, meta.previewCaption));
    card.appendChild(head);

    var checklist = document.createElement('div');
    checklist.className = 'card-checklist';
    def.checkboxes.forEach(function(field) { checklist.appendChild(tp3dBuildCheckboxField(field, key)); });
    card.appendChild(checklist);

    def.numberFields.forEach(function(field) { card.appendChild(tp3dBuildNumberField(field)); });

    tp3dRepaintElementToggle(key);
    return card;
}

function tp3dSendMapField(field, value) {
    fetch('http://127.0.0.1:' + PORT + '/' + field.endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: field.key, value: value })
    }).catch(function() {});
}

// A range slider paired with a number input that shares its value --
// dragging the slider can't exceed field.max, but typing into the number
// box can, for fields (like Resolution) where the common range fits a
// slider but an occasional larger value should still be reachable without
// raising the slider's own max (which would make every-day dragging less
// precise).
function tp3dBuildRangeField(field, stateObj) {
    var controls = document.createElement('div');
    controls.className = 'range-with-number';

    var initial = tp3dRoundForDisplay(stateObj[field.key], field.decimals);

    var range = document.createElement('input');
    range.type = 'range';
    range.min = field.min;
    range.max = field.max;
    range.step = field.step || 1;
    range.value = Math.min(initial, field.max);

    var number = document.createElement('input');
    number.type = 'number';
    number.min = field.min;
    number.step = field.step || 1;
    number.value = initial;

    range.addEventListener('input', function() {
        number.value = range.value;
        tp3dSendMapField(field, parseFloat(range.value));
    });
    number.addEventListener('change', function() {
        var value = parseFloat(number.value);
        if (isNaN(value)) return;
        range.value = Math.min(Math.max(value, field.min), field.max);
        tp3dSendMapField(field, value);
    });

    controls.appendChild(range);
    controls.appendChild(number);
    return controls;
}

// Tracks each Map-tab field's own live value (populated/updated by
// tp3dBuildFieldRow below), independent of SETTINGS_STATE/
// ADVANCED_SETTINGS_STATE (which only hold the *initial* snapshot, never
// mutated by a Map-tab edit the way ADVANCED_SETTINGS_STATE is for Elements-
// tab composites) -- lets a field with `showWhen` (e.g. Smoothing Strength,
// gated on Smooth Terrain) look up its controlling field's current value
// without re-fetching from the server.
var MAP_TAB_CONTROL_VALUES = {};
// { field, row } per built Map-tab row, so tp3dRefreshMapTabVisibility can
// re-check every `showWhen` after any field's value changes, not just the
// one that was just edited. Self-prunes rows the Elements tab has since
// discarded (tp3dRebuildElementsTab, from the OSM/ESA WorldCover switch,
// rebuilds WORLDCOVER_MIN_AREA_FIELD's own row from scratch every time)
// instead of growing forever across repeated switches.
var MAP_TAB_ROWS = [];

function tp3dRefreshMapTabVisibility() {
    MAP_TAB_ROWS = MAP_TAB_ROWS.filter(function(entry) { return entry.row.isConnected; });
    MAP_TAB_ROWS.forEach(function(entry) {
        var when = entry.field.showWhen;
        var visible = !when || MAP_TAB_CONTROL_VALUES[when.key] === when.equals;
        entry.row.style.display = visible ? '' : 'none';
    });
}

// Builds one .adv-field-row for a SETTINGS_MODAL_MAP_FIELDS-shaped field
// (number/checkbox/range) -- shared by tp3dBuildMapTab's own field list and
// the WorldCover Elements tab's single Min Feature Area row below, so both
// use the same look the Map tab already established. A field with
// `showWhen: { key, equals }` (see tp3dRefreshMapTabVisibility) mirrors one
// of panels.py's own conditional rows -- registered in MAP_TAB_ROWS
// regardless of tab, since the WorldCover Min Feature Area field has no
// `showWhen` and just always shows. A checkbox field with `companion` (its
// own number-field definition, no `label`/`type` of its own since it always
// shares the checkbox's row) instead gets that number input placed right
// next to its own checkbox, shown only while the checkbox is on -- mirrors
// panels.py's own smoothRow, which puts smoothTerrainTop and
// smoothTerrainStrength on the same row rather than as two separate ones.
function tp3dBuildFieldRow(field) {
    var row = document.createElement('div');
    row.className = 'adv-field-row';
    row.title = field.title || '';
    MAP_TAB_ROWS.push({ field: field, row: row });

    var label = document.createElement('label');
    label.textContent = field.label;
    var stateObj = field.source === 'SETTINGS_STATE' ? SETTINGS_STATE : ADVANCED_SETTINGS_STATE;

    if (field.type === 'range') {
        row.appendChild(tp3dWithPreview(label, field.preview, field.previewCaption));
        row.appendChild(tp3dBuildRangeField(field, stateObj));
        return row;
    }

    var input = document.createElement('input');
    input.type = field.type;
    // Lets a picker page find this field's input to push a value into it
    // (e.g. multitile's Dovetail toggle raising Extra Map Height).
    input.dataset.settingKey = field.key;
    if (field.type === 'number') {
        if (field.step != null) input.step = field.step;
        if (field.min != null) input.min = field.min;
        if (field.max != null) input.max = field.max;
        input.value = tp3dRoundForDisplay(stateObj[field.key], field.decimals);
    } else {
        input.checked = !!stateObj[field.key];
    }
    MAP_TAB_CONTROL_VALUES[field.key] = field.type === 'checkbox' ? input.checked : input.value;

    var controls = document.createElement('div');
    controls.className = 'adv-field-controls';
    controls.appendChild(input);

    var companionInput = null;
    if (field.companion) {
        var comp = field.companion;
        var compState = comp.source === 'SETTINGS_STATE' ? SETTINGS_STATE : ADVANCED_SETTINGS_STATE;
        companionInput = document.createElement('input');
        companionInput.type = 'number';
        companionInput.title = comp.title || '';
        if (comp.step != null) companionInput.step = comp.step;
        if (comp.min != null) companionInput.min = comp.min;
        if (comp.max != null) companionInput.max = comp.max;
        companionInput.value = tp3dRoundForDisplay(compState[comp.key], comp.decimals);
        companionInput.hidden = !input.checked;
        companionInput.addEventListener('change', function() {
            var value = parseFloat(companionInput.value);
            if (isNaN(value)) return;
            tp3dSendMapField(comp, value);
        });
        controls.appendChild(companionInput);
    }

    input.addEventListener('change', function() {
        var value = field.type === 'checkbox' ? input.checked : parseFloat(input.value);
        if (field.decimals != null && typeof value === 'number') {
            value = parseFloat(value.toFixed(field.decimals));
            input.value = value;
        }
        MAP_TAB_CONTROL_VALUES[field.key] = value;
        tp3dRefreshMapTabVisibility();
        if (companionInput) companionInput.hidden = !value;
        tp3dSendMapField(field, value);
        // Optional per-page hook (e.g. premium/map_generator_pe.html's rotated
        // shape-preview overlay) -- most pages don't define this, so it's a
        // no-op for them.
        if (window.tp3dOnMapFieldChanged) window.tp3dOnMapFieldChanged(field.key, value);
    });

    row.appendChild(tp3dWithPreview(label, field.preview, field.previewCaption));
    row.appendChild(controls);
    return row;
}

function tp3dBuildMapTab() {
    var wrap = document.createElement('div');
    wrap.className = 'map-tab-fields';
    SETTINGS_MODAL_MAP_FIELDS.forEach(function(field) {
        if (field.type === 'divider') {
            var hr = document.createElement('hr');
            hr.className = 'map-tab-divider';
            wrap.appendChild(hr);
            return;
        }
        wrap.appendChild(tp3dBuildFieldRow(field));
    });
    tp3dRefreshMapTabVisibility();
    return wrap;
}

// Two-button OSM / ESA WorldCover switch at the top of the Elements tab --
// only built when ELEMENT_SOURCE is defined (see element_status.js), i.e.
// only on premium/map_generator_pe.html; every other picker page's Elements
// tab is unaffected. Posts to the existing /update_setting route's
// 'elementSource' field (utils.ui_state._SETTINGS_ROW_FIELDS), then patches
// ELEMENT_SOURCE and the chip strip/Elements tab in place instead of
// reloading the whole page -- a reload used to close this modal right after
// the switch was clicked from inside it, which read as broken. Returns the
// whole labeled block (heading + button pair), not just the buttons.
function tp3dBuildElementSourceSwitch() {
    var block = document.createElement('div');
    block.className = 'element-source-block';

    var heading = document.createElement('div');
    heading.className = 'element-source-label';
    heading.textContent = 'Element Source';
    block.appendChild(heading);

    var wrap = document.createElement('div');
    wrap.className = 'element-source-switch';
    [['OSM', 'OpenStreetMap'], ['WORLDCOVER', 'ESA WorldCover']].forEach(function(opt) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'shape-btn' + (ELEMENT_SOURCE === opt[0] ? ' active' : '');
        btn.textContent = opt[1];
        btn.addEventListener('click', function() {
            if (ELEMENT_SOURCE === opt[0]) return;
            wrap.querySelectorAll('button').forEach(function(b) { b.disabled = true; });
            fetch('http://127.0.0.1:' + PORT + '/update_setting', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key: 'elementSource', value: opt[0] })
            }).then(function() {
                // Blender's modal timer only ticks every 0.5s -- wait for at
                // least one tick so the switch is actually applied and this
                // server's cached snapshots are refreshed
                // (picker_server.refresh_state_snapshots) before asking for
                // them back below.
                return new Promise(function(resolve) { setTimeout(resolve, 700); });
            }).then(function() {
                return fetch('http://127.0.0.1:' + PORT + '/get_source_state', { cache: 'no-store' });
            }).then(function(r) { return r.json(); })
            .then(function(s) {
                ELEMENT_SOURCE = s.elementSource;
                SETTINGS_STATE = s.settingsState;
                ADVANCED_SETTINGS_STATE = s.advancedSettings;
                ELEMENT_STATUS_ORDER = tp3dIsWorldCover() ? ELEMENT_STATUS_ORDER_WORLDCOVER : ELEMENT_STATUS_ORDER_OSM;
                TP3D_ELEMENT_STATE = {};
                ELEMENT_STATUS_ORDER.forEach(function(entry) { TP3D_ELEMENT_STATE[entry[0]] = !!s.elementStates[entry[0]]; });
                tp3dRenderElementStatus();
                window.tp3dRebuildElementsTab();
                saveState();
            })
            .catch(function() {
                wrap.querySelectorAll('button').forEach(function(b) { b.disabled = false; });
            });
        });
        wrap.appendChild(btn);
    });
    block.appendChild(wrap);
    return block;
}

function tp3dBuildElementsTab() {
    var wrap = document.createElement('div');
    wrap.className = 'elements-grid';

    if (typeof ELEMENT_SOURCE !== 'undefined') {
        wrap.appendChild(tp3dBuildElementSourceSwitch());
    }

    // ESA WorldCover has none of OSM's per-category thresholds or
    // Water/Roads composites -- one shared Min Feature Area field plus a
    // row of plain toggle cards, mirroring panels.py's "6. Map Elements"
    // WORLDCOVER branch structure.
    if (tp3dIsWorldCover()) {
        wrap.appendChild(tp3dBuildFieldRow(WORLDCOVER_MIN_AREA_FIELD));

        var landcoverRow = document.createElement('div');
        landcoverRow.className = 'elements-row';
        LANDCOVER_ELEMENT_ORDER.forEach(function(key) { landcoverRow.appendChild(tp3dBuildSimpleElementCard(key)); });
        wrap.appendChild(landcoverRow);
        return wrap;
    }

    var simpleRow = document.createElement('div');
    simpleRow.className = 'elements-row';
    SIMPLE_ELEMENT_ORDER.forEach(function(key) { simpleRow.appendChild(tp3dBuildSimpleElementCard(key)); });
    wrap.appendChild(simpleRow);

    var compositeRow = document.createElement('div');
    compositeRow.className = 'elements-row';
    COMPOSITE_ELEMENT_ORDER.forEach(function(key) { compositeRow.appendChild(tp3dBuildCompositeElementCard(key)); });
    compositeRow.appendChild(tp3dBuildSimpleElementCard('buildings'));
    wrap.appendChild(compositeRow);

    return wrap;
}

// Relocates the puzzle-cut field-rows (Tab Size, Jitter, Seed, Corner Radius)
// out of their hidden sidebar container into this tab -- same elements, same
// ids, so the page's own saveState/restoreState/regeneratePuzzle listeners
// keep working unchanged regardless of where they end up living in the DOM.
function tp3dBuildPuzzleTab() {
    var wrap = document.createElement('div');
    wrap.className = 'map-tab-fields';
    var source = document.getElementById('puzzleTabFieldsSource');
    if (source) {
        while (source.firstElementChild) {
            wrap.appendChild(source.firstElementChild);
        }
    }
    return wrap;
}

(function renderSettingsModal() {
    var elementStatus = document.getElementById('elementStatus');
    if (!elementStatus) return;

    var modal = document.createElement('div');
    modal.className = 'tp3d-modal';
    modal.id = 'settingsModal';

    var box = document.createElement('div');
    box.className = 'tp3d-modal-box settings-modal-box';
    modal.appendChild(box);

    var header = document.createElement('div');
    header.className = 'modal-header';
    var title = document.createElement('span');
    title.textContent = 'Advanced Settings';
    var closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'tp3d-modal-close-btn';
    closeBtn.title = 'Close';
    closeBtn.textContent = '✕';
    header.appendChild(title);
    header.appendChild(closeBtn);
    box.appendChild(header);

    var body = document.createElement('div');
    body.className = 'settings-modal-body';
    box.appendChild(body);

    var left = document.createElement('div');
    left.className = 'settings-modal-left';
    var tabBar = document.createElement('div');
    tabBar.className = 'settings-modal-tabs';
    var panelHost = document.createElement('div');
    panelHost.style.flex = '1';
    panelHost.style.minHeight = '0';
    panelHost.style.display = 'flex';
    left.appendChild(tabBar);
    left.appendChild(panelHost);
    body.appendChild(left);

    // Shows a per-feature preview GIF/image once a field with a `preview`
    // URL is clicked (tp3dShowPreview) -- most fields don't have one yet
    // and this just stays on its placeholder text.
    var right = document.createElement('div');
    right.className = 'settings-modal-right';
    right.id = 'settingsPreviewPanel';
    right.textContent = 'Click a highlighted setting to preview it here';
    body.appendChild(right);

    var TABS = [
        { id: 'elements', label: 'Elements', build: tp3dBuildElementsTab },
        { id: 'map', label: 'Map', build: tp3dBuildMapTab }
    ];
    if (typeof TP3D_IS_PUZZLE_PAGE !== 'undefined' && TP3D_IS_PUZZLE_PAGE) {
        TABS.push({ id: 'puzzle', label: 'Puzzle', build: tp3dBuildPuzzleTab });
    }
    var panels = {};
    var tabBtns = {};

    function activateTab(id) {
        if (!tabBtns[id]) return;
        TP3D_SETTINGS_TAB = id;
        TABS.forEach(function(tab) {
            tabBtns[tab.id].classList.toggle('active', tab.id === id);
            panels[tab.id].classList.toggle('active', tab.id === id);
        });
    }
    // Global hook so each page's own restoreState() can put the popup back
    // on whichever tab was last open, once it fetches the saved state.
    window.tp3dActivateSettingsTab = activateTab;

    TABS.forEach(function(tab) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'settings-modal-tab-btn';
        var arrow = document.createElement('span');
        arrow.className = 'tab-arrow';
        arrow.textContent = '▶';
        btn.appendChild(arrow);
        btn.appendChild(document.createTextNode(' ' + tab.label));
        btn.addEventListener('click', function() {
            activateTab(tab.id);
            saveState(); // persists TP3D_SETTINGS_TAB via the page's own state blob
        });
        tabBar.appendChild(btn);
        tabBtns[tab.id] = btn;

        var panel = document.createElement('div');
        panel.className = 'settings-modal-tab-panel';
        panel.style.width = '100%';
        panel.appendChild(tab.build());
        panelHost.appendChild(panel);
        panels[tab.id] = panel;
    });
    activateTab('elements');

    // Global hook for the OSM/ESA WorldCover switch (tp3dBuildElementSourceSwitch)
    // to rebuild just this tab's contents in place after ELEMENT_SOURCE
    // changes, instead of reloading the whole page (which used to close
    // this modal right after the switch was clicked from inside it).
    window.tp3dRebuildElementsTab = function() {
        if (!panels.elements) return;
        panels.elements.innerHTML = '';
        panels.elements.appendChild(tp3dBuildElementsTab());
        tp3dRepaintAllElementToggles();
    };

    document.body.appendChild(modal);
    // The cards' own per-card repaint ran while they were still detached from
    // the document, so it painted nothing -- do it again now they're attached.
    tp3dRepaintAllElementToggles();

    function openModal() { modal.classList.add('open'); }
    function closeModal() { modal.classList.remove('open'); }
    closeBtn.addEventListener('click', closeModal);
    modal.addEventListener('click', function(e) { if (e.target === modal) closeModal(); });

    var gearBtn = document.createElement('button');
    gearBtn.type = 'button';
    gearBtn.className = 'settings-gear-btn';
    gearBtn.title = 'More generation settings';
    gearBtn.innerHTML = '⚙️ <span>Settings</span>';
    gearBtn.addEventListener('click', openModal);
    elementStatus.insertBefore(gearBtn, elementStatus.firstChild);
})();
