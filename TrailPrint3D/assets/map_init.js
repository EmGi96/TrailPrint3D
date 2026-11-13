// Shared base-map setup -- reused by every 2D-map picker page via
// __MAP_INIT_JS__ in picker_server.py. Requires `saveState` to be defined
// later in the including page's own <script> block (only referenced from
// the 'baselayerchange' handler below, which fires on user interaction
// well after the whole page has finished loading, so declaration order
// doesn't matter). Unlike saveState, DEM_BOUNDS (see the bottom of this
// file) IS read immediately at load time, so every including page must place
// its own __DEM_BOUNDS_JS__ placeholder before __MAP_INIT_JS__.
var map = L.map('map', { doubleClickZoom: false }).setView([46.57, 7.98], 11);
new ResizeObserver(function() { map.invalidateSize({ pan: false }); }).observe(map.getContainer());

// The draw/shape/mode toggle panels, coord search box and legend are plain
// DOM children of the #map container (positioned absolutely on top of it)
// rather than L.Control instances, so Leaflet never got a chance to wire up
// its usual click-propagation guard for them. Without it, a mousedown on one
// of these buttons also reaches the map underneath -- e.g. clicking a draw
// mode button while a rectangle draw is armed (waiting for the next map
// mousedown to place the first corner/center) starts drawing right behind
// the button instead of just switching modes. Skip Leaflet's own panes so
// map panning/zooming behavior is untouched.
Array.prototype.forEach.call(document.getElementById('map').children, function (el) {
    if (el.classList.contains('leaflet-pane') || el.classList.contains('leaflet-control-container')) return;
    L.DomEvent.disableClickPropagation(el);
    L.DomEvent.disableScrollPropagation(el);
});

// Pinned explicitly on every tile <img> (Leaflet's `referrerPolicy` option)
// instead of relying on the browser's default. OSMF's tile servers reject
// browser requests that carry no Referer -- they serve an "Access blocked"
// placeholder image in place of every tile (with an HTTP 200, so nothing
// errors, the map just looks broken) -- and a per-image policy wins over a
// document-level one, so a future <meta name="referrer"> or Referrer-Policy
// header on these pages can't silently strip it. strict-origin-when-cross-
// origin sends only the origin (http://127.0.0.1:<port>/), never the full
// page URL. The other providers get the same policy, for consistency.
var TP3D_TILE_REFERRER_POLICY = 'strict-origin-when-cross-origin';
var OSM_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a> contributors';

var baseLayers = {
    // Single canonical hostname, no '{s}.' subdomain prefix -- OSMF's Tile
    // Usage Policy (operations.osmfoundation.org/policies/tiles) documents
    // https://tile.openstreetmap.org/{z}/{x}/{y}.png as the URL to use. The
    // old lettered a/b/c.tile.openstreetmap.org subdomains (Leaflet's
    // default 'abc' `{s}` sharding, a leftover from the days browsers
    // capped parallel connections per hostname) are deprecated and are why
    // some users saw 403s here -- whichever letter a given tile's URL
    // happened to hash to could be one of the now-blocked subdomains, while
    // others (hashing to a still-working one) saw nothing wrong. The same
    // policy asks for the attribution to link to the copyright page.
    'OpenStreetMap': L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        referrerPolicy: TP3D_TILE_REFERRER_POLICY,
        attribution: OSM_ATTRIBUTION
    }),
    'Satellite': L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
        referrerPolicy: TP3D_TILE_REFERRER_POLICY,
        attribution: 'Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community'
    }),
    // Replaces CARTO's Voyager, which now serves an "API KEY REQUIRED"
    // placeholder for every tile without a per-project key (and any key
    // embedded here would be shared and exhausted across every install).
    // Both of these are keyless: OpenTopoMap is free OSM-based topo
    // (contours + hillshade, handy for judging terrain before printing, fair
    // use, tops out at z17); Esri's World Topo Map is the same arcgisonline
    // server the Satellite layer already uses, with a cleaner Voyager-like
    // look. A previously saved 'Voyager' selection just fails the
    // `baseLayers[s.baseLayer]` check in each page's restoreState and falls
    // back to the OpenStreetMap default.
    'Topographic': L.tileLayer('https://tile.opentopomap.org/{z}/{x}/{y}.png', {
        maxZoom: 17,
        referrerPolicy: TP3D_TILE_REFERRER_POLICY,
        attribution: 'Map data: ' + OSM_ATTRIBUTION + ', SRTM | Style: &copy; <a href="https://opentopomap.org" target="_blank">OpenTopoMap</a> (CC-BY-SA)'
    }),
    'Esri Topo': L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}', {
        referrerPolicy: TP3D_TILE_REFERRER_POLICY,
        attribution: 'Tiles &copy; Esri &mdash; Esri and the GIS User Community'
    })
};
var activeBaseLayerName = 'OpenStreetMap';
baseLayers[activeBaseLayerName].addTo(map);
// Overridden before addTo(): Leaflet binds this.collapse to mouseleave and map
// clicks there. While held (map_tutorials.js's tp3dHoldLayersOpen), the list
// stays open instead of closing on those.
var tp3dLayersHeldOpen = false;
var tp3dLayersControl = L.control.layers(baseLayers, null, { position: 'bottomleft' });
tp3dLayersControl.collapse = function () {
    if (tp3dLayersHeldOpen) return this;
    return L.Control.Layers.prototype.collapse.call(this);
};
tp3dLayersControl.addTo(map);
map.on('baselayerchange', function (e) {
    activeBaseLayerName = e.name;
    saveState();
});

// Reference overlay for the currently selected Local DEM File's coverage extent
// (see utils.geotiff.dem_coverage() / picker_server.py's __DEM_BOUNDS_JS__).
// DEM_BOUNDS is null unless the Local DEM File API is active and something is
// selected -- drawn as plain dashed polygons, not click-editable, so they can't be
// confused with the trail-area selection itself. A single file gives one footprint
// ({footprint, name}); a tile folder gives one per tile ({tiles: [...], name})
// rather than a single bounding box, so gaps between tiles (a non-rectangular
// bulk-download area) don't silently look covered. Neither key is there for an
// unreadable file ({error, name}), which only the Settings popup's DEM row reports.
//
// "footprint" is each tile's actual 4 corners, not an axis-aligned box: a UTM
// raster's grid north only matches true north exactly at its zone's central
// meridian, so away from it a raster square is very slightly rotated relative to
// true north (see geotiff.get_geotiff_footprint). Drawing the true corners as a
// polygon means two tiles that share an edge in real easting/northing space still
// share that edge's lat/lon corners and line up exactly; squaring each tile off
// into its own axis-aligned box independently would leave a visible brick-like
// stagger between neighbors instead.
var tp3dDemLayer = L.featureGroup().addTo(map);
var tp3dDemLegendRow = null;
var tp3dDemCoverage = null;  // also read by the Settings popup's DEM row
var tp3dDemStatusKey = null;

// "Center DEM" -- bottom-center on the map, only while a coverage outline is
// drawn; fits the view to the whole outline. Same click/scroll guard as the
// other #map overlays (see the top of this file), since it's added later.
var tp3dDemCenterBtn = document.createElement('button');
tp3dDemCenterBtn.type = 'button';
tp3dDemCenterBtn.className = 'map-dem-center-btn';
tp3dDemCenterBtn.textContent = 'Center DEM';
tp3dDemCenterBtn.title = 'Show the whole local DEM coverage';
tp3dDemCenterBtn.style.display = 'none';
tp3dDemCenterBtn.addEventListener('click', function () {
    if (tp3dDemLayer.getLayers().length) map.fitBounds(tp3dDemLayer.getBounds(), { padding: [40, 40] });
});
map.getContainer().appendChild(tp3dDemCenterBtn);
L.DomEvent.disableClickPropagation(tp3dDemCenterBtn);
L.DomEvent.disableScrollPropagation(tp3dDemCenterBtn);

// Drawn as a wide white casing under a thicker dashed green line, plus a faint
// green tint inside, so the outline stays readable on every base layer --
// including green ones (topo, satellite forests), where a thin green line alone
// disappears.
function drawDemFootprint(footprint, tooltip) {
    L.polygon(footprint, {
        color: '#ffffff', weight: 7, opacity: 0.85,
        fillColor: '#2ecc71', fillOpacity: 0.08, interactive: false
    }).addTo(tp3dDemLayer);
    L.polygon(footprint, {
        color: '#1e9e55', weight: 3, opacity: 1, dashArray: '8 6', fill: false, interactive: false
    }).addTo(tp3dDemLayer).bindTooltip(tooltip, { sticky: true });
}

function tp3dShowDemCoverage(coverage, focus) {
    tp3dDemCoverage = coverage;
    tp3dDemLayer.clearLayers();
    if (coverage && coverage.tiles) {
        coverage.tiles.forEach(function (tile) {
            drawDemFootprint(tile.footprint, 'DEM tile: ' + tile.name);
        });
    } else if (coverage && coverage.footprint) {
        drawDemFootprint(coverage.footprint, 'DEM coverage: ' + coverage.name);
    }
    var drawn = tp3dDemLayer.getLayers().length > 0;

    // Only shown when a Local DEM File is actually active -- the legend stays
    // uncluttered for the vast majority of sessions that never touch it.
    var legend = document.getElementById('legend');
    if (legend && drawn && !tp3dDemLegendRow) {
        tp3dDemLegendRow = document.createElement('div');
        tp3dDemLegendRow.className = 'legend-row';
        tp3dDemLegendRow.innerHTML = '<span class="legend-swatch line" style="border-color:#1e9e55;"></span>DEM coverage';
        legend.appendChild(tp3dDemLegendRow);
    }
    if (tp3dDemLegendRow) tp3dDemLegendRow.style.display = drawn ? '' : 'none';
    tp3dDemCenterBtn.style.display = drawn ? '' : 'none';

    // A newly chosen file may be nowhere near the current view.
    if (drawn && focus && !map.getBounds().intersects(tp3dDemLayer.getBounds())) {
        map.fitBounds(tp3dDemLayer.getBounds(), { padding: [40, 40] });
    }
    document.dispatchEvent(new Event('tp3d-dem-coverage'));
}

// DEM_BOUNDS is only what was selected when the page was served. The path can
// change later from the Settings popup or Blender's sidebar, and the elevation API
// too, so this keeps watching the cheap /dem_path_status and only fetches the
// (possibly large) /dem_coverage when something actually changed.
function tp3dCheckDemCoverage() {
    return fetch('/dem_path_status', { cache: 'no-store' })
        .then(function (r) { return r.json(); })
        .then(function (s) {
            var key = [s.api, s.path, s.revision].join('|');
            if (key === tp3dDemStatusKey) return;
            return fetch('/dem_coverage', { cache: 'no-store' })
                .then(function (r) { return r.json(); })
                .then(function (coverage) {
                    tp3dShowDemCoverage(coverage, tp3dDemStatusKey !== null);
                    tp3dDemStatusKey = key;
                });
        })
        .catch(function () {});
}

if (typeof DEM_BOUNDS !== 'undefined') tp3dShowDemCoverage(DEM_BOUNDS, false);
(function tp3dWatchDemCoverage() {
    tp3dCheckDemCoverage().then(function () { setTimeout(tp3dWatchDemCoverage, 1500); });
})();
