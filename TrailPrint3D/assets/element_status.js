// Renders the element enabled/disabled status strip above the map, and
// establishes the shared toggle state + repaint mechanism the Settings
// modal's Elements tab (settings_modal.js) also plugs into -- reused by
// every 2D-map picker page via __ELEMENT_STATUS_JS__ in picker_server.py.
// Requires PORT, ELEMENT_ICONS (key -> recolored SVG markup, from
// __ELEMENT_ICONS_JS__) and ELEMENT_STATES (key -> bool, from
// __ELEMENT_STATES_JS__) to already be defined -- both are per-request
// snapshots the server inlines earlier in the same <script> block.
//
// 'elevation' is deliberately left out of both of these -- it has no
// toggle of its own (the base terrain height is always fetched), so there
// was nothing meaningful to show/click; its own quick setting (Elevation
// Scale) lives in the Settings modal's Map tab instead.
var ELEMENT_STATUS_ORDER_OSM = [
    ['water', 'Water'],
    ['forest', 'Forest'],
    ['scree', 'Scree'],
    ['city', 'City Boundaries'],
    ['greenspace', 'Greenspace'],
    ['farmland', 'Farmland'],
    ['glacier', 'Glacier'],
    ['buildings', 'Buildings'],
    ['roads', 'Roads']
];

// ESA WorldCover's own chip set -- mirrors panels.py's _LANDCOVER_COLOR_ROWS
// (same order). Shares the 'water'/'forest'/'city'/'greenspace'/'farmland'/
// 'glacier' keys with the OSM order above on purpose (same on-screen
// concept, different data source, and this page only ever shows one order
// at a time -- see ELEMENT_SOURCE below), reusing the same ELEMENT_ICONS
// entries; 'mountain' is WorldCover-only.
var ELEMENT_STATUS_ORDER_WORLDCOVER = [
    ['water', 'Water'],
    ['forest', 'Forest'],
    ['mountain', 'Mountain'],
    ['city', 'City Boundaries'],
    ['greenspace', 'Greenspace'],
    ['farmland', 'Farmland'],
    ['glacier', 'Glacier']
];

// ELEMENT_SOURCE (from __ELEMENT_SOURCE_JS__) is only inlined by
// premium/map_generator_pe.html today -- every other picker page leaves it
// undefined and keeps the OSM order, unaffected by whatever elementSource
// the scene happens to have.
function tp3dIsWorldCover() {
    return typeof ELEMENT_SOURCE !== 'undefined' && ELEMENT_SOURCE === 'WORLDCOVER';
}

var ELEMENT_STATUS_ORDER = tp3dIsWorldCover() ? ELEMENT_STATUS_ORDER_WORLDCOVER : ELEMENT_STATUS_ORDER_OSM;

// Shared mutable copy of ELEMENT_STATES so a click anywhere (this strip, or
// a card in the Settings modal's Elements tab) can repaint every element
// showing that key at once, not just the control that was clicked. Fire-
// and-forget/optimistic like the rest of this page's Blender round-trips:
// the actual scene property change happens on Blender's main thread the
// next time the picker's modal timer ticks (TP3D_OT_*.modal's
// drain_pending_toggles poll / utils.apply_element_toggle), which this
// page has no way to await -- but since a toggle only ever flips the same
// bit this page already computed ELEMENT_STATES from, the optimistic
// guess always matches what Blender ends up doing.
var TP3D_ELEMENT_STATE = {};
ELEMENT_STATUS_ORDER.forEach(function(entry) { TP3D_ELEMENT_STATE[entry[0]] = !!ELEMENT_STATES[entry[0]]; });

// Mirrors utils.generation._ELEMENT_COMPOSITE_FLAGS -- 'water' and 'roads'
// are each an OR of several independent sub-checkboxes with no single
// master flag on the scene PropertyGroup, so a chip/card-icon click needs
// to pick a sub-flag to actually turn on/off. Kept in sync with that Python
// dict by hand (it's small and rarely changes); ADVANCED_SETTINGS_STATE
// keys here are the camelCase versions of its snake_case attr names, same
// convention as COMPOSITE_ELEMENTS in settings_modal.js.
var TP3D_COMPOSITE_FLAGS = {
    water: { subflags: ['colWPondsActive', 'colWSmallRiversActive', 'colWBigRiversActive', 'elOActive'], bootstrap: 'colWPondsActive' },
    roads: { subflags: ['elSHighwaysActive', 'elSMajorActive', 'elSMinorActive', 'elSResidentialActive', 'elSServiceActive', 'elSFootwayActive', 'elSCycleBridleActive', 'elSTrackActive', 'elSPathActive'], bootstrap: 'elSResidentialActive' }
};

// TP3D_COMPOSITE_FLAGS is an OSM-only concept -- under WorldCover, 'water'
// is just another single-flag category (see _LANDCOVER_SINGLE_FLAGS in
// utils/ui_state.py), so the lookup must never apply here even though the
// key string is shared between the two orders above.
function tp3dCompositeIsActive(key) {
    if (tp3dIsWorldCover()) return false;
    var def = TP3D_COMPOSITE_FLAGS[key];
    return !!def && typeof ADVANCED_SETTINGS_STATE !== 'undefined'
        && def.subflags.some(function(f) { return !!ADVANCED_SETTINGS_STATE[f]; });
}

// Repaints every element with data-element-toggle="key" (this strip's chip
// and/or the modal's card icon, whichever are currently in the DOM).
function tp3dRepaintElementToggle(key) {
    var enabled = !!TP3D_ELEMENT_STATE[key];
    document.querySelectorAll('[data-element-toggle="' + key + '"]').forEach(function(el) {
        el.classList.toggle('enabled', enabled);
        el.classList.toggle('disabled', !enabled);
    });
}

// Repaints every known element key -- for after a batch of freshly-built
// (not yet in the DOM at build time) icons gets attached, e.g. the Settings
// modal's Elements tab cards, whose own per-card repaint call runs before
// they're appended anywhere and so finds nothing to paint.
function tp3dRepaintAllElementToggles() {
    Object.keys(TP3D_ELEMENT_STATE).forEach(tp3dRepaintElementToggle);
}

// Repaints a composite category's own sub-checkbox inputs (in the Settings
// modal's Elements tab, if currently built) to match ADVANCED_SETTINGS_STATE
// -- live + editable while the category is on, or showing its remembered
// combo greyed out + locked while it's off. That tab is built once and
// never re-rendered (see tp3dBuildElementsTab), so this has to reach into
// the DOM directly rather than relying on a rebuild.
function tp3dRepaintCompositeCheckboxes(key) {
    var def = TP3D_COMPOSITE_FLAGS[key];
    if (!def || typeof ADVANCED_SETTINGS_STATE === 'undefined') return;
    var active = tp3dCompositeIsActive(key);
    var remembered = (ADVANCED_SETTINGS_STATE._compositeRemembered || {})[key] || {};
    def.subflags.forEach(function(f) {
        var checked = active ? !!ADVANCED_SETTINGS_STATE[f] : !!remembered[f];
        document.querySelectorAll('[data-advanced-checkbox="' + f + '"]').forEach(function(el) {
            el.checked = checked;
            el.disabled = !active;
            if (el.closest('label')) el.closest('label').classList.toggle('locked', !active);
        });
    });
}

// Predicts what utils.apply_element_toggle will do server-side for a
// composite category -- same remember-on-off / restore-on-on logic, kept
// in ADVANCED_SETTINGS_STATE._compositeRemembered so a fresh page load and
// a same-session chip click agree -- and applies it optimistically to
// ADVANCED_SETTINGS_STATE + the modal's checkboxes.
function tp3dToggleElement(key) {
    TP3D_ELEMENT_STATE[key] = !TP3D_ELEMENT_STATE[key];
    tp3dRepaintElementToggle(key);

    var def = tp3dIsWorldCover() ? null : TP3D_COMPOSITE_FLAGS[key];
    if (def && typeof ADVANCED_SETTINGS_STATE !== 'undefined') {
        ADVANCED_SETTINGS_STATE._compositeRemembered = ADVANCED_SETTINGS_STATE._compositeRemembered || {};
        if (tp3dCompositeIsActive(key)) {
            var snapshot = {};
            def.subflags.forEach(function(f) { snapshot[f] = !!ADVANCED_SETTINGS_STATE[f]; });
            ADVANCED_SETTINGS_STATE._compositeRemembered[key] = snapshot;
            def.subflags.forEach(function(f) { ADVANCED_SETTINGS_STATE[f] = false; });
        } else {
            var remembered = ADVANCED_SETTINGS_STATE._compositeRemembered[key] || {};
            var hasRemembered = def.subflags.some(function(f) { return !!remembered[f]; });
            if (hasRemembered) {
                def.subflags.forEach(function(f) { ADVANCED_SETTINGS_STATE[f] = !!remembered[f]; });
            } else {
                ADVANCED_SETTINGS_STATE[def.bootstrap] = true;
            }
        }
        tp3dRepaintCompositeCheckboxes(key);
    }

    fetch('http://127.0.0.1:' + PORT + '/toggle_element', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: key })
    }).catch(function() {});
}

// Named (not an IIFE) so premium/map_generator_pe.html's OSM/ESA WorldCover
// switch (settings_modal.js) can call this again after patching
// ELEMENT_SOURCE/ELEMENT_STATUS_ORDER/TP3D_ELEMENT_STATE in place, instead
// of reloading the whole page (which used to close the Settings modal the
// switch was clicked from).
function tp3dRenderElementStatus() {
    var container = document.getElementById('elementStatus');
    if (!container) return;
    // Clears only the previously-rendered chips -- not the Settings gear
    // button, which settings_modal.js prepends into this same container --
    // so a re-render after switching source doesn't disturb it.
    container.querySelectorAll('.element-chip-wrap').forEach(function(el) { el.remove(); });

    ELEMENT_STATUS_ORDER.forEach(function(entry) {
        var key = entry[0], label = entry[1];
        var svg = ELEMENT_ICONS[key];
        if (!svg) return;
        var chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'element-chip';
        chip.setAttribute('data-element-toggle', key);
        var iconWrap = document.createElement('span');
        iconWrap.className = 'element-icon';
        iconWrap.innerHTML = svg;
        chip.appendChild(iconWrap);
        var labelEl = document.createElement('span');
        labelEl.className = 'element-label';
        labelEl.textContent = label;
        chip.appendChild(labelEl);
        chip.title = label + ' (click to toggle)';

        chip.addEventListener('click', function() { tp3dToggleElement(key); });
        var wrap = document.createElement('div');
        wrap.className = 'element-chip-wrap';
        wrap.appendChild(chip);
        tp3dAttachSubFlyout(wrap, chip, key);
        // Before the Prefetch bar (which is right-aligned via margin-left:auto)
        // so a re-render after switching source keeps the chips on the left.
        container.insertBefore(wrap, container.querySelector('.prefetch-bar'));
        tp3dRepaintElementToggle(key);
    });
    if (typeof prefetchSyncSource === 'function') prefetchSyncSource();
}

// Hover flyout under the Water / Roads chips with one checkbox per
// sub-category (COMPOSITE_ELEMENTS in settings_modal.js -- same fields and
// same /update_advanced_setting route as the Settings modal's Elements tab).
// position:fixed so the strip's overflow-x scrolling can't clip it. The
// checkboxes carry data-advanced-checkbox, so tp3dRepaintCompositeCheckboxes
// keeps them in sync with chip toggles and the modal.
function tp3dAttachSubFlyout(wrap, chip, key) {
    if (tp3dIsWorldCover() || !TP3D_COMPOSITE_FLAGS[key]) return;
    var flyout = null, hideTimer = null;

    function build() {
        var def = typeof COMPOSITE_ELEMENTS !== 'undefined' ? COMPOSITE_ELEMENTS[key] : null;
        if (!def || typeof ADVANCED_SETTINGS_STATE === 'undefined') return null;
        var el = document.createElement('div');
        el.className = 'element-flyout';
        var remembered = (ADVANCED_SETTINGS_STATE._compositeRemembered || {})[key] || {};
        var active = tp3dCompositeIsActive(key);
        def.checkboxes.forEach(function(field) {
            var label = document.createElement('label');
            var input = document.createElement('input');
            input.type = 'checkbox';
            input.setAttribute('data-advanced-checkbox', field.key);
            input.checked = active ? !!ADVANCED_SETTINGS_STATE[field.key] : !!remembered[field.key];
            input.disabled = !active;
            if (!active) label.classList.add('locked');
            input.addEventListener('change', function() {
                ADVANCED_SETTINGS_STATE[field.key] = input.checked;
                // Unticking the last sub-category turns the whole element off
                // (and ticking one back on turns it on), mirroring the chip.
                TP3D_ELEMENT_STATE[key] = tp3dCompositeIsActive(key);
                tp3dRepaintElementToggle(key);
                if (typeof tp3dSendAdvancedUpdate === 'function') tp3dSendAdvancedUpdate(field.key, input.checked);
            });
            label.appendChild(input);
            label.appendChild(document.createTextNode(field.label));
            el.appendChild(label);
        });
        wrap.appendChild(el);
        return el;
    }

    function show() {
        clearTimeout(hideTimer);
        if (flyout) flyout.remove();
        flyout = build();
        if (!flyout) return;
        var r = chip.getBoundingClientRect();
        flyout.style.left = r.left + 'px';
        flyout.style.top = r.bottom + 'px';
    }
    function hide() {
        clearTimeout(hideTimer);
        hideTimer = setTimeout(function() { if (flyout) { flyout.remove(); flyout = null; } }, 150);
    }
    wrap.addEventListener('mouseenter', show);
    wrap.addEventListener('mouseleave', hide);
}
tp3dRenderElementStatus();
