import hashlib
import json
import math
import os
import time
from collections import deque
from typing import NamedTuple

import bmesh  # type: ignore
import bpy  # type: ignore
import requests
from mathutils import Vector  # type: ignore

from .. import constants as const
from .. import progress as _progress


class OsmFetchSettings(NamedTuple):
    """Snapshot of all bpy.context values needed by fetch_osm_data.

    Read these on the main thread before spawning worker threads so that
    workers never touch bpy.context.
    """
    disable_cache: int
    api_retries: int
    mapsize: float
    road_big: bool
    road_med: bool
    road_small: bool
    water_ponds: bool
    water_small_rivers: bool
    water_big_rivers: bool


def _overpass_request(query, overpass_url, method='POST', timeout=60, max_retries=3,
                      log_callback=None):
    """Make one Overpass API request with retry/backoff.

    Parameters
    ----------
    query        : Overpass QL string
    overpass_url : endpoint URL
    method       : 'POST' (default) or 'GET'
    timeout      : per-request timeout in seconds
    max_retries  : total attempts before giving up
    log_callback : optional callable(str) for progress messages — called from
                   whatever thread this runs on, so keep it thread-safe.

    Returns the parsed JSON dict on success, or None on failure.
    """
    for attempt in range(max_retries):
        try:
            if method == 'POST':
                response = requests.post(
                    overpass_url,
                    data={"data": query},
                    headers={'User-Agent': 'TrailPrint3D_3.00', 'Accept': '*/*'},
                    timeout=timeout,
                )
            else:
                response = requests.get(
                    overpass_url,
                    params={'data': query},
                    headers={'User-Agent': 'TrailPrint3D_3.00', 'Accept': '*/*'},
                    timeout=timeout,
                )

            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError:
                    print(f"Attempt {attempt + 1}: Invalid JSON response")
                    # fall through to retry
            else:
                retry_num = attempt + 2
                print(f"Status ({response.status_code}), retrying... {retry_num}/{max_retries}")
                if log_callback:
                    log_callback(f"Overpass error {response.status_code} — retrying {retry_num}/{max_retries}")
                time.sleep(5 + attempt)

        except requests.exceptions.Timeout:
            retry_num = attempt + 2
            print(f"Request timed out (attempt {attempt + 1}/{max_retries})")
            if log_callback:
                log_callback(f"Timed out — retrying {retry_num}/{max_retries}")
            time.sleep(5)
        except requests.RequestException as e:
            print(f"Request failed: {e}")
            time.sleep(5)

    print("Overpass request failed after retries")
    return None


def fetch_osm_data(bbox, kind="WATER", max_cache_age_hours=720, return_cache_status=False,
                   settings=None):
    """Fetch (or return cached) OSM data for a bbox + kind.

    Parameters
    ----------
    settings : OsmFetchSettings or None
        When supplied (worker-thread path), all bpy.context reads are skipped
        and the pre-read values are used instead.  Must be None only when
        called from the main thread, where bpy.context is valid.
    """
    #print("FETCH OSM:", kind)

    if settings is not None:
        disableCache       = settings.disable_cache
        apiRetries         = settings.api_retries
        mapsize            = settings.mapsize
        road_big           = settings.road_big
        road_med           = settings.road_med
        road_small         = settings.road_small
        water_ponds        = settings.water_ponds
        water_small_rivers = settings.water_small_rivers
        water_big_rivers   = settings.water_big_rivers
    else:
        disableCache       = bpy.context.scene.tp3d.disableCache
        apiRetries         = bpy.context.scene.tp3d.apiRetries
        mapsize            = bpy.context.scene.tp3d.sMapInKm
        road_big           = bool(bpy.context.scene.tp3d.el_sBigActive)
        road_med           = bool(bpy.context.scene.tp3d.el_sMedActive)
        road_small         = bool(bpy.context.scene.tp3d.el_sSmallActive)
        water_ponds        = bool(bpy.context.scene.tp3d.col_wPondsActive)
        water_small_rivers = bool(bpy.context.scene.tp3d.col_wSmallRiversActive)
        water_big_rivers   = bool(bpy.context.scene.tp3d.col_wBigRiversActive)

    # Small/minor waterways are expensive on large maps -- drop them above
    # SMALL_RIVERS_MAXSIZE. Big (wikidata-tagged) rivers and ponds keep
    # applying up to the regular WATER_MAXSIZE cap.
    water_small_rivers = water_small_rivers and mapsize <= const.SMALL_RIVERS_MAXSIZE

    def get_cache_dir():
        path = const.overpass_cache_dir
        os.makedirs(path, exist_ok=True)
        return path


    def make_cache_key(bbox, kind):
        south, west, north, east = bbox
        payload = {
            "bbox": [round(south, 7), round(west, 7), round(north, 7), round(east, 7)],
            "kind": kind
        }
        data = json.dumps(payload, sort_keys=True).encode("utf-8")
        return hashlib.sha256(data).hexdigest()

    cache_dir = get_cache_dir()
    cache_key = make_cache_key(bbox, kind)
    if kind == "STREETS":
        cache_key = make_cache_key(bbox, kind + str(road_big) + str(road_med) + str(road_small))
    if kind == "WATER":
        cache_key = make_cache_key(bbox, kind + str(water_ponds) + str(water_small_rivers) + str(water_big_rivers))
    cache_path = os.path.join(cache_dir, f"{cache_key}.json")

    # --------------------------------------------------
    # Use cache if fresh
    # --------------------------------------------------
    if os.path.exists(cache_path) and disableCache == 0:
        age_hours = (time.time() - os.path.getmtime(cache_path)) / 3600
        if age_hours < max_cache_age_hours:
            print("Cached Data found")
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return (data, True) if return_cache_status else data


    south, west, north, east = bbox
    # Clamp to valid geographic ranges — guards against antimeridian padding overflow
    west = max(-180.0, min(180.0, west))
    east = max(-180.0, min(180.0, east))
    south = max(-90.0, min(90.0, south))
    north = max(-90.0, min(90.0, north))
    overpass_url = "https://overpass-api.de/api/interpreter"

    # --------------------------------------------------
    # Build query
    # Each entry is a callable (south, west, north, east, **ctx) -> query string.
    # ctx carries extra context (e.g. mapsize) for kinds that need dynamic filters.
    # To add a new OSM kind, add one entry to this dict.
    # --------------------------------------------------
    def _bbox_header(s, w, n, e):
        return f"[out:json][timeout:60][bbox:{s},{w},{n},{e}]"

    def _simple_query(s, w, n, e, filters):
        """Build a standard area query from a list of tag-filter strings."""
        lines = "\n".join(f"        {f};" for f in filters)
        return f"""
        {_bbox_header(s, w, n, e)};
        (
{lines}
        );
        out body;
        >;
        out skel qt;
        """

    OSM_QUERY_BUILDERS = {
        "WATER": lambda s, w, n, e, ponds=True, small_rivers=True, big_rivers=True, **_: _build_water_query(s, w, n, e, ponds, small_rivers, big_rivers),
        "FOREST": lambda s, w, n, e, **_: _simple_query(s, w, n, e, [
            'way["natural"="wood"]',
            'relation["natural"="wood"]',
            'way["landuse"="forest"]',
            'relation["landuse"="forest"]',
        ]),
        "SCREE": lambda s, w, n, e, **_: _simple_query(s, w, n, e, [
            'nwr["natural"="scree"]',
            'nwr["natural"="stone"]',
            'nwr["natural"="boulder"]',
            'nwr["natural"="rock"]',
            'nwr["natural"="bare_rock"]',
        ]),
        "CITY": lambda s, w, n, e, **_: _simple_query(s, w, n, e, [
            'way["landuse"~"residential|urban|commercial|industrial"]',
            'relation["landuse"~"residential|urban|commercial|industrial"]',
        ]),
        "GREENSPACE": lambda s, w, n, e, **_: _simple_query(s, w, n, e, [
            'way["leisure"="park"]',
            'relation["leisure"="park"]',
            'way["leisure"="garden"]',
            'relation["leisure"="garden"]',
            'way["leisure"="recreation_ground"]',
            'relation["leisure"="recreation_ground"]',
            'way["landuse"="grass"]',
            'way["natural"="grass"]',
            'way["landuse"="village_green"]',
            'relation["landuse"="village_green"]',
        ]),
        "FARMLAND": lambda s, w, n, e, **_: _simple_query(s, w, n, e, [
            'way["landuse"="farmland"]',
            'way["landuse"="farmyard"]',
            'relation["landuse"="farmland"]',
            'relation["landuse"="farmyard"]',
        ]),
        "GLACIER": lambda s, w, n, e, **_: _simple_query(s, w, n, e, [
            'way["natural"="glacier"]',
            'relation["natural"="glacier"]',
        ]),
        "COASTLINE": lambda s, w, n, e, **_: _simple_query(s, w, n, e, [
            'way["natural"="coastline"]',
        ]),
        "BUILDINGS": lambda s, w, n, e, **_: _simple_query(s, w, n, e, [
            'nwr["building"]',
        ]),
        "STREETS": lambda s, w, n, e, mapsize=0, big=True, med=True, small=False, **_: _build_streets_query(s, w, n, e, mapsize, big, med, small),
    }

    def _build_water_query(s, w, n, e, ponds, small_rivers, big_rivers):
        filters = []
        if ponds:
            filters += [
                'way["natural"="water"]',
                'relation["natural"="water"]',
                'way["water"~"river|lake|stream|canal"]',
                'relation["water"~"river|lake|stream|canal"]',
            ]
        if small_rivers:
            # No wikidata filter — includes all minor waterways
            filters.append('way["waterway"~"stream|river|canal|ditch|drain"]')
        elif big_rivers:
            # Only major named rivers (wikidata-tagged)
            filters.append('way["waterway"~"stream|river|canal|ditch|drain"]["wikidata"]')
        if big_rivers and small_rivers:
            # small_rivers already covers big ones; wikidata filter would be redundant
            pass
        if not filters:
            # Fallback: return an empty result query
            return f"{_bbox_header(s, w, n, e)};\n(  );\nout body;\n>;\nout skel qt;"
        return _simple_query(s, w, n, e, filters)

    def _build_streets_query(s, w, n, e, mapsize, big, med, small):
        all_big   = {'primary', 'motorway', 'primary_link', 'motorway_link'}
        all_med   = {'secondary', 'tertiary', 'secondary_link', 'tertiary_link', 'unclassified', 'trunk', 'trunk_link'}
        all_small = {'residential', 'living_street', 'service', 'footway'}

        # Build user-requested set
        requested = set()
        if big:   requested |= all_big
        if med:   requested |= all_med
        if small: requested |= all_small

        # Apply mapsize performance limits (larger maps = fewer road types allowed)
        allowed = all_big | all_med | all_small
        if mapsize > const.ROADS_MAXSIZE:
            allowed = all_big
        elif mapsize > const.STREETS_PRIMARY_THRESHOLD:
            allowed = all_big | all_med
        elif mapsize > const.STREETS_MAJOR_ONLY_THRESHOLD:
            allowed = all_big | all_med | all_small

        highway_types = sorted(requested & allowed)
        if not highway_types:
            highway_types = ['motorway', 'primary']
        pattern = '|'.join(highway_types)
        filter_str = f'["highway"~"^({pattern})$"]'
        print(f"Filter str: {filter_str}")
        return _simple_query(s, w, n, e, [f"way{filter_str}"])

    builder = OSM_QUERY_BUILDERS.get(kind)
    if builder is None:
        raise ValueError(f"Unknown OSM kind: {kind}")
    query = builder(south, west, north, east, mapsize=mapsize, big=road_big, med=road_med, small=road_small,
                    ponds=water_ponds, small_rivers=water_small_rivers, big_rivers=water_big_rivers)

    # --------------------------------------------------
    # Request with retries
    # --------------------------------------------------
    # Progress callback — only safe on the main thread (ProgressOverlay touches
    # bpy.context.region).  Worker threads pass settings!=None so _log is None.
    if settings is None:
        def _log(msg):
            _ov = _progress.ProgressOverlay.get()
            if _ov.active:
                _ov.update(message=msg)
    else:
        _log = None

    print(f"[fetch_osm_data] {kind}: {query.splitlines()[1].strip() if len(query.splitlines()) > 1 else query[:80]}")
    data = _overpass_request(
        query, overpass_url,
        method='POST', timeout=60, max_retries=apiRetries,
        log_callback=_log,
    )
    if data is None:
        _progress.WarningsOverlay.add_warning(f"failed to fetch {kind} elements from Overpass API", "error")
        return None

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    return (data, False) if return_cache_status else data


# ─────────────────────────────────────────────────────────────────────────────
# Combined (union) fetch — one Overpass request covers all requested kinds
# ─────────────────────────────────────────────────────────────────────────────

def _make_cache_path(bbox, kind, settings=None):
    """Return the cache file path for a given bbox + kind + settings.

    Mirrors the key computation inside fetch_osm_data so that
    fetch_osm_combined writes to exactly the same files that fetch_osm_data
    would later read, giving a warm-cache hit.
    """
    south, west, north, east = bbox
    if settings is not None:
        mapsize            = settings.mapsize
        road_big           = settings.road_big
        road_med           = settings.road_med
        road_small         = settings.road_small
        water_ponds        = settings.water_ponds
        water_small_rivers = settings.water_small_rivers
        water_big_rivers   = settings.water_big_rivers
    else:
        mapsize            = bpy.context.scene.tp3d.sMapInKm
        road_big           = bool(bpy.context.scene.tp3d.el_sBigActive)
        road_med           = bool(bpy.context.scene.tp3d.el_sMedActive)
        road_small         = bool(bpy.context.scene.tp3d.el_sSmallActive)
        water_ponds        = bool(bpy.context.scene.tp3d.col_wPondsActive)
        water_small_rivers = bool(bpy.context.scene.tp3d.col_wSmallRiversActive)
        water_big_rivers   = bool(bpy.context.scene.tp3d.col_wBigRiversActive)

    # Keep in sync with the same gate in fetch_osm_data / _build_union_query
    # so the cache key matches whatever was actually queried.
    water_small_rivers = water_small_rivers and mapsize <= const.SMALL_RIVERS_MAXSIZE

    cache_kind = kind
    if kind == "STREETS":
        cache_kind = kind + str(road_big) + str(road_med) + str(road_small)
    elif kind == "WATER":
        cache_kind = kind + str(water_ponds) + str(water_small_rivers) + str(water_big_rivers)

    payload = {
        "bbox": [round(south, 7), round(west, 7), round(north, 7), round(east, 7)],
        "kind": cache_kind,
    }
    key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    cache_dir = const.overpass_cache_dir
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, f"{key}.json")


def _build_union_query(south, west, north, east, kinds, settings=None):
    """Build a single Overpass QL union query covering all requested kinds.

    Returns the QL string, or None if the combination of requested kinds and
    settings produces no filter statements (e.g. WATER requested but all
    water sub-types are disabled).
    """
    if settings is not None:
        mapsize            = settings.mapsize
        road_big           = settings.road_big
        road_med           = settings.road_med
        road_small         = settings.road_small
        water_ponds        = settings.water_ponds
        water_small_rivers = settings.water_small_rivers
        water_big_rivers   = settings.water_big_rivers
    else:
        mapsize            = bpy.context.scene.tp3d.sMapInKm
        road_big           = bool(bpy.context.scene.tp3d.el_sBigActive)
        road_med           = bool(bpy.context.scene.tp3d.el_sMedActive)
        road_small         = bool(bpy.context.scene.tp3d.el_sSmallActive)
        water_ponds        = bool(bpy.context.scene.tp3d.col_wPondsActive)
        water_small_rivers = bool(bpy.context.scene.tp3d.col_wSmallRiversActive)
        water_big_rivers   = bool(bpy.context.scene.tp3d.col_wBigRiversActive)

    # Small/minor waterways are expensive on large maps -- drop them above
    # SMALL_RIVERS_MAXSIZE. Big (wikidata-tagged) rivers and ponds keep
    # applying up to the regular WATER_MAXSIZE cap.
    water_small_rivers = water_small_rivers and mapsize <= const.SMALL_RIVERS_MAXSIZE

    filters = []

    if "FOREST" in kinds:
        filters += [
            'way["natural"="wood"]',
            'relation["natural"="wood"]',
            'way["landuse"="forest"]',
            'relation["landuse"="forest"]',
        ]

    if "WATER" in kinds:
        if water_ponds:
            filters += [
                'way["natural"="water"]',
                'relation["natural"="water"]',
                'way["water"~"river|lake|stream|canal"]',
                'relation["water"~"river|lake|stream|canal"]',
            ]
        if water_small_rivers:
            filters.append('way["waterway"~"stream|river|canal|ditch|drain"]')
        elif water_big_rivers:
            filters.append('way["waterway"~"stream|river|canal|ditch|drain"]["wikidata"]')

    if "SCREE" in kinds:
        filters += [
            'nwr["natural"="scree"]',
            'nwr["natural"="stone"]',
            'nwr["natural"="boulder"]',
            'nwr["natural"="rock"]',
            'nwr["natural"="bare_rock"]',
        ]

    if "CITY" in kinds:
        filters += [
            'way["landuse"~"residential|urban|commercial|industrial"]',
            'relation["landuse"~"residential|urban|commercial|industrial"]',
        ]

    if "GREENSPACE" in kinds:
        filters += [
            'way["leisure"="park"]',
            'relation["leisure"="park"]',
            'way["leisure"="garden"]',
            'relation["leisure"="garden"]',
            'way["leisure"="recreation_ground"]',
            'relation["leisure"="recreation_ground"]',
            'way["landuse"="grass"]',
            'way["natural"="grass"]',
            'way["landuse"="village_green"]',
            'relation["landuse"="village_green"]',
        ]

    if "FARMLAND" in kinds:
        filters += [
            'way["landuse"="farmland"]',
            'way["landuse"="farmyard"]',
            'relation["landuse"="farmland"]',
            'relation["landuse"="farmyard"]',
        ]

    if "GLACIER" in kinds:
        filters += [
            'way["natural"="glacier"]',
            'relation["natural"="glacier"]',
        ]

    if "COASTLINE" in kinds:
        filters.append('way["natural"="coastline"]')

    if "BUILDINGS" in kinds:
        filters.append('nwr["building"]')

    if "STREETS" in kinds:
        all_big   = {"primary", "motorway", "primary_link", "motorway_link"}
        all_med   = {"secondary", "tertiary", "secondary_link", "tertiary_link",
                     "unclassified", "trunk", "trunk_link"}
        all_small = {"residential", "living_street", "service", "footway"}

        requested: set = set()
        if road_big:   requested |= all_big
        if road_med:   requested |= all_med
        if road_small: requested |= all_small

        # Apply the same mapsize performance limits as _build_streets_query
        allowed = all_big | all_med | all_small
        if mapsize > const.ROADS_MAXSIZE:
            allowed = all_big
        elif mapsize > const.STREETS_PRIMARY_THRESHOLD:
            allowed = all_big | all_med
        elif mapsize > const.STREETS_MAJOR_ONLY_THRESHOLD:
            allowed = all_big | all_med | all_small

        highway_types = sorted(requested & allowed) or ["motorway", "primary"]
        pattern = "|".join(highway_types)
        filters.append(f'way["highway"~"^({pattern})$"]')

    if not filters:
        return None

    filter_lines = "\n".join(f"  {f};" for f in filters)
    return (
        f"[out:json][timeout:120][bbox:{south},{west},{north},{east}];\n"
        f"(\n{filter_lines}\n);\n"
        f"out body;\n>;\nout skel qt;\n"
    )


def _classify_element(element, active_kinds, settings=None):
    """Classify a way or relation element into one of *active_kinds*.

    Returns the kind string, or None if no kind matches.
    Priority order mirrors tag-filter specificity (most-specific first).
    Bare nodes carry no kind-level tags and are not classified here.
    """
    tags = element.get("tags", {})

    # STREETS — road network is unambiguous
    if "STREETS" in active_kinds:
        highway = tags.get("highway", "")
        if highway:
            if settings is not None:
                all_big   = {"primary", "motorway", "primary_link", "motorway_link"}
                all_med   = {"secondary", "tertiary", "secondary_link", "tertiary_link",
                             "unclassified", "trunk", "trunk_link"}
                all_small = {"residential", "living_street", "service", "footway"}
                allowed: set = set()
                if settings.road_big:   allowed |= all_big
                if settings.road_med:   allowed |= all_med
                if settings.road_small: allowed |= all_small
                # Clamp to mapsize-based performance limits (mirror _build_streets_query)
                if settings.mapsize > const.ROADS_MAXSIZE:
                    allowed &= all_big
                elif settings.mapsize > const.STREETS_PRIMARY_THRESHOLD:
                    allowed &= all_big | all_med
                if highway in allowed:
                    return "STREETS"
            else:
                return "STREETS"

    # BUILDINGS — anything with a building=* tag
    if "BUILDINGS" in active_kinds and tags.get("building"):
        return "BUILDINGS"

    # WATER — natural water bodies and waterways
    if "WATER" in active_kinds:
        natural  = tags.get("natural", "")
        water    = tags.get("water", "")
        waterway = tags.get("waterway", "")
        if natural == "water":
            if settings is None or settings.water_ponds:
                return "WATER"
        if water in {"river", "lake", "stream", "canal"}:
            if settings is None or settings.water_ponds:
                return "WATER"
        if waterway in {"stream", "river", "canal", "ditch", "drain"}:
            if settings is None or settings.water_small_rivers:
                return "WATER"
            elif settings is not None and settings.water_big_rivers and tags.get("wikidata"):
                return "WATER"

    # FOREST
    if "FOREST" in active_kinds:
        if tags.get("natural") == "wood" or tags.get("landuse") == "forest":
            return "FOREST"

    # GLACIER
    if "GLACIER" in active_kinds and tags.get("natural") == "glacier":
        return "GLACIER"

    # SCREE
    if "SCREE" in active_kinds:
        if tags.get("natural") in {"scree", "stone", "boulder", "rock", "bare_rock"}:
            return "SCREE"

    # CITY — urban land use
    if "CITY" in active_kinds:
        if tags.get("landuse") in {"residential", "urban", "commercial", "industrial"}:
            return "CITY"

    # GREENSPACE — parks and grassy areas
    if "GREENSPACE" in active_kinds:
        leisure = tags.get("leisure", "")
        landuse = tags.get("landuse", "")
        natural = tags.get("natural", "")
        if leisure in {"park", "garden", "recreation_ground"}:
            return "GREENSPACE"
        if landuse in {"grass", "village_green"}:
            return "GREENSPACE"
        if natural == "grass":
            return "GREENSPACE"

    # FARMLAND
    if "FARMLAND" in active_kinds:
        if tags.get("landuse") in {"farmland", "farmyard"}:
            return "FARMLAND"

    # COASTLINE
    if "COASTLINE" in active_kinds and tags.get("natural") == "coastline":
        return "COASTLINE"

    return None


def fetch_osm_combined(bbox, kinds, settings=None, semaphore=None,
                       max_cache_age_hours=720, tile_progress=None):
    """Fetch all requested OSM kinds for one tile bbox in a single Overpass request.

    Cache is checked per-kind first.  Only cache-miss kinds are bundled into
    the request.  After the request the response is split by kind and each
    subset is written to the standard per-kind cache file so that any later
    fetch_osm_data() call for the same bbox+kind gets a cache hit.

    Parameters
    ----------
    bbox      : (south, west, north, east) tuple
    kinds     : sequence of OSM kind strings (e.g. ['FOREST', 'WATER'])
    settings  : OsmFetchSettings snapshot, or None to read bpy.context live
    semaphore : optional threading.Semaphore — acquired only during the
                network request so that cache-only calls never contend
    max_cache_age_hours : freshness threshold for the per-kind cache files
    tile_progress : optional (index, total) tuple — this tile's 1-based
                position among all tiles in the current batch, used only to
                annotate the console log (e.g. "(5/43)")

    Returns
    -------
    dict  {kind: (data_dict, from_cache_bool)}
    Only kinds successfully fetched (or found in cache) are present.
    """
    # ── 1. Per-kind cache check (no semaphore needed) ────────────────────
    if settings is not None:
        disable_cache = settings.disable_cache
        api_retries   = settings.api_retries
    else:
        disable_cache = bpy.context.scene.tp3d.disableCache
        api_retries   = bpy.context.scene.tp3d.apiRetries

    result: dict = {}
    missing: list = []
    for kind in kinds:
        cache_path = _make_cache_path(bbox, kind, settings)
        if not disable_cache and os.path.exists(cache_path):
            age_hours = (time.time() - os.path.getmtime(cache_path)) / 3600
            if age_hours < max_cache_age_hours:
                try:
                    with open(cache_path, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    result[kind] = (data, True)
                    continue
                except (OSError, json.JSONDecodeError):
                    pass  # fall through to network fetch
        missing.append(kind)

    if not missing:
        return result

    # ── 2. Build union query ──────────────────────────────────────────────
    south, west, north, east = bbox
    south = max(-90.0,  min(90.0,  south))
    north = max(-90.0,  min(90.0,  north))
    west  = max(-180.0, min(180.0, west))
    east  = max(-180.0, min(180.0, east))

    query = _build_union_query(south, west, north, east, missing, settings)
    if query is None:
        return result

    overpass_url = "https://overpass-api.de/api/interpreter"
    progress_suffix = f"  ({tile_progress[0]}/{tile_progress[1]})" if tile_progress else ""
    print(f"[fetch_osm_combined] requesting {missing} for bbox {(south, west, north, east)}{progress_suffix}")

    # ── 3. Network request (hold semaphore only here) ─────────────────────
    if semaphore is not None:
        with semaphore:
            data = _overpass_request(
                query, overpass_url, method="POST",
                timeout=120, max_retries=api_retries,
            )
    else:
        data = _overpass_request(
            query, overpass_url, method="POST",
            timeout=120, max_retries=api_retries,
        )

    if data is None:
        _progress.WarningsOverlay.add_warning(
            f"failed to fetch {missing} elements from Overpass API", "error"
        )
        return result  # return whatever cache hits we already have

    # ── 4. Split response elements by kind ───────────────────────────────
    elements = data.get("elements", [])

    # Index all node elements by id for O(1) lookup
    all_nodes: dict = {
        e["id"]: e for e in elements if e.get("type") == "node"
    }

    # Index all way elements by id so relation member ways can be looked up
    all_ways: dict = {
        e["id"]: e for e in elements if e.get("type") == "way"
    }

    # First pass: classify all way/relation elements into per-kind buckets.
    # When a relation is classified, also include its member ways — those
    # boundary ways carry no feature tags of their own, so _classify_element
    # returns None for them, but extract_multipolygon_bodies needs them to
    # stitch the polygon rings.
    per_kind: dict = {kind: {"elements": []} for kind in missing}
    for element in elements:
        if element.get("type") == "node":
            continue
        kind = _classify_element(element, missing, settings)
        if kind is not None:
            per_kind[kind]["elements"].append(element)
            if element.get("type") == "relation":
                seen = {e["id"] for e in per_kind[kind]["elements"]}
                for member in element.get("members", []):
                    if member.get("type") == "way":
                        way = all_ways.get(member["ref"])
                        if way is not None and way["id"] not in seen:
                            per_kind[kind]["elements"].append(way)
                            seen.add(way["id"])

    # Second pass: add only the nodes that are actually referenced by each
    # kind's ways.  Including every node from every kind in every bucket
    # causes e.g. STREETS to receive hundreds of thousands of CITY/BUILDINGS
    # nodes, making build_osm_nodes and create_roads dramatically slower.
    for kind in missing:
        referenced_ids: set = set()
        for el in per_kind[kind]["elements"]:
            if el.get("type") == "way":
                referenced_ids.update(el.get("nodes", []))
            # relation members are ways, not raw nodes — no node ids here
        kind_nodes = [all_nodes[nid] for nid in referenced_ids if nid in all_nodes]
        per_kind[kind]["elements"] = kind_nodes + per_kind[kind]["elements"]

    # ── 5. Write per-kind cache files and build return value ──────────────
    for kind in missing:
        kind_data  = per_kind[kind]
        cache_path = _make_cache_path(bbox, kind, settings)
        try:
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump(kind_data, fh)
        except OSError as exc:
            print(f"[fetch_osm_combined] cache write failed for {kind}: {exc}")
        result[kind] = (kind_data, False)

    return result


def extract_multipolygon_bodies(elements, nodes):
    # Helper to get coordinates of a way by its node ids
    def way_coords(way):
        return [ (nodes[nid]['lat'], nodes[nid]['lon'], nodes[nid].get('elevation', 0)) for nid in way['nodes'] if nid in nodes ]

    # Store all multipolygon lakes as lists of outer rings (each ring = list of coords)
    multipolygon_lakes = []
    multipolycon_negatives = []

    # Index ways by their id for quick lookup
    way_dict = {el['id']: el for el in elements if el['type'] == 'way'}

    for el in elements:
        if el['type'] == 'relation':
            # Collect outer and inner member ways
            outer_ways = []
            inner_ways = []

            for member in el.get('members', []):
                if member['type'] != 'way':
                    continue
                way = way_dict.get(member['ref'])
                if not way:
                    continue

                role = member.get('role', '')
                if role == 'outer':
                    outer_ways.append(way)
                elif role == 'inner':
                    inner_ways.append(way)

            # Stitch ways to closed loops for outer and inner rings
            def stitch_ways(ways):
                loops = []
                # Convert ways to deque of coord lists for O(1) popleft
                ways_dq = deque(way_coords(w) for w in ways)

                while ways_dq:
                    current = ways_dq.popleft()
                    changed = True
                    while changed:
                        changed = False
                        remaining = deque()
                        while ways_dq:
                            w = ways_dq.popleft()
                            if not w:
                                continue
                            # Check if current end connects to w start or end
                            if current[-1] == w[0]:
                                current.extend(w[1:])
                                changed = True
                            elif current[-1] == w[-1]:
                                current.extend(reversed(w[:-1]))
                                changed = True
                            # Also check if current start connects to w end or start
                            elif current[0] == w[-1]:
                                current = w[:-1] + current
                                changed = True
                            elif current[0] == w[0]:
                                current = list(reversed(w[1:])) + current
                                changed = True
                            else:
                                remaining.append(w)
                        ways_dq = remaining
                    loops.append(current)

                return loops

            outer_loops = stitch_ways(outer_ways)
            inner_loops = stitch_ways(inner_ways)

            OSM_MAX_POLYGON_VERTS = 300000
            for loop in outer_loops:
                if len(loop) > OSM_MAX_POLYGON_VERTS:
                    print(f"Skipping OSM outer ring with {len(loop)} nodes (limit {OSM_MAX_POLYGON_VERTS})")
                    _progress.WarningsOverlay.add_warning("once Very large instance polygon was removed due to its complex shape", "warn")
                    continue
                multipolygon_lakes.append(loop)
            for loop in inner_loops:
                if len(loop) > OSM_MAX_POLYGON_VERTS:
                    _progress.WarningsOverlay.add_warning("once Very large instance polygon was removed due to its complex shape", "warn")
                    print(f"Skipping OSM inner ring with {len(loop)} nodes (limit {OSM_MAX_POLYGON_VERTS})")
                    continue
                multipolycon_negatives.append(loop)
    return multipolygon_lakes, multipolycon_negatives


def calculate_polygon_area_2d(coords):
    area = 0.0

    if len(coords) >= 3:

        n = len(coords)
        for i in range(n):
            x0, y0, z0 = coords[i]
            x1, y1, z1 = coords[(i + 1) % n]  # Wrap around to the first point
            area += (x0 * y1) - (x1 * y0)

    return abs(area) * 0.5

def build_osm_nodes(data):
    nodes = {}
    for element in data['elements']:
        if element['type'] == 'node':
            nodes[element['id']] = element
    return nodes

def _build_terrain_height_sampler(bvh, x_min, x_max, y_min, y_max,
                                  z_cast, z_floor, resolution=160):
    """Sample terrain height for many (x, y) points fast, via a numpy grid.

    Raycasting a BVHTree is one ray at a time (no batch API), so draping ~200k
    footprint/road vertices one-by-one costs ~30s. Instead this raycasts a
    fixed resolution x resolution grid ONCE (e.g. 160 = 25.6k rays, a few
    seconds, independent of how many buildings/roads there are) and returns a
    vectorized bilinear sampler. Every subsequent height lookup is pure numpy.

    Returns sample(px, py) -> np.ndarray of z, accepting array-likes of equal
    length. Grid cells that miss the terrain fall back to z_floor.

    The grid is a smoothed approximation of the surface (cell spacing ~ map
    width / resolution). For the small, relatively flat maps roads/buildings
    appear on this is visually indistinguishable from per-vertex raycasting;
    raise *resolution* if roads/buildings cut into or float over steep terrain.
    """
    import numpy as np
    ray_down = Vector((0, 0, -1))
    nx = ny = max(2, int(resolution))
    gxs = np.linspace(x_min, x_max, nx)
    gys = np.linspace(y_min, y_max, ny)
    grid = np.empty((ny, nx), dtype=np.float64)
    for j in range(ny):
        gy = float(gys[j])
        for i in range(nx):
            hit, _, _, _ = bvh.ray_cast(Vector((float(gxs[i]), gy, z_cast)), ray_down)
            grid[j, i] = hit.z if hit is not None else z_floor
    dx = (x_max - x_min) / (nx - 1) if nx > 1 else 1.0
    dy = (y_max - y_min) / (ny - 1) if ny > 1 else 1.0

    def sample(px, py):
        px = np.asarray(px, dtype=np.float64)
        py = np.asarray(py, dtype=np.float64)
        fx = np.clip((px - x_min) / dx, 0.0, nx - 1)
        fy = np.clip((py - y_min) / dy, 0.0, ny - 1)
        ix = np.floor(fx).astype(np.intp)
        iy = np.floor(fy).astype(np.intp)
        ix1 = np.minimum(ix + 1, nx - 1)
        iy1 = np.minimum(iy + 1, ny - 1)
        tx = fx - ix
        ty = fy - iy
        z0 = grid[iy, ix] * (1.0 - tx) + grid[iy, ix1] * tx
        z1 = grid[iy1, ix] * (1.0 - tx) + grid[iy1, ix1] * tx
        return z0 * (1.0 - ty) + z1 * ty

    return sample


def create_buildings(map, default_height=10, scaleHor=1.0):
    import numpy as np  # bundled wheel; deferred to keep module import light
    from .geo import convert_to_blender_coordinates  # deferred to avoid circular import at load time
    from .mesh_ops import recalculateNormals  # deferred to avoid circular import at load time
    from .scene import remove_objects  # deferred to avoid circular import at load time
    from .geometry2d import _earcut_triangulate  # deferred to avoid circular import at load time
    from . import geometry2d as g2d  # deferred to avoid circular import at load time
    from mathutils.bvhtree import BVHTree  # type: ignore

    # Mercator scale used by convert_to_blender_coordinates (it reads sScaleHor
    # from the scene). Read once so the vectorized node conversion matches.
    _sScaleHor = bpy.context.scene.tp3d.sScaleHor
    _t_setup = time.time()

    # Copy map and extrude vertical faces outward
    wall_obj = map.copy()
    wall_obj.data = map.data.copy()
    bpy.context.collection.objects.link(wall_obj)
    bpy.ops.object.select_all(action='DESELECT')
    wall_obj.select_set(True)
    bpy.context.view_layer.objects.active = wall_obj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='FACE')
    bpy.ops.mesh.select_all(action='DESELECT')
    bm = bmesh.from_edit_mesh(wall_obj.data)
    for f in bm.faces:
        f.select = abs(f.normal.normalized().z) < 0.1  # near-vertical faces
    bmesh.update_edit_mesh(wall_obj.data)
    bpy.ops.mesh.extrude_region_shrink_fatten(TRANSFORM_OT_shrink_fatten={"value": 20.0})
    bpy.ops.object.mode_set(mode='OBJECT')

    # Build BVH once from wall_obj, then bake a terrain heightmap grid so the
    # per-vertex drape is a vectorized numpy lookup instead of ~200k one-by-one
    # BVH raycasts (which were the ~30s cost).
    depsgraph = bpy.context.evaluated_depsgraph_get()
    eval_wall = wall_obj.evaluated_get(depsgraph)
    bm_bvh = bmesh.new()
    bm_bvh.from_mesh(eval_wall.to_mesh())
    bm_bvh.transform(wall_obj.matrix_world)
    terrain_bvh = BVHTree.FromBMesh(bm_bvh)
    bm_bvh.free()

    _ov = _progress.ProgressOverlay.get()
    if _ov.active:
        _ov.set_fetch_progress('buildings', 0.0)

    minThickness = bpy.context.scene.tp3d.minThickness

    _mc = [map.matrix_world @ Vector(c) for c in map.bound_box]
    _x_min = min(v.x for v in _mc); _x_max = max(v.x for v in _mc)
    _y_min = min(v.y for v in _mc); _y_max = max(v.y for v in _mc)
    _z_cast = max(v.z for v in _mc) + 10.0
    _sample_z = _build_terrain_height_sampler(
        terrain_bvh, _x_min, _x_max, _y_min, _y_max, _z_cast, minThickness
    )

    minLat = bpy.context.scene.tp3d.minLat
    minLon = bpy.context.scene.tp3d.minLon
    maxLat = bpy.context.scene.tp3d.maxLat
    maxLon = bpy.context.scene.tp3d.maxLon

    # Geometry for ALL buildings across every tile is accumulated here and built
    # into one mesh at the very end.
    b_verts = []
    b_faces = []
    b_height_mult = bpy.context.scene.tp3d.el_bHeightMultiplier

    # Clip footprints to the map outline in 2D so buildings never spill past the
    # map edge -- robust, unlike a 3D boolean against a non-manifold building mesh.
    map_fp = g2d.map_footprint_polygon(map)
    print(f"[TP3D buildings] setup (wall extrude + BVH + map outline) took {time.time() - _t_setup:.1f}s")
    if _ov.active:
        _ov.set_fetch_progress('buildings', 0.15)

    # Stage timers accumulated across all tiles.
    _t_fetch = 0.0
    _t_convert = 0.0
    _t_geom = 0.0

    # Cull buildings whose PRINTED footprint is too small to print cleanly. The
    # footprint coords are already in print units (same space as the map; 1 unit
    # ≈ 1 mm on the model), so the threshold is a direct printed-size cutoff. This
    # is inherently scale-aware: the same mm cutoff culls a larger real-world
    # building on a larger-km map, where everything prints smaller.
    min_area = bpy.context.scene.tp3d.el_bMinPrintMM ** 2

    def _append_building(poly, z_offset):
        # poly: shapely Polygon (single, possibly with holes). Builds a manifold
        # prism with a flat floor at the lowest terrain point under the footprint
        # and a flat roof at the highest terrain point + height.
        ext = list(poly.exterior.coords)
        if len(ext) > 1 and ext[0] == ext[-1]:
            ext = ext[:-1]
        if len(ext) < 3:
            return
        holes = []
        for interior in poly.interiors:
            ring = list(interior.coords)
            if len(ring) > 1 and ring[0] == ring[-1]:
                ring = ring[:-1]
            if len(ring) >= 3:
                holes.append(ring)
        ec = _earcut_triangulate(ext, holes)
        if ec is None:
            return
        verts2d, cap_tris = ec
        # Vectorized terrain-height lookup for every footprint vertex at once.
        _vx = [v[0] for v in verts2d]
        _vy = [v[1] for v in verts2d]
        zs = _sample_z(_vx, _vy)
        z_min = float(zs.min())
        z_top = float(zs.max()) + z_offset
        n2 = len(verts2d)
        base = len(b_verts)
        for (vx, vy) in verts2d:
            b_verts.append((vx, vy, z_min))
        for (vx, vy) in verts2d:
            b_verts.append((vx, vy, z_top))
        for (ia, ib, ic) in cap_tris:
            b_faces.append([base + ic, base + ib, base + ia])                 # floor (down)
            b_faces.append([base + n2 + ia, base + n2 + ib, base + n2 + ic])   # roof (up)
        # Walls around the exterior ring and each hole. Ring ranges follow
        # earcut's vertex order (exterior first, then holes).
        start = 0
        for ring in [ext] + holes:
            rn = len(ring)
            for i in range(rn):
                a = base + start + i
                b = base + start + (i + 1) % rn
                c = base + n2 + start + (i + 1) % rn
                d = base + n2 + start + i
                b_faces.append([a, b, c, d])
            start += rn

    lat_step = 2
    lon_step = 2


    if maxLat - minLat < lat_step:
        lat_step = maxLat - minLat
    if maxLon - minLon < lon_step:
        lon_step = maxLon - minLon

    lats = math.ceil((maxLat - minLat) / lat_step)
    lons = math.ceil((maxLon - minLon) / lon_step)


    if lats * lons < 20:
        for k in range(lats):
            for l in range(lons):
                _cntr = (k) * lons + l + 1
                _maxcntr = lats * lons
                print(f"Buildings loop: {_cntr}/{_maxcntr}")
                _ov = _progress.ProgressOverlay.get()
                if _ov.active:
                    _ov.update(message=f"Buildings: tile {_cntr}/{_maxcntr} — processing…")
                south = minLat + k * lat_step
                north = south + lat_step
                west = minLon + l * lon_step
                east = west + lon_step

                bbox = (south, west, north, east)
                data = []

                _t0 = time.time()
                data = fetch_osm_data(bbox,"BUILDINGS")
                _t_fetch += time.time() - _t0

                if not data or "elements" not in data:
                    print("No Building data returned")
                    continue

                n_buildings = len([e for e in data['elements'] if e['type'] == 'way'])
                if _ov.active:
                    _ov.update(message=f"Buildings: tile {_cntr}/{_maxcntr} — calculating {n_buildings} buildings…")
                # Cache node id -> (lat, lon) and node id -> (x, y, z_base) to avoid repeated conversions
                raw_nodes = {n['id']: (n['lat'], n['lon']) for n in data['elements'] if n['type'] == 'node'}

                # Compute 2D coordinates for every node in one vectorized numpy
                # pass instead of a per-node convert_to_blender_coordinates call
                # (each of which re-reads scene properties).
                _t0 = time.time()
                node_xy = {}
                if raw_nodes:
                    nid_list = list(raw_nodes.keys())
                    arr = np.array([raw_nodes[nid] for nid in nid_list], dtype=np.float64)  # (N, 2) lat, lon
                    xs = const.R * np.radians(arr[:, 1]) * _sScaleHor
                    ys = const.R * np.log(np.tan(np.pi / 4.0 + np.radians(arr[:, 0]) / 2.0)) * _sScaleHor
                    for nid, x, y, (nlat, nlon) in zip(nid_list, xs.tolist(), ys.tolist(), arr.tolist()):
                        node_xy[nid] = (x, y, nlat, nlon)
                _t_convert += time.time() - _t0

                def safe_float_height(h):
                    # supports strings like "10", "10.0", "10 m"
                    if h is None:
                        return float(default_height)
                    if isinstance(h, (int, float)):
                        return float(h)
                    try:
                        s = str(h).strip().lower()
                        # strip units like "m"
                        if s.endswith('m'):
                            s = s[:-1].strip()
                        return float(s)
                    except Exception:
                        return float(default_height)


                # Build a lookup for ways by id, so relations can reference them
                ways_by_id = {e['id']: e for e in data['elements'] if e['type'] == 'way'}

                relation_elements = [e for e in data['elements'] if e['type'] == 'relation']

                _t0 = time.time()
                _tile_total = max(1, len(data['elements']))
                for i,element in enumerate(data['elements']):
                    if _ov.active and i % max(1, _tile_total // 20) == 0:
                        _elem_frac = ((_cntr - 1) + i / _tile_total) / _maxcntr
                        _ov.set_fetch_progress('buildings', 0.15 + 0.60 * _elem_frac)
                    if element['type'] == 'relation':
                        # Find the outer member way and use its nodes as the footprint
                        outer_way = None
                        for member in element.get('members', []):
                            if member.get('type') == 'way' and member.get('role') == 'outer':
                                outer_way = ways_by_id.get(member['ref'])
                                if outer_way:
                                    break
                        if outer_way is None:
                            continue
                        # Treat the relation like the outer way but use relation tags if present
                        node_ids = outer_way.get('nodes', [])
                        tags = element.get('tags') or outer_way.get('tags', {})
                    elif element['type'] == 'way':
                        node_ids = element.get('nodes', [])
                        tags = element.get('tags', {})
                    else:
                        continue

                    # build 2D footprint coords from cached node_xy
                    footprint = []
                    for nid in node_ids:
                        if nid in node_xy:
                            x, y, nlat, nlon = node_xy[nid]
                            footprint.append((x, y))
                    if len(footprint) < 3:
                        continue

                    height = safe_float_height(tags.get('height', default_height))
                    levels = safe_float_height(tags.get("building:levels", 0))
                    if levels != 0:
                        height = levels * 2.7

                    z_offset = height * 0.002 * scaleHor * b_height_mult

                    # Validate the footprint and clip it to the map shape in 2D.
                    # validate() repairs self-touching OSM outlines; the clip keeps
                    # buildings from spilling past the map edge.
                    poly = g2d.xy_ring_to_polygon(footprint)
                    if poly is None:
                        continue
                    if map_fp is not None:
                        poly = g2d.validate(poly.intersection(map_fp))
                    if poly is None or poly.is_empty:
                        continue

                    # Each (clipped) polygon part becomes its own manifold prism.
                    for part in g2d.iter_polygons(poly, min_area=min_area):
                        _append_building(part, z_offset)

                _t_geom += time.time() - _t0

                if _ov.active:
                    _ov.update(message=f"Buildings: tile {_cntr}/{_maxcntr} — creating {n_buildings} buildings…")

    print(f"[TP3D buildings] fetch={_t_fetch:.1f}s  convert={_t_convert:.1f}s  "
          f"geometry(clip+earcut+raycast)={_t_geom:.1f}s")
    if _ov.active:
        _ov.set_fetch_progress('buildings', 0.75)
        _ov.update(message="Buildings: building mesh…")
    _t0 = time.time()
    remove_objects(wall_obj)

    if not b_verts:
        return None

    # Build one mesh containing every building across all tiles.
    mesh = bpy.data.meshes.new("building_mesh")
    mesh.from_pydata(b_verts, [], b_faces)
    mesh.update(calc_edges=True)

    obj = bpy.data.objects.new("Buildings", mesh)
    bpy.context.collection.objects.link(obj)

    mesh.validate(verbose=False)
    mesh.update(calc_edges=True)
    bpy.context.view_layer.update()

    if _ov.active:
        _ov.set_fetch_progress('buildings', 0.90)

    for poly in mesh.polygons:
        poly.use_smooth = False  # flat shading for buildings

    recalculateNormals(obj)
    if _ov.active:
        _ov.set_fetch_progress('buildings', 1.0)

    mat = bpy.data.materials.get("BUILDINGS")
    obj.data.materials.clear()
    obj.data.materials.append(mat)

    print(f"[TP3D buildings] final mesh build ({len(b_verts)} verts) took {time.time() - _t0:.1f}s")
    return obj

def highway_default_width(highway):
    mapping = {
        "motorway": 6.0, "trunk": 6.0, "primary": 6.0, "secondary": 6.0, "footway": 6,
        "tertiary": 6.0, "residential": 6.0, "service": 6.0, "track": 6.0, "path": 6
    }
    return mapping.get(highway, 6.0)


def create_roads(map, default_height=10, scaleHor=1.0, mapsize = 1):
    from .geo import convert_to_blender_coordinates  # deferred to avoid circular import at load time
    from .mesh_ops import extrude_plane, selectBottomFacesByZ, remeshClearing, boolean_operation  # deferred to avoid circular import at load time
    from .scene import set_origin_to_3d_cursor  # deferred to avoid circular import at load time

    minLat = bpy.context.scene.tp3d.minLat
    minLon = bpy.context.scene.tp3d.minLon
    maxLat = bpy.context.scene.tp3d.maxLat
    maxLon = bpy.context.scene.tp3d.maxLon

    streetwidthMultiplier = bpy.context.scene.tp3d.el_sMultiplier


    lat_step = 2
    lon_step = 2


    if maxLat - minLat < lat_step:
        lat_step = maxLat - minLat
    if maxLon - minLon < lon_step:
        lon_step = maxLon - minLon

    lats = math.ceil((maxLat - minLat) / lat_step)
    lons = math.ceil((maxLon - minLon) / lon_step)


    if lats * lons < 20:
        for k in range(lats):
            for l in range(lons):
                _cntr = (k) * lons + l + 1
                _maxcntr = lats * lons
                print(f"Roads loop: {_cntr}/{_maxcntr}")
                _ov = _progress.ProgressOverlay.get()
                if _ov.active:
                    _ov.update(message=f"Roads: tile {_cntr}/{_maxcntr} — processing…")
                    _ov.set_fetch_progress('roads', 0.0)

                south = minLat + k * lat_step
                north = south + lat_step
                west = minLon + l * lon_step
                east = west + lon_step

                bbox = (south, west, north, east)

                data = []

                #fetches cached data thats been downloaded by fetch_osm_combined
                data = fetch_osm_data(bbox, "STREETS")

                if not data or "elements" not in data:
                    print("No Road data returned")
                    return None

                # Build node dict (id -> (lat, lon))
                nodes = {el['id']:(el['lat'], el['lon']) for el in data['elements'] if el['type'] == "node"}
                print(f"Road nodes: {len(nodes)}")
                n_roads = len([e for e in data['elements'] if e['type'] == 'way'])
                if _ov.active:
                    _ov.update(message=f"Roads: tile {_cntr}/{_maxcntr} — calculating {n_roads} road segments…")

                # Cache converted coordinates per node id (as Vector)
                coord_cache = {}
                for nid, (lat, lon) in nodes.items():
                    x, y, z = convert_to_blender_coordinates(lat, lon, 0, scaleHor)
                    coord_cache[nid] = Vector((x, y, 0))

                wm = bpy.context.window_manager
                wm.progress_begin(0, max(1, len(data['elements'])))

                # Group geometry by width key (rounded width to avoid float equality issues)
                groups = {}
                element_count = len(data['elements'])
                any_adjusted = False

                for idx, el in enumerate(data['elements']):
                    wm.progress_update(int(idx * 100 / element_count))
                    if _ov.active and element_count > 0 and idx % max(1, element_count // 20) == 0:
                        _ov.set_fetch_progress('roads', 0.35 * idx / element_count)
                    if el['type'] != "way":
                        continue
                    node_ids = el.get("nodes", [])
                    pts = [coord_cache[nid] for nid in node_ids if nid in coord_cache]
                    if len(pts) < 2:
                        continue

                    tags = el.get("tags", {}) or {}

                    # Determine width_m (prefer explicit width, then lanes, then highway fallback)
                    width_m = None
                    if "width" in tags:
                        try:
                            s = str(tags["width"]).strip().lower()
                            if s.endswith("m"):
                                s = s[:-1]
                            width_m = float(s)
                        except Exception:
                            width_m = None
                    if width_m is None and "lanes" in tags:
                        try:
                            width_m = float(tags["lanes"]) * 3.0
                        except Exception:
                            width_m = None
                    if width_m is None:
                        width_m = highway_default_width(tags.get("highway"))
                    width_m = highway_default_width(tags.get("highway"))

                    # Grouping key -- round to millimeters to avoid tiny float diffs
                    key = round(width_m, 3)

                    if key not in groups:
                        groups[key] = {
                            'width_m': width_m,
                            'verts': [],
                            'faces': [],
                            'vert_count': 0
                        }
                    group = groups[key]

                    # Keep same streetWidth logic as before
                    streetWidth = (width_m * 0.5) * 0.2 * scaleHor * 0.02 * streetwidthMultiplier
                    if streetWidth < 0.2:
                        streetWidth = 0.2
                        any_adjusted = True

                    # Compute segment directions and per-node perpendiculars (2D perp)
                    seg_dirs = []
                    for a, b in zip(pts[:-1], pts[1:]):
                        d = (b - a)
                        if d.length == 0:
                            seg_dirs.append(Vector((0.0, 0.0, 0.0)))
                        else:
                            seg_dirs.append(d.normalized())

                    perp_at = []
                    npts = len(pts)
                    for i_pt in range(npts):
                        if i_pt == 0:
                            dir_vec = seg_dirs[0]
                        elif i_pt == npts - 1:
                            dir_vec = seg_dirs[-1]
                        else:
                            s = seg_dirs[i_pt - 1] + seg_dirs[i_pt]
                            if s.length == 0:
                                dir_vec = seg_dirs[i_pt - 1] if seg_dirs[i_pt - 1].length != 0 else seg_dirs[i_pt]
                            else:
                                dir_vec = s.normalized()
                        perp = Vector((-dir_vec.y, dir_vec.x, 0.0))
                        if perp.length != 0:
                            perp = perp.normalized()
                        perp_at.append(perp)

                    # Create left/right vertices for each node, add to group's vert list
                    idx_pairs = []
                    for p, perp in zip(pts, perp_at):
                        terrainOffsetPoint = Vector((0,0,0))
                        left = p + perp * streetWidth + terrainOffsetPoint
                        right = p - perp * streetWidth + terrainOffsetPoint
                        group['verts'].append((left.x, left.y, left.z))
                        group['verts'].append((right.x, right.y, right.z))
                        a_idx = group['vert_count']
                        b_idx = group['vert_count'] + 1
                        idx_pairs.append((a_idx, b_idx))
                        group['vert_count'] += 2

                    # Create quads between consecutive node pairs (top faces)
                    for j in range(len(idx_pairs) - 1):
                        a_left, a_right = idx_pairs[j]
                        b_left, b_right = idx_pairs[j + 1]
                        group['faces'].append((a_left, b_left, b_right, a_right))

                wm.progress_end()
                if _ov.active:
                    _ov.set_fetch_progress('roads', 0.35)

                # One-line width summary instead of per-road spam
                width_summary = ", ".join(
                    f"w{k}m={groups[k]['vert_count']//2}segs"
                    for k in sorted(groups.keys())
                )
                print(f"Road widths: {width_summary}")

                if _ov.active:
                    _ov.update(message=f"Roads: tile {_cntr}/{_maxcntr} — creating {n_roads} road mesh…")

                # Create mesh objects for each group
                created_objects = []
                for key in sorted(groups.keys()):
                    group = groups[key]
                    if not group['verts'] or not group['faces']:
                        continue

                    width_m = group['width_m']
                    streetWidth = (width_m * 0.5) * 0.2 * scaleHor * 0.02 * streetwidthMultiplier

                    mesh_name = f"Road_w{str(key).replace('.', '_')}"
                    mesh = bpy.data.meshes.new(mesh_name)
                    mesh.from_pydata(group['verts'], [], group['faces'])
                    mesh.update(calc_edges=True)

                    obj_name = f"Road_{str(key)}"
                    obj = bpy.data.objects.new(obj_name, mesh)
                    bpy.context.collection.objects.link(obj)

                    extrude_plane(obj,30)

                    # store width metadata
                    obj["width"] = width_m

                    created_objects.append(obj)

                if _ov.active:
                    _ov.set_fetch_progress('roads', 0.55)

                # If nothing was created, return None
                if not created_objects:
                    return None


                # Apply modifiers on each object (only Solidify in this case)
                # To safely apply modifiers we need each object to be active in the view layer
                original_active = bpy.context.view_layer.objects.active
                original_selection = list(bpy.context.selected_objects)

                # Deselect all first
                bpy.ops.object.select_all(action='DESELECT')



        for obj in created_objects:
            try:
                obj.select_set(True)
                bpy.context.view_layer.objects.active = obj
                # apply all modifiers present (we expect Solidify only)
                # copy list because applying modifies the collection
                mods = [m.name for m in obj.modifiers]
                for mname in mods:
                    try:
                        bpy.ops.object.modifier_apply(modifier=mname)
                    except RuntimeError as e:
                        # in some contexts modifier_apply may fail; print and continue
                        print(f"Failed to apply modifier {mname} on {obj.name}: {e}")
                obj.select_set(False)
            except Exception as e:
                print(f"Error applying modifiers on {obj.name}: {e}")

        if _ov.active:
            _ov.set_fetch_progress('roads', 0.65)
            _ov.update(message=f"Roads: Merge road segments into single object")


        # Merge (join) all created objects into a single object
        # Re-select all created_objects, set active to the first one and join
        bpy.ops.object.select_all(action='DESELECT')
        for o in created_objects:
            o.select_set(True)
        bpy.context.view_layer.objects.active = created_objects[0]

        try:
            bpy.ops.object.join()
        except Exception as e:
            print(f"Join failed: {e}")

        if _ov.active:
            _ov.set_fetch_progress('roads', 0.70)

        # The joined object is now the active object
        merged_obj = bpy.context.view_layer.objects.active

        # Shade flat and assign material
        try:
            bpy.ops.object.shade_flat()
        except Exception:
            pass

        # restore selection / active if needed (optional)
        bpy.ops.object.select_all(action='DESELECT')
        if merged_obj:
            merged_obj.select_set(True)
            bpy.context.view_layer.objects.active = merged_obj
        else:
            # restore previous state
            for o in original_selection:
                o.select_set(True)
            bpy.context.view_layer.objects.active = original_active

        roads = merged_obj

        set_origin_to_3d_cursor(roads)

        selectBottomFacesByZ(roads)
        bpy.ops.mesh.select_all(action='INVERT')
        bpy.ops.mesh.delete(type='VERT')
        bpy.ops.object.mode_set(mode='OBJECT')

        if _ov.active:
            _ov.update(message=f"Roads: Remeshing roads for clean geometry")
            _ov.set_fetch_progress('roads', 0.75)

        remeshClearing(roads, 0.2, 0, map)

        if _ov.active:
            _ov.set_fetch_progress('roads', 0.90)

        boolean_operation(roads,map,"INTERSECT")

        if _ov.active:
            _ov.set_fetch_progress('roads', 1.0)

        selectBottomFacesByZ(roads)
        bpy.ops.mesh.delete(type='VERT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.extrude_region_move(TRANSFORM_OT_translate={"value": (0, 0, default_height)})
        bpy.ops.object.mode_set(mode='OBJECT')


        if any_adjusted:
            _progress.WarningsOverlay.add_warning("Some roads were too thin and made thicker", "warn")

        return roads


def is_bbox_overlapping(obj1, obj2):
    # Get world-space corners of bounding boxes
    bbox1 = [obj1.matrix_world @ Vector(corner) for corner in obj1.bound_box]
    bbox2 = [obj2.matrix_world @ Vector(corner) for corner in obj2.bound_box]

    # Calculate Min/Max for each axis
    def get_min_max(bbox):
        return [min(c[i] for c in bbox) for i in range(3)], [max(c[i] for c in bbox) for i in range(3)]

    min1, max1 = get_min_max(bbox1)
    min2, max2 = get_min_max(bbox2)

    # Standard AABB overlap test
    return all(max1[i] >= min2[i] and max2[i] >= min1[i] for i in range(3))


def fetch_coastline_ways(prefetched_tiles, scaleHor):
    """Extract raw directed coastline way sequences from pre-fetched Overpass data.

    Returns a list of coordinate chains: each chain is a list of (x, y) tuples
    in Blender space, in OSM way direction (land-is-left convention).
    Closed ways (first node == last node) are returned as closed chains.
    No Blender objects are created.  No bpy.context reads.

    Parameters
    ----------
    prefetched_tiles : dict  {bbox -> (data_dict, from_cache_bool)}
                       The COASTLINE entry from the prefetch result dict.
    scaleHor         : float  horizontal scale factor
    """
    import math as _math
    from .. import constants as _const  # type: ignore

    def _ll_to_bl(lat, lon):
        """Inline Mercator → Blender XY, elevation fixed at 0."""
        x = _const.R * _math.radians(lon) * scaleHor
        y = _const.R * _math.log(_math.tan(_math.pi / 4 + _math.radians(lat) / 2)) * scaleHor
        return (x, y)

    chains = []
    seen_way_ids = set()

    for bbox, (data, _from_cache) in prefetched_tiles.items():
        if not data or "elements" not in data:
            continue

        nodes = {el['id']: el for el in data['elements'] if el['type'] == 'node'}

        for el in data['elements']:
            if el['type'] != 'way':
                continue
            if el['id'] in seen_way_ids:
                continue
            if el.get('tags', {}).get('natural') != 'coastline':
                continue
            seen_way_ids.add(el['id'])

            node_ids = el.get('nodes', [])
            pts = []
            for nid in node_ids:
                if nid not in nodes:
                    continue
                nd = nodes[nid]
                pts.append(_ll_to_bl(nd['lat'], nd['lon']))

            if len(pts) >= 2:
                chains.append(pts)

    return chains


