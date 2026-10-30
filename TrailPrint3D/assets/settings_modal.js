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
// Tabs:
//  - Map: the handful of generation-wide fields that don't belong to any
//    one element category (Elevation Scale, Path Thickness, ...).
//  - Elements: every element category's individual sub-checkboxes and
//    per-category thresholds, grouped as cards -- the "much more detailed"
//    breakdown behind each #elementStatus chip's single on/off summary.
//    A simple category (Forest, Buildings, ...) is one card with its own
//    toggle icon and threshold field(s); Water and Roads are wider
//    composite cards with a checklist of sub-flags plus their own number
//    fields, alongside the same quick toggle icon.
//  - Other: the elevation API, its cache toggle and Clear Cache (panels.py's
//    API box), plus rarely changed settings that fit nowhere else (texture
//    resolution).
//
// The right-hand panel is the "Setting guide": on wide modals it's always
// visible and updates as the user hovers or selects a setting; on narrow
// modals it stays hidden until a setting's "?" is clicked. See the
// tp3dGuide* functions below.
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
// tp3dAlert and dialog focus management live in picker_ui.js so the
// 3D-preview generator uses the same notice and keyboard behavior.

var TP3D_SETTINGS_TAB = 'elements';

// Resolution deliberately isn't in this list -- every picker page already
// has its own page-level #resolutionSlider (outside this modal), and that's
// the one the /confirm handler actually applies (see operators.py: it
// overwrites props.num_subdivisions from the payload's own 'resolution' at
// Send time regardless of anything pushed live via this modal). Having both
// meant two controls for one setting that could silently disagree.
//
// Grouped and ordered to mirror panels.py's own "4. Terrain" then "3. Trail"
// sections (in that order, since Elevation was already the modal's first
// field before this list grew) -- same fields, same conditional visibility
// (`showWhen`, see tp3dRefreshMapTabVisibility) as the sidebar's own
// `if props.smoothTerrainTop` / `if props.singleColorMode` branches in
// panels.py. Elevation Mode itself is deliberately left out -- Fixed Height
// mode is a niche option not worth the extra control here.
//
// `{ type: 'section', title }` starts a new titled card (tp3dBuildFieldList);
// `{ type: 'subsection', title, fields }` nests a smaller card inside the
// current one for fields that only matter in one mode.
var SETTINGS_MODAL_MAP_FIELDS = [
    // -- Terrain (panels.py "4. Terrain") --
    { type: 'section', title: 'Terrain & Geometry' },
    { key: 'scaleElevation', source: 'SETTINGS_STATE', endpoint: 'update_setting',
      label: 'Elevation Scale', type: 'number', step: 0.1, min: 0,
      title: 'Multiplier to the Elevation',
      preview: 'https://trailprint3d.com/images/howto/ElevationScaleGif.webp',
      previewCaption: 'Multiplier to the scale of the elevation.' },
    { key: 'minThickness', source: 'SETTINGS_STATE', endpoint: 'update_setting',
      label: 'Extra Map Height', type: 'number', step: 0.5, min: 0.5, unit: 'mm',
      title: 'Extra height added to the map, below the terrain',
      preview: 'https://trailprint3d.com/images/howto/ExtraMapHeight.webp' },
    // Shape Rotation is deliberately left out -- it stays a Blender-sidebar
    // setting (history_panel.js reads it from SETTINGS_STATE instead).
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
                   type: 'range', label: 'Smoothing strength', step: 1, min: 0, max: 10,
                   title: 'Number of smoothing passes -- higher is smoother but less detailed' } },
    // -- Trail (panels.py "3. Trail") --
    { type: 'section', title: 'Trail' },
    // Normal / Single Extruder Mode / Paint Trail -- one switch over
    // singleColorMode and texIncludeTrail (tp3dBuildTrailAppearanceSwitch).
    { type: 'custom', build: tp3dBuildTrailAppearanceSwitch },
    { key: 'pathThickness', source: 'SETTINGS_STATE', endpoint: 'update_setting',
      label: 'Trail Thickness', type: 'number', step: 0.01, min: 0.1, decimals: 2,
      title: 'Thickness of the path in mm',
      preview: 'https://trailprint3d.com/images/howto/PathThicknessGif.webp',
      previewCaption: 'Thickness of the trail in mm.' },
    { type: 'subsection', title: 'Single Extruder Mode', fields: [
        { key: 'singleColorModeHeight', source: 'SETTINGS_STATE', endpoint: 'update_setting',
          label: 'Trail Height', type: 'number', step: 0.05, min: 0, max: 10,
          title: 'How far the SCM trail strip rises above the terrain surface (mm)',
          preview: 'https://trailprint3d.com/images/howto/SEM%20-%20Trail%20Height.webp',
          previewCaption: 'How far the trail sticks out of the map in Single Extruder Mode.',
          enabledWhen: tp3dTrailIsSingleExtruder },
        { key: 'singleColorModeTolerance', source: 'SETTINGS_STATE', endpoint: 'update_setting',
          label: 'Tolerance', type: 'number', step: 0.05, min: 0,
          title: 'Tolerance of the Trail for the Single Extruder Mode',
          preview: 'https://trailprint3d.com/images/howto/SEM%20-%20Trail%20Tolerance.webp',
          previewCaption: 'Tolerance of the trail to the map in Single Extruder Mode.',
          enabledWhen: tp3dTrailIsSingleExtruder }
    ] }
];

// Other tab -- rarely changed settings that don't fit the other tabs.
// The section title is now supplied by tp3dBuildOtherTab's "Appearance" card.
var SETTINGS_MODAL_OTHER_FIELDS = [
    { key: 'texResolution', source: 'SETTINGS_STATE', endpoint: 'update_setting',
      label: 'Texture Resolution', type: 'number', step: 1024, min: 1024, max: 8192,
      title: 'Image resolution of the texture (px)',
      preview: 'https://trailprint3d.com/images/howto/TextureResolution.webp',
      previewCaption: 'Resolution of the texture the map is painted with in Paint mode — used for both OSM and ESA WorldCover.',
      enabledWhen: tp3dTextureAvailable,
      lockedTitle: tp3dNo3mfTitle },
    { key: 'overwritePathElevation', source: 'SETTINGS_STATE', endpoint: 'update_setting',
      label: 'Snap Trail to Terrain', type: 'checkbox',
      title: 'Cast each point of the trail onto the Terrain Mesh — should usually always be enabled',
      preview: 'https://trailprint3d.com/images/howto/SnapTrailToTerrain.jpg',
      previewCaption: [
          'GPX files usually have their own elevation data, but sometimes they don\'t, and sometimes their values come from a different dataset.',
          'To make sure the trail and terrain match perfectly, enable this to snap each point of the trail onto the terrain.',
          'This should usually always be enabled.'
      ] }
];

// The texture only exists in Paint mode (panels.py only offers "Create a
// texture" there), and a SingleColorMode trail is its own object, so neither
// it nor the roads can go into the texture then -- mirrors the `enabled`
// conditions of panels.py's trail/road texture rows.
function tp3dTexturePaintMode() {
    return SETTINGS_STATE.elementMode === 'PAINT';
}
// The texture itself also needs the 3MF addon: tex_use_texture defaults to
// temp.has3mf (props.py) and panels.py only draws the Appearance box with it.
// SETTINGS_STATE.has3mf flips live once threemf_promo.js's install finishes.
function tp3dHas3mf() {
    return !!SETTINGS_STATE.has3mf;
}
function tp3dTextureAvailable() {
    return tp3dHas3mf() && tp3dTexturePaintMode();
}
var TP3D_NO_3MF_TITLE = 'Requires the free 3MF addon';
function tp3dNo3mfTitle() {
    return tp3dHas3mf() ? '' : TP3D_NO_3MF_TITLE;
}
// Single Extruder elements force a Single Extruder trail too -- panels.py
// shows singleColorMode checked and locked then, with Height/Tolerance live.
function tp3dTrailIsSingleExtruder() {
    return MAP_TAB_CONTROL_VALUES.singleColorMode === true || !tp3dTexturePaintMode();
}

// "Trail Appearance": Normal / Single Extruder Mode / Paint Trail, writing
// the two scene flags behind it (singleColorMode, texIncludeTrail) so at
// most one is on. Locked on Single Extruder Mode while the elements are in
// Single Extruder mode -- mirrors panels.py's `enabled` conditions for
// those two flags. `preview`/`caption` add a "?" right after that option
// (caption only = a text-only preview, see tp3dShowPreview).
// `desc` is the short line shown under each option's name.
var TRAIL_APPEARANCE_OPTIONS = [
    { mode: 'NORMAL', label: 'Normal', desc: 'Print trail together with map',
      title: 'The trail is its own object',
      preview: 'https://trailprint3d.com/images/howto/normalTrail.webp',
      caption: 'The trail is printed together with the map as its own colored object.' },
    { mode: 'SCM', label: 'Single Extruder Mode', desc: 'Print the Trail Separately',
      title: 'Enable this if you don\'t have a Multicolor printer',
      preview: 'https://trailprint3d.com/images/howto/SingleColorTrail.webp',
      caption: 'Print the trail separately and put it into the cutout afterwards.' },
    { mode: 'PAINT', label: 'Paint Trail', desc: 'Painted into the map texture',
      title: 'Paint the trail into the texture (terrain under the trail coloured red)',
      preview: 'https://trailprint3d.com/images/howto/PaintTrail.webp',
      caption: 'Paint the trail onto the map instead of creating it as a separate object.' }
];
function tp3dBuildTrailAppearanceSwitch() {
    var row = document.createElement('div');
    row.className = 'adv-field-row trail-appearance-row';
    row.title = 'How the trail is printed';

    var label = document.createElement('label');
    label.textContent = 'Trail Appearance';
    row.appendChild(label);

    MAP_TAB_CONTROL_VALUES.singleColorMode = !!SETTINGS_STATE.singleColorMode;
    MAP_TAB_CONTROL_VALUES.texIncludeTrail = !SETTINGS_STATE.singleColorMode && !!SETTINGS_STATE.texIncludeTrail;

    var wrap = document.createElement('div');
    wrap.className = 'trail-appearance-switch';
    var buttons = {};
    // Each option is one pill: its select button plus, when it has a
    // preview, a small "?" inside the same frame (siblings, since a button
    // can't contain another button).
    var options = {};
    TRAIL_APPEARANCE_OPTIONS.forEach(function(opt) {
        var pill = document.createElement('span');
        pill.className = 'trail-option';
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'trail-option-btn';
        btn.title = opt.title;
        var name = document.createElement('span');
        name.className = 'trail-option-name';
        name.textContent = opt.label;
        var desc = document.createElement('span');
        desc.className = 'trail-option-desc';
        desc.textContent = opt.desc || '';
        btn.appendChild(name);
        btn.appendChild(desc);
        btn.addEventListener('click', function() { select(opt.mode); });
        pill.appendChild(btn);
        if (opt.preview || opt.caption) pill.appendChild(tp3dMakePreviewButton(opt.preview, opt.caption, opt.label, pill));
        wrap.appendChild(pill);
        buttons[opt.mode] = btn;
        options[opt.mode] = pill;
    });
    row.appendChild(wrap);

    function current() {
        if (MAP_TAB_CONTROL_VALUES.singleColorMode) return 'SCM';
        return MAP_TAB_CONTROL_VALUES.texIncludeTrail ? 'PAINT' : 'NORMAL';
    }
    function send(key, value) {
        if (MAP_TAB_CONTROL_VALUES[key] === value) return;
        MAP_TAB_CONTROL_VALUES[key] = value;
        tp3dSendMapField({ key: key, endpoint: 'update_setting' }, value);
        if (window.tp3dOnMapFieldChanged) window.tp3dOnMapFieldChanged(key, value);
    }
    function select(mode) {
        // Turn the old flag off before the new one goes on.
        if (mode !== 'SCM') send('singleColorMode', false);
        if (mode !== 'PAINT') send('texIncludeTrail', false);
        if (mode === 'SCM') send('singleColorMode', true);
        if (mode === 'PAINT') send('texIncludeTrail', true);
        tp3dRefreshMapTabVisibility();
    }

    // Repainted by tp3dRefreshMapTabVisibility via the `apply` hook, so it
    // follows the Elements tab's Paint/Single Extruder switch too. Only the
    // option buttons get locked -- the "?" previews stay clickable.
    // Paint Trail alone also locks without the 3MF addon (no texture to
    // paint into), showing Normal -- what Blender then actually builds.
    var paintTitle = buttons.PAINT.title;
    MAP_TAB_ROWS.push({ field: { apply: function() {
        var forcedScm = !tp3dTexturePaintMode();
        var noPaint = !tp3dHas3mf();
        var shown = forcedScm ? 'SCM' : current();
        if (shown === 'PAINT' && noPaint) shown = 'NORMAL';
        Object.keys(buttons).forEach(function(mode) {
            var locked = forcedScm || (mode === 'PAINT' && noPaint);
            options[mode].classList.toggle('active', mode === shown);
            options[mode].classList.toggle('disabled', locked);
            options[mode].classList.toggle('no-3mf', mode === 'PAINT' && noPaint);
            buttons[mode].disabled = locked;
        });
        buttons.PAINT.title = noPaint ? TP3D_NO_3MF_TITLE : paintTitle;
        wrap.title = forcedScm ? 'Locked: the elements are in Single Extruder mode (Elements tab)' : '';
    } }, row: row });
    return row;
}

// Other tab's API section -- mirrors panels.py's _draw_api_box: the API
// select, then (only for the API they belong to, `hideWhenInactive`) its
// dataset select or the Local DEM file/folder row (tp3dBuildDemPathRow).
// Opentopodata's self-hosted URL and the OpenTopography key stay
// sidebar/preferences-only; API_SETUP_NOTES points there for the key.
var SETTINGS_MODAL_API_FIELDS = [
    { key: 'api', source: 'SETTINGS_STATE', endpoint: 'update_setting',
      label: 'Elevation API', type: 'select',
      title: 'Which service the terrain elevation is downloaded from',
      previewCaption: [
          'Elevation API',
          'The service the terrain heights are downloaded from — or your own DEM file.',
          'They differ in speed, detail, coverage and limits; the overview below lists them. Click a name to open its website.'
      ],
      // Same order and identifiers as props.py's `api` EnumProperty.
      options: [
          ['MAPTERHORN', 'Mapterhorn'],
          ['OPENTOPODATA', 'Opentopodata'],
          ['TERRAIN-TILES', 'Terrain-Tiles'],
          ['OPENTOPOGRAPHY', 'OpenTopography'],
          ['LOCAL_DEM', 'Local DEM File']
      ] },
    // Same identifiers as props.py's `dataset` / `openTopographyDataset`.
    { key: 'dataset', source: 'SETTINGS_STATE', endpoint: 'update_setting',
      label: 'Dataset', type: 'select',
      showWhen: { key: 'api', equals: 'OPENTOPODATA' }, hideWhenInactive: true,
      title: 'Which Opentopodata dataset the elevation is read from',
      previewCaption: [
          'Opentopodata Dataset',
          'Each dataset covers a different area at a different resolution. Outside its coverage the terrain comes out flat.'
      ],
      options: [
          ['srtm30m', 'SRTM 30m — latitudes -60 to 60'],
          ['aster30m', 'ASTER 30m — global'],
          ['ned10m', 'NED 10m — continental USA, Hawaii, parts of Alaska'],
          ['mapzen', 'Mapzen — global'],
          ['nzdem8m', 'NZ DEM 8m — New Zealand'],
          ['eudem25m', 'EU-DEM 25m — Europe']
      ] },
    { key: 'openTopographyDataset', source: 'SETTINGS_STATE', endpoint: 'update_setting',
      label: 'Dataset', type: 'select',
      showWhen: { key: 'api', equals: 'OPENTOPOGRAPHY' }, hideWhenInactive: true,
      title: 'Which OpenTopography dataset the elevation is read from',
      previewCaption: [
          'OpenTopography Dataset',
          'Global datasets at different resolutions. SRTM15+ is the fastest download but the least detailed.'
      ],
      options: [
          ['SRTMGL1', 'SRTM GL1 (30m)'],
          ['SRTMGL3', 'SRTM GL3 (90m)'],
          ['SRTM15Plus', 'SRTM15+ (500m)'],
          ['AW3D30', 'ALOS AW3D30 (30m)'],
          ['NASADEM', 'NASADEM (30m)'],
          ['COP30', 'Copernicus 30m'],
          ['COP90', 'Copernicus 90m']
      ] },
    { key: 'disableCache', source: 'SETTINGS_STATE', endpoint: 'update_setting',
      label: 'Disable Cache', type: 'checkbox',
      // Every API: besides the per-API tile/point caches it also skips the
      // elevation results, OSM and satellite caches.
      title: 'Always download fresh elevation, map element and satellite data instead of reusing cached results',
      previewCaption: [
          'Disable Cache',
          'Downloaded data (elevation, map elements, satellite images) is saved on your computer, so generating the same area again is much faster.',
          'Turn this on to always download fresh data instead — e.g. if a download was broken or the source data has been updated.'
      ] }
];
// One line per API, shown under the dropdown. `url` (same links as the
// sidebar's attribution box) makes the API's name open its website.
var API_SUMMARIES = {
    'MAPTERHORN': { text: 'Fastest and most detailed API, with wide coverage.',
                    url: 'https://github.com/mapterhorn/mapterhorn' },
    'OPENTOPODATA': { text: 'High accuracy, but by far the slowest API.',
                      url: 'https://www.opentopodata.org/' },
    'TERRAIN-TILES': { text: 'As fast as Mapterhorn, but quite often has buggy data.',
                       url: 'https://registry.opendata.aws/terrain-tiles/' },
    'OPENTOPOGRAPHY': { text: 'Different datasets, fast, detailed. Requires a registered API key (free).',
                        url: 'https://opentopography.org/' },
    'LOCAL_DEM': { text: 'Your own GeoTIFF DEM file.' }
};
var API_SETUP_NOTES = [
    { text: 'ⓘ Needs an API key — set it in the TrailPrint3D addon preferences',
      showWhen: { key: 'api', equals: 'OPENTOPOGRAPHY' } }
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
// Shared by every element's area threshold (and Water's Lake/Pond one).
var ELEMENT_THRESHOLD_PREVIEW = 'https://trailprint3d.com/images/howto/ElementTreshold.webp';
var ELEMENT_THRESHOLD_CAPTION = 'Filter out smaller occurrences of the elements for simpler printing.';
var SIMPLE_ELEMENT_FIELDS = {
    forest: [{ key: 'colFArea', label: 'Area Threshold', step: 0.5, min: 0, preview: ELEMENT_THRESHOLD_PREVIEW, previewCaption: ELEMENT_THRESHOLD_CAPTION }],
    scree: [{ key: 'colScrArea', label: 'Area Threshold', step: 0.5, min: 0, preview: ELEMENT_THRESHOLD_PREVIEW, previewCaption: ELEMENT_THRESHOLD_CAPTION }],
    city: [{ key: 'colCArea', label: 'Area Threshold', step: 0.5, min: 0, preview: ELEMENT_THRESHOLD_PREVIEW, previewCaption: ELEMENT_THRESHOLD_CAPTION }],
    greenspace: [{ key: 'colGrArea', label: 'Area Threshold', step: 0.5, min: 0, preview: ELEMENT_THRESHOLD_PREVIEW, previewCaption: ELEMENT_THRESHOLD_CAPTION }],
    farmland: [{ key: 'colFaArea', label: 'Area Threshold', step: 0.5, min: 0, preview: ELEMENT_THRESHOLD_PREVIEW, previewCaption: ELEMENT_THRESHOLD_CAPTION }],
    glacier: [{ key: 'colGlArea', label: 'Area Threshold', step: 0.5, min: 0, preview: ELEMENT_THRESHOLD_PREVIEW, previewCaption: ELEMENT_THRESHOLD_CAPTION }],
    buildings: [
        { key: 'elBHeightMultiplier', label: 'Height Multiplier', step: 0.1, min: 0.01,
          preview: 'https://trailprint3d.com/images/howto/BuildingsHeight.webp',
          previewCaption: 'Multiplier to the height of the buildings.' },
        { key: 'elBMinPrintMM', label: 'Min Footprint (mm)', step: 0.01, min: 0,
          preview: 'https://trailprint3d.com/images/howto/BuildingSizeFootprint.webp',
          previewCaption: 'Filter out buildings whose footprint is below this size.' }
    ]
};

// ESA WorldCover's own card order -- no per-category threshold fields (see
// SIMPLE_ELEMENT_FIELDS not having entries for these keys below); a single
// shared field (WORLDCOVER_MIN_AREA_FIELD) covers all of them at once,
// mirroring panels.py's "5. Map Elements" layout (one el_wcMinFeatureArea
// row above the whole _LANDCOVER_COLOR_ROWS box, not one per category).
var LANDCOVER_ELEMENT_ORDER = ['water', 'forest', 'mountain', 'city', 'greenspace', 'farmland', 'glacier'];
// OSM-only, next to Element Smoothing like panels.py's _osm_processing_box,
// and greyed out outside Single Extruder mode the same way.
var ELEMENT_TOLERANCE_FIELD = {
    key: 'toleranceElements', source: 'ADVANCED_SETTINGS_STATE', endpoint: 'update_advanced_setting',
    label: 'Element Tolerance', type: 'number', step: 0.01, min: 0, decimals: 2,
    title: 'Clearance around the elements (water, forest, ...) in Single Extruder Mode',
    preview: 'https://trailprint3d.com/images/howto/ElementTolerance.webp',
    previewCaption: 'Tolerance between the elements.',
    enabledWhen: function() { return SETTINGS_STATE.elementMode === 'SINGLECOLORMODE_REMESH'; }
};
var COASTLINE_SIMPLIFY_FIELD = {
    key: 'elORdpEpsilon', source: 'ADVANCED_SETTINGS_STATE', endpoint: 'update_advanced_setting',
    label: 'Coastline Simplify', type: 'number', step: 0.01, min: 0, max: 2, decimals: 3,
    manualDefault: 0.1,
    preview: 'https://trailprint3d.com/images/howto/CoastlineSimplify.webp',
    // Coastlines come from OSM -- nothing to simplify for ESA WorldCover.
    enabledWhen: function() { return !tp3dIsWorldCover(); },
    title: 'Check to enter a manual Douglas-Peucker coastline tolerance (map units). Unchecked uses 0: automatic simplification scaled with Element Smoothing, not disabled simplification. Positive values are used as-is, followed by Element Smoothing as usual. Higher values remove detail, may delete thin features, and can introduce self-intersections. Use 0.001 to effectively disable simplification.'
};
var WORLDCOVER_MIN_AREA_FIELD = {
    key: 'elWcMinFeatureArea', source: 'ADVANCED_SETTINGS_STATE', endpoint: 'update_advanced_setting',
    label: 'Min Feature Area', type: 'number', step: 0.5, min: 0,
    title: 'Smallest land-cover feature area to keep before it gets discarded',
    preview: 'https://trailprint3d.com/images/howto/ESA_MinFeatureSize.webp'
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
            { key: 'colWArea', label: 'Lake/Pond Threshold (in mm²)', step: 0.1, min: 0, preview: ELEMENT_THRESHOLD_PREVIEW, previewCaption: ELEMENT_THRESHOLD_CAPTION },
            { key: 'colWStreamWidth', label: 'River Width (multiplicator)', step: 0.1, min: 0.1, max: 100 },
            { key: 'elOMinIslandArea', label: 'Min Island Area (in mm²)', step: 0.5, min: 0 }
        ],
        extraFields: tp3dBuildWaterFlattenFields
    },
    roads: {
        checkboxes: [
            { key: 'elSHighwaysActive', label: 'Highways' },
            { key: 'elSMajorActive', label: 'Major Roads' },
            { key: 'elSMinorActive', label: 'Minor Roads' },
            { key: 'elSResidentialActive', label: 'Residential Roads' },
            { key: 'elSServiceActive', label: 'Service Roads' },
            { key: 'elSFootwayActive', label: 'Footways/Sidewalks' },
            { key: 'elSPedestrianActive', label: 'Pedestrian Streets' },
            { key: 'elSCycleBridleActive', label: 'Cycle/Bridle Paths' },
            { key: 'elSTrackActive', label: 'Tracks' },
            { key: 'elSPathActive', label: 'Trails/Paths' },
            { key: 'elSRacewayActive', label: 'Raceways' }
        ],
        numberFields: [
            { key: 'elSMultiplier', label: 'Width Multiplier', step: 0.1, min: 0 },
            { key: 'elSHeight', label: 'Height', step: 0.05, min: 0,
              preview: 'https://trailprint3d.com/images/howto/RoadHeight.webp',
              previewCaption: 'How high the roads stick out of the map.' },
            { key: 'elSCutTolerance', label: 'Cut Tolerance', step: 0.05, min: 0, singleExtruderOnly: true,
              preview: 'https://trailprint3d.com/images/howto/RoadCutTolerance.webp',
              previewCaption: 'Tolerance for the roads in Single Extruder Mode.' }
        ],
        extraFields: tp3dBuildRoadsPaintField
    }
};

function tp3dSendAdvancedUpdate(key, value) {
    fetch('http://127.0.0.1:' + PORT + '/update_advanced_setting', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: key, value: value })
    }).catch(function() {});
}

// ── Setting guide: preview panel that updates on hover/selection ────────
// A registry maps each guide-able element's data-guide-id to its
// {title, url, caption}. The panel itself is built once (in
// renderSettingsModal below) and entries are swapped in place. Wide modals
// (>= 70rem) show it by default; narrow modals start with it hidden and
// reveal it (stacked under the settings) when a "?" is clicked or the header
// Show button is used. Content logic is shared; only visibility differs.
var TP3D_GUIDE_REGISTRY = {};
var TP3D_GUIDE_NEXT_ID = 0;
var TP3D_GUIDE_STATE = { pinnedId: null, hoveredId: null, hoverTimer: null, leaveTimer: null, imageToken: 0 };
var TP3D_GUIDE_HOVER_OK = !!(window.matchMedia && window.matchMedia('(hover: hover) and (pointer: fine)').matches);
// Visibility. Two separate things, so a choice made in one layout can't
// leak into the other:
//  - TP3D_GUIDE_VISIBLE: what is shown right now (runtime only).
//  - TP3D_GUIDE_PREF_WIDE: the saved preference, changed ONLY by the header
//    toggle while the modal is wide. Narrow modals always start hidden and
//    never write the preference. tp3dGetGuideVisible/tp3dSetGuideVisible are
//    what the pages' state snapshots call, so they read/write the preference.
var TP3D_GUIDE_WIDE_MQ = window.matchMedia ? window.matchMedia('(min-width: 70.01rem)') : null;
function tp3dGuideIsWide() { return TP3D_GUIDE_WIDE_MQ ? TP3D_GUIDE_WIDE_MQ.matches : true; }
var TP3D_GUIDE_PREF_WIDE = true;
var TP3D_GUIDE_VISIBLE = tp3dGuideIsWide();
var TP3D_GUIDE_TOGGLE_BTN = null;
window.tp3dGetGuideVisible = function() { return TP3D_GUIDE_PREF_WIDE; };
window.tp3dSetGuideVisible = function(visible) {
    TP3D_GUIDE_PREF_WIDE = !!visible;
    if (tp3dGuideIsWide()) tp3dGuideApplyVisible(TP3D_GUIDE_PREF_WIDE);
};
function tp3dGuideApplyVisible(visible) {
    TP3D_GUIDE_VISIBLE = !!visible;
    var panel = tp3dGuidePanel();
    if (panel) {
        var box = panel.closest('.settings-modal-box');
        if (box) box.classList.toggle('guide-hidden', !TP3D_GUIDE_VISIBLE);
    }
    if (TP3D_GUIDE_TOGGLE_BTN) {
        TP3D_GUIDE_TOGGLE_BTN.textContent = TP3D_GUIDE_VISIBLE ? 'Hide guide' : 'Show guide';
        TP3D_GUIDE_TOGGLE_BTN.setAttribute('aria-expanded', String(TP3D_GUIDE_VISIBLE));
    }

    // Toggle the native hover text on the "?" buttons. When the guide is
    // open, we rely on the faded visual and the panel itself, so we strip
    // the native tooltip to prevent it from overlapping the UI.
    document.querySelectorAll('.preview-btn').forEach(function(btn) {
        if (TP3D_GUIDE_VISIBLE) {
            btn.removeAttribute('title');
        } else {
            btn.setAttribute('title', 'Click to preview');
        }
    });
    
    // Clear stale pin/hover when hiding, so a later Show doesn't resurrect
    // whatever was showing when the user hid it.
    if (!TP3D_GUIDE_VISIBLE) {
        TP3D_GUIDE_STATE.pinnedId = null;
        TP3D_GUIDE_STATE.hoveredId = null;
        tp3dGuideMarkPinned();
    } else {
        tp3dGuideDisplay();
    }
}
function tp3dGuideUserToggle() {
    var next = !TP3D_GUIDE_VISIBLE;
    tp3dGuideApplyVisible(next);
    if (tp3dGuideIsWide()) {
        TP3D_GUIDE_PREF_WIDE = next;
        if (typeof saveState === 'function') saveState();
    }
}
// Crossing the breakpoint while the modal is open: wide -> saved preference,
// narrow -> hidden (the stacked guide is opt-in via "?" / Show).
if (TP3D_GUIDE_WIDE_MQ) {
    var tp3dGuideOnResize = function() {
        tp3dGuideApplyVisible(tp3dGuideIsWide() ? TP3D_GUIDE_PREF_WIDE : false);
    };
    if (TP3D_GUIDE_WIDE_MQ.addEventListener) TP3D_GUIDE_WIDE_MQ.addEventListener('change', tp3dGuideOnResize);
    else if (TP3D_GUIDE_WIDE_MQ.addListener) TP3D_GUIDE_WIDE_MQ.addListener(tp3dGuideOnResize);
}

function tp3dRegisterGuide(title, url, caption) {
    var id = 'tp3dGuide' + (++TP3D_GUIDE_NEXT_ID);
    TP3D_GUIDE_REGISTRY[id] = { title: title || '', url: url || null, caption: caption || null };
    return id;
}

function tp3dGuidePanel() { return document.getElementById('settingsPreviewPanel'); }

function tp3dGuideRenderDefault() {
    var panel = tp3dGuidePanel();
    if (!panel) return;
    // Visibility is driven entirely by .guide-hidden; only content swaps here.
    var title = panel.querySelector('.preview-title');
    if (title) title.textContent = 'Setting guide';
    var media = panel.querySelector('.preview-media');
    if (media) { media.innerHTML = ''; media.classList.remove('reserve'); }
    var cap = panel.querySelector('.preview-caption');
    if (cap) {
        cap.className = 'preview-caption';
        cap.textContent = 'Hover or select a setting to see a description here.';
    }
}

function tp3dGuideRender(id) {
    var entry = TP3D_GUIDE_REGISTRY[id];
    var panel = tp3dGuidePanel();
    if (!entry || !panel) return;
    // Visibility is driven entirely by .guide-hidden; only content swaps here.

    var title = panel.querySelector('.preview-title');
    if (title) title.textContent = entry.title || 'Setting guide';

    var media = panel.querySelector('.preview-media');
    var token = ++TP3D_GUIDE_STATE.imageToken;
    // .reserve keeps the media box's 16:9 space while an image loads, so the
    // caption doesn't jump down when it arrives.
    media.classList.toggle('reserve', !!entry.url);
    if (entry.url) {
        var current = media.querySelector('img');
        if (current && current.getAttribute('data-guide-url') === entry.url) {
            current.alt = 'Example: ' + (entry.title || 'setting');
        } else {
            // Keep the old image visible until the new one has loaded, and
            // ignore a load that resolves after the user has moved on. The
            // <img> is only created/updated on load, so an empty <img> (which
            // browsers render as its alt text) never flashes.
            var pending = new Image();
            pending.onload = function() {
                if (token !== TP3D_GUIDE_STATE.imageToken) return;
                var img = media.querySelector('img');
                if (!img) { img = document.createElement('img'); media.appendChild(img); }
                img.alt = 'Example: ' + (entry.title || 'setting');
                img.src = entry.url;
                img.setAttribute('data-guide-url', entry.url);
            };
            pending.onerror = function() {
                if (token !== TP3D_GUIDE_STATE.imageToken) return;
                media.innerHTML = ''; // caption-only fallback
                media.classList.remove('reserve');
            };
            pending.src = entry.url;
        }
    } else {
        media.innerHTML = '';
    }

    var cap = panel.querySelector('.preview-caption');
    if (cap) {
        cap.className = 'preview-caption' + (entry.url ? '' : ' preview-text-only');
        cap.innerHTML = '';
        if (entry.caption) {
            var lines = Array.isArray(entry.caption) ? entry.caption : [entry.caption];
            lines.forEach(function(line) {
                var d = document.createElement('div');
                d.textContent = line;
                cap.appendChild(d);
            });
        }
    }
}

function tp3dGuideDisplay() {
    var id = TP3D_GUIDE_STATE.hoveredId || TP3D_GUIDE_STATE.pinnedId;
    if (id && TP3D_GUIDE_REGISTRY[id]) tp3dGuideRender(id);
    else tp3dGuideRenderDefault();
    tp3dGuideMarkPinned();
}

// Outline the row/card whose setting is pinned, so it's clear which one the
// panel is describing (several cards share one guide, e.g. Area Threshold).
// Only the outermost element carrying the id is marked, never the "?" button.
function tp3dGuideMarkPinned() {
    var host = document.getElementById('settingsModal');
    if (!host) return;
    host.querySelectorAll('.guide-pinned').forEach(function(el) { el.classList.remove('guide-pinned'); });
    var id = TP3D_GUIDE_STATE.pinnedId;
    if (!id) return;
    var sel = '[data-guide-id="' + id + '"]';
    host.querySelectorAll(sel).forEach(function(el) {
        if (el.classList.contains('preview-btn')) return;
        var outer = el.parentElement && el.parentElement.closest(sel);
        if (outer && !outer.classList.contains('preview-btn')) return;
        el.classList.add('guide-pinned');
    });
}

// Screen readers: announce only deliberate selections (focus/click), never
// hover, through a visually hidden live region.
function tp3dGuideAnnounce(id) {
    var live = document.getElementById('settingsGuideAnnounce');
    if (!live) return;
    var entry = id && TP3D_GUIDE_REGISTRY[id];
    if (!entry) { live.textContent = ''; return; }
    var lines = Array.isArray(entry.caption) ? entry.caption : (entry.caption ? [entry.caption] : []);
    live.textContent = (entry.title ? entry.title + '. ' : '') + lines.join(' ');
}

function tp3dGuidePin(id) {
    TP3D_GUIDE_STATE.pinnedId = id;
    tp3dGuideDisplay();
    tp3dGuideAnnounce(id);
}
function tp3dGuideUnpin() {
    // Only clears pinned/hovered content. Turning the guide off is the
    // header toggle's job, so this just resets the panel to the default
    // placeholder while the guide stays on.
    TP3D_GUIDE_STATE.pinnedId = null;
    TP3D_GUIDE_STATE.hoveredId = null;
    tp3dGuideDisplay();
    tp3dGuideAnnounce(null);
}

// Compatibility shims for any external caller that still uses the old
// click-only API (tp3dShowPreview(url, caption) / tp3dHidePreview()).
function tp3dShowPreview(url, caption) {
    tp3dGuideApplyVisible(true);
    tp3dGuidePin(tp3dRegisterGuide('', url, caption));
}
function tp3dHidePreview() { tp3dGuideUnpin(); }

function tp3dMakePreviewButtonForGuide(guideId) {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'preview-btn';
    btn.title = 'Show in the setting guide';
    btn.textContent = '?';
    btn.setAttribute('data-guide-id', guideId);
    btn.addEventListener('click', function(e) {
        e.preventDefault();
        e.stopPropagation();
        // "?" is one-way: turn the guide on (without changing the saved
        // preference) and show this setting. Only the header Show/Hide
        // button turns the guide off.
        tp3dGuideApplyVisible(true);
        tp3dGuidePin(guideId);
    });
    return btn;
}

// *tagEl*, when given, gets the same data-guide-id as the "?" button, so
// hovering anywhere in it (e.g. a pill/option row that wraps both a big
// button and its "?") triggers the guide, not just the tiny "?" itself.
function tp3dMakePreviewButton(url, caption, title, tagEl) {
    var btn = tp3dMakePreviewButtonForGuide(tp3dRegisterGuide(title || '', url, caption));
    if (tagEl) tagEl.setAttribute('data-guide-id', btn.getAttribute('data-guide-id'));
    return btn;
}

// Wraps *el* together with a preview button and a data-guide-id. `title` is
// the field label shown as the guide heading; `caption` is the
// previewCaption or, as a fallback, the field's own `title` string (so
// settings without a preview image still get a text-only guide). *rowEl*,
// when passed, also gets the same data-guide-id so hovering anywhere in
// that enclosing row/card shows the guide (not just the label).
function tp3dWithPreview(el, url, caption, title, rowEl) {
    if (!url && !caption) return el;
    var guideId = tp3dRegisterGuide(title || '', url, caption);
    var wrap = document.createElement('span');
    wrap.className = 'preview-wrap';
    wrap.setAttribute('data-guide-id', guideId);
    wrap.appendChild(el);
    wrap.appendChild(tp3dMakePreviewButtonForGuide(guideId));
    if (rowEl && rowEl !== wrap) rowEl.setAttribute('data-guide-id', guideId);
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

// *contextLabel* (optional) is appended to the guide title, so a field that
// repeats on several cards (e.g. Area Threshold) says which card it's for.
function tp3dBuildNumberField(field, contextLabel) {
    var wrap = document.createElement('div');
    wrap.className = 'card-field';
    var guideCaption = field.previewCaption || field.title || null;
    // If a guide (image or text-only) covers this field, suppress the native
    // tooltip so it doesn't pop up over the same information.
    wrap.title = (field.preview || guideCaption) ? '' : (field.title || '');
    var label = document.createElement('label');
    label.textContent = field.label;
    var input = document.createElement('input');
    input.type = 'number';
    input.id = 'advanced-' + field.key;
    label.htmlFor = input.id;
    if (field.step != null) input.step = field.step;
    if (field.min != null) input.min = field.min;
    if (field.max != null) input.max = field.max;
    input.value = tp3dRoundForDisplay(ADVANCED_SETTINGS_STATE[field.key], field.decimals);
    input.addEventListener('change', function() {
        tp3dSendAdvancedUpdate(field.key, parseFloat(input.value));
    });
    wrap.appendChild(tp3dWithPreview(label, field.preview, guideCaption,
        contextLabel ? field.label + ' \u00b7 ' + contextLabel : field.label, wrap));
    wrap.appendChild(input);
    if (field.singleExtruderOnly) {
        wrap.setAttribute('data-single-extruder-only', '');
        tp3dApplySingleExtruderLock(wrap);
    }
    return wrap;
}

// Fields flagged singleExtruderOnly (e.g. the Roads cut tolerance, which
// panels.py hides in Paint mode) are greyed out rather than hidden while
// elementMode isn't SINGLECOLORMODE_REMESH.
function tp3dApplySingleExtruderLock(wrap) {
    var locked = SETTINGS_STATE.elementMode !== 'SINGLECOLORMODE_REMESH';
    wrap.classList.toggle('locked', locked);
    wrap.querySelector('input').disabled = locked;
}
function tp3dRefreshSingleExtruderFields() {
    document.querySelectorAll('[data-single-extruder-only]').forEach(tp3dApplySingleExtruderLock);
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
    var guideCaption = field.previewCaption || field.title || null;
    label.title = (field.preview || guideCaption) ? '' : (field.title || '');
    var input = document.createElement('input');
    input.type = 'checkbox';
    input.setAttribute('data-advanced-checkbox', field.key);
    var remembered = compositeKey ? ((ADVANCED_SETTINGS_STATE._compositeRemembered || {})[compositeKey] || {}) : {};
    var locked = compositeKey && !tp3dCompositeIsActive(compositeKey) && remembered.hasOwnProperty(field.key);
    input.checked = locked ? !!remembered[field.key] : !!ADVANCED_SETTINGS_STATE[field.key];
    input.disabled = locked;
    if (locked) label.classList.add('locked');
    input.addEventListener('change', function() {
        // Keep the page's copy current too -- the chip flyouts and the
        // tutorials read sub-flags from it.
        ADVANCED_SETTINGS_STATE[field.key] = input.checked;
        tp3dSendAdvancedUpdate(field.key, input.checked);
    });
    label.appendChild(input);
    label.appendChild(document.createTextNode(field.label));
    return tp3dWithPreview(label, field.preview, guideCaption, field.label);
}

function tp3dBuildSimpleElementCard(key) {
    var meta = ELEMENT_CARD_LABELS[key] || { label: key };
    var card = document.createElement('div');
    card.className = 'element-card';

    var icon = document.createElement('button');
    icon.type = 'button';
    icon.setAttribute('aria-label', meta.label);
    icon.className = 'card-icon';
    icon.setAttribute('data-element-toggle', key);
    icon.innerHTML = ELEMENT_ICONS[key] || '';
    icon.title = meta.label + ' (click to toggle)';
    icon.addEventListener('click', function() { tp3dToggleElement(key); });
    card.appendChild(icon);

    var label = document.createElement('div');
    label.className = 'card-label';
    label.textContent = meta.label;
    card.appendChild(tp3dWithPreview(label, meta.preview, meta.previewCaption, meta.label, card));

    // SIMPLE_ELEMENT_FIELDS is OSM-only -- WorldCover shares several of
    // these same keys (forest/city/greenspace/farmland/glacier) for its own
    // cards, which have no per-category threshold of their own (one shared
    // WORLDCOVER_MIN_AREA_FIELD covers all of them, see tp3dBuildElementsTab),
    // so the lookup must never apply here even though the key string matches.
    if (!tp3dIsWorldCover()) {
        (SIMPLE_ELEMENT_FIELDS[key] || []).forEach(function(field) {
            card.appendChild(tp3dBuildNumberField(field, meta.label));
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
    var icon = document.createElement('button');
    icon.type = 'button';
    icon.setAttribute('aria-label', meta.label);
    icon.className = 'card-icon';
    icon.setAttribute('data-element-toggle', key);
    icon.innerHTML = ELEMENT_ICONS[key] || '';
    icon.title = meta.label + ' (click to toggle all)';
    icon.addEventListener('click', function() { tp3dToggleElement(key); });
    var label = document.createElement('div');
    label.className = 'card-label';
    label.textContent = meta.label;
    head.appendChild(icon);
    head.appendChild(tp3dWithPreview(label, meta.preview, meta.previewCaption, meta.label, head));
    card.appendChild(head);

    var checklist = document.createElement('div');
    checklist.className = 'card-checklist';
    def.checkboxes.forEach(function(field) { checklist.appendChild(tp3dBuildCheckboxField(field, key)); });
    card.appendChild(checklist);

    def.numberFields.forEach(function(field) { card.appendChild(tp3dBuildNumberField(field, meta.label)); });
    if (def.extraFields) card.appendChild(def.extraFields());

    tp3dRepaintElementToggle(key);
    return card;
}

// Water card's Flatten Water Surface checkbox + Insert (mm) field --
// Insert only applies while col_wFlattenTop is on, so it's greyed out
// (disabled) rather than hidden while flattening is off.
function tp3dBuildWaterFlattenFields() {
    var wrap = document.createElement('div');
    var flatten = tp3dBuildCheckboxField({
        key: 'colWFlattenTop',
        label: 'Flatten Water Surface',
        title: "Flatten each water body's surface to its own median height instead of following every terrain bump",
        preview: 'https://trailprint3d.com/images/howto/FlatttenWater.webp',
        previewCaption: 'Flatten out the sometimes uneven water surface that comes from the data source.'
    });
    var insert = tp3dBuildNumberField({
        key: 'colWInsert',
        label: 'Insert (mm)',
        step: 0.1,
        min: 0,
        title: 'Sink the water this many mm lower in Z',
        preview: 'https://trailprint3d.com/images/howto/WaterInsert.webp',
        previewCaption: 'Insert the water deeper into the part for a unique look.'
    });
    var insertInput = insert.querySelector('input');
    // Mirrors panels.py's INFO label: generation caps Insert to Extra Map
    // Height - TP3D_WATER_INSERT_MARGIN (terrain.effective_water_insert).
    var note = document.createElement('div');
    note.className = 'card-field warning-text';
    function refresh() {
        var flat = !!ADVANCED_SETTINGS_STATE.colWFlattenTop;
        insertInput.disabled = !flat;
        insert.classList.toggle('locked', !flat);
        var minT = parseFloat(MAP_TAB_CONTROL_VALUES.minThickness != null
            ? MAP_TAB_CONTROL_VALUES.minThickness : SETTINGS_STATE.minThickness);
        var cap = Math.max(0, minT - TP3D_WATER_INSERT_MARGIN);
        var over = flat && !isNaN(cap) && parseFloat(insertInput.value) > cap;
        note.textContent = over
            ? 'ⓘ Deeper than Extra Map Height allows — capped to ' + tp3dRoundForDisplay(cap, 2) + ' mm'
            : '';
        note.style.display = over ? '' : 'none';
    }
    var checkbox = flatten.querySelector('input[type="checkbox"]');
    checkbox.addEventListener('change', function() {
        ADVANCED_SETTINGS_STATE.colWFlattenTop = checkbox.checked;
        refresh();
    });
    tp3dWaterInsertNoteRefresh = refresh;
    var checklist = document.createElement('div');
    checklist.className = 'card-checklist';
    checklist.appendChild(flatten);
    wrap.appendChild(checklist);
    wrap.appendChild(insert);
    wrap.appendChild(note);
    refresh();
    return wrap;
}
var TP3D_WATER_INSERT_MARGIN = 0.5; // constants.WATER_INSERT_MARGIN

// Roads card's "Paint Roads" (props.tex_include_roads) -- the Map tab's
// "Paint Trail" counterpart, greyed out via MAP_TAB_ROWS whenever there's
// no texture to paint into. Unlike Paint Trail it stays available with a
// Single Extruder trail -- painted roads don't conflict with the trail cutout.
function tp3dBuildRoadsPaintField() {
    var field = {
        key: 'texIncludeRoads',
        label: 'Paint Roads',
        title: 'Paint the roads into the texture (terrain under roads coloured black)',
        preview: 'https://trailprint3d.com/images/howto/PaintRoads.webp',
        previewCaption: 'Paint the roads onto the map instead of creating a separate object.',
        enabledWhen: tp3dTextureAvailable,
        lockedTitle: tp3dNo3mfTitle
    };
    // With a preview this is a .preview-wrap around the <label> -- lock the
    // label itself so the checklist's `label.locked` styling still applies.
    var checkbox = tp3dBuildCheckboxField(field);
    var label = checkbox.tagName === 'LABEL' ? checkbox : checkbox.querySelector('label');
    MAP_TAB_ROWS.push({ field: field, row: label });
    var checklist = document.createElement('div');
    checklist.className = 'card-checklist';
    checklist.appendChild(checkbox);
    return checklist;
}
// The Elements tab gets rebuilt in place (tp3dRebuildElementsTab), so one
// document listener calls whichever Water card is current instead of each
// build adding its own. Any field change counts -- Insert here, or Extra Map
// Height on the Map tab.
var tp3dWaterInsertNoteRefresh = null;
document.addEventListener('change', function() {
    if (tp3dWaterInsertNoteRefresh) tp3dWaterInsertNoteRefresh();
});

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
    range.setAttribute('aria-label', field.label);

    var number = document.createElement('input');
    number.type = 'number';
    number.min = field.min;
    number.step = field.step || 1;
    number.value = initial;
    number.setAttribute('aria-label', field.label + ' value');

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
// one that was just edited. Rows of a tab that gets rebuilt in place (e.g.
// tp3dRebuildElementsTab, from the OSM/ESA WorldCover switch) are dropped by
// tp3dClearTabPanel, so this doesn't grow forever across repeated switches.
// Not pruned by isConnected: every tab is built detached, before it's
// attached to the document, so that would drop each row before its first
// visibility check.
var MAP_TAB_ROWS = [];

function tp3dClearTabPanel(panel) {
    MAP_TAB_ROWS = MAP_TAB_ROWS.filter(function(entry) { return !panel.contains(entry.row); });
    panel.innerHTML = '';
}

// A field row whose `showWhen` doesn't match, or whose `enabledWhen()` (for
// conditions spanning several settings) returns false, is greyed out
// (locked, its controls disabled) rather than hidden, so rows don't jump
// around; an info note (an entry with `text` instead of a field key) is
// hidden instead, and so is a field with `hideWhenInactive` (rows that only
// exist for one choice of another field, e.g. an API's own dataset select).
function tp3dRefreshMapTabVisibility() {
    MAP_TAB_ROWS.forEach(function(entry) {
        var when = entry.field.showWhen;
        var current = when ? MAP_TAB_CONTROL_VALUES[when.key] : undefined;
        var active = !when || (when.in ? when.in.indexOf(current) !== -1 : current === when.equals);
        if (active && entry.field.enabledWhen) active = entry.field.enabledWhen();
        if (entry.field.apply) {
            entry.field.apply(); // a custom control repaints itself
            return;
        }
        if (entry.field.text || entry.field.hideWhenInactive) {
            entry.row.style.display = active ? '' : 'none';
            return;
        }
        entry.row.classList.toggle('locked', !active);
        if (entry.field.lockedTitle) {
            entry.row.classList.toggle(
                'no-3mf',
                !active && entry.field.lockedTitle() === TP3D_NO_3MF_TITLE
            );
        }
        // `lockedTitle()` -- why the row is locked, as its tooltip (empty
        // when that reason doesn't apply, keeping the row's own title).
        if (entry.field.lockedTitle) {
            if (entry.baseTitle === undefined) entry.baseTitle = entry.row.title;
            entry.row.title = (!active && entry.field.lockedTitle()) || entry.baseTitle;
        }
        // The "?" preview stays clickable -- explaining a locked setting is
        // exactly when it's useful.
        entry.row.querySelectorAll('input, select, button:not(.preview-btn)').forEach(function(control) {
            control.disabled = !active;
        });
        if (entry.field.manualDefault != null && active) {
            entry.row.querySelector('input[type=number]').disabled = !entry.row.querySelector('input[type=checkbox]').checked;
        }
    });
}

// Builds one .adv-field-row for a SETTINGS_MODAL_MAP_FIELDS-shaped field
// (number/checkbox/range/select -- a select takes `options: [[value, label]]`
// and sends its value as a string) -- shared by tp3dBuildMapTab's own field
// list, tp3dAppendApiSection's and
// the WorldCover Elements tab's single Min Feature Area row below, so both
// use the same look the Map tab already established. A field with
// `showWhen: { key, equals }` (see tp3dRefreshMapTabVisibility) mirrors one
// of panels.py's own conditional rows (`showWhen: { key, in: [...] }` for
// "any of these values") -- registered in MAP_TAB_ROWS
// regardless of tab, since the WorldCover Min Feature Area field has no
// `showWhen` and just always shows. A number field with `manualDefault`
// gets a checkbox: unchecked sends zero, checked restores the last positive
// value (or manualDefault). A checkbox field with `companion` (its
// own number-field definition, no `label`/`type` of its own since it always
// shares the checkbox's row) instead gets that number input placed right
// next to its own checkbox, shown only while the checkbox is on -- mirrors
// panels.py's own smoothRow, which puts smoothTerrainTop and
// smoothTerrainStrength on the same row rather than as two separate ones.
function tp3dBuildFieldRow(field) {
    var row = document.createElement('div');
    row.className = 'adv-field-row';
    var guideCaption = field.previewCaption || field.title || null;
    // If a guide (image or text-only) covers this field, suppress the native
    // tooltip so it doesn't pop up over the same information.
    row.title = (field.preview || guideCaption) ? '' : (field.title || '');
    MAP_TAB_ROWS.push({ field: field, row: row });

    var label = document.createElement('label');
    label.textContent = field.label;
    var stateObj = field.source === 'SETTINGS_STATE' ? SETTINGS_STATE : ADVANCED_SETTINGS_STATE;

    if (field.type === 'range') {
        row.appendChild(tp3dWithPreview(label, field.preview, guideCaption, field.label, row));
        row.appendChild(tp3dBuildRangeField(field, stateObj));
        return row;
    }

    var input;
    if (field.type === 'select') {
        input = document.createElement('select');
        field.options.forEach(function(opt) {
            var option = document.createElement('option');
            option.value = opt[0];
            option.textContent = opt[1];
            input.appendChild(option);
        });
    } else {
        input = document.createElement('input');
        input.type = field.type;
        // Drawn as an on/off switch (.tp3d-switch in picker_common.css).
        if (field.type === 'checkbox') input.className = 'tp3d-switch';
    }
    input.id = 'setting-' + field.key;
    label.htmlFor = input.id;
    // Lets a picker page find this field's input to push a value into it
    // (e.g. multitile's Dovetail toggle raising Extra Map Height).
    input.dataset.settingKey = field.key;
    if (field.type === 'select') {
        input.value = stateObj[field.key];
    } else if (field.type === 'number') {
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
    var manualToggle = null;
    var manualValue;
    if (field.manualDefault != null) {
        manualValue = Number(input.value) > 0 ? Number(input.value) : field.manualDefault;
        manualToggle = document.createElement('input');
        manualToggle.type = 'checkbox';
        manualToggle.checked = Number(input.value) > 0;
        manualToggle.setAttribute('aria-label', 'Use manual ' + field.label);
        manualToggle.title = row.title;
        input.disabled = !manualToggle.checked;
        manualToggle.addEventListener('change', function() {
            input.value = manualToggle.checked ? manualValue : 0;
            input.dispatchEvent(new Event('change'));
        });
        controls.appendChild(manualToggle);
    }
    // `unit` ('mm', '°', ...) is shown inside the field, after the value.
    if (field.unit) {
        var unitWrap = document.createElement('span');
        unitWrap.className = 'input-with-unit';
        var unit = document.createElement('span');
        unit.className = 'input-unit';
        unit.setAttribute('aria-hidden', 'true');
        unit.textContent = field.unit;
        unitWrap.appendChild(input);
        unitWrap.appendChild(unit);
        controls.appendChild(unitWrap);
    } else {
        controls.appendChild(input);
    }

    var showCompanion = null;
    if (field.companion && field.companion.type === 'range') {
        // A slider + value pair (tp3dBuildRangeField), shown only while the
        // checkbox is on. Hidden via style.display -- .range-with-number's
        // own display:flex would override the `hidden` attribute.
        var rangeComp = field.companion;
        var rangeState = rangeComp.source === 'SETTINGS_STATE' ? SETTINGS_STATE : ADVANCED_SETTINGS_STATE;
        var rangeWrap = tp3dBuildRangeField(rangeComp, rangeState);
        rangeWrap.title = rangeComp.title || '';
        rangeWrap.style.display = input.checked ? '' : 'none';
        controls.appendChild(rangeWrap);
        showCompanion = function(on) { rangeWrap.style.display = on ? '' : 'none'; };
    } else if (field.companion) {
        var comp = field.companion;
        var compState = comp.source === 'SETTINGS_STATE' ? SETTINGS_STATE : ADVANCED_SETTINGS_STATE;
        var companionInput = document.createElement('input');
        showCompanion = function(on) { companionInput.hidden = !on; };
        companionInput.type = 'number';
        companionInput.setAttribute('aria-label', comp.title || field.label + ' strength');
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
        var value = field.type === 'checkbox' ? input.checked
            : field.type === 'select' ? input.value
            : parseFloat(input.value);
        if (manualToggle) {
            input.setCustomValidity('');
            if (!Number.isFinite(value) || value < 0) {
                input.setCustomValidity('Enter a non-negative number.');
                input.reportValidity();
                return;
            }
        }
        if (field.decimals != null && typeof value === 'number') {
            value = parseFloat(value.toFixed(field.decimals));
            input.value = value;
        }
        if (manualToggle) {
            manualToggle.checked = value > 0;
            if (value > 0) manualValue = value;
            stateObj[field.key] = value;
        }
        MAP_TAB_CONTROL_VALUES[field.key] = value;
        tp3dRefreshMapTabVisibility();
        if (showCompanion) showCompanion(value);
        tp3dSendMapField(field, value);
        // Optional per-page hook (e.g. premium/map_generator_pe.html's rotated
        // shape-preview overlay) -- most pages don't define this, so it's a
        // no-op for them.
        if (window.tp3dOnMapFieldChanged) window.tp3dOnMapFieldChanged(field.key, value);
    });

    row.appendChild(tp3dWithPreview(label, field.preview, guideCaption, field.label, row));
    row.appendChild(controls);
    return row;
}

function tp3dBuildMapTab() {
    return tp3dBuildFieldList(SETTINGS_MODAL_MAP_FIELDS);
}

// ── Color styles ─────────────────────────────────────────────────────
// Mirrors ui/color_styles.py's COLOR_ROLES: every role the backend knows
// about, in a display order that groups terrain, nature, and built
// features together. TP3D_COLOR_STYLES (injected as __COLOR_STYLES_JS__)
// carries the built-in + saved palettes, the current selection, and
// whether the live colors have drifted from that selection.
var COLOR_ROLE_ORDER = ['BASE', 'MOUNTAIN', 'GLACIER', 'WATER', 'FOREST',
                        'GREENSPACE', 'FARMLAND', 'CITY', 'BUILDINGS',
                        'ROADS', 'TRAIL', 'YELLOW'];
var COLOR_ROLE_LABELS = {
    BASE: 'Base Terrain', MOUNTAIN: 'Mountain / Scree', GLACIER: 'Glacier',
    WATER: 'Water', FOREST: 'Forest', GREENSPACE: 'Greenspace',
    FARMLAND: 'Farmland', CITY: 'City', BUILDINGS: 'Buildings',
    ROADS: 'Roads', TRAIL: 'Trail', YELLOW: 'Secondary Trail'
};

var TP3D_COLOR_STYLES = window.TP3D_COLOR_STYLES || {
    builtIn: [], user: [], currentStyle: '', currentColors: {}, isModified: false
};

function tp3dFindColorStyle(styleId) {
    if (!styleId) return null;
    if (styleId.indexOf('USER:') === 0) {
        var name = styleId.substring(5);
        return TP3D_COLOR_STYLES.user.find(function(s) { return s.name === name; }) || null;
    }
    return TP3D_COLOR_STYLES.builtIn.find(function(s) { return s.id === styleId; }) || null;
}

function tp3dColorStylesAreModified() {
    var style = tp3dFindColorStyle(TP3D_COLOR_STYLES.currentStyle);
    if (!style) return true;
    for (var i = 0; i < COLOR_ROLE_ORDER.length; i++) {
        var role = COLOR_ROLE_ORDER[i];
        var current = (TP3D_COLOR_STYLES.currentColors[role] || '').toUpperCase();
        var expected = (style.colors[role] || '').toUpperCase();
        if (current !== expected) return true;
    }
    return false;
}

// *container* is the wrapper the controls live in (used instead of
// document.getElementById so this also works before the card is attached).
function tp3dUpdateColorStyleDirty(container) {
    var root = container || document;
    var modified = tp3dColorStylesAreModified();

    var dirty = root.querySelector('#colorStyleDirty');
    if (dirty) dirty.style.display = modified ? '' : 'none';

    var restore = root.querySelector('#colorStyleRestore');
    if (restore) restore.style.display = modified ? '' : 'none';

    var saveBtn = root.querySelector('.color-save-btn');
    if (saveBtn) saveBtn.style.display = modified ? '' : 'none';

    var updateBtn = root.querySelector('.color-update-btn');
    if (updateBtn) updateBtn.style.display = modified ? '' : 'none';

    var actions = root.querySelector('.color-style-actions');
    if (actions) {
        var hasVisible = Array.from(actions.children).some(function(c) { return c.style.display !== 'none'; });
        actions.style.display = hasVisible ? 'flex' : 'none';
    }
}

function tp3dBuildColorRoleSwatch(role) {
    var swatch = document.createElement('label');
    swatch.className = 'color-swatch';

    var input = document.createElement('input');
    input.type = 'color';
    input.className = 'color-swatch-input';
    input.id = 'color-role-' + role.toLowerCase();
    input.value = TP3D_COLOR_STYLES.currentColors[role] || '#000000';
    input.title = COLOR_ROLE_LABELS[role];
    
    input.addEventListener('change', function() {
        var hex = this.value.toUpperCase();
        TP3D_COLOR_STYLES.currentColors[role] = hex;
        tp3dSendColorUpdate('element_color_' + role.toLowerCase(), hex);
        tp3dUpdateColorStyleDirty();
    });
    swatch.appendChild(input);

    var label = document.createElement('span');
    label.className = 'color-swatch-label';
    label.textContent = COLOR_ROLE_LABELS[role];
    label.title = COLOR_ROLE_LABELS[role];
    swatch.appendChild(label);

    return swatch;
}

// (Re)fills *select* with the built-in styles, then the user's saved ones
// below a separator -- shared by the Settings modal's selector and the
// compact one in the element-status row.
function tp3dFillColorStyleOptions(select) {
    select.innerHTML = '';
    TP3D_COLOR_STYLES.builtIn.forEach(function(s) {
        var opt = document.createElement('option');
        opt.value = s.id;
        opt.textContent = s.label;
        opt.title = s.description || '';
        select.appendChild(opt);
    });
    if (TP3D_COLOR_STYLES.user.length) {
        var sep = document.createElement('option');
        sep.disabled = true;
        sep.textContent = '──────────';
        select.appendChild(sep);
        TP3D_COLOR_STYLES.user.forEach(function(s) {
            var opt = document.createElement('option');
            opt.value = 'USER:' + s.name;
            opt.textContent = s.name;
            select.appendChild(opt);
        });
    }
    select.value = TP3D_COLOR_STYLES.currentStyle;
}

// Painter's palette (paint wells cut out via evenodd) with a brush beside it.
var TP3D_PALETTE_ICON_SVG =
    '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="currentColor" fill-rule="evenodd">' +
    '<path d="M9.5 3C5.2 3 2 6.2 2 10.2c0 4 3 7.3 6.8 7.3 1.4 0 2-1 1.4-2.1-.5-1 .1-2 1.4-2h1.2' +
    'c1.6 0 2.7-1.2 2.7-2.9V8.3C15.5 5.2 13 3 9.5 3Z' +
    'M7.2 7.6a1.3 1.3 0 1 1-2.6 0a1.3 1.3 0 1 1 2.6 0Z' +
    'M11 5.9a1.2 1.2 0 1 1-2.4 0a1.2 1.2 0 1 1 2.4 0Z' +
    'M6.4 11.6a1.4 1.4 0 1 1-2.8 0a1.4 1.4 0 1 1 2.8 0Z' +
    'M13.3 9.7a1 1 0 1 1-2 0a1 1 0 1 1 2 0Z"/>' +
    '<path d="M16.5 4.5h4.5l-.7 4.8h-3.1Z' +
    'M17.9 10.1h1.7v9.6a.85.85 0 0 1-1.7 0Z"/>' +
    '</svg>';

// Quick style switch for the element-status row, between the Settings gear
// and the OSM/ESA pill -- same apply path as the modal's selector. The
// palette icon sits inside the same bordered control, before the select.
function tp3dBuildCompactColorStyleSelect() {
    var wrap = document.createElement('label');
    wrap.className = 'color-style-compact-wrap';
    wrap.title = 'Color style';
    wrap.innerHTML = TP3D_PALETTE_ICON_SVG;
    var select = document.createElement('select');
    select.className = 'color-style-compact';
    select.setAttribute('aria-label', 'Color style');
    tp3dFillColorStyleOptions(select);
    select.addEventListener('change', function() { tp3dApplyColorStyle(this.value); });
    wrap.appendChild(select);
    return wrap;
}

// Keeps every style selector (modal + compact) showing the current style.
function tp3dSyncColorStyleSelects() {
    document.querySelectorAll('#colorStyleSelect, .color-style-compact').forEach(function(s) {
        s.value = TP3D_COLOR_STYLES.currentStyle;
    });
    tp3dRefreshColorStyleActions();
}

// Update/Delete only apply to the user's own saved styles. *container*
// defaults to the document (a section still being built passes itself).
function tp3dRefreshColorStyleActions(container) {
    var root = container || document;
    var isUser = TP3D_COLOR_STYLES.currentStyle.indexOf('USER:') === 0;
    root.querySelectorAll('.color-update-btn, .color-delete-btn').forEach(function(btn) {
        btn.style.display = isUser ? '' : 'none';
    });
}

function tp3dBuildColorStylesSection() {
    var wrap = document.createElement('div');
    wrap.className = 'color-styles-section';

    if (!TP3D_COLOR_STYLES.builtIn.length) {
        var msg = document.createElement('div');
        msg.className = 'field-hint';
        msg.textContent = 'Color styles are not available on this page.';
        wrap.appendChild(msg);
        return wrap;
    }

    // ── Style selector row ───────────────────────────────────────────
    var styleRow = document.createElement('div');
    styleRow.className = 'adv-field-row color-style-row';
    var label = document.createElement('label');
    label.textContent = 'Color Style';
    label.htmlFor = 'colorStyleSelect';
    styleRow.appendChild(label);

    // Register the guide and attach it to the label + the whole section
    var guideCaption = [
        'Color Styles',
        'Choose a preset palette or click any color to customize it.',
        'These colors are applied to the terrain, water, forests, roads, and other elements on your generated map.',
        'If you modify a preset, use "Save as new" to create your own reusable style.'
    ];
    // We pass the main `wrap` as the rowEl so hovering anywhere in the color grid 
    // (or the save buttons) will trigger the guide, not just the label.
    styleRow.appendChild(tp3dWithPreview(label, null, guideCaption, 'Color Styles', wrap));

    var controls = document.createElement('div');
    controls.className = 'adv-field-controls color-style-controls';

    var select = document.createElement('select');
    select.id = 'colorStyleSelect';
    tp3dFillColorStyleOptions(select);
    select.addEventListener('change', function() { tp3dApplyColorStyle(this.value); });
    controls.appendChild(select);

    var dirty = document.createElement('span');
    dirty.id = 'colorStyleDirty';
    dirty.className = 'color-style-dirty';
    dirty.textContent = '• changed';
    dirty.title = 'The current colors differ from the selected style';
    dirty.style.display = 'none';
    controls.appendChild(dirty);

    var restore = document.createElement('button');
    restore.type = 'button';
    restore.id = 'colorStyleRestore';
    restore.className = 'restore-btn';
    restore.textContent = '↺';
    restore.title = 'Restore the selected style\'s colors';
    restore.style.display = 'none';
    restore.addEventListener('click', function() {
        tp3dApplyColorStyle(TP3D_COLOR_STYLES.currentStyle);
    });
    controls.appendChild(restore);

    styleRow.appendChild(controls);
    wrap.appendChild(styleRow);

    // ── Color grid ───────────────────────────────────────────────────
    var grid = document.createElement('div');
    grid.className = 'color-role-grid';
    COLOR_ROLE_ORDER.forEach(function(role) {
        grid.appendChild(tp3dBuildColorRoleSwatch(role));
    });
    wrap.appendChild(grid);

    // ── Save / Update / Delete ───────────────────────────────────────
    var actions = document.createElement('div');
    actions.className = 'color-style-actions';

    var saveBtn = document.createElement('button');
    saveBtn.type = 'button';
    saveBtn.className = 'element-mode-btn color-save-btn';
    saveBtn.textContent = 'Save as new';
    saveBtn.title = 'Save the current colors as a new user style';
    saveBtn.addEventListener('click', function() { tp3dShowColorStyleNameRow('new'); });
    actions.appendChild(saveBtn);

    // Always built; tp3dRefreshColorStyleActions shows them only while a
    // user style is selected (built-in styles can't be changed or deleted).
    var updateBtn = document.createElement('button');
    updateBtn.type = 'button';
    updateBtn.className = 'element-mode-btn color-update-btn';
    updateBtn.textContent = 'Update';
    updateBtn.title = 'Replace the selected user style with the current colors';
    updateBtn.addEventListener('click', function() { tp3dShowColorStyleNameRow('update'); });
    actions.appendChild(updateBtn);

    var deleteBtn = document.createElement('button');
    deleteBtn.type = 'button';
    deleteBtn.className = 'element-mode-btn danger color-delete-btn';
    deleteBtn.textContent = 'Delete';
    deleteBtn.title = 'Delete this user style';
    deleteBtn.addEventListener('click', function() { tp3dDeleteColorStyle(); });
    actions.appendChild(deleteBtn);
    wrap.appendChild(actions);
    tp3dRefreshColorStyleActions(wrap);

    // ── Hidden name row ──────────────────────────────────────────────
    var nameRow = document.createElement('div');
    nameRow.id = 'colorStyleNameRow';
    nameRow.className = 'color-style-name-row';
    nameRow.style.display = 'none';
    
    var nameInput = document.createElement('input');
    nameInput.type = 'text';
    nameInput.id = 'colorStyleName';
    nameInput.maxLength = 64;
    nameInput.placeholder = 'Style name';
    nameRow.appendChild(nameInput);
    
    var nameOk = document.createElement('button');
    nameOk.type = 'button';
    nameOk.className = 'element-mode-btn active';
    nameOk.textContent = 'Save';
    nameOk.addEventListener('click', tp3dCommitColorStyleName);
    nameRow.appendChild(nameOk);
    
    var nameCancel = document.createElement('button');
    nameCancel.type = 'button';
    nameCancel.className = 'element-mode-btn';
    nameCancel.textContent = 'Cancel';
    nameCancel.addEventListener('click', tp3dHideColorStyleNameRow);
    nameRow.appendChild(nameCancel);
    
    wrap.appendChild(nameRow);

    tp3dUpdateColorStyleDirty(wrap);
    return wrap;
}

function tp3dSendColorUpdate(key, hex) {
    fetch('http://127.0.0.1:' + PORT + '/update_color_setting', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: key, value: hex })
    }).catch(function() {});
}

function tp3dApplyColorStyle(styleId) {
    var style = tp3dFindColorStyle(styleId);
    if (!style) return;
    TP3D_COLOR_STYLES.currentStyle = styleId;
    COLOR_ROLE_ORDER.forEach(function(role) {
        var input = document.getElementById('color-role-' + role.toLowerCase());
        if (input && style.colors[role]) {
            input.value = style.colors[role];
            TP3D_COLOR_STYLES.currentColors[role] = style.colors[role];
        }
    });
    fetch('http://127.0.0.1:' + PORT + '/apply_color_style', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ style: styleId })
    }).catch(function() {});
    tp3dSyncColorStyleSelects();
    tp3dUpdateColorStyleDirty();
}

var _colorStyleNameMode = null;

function tp3dShowColorStyleNameRow(mode) {
    _colorStyleNameMode = mode;
    var row = document.getElementById('colorStyleNameRow');
    var input = document.getElementById('colorStyleName');
    var okBtn = row.querySelector('.element-mode-btn.active');
    if (mode === 'update') {
        input.value = TP3D_COLOR_STYLES.currentStyle.substring(5);
        if (okBtn) okBtn.textContent = 'Update';
    } else {
        input.value = '';
        if (okBtn) okBtn.textContent = 'Save';
    }
    row.style.display = 'flex';
    input.focus();
    input.select();
}

function tp3dHideColorStyleNameRow() {
    _colorStyleNameMode = null;
    var row = document.getElementById('colorStyleNameRow');
    if (row) row.style.display = 'none';
}

function tp3dCommitColorStyleName() {
    var input = document.getElementById('colorStyleName');
    var name = (input.value || '').trim().replace(/\s+/g, ' ');
    if (!name) { input.focus(); return; }
    var ops = Promise.resolve();
    if (_colorStyleNameMode === 'update') {
        var oldName = TP3D_COLOR_STYLES.currentStyle.substring(5);
        ops = fetch('http://127.0.0.1:' + PORT + '/delete_color_style', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ style: 'USER:' + oldName })
        });
    }
    ops.then(function() {
        return fetch('http://127.0.0.1:' + PORT + '/save_color_style', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name })
        });
    }).then(function() {
        return fetch('http://127.0.0.1:' + PORT + '/get_color_styles', { cache: 'no-store' })
            .then(function(r) { return r.json(); });
    }).then(function(data) {
        tp3dRefreshColorStyleFromServer(data);
        tp3dHideColorStyleNameRow();
    }).catch(function() { tp3dHideColorStyleNameRow(); });
}

function tp3dDeleteColorStyle() {
    var styleId = TP3D_COLOR_STYLES.currentStyle;
    if (styleId.indexOf('USER:') !== 0) return;
    if (!confirm('Delete "' + styleId.substring(5) + '"?')) return;
    fetch('http://127.0.0.1:' + PORT + '/delete_color_style', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ style: styleId })
    }).then(function() {
        return fetch('http://127.0.0.1:' + PORT + '/get_color_styles', { cache: 'no-store' })
            .then(function(r) { return r.json(); });
    }).then(function(data) {
        tp3dRefreshColorStyleFromServer(data);
    }).catch(function() {});
}

function tp3dRefreshColorStyleFromServer(data) {
    TP3D_COLOR_STYLES = data;
    // Rebuild the whole block -- simplest way to reflect added/removed
    // user styles (and their per-style Update/Delete buttons) in place.
    var section = document.querySelector('.color-styles-section');
    if (section && section.parentNode) {
        var fresh = tp3dBuildColorStylesSection();
        section.parentNode.replaceChild(fresh, section);
    }
    // Saved/deleted user styles change the option list, not just the value.
    document.querySelectorAll('.color-style-compact').forEach(tp3dFillColorStyleOptions);
    tp3dSyncColorStyleSelects();
    tp3dUpdateColorStyleDirty();
}

// API section on top, then the consolidated Appearance card (color styles +
// texture), then the remaining Advanced fields in their own card.
function tp3dBuildOtherTab() {
    var wrap = document.createElement('div');
    wrap.className = 'settings-sections';

    var api = tp3dBuildSection('Elevation Data', 'settings-grid-2');
    tp3dAppendApiSection(api.grid);
    wrap.appendChild(api.card);

    var appearance = tp3dBuildSection('Appearance', 'settings-grid-2');
    // Color styles get their own full-width sub-block above the texture
    // fields -- the texture rows themselves stay in the card's own 2-col
    // grid (they're simple label+input rows, not a swatch grid).
    var colorBlock = document.createElement('div');
    colorBlock.className = 'settings-subsection appearance-colors';
    colorBlock.style.cssText = 'grid-column:1 / -1;padding:6px 10px 10px;';
    colorBlock.appendChild(tp3dBuildColorStylesSection());
    appearance.grid.appendChild(colorBlock);
    SETTINGS_MODAL_OTHER_FIELDS.forEach(function(field) {
        if (field.type === 'section') return; // heading now comes from the Appearance card
        appearance.grid.appendChild(tp3dBuildFieldRow(field));
    });
    wrap.appendChild(appearance.card);

    var advancedSection = tp3dBuildSection('Advanced Element Settings', 'settings-grid-2');
    advancedSection.grid.appendChild(tp3dBuildFieldRow(COASTLINE_SIMPLIFY_FIELD));
    wrap.appendChild(advancedSection.card);

    wrap.appendChild(tp3dBuildSlicerProfileSection());

    tp3dRefreshMapTabVisibility();
    return wrap;
}

// "3MF Print Profile" card -- the sidebar's 3MF Export Profile dropdown
// (props.slicer_profile_name) plus the 3MF add-on's own "Load Profile from
// 3MF" (Preferences → Add-ons → 3MF Format → Advanced). Locked, red, without
// the 3MF add-on (SETTINGS_STATE.has3mf, which flips live after
// threemf_promo.js's install). Profiles come from picker_server's
// /slicer_profiles snapshot, kept out of SETTINGS_STATE like the export
// folder so a history restore doesn't switch printers. Every request is
// answered once Blender has handled it, with the fresh list as `state`.
function tp3dBuildSlicerProfileSection() {
    var section = tp3dBuildSection('3MF Print Profile', 'settings-grid-2');
    var card = section.card;

    // Full card width, the import button on the dropdown's own line.
    var row = document.createElement('div');
    row.className = 'adv-field-row';
    row.style.gridColumn = '1 / -1';
    var label = document.createElement('label');
    label.textContent = 'Print Profile';
    label.htmlFor = 'slicerProfileSelect';
    row.appendChild(label);
    var controls = document.createElement('div');
    controls.className = 'adv-field-controls';
    var select = document.createElement('select');
    select.id = 'slicerProfileSelect';
    controls.appendChild(select);
    var importBtn = document.createElement('button');
    importBtn.type = 'button';
    importBtn.className = 'element-mode-btn';
    importBtn.style.cssText = 'height:36px;flex:0 0 auto;padding:0 16px;';
    importBtn.textContent = 'Import from 3MF…';
    importBtn.title = 'Add a print profile from a project .3mf saved in your slicer — its printer, filament and process settings get embedded in every 3MF export';
    var fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.accept = '.3mf';
    fileInput.hidden = true;
    controls.appendChild(importBtn);
    controls.appendChild(fileInput);
    row.appendChild(controls);
    section.grid.appendChild(row);
    MAP_TAB_ROWS.push({ field: { enabledWhen: tp3dHas3mf, lockedTitle: tp3dNo3mfTitle }, row: row });

    // Name row shown after a file was picked, like the color styles' one.
    var nameRow = document.createElement('div');
    nameRow.className = 'color-style-name-row';
    nameRow.style.cssText = 'display:none;grid-column:1 / -1;';
    var nameInput = document.createElement('input');
    nameInput.type = 'text';
    nameInput.maxLength = 64;
    nameInput.placeholder = 'Profile name';
    var saveBtn = document.createElement('button');
    saveBtn.type = 'button';
    saveBtn.className = 'element-mode-btn active';
    saveBtn.textContent = 'Save';
    var cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'element-mode-btn';
    cancelBtn.textContent = 'Cancel';
    nameRow.appendChild(nameInput);
    nameRow.appendChild(saveBtn);
    nameRow.appendChild(cancelBtn);
    section.grid.appendChild(nameRow);

    var hint = document.createElement('div');
    hint.className = 'field-hint';
    hint.style.gridColumn = '1 / -1';
    hint.textContent = 'Save a project .3mf in Bambu Studio, OrcaSlicer or PrusaSlicer with the printer and filament you use, then import it here.';
    section.grid.appendChild(hint);

    var status = document.createElement('div');
    status.className = 'field-hint';
    status.style.gridColumn = '1 / -1';
    status.hidden = true;
    section.grid.appendChild(status);

    function render(state) {
        select.innerHTML = '';
        (state.profiles || []).forEach(function(p) {
            var opt = document.createElement('option');
            opt.value = p.id;
            opt.textContent = p.label;
            opt.title = p.description || '';
            select.appendChild(opt);
        });
        select.value = state.current;
    }

    function showStatus(reply) {
        status.hidden = !reply.message;
        status.textContent = reply.message || '';
        status.classList.toggle('warning-text', !reply.ok);
    }

    function post(path, body, headers) {
        return fetch('http://127.0.0.1:' + PORT + path, {
            method: 'POST', headers: headers || { 'Content-Type': 'application/json' }, body: body
        }).then(function(r) { return r.json(); }).then(function(reply) {
            if (reply.state) render(reply.state);
            return reply;
        });
    }

    var picked = null;
    function hideNameRow() {
        picked = null;
        fileInput.value = '';
        nameRow.style.display = 'none';
    }

    select.addEventListener('change', function() {
        post('/select_slicer_profile', JSON.stringify({ name: select.value }))
            .then(showStatus).catch(function() {});
    });
    importBtn.addEventListener('click', function() { fileInput.click(); });
    fileInput.addEventListener('change', function() {
        picked = fileInput.files && fileInput.files[0];
        if (!picked) return;
        nameInput.value = picked.name.replace(/\.3mf$/i, '');
        nameRow.style.display = 'flex';
        nameInput.focus();
        nameInput.select();
    });
    cancelBtn.addEventListener('click', hideNameRow);
    saveBtn.addEventListener('click', function() {
        if (!picked) return;
        var file = picked;
        saveBtn.disabled = true;
        status.hidden = false;
        status.classList.remove('warning-text');
        status.textContent = 'Importing ' + file.name + '…';
        file.arrayBuffer().then(function(buf) {
            return post('/upload_slicer_profile', buf, {
                'Content-Type': 'application/octet-stream',
                'X-Profile-Name': encodeURIComponent(nameInput.value.trim()),
                'X-File-Name': encodeURIComponent(file.name)
            });
        }).then(function(reply) {
            showStatus(reply);
            if (reply.ok) hideNameRow();
        }).catch(function() {
            showStatus({ ok: false, message: 'Import failed, see the Blender console.' });
        }).then(function() { saveBtn.disabled = false; });
    });

    // Refreshed from Blender on first build (the locked dropdown still shows
    // the built-in default) and when the 3MF add-on (re)appears, so the list
    // also picks up profiles added elsewhere.
    var refreshedFor = null;
    MAP_TAB_ROWS.push({ field: { apply: function() {
        var has3mf = tp3dHas3mf();
        if (has3mf !== refreshedFor) {
            post('/select_slicer_profile', JSON.stringify({ name: '' })).catch(function() {});
        }
        refreshedFor = has3mf;
    } }, row: card });
    return card;
}

// One titled card of the Settings popup -- every tab is a stack of these.
// Returns the card and the field grid inside it that rows go into.
// *gridClass* adds a layout variant to that grid (e.g. settings-grid-2).
function tp3dBuildSection(title, gridClass, cardClass) {
    var card = document.createElement('section');
    card.className = cardClass || 'settings-section';
    if (title) {
        var heading = document.createElement('h3');
        heading.className = 'settings-section-title';
        heading.textContent = title;
        card.appendChild(heading);
    }
    var grid = document.createElement('div');
    grid.className = 'map-tab-fields' + (gridClass ? ' ' + gridClass : '');
    card.appendChild(grid);
    return { card: card, grid: grid };
}

// A tab of section cards, from a flat list of field rows with
// `{ type: 'section', title }` markers between them (and
// `{ type: 'subsection', title, fields }` for a nested card).
function tp3dBuildFieldList(fields) {
    var wrap = document.createElement('div');
    wrap.className = 'settings-sections';
    var grid = null;
    function startSection(title) {
        var section = tp3dBuildSection(title);
        wrap.appendChild(section.card);
        grid = section.grid;
    }
    fields.forEach(function(field) {
        if (field.type === 'section') {
            startSection(field.title);
            return;
        }
        if (!grid) startSection(null);
        if (field.type === 'custom') {
            grid.appendChild(field.build());
            return;
        }
        if (field.type === 'subsection') {
            var sub = tp3dBuildSection(field.title, 'settings-grid-2', 'settings-subsection');
            field.fields.forEach(function(subField) { sub.grid.appendChild(tp3dBuildFieldRow(subField)); });
            grid.appendChild(sub.card);
            return;
        }
        grid.appendChild(tp3dBuildFieldRow(field));
    });
    tp3dRefreshMapTabVisibility();
    return wrap;
}

function tp3dAppendApiSection(wrap) {
    // API with its dataset / DEM file row stacked below it on the left;
    // Disable Cache with Clear Cache stacked below it on the right.
    var apiGroup = document.createElement('div');
    apiGroup.className = 'api-cache-group';
    var cacheGroup = document.createElement('div');
    cacheGroup.className = 'api-cache-group';
    SETTINGS_MODAL_API_FIELDS.forEach(function(field) {
        (field.key === 'disableCache' ? cacheGroup : apiGroup).appendChild(tp3dBuildFieldRow(field));
    });
    apiGroup.appendChild(tp3dBuildDemPathRow());
    cacheGroup.appendChild(tp3dBuildClearCacheRow());
    wrap.appendChild(apiGroup);
    wrap.appendChild(cacheGroup);
    wrap.appendChild(tp3dBuildApiSummary(wrap.querySelector('#setting-api')));

    API_SETUP_NOTES.forEach(function(entry) {
        var note = document.createElement('div');
        note.className = 'adv-field-row warning-text api-setup-note';
        note.textContent = entry.text;
        MAP_TAB_ROWS.push({ field: entry, row: note });
        wrap.appendChild(note);
    });
}

// Short overview of every API (API_SUMMARIES), in the dropdown's order,
// with the currently selected one highlighted.
function tp3dBuildApiSummary(select) {
    var list = document.createElement('dl');
    list.className = 'api-summary';
    var items = {};
    SETTINGS_MODAL_API_FIELDS[0].options.forEach(function(opt) {
        var summary = API_SUMMARIES[opt[0]] || {};
        var item = document.createElement('div');
        var name = document.createElement('dt');
        if (summary.url) {
            var link = document.createElement('a');
            link.href = summary.url;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            link.title = 'Open the ' + opt[1] + ' website';
            link.textContent = opt[1] + ' ↗';
            name.appendChild(link);
        } else {
            name.textContent = opt[1];
        }
        var text = document.createElement('dd');
        text.textContent = summary.text || '';
        item.appendChild(name);
        item.appendChild(text);
        list.appendChild(item);
        items[opt[0]] = item;
    });
    function highlight() {
        Object.keys(items).forEach(function(api) {
            items[api].classList.toggle('active', api === select.value);
        });
    }
    select.addEventListener('change', highlight);
    highlight();
    return list;
}

// Local DEM File row -- same as the sidebar's demFilePath field and its
// tp3d.pick_dem_path button. A browser can't hand out a local path, so
// "File…"/"Folder…" ask picker_server (POST /pick_dem_path) to open the OS
// dialog, then poll GET /dem_path_status until it reports 'done' or
// 'cancelled' -- the same flow as export_folder.js. The server queues the
// choice as a 'demFilePath' setting for Blender itself.
function tp3dBuildDemPathRow() {
    var field = { key: 'demFilePath', showWhen: { key: 'api', equals: 'LOCAL_DEM' }, hideWhenInactive: true };
    var row = document.createElement('div');
    row.className = 'adv-field-row';
    MAP_TAB_ROWS.push({ field: field, row: row });

    var label = document.createElement('label');
    label.textContent = 'DEM File';
    row.appendChild(tp3dWithPreview(label, null, [
        'DEM File',
        'A GeoTIFF (.tif/.tiff) elevation file you downloaded, or a folder of tiled GeoTIFFs covering a larger area — only the tiles covering the map are read.',
        'WGS84 lat/lon, or UTM in WGS84, ETRS89 or NAD83.'
    ], 'DEM File', row));

    var pathEl = document.createElement('div');
    pathEl.className = 'dem-path';
    row.appendChild(pathEl);

    var controls = document.createElement('div');
    controls.className = 'adv-field-controls';
    var buttons = {};
    [['file', 'File…', 'Pick a GeoTIFF DEM file'],
     ['folder', 'Folder…', 'Pick a folder of tiled GeoTIFF DEM files']].forEach(function(def) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'element-mode-btn';
        btn.textContent = def[1];
        btn.title = def[2];
        btn.addEventListener('click', function() { pick(def[0]); });
        controls.appendChild(btn);
        buttons[def[0]] = btn;
    });
    row.appendChild(controls);

    var state = { path: SETTINGS_STATE.demFilePath || '', valid: true, picking: null };
    function render() {
        pathEl.textContent = state.path || 'No file selected';
        pathEl.title = state.path;
        pathEl.classList.toggle('warning-text', !state.path || !state.valid);
        if (state.path && !state.valid) pathEl.textContent = state.path + ' (not found)';
        Object.keys(buttons).forEach(function(kind) {
            buttons[kind].textContent = state.picking === kind ? 'Choosing…'
                : kind === 'file' ? 'File…' : 'Folder…';
        });
    }
    function poll() {
        fetch('/dem_path_status', { cache: 'no-store' })
            .then(function(r) { return r.json(); })
            .then(function(s) {
                state.path = s.path || '';
                state.valid = !!s.valid;
                if (s.status !== 'picking') state.picking = null;
                SETTINGS_STATE.demFilePath = state.path;
                render();
                if (s.status === 'picking') setTimeout(poll, 400);
            })
            .catch(function() { state.picking = null; render(); });
    }
    function pick(kind) {
        // A click while a dialog is already open brings it back to the front.
        if (!state.picking) state.picking = kind;
        render();
        fetch('/pick_dem_path', { method: 'POST', body: JSON.stringify({ kind: kind }) })
            .then(function() { setTimeout(poll, 400); })
            .catch(function() { state.picking = null; render(); });
    }
    render();
    poll();
    return row;
}

// Clear Cache -- same as the sidebar's tp3d.clear_cache button. Sent as the
// 'clearCache' key of /update_setting so Blender deletes the files on its
// own main thread (utils.ui_state.apply_setting_update), not the server's.
function tp3dBuildClearCacheRow() {
    var row = document.createElement('div');
    row.className = 'adv-field-row';
    row.title = 'Delete all cached elevation and map data downloaded from the APIs';

    var label = document.createElement('label');
    label.textContent = 'Cached API Data';
    row.appendChild(tp3dWithPreview(label, null, [
        'Clear Cache',
        'Deletes all downloaded data saved on your computer (elevation, map elements, satellite images).',
        'Frees disk space; the next generation downloads everything fresh, so it takes longer.'
    ], 'Cached API Data', row));

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'element-mode-btn';
    btn.textContent = 'Clear Cache';
    btn.addEventListener('click', function() {
        tp3dSendMapField({ key: 'clearCache', endpoint: 'update_setting' }, true);
        btn.disabled = true;
        btn.textContent = 'Cleared ✓';
        setTimeout(function() {
            btn.disabled = false;
            btn.textContent = 'Clear Cache';
        }, 2000);
    });
    row.appendChild(btn);
    return row;
}

// Two-button OSM / ESA WorldCover switch at the top of the Elements tab --
// only built when ELEMENT_SOURCE is defined (see element_status.js); every
// picker page now carries that token, so this shows up in all of them. The
// actual switch (POST /update_setting, poll, re-sync + repaint) lives in
// element_status.js's tp3dSwitchElementSource, shared with the compact pill
// toggle in the element-status row itself -- this just builds the buttons
// and wires them to it. Returns the button pair's wrapping block.
function tp3dBuildElementSourceSwitch() {
    // Titled by its section card (tp3dBuildElementsTab).
    var block = document.createElement('div');
    block.className = 'element-source-block';

    var wrap = document.createElement('div');
    wrap.className = 'element-source-switch';
    ELEMENT_SOURCE_OPTIONS.forEach(function(opt) {
        // The "?" sits inside the button's right edge -- as a sibling laid
        // over it, since a button can't contain another button.
        var option = document.createElement('span');
        option.className = 'element-source-option';
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'shape-btn' + (ELEMENT_SOURCE === opt.source ? ' active' : '');
        btn.setAttribute('data-tp3d-source-btn', '');
        btn.textContent = opt.label;
        btn.addEventListener('click', function() { tp3dSwitchElementSource(opt.source); });
        option.appendChild(btn);
        option.appendChild(tp3dMakePreviewButton(null, opt.caption, opt.label, option));
        wrap.appendChild(option);
    });
    block.appendChild(wrap);
    return block;
}
// Short text-only previews behind each source's "?".
var ELEMENT_SOURCE_OPTIONS = [
    // Overpass fetches every feature in the map's bbox -- the Area Threshold
    // filters only apply after the download (utils/terrain.py). WorldCover is
    // one image per 3x3 degree tile, capped at satellite._MAX_PX overall.
    { source: 'OSM', label: 'OpenStreetMap', caption: [
        'OpenStreetMap',
        '• Accurate data for water, forests and 3D elements like roads and buildings.',
        '• Takes longer for large regions, since it downloads every feature inside your map — even ones too small to be visible.',
        '• Ideal for smaller maps.'
    ] },
    { source: 'WORLDCOVER', label: 'ESA WorldCover', caption: [
        'ESA WorldCover',
        '• Land cover from satellite images. No 3D elements.',
        '• Outlines may look rougher.',
        '• Very fast, even for very large maps.'
    ] }
];

// Small two-button Paint on Map / Single Extruder Mode switch -- mirrors
// panels.py's own "Paint or Single Extruder Mode" row in the "5. Map
// Elements" sidebar section, which only shows up there when
// props.elementSource == "OSM" (ESA WorldCover has no per-element
// paint-vs-remesh split of its own). Unlike that sidebar row, this stays
// visible under WorldCover too -- just disabled with neither side marked
// active -- rather than disappearing, so the row doesn't jump around as the
// source switch above it is used. Posts straight to /update_setting's
// 'elementMode' key (now part of _SETTINGS_ROW_FIELDS, same whitelist
// elementSource itself uses) -- no poll-and-resync needed here the way the
// source switch needs one: unlike elementSource, changing elementMode
// doesn't change which fields/cards the rest of this tab shows -- it only
// greys singleExtruderOnly fields in or out (tp3dRefreshSingleExtruderFields).
// Visual example behind the row's "?" button (also highlighted by the
// "Colorize your Map" tutorial, assets/map_tutorials.js).
var ELEMENT_MODE_PREVIEW = 'https://trailprint3d.com/images/howto/PaintVsSingleExtruder.webp';
function tp3dBuildElementModeSwitch() {
    var row = document.createElement('div');
    row.className = 'adv-field-row element-mode-row';

    var label = document.createElement('label');
    label.textContent = 'Paint or Single Extruder Mode';
    row.appendChild(tp3dWithPreview(label, ELEMENT_MODE_PREVIEW, null, 'Paint or Single Extruder Mode', row));

    var isWorldCover = tp3dIsWorldCover();
    var wrap = document.createElement('div');
    wrap.className = 'element-mode-switch';
    wrap.title = isWorldCover ? 'Only available for OpenStreetMap' : '';
    [['PAINT', 'Paint'], ['SINGLECOLORMODE_REMESH', 'Single Extruder']].forEach(function(opt) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'element-mode-btn' + (!isWorldCover && SETTINGS_STATE.elementMode === opt[0] ? ' active' : '');
        btn.disabled = isWorldCover;
        btn.textContent = opt[1];
        btn.addEventListener('click', function() {
            if (SETTINGS_STATE.elementMode === opt[0]) return;
            SETTINGS_STATE.elementMode = opt[0];
            wrap.querySelectorAll('button').forEach(function(b) { b.classList.toggle('active', b === btn); });
            tp3dRefreshSingleExtruderFields();
            tp3dRefreshMapTabVisibility(); // texture fields depend on elementMode
            tp3dSendMapField({ key: 'elementMode', endpoint: 'update_setting' }, opt[0]);
        });
        wrap.appendChild(btn);
    });
    row.appendChild(wrap);
    return row;
}

// On/off toggle plus strength slider for OSM element smoothing
// (props.col_osmSmoothing, panels.py's "Element Smoothing" -- a 0-1 FACTOR
// slider that utils/terrain.py turns into int(value * 20) Taubin smoothing
// rounds). The toggle sits left of a range+number pair (same
// .range-with-number look as tp3dBuildRangeField) that's shown only while ON:
// the slider snaps to 0.1 steps for quick picks, the number box takes any
// exact value. Min is 0.05 because anything lower rounds down to 0 rounds,
// i.e. silently off. OFF writes exactly 0; turning it back ON restores the
// last strength used this session (TP3D_OSM_SMOOTHING_ON the first time).
// OSM-only, same as panels.py's own placement inside its `elif
// props.elementSource == "OSM":` branch -- ESA WorldCover has no equivalent.
var TP3D_OSM_SMOOTHING_ON = 0.5;
var TP3D_OSM_SMOOTHING_MIN = 0.05;
function tp3dBuildOsmSmoothingToggle() {
    var row = document.createElement('div');
    row.className = 'adv-field-row element-mode-row';
    row.title = 'Rounds element polygon edges, making them easier to print';

    var label = document.createElement('label');
    label.textContent = 'Element Smoothing';
    row.appendChild(tp3dWithPreview(label, 'https://trailprint3d.com/images/howto/ElementSmoothing.webp',
        'Smooth the outline of the elements so they are easier to print.', 'Element Smoothing', row));

    var current = ADVANCED_SETTINGS_STATE.colOsmSmoothing || 0;
    var isOn = current > 0;
    var lastOnValue = isOn ? tp3dRoundForDisplay(current, 2) : TP3D_OSM_SMOOTHING_ON;

    var controls = document.createElement('div');
    controls.className = 'adv-field-controls';

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'element-mode-btn' + (isOn ? ' active' : '');
    btn.textContent = isOn ? 'On' : 'Off';

    // Hidden via style.display, not the `hidden` attribute --
    // .range-with-number's own `display: flex` would override that.
    var strengthWrap = document.createElement('div');
    strengthWrap.className = 'range-with-number';
    strengthWrap.title = 'Smoothing strength (0-1) -- higher rounds edges more but loses detail';
    strengthWrap.style.display = isOn ? '' : 'none';
    // Explicit width: inside the content-sized .adv-field-controls, the
    // class's own flex:1 + min-width:0 would otherwise let the range collapse.
    strengthWrap.classList.add('smoothing-strength');

    var range = document.createElement('input');
    range.type = 'range';
    range.min = 0.1;
    range.max = 1;
    range.step = 0.1;
    range.value = lastOnValue;

    var number = document.createElement('input');
    number.type = 'number';
    number.min = TP3D_OSM_SMOOTHING_MIN;
    number.max = 1;
    number.step = 0.01;
    number.value = lastOnValue;

    function commit(value) {
        value = parseFloat(Math.min(Math.max(value, TP3D_OSM_SMOOTHING_MIN), 1).toFixed(2));
        number.value = value;
        range.value = value;
        lastOnValue = value;
        ADVANCED_SETTINGS_STATE.colOsmSmoothing = value;
        tp3dSendAdvancedUpdate('colOsmSmoothing', value);
    }

    // Live-mirror into the number box while dragging, but only send once on
    // release rather than on every intermediate step.
    range.addEventListener('input', function() {
        number.value = parseFloat(parseFloat(range.value).toFixed(2));
    });
    range.addEventListener('change', function() { commit(parseFloat(range.value)); });
    number.addEventListener('change', function() {
        var value = parseFloat(number.value);
        if (isNaN(value)) {
            number.value = lastOnValue;
            return;
        }
        commit(value);
    });

    btn.addEventListener('click', function() {
        isOn = !isOn;
        var value = isOn ? lastOnValue : 0.0;
        ADVANCED_SETTINGS_STATE.colOsmSmoothing = value;
        btn.classList.toggle('active', isOn);
        btn.textContent = isOn ? 'On' : 'Off';
        strengthWrap.style.display = isOn ? '' : 'none';
        tp3dSendAdvancedUpdate('colOsmSmoothing', value);
    });

    strengthWrap.appendChild(range);
    strengthWrap.appendChild(number);
    controls.appendChild(btn);
    controls.appendChild(strengthWrap);
    row.appendChild(controls);
    return row;
}

function tp3dBuildElementsTab() {
    var wrap = document.createElement('div');
    wrap.className = 'settings-sections';

    // Top card: the source switch, then the source's own top-level option --
    // Element Smoothing under OSM, Min Feature Area under ESA WorldCover.
    var sourceSection = tp3dBuildSection('Element Source', 'settings-grid-2');
    if (typeof ELEMENT_SOURCE !== 'undefined') {
        sourceSection.grid.appendChild(tp3dBuildElementSourceSwitch());
    }
    sourceSection.grid.appendChild(tp3dIsWorldCover()
        ? tp3dBuildFieldRow(WORLDCOVER_MIN_AREA_FIELD)
        : tp3dBuildOsmSmoothingToggle());
    wrap.appendChild(sourceSection.card);

    var cardsSection = tp3dBuildSection('Elements', 'elements-grid');
    wrap.appendChild(cardsSection.card);

    // ESA WorldCover has none of OSM's per-category thresholds or
    // Water/Roads composites -- one shared Min Feature Area field (in the
    // options row above) plus a row of plain toggle cards, mirroring
    // panels.py's "5. Map Elements" WORLDCOVER branch structure.
    if (tp3dIsWorldCover()) {
        var landcoverRow = document.createElement('div');
        landcoverRow.className = 'elements-row';
        LANDCOVER_ELEMENT_ORDER.forEach(function(key) { landcoverRow.appendChild(tp3dBuildSimpleElementCard(key)); });
        cardsSection.grid.appendChild(landcoverRow);
    } else {
        var simpleRow = document.createElement('div');
        simpleRow.className = 'elements-row';
        SIMPLE_ELEMENT_ORDER.forEach(function(key) { simpleRow.appendChild(tp3dBuildSimpleElementCard(key)); });
        cardsSection.grid.appendChild(simpleRow);

        var compositeRow = document.createElement('div');
        compositeRow.className = 'elements-row';
        COMPOSITE_ELEMENT_ORDER.forEach(function(key) { compositeRow.appendChild(tp3dBuildCompositeElementCard(key)); });
        compositeRow.appendChild(tp3dBuildSimpleElementCard('buildings'));
        cardsSection.grid.appendChild(compositeRow);
    }

    // The Paint/Single Extruder switch and the Element
    // Tolerance it gates (OSM only, like the sidebar).
    var modeSection = tp3dBuildSection('Print Mode', 'settings-grid-2');
    if (typeof ELEMENT_SOURCE !== 'undefined') modeSection.grid.appendChild(tp3dBuildElementModeSwitch());
    if (!tp3dIsWorldCover()) modeSection.grid.appendChild(tp3dBuildFieldRow(ELEMENT_TOLERANCE_FIELD));
    // ESA WorldCover has no Paint/Single Extruder split -- the whole card is
    // greyed out (its buttons are already disabled).
    if (tp3dIsWorldCover()) {
        modeSection.card.classList.add('section-locked');
        modeSection.card.title = 'Only available for OpenStreetMap';
    }
    if (modeSection.grid.children.length) wrap.appendChild(modeSection.card);
    tp3dRefreshMapTabVisibility();

    return wrap;
}

// Relocates the puzzle-cut field-rows (Tab Size, Jitter, Seed, Corner Radius, Tolerance;
// just Corner Radius and Tolerance on the sliding puzzle page) out of their hidden sidebar container into this tab -- same elements, same
// ids, so the page's own saveState/restoreState/regeneratePuzzle listeners
// keep working unchanged regardless of where they end up living in the DOM.
//
// In a second card, the same relocation for the holder (jigsaw) / frame
// (sliding) fields from #plateTabFieldsSource -- formerly their own Plate/
// Frame Settings popup -- into their own #plateTabFields group.
function tp3dBuildPuzzleTab() {
    var wrap = document.createElement('div');
    wrap.className = 'settings-sections';
    var sliding = typeof TP3D_IS_SLIDING_PUZZLE_PAGE !== 'undefined' && TP3D_IS_SLIDING_PUZZLE_PAGE;
    var pieces = tp3dBuildSection(sliding ? 'Tiles' : 'Puzzle Pieces');
    tp3dRelocateFields('puzzleTabFieldsSource', pieces.grid);
    wrap.appendChild(pieces.card);
    if (document.getElementById('plateTabFieldsSource')) {
        var plate = tp3dBuildSection(sliding ? 'Frame' : 'Holder');
        tp3dRelocateFields('plateTabFieldsSource', plate.grid);
        plate.grid.id = 'plateTabFields';
        wrap.appendChild(plate.card);
    }
    tp3dAddPuzzlePreviews(wrap, sliding ? SLIDING_PUZZLE_PREVIEWS : JIGSAW_PUZZLE_PREVIEWS);
    return wrap;
}

// Guide "?" for the relocated puzzle fields, keyed by input id. The jigsaw
// and sliding pages share some ids (holder*), so each page type has its own map.
var PUZZLE_PREVIEW_BASE = 'https://trailprint3d.com/images/howto/';
var JIGSAW_PUZZLE_PREVIEWS = {
    tabSizeInput: { img: 'JigsawTabSize.webp', caption: 'Size of the tabs that interlock the puzzle pieces.' },
    jitterInput: { img: 'JigsawJitter.webp', caption: 'Random variation of the cut lines, so every piece gets its own shape.' },
    puzzleCornerRadiusInput: { img: 'JigsawCornerRadius.webp', caption: 'Rounds the outer corners of the puzzle.' },
    puzzleToleranceInput: { img: 'JigsawTolerance.webp', caption: ['Gap between neighboring puzzle pieces (each piece shrinks by half of it).',
        'Depending on the printer, I recommend 0.2-0.3mm tolerance.'] },
    holderWallWidthInput: { img: 'JigsawFrameThickness.webp', caption: 'Width of the holder frame around the puzzle.' },
    holderCornerRadiusInput: { img: 'JigsawFrameCornerRadius.webp', caption: 'Rounds the outer corners of the holder.' },
    holderSeamEngravingInput: { img: 'JigsawEngravePattern.webp', caption: 'Engraves the puzzle cut pattern into the floor of the holder.' },
    holderFrameTerrainInput: { img: 'JigsawTerrainOnFrame.webp', caption: 'Continues the terrain onto the holder frame instead of a flat rim.' },
    holderTextInput: { img: 'JigsawText.webp', caption: 'Text embossed on the flat rim of the holder.' }
};
var SLIDING_PUZZLE_PREVIEWS = {
    cornerRadiusInput: { img: 'SlidingCornerRadius.webp', caption: ['Rounds the corners of every tile.',
        'I recommend the default of 1mm.'] },
    toleranceInput: { img: 'SlidingTolerance.webp', caption: ['Gap between neighboring tiles, and between the tiles and the frame.',
        'Depending on the printer, I recommend 0.2-0.3mm tolerance.'] },
    holderWallWidthInput: { img: 'SlidingFrameThickness.webp', caption: 'Width of the frame around the tiles.' },
    holderCornerRadiusInput: { img: 'SlidingFrameCornerRadius.webp', caption: 'Rounds the outer corners of the frame.' },
    holderFrameTerrainInput: { img: 'SlidingTerrainOnFrame.webp', caption: 'Continues the terrain onto the frame instead of a flat rim.' },
    holderTopBevelInput: { img: 'SlidingTopBevel.webp', caption: 'Chamfers the top outer edge of the frame.' },
    holderTextInput: { img: 'SlidingText.webp', caption: 'Text embossed on the flat rim of the frame.' }
};
function tp3dAddPuzzlePreviews(root, previews) {
    Object.keys(previews).forEach(function(id) {
        var label = root.querySelector('label[for="' + id + '"]');
        if (!label) return;
        var parent = label.parentNode;
        var next = label.nextSibling;
        var entry = previews[id];
        parent.insertBefore(tp3dWithPreview(label, PUZZLE_PREVIEW_BASE + entry.img, entry.caption,
            label.textContent, label.closest('.field-row')), next);
    });
}

// Moves every child of the hidden #sourceId container into the fields
// *grid*, and carries its classes over too (e.g. a page's initial
// tab-fields-inactive).
function tp3dRelocateFields(sourceId, grid) {
    var source = document.getElementById(sourceId);
    if (source) {
        source.classList.forEach(function(cls) { grid.classList.add(cls); });
        while (source.firstElementChild) {
            grid.appendChild(source.firstElementChild);
        }
    }
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
    var guideToggleBtn = document.createElement('button');
    guideToggleBtn.type = 'button';
    guideToggleBtn.className = 'guide-toggle-btn';
    guideToggleBtn.setAttribute('aria-controls', 'settingsPreviewPanel');
    guideToggleBtn.setAttribute('aria-expanded', String(TP3D_GUIDE_VISIBLE));
    guideToggleBtn.textContent = TP3D_GUIDE_VISIBLE ? 'Hide guide' : 'Show guide';
    guideToggleBtn.addEventListener('click', tp3dGuideUserToggle);
    TP3D_GUIDE_TOGGLE_BTN = guideToggleBtn;
    var closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'tp3d-modal-close-btn';
    closeBtn.title = 'Close';
    closeBtn.textContent = '✕';
    header.appendChild(title);
    header.appendChild(guideToggleBtn);
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
    panelHost.className = 'settings-panel-host';
    left.appendChild(tabBar);
    left.appendChild(panelHost);
    body.appendChild(left);

    // Setting guide: visible by default on wide modals and updated on
    // hover/selection; hidden on narrow ones until a "?" or the header Show
    // button is used (.guide-hidden controls it, see picker_common.css).
    // Content is swapped in place by tp3dGuide*.
    var right = document.createElement('div');
    right.className = 'settings-modal-right';
    right.id = 'settingsPreviewPanel';
    right.setAttribute('role', 'region');
    right.setAttribute('aria-label', 'Setting guide');
    right.innerHTML = '<div class="preview-title">Setting guide</div>'
        + '<div class="preview-media"></div>'
        + '<div class="preview-caption">Hover or select a setting to see a description here.</div>'
        + '<div class="guide-sr-only" id="settingsGuideAnnounce" role="status" aria-live="polite"></div>';
    body.appendChild(right);

    // Delegated listeners so the guide survives tp3dRebuildElementsTab /
    // tp3dRebuildMapTab without re-binding anything. Hover is gated on
    // (hover: hover) and (pointer: fine) so touch users just pin via click
    // (which both the "?" button and the delegated click handler below do).
    // Compare by data-guide-id, not by the element that matched: a pill
    // (trail-appearance option) and its inner "?" share the same ID but are
    // different elements, so an element-only comparison would fire a leave
    // as the pointer slid from the pill's button onto its own "?".
    function guideIdOf(el) {
        var hit = el && el.closest && el.closest('[data-guide-id]');
        return hit ? hit.getAttribute('data-guide-id') : null;
    }
    function onGuideEnter(target) {
        clearTimeout(TP3D_GUIDE_STATE.leaveTimer);
        clearTimeout(TP3D_GUIDE_STATE.hoverTimer);
        // Hover never re-opens a hidden guide and never overrides an
        // explicit off: it only updates the panel's contents while the
        // guide is on.
        if (!TP3D_GUIDE_VISIBLE) return;
        var id = guideIdOf(target);
        if (!id) return;
        if (TP3D_GUIDE_STATE.hoveredId === id) return;
        TP3D_GUIDE_STATE.hoverTimer = setTimeout(function() {
            TP3D_GUIDE_STATE.hoveredId = id;
            tp3dGuideDisplay();
        }, 100);
    }
    function onGuideLeave(target, related) {
        clearTimeout(TP3D_GUIDE_STATE.hoverTimer);
        var from = guideIdOf(target);
        if (!from) return;
        var to = guideIdOf(related);
        if (to === from) return;
        TP3D_GUIDE_STATE.leaveTimer = setTimeout(function() {
            TP3D_GUIDE_STATE.hoveredId = null;
            tp3dGuideDisplay();
        }, 150);
    }
    if (TP3D_GUIDE_HOVER_OK) {
        body.addEventListener('mouseover', function(e) { onGuideEnter(e.target); });
        body.addEventListener('mouseout', function(e) { onGuideLeave(e.target, e.relatedTarget); });
    }
    body.addEventListener('focusin', function(e) {
        // Only pin when the guide is already on -- focusing a field never
        // turns it back on.
        if (!TP3D_GUIDE_VISIBLE) return;
        var el = e.target.closest && e.target.closest('[data-guide-id]');
        if (el) tp3dGuidePin(el.getAttribute('data-guide-id'));
    });
    body.addEventListener('click', function(e) {
        // Same as focusin: clicking a field/button never re-shows a
        // hidden guide. Only a "?" click or the header Show button do.
        if (!TP3D_GUIDE_VISIBLE) return;
        var el = e.target.closest && e.target.closest('[data-guide-id]');
        if (el) tp3dGuidePin(el.getAttribute('data-guide-id'));
    });

    var TABS = [
        { id: 'elements', label: 'Elements', build: tp3dBuildElementsTab },
        { id: 'map', label: 'Map', build: tp3dBuildMapTab }
    ];
    TABS.push({ id: 'other', label: 'Other', build: tp3dBuildOtherTab });
    // The sliding puzzle page gets the tab too, for its own (fewer) fields.
    // Last, after Other, and tinted green (picker_common.css) as page-specific.
    if ((typeof TP3D_IS_PUZZLE_PAGE !== 'undefined' && TP3D_IS_PUZZLE_PAGE) ||
            (typeof TP3D_IS_SLIDING_PUZZLE_PAGE !== 'undefined' && TP3D_IS_SLIDING_PUZZLE_PAGE)) {
        TABS.push({ id: 'puzzle', label: 'Puzzle', build: tp3dBuildPuzzleTab });
    }
    var panels = {};
    var tabBtns = {};
    tabBar.setAttribute('role', 'tablist');
    tabBar.setAttribute('aria-label', 'Settings categories');

    function activateTab(id) {
        if (!tabBtns[id]) return;
        // A pinned setting belongs to the tab it was on.
        if (TP3D_SETTINGS_TAB !== id) tp3dGuideUnpin();
        TP3D_SETTINGS_TAB = id;
        TABS.forEach(function(tab) {
            tabBtns[tab.id].classList.toggle('active', tab.id === id);
            tabBtns[tab.id].setAttribute('aria-selected', String(tab.id === id));
            tabBtns[tab.id].tabIndex = tab.id === id ? 0 : -1;
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
        btn.id = 'settings-tab-' + tab.id;
        btn.setAttribute('role', 'tab');
        btn.setAttribute('aria-controls', 'settings-panel-' + tab.id);
        btn.textContent = tab.label;
        btn.addEventListener('click', function() {
            activateTab(tab.id);
            saveState(); // persists TP3D_SETTINGS_TAB via the page's own state blob
        });
        btn.addEventListener('keydown', function(event) {
            var index = TABS.indexOf(tab);
            if (event.key === 'ArrowRight') index = (index + 1) % TABS.length;
            else if (event.key === 'ArrowLeft') index = (index + TABS.length - 1) % TABS.length;
            else if (event.key === 'Home') index = 0;
            else if (event.key === 'End') index = TABS.length - 1;
            else return;
            event.preventDefault();
            activateTab(TABS[index].id);
            tabBtns[TABS[index].id].focus();
            saveState();
        });
        tabBar.appendChild(btn);
        tabBtns[tab.id] = btn;

        var panel = document.createElement('div');
        panel.className = 'settings-modal-tab-panel';
        panel.id = 'settings-panel-' + tab.id;
        panel.setAttribute('role', 'tabpanel');
        panel.setAttribute('aria-labelledby', btn.id);
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
        tp3dClearTabPanel(panels.elements);
        panels.elements.appendChild(tp3dBuildElementsTab());
        // SETTINGS_STATE was just resynced (e.g. WorldCover forcing Paint
        // mode) -- the Map tab's texture fields depend on its elementMode.
        tp3dRefreshMapTabVisibility();
        tp3dRepaintAllElementToggles();
    };

    // Same idea for the Map tab -- used by assets/history_panel.js's
    // tp3dApplyHistorySettings after pushing a history entry's SETTINGS_STATE
    // back to Blender, so the modal's Elevation Scale/Path Thickness/etc.
    // inputs pick up the restored values instead of showing whatever was
    // there when the modal was first built.
    window.tp3dRebuildMapTab = function() {
        if (!panels.map) return;
        tp3dClearTabPanel(panels.map);
        panels.map.appendChild(tp3dBuildMapTab());
        // A history entry also restores the API and texture resolution --
        // same refresh for the Other tab.
        tp3dClearTabPanel(panels.other);
        panels.other.appendChild(tp3dBuildOtherTab());
    };

    document.body.appendChild(modal);
    // Apply the initial visibility state (box class + button label) so the
    // panel doesn't flash visible for a frame the first time the modal
    // opens on a narrow viewport. A saved guideVisible in the state file
    // overrides this later, via tp3dApplyStateSnapshot.
    tp3dGuideApplyVisible(TP3D_GUIDE_VISIBLE);
    // The cards' own per-card repaint ran while they were still detached from
    // the document, so it painted nothing -- do it again now they're attached.
    tp3dRepaintAllElementToggles();

    // Preload every guide image once on open so hover doesn't flash an
    // empty media box on first hover. Deduped by URL (several fields share
    // the same image, e.g. all the element-threshold GIFs).
    function preloadGuideImages() {
        var seen = {};
        Object.keys(TP3D_GUIDE_REGISTRY).forEach(function(id) {
            var url = TP3D_GUIDE_REGISTRY[id].url;
            if (url && !seen[url]) { seen[url] = true; new Image().src = url; }
        });
    }
    function openModal() {
        modal.classList.add('open');
        preloadGuideImages();
    }
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
    // Not one of the classes tp3dRenderElementStatus clears, so it stays
    // put between the gear and the OSM/ESA pill across re-renders.
    if (TP3D_COLOR_STYLES.builtIn.length) {
        elementStatus.insertBefore(tp3dBuildCompactColorStyleSelect(), gearBtn.nextSibling);
    }
})();