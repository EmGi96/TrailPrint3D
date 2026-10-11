import hashlib
import json
import math
import os
import time

import bpy  # type: ignore

from ... import constants as const
from ... import progress as _progress
from ..geo import convert_to_blender_coordinates_batch
from .fetch_utils import _overpass_request


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
        road_big = settings.road_big
        road_med = settings.road_med
        road_small = settings.road_small
        water_ponds = settings.water_ponds
        water_small_rivers = settings.water_small_rivers
        water_big_rivers = settings.water_big_rivers
        exclude_alleys = settings.exclude_alleys
        road_footways = settings.road_footways
        road_service = settings.road_service
    else:
        disableCache = bpy.context.scene.tp3d.disableCache
        apiRetries = bpy.context.scene.tp3d.apiRetries
        mapsize = bpy.context.scene.tp3d.sMapInKm
        road_big = bool(bpy.context.scene.tp3d.el_sBigActive)
        road_med = bool(bpy.context.scene.tp3d.el_sMedActive)
        road_small = bool(bpy.context.scene.tp3d.el_sSmallActive)
        water_ponds = bool(bpy.context.scene.tp3d.col_wPondsActive)
        water_small_rivers = bool(bpy.context.scene.tp3d.col_wSmallRiversActive)
        water_big_rivers = bool(bpy.context.scene.tp3d.col_wBigRiversActive)
        exclude_alleys = bool(bpy.context.scene.tp3d.el_sExcludeAlleys)
        road_footways = bool(bpy.context.scene.tp3d.el_sFootwaysActive)
        road_service = bool(bpy.context.scene.tp3d.el_sServiceActive)

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
            kind
            + str(road_big)
            + str(road_med)
            + str(road_small)
            + str(exclude_alleys)
            + str(road_footways)
            + str(road_service),
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
            ],
        ),
        "STREETS": lambda s, w, n, e, mapsize=0, big=True, med=True, small=False, exclude_alleys=True, footways=False, service=False, **_: (
            _build_streets_query(
                s, w, n, e, mapsize, big, med, small, exclude_alleys, footways, service
            )
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
        if small_rivers:
            # No wikidata filter — includes all minor waterways
            filters.append('way["waterway"~"stream|river|canal|ditch|drain"]')
        elif big_rivers:
            # Only major named rivers (wikidata-tagged)
            filters.append(
                'way["waterway"~"stream|river|canal|ditch|drain"]["wikidata"]'
            )
        if big_rivers and small_rivers:
            # small_rivers already covers big ones; wikidata filter would be redundant
            pass
        if not filters:
            # Fallback: return an empty result query
            return f"{_bbox_header(s, w, n, e)};\n(  );\nout body;\n>;\nout skel qt;"
        return _simple_query(s, w, n, e, filters)

    def _build_streets_query(
        s,
        w,
        n,
        e,
        mapsize,
        big,
        med,
        small,
        exclude_alleys=True,
        footways=False,
        service=False,
    ):
        all_big = {"primary", "motorway", "primary_link", "motorway_link"}
        all_med = {
            "secondary",
            "tertiary",
            "secondary_link",
            "tertiary_link",
            "unclassified",
            "trunk",
            "trunk_link",
        }
        all_small = {"residential", "living_street"}
        all_footway = {"footway"}
        all_service = {"service"}

        # Build user-requested set. Footways and service roads are each
        # kept independent of "Small Roads" instead of bundled in: footways
        # are OSM's own separate non-vehicle category (sidewalks/paths,
        # Key:highway on the OSM wiki) and trace almost every street, while
        # service roads need their own alley/driveway/parking_aisle sub-tag
        # filtering (Key:service) that plain residential streets don't.
        requested = set()
        if big:
            requested |= all_big
        if med:
            requested |= all_med
        if small:
            requested |= all_small
        if footways:
            requested |= all_footway
        if service:
            requested |= all_service

        # Apply mapsize performance limits (larger maps = fewer road types
        # allowed). Footways/service are grouped with the "small" tier for
        # this gate -- similarly dense, so similarly expensive on a big map.
        allowed = all_big | all_med | all_small | all_footway | all_service
        if mapsize > const.ROADS_MAXSIZE:
            allowed = all_big
        elif mapsize > const.STREETS_PRIMARY_THRESHOLD:
            allowed = all_big | all_med
        elif mapsize > const.STREETS_MAJOR_ONLY_THRESHOLD:
            allowed = all_big | all_med | all_small | all_footway | all_service

        highway_types = sorted(requested & allowed)
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
        big=road_big,
        med=road_med,
        small=road_small,
        exclude_alleys=exclude_alleys,
        footways=road_footways,
        service=road_service,
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
