// Shared "go to location" panel -- reused by every 2D-map picker page via
// __LOCATION_PANEL_JS__ in picker_server.py. Collapsed to a single
// pin-icon button by default; expands to a small panel offering either a
// city-name lookup (via Nominatim) or direct lat/lon entry. Requires `map`
// to already be defined by the including page (see map_init.js).

document.getElementById('coordSearchToggle').addEventListener('click', function() {
    var body = document.getElementById('coordSearchBody');
    var open = body.classList.toggle('open');
    this.classList.toggle('active', open);
    this.setAttribute('aria-expanded', String(open));
    this.setAttribute('aria-controls', 'coordSearchBody');
    if (open) document.getElementById('citySearchInput').focus();
});
document.getElementById('coordSearchToggle').setAttribute('aria-expanded', 'false');
document.getElementById('coordSearchBody').addEventListener('keydown', function(event) {
    if (event.key !== 'Escape') return;
    document.getElementById('coordSearchBody').classList.remove('open');
    var toggle = document.getElementById('coordSearchToggle');
    toggle.classList.remove('active');
    toggle.setAttribute('aria-expanded', 'false');
    toggle.focus();
});

function tp3dLocationError(message, inputs) {
    var error = document.getElementById('coordSearchError');
    if (!error) {
        error = document.createElement('p');
        error.id = 'coordSearchError';
        error.className = 'field-error';
        error.setAttribute('role', 'status');
        document.getElementById('coordSearchBody').appendChild(error);
    }
    error.textContent = message;
    error.hidden = !message;
    inputs.forEach(function(input) {
        input.setAttribute('aria-invalid', String(!!message));
        var ids = (input.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean);
        if (ids.indexOf(error.id) === -1) ids.push(error.id);
        input.setAttribute('aria-describedby', ids.join(' '));
    });
}

// Pin marking the searched spot; clicking it fades it out over
// SEARCH_MARKER_FADE_MS and then removes it. A new search replaces it.
var SEARCH_MARKER_FADE_MS = 400;
var searchMarker = null;

function fadeOutMarker(marker) {
    var els = [marker.getElement(), marker._shadow].filter(Boolean);
    els.forEach(function(el) {
        el.style.transition = 'opacity ' + SEARCH_MARKER_FADE_MS + 'ms ease';
        el.style.opacity = '0';
        el.style.pointerEvents = 'none';
    });
    setTimeout(function() {
        map.removeLayer(marker);
        if (searchMarker === marker) searchMarker = null;
    }, SEARCH_MARKER_FADE_MS);
}

function flyToLocation(lat, lon, zoom) {
    if (searchMarker) map.removeLayer(searchMarker);
    var marker = L.marker([lat, lon], { title: 'Click to hide' }).addTo(map);
    marker.on('click', function() { fadeOutMarker(marker); });
    searchMarker = marker;
    map.flyTo([lat, lon], zoom);
}

function goToCoords() {
    var latInput = document.getElementById('latInput');
    var lonInput = document.getElementById('lonInput');
    var lat = Number(latInput.value);
    var lon = Number(lonInput.value);
    var latOk = latInput.value.trim() !== '' && Number.isFinite(lat) && lat >= -90 && lat <= 90;
    var lonOk = lonInput.value.trim() !== '' && Number.isFinite(lon) && lon >= -180 && lon <= 180;
    tp3dLocationError('', [latInput, lonInput]);
    if (!latOk || !lonOk) {
        tp3dLocationError('Enter a latitude from -90 to 90 and a longitude from -180 to 180.',
            [latOk ? null : latInput, lonOk ? null : lonInput].filter(Boolean));
        return;
    }
    flyToLocation(lat, lon, 15);
}

document.getElementById('coordSearchBtn').addEventListener('click', goToCoords);
['latInput', 'lonInput'].forEach(function(id) {
    var el = document.getElementById(id);
    el.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') { e.preventDefault(); goToCoords(); }
    });
    el.addEventListener('input', function() {
        tp3dLocationError('', [document.getElementById('latInput'), document.getElementById('lonInput')]);
    });
});

// City lookup uses OpenStreetMap's Nominatim geocoder -- free, no
// API key, CORS-enabled for browser fetches. Only fires on explicit
// user action (button/Enter), never as-you-type, and never while a
// previous lookup is still in flight (the button stays disabled), to stay
// within its 1-request-per-second usage policy. Nominatim identifies
// browser apps by their Referer (a page can't set its own User-Agent), so
// the referrer policy is pinned here the same way map_init.js pins it for
// tiles. A non-OK response (e.g. 403/429 if we ever get blocked or rate-
// limited) is treated as a failed lookup rather than parsed as results.
function searchCity() {
    var input = document.getElementById('citySearchInput');
    var btn = document.getElementById('citySearchBtn');
    var q = input.value.trim();
    if (btn.disabled) return;
    if (!q) {
        tp3dLocationError('Enter a city or place name.', [input]);
        return;
    }
    tp3dLocationError('Searching for a location...', []);
    btn.disabled = true;
    btn.setAttribute('aria-busy', 'true');
    fetch('https://nominatim.openstreetmap.org/search?format=json&limit=1&q=' + encodeURIComponent(q), {
        referrerPolicy: 'strict-origin-when-cross-origin'
    })
        .then(function(r) {
            if (!r.ok) throw new Error('Nominatim HTTP ' + r.status);
            return r.json();
        })
        .then(function(results) {
            if (!results || !results.length) {
                tp3dLocationError('No matching location found. Try a more specific name.', [input]);
                return;
            }
            tp3dLocationError('', [input]);
            flyToLocation(parseFloat(results[0].lat), parseFloat(results[0].lon), 12);
        })
        .catch(function(error) {
            console.error('[TP3D location] Search failed:', error);
            tp3dLocationError('Location search failed. Check your connection and try again.', [input]);
        })
        .finally(function() {
            btn.disabled = false;
            btn.removeAttribute('aria-busy');
        });
}

document.getElementById('citySearchBtn').addEventListener('click', searchCity);
document.getElementById('citySearchInput').addEventListener('keydown', function(e) {
    if (e.key === 'Enter') { e.preventDefault(); searchCity(); }
});
document.getElementById('citySearchInput').addEventListener('input', function() {
    tp3dLocationError('', [this]);
});
