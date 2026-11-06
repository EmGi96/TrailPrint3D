// Browser-only fixture: no Blender state, generation, or user files are changed.
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const root = path.resolve(__dirname, '..');
const addon = path.join(root, 'TrailPrint3D');
const pages = [
    'generators/map_generator.html',
    'generators/puzzleGenerator.html',
    'generators/medalHolderGenerator.html',
    'generators/backplateGenerator.html',
    'premium/generators/map_generator_pe.html',
    'premium/generators/puzzleGenerator_pe.html',
    'premium/generators/multitile_generator.html',
    'premium/generators/slidingPuzzleGenerator.html'
];
const assets = {
    COMMON_CSS: 'picker_common.css', PICKER_UI_JS: 'picker_ui.js',
    MAP_INIT_JS: 'map_init.js', LOCATION_PANEL_JS: 'location_panel.js',
    ELEMENT_STATUS_JS: 'element_status.js', SETTINGS_MODAL_JS: 'settings_modal.js',
    HISTORY_PANEL_JS: 'history_panel.js', RECT_EDITOR_JS: 'rect_editor.js',
    PREFETCH_JS: 'prefetch_layer.js', SHAPE_EXTRAS_JS: 'shape_extras.js',
    TUTORIAL_JS: 'tutorial.js', MAP_TUTORIALS_JS: 'map_tutorials.js',
    EXPORT_FOLDER_JS: 'export_folder.js', MULTITILE_TUTORIALS_JS: 'multitile_tutorials.js',
    THREEMF_PROMO_JS: 'threemf_promo.js'
};
const elementStates = Object.fromEntries([
    'water', 'forest', 'roads', 'city', 'greenspace', 'farmland',
    'scree', 'glacier', 'buildings', 'ocean'
].map(key => [key, true]));
const icon = '<svg viewBox="0 0 20 20" aria-hidden="true"><path fill="currentColor" d="M2 2h16v16H2z"/></svg>';
const settingsState = {elementMode: 'PAINT', scaleElevation: 1, minThickness: 2, elementSource: 'OSM', api: 'MAPTERHORN', disableCache: false, texIncludeTrail: false, texResolution: 2048};
const advancedSettings = {_compositeMaster: {water: true, roads: true}, colWPondsActive: true, elSResidentialActive: true, toleranceElements: 0.12, elORdpEpsilon: 0, texIncludeRoads: false};
function renderPage(index, port) {
    const tokens = {
        PORT: port, OBJSIZE: 100, DEM_BOUNDS_JS: 'var DEM_BOUNDS = null;',
        ELEMENT_ICONS_JS: 'var ELEMENT_ICONS = ' + JSON.stringify(Object.fromEntries(Object.keys(elementStates).map(key => [key, icon]))) + ';',
        ELEMENT_STATES_JS: 'var ELEMENT_STATES = ' + JSON.stringify(elementStates) + ';',
        ELEMENT_SOURCE_JS: 'var ELEMENT_SOURCE = "OSM";',
        SETTINGS_STATE_JS: 'var SETTINGS_STATE = ' + JSON.stringify(settingsState) + ';',
        ADVANCED_SETTINGS_STATE_JS: 'var ADVANCED_SETTINGS_STATE = ' + JSON.stringify(advancedSettings) + ';'
    };
    for (const [token, file] of Object.entries(assets)) {
        tokens[token] = fs.readFileSync(path.join(addon, 'assets', file), 'utf8');
    }
    // Same as picker_server.py: the generated icon outlines ride along.
    tokens.SHAPE_EXTRAS_JS = fs.readFileSync(path.join(addon, 'assets', 'text_icons.js'), 'utf8') + '\n' + tokens.SHAPE_EXTRAS_JS;
    return fs.readFileSync(path.join(addon, pages[index]), 'utf8')
        .replace(/__([A-Z_]+)__/g, (match, token) => {
            if (!(token in tokens)) throw new Error('Unknown template token: ' + token);
            return tokens[token];
        });
}
function startFixture(port = 0) {
    const server = http.createServer((request, response) => {
        const url = new URL(request.url, 'http://localhost');
        if (url.pathname === '/page') {
            const index = Number(url.searchParams.get('index'));
            if (!Number.isInteger(index) || index < 0 || index >= pages.length) {
                response.writeHead(400).end('Invalid page index');
                return;
            }
            response.writeHead(200, {'Content-Type': 'text/html; charset=utf-8'});
            response.end(renderPage(index, server.address().port).replace('</body>', '<script src="/checks.js"></script></body>'));
            return;
        }
        if (url.pathname === '/checks.js') {
            response.writeHead(200, {'Content-Type': 'text/javascript'});
            response.end(fs.readFileSync(path.join(__dirname, 'picker_ui_browser_checks.js')));
            return;
        }
        if (url.pathname === '/get_history') {
            if (url.searchParams.has('fail')) {
                response.writeHead(503).end('Fixture failure');
                return;
            }
            response.writeHead(200, {'Content-Type': 'application/json'});
            response.end(JSON.stringify([
                {id: 'fixture-1', timestamp: 1750000000, summary: 'Lake district, 100 mm', settings: {coords: {north: 47, south: 46, east: 8, west: 7}}},
                {id: 'fixture-2', timestamp: 1749999999, summary: 'Lake district, 120 mm', settings: {coords: {north: 47, south: 46, east: 8, west: 7}}}
            ]));
            return;
        }
        let data = {};
        if (url.pathname === '/get_existing_maps' || url.pathname === '/get_existing_trails' || url.pathname === '/delete_history_entry') data = [];
        else if (url.pathname === '/get_source_state') data = {elementSource: 'OSM', elementStates, settingsState, advancedSettings};
        else if (url.pathname === '/prefetch_status' || url.pathname === '/model_preview_status') data = {status: 'idle', version: 0};
        response.writeHead(200, {'Content-Type': 'application/json'});
        response.end(JSON.stringify(data));
    });
    server.listen(port, '127.0.0.1', () => console.log('Picker UI fixture: http://127.0.0.1:' + server.address().port + '/page?index=0'));
    return server;
}
module.exports = {pages, renderPage, startFixture};
if (require.main === module) startFixture(Number(process.argv[2]) || 0);
