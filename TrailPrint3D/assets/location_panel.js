// Shared "go to location" panel -- reused by every 2D-map picker page via
// __LOCATION_PANEL_JS__ in picker_server.py. Collapsed to a single
// pin-icon button by default; expands to a small panel offering either a
// city-name lookup (via Nominatim) or direct lat/lon entry. Requires `map`
// to already be defined by the including page (see map_init.js).
var searchMarker = null;

document.getElementById('coordSearchToggle').addEventListener('click', function() {
    var body = document.getElementById('coordSearchBody');
    var open = body.classList.toggle('open');
    this.classList.toggle('active', open);
});

function flyToMarker(lat, lon, zoom) {
    if (searchMarker) map.removeLayer(searchMarker);
    searchMarker = L.marker([lat, lon]).addTo(map);
    map.flyTo([lat, lon], zoom);
}

function goToCoords() {
    var latInput = document.getElementById('latInput');
    var lonInput = document.getElementById('lonInput');
    var lat = parseFloat(latInput.value);
    var lon = parseFloat(lonInput.value);
    var latOk = !isNaN(lat) && lat >= -90 && lat <= 90;
    var lonOk = !isNaN(lon) && lon >= -180 && lon <= 180;
    latInput.style.borderColor = latOk ? '' : '#cc4444';
    lonInput.style.borderColor = lonOk ? '' : '#cc4444';
    if (!latOk || !lonOk) return;
    flyToMarker(lat, lon, 15);
}

document.getElementById('coordSearchBtn').addEventListener('click', goToCoords);
['latInput', 'lonInput'].forEach(function(id) {
    var el = document.getElementById(id);
    el.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') { e.preventDefault(); goToCoords(); }
    });
    el.addEventListener('input', function() { this.style.borderColor = ''; });
});

// City lookup uses OpenStreetMap's Nominatim geocoder -- free, no
// API key, CORS-enabled for browser fetches. Only fires on explicit
// user action (button/Enter), never as-you-type, to stay well
// within its usage policy.
function searchCity() {
    var input = document.getElementById('citySearchInput');
    var btn = document.getElementById('citySearchBtn');
    var q = input.value.trim();
    if (!q) return;
    btn.disabled = true;
    fetch('https://nominatim.openstreetmap.org/search?format=json&limit=1&q=' + encodeURIComponent(q))
        .then(function(r) { return r.json(); })
        .then(function(results) {
            btn.disabled = false;
            if (!results || !results.length) {
                input.style.borderColor = '#cc4444';
                return;
            }
            input.style.borderColor = '';
            flyToMarker(parseFloat(results[0].lat), parseFloat(results[0].lon), 12);
        })
        .catch(function() {
            btn.disabled = false;
            input.style.borderColor = '#cc4444';
        });
}

document.getElementById('citySearchBtn').addEventListener('click', searchCity);
document.getElementById('citySearchInput').addEventListener('keydown', function(e) {
    if (e.key === 'Enter') { e.preventDefault(); searchCity(); }
});
document.getElementById('citySearchInput').addEventListener('input', function() {
    this.style.borderColor = '';
});
