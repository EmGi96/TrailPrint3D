import hashlib
import json
import math
import os
import time

import bpy  # type: ignore

from ... import constants as const
from ... import progress as _progress
from ..geo import convert_to_blender_coordinates_batch
from .fetch_utils import _overpass_request, requested_highway_tags, resolve_road_tiers


def fetch_osm_data(
    bbox,
    kind="WATER",
    max_cache_age_hours=720,
    return_cache_status=False,
    settings=None,
):
    """Fetch (or return cached) OSM data for a bbox + kind.

    Parameters
    ----------
    settings : OsmFetchSettings or None
        When supplied (worker-thread path), all bpy.context reads are skipped
        and the pre-read values are used instead.  Must be None only when
        called from the main thread, where bpy.context is valid.
    """
    # print("FETCH OSM:", kind)

    if settings is not None:
        disableCache = settings.disable_cache
        apiRetries = settings.api_retries
        mapsize = settings.mapsize
        water_ponds = settings.water_ponds
        water_small_rivers = settings.water_small_rivers
        water_big_rivers = settings.water_big_rivers
        exclude_alleys = settings.exclude_alleys
    else:
        disableCache = bpy.context.scene.tp3d.disableCache
        apiRetries = bpy.context.scene.tp3d.apiRetries
        mapsize = bpy.context.scene.tp3d.sMapInKm
        water_ponds = bool(bpy.context.scene.tp3d.col_wBodiesActive)
        water_small_rivers = bool(bpy.context.scene.tp3d.col_wMinorActive)
        water_big_rivers = bool(bpy.context.scene.tp3d.col_wMajorActive)
        exclude_alleys = True
    road_tiers = resolve_road_tiers(settings)

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
            "kind": kind,
        }
        data = json.dumps(payload, sort_keys=True).encode("utf-8")
        return hashlib.sha256(data).hexdigest()

    cache_dir = get_cache_dir()
    cache_key = make_cache_key(bbox, kind)
    if kind == "STREETS":
        cache_key = make_cache_key(
            bbox,
            kind + str(sorted(road_tiers.items())) + str(exclude_alleys),
        )
    if kind == "WATER":
        cache_key = make_cache_key(
            bbox,
            kind + str(water_ponds) + str(water_small_rivers) + str(water_big_rivers),
        )
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
        "WATER": lambda s, w, n, e, ponds=True, small_rivers=True, big_rivers=True, **_: (
        _build_water_query(s, w, n, e, ponds, small_rivers, big_rivers)
        ),
        "FOREST": lambda s, w, n, e, **_: _simple_query(
            s,
            w,
            n,
            e,
            [
                'way["natural"="wood"]',
                'relation["natural"="wood"]',
                'way["landuse"="forest"]',
                'relation["landuse"="forest"]',
            ],
        ),
        "SCREE": lambda s, w, n, e, **_: _simple_query(
            s,
            w,
            n,
            e,
            [
                'nwr["natural"="scree"]',
                'nwr["natural"="stone"]',
                'nwr["natural"="boulder"]',
                'nwr["natural"="rock"]',
                'nwr["natural"="bare_rock"]',
            ],
        ),
        "CITY": lambda s, w, n, e, **_: _simple_query(
            s,
            w,
            n,
            e,
            [
                'way["landuse"~"residential|urban|commercial|industrial"]',
                'relation["landuse"~"residential|urban|commercial|industrial"]',
            ],
        ),
        "GREENSPACE": lambda s, w, n, e, **_: _simple_query(
            s,
            w,
            n,
            e,
            [
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
            ],
        ),
        "FARMLAND": lambda s, w, n, e, **_: _simple_query(
            s,
            w,
            n,
            e,
            [
                'way["landuse"="farmland"]',
                'way["landuse"="farmyard"]',
                'relation["landuse"="farmland"]',
                'relation["landuse"="farmyard"]',
            ],
        ),
        "GLACIER": lambda s, w, n, e, **_: _simple_query(
            s,
            w,
            n,
            e,
            [
                'way["natural"="glacier"]',
                'relation["natural"="glacier"]',
            ],
        ),
        "COASTLINE": lambda s, w, n, e, **_: _simple_query(
            s,
            w,
            n,
            e,
            [
                'way["natural"="coastline"]',
            ],
        ),
        "BUILDINGS": lambda s, w, n, e, **_: _simple_query(
            s,
            w,
            n,
            e,
            [
                'nwr["building"]',
                'nwr["building:part"]',
            ],
        ),
        "STREETS": lambda s, w, n, e, mapsize=0, tier_active=None, exclude_alleys=True, **_: (
            _build_streets_query(s, w, n, e, mapsize, tier_active, exclude_alleys)
        ),
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

        if big_rivers:
            filters.append('way["waterway"~"river|canal"]')

        if small_rivers:
            filters.append('way["waterway"~"stream|ditch|drain"]')

        if not filters:
            # Fallback: return an empty result query
            return f"{_bbox_header(s, w, n, e)};\n(  );\nout body;\n>;\nout skel qt;"

        return _simple_query(s, w, n, e, filters)

    def _build_streets_query(s, w, n, e, mapsize, tier_active, exclude_alleys=True):
        # tier_active is a {tier_id: bool} dict keyed by utils.osm.roads.TIER_TAGS
        # (e.g. "highways", "major", "minor", "residential", "service",
        # "footway", "cycle_bridle", "track", "path"). requested_highway_tags
        # already applies the mapsize-based performance gate (see
        # fetch_utils.allowed_road_tiers) -- dense short-segment tiers are
        # dropped above STREETS_PRIMARY_THRESHOLD, sparse long-segment tiers
        # (including Tracks) survive up to ROADS_MAXSIZE.
        highway_types = sorted(requested_highway_tags(tier_active or {}, mapsize))
        if not highway_types:
            highway_types = ["motorway", "primary"]

        # OSM's own tagging already distinguishes real back-alley/driveway
        # clutter from legitimate roads via the service=* sub-tag -- a single
        # named street is routinely split into many short `way`s at every
        # intersection, so filtering by geometric length would wrongly drop
        # real streets too (see repo memory / the 2026-07 roads rewrite).
        # Pull 'service' out of the combined regex and, when requested, add
        # it back with the noisy sub-types excluded.
        regex_types = [h for h in highway_types if h != "service"]
        filters = []
        if regex_types:
            pattern = "|".join(regex_types)
            filters.append(f'way["highway"~"^({pattern})$"]')
        if "service" in highway_types:
            if exclude_alleys:
                filters.append(
                    'way["highway"="service"]["service"!~"alley|driveway|parking_aisle|drive-through"]'
                )
            else:
                filters.append('way["highway"="service"]')
        if not filters:
            filters = ['way["highway"~"^(motorway|primary)$"]']
        print(f"Filter(s): {filters}")
        return _simple_query(s, w, n, e, filters)

    builder = OSM_QUERY_BUILDERS.get(kind)
    if builder is None:
        raise ValueError(f"Unknown OSM kind: {kind}")
    query = builder(
        south,
        west,
        north,
        east,
        mapsize=mapsize,
        tier_active=road_tiers,
        exclude_alleys=exclude_alleys,
        ponds=water_ponds,
        small_rivers=water_small_rivers,
        big_rivers=water_big_rivers,
    )

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

    print(
        f"[fetch_osm_data] {kind}: {query.splitlines()[1].strip() if len(query.splitlines()) > 1 else query[:80]}"
    )
    data = _overpass_request(
        query,
        overpass_url,
        method="POST",
        timeout=60,
        max_retries=apiRetries,
        log_callback=_log,
    )
    if data is None:
        _progress.WarningsOverlay.add_warning(
            f"failed to fetch {kind} elements from Overpass API", "error"
        )
        return None

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    return (data, False) if return_cache_status else data


def fetch_tier_polylines(
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
    tier_tags: dict[str, set[str]],
    tier_active: dict[str, bool],
    exclude_alleys: bool,
    alley_service_types: frozenset[str],
    progress_overlay=None,
    prefetched_tiles=None,
) -> dict[str, list] | None:
    """
    Fetch OSM road data over a tiled grid and bucket ways by tier.

    Returns a ``{tier: [polyline, ...]}`` dict on success.
    Returns ``None`` if any tile fetch fails — the caller should treat this as
    a hard error and abort.

    If the requested area is too large (≥ 20 tiles) the function returns an
    empty dict rather than ``None`` so the caller can decide how to handle it
    (warn, sub-tile, etc.) without crashing.
    """
    lat_step = 2.0
    lon_step = 2.0
    lat_step = min(lat_step, max_lat - min_lat)
    lon_step = min(lon_step, max_lon - min_lon)
    lats = math.ceil((max_lat - min_lat) / lat_step)
    lons = math.ceil((max_lon - min_lon) / lon_step)

    tier_polylines: dict[str, list] = {tier: [] for tier in tier_tags}

    if lats * lons >= 20:
        # Area is too large to fetch safely; return empty so the caller can warn.
        print(
            f"[TP3D roads] fetch skipped — tile count {lats * lons} exceeds limit of 20"
        )
        return tier_polylines

    for k in range(lats):
        for l in range(lons):
            _cntr = k * lons + l + 1
            _maxcntr = lats * lons
            print(f"Roads loop: {_cntr}/{_maxcntr}")
            if progress_overlay and progress_overlay.active:
                progress_overlay.update(
                    message=f"Roads: tile {_cntr}/{_maxcntr} — fetching…"
                )

            south = min_lat + k * lat_step
            north = south + lat_step
            west = min_lon + l * lon_step
            east = west + lon_step
            bbox = (south, west, north, east)

            if prefetched_tiles is not None:
                # Already fetched (and disk-cached) by the combined
                # background prefetch -- avoid re-querying Overpass.
                tile_result = prefetched_tiles.get(bbox)
                data = tile_result[0] if tile_result else None
            else:
                data = fetch_osm_data(bbox, "STREETS")
            if not data or "elements" not in data:
                print("No Road data returned")
                return None  # Hard failure — propagate upward.

            assert isinstance(data, dict)
            n_roads = len([e for e in data["elements"] if e["type"] == "way"])
            if progress_overlay and progress_overlay.active:
                progress_overlay.update(
                    message=f"Roads: tile {_cntr}/{_maxcntr} — bucketing {n_roads} ways…"
                )

            nodes = {
                el["id"]: (el["lat"], el["lon"], 0.0, None)
                for el in data["elements"]
                if el["type"] == "node"
            }
            node_ids = list(nodes.keys())
            coord_cache: dict = {}
            if node_ids:
                xyz = convert_to_blender_coordinates_batch(
                    [nodes[nid] for nid in node_ids]
                )
                coord_cache = {nid: (x, y) for nid, (x, y, _z) in zip(node_ids, xyz)}

            for el in data["elements"]:
                if el["type"] != "way":
                    continue
                tags = el.get("tags", {}) or {}
                highway = tags.get("highway", "")
                if (
                    highway == "service"
                    and exclude_alleys
                    and tags.get("service") in alley_service_types
                ):
                    continue
                tier = next(
                    (t for t, tagset in tier_tags.items() if highway in tagset),
                    None,
                )
                if tier is None or not tier_active[tier]:
                    continue
                pts = [
                    coord_cache[nid]
                    for nid in el.get("nodes", [])
                    if nid in coord_cache
                ]
                if len(pts) >= 2:
                    tier_polylines[tier].append(pts)

    return tier_polylines
