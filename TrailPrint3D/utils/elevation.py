import io
import json
import math
import os
import struct
import threading
import time
import zipfile
import zlib
from array import array
from datetime import datetime, timezone

import bpy  # type: ignore
import numpy as np  # type: ignore
import requests  # type: ignore
from bpy.app.translations import pgettext_iface as _
from bpy.app.translations import pgettext_rpt as _rpt

from .. import constants as const
from .. import progress as _progress
from ..utils.dataclasses import GenerationContext


_run_elevation_cache = None


def begin_run_cache():
    """Start an in-memory elevation cache scoped to one generation batch."""
    global _run_elevation_cache
    _run_elevation_cache = {}


def end_run_cache():
    """Discard elevation results retained for the current generation batch."""
    global _run_elevation_cache
    _run_elevation_cache = None


def load_counter():
    if os.path.exists(const.counter_file):
        try:
            with open(const.counter_file, "r") as f:
                data = json.load(f)
                return data.get("count_openTopodata", 0), data.get("date_openTopoData", "")
        except (OSError, json.JSONDecodeError, ValueError, KeyError):
            return 0, ""
    return 0, ""

def save_counter(count_openTopodata, date_openTopoData):
    with open(const.counter_file, "w") as f:
        json.dump({"count_openTopodata": count_openTopodata, "date_openTopoData": date_openTopoData}, f)

def load_generation_counter():
    if os.path.exists(const.generation_counter_file):
        try:
            with open(const.generation_counter_file, "r") as f:
                data = json.load(f)
                return data.get("total_maps_generated", 0)
        except (OSError, json.JSONDecodeError, ValueError, KeyError):
            return 0
    return 0

def save_generation_counter(count):
    with open(const.generation_counter_file, "w") as f:
        json.dump({"total_maps_generated": count}, f)

def update_request_counter():

    api = bpy.context.scene.tp3d.api

    today_date = datetime.now(tz=timezone.utc).date().isoformat()
    count_openTopodata, date_openTopoData = load_counter()

    # Reset counter if the date has changed
    if date_openTopoData != today_date:
        count_openTopodata = 0

    if api == "OPENTOPODATA":
        count_openTopodata += 1

    save_counter(count_openTopodata, today_date)

    return count_openTopodata

def send_api_request(addition = ""):

    dataset = bpy.context.scene.tp3d.dataset
    api = bpy.context.scene.tp3d.api

    request_count = update_request_counter()
    now = datetime.now(tz=timezone.utc)
    if api == "OPENTOPODATA":
        print(f"{now.hour:02d}:{now.minute:02d} | Fetching: {addition} | API Usage: {request_count} | {dataset}")
    elif api == "TERRAIN-TILES":
        print(f"{now.hour:02d}:{now.minute:02d} | Fetching API")
    elif api == "MAPTERHORN":
        print(f"{now.hour:02d}:{now.minute:02d} | Fetching Mapterhorn")


def load_elevation_cache():
    """Load the elevation cache from disk"""

    if os.path.exists(const.elevation_cache_file):
        try:
            with open(const.elevation_cache_file, "r") as f:
                const._elevation_cache = json.load(f)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            print(f"Error loading elevation cache: {e!s}")
            const._elevation_cache = {}
    else:
        const._elevation_cache = {}

def save_elevation_cache():
    """Save the elevation cache from Opentopodata to disk"""

    cacheSize = bpy.context.scene.tp3d.ccacheSize

    if len(const._elevation_cache) > cacheSize:
        # Keep only the most recent entries
        keys = list(const._elevation_cache.keys())
        for key in keys[:-cacheSize]:
            del const._elevation_cache[key]

    try:
        with open(const.elevation_cache_file, "w") as f:
            json.dump(const._elevation_cache, f)
    except OSError as e:
        print(f"Error saving elevation cache: {e!s}")

def clear_api_cache():
    """Delete every cached API response (elevation, OSM, tiles) -- shared by
    the sidebar's Clear Cache operator and the generator pages' Settings
    popup (utils.ui_state.apply_setting_update's 'clearCache' key)."""
    import shutil

    if os.path.exists(const.cache_dir):
        try:
            shutil.rmtree(const.cache_dir)
            print(f"Deleted cache directory: {const.cache_dir}")
        except OSError as e:
            print(f"Failed to delete cache directory: {e}")

    # Clear the in-memory elevation cache so the load guard doesn't
    # serve stale data after the files have been deleted.
    const._elevation_cache.clear()

    # Recreate the directory tree so save_elevation_cache() doesn't
    # silently fail trying to write into a path that no longer exists.
    const._ensure_dirs()


def get_cached_elevation(lat, lon, api_type="opentopodata"):
    """Get elevation from cache if available"""
    key = f"{lat:.5f}_{lon:.5f}_{api_type}"
    return const._elevation_cache.get(key)

def cache_elevation(lat, lon, elevation, api_type="opentopodata"):
    """Cache elevation data"""
    key = f"{lat:.5f}_{lon:.5f}_{api_type}"
    const._elevation_cache[key] = elevation

class _SegmentProgress:
    """Report per-segment fetch progress as progress_cb(pct, (done, total)).

    A segment is one of the separate requests a fetch gets split into (a map
    tile for Terrain Tiles/Mapterhorn, a point batch for OpenTopoData).
    Calls are throttled so a fast run over already-cached
    tiles doesn't rewrite the progress file hundreds of times; the final
    segment always reports.
    """

    _MIN_INTERVAL = 0.2  # seconds

    def __init__(self, progress_cb, total):
        self.progress_cb = progress_cb
        self.total = total
        self._last = None

    def __call__(self, done):
        if not self.progress_cb or self.total <= 0:
            return
        now = time.monotonic()
        if done < self.total and self._last is not None and now - self._last < self._MIN_INTERVAL:
            return
        self._last = now
        self.progress_cb(int(done / self.total * 100), (done, self.total))


def elevation_sub_label(base, segments=None):
    """Sub-bar label for an elevation progress_cb call, with the segment
    count appended when the fetch is split into more than one request."""
    if segments and segments[1] > 1:
        return f"{base} — segment {segments[0]}/{segments[1]}"
    return base


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

    batch_size = 100
    total_to_fetch = len(coords_to_fetch)
    report = _SegmentProgress(progress_cb, math.ceil(total_to_fetch / batch_size))
    report(0)
    for i in range(0, total_to_fetch, batch_size):
        batch = coords_to_fetch[i:i + batch_size]
        query = "|".join([f"{c[0]},{c[1]}" for c in batch])
        url = f"{opentopoAdress}{dataset}?locations={query}"
        last_request_time = time.monotonic()
        response = requests.get(url)
        nr = i + len(batch) + pointsDone
        addition = f" {nr}/{int(lenv)}"
        send_api_request(addition)
        report(i // batch_size + 1)
        response.raise_for_status()


        data = response.json()
        # Handle the elevation data and replace 'null' with 0
        for o, result in enumerate(data['results']):
            elevation = result.get('elevation', None)  # Safe get, default to None if key is missing
            if elevation is None:
                elevation = 0  # Replace None (null in JSON) with 0
            else:
                # Only cache real values — null responses (stored as 0) would poison the cache
                # and cause silent all-zero terrain on subsequent runs.
                cache_elevation(batch[o][0], batch[o][1], elevation)
            ind = coords_indices[i+o]
            elevations[ind] = elevation

        # Get current time
        now = time.monotonic()
        elapsed_time = now - last_request_time
        if i + batch_size < len(coords_to_fetch) and elapsed_time < 1.3:
            time.sleep(1.3 - elapsed_time)  # Pause to prevent request throttling

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

def _write_tile_atomic(tile_path, content):
    """Write a downloaded tile in one step -- the picker's prefetch thread can
    be downloading while a generation reads the same cache folder, and a
    half-written file would decode as garbage instead of a cache miss."""
    tmp_path = f"{tile_path}.{threading.get_ident()}.tmp"
    with open(tmp_path, "wb") as f:
        f.write(content)
    try:
        os.replace(tmp_path, tile_path)
    except PermissionError:
        # Windows: the same tile, finished by the other thread, is open.
        os.remove(tmp_path)
        if not os.path.exists(tile_path):
            raise


def _terrarium_tile_path(zoom, xtile, ytile):
    return os.path.join(const.terrarium_cache_dir, f"{zoom}_{xtile}_{ytile}.png")


def _download_terrarium_tile(zoom, xtile, ytile, get=requests.get):
    """Worker-thread safe, no bpy access. *get*: requests.get or a Session's."""
    url = f"https://elevation-tiles-prod.s3.amazonaws.com/terrarium/{zoom}/{xtile}/{ytile}.png"
    response = get(url, timeout=20)
    response.raise_for_status()
    _write_tile_atomic(_terrarium_tile_path(zoom, xtile, ytile), response.content)


def fetch_terrarium_tile_raw(zoom, xtile, ytile):
    """Fetch the raw PNG binary data for a tile, either from cache or online."""
    disableCache = bpy.context.scene.tp3d.disableCache
    tile_path = _terrarium_tile_path(zoom, xtile, ytile)
    if not os.path.exists(tile_path) or disableCache:
        _download_terrarium_tile(zoom, xtile, ytile)
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
            width, height, bit_depth, color_type, _unused, _unused2, _unused3 = struct.unpack(">IIBBBBB", data)
            assert bit_depth == 8 and color_type == 2, "Only 8-bit RGB PNGs supported"
        elif chunk_type == b'IDAT':
            idat_data += data
        elif chunk_type == b'IEND':
            break

    raw = zlib.decompress(idat_data)
    stride = 3 * width
    rgb_array = []
    prev_row = bytearray(stride)

    if height is None:
        return
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
            raise ValueError(_("Unsupported filter type %s") % filter_type)

        # Convert scanline to list of (R, G, B) tuples
        row = [(recon[i], recon[i+1], recon[i+2]) for i in range(0, stride, 3)]
        rgb_array.append(row)
        prev_row = recon

    return rgb_array


def terrarium_pixel_to_elevation(r, g, b):
    """Convert Terrarium RGB pixel to elevation in meters."""
    return (r * 256 + g + b / 256) - 32768

def terrain_tile_zoom(bounds, num_subdivisions):
    """Tile zoom for a map's (minLat, maxLat, minLon, maxLon) *bounds*, so one
    tile pixel is about one mesh vertex. Shared by the tile-based APIs and
    the picker prefetch, which has to pick the same cache files."""
    from .geo import parallel_distance  # deferred to avoid circular import at load time

    minLat, maxLat, minLon, maxLon = bounds
    realdist1 = parallel_distance(minLat, minLon, maxLon) * 1000
    realdist2 = parallel_distance(maxLat, minLon, maxLon) * 1000
    horVerts = 1 + 2 ** (num_subdivisions + 1)
    strt = 156543  # m/Pixel on Tile PNG
    cntr = 2
    vertdist = max(realdist1, realdist2) / horVerts  # Distance between 2 vertices
    while strt > vertdist:
        cntr += 1
        strt /= 2
    return min(cntr, 15)


def get_elevation_TerrainTiles(bounds, coords, lenv=0, pointsDone=0, zoom=10, progress_cb=None):

    num_subdivisions = bpy.context.scene.tp3d.num_subdivisions
    zoom = terrain_tile_zoom(bounds, num_subdivisions)

    print(f"Zoom Level for API: {zoom}, Start fetching Data...")

    tile_dict = {}
    for idx, (lat, lon) in enumerate(coords):
        xtile, ytile = lonlat_to_tilexy(lon, lat, zoom)
        tile_dict.setdefault((xtile, ytile), []).append((idx, lat, lon))

    total_tiles = len(tile_dict)
    invalidElevations = 0
    progress_intervals = set(range(10,101,10))
    elevations = [0] * len(coords)
    report = _SegmentProgress(progress_cb, total_tiles)
    for i, ((xtile, ytile), idx_lat_lon_list) in enumerate(tile_dict.items(), 1):
        report(i - 1)
        percent_complete = int((i/ total_tiles) * 100)
        if percent_complete in progress_intervals:
            print(f"{datetime.now(tz=timezone.utc).strftime('%H:%M:%S')} - Elevation {percent_complete}% complete, {i}")
            progress_intervals.remove(percent_complete)
        try:
            png_bytes = fetch_terrarium_tile_raw(zoom, xtile, ytile)
            rgb_array:list[list[tuple[int, int]]] = parse_png_rgb_data(png_bytes)
        except (requests.RequestException, OSError, AssertionError, ValueError, struct.error, zlib.error) as e:
            print(f"Failed to fetch or parse tile {zoom}/{xtile}/{ytile}: {e}")
            for idx, _lat, _long in idx_lat_lon_list:
                elevations[idx] = 0
            continue

        for idx, lat, lon in idx_lat_lon_list:
            px, py = lonlat_to_pixelxy(lon, lat, zoom)
            px = min(max(px, 0), 255)
            py = min(max(py, 0), 255)
            r, g, b = rgb_array[py][px]
            temp_ele = terrarium_pixel_to_elevation(r, g, b)
            elevations[idx] = temp_ele
    report(total_tiles)

    print(f"Finished fetching elevation data. Invalid elevations: {invalidElevations} ({(invalidElevations/len(coords))*100:.2f}%)")
    return elevations


_MAPTERHORN_TILE_SIZE = 512


def _mapterhorn_tile_path(zoom, xtile, ytile):
    return os.path.join(const.terrarium_cache_dir, f"mapterhorn_{zoom}_{xtile}_{ytile}.webp")


def _download_mapterhorn_tile(zoom, xtile, ytile, get=requests.get):
    """Worker-thread safe, no bpy access. *get*: requests.get or a Session's."""
    url = f"https://tiles.mapterhorn.com/{zoom}/{xtile}/{ytile}.webp"
    # A single 512px tile should return almost instantly -- this timeout
    # is just to stop a stalled connection (e.g. right after another
    # tile's request got dropped mid-batch) from hanging the whole
    # elevation fetch forever. requests.exceptions.Timeout is a
    # RequestException subclass, so the existing per-tile except clause
    # in get_elevation_Mapterhorn already catches it the same way it
    # catches a hard connection failure.
    response = get(url, timeout=20)
    response.raise_for_status()
    _write_tile_atomic(_mapterhorn_tile_path(zoom, xtile, ytile), response.content)


def fetch_mapterhorn_tile_path(zoom, xtile, ytile):
    """Download a Mapterhorn WebP tile and return its local cache path."""
    disableCache = bpy.context.scene.tp3d.disableCache
    tile_path = _mapterhorn_tile_path(zoom, xtile, ytile)
    if not os.path.exists(tile_path) or disableCache:
        _download_mapterhorn_tile(zoom, xtile, ytile)
    return tile_path


def parse_webp_rgb_data(webp_path):
    """Load a WebP tile via Blender's image API and return a top-down
    height x width x 3 uint8 array.

    Blender stores pixels bottom-up (y=0 = bottom row), so the rows are
    reversed here to match the XYZ tile convention where y=0 is the top (north).
    colorspace is forced to Non-Color so gamma correction doesn't corrupt the
    raw elevation values.
    """
    img = bpy.data.images.load(webp_path)
    img.colorspace_settings.name = 'Non-Color'
    width, height = img.size
    pixels = np.empty(width * height * 4, dtype=np.float32)  # flat RGBA floats 0-1, bottom row first
    img.pixels.foreach_get(pixels)
    bpy.data.images.remove(img)
    return np.rint(pixels.reshape(height, width, 4)[::-1, :, :3] * 255).astype(np.uint8)


def get_elevation_Mapterhorn(bounds, coords, lenv=0, pointsDone=0, zoom=10, progress_cb=None):
    """Fetch elevation from Mapterhorn terrain tiles (512px WebP, Terrarium encoding)."""
    num_subdivisions = bpy.context.scene.tp3d.num_subdivisions
    zoom = terrain_tile_zoom(bounds, num_subdivisions)

    print(f"Zoom Level for Mapterhorn: {zoom}, Start fetching Data...")

    ts = _MAPTERHORN_TILE_SIZE
    tile_dict = {}
    for idx, (lat, lon) in enumerate(coords):
        xtile, ytile = lonlat_to_tilexy(lon, lat, zoom)
        tile_dict.setdefault((xtile, ytile), []).append((idx, lat, lon))

    total_tiles = len(tile_dict)
    invalidElevations = 0
    progress_intervals = set(range(10, 101, 10))
    elevations = np.zeros(len(coords))
    report = _SegmentProgress(progress_cb, total_tiles)

    for i, ((xtile, ytile), idx_lat_lon_list) in enumerate(tile_dict.items(), 1):
        report(i - 1)
        percent_complete = int((i / total_tiles) * 100)
        if percent_complete in progress_intervals:
            print(f"{datetime.now(tz=timezone.utc).strftime('%H:%M:%S')} - Mapterhorn elevation {percent_complete}% ({i}/{total_tiles})")
            progress_intervals.discard(percent_complete)

        # Mapterhorn only has zoom 13+ tiles for select regions; fall back to zoom 12 (global) on 404.
        tile_path = None
        actual_zoom = zoom
        actual_xtile, actual_ytile = xtile, ytile
        while True:
            try:
                tile_path = fetch_mapterhorn_tile_path(actual_zoom, actual_xtile, actual_ytile)
                break
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404 and actual_zoom > 12:
                    actual_zoom -= 1
                    sample_lat = idx_lat_lon_list[0][1]
                    sample_lon = idx_lat_lon_list[0][2]
                    actual_xtile, actual_ytile = lonlat_to_tilexy(sample_lon, sample_lat, actual_zoom)
                    continue
                print(f"Failed to fetch Mapterhorn tile {actual_zoom}/{actual_xtile}/{actual_ytile}: {e}")
                break
            except (requests.RequestException, OSError) as e:
                print(f"Failed to fetch Mapterhorn tile {actual_zoom}/{actual_xtile}/{actual_ytile}: {e}")
                break

        if tile_path is None:
            invalidElevations += len(idx_lat_lon_list)
            for idx, _lat, _long in idx_lat_lon_list:
                elevations[idx] = 0
            continue

        try:
            rgb_array = parse_webp_rgb_data(tile_path)
        except (OSError, RuntimeError, ValueError, AttributeError) as e:
            print(f"Failed to parse Mapterhorn tile {actual_zoom}/{actual_xtile}/{actual_ytile}: {e}")
            invalidElevations += len(idx_lat_lon_list)
            for idx, _lat, _long in idx_lat_lon_list:
                elevations[idx] = 0
            continue

        points = np.array(idx_lat_lon_list, dtype=np.float64)
        lat_rad = np.radians(points[:, 1])
        n = 2.0 ** actual_zoom
        px = (((points[:, 2] + 180.0) / 360.0 * n * ts) % ts).astype(np.int64)
        py = (((1.0 - np.log(np.tan(lat_rad) + 1.0 / np.cos(lat_rad)) / np.pi) / 2.0 * n * ts) % ts).astype(np.int64)
        rgb = rgb_array[np.clip(py, 0, ts - 1), np.clip(px, 0, ts - 1)].astype(np.float64)
        elevations[points[:, 0].astype(np.int64)] = terrarium_pixel_to_elevation(rgb[:, 0], rgb[:, 1], rgb[:, 2])
    report(total_tiles)

    print(f"Mapterhorn: finished. Invalid elevations: {invalidElevations}")
    return elevations.tolist()


# The elevation APIs a prefetch can download ahead of time: both are plain
# tile downloads, unlike the per-point APIs, whose results depend on the
# generated mesh's own vertices.
PREFETCHABLE_ELEVATION_APIS = ("MAPTERHORN", "TERRAIN-TILES")


def terrain_tiles_for_bounds(bounds, num_subdivisions):
    """(zoom, xtile, ytile) of every tile a map covering *bounds* samples."""
    minLat, maxLat, minLon, maxLon = bounds
    zoom = terrain_tile_zoom(bounds, num_subdivisions)
    x_first, y_first = lonlat_to_tilexy(minLon, maxLat, zoom)  # y grows southward
    x_last, y_last = lonlat_to_tilexy(maxLon, minLat, zoom)
    n = 2 ** zoom
    return [
        (zoom, x % n, y)  # a map past the 180th meridian wraps around the tile grid
        for x in range(x_first, x_last + 1)
        for y in range(y_first, y_last + 1)
    ]


def _prefetch_mapterhorn_tile(zoom, xtile, ytile, get):
    """Same zoom 13+ fallback as get_elevation_Mapterhorn: a missing tile's
    parent is what the generation will sample instead."""
    while not os.path.exists(_mapterhorn_tile_path(zoom, xtile, ytile)):
        try:
            _download_mapterhorn_tile(zoom, xtile, ytile, get=get)
            return
        except requests.HTTPError as e:
            if e.response is None or e.response.status_code != 404 or zoom <= 12:
                raise
            zoom, xtile, ytile = zoom - 1, xtile // 2, ytile // 2


def _prefetch_terrarium_tile(zoom, xtile, ytile, get):
    if not os.path.exists(_terrarium_tile_path(zoom, xtile, ytile)):
        _download_terrarium_tile(zoom, xtile, ytile, get=get)


def prefetch_elevation_tiles(api, tiles, progress_cb=None):
    """Download *tiles* ((zoom, xtile, ytile) list) into the disk cache the
    generation reads, one at a time. Worker-thread safe, no bpy access. A
    failed tile is simply fetched again by the generation; three failures in
    a row means the API is down or we're offline, so give up instead of
    waiting out every remaining timeout. Returns the number of failed tiles.
    """
    fetch_tile = _prefetch_mapterhorn_tile if api == "MAPTERHORN" else _prefetch_terrarium_tile
    failed = failed_in_a_row = 0
    with requests.Session() as session:
        for done, (zoom, xtile, ytile) in enumerate(tiles, 1):
            try:
                fetch_tile(zoom, xtile, ytile, session.get)
                failed_in_a_row = 0
            except (requests.RequestException, OSError) as e:
                print(f"Elevation prefetch failed for tile {zoom}/{xtile}/{ytile}: {e}")
                failed += 1
                failed_in_a_row += 1
                if failed_in_a_row >= 3:
                    print("Elevation prefetch stopped: too many failures in a row")
                    return failed
            if progress_cb:
                progress_cb(done)
    return failed


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
                _rpt("OpenTopography: invalid or missing API key (401). "
                "Get a free key at portal.opentopography.org", "error"))
            return [0.0] * len(coords)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        _progress.WarningsOverlay.add_warning(
            _("OpenTopography: request failed — {e}", "error").format(e=e))
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
                    _rpt("OpenTopography: no .asc file found in ZIP response", "error"))
                return [0.0] * len(coords)
            asc_data = zf.read(asc_name).decode('utf-8')
    except zipfile.BadZipFile:
        # Plain-text .asc response
        try:
            asc_data = content.decode('utf-8')
        except (UnicodeDecodeError, AttributeError) as e:
            _progress.WarningsOverlay.add_warning(
                _rpt("OpenTopography: could not decode response — {e}", "error").format(e=e))
            return [0.0] * len(coords)
        # Sanity-check: if it looks like an error page rather than a grid, bail out
        if 'ncols' not in asc_data[:500].lower():
            print(f"OpenTopography unexpected response: {asc_data[:300]}")
            _progress.WarningsOverlay.add_warning(
                _rpt("OpenTopography: unexpected response format (check API key / bbox)", "error"))
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
        col_i = round((lon - xll) / cellsize)
        row_i = round((nrows - 1) - (lat - yll) / cellsize)
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


def get_elevation_localDem(coords, lenv=0, pointsDone=0, progress_cb=None):
    """Sample elevation from a user-selected local GeoTIFF DEM file, or a folder of
    tiled GeoTIFFs covering a larger area (e.g. a national survey's per-km grid),
    with no internet required."""
    if not coords:
        return []

    demFilePath = bpy.context.scene.tp3d.demFilePath
    if not demFilePath or not os.path.exists(demFilePath):
        _progress.WarningsOverlay.add_warning(
            _rpt("Local DEM: no file/folder selected or not found — set it under Advanced ▸ API"), "error")
        return [0.0] * len(coords)

    if progress_cb:
        progress_cb(10)

    # deferred to avoid import cost when unused
    from .geotiff import (
        GeoTiffError,
        build_tile_index,
        dem_contains_point,
        read_geotiff,
        sample_geotiff,
        sample_tile_index,
    )

    if os.path.isdir(demFilePath):
        index = build_tile_index(demFilePath)
        if not index:
            _progress.WarningsOverlay.add_warning(
                _rpt("Local DEM: no readable GeoTIFF tiles found in file."), "error")
            return [0.0] * len(coords)

        if progress_cb:
            progress_cb(30)

        cache = {}
        elevations = []
        missing = 0
        for lat, lon in coords:
            value = sample_tile_index(index, lat, lon, cache)
            if value is None:
                missing += 1
                value = 0.0
            elevations.append(value)

        if missing:
            print(f"Local DEM: {missing} of {len(coords)} points fell outside every tile in the folder (sampled as 0.0)")
            _progress.WarningsOverlay.add_warning(
                _rpt("There was an error, see console for details."), "warn")

        if progress_cb:
            progress_cb(100)

        print(f"Local DEM: sampled {len(elevations)} elevations using {len(cache)}/{len(index)} tiles "
              f"from folder {os.path.basename(demFilePath.rstrip(os.sep))}")
        return elevations

    try:
        dem = read_geotiff(demFilePath)
    except (GeoTiffError, OSError, struct.error, zlib.error) as e:
        _progress.WarningsOverlay.add_warning(_rpt("There was an error, see console for details."), "error")
        print(f"Local DEM: {e}")
        return [0.0] * len(coords)

    if progress_cb:
        progress_cb(50)

    missing = sum(1 for lat, lon in coords if not dem_contains_point(dem, lat, lon))
    elevations = [sample_geotiff(dem, lat, lon) for lat, lon in coords]

    if missing:
        print(f"Local DEM: {missing} of {len(coords)} points fell outside the DEM file's coverage (edge-clamped) — you're generating outside this dataset")
        _progress.WarningsOverlay.add_warning(
            _rpt("There was an error, see console for details."), "warn")

    if progress_cb:
        progress_cb(100)

    print(f"Local DEM: sampled {len(elevations)} elevations from {dem['width']}x{dem['height']} grid ({os.path.basename(demFilePath)})")
    return elevations


def _elevation_results_key(minLat, maxLat, minLon, maxLon, api, num_subdivisions):
    """Return the cache file path for a given map configuration."""
    import hashlib
    raw = f"{minLat:.6f},{maxLat:.6f},{minLon:.6f},{maxLon:.6f},{api},{num_subdivisions}"
    h = hashlib.md5(raw.encode()).hexdigest()
    return os.path.join(const.elevation_results_dir, f"{h}.elev")


def fix_invalid_elevations(elevations):
    """Replace invalid elevation values with the average of their nearest valid neighbors.

    A value is considered invalid if it falls outside the plausible Earth range
    (-500 m to 9000 m) or is a statistical outlier (more than 3 standard deviations
    from the mean of all valid values).

    Returns (fixed_elevations, count) where count is the number of values that were fixed.
    """
    debug = 0  # set to 1 to mark invalid elevations as -1000 instead of fixing them

    n = len(elevations)
    if n == 0:
        return elevations, 0

    valid = [e for e in elevations if -50 < e < 9000]
    if not valid:
        return elevations, 0

    mean = sum(valid) / len(valid)
    variance = sum((e - mean) ** 2 for e in valid) / len(valid)
    std = variance ** 0.5

    def is_invalid(e):
        if e < -500 or e > 9000:
            return True
        return bool(std > 0 and abs(e - mean) > 8 * std)

    count = sum(1 for e in elevations if is_invalid(e))
    if count == 0:
        return elevations, 0

    fixed = list(elevations)
    for i in range(n):
        if not is_invalid(elevations[i]):
            continue
        if debug:
            fixed[i] = -1000
            continue
        left = next((elevations[j] for j in range(i - 1, -1, -1) if not is_invalid(elevations[j])), None)
        right = next((elevations[j] for j in range(i + 1, n)      if not is_invalid(elevations[j])), None)
        if left is not None and right is not None:
            fixed[i] = (left + right) / 2
        elif left is not None:
            fixed[i] = left
        elif right is not None:
            fixed[i] = right
        else:
            fixed[i] = mean

    return fixed, count


def compute_and_store_tile_bounds(gen_or_obj):
    """Compute geographic bounds from obj's mesh and write them to tp3d.

    Accepts either a GenerationContext (preferred) or a bare bpy Object for
    backward compatibility with callers that predate the gen refactor.

    Returns (world_verts, num_subdivisions, disable_cache, minLat, maxLat, minLon, maxLon).
    """
    from .geo import (  # deferred to avoid circular import at load time
        convert_to_geo,
        haversine,
    )
    gen = gen_or_obj if isinstance(gen_or_obj, GenerationContext) else None
    obj = gen.runtime.mapObject if gen is not None else gen_or_obj
    mesh = obj.data
    vertices = list(mesh.vertices)
    obj_matrix = obj.matrix_world

    world_verts = [obj_matrix @ v.co for v in vertices]

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

    num_subdivisions = bpy.context.scene.tp3d.num_subdivisions
    disable_cache = bpy.context.scene.tp3d.disableCache

    realdist1 = haversine(minLat, minLon, maxLat, maxLon)
    realdist2 = haversine(minLat, minLon, maxLat, maxLon)

    if gen is not None:
        gen.runtime.mapKm = max(realdist1, realdist2)
        gen.runtime.tbMinLat = minLat
        gen.runtime.tbMaxLat = maxLat
        gen.runtime.tbMinLon = minLon
        gen.runtime.tbMaxLon = maxLon

    return world_verts, num_subdivisions, disable_cache, minLat, maxLat, minLon, maxLon


def get_tile_elevation_ranges(obj, elevations, scale_hor):
    """Return raw and Mercator-corrected elevation ranges, both in meters.

    Uses bulk mesh coordinates without fetching elevations again. The corrected
    range can be scaled after the batch-wide Fixed Height factor is known.
    """
    import numpy as np

    n_verts = len(obj.data.vertices)
    if n_verts == 0 or len(elevations) != n_verts:
        raise ValueError("Elevation values must match a non-empty tile mesh.")
    if not math.isfinite(scale_hor) or scale_hor <= 0:
        raise ValueError("Horizontal scale must be positive and finite.")

    coords = np.empty(n_verts * 3, dtype=np.float64)
    obj.data.vertices.foreach_get("co", coords)
    coords = coords.reshape((n_verts, 3))
    matrix_y = np.array(obj.matrix_world[1], dtype=np.float64)
    world_y = coords @ matrix_y[:3] + matrix_y[3]
    lat_rad = 2.0 * np.arctan(
        np.exp(world_y / (const.R * scale_hor))
    ) - np.pi / 2.0
    values = np.asarray(elevations, dtype=np.float64)
    corrected = values / np.cos(lat_rad)
    return (
        (float(values.min()), float(values.max())),
        (float(corrected.min()), float(corrected.max())),
    )


def get_tile_elevation(gen_or_obj, progress_cb=None):
    """Fetch terrain elevation for all mesh vertices and return (elevations, diff).

    Accepts either a GenerationContext (preferred — stores results directly into
    gen.runtime.tileVerts and gen.runtime.elDiff) or a bare bpy Object for backward compatibility
    with callers that predate the ctx refactor (operators, io_geojson, ctfs path).
    """
    if isinstance(gen_or_obj, GenerationContext):
        gen = gen_or_obj
        obj = gen.runtime.mapObject
    else:
        gen = None
        obj = gen_or_obj

    mesh = obj.data
    api = bpy.context.scene.tp3d.api

    from .geo import convert_to_geo  # deferred to avoid circular import at load time

    # Set chunk size based on API
    if api == "OPENTOPODATA":
        chunk_size = 100000
    elif api == "TERRAIN-TILES" or api == "MAPTERHORN" or api == "OPENTOPOGRAPHY" or api == "LOCAL_DEM":
        chunk_size = 50000000   # single request for all verts
    else:
        chunk_size = 100000  # fallback

    world_verts, num_subdivisions, disable_cache, minLat, maxLat, minLon, maxLon = compute_and_store_tile_bounds(obj)

    # ── Elevation results cache ───────────────────────────────────────────────
    # Key on geographic bounds + API + subdivision count so the same map on
    # re-generation skips all tile fetching / PNG parsing.
    _cache_path = _elevation_results_key(minLat, maxLat, minLon, maxLon, api, num_subdivisions)

    def _finish_elevation_results(elevations):
        if not bpy.context.scene.tp3d.disableElevationOutlierFix:
            elevations, _fixed_count = fix_invalid_elevations(elevations)
        else:
            _fixed_count = 0
        if _fixed_count > 0:
            print(f"Fixed {_fixed_count} invalid elevation value(s)")
            if gen is not None:
                gen.runtime.buggyData = 1

        lowest_elevation = min(elevations)
        highest_elevation = max(elevations)
        diff = highest_elevation - lowest_elevation
        tp3d = bpy.context.scene.tp3d
        tp3d["o_verticesMap"] = str(len(mesh.vertices))
        tp3d["lowestElevation"] = lowest_elevation
        tp3d["highestElevation"] = highest_elevation
        tp3d.sAdditionalExtrusion = lowest_elevation
        if gen is not None:
            gen.runtime.tileVerts = elevations
            gen.runtime.elDiff = diff
        return elevations, diff

    if _run_elevation_cache is not None:
        _run_cached = _run_elevation_cache.get(_cache_path)
        if _run_cached is not None:
            if len(_run_cached) == len(world_verts):
                print(f"Elevation run cache hit ({len(_run_cached)} verts) — skipping API fetch")
                elevations, diff = _finish_elevation_results(list(_run_cached))
                return elevations, diff
            print(
                f"Elevation run cache vertex count mismatch ({len(_run_cached)} "
                f"vs {len(world_verts)}) — refetching"
            )
            del _run_elevation_cache[_cache_path]

    if not disable_cache and os.path.exists(_cache_path):
        try:
            with open(_cache_path, "rb") as _f:
                _raw = zlib.decompress(_f.read())
            _n = struct.unpack_from("<I", _raw, 0)[0]
            elevations = list(struct.unpack_from(f"<{_n}f", _raw, 4))
            if len(elevations) == len(world_verts):
                print(f"Elevation cache hit ({_n} verts) — skipping API fetch")
                elevations, diff = _finish_elevation_results(elevations)
                if _run_elevation_cache is not None and any(elevations):
                    _run_elevation_cache[_cache_path] = array("f", elevations)
                return elevations, diff
            else:
                print(f"Elevation cache vertex count mismatch ({len(elevations)} vs {len(world_verts)}) — refetching")
        except (OSError, zlib.error, struct.error) as _e:
            print(f"Elevation cache read error: {_e} — refetching")
    # ─────────────────────────────────────────────────────────────────────────

    elevations = []
    for i in range(0, len(world_verts), chunk_size):
        chunk = world_verts[i:i + chunk_size]

        coords = [convert_to_geo(v.x, v.y) for v in chunk]
        # A map unwrapped across the 180th meridian (io_geojson's
        # _unwrap_antimeridian) has vertices past lon 180 -- wrap them back
        # into -180..180 for the APIs/tile grids.
        coords = [(lat, (lon + 180.0) % 360.0 - 180.0) for lat, lon in coords]
        if api == "OPENTOPODATA":
            chunk_elevations = get_elevation_openTopoData(coords, len(world_verts), i, progress_cb=progress_cb)
        elif api == "TERRAIN-TILES":
            chunk_elevations = get_elevation_TerrainTiles((minLat, maxLat, minLon, maxLon), coords, len(world_verts), i, progress_cb=progress_cb)
        elif api == "MAPTERHORN":
            chunk_elevations = get_elevation_Mapterhorn((minLat, maxLat, minLon, maxLon), coords, len(world_verts), i, progress_cb=progress_cb)
        elif api == "OPENTOPOGRAPHY":
            chunk_elevations = get_elevation_openTopography(coords, len(world_verts), i, progress_cb=progress_cb)
        elif api == "LOCAL_DEM":
            chunk_elevations = get_elevation_localDem(coords, len(world_verts), i, progress_cb=progress_cb)
        else:
            chunk_elevations = [0.0] * len(chunk)  # fallback

        elevations.extend(chunk_elevations)

        # Free memory after processing chunk
        del chunk_elevations

    elevations, diff = _finish_elevation_results(elevations)

    # Save results cache for future re-generations of the same map.
    # Skip if all elevations are zero — indicates a completely failed fetch.
    _elev_min = min(elevations)
    _elev_max = max(elevations)
    if _elev_min == 0.0 and _elev_max == 0.0:
        print("Elevation results are all zero — skipping cache write to avoid caching a failed fetch")
    else:
        if _run_elevation_cache is not None:
            _run_elevation_cache[_cache_path] = array("f", elevations)
        if not (_run_elevation_cache is not None and disable_cache):
            try:
                os.makedirs(const.elevation_results_dir, exist_ok=True)
                _n = len(elevations)
                _packed = struct.pack("<I", _n) + struct.pack(f"<{_n}f", *elevations)
                with open(_cache_path, "wb") as _f:
                    _f.write(zlib.compress(_packed, level=1))
            except (OSError, struct.error, zlib.error) as _e:
                print(f"Elevation cache write error: {_e}")

    if not (_run_elevation_cache is not None and disable_cache):
        save_elevation_cache()

    return elevations, diff
