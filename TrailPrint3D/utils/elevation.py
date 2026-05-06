import bpy  # type: ignore
import math
import os
import json
import time
import zlib
import struct
import zipfile
import io
import requests  # type: ignore
from datetime import date, datetime
from .. import constants as const
from .. import progress as _progress


def load_counter():
    if os.path.exists(const.counter_file):
        try:
            with open(const.counter_file, "r") as f:
                data = json.load(f)
                return data.get("count_openTopodata", 0), data.get("date_openTopoData", ""), data.get("count_openElevation",0), data.get("date_openElevation","")
        except:
            return 0, "", 0, ""
    return 0, "", 0, ""

def save_counter(count_openTopodata, date_openTopoData, count_openElevation, date_openElevation):
    with open(const.counter_file, "w") as f:
        json.dump({"count_openTopodata": count_openTopodata, "date_openTopoData": date_openTopoData, "count_openElevation": count_openElevation, "date_openElevation": date_openElevation}, f)

def update_request_counter():

    api = bpy.context.scene.tp3d.api

    today_date = date.today().isoformat()
    today_month = date.today().month
    count_openTopodata, date_openTopoData, count_openElevation, date_openElevation = load_counter()

    # Reset counter if the date has changed
    if date_openTopoData != today_date:
        count_openTopodata = 0

    if date_openElevation != today_month:
        count_openElevation = 0

    if api == "OPENTOPODATA":
        count_openTopodata += 1
    elif api == "OPEN-ELEVATION":
        count_openElevation += 1

    save_counter(count_openTopodata, today_date, count_openElevation,today_month)

    return count_openTopodata, count_openElevation

def send_api_request(addition = ""):

    dataset = bpy.context.scene.tp3d.dataset
    api = bpy.context.scene.tp3d.api

    request_count = update_request_counter()
    now = datetime.now()
    if api == "OPENTOPODATA":
        print(f"{now.hour:02d}:{now.minute:02d} | Fetching: {addition} | API Usage: {request_count} | {dataset}")
    elif api == "OPEN-ELEVATION":
        print(f"{now.hour:02d}:{now.minute:02d} | Fetching: {addition} | API Usage: {request_count}")
    elif api == "TERRAIN-TILES":
        print(f"{now.hour:02d}:{now.minute:02d} | Fetching API")


def load_elevation_cache():
    """Load the elevation cache from disk"""

    if os.path.exists(const.elevation_cache_file):
        try:
            with open(const.elevation_cache_file, "r") as f:
                const._elevation_cache = json.load(f)
        except Exception as e:
            print(f"Error loading elevation cache: {str(e)}")
            const._elevation_cache = {}
    else:
        const._elevation_cache = {}

def save_elevation_cache():
    """Save the elevation cache from Opentopodata or OpenElevation to disk"""

    cacheSize = bpy.context.scene.tp3d.ccacheSize

    if len(const._elevation_cache) > cacheSize:
        # Keep only the most recent entries
        keys = list(const._elevation_cache.keys())
        for key in keys[:-cacheSize]:
            del const._elevation_cache[key]

    try:
        with open(const.elevation_cache_file, "w") as f:
            json.dump(const._elevation_cache, f)
    except Exception as e:
        print(f"Error saving elevation cache: {str(e)}")

def get_cached_elevation(lat, lon, api_type="opentopodata"):
    """Get elevation from cache if available"""
    key = f"{lat:.5f}_{lon:.5f}_{api_type}"
    return const._elevation_cache.get(key)

def cache_elevation(lat, lon, elevation, api_type="opentopodata"):
    """Cache elevation data"""
    key = f"{lat:.5f}_{lon:.5f}_{api_type}"
    const._elevation_cache[key] = elevation

# Get real elevation for a point
def get_elevation_single(lat, lon):
    """Fetches real elevation for a single latitude and longitude using OpenTopoData."""

    dataset = bpy.context.scene.tp3d.dataset

    url = f"https://api.opentopodata.org/v1/{dataset}?locations={lat},{lon}"
    response = requests.get(url).json()
    elevation = response['results'][0]['elevation'] if 'results' in response else 0
    return elevation

def get_elevation_openTopoData(coords, lenv = 0, pointsDone = 0, progress_cb=None):
    """Fetches real elevation for each vertex using OpenTopoData with request batching."""

    disableCache = bpy.context.scene.tp3d.disableCache
    opentopoAdress = bpy.context.scene.tp3d.opentopoAdress
    dataset = bpy.context.scene.tp3d.dataset

    # Ensure the cache is loaded
    if not const._elevation_cache:
        load_elevation_cache()

    # First, check which coordinates need fetching (not in cache)
    coords_to_fetch = []
    coords_indices = []

    elevations = [0] * len(coords)  # Pre-allocate list

    #check if coordinates are in cache or not
    for i, (lat, lon) in enumerate(coords):
        cached_elevation = get_cached_elevation(lat, lon)
        if cached_elevation is not None and disableCache == 0:
            # Use cached elevation
            elevations[i] = cached_elevation
        else:
            # Need to fetch this coordinate
            elevations[i] = -5
            coords_to_fetch.append((lat, lon))
            coords_indices.append(i)

    if len(coords) - len(coords_to_fetch) > 0:
        print(f"Using: {len(coords) - len(coords_to_fetch)} cached Coordinates")

    # If all elevations were found in cache, return immediately
    if not coords_to_fetch:
        if progress_cb:
            progress_cb(100)
        return elevations

    batch_size = 10
    progress_intervals = set(range(5, 101, 5))
    total_to_fetch = len(coords_to_fetch)
    for i in range(0, total_to_fetch, batch_size):
        batch = coords_to_fetch[i:i + batch_size]
        query = "|".join([f"{c[0]},{c[1]}" for c in batch])
        url = f"{opentopoAdress}{dataset}?locations={query}"
        last_request_time = time.monotonic()
        response = requests.get(url)
        nr = i + len(batch) + pointsDone
        addition = f" {nr}/{int(lenv)}"
        send_api_request(addition)
        pct = int(min((i + len(batch)) / total_to_fetch * 100, 100))
        for threshold in sorted(t for t in progress_intervals if pct >= t):
            if progress_cb:
                progress_cb(threshold)
            progress_intervals.discard(threshold)
        response.raise_for_status()


        data = response.json()
        # Handle the elevation data and replace 'null' with 0
        for o, result in enumerate(data['results']):
            elevation = result.get('elevation', None)  # Safe get, default to None if key is missing
            if elevation is None:
                elevation = 0  # Replace None (null in JSON) with 0
            cache_elevation(batch[o][0], batch[o][1], elevation)
            ind = coords_indices[i+o]
            elevations[ind] = elevation

        # Get current time
        now = time.monotonic()
        elapsed_time = now - last_request_time
        if i + batch_size < len(coords_to_fetch) and elapsed_time < 1.3:
            time.sleep(1.3 - elapsed_time)  # Pause to prevent request throttling

    return elevations

def get_elevation_openElevation(coords, lenv = 0, pointsDone = 0, progress_cb=None):
    """Fetches real elevation for each vertex using Open-Elevation with request batching."""

    elevations = []
    batch_size = 1000
    total = len(coords)
    progress_intervals = set(range(5, 101, 5))
    for i in range(0, total, batch_size):
        batch = coords[i:i + batch_size]
        # Open-Elevation expects a POST request with JSON body
        payload = {"locations": [{"latitude": c[0], "longitude": c[1]} for c in batch]}
        url = "https://api.open-elevation.com/api/v1/lookup"
        last_request_time = time.monotonic()

        headers = {'Content-Type': 'application/json'}
        nr = i + len(batch) + pointsDone
        addition = f" {nr}/{int(lenv)}"
        send_api_request(addition)
        pct = int(min((i + len(batch)) / total * 100, 100))
        for threshold in sorted(t for t in progress_intervals if pct >= t):
            if progress_cb:
                progress_cb(threshold)
            progress_intervals.discard(threshold)

        response = requests.post(url, json=payload, headers=headers)

        response.raise_for_status()

        data = response.json()

        # Handle the elevation data and replace 'null' with 0
        for result in data['results']:
            elevation = result.get('elevation', None)
            if elevation is None:
                elevation = 0
            elevations.append(elevation)

        # Get current time for request rate limiting
        now = time.monotonic()
        elapsed_time = now - last_request_time
        if elapsed_time < 2:
            time.sleep(2 - elapsed_time)  # Pause to prevent request throttling

    return elevations

def lonlat_to_tilexy(lon, lat, zoom):
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    xtile = int((lon + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return xtile, ytile

def lonlat_to_pixelxy(lon, lat, zoom):
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    x = (lon + 180.0) / 360.0 * n * 256
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n * 256
    return int(x % 256), int(y % 256)

def fetch_terrarium_tile_raw(zoom, xtile, ytile):
    """Fetch the raw PNG binary data for a tile, either from cache or online."""
    tile_path = os.path.join(const.terrarium_cache_dir, f"{zoom}_{xtile}_{ytile}.png")
    if not os.path.exists(tile_path):
        url = f"https://elevation-tiles-prod.s3.amazonaws.com/terrarium/{zoom}/{xtile}/{ytile}.png"
        response = requests.get(url)
        response.raise_for_status()
        with open(tile_path, "wb") as f:
            f.write(response.content)
    with open(tile_path, "rb") as f:
        return f.read()

def paeth_predictor(a, b, c):
    # PNG Paeth filter
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    elif pb <= pc:
        return b
    else:
        return c

def parse_png_rgb_data(png_bytes):
    """Extract uncompressed RGB bytes from a PNG image (supports all PNG filter types)."""
    assert png_bytes[:8] == b'\x89PNG\r\n\x1a\n', "Not a valid PNG file"
    offset = 8
    width = height = None
    idat_data = b''

    while offset < len(png_bytes):
        length = struct.unpack(">I", png_bytes[offset:offset+4])[0]
        chunk_type = png_bytes[offset+4:offset+8]
        data = png_bytes[offset+8:offset+8+length]
        offset += 12 + length

        if chunk_type == b'IHDR':
            width, height, bit_depth, color_type, _, _, _ = struct.unpack(">IIBBBBB", data)
            assert bit_depth == 8 and color_type == 2, "Only 8-bit RGB PNGs supported"
        elif chunk_type == b'IDAT':
            idat_data += data
        elif chunk_type == b'IEND':
            break

    raw = zlib.decompress(idat_data)
    stride = 3 * width
    rgb_array = []
    prev_row = bytearray(stride)

    for y in range(height):
        i = y * (stride + 1)
        filter_type = raw[i]
        scanline = bytearray(raw[i + 1:i + 1 + stride])
        recon = bytearray(stride)

        if filter_type == 0:
            recon[:] = scanline
        elif filter_type == 1:  # Sub
            for i in range(stride):
                val = scanline[i]
                left = recon[i - 3] if i >= 3 else 0
                recon[i] = (val + left) % 256
        elif filter_type == 2:  # Up
            for i in range(stride):
                recon[i] = (scanline[i] + prev_row[i]) % 256
        elif filter_type == 3:  # Average
            for i in range(stride):
                left = recon[i - 3] if i >= 3 else 0
                up = prev_row[i]
                recon[i] = (scanline[i] + (left + up) // 2) % 256
        elif filter_type == 4:  # Paeth
            for i in range(stride):
                a = recon[i - 3] if i >= 3 else 0
                b = prev_row[i]
                c = prev_row[i - 3] if i >= 3 else 0
                recon[i] = (scanline[i] + paeth_predictor(a, b, c)) % 256
        else:
            raise ValueError(f"Unsupported filter type {filter_type}")

        # Convert scanline to list of (R, G, B) tuples
        row = [(recon[i], recon[i+1], recon[i+2]) for i in range(0, stride, 3)]
        rgb_array.append(row)
        prev_row = recon

    return rgb_array


def terrarium_pixel_to_elevation(r, g, b):
    """Convert Terrarium RGB pixel to elevation in meters."""
    return (r * 256 + g + b / 256) - 32768

def get_elevation_TerrainTiles(coords, lenv=0, pointsDone=0, zoom=10, progress_cb=None):

    num_subdivisions = bpy.context.scene.tp3d.num_subdivisions
    minLat = bpy.context.scene.tp3d.minLat
    minLon = bpy.context.scene.tp3d.minLon
    maxLat = bpy.context.scene.tp3d.maxLat
    maxLon = bpy.context.scene.tp3d.maxLon

    from .geo import haversine  # deferred to avoid circular import at load time

    realdist1 = haversine(minLat,minLon,minLat,maxLon)*1000
    realdist2 = haversine(maxLat,minLon,maxLat,maxLon)*1000
    #calculating zoom
    zoom = 10
    horVerts = 1 + 2**(num_subdivisions+1)
    strt = 156543 #m/Pixel on Tile PNG
    cntr = 2

    vertdist = max(realdist1,realdist2)/horVerts #Distance between 2 vertices
    while strt > vertdist:
        cntr += 1
        strt /= 2
    #Max zoom level to 14
    cntr = min(cntr,15)

    print(f"Zoom Level for API: {cntr}, Start fetching Data...")
    zoom = cntr

    tile_dict = {}
    for idx, (lat, lon) in enumerate(coords):
        xtile, ytile = lonlat_to_tilexy(lon, lat, zoom)
        tile_dict.setdefault((xtile, ytile), []).append((idx, lat, lon))

    total_tiles = len(tile_dict)
    progress_intervals = set(range(10,101,10))
    elevations = [0] * len(coords)
    for i, ((xtile, ytile), idx_lat_lon_list) in enumerate(tile_dict.items(), 1):
        percent_complete = int((i/ total_tiles) * 100)
        if percent_complete in progress_intervals:
            print(f"{datetime.now().strftime('%H:%M:%S')} - Elevation {percent_complete}% complete, {i}")
            progress_intervals.remove(percent_complete)
            if progress_cb:
                progress_cb(percent_complete)
        try:
            png_bytes = fetch_terrarium_tile_raw(zoom, xtile, ytile)
            rgb_array = parse_png_rgb_data(png_bytes)
        except Exception as e:
            print(f"Failed to fetch or parse tile {zoom}/{xtile}/{ytile}: {e}")
            for idx, _, _ in idx_lat_lon_list:
                elevations[idx] = 0
            continue

        for idx, lat, lon in idx_lat_lon_list:
            px, py = lonlat_to_pixelxy(lon, lat, zoom)
            px = min(max(px, 0), 255)
            py = min(max(py, 0), 255)
            r, g, b = rgb_array[py][px]
            temp_ele = terrarium_pixel_to_elevation(r, g, b)
            if temp_ele < -50:
                temp_ele = -1
                buggyDataset = 1
                bpy.context.scene.tp3d.buggyDataset = buggyDataset
            elevations[idx] = temp_ele

    return elevations


def get_elevation_openTopography(coords, lenv=0, pointsDone=0, progress_cb=None):
    """Fetch elevation using the OpenTopography Global DEM API.

    Downloads an ASCII Grid DEM for the coordinate bounding box in a single
    request, then samples the grid at each coordinate.  Supports all
    OpenTopography Global DEM datasets; SRTMGL3 and SRTM15Plus are free and
    work without an API key.
    """
    if not coords:
        return []

    tp3d    = bpy.context.scene.tp3d
    from ..addon_preferences import get_prefs
    api_key = get_prefs().openTopographyApiKey
    demtype = tp3d.openTopographyDataset

    lats  = [c[0] for c in coords]
    lons  = [c[1] for c in coords]
    pad   = 0.005          # small padding so border verts don't fall outside the grid
    south = min(lats) - pad
    north = max(lats) + pad
    west  = min(lons) - pad
    east  = max(lons) + pad

    if progress_cb:
        progress_cb(5)

    params = {
        "demtype":      demtype,
        "south":        f"{south:.6f}",
        "north":        f"{north:.6f}",
        "west":         f"{west:.6f}",
        "east":         f"{east:.6f}",
        "outputFormat": "AAIGrid",
    }
    if api_key:
        params["API_Key"] = api_key

    print(f"OpenTopography: requesting {demtype} DEM "
          f"({south:.3f},{west:.3f})→({north:.3f},{east:.3f}, {len(coords)} coordinates)")

    try:
        response = requests.get(
            "https://portal.opentopography.org/API/globaldem",
            params=params,
            timeout=180,
        )
        if response.status_code == 401:
            _progress.WarningsOverlay.add_warning(
                "OpenTopography: invalid or missing API key (401). "
                "Get a free key at portal.opentopography.org", "error")
            return [0.0] * len(coords)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        _progress.WarningsOverlay.add_warning(
            f"OpenTopography: request failed — {e}", "error")
        return [0.0] * len(coords)

    if progress_cb:
        progress_cb(50)

    # The API returns either a plain-text .asc file or (rarely) a ZIP archive.
    content = response.content
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            asc_name = next((n for n in zf.namelist() if n.lower().endswith('.asc')), None)
            if asc_name is None:
                _progress.WarningsOverlay.add_warning(
                    "OpenTopography: no .asc file found in ZIP response", "error")
                return [0.0] * len(coords)
            asc_data = zf.read(asc_name).decode('utf-8')
    except zipfile.BadZipFile:
        # Plain-text .asc response
        try:
            asc_data = content.decode('utf-8')
        except Exception as e:
            _progress.WarningsOverlay.add_warning(
                f"OpenTopography: could not decode response — {e}", "error")
            return [0.0] * len(coords)
        # Sanity-check: if it looks like an error page rather than a grid, bail out
        if 'ncols' not in asc_data[:500].lower():
            print(f"OpenTopography unexpected response: {asc_data[:300]}")
            _progress.WarningsOverlay.add_warning(
                "OpenTopography: unexpected response format (check API key / bbox)", "error")
            return [0.0] * len(coords)

    # --- Parse ASCII Grid header ---
    lines      = asc_data.strip().splitlines()
    header     = {}
    data_start = 0
    for i, line in enumerate(lines):
        parts = line.strip().split()
        if len(parts) >= 2 and not parts[0][0].lstrip('-').replace('.', '').isdigit():
            header[parts[0].lower()] = float(parts[1])
            data_start = i + 1
        else:
            data_start = i
            break

    ncols    = int(header.get('ncols', 0))
    nrows    = int(header.get('nrows', 0))
    xll      = header.get('xllcenter', header.get('xllcorner', west))
    yll      = header.get('yllcenter', header.get('yllcorner', south))
    cellsize = header.get('cellsize', 1.0)
    nodata   = header.get('nodata_value', header.get('nodata', -9999.0))

    if ncols == 0 or nrows == 0 or cellsize == 0:
        print("OpenTopography: empty or invalid DEM grid — returning zeros")
        return [0.0] * len(coords)

    # --- Load grid rows (row 0 = northernmost row) ---
    grid_rows = []
    for line in lines[data_start:]:
        vals = line.strip().split()
        if vals:
            grid_rows.append([float(v) for v in vals])

    if progress_cb:
        progress_cb(80)

    def _sample(lat, lon):
        col_i = int(round((lon - xll) / cellsize))
        row_i = int(round((nrows - 1) - (lat - yll) / cellsize))
        col_i = max(0, min(col_i, ncols - 1))
        row_i = max(0, min(row_i, nrows - 1))
        if row_i < len(grid_rows) and col_i < len(grid_rows[row_i]):
            val = grid_rows[row_i][col_i]
            return 0.0 if val == nodata else float(val)
        return 0.0

    elevations = [_sample(lat, lon) for lat, lon in coords]

    if progress_cb:
        progress_cb(100)

    print(f"OpenTopography: sampled {len(elevations)} elevations "
          f"from {nrows}×{ncols} grid (cellsize={cellsize}°)")
    return elevations


def get_elevation_path_openElevation(vertices):
    """Fetches real elevation for each vertex using OpenTopoData with request batching."""
    coords = [(v[0], v[1], v[2], v[3]) for v in vertices]
    elevations = []
    batch_size = 1000
    for i in range(0, len(coords), batch_size):
        batch = coords[i:i + batch_size]
        # Open-Elevation expects a POST request with JSON body
        payload = {"locations": [{"latitude": c[0], "longitude": c[1]} for c in batch]}
        url = "https://api.open-elevation.com/api/v1/lookup"
        last_request_time = time.monotonic()

        headers = {'Content-Type': 'application/json'}

        addition = f"(overwrite path) {i + len(batch)}/{len(coords)}"
        send_api_request(addition)

        response = requests.post(url, json=payload, headers=headers)

        response.raise_for_status()

        data = response.json()

        elevations.extend([r['elevation'] for r in data['results']])
        now = time.monotonic()
        elapsed_time = now - last_request_time
        if i + batch_size < len(coords) and elapsed_time < 1.4:
            time.sleep(1.4 - elapsed_time)  # Pause to prevent request throttling

    for i in range(len(vertices)):
        coords[i] =  (coords[i][0], coords[i][1], elevations[i], coords[i][3])

    return coords

def get_elevation_path_openTopoData(vertices):

    opentopoAdress = bpy.context.scene.tp3d.opentopoAdress
    dataset = bpy.context.scene.tp3d.dataset

    print("Getting elevation")
    """Fetches real elevation for each vertex using OpenTopoData with request batching."""
    coords = [(v[0], v[1], v[2], v[3]) for v in vertices]
    elevations = []
    batch_size = 100
    for i in range(0, len(coords), batch_size):
        batch = coords[i:i + batch_size]
        query = "|".join([f"{c[0]},{c[1]}" for c in batch])
        url = f"{opentopoAdress}{dataset}?locations={query}"
        last_request_time = time.monotonic()
        response = requests.get(url).json()
        addition = f"(overwrite path) {i + len(batch)}/{len(coords)}"
        send_api_request(addition)

        elevations.extend([r.get('elevation') or 0 for r in response['results']])

        now = time.monotonic()
        elapsed_time = now - last_request_time
        if i + batch_size < len(coords) and elapsed_time < 1.4:
            time.sleep(1.4 - elapsed_time)  # Pause to prevent request throttling

    for i in range(len(vertices)):
        coords[i] =  (coords[i][0], coords[i][1], elevations[i], coords[i][3])

    return coords


def get_tile_elevation(obj, progress_cb=None):

    mesh = obj.data
    api = bpy.context.scene.tp3d.api

    from .geo import convert_to_geo, haversine  # deferred to avoid circular import at load time

    # Set chunk size based on API
    if api == "OPENTOPODATA" or api == "OPEN-ELEVATION":
        chunk_size = 100000
    elif api == "TERRAIN-TILES" or api == "OPENTOPOGRAPHY":
        chunk_size = 50000000   # single request for all verts
    else:
        chunk_size = 100000  # fallback

    vertices = list(mesh.vertices)
    obj_matrix = obj.matrix_world

    # Convert all vertex positions to world space
    world_verts = [obj_matrix @ v.co for v in vertices]

    # Get min/max bounds in world space
    min_x = min(v.x for v in world_verts)
    max_x = max(v.x for v in world_verts)
    min_y = min(v.y for v in world_verts)
    max_y = max(v.y for v in world_verts)

    minl = convert_to_geo(min_x, min_y)
    maxl = convert_to_geo(max_x, max_y)

    minLat = minl[0]
    maxLat = maxl[0]
    minLon = minl[1]
    maxLon = maxl[1]


    realdist1 = haversine(minLat,minLon,maxLat,maxLon)*1
    realdist2 = haversine(minLat,minLon,maxLat,maxLon)*1

    bpy.context.scene.tp3d["sMapInKm"] = max(realdist1,realdist2)
    bpy.context.scene.tp3d["minLat"] = minLat
    bpy.context.scene.tp3d.maxLat = maxLat
    bpy.context.scene.tp3d.minLon = minLon
    bpy.context.scene.tp3d.maxLon = maxLon


    elevations = []
    for i in range(0, len(world_verts), chunk_size):
        chunk = world_verts[i:i + chunk_size]

        coords = [convert_to_geo(v.x, v.y) for v in chunk]
        if api == "OPENTOPODATA":
            chunk_elevations = get_elevation_openTopoData(coords, len(vertices), i, progress_cb=progress_cb)
        elif api == "OPEN-ELEVATION":
            chunk_elevations = get_elevation_openElevation(coords, len(vertices), i, progress_cb=progress_cb)
        elif api == "TERRAIN-TILES":
            chunk_elevations = get_elevation_TerrainTiles(coords, len(vertices), i, progress_cb=progress_cb)
        elif api == "OPENTOPOGRAPHY":
            chunk_elevations = get_elevation_openTopography(coords, len(vertices), i, progress_cb=progress_cb)
        else:
            chunk_elevations = [0.0] * len(chunk)  # fallback

        elevations.extend(chunk_elevations)

        # Free memory after processing chunk
        del chunk_elevations

    save_elevation_cache()

    lowestElevation = min(elevations)
    highestElevation = max(elevations)
    additionalExtrusion = lowestElevation
    diff = highestElevation - lowestElevation

    bpy.context.scene.tp3d["o_verticesMap"] = str(len(mesh.vertices))
    bpy.context.scene.tp3d.lowestElevation = lowestElevation
    bpy.context.scene.tp3d.highestElevation = highestElevation
    bpy.context.scene.tp3d.sAdditionalExtrusion = additionalExtrusion

    return elevations, diff
