import hashlib
import json
import os
import time

import bpy  # type: ignore

from ... import constants as const
from ... import progress as _progress
from .fetch_utils import _overpass_request


def _make_cache_path(bbox, kind, settings=None):
    """Return the cache file path for a given bbox + kind + settings.

    Mirrors the key computation inside fetch_osm_data so that
    fetch_osm_combined writes to exactly the same files that fetch_osm_data
    would later read, giving a warm-cache hit.
    """
    south, west, north, east = bbox
    if settings is not None:
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

    # Keep in sync with the same gate in fetch_osm_data / _build_union_query
    # so the cache key matches whatever was actually queried.
    water_small_rivers = water_small_rivers and mapsize <= const.SMALL_RIVERS_MAXSIZE

    cache_kind = kind
    if kind == "STREETS":
        cache_kind = (
            kind
            + str(road_big)
            + str(road_med)
            + str(road_small)
            + str(exclude_alleys)
            + str(road_footways)
            + str(road_service)
        )
    elif kind == "WATER":
        cache_kind = (
            kind + str(water_ponds) + str(water_small_rivers) + str(water_big_rivers)
        )

    payload = {
        "bbox": [round(south, 7), round(west, 7), round(north, 7), round(east, 7)],
        "kind": cache_kind,
    }
    key = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
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
            filters.append(
                'way["waterway"~"stream|river|canal|ditch|drain"]["wikidata"]'
            )

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

        requested: set = set()
        if road_big:
            requested |= all_big
        if road_med:
            requested |= all_med
        if road_small:
            requested |= all_small
        if road_footways:
            requested |= all_footway
        if road_service:
            requested |= all_service

        # Apply the same mapsize performance limits as _build_streets_query
        allowed = all_big | all_med | all_small | all_footway | all_service
        if mapsize > const.ROADS_MAXSIZE:
            allowed = all_big
        elif mapsize > const.STREETS_PRIMARY_THRESHOLD:
            allowed = all_big | all_med
        elif mapsize > const.STREETS_MAJOR_ONLY_THRESHOLD:
            allowed = all_big | all_med | all_small | all_footway | all_service

        highway_types = sorted(requested & allowed) or ["motorway", "primary"]

        # Same service=* sub-tag split as _build_streets_query -- see that
        # function's comment for why length-based filtering is the wrong
        # tool here.
        regex_types = [h for h in highway_types if h != "service"]
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
                allowed: set = set()
                if settings.road_big:
                    allowed |= all_big
                if settings.road_med:
                    allowed |= all_med
                if settings.road_small:
                    allowed |= all_small
                if settings.road_footways:
                    allowed |= all_footway
                if settings.road_service:
                    allowed |= all_service
                # Clamp to mapsize-based performance limits (mirror _build_streets_query)
                if settings.mapsize > const.ROADS_MAXSIZE:
                    allowed &= all_big
                elif settings.mapsize > const.STREETS_PRIMARY_THRESHOLD:
                    allowed &= all_big | all_med
                if highway in allowed and not (
                    highway == "service"
                    and settings.exclude_alleys
                    and tags.get("service")
                    in {"alley", "driveway", "parking_aisle", "drive-through"}
                ):
                    return "STREETS"
            else:
                exclude_alleys = bool(bpy.context.scene.tp3d.el_sExcludeAlleys)
                if not (
                    highway == "service"
                    and exclude_alleys
                    and tags.get("service")
                    in {"alley", "driveway", "parking_aisle", "drive-through"}
                ):
                    return "STREETS"

    # BUILDINGS — anything with a building=* tag
    if "BUILDINGS" in active_kinds and tags.get("building"):
        return "BUILDINGS"

    # WATER — natural water bodies and waterways
    if "WATER" in active_kinds:
        natural = tags.get("natural", "")
        water = tags.get("water", "")
        waterway = tags.get("waterway", "")

        is_pond_or_lake = natural == "water" or water in {
            "river",
            "lake",
            "stream",
            "canal",
        }
        is_small_river = waterway in {"stream", "river", "canal", "ditch", "drain"}
        is_big_river = is_small_river and tags.get("wikidata")

        ponds_on = settings is None or settings.water_ponds
        small_on = settings is None or settings.water_small_rivers
        big_on = settings is not None and settings.water_big_rivers

        if (
            (is_pond_or_lake and ponds_on)
            or (is_small_river and small_on)
            or (is_big_river and big_on)
        ):
            return "WATER"

    # FOREST
    if "FOREST" in active_kinds and (
        tags.get("natural") == "wood" or tags.get("landuse") == "forest"
    ):
        return "FOREST"

    # GLACIER
    if "GLACIER" in active_kinds and tags.get("natural") == "glacier":
        return "GLACIER"

    # SCREE
    if "SCREE" in active_kinds and (
        tags.get("natural") in {"scree", "stone", "boulder", "rock", "bare_rock"}
    ):
        return "SCREE"

    # CITY — urban land use
    if "CITY" in active_kinds and (
        tags.get("landuse") in {"residential", "urban", "commercial", "industrial"}
    ):
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
    if "FARMLAND" in active_kinds and (tags.get("landuse") in {"farmland", "farmyard"}):
        return "FARMLAND"

    # COASTLINE
    if "COASTLINE" in active_kinds and tags.get("natural") == "coastline":
        return "COASTLINE"

    return None


def fetch_osm_combined(
    bbox,
    kinds,
    settings=None,
    semaphore=None,
    max_cache_age_hours=720,
    tile_progress=None,
):
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
        api_retries = settings.api_retries
    else:
        disable_cache = bpy.context.scene.tp3d.disableCache
        api_retries = bpy.context.scene.tp3d.apiRetries

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
    south = max(-90.0, min(90.0, south))
    north = max(-90.0, min(90.0, north))
    west = max(-180.0, min(180.0, west))
    east = max(-180.0, min(180.0, east))

    query = _build_union_query(south, west, north, east, missing, settings)
    if query is None:
        return result

    overpass_url = "https://overpass-api.de/api/interpreter"
    progress_suffix = (
        f"  ({tile_progress[0]}/{tile_progress[1]})" if tile_progress else ""
    )
    print(
        f"[fetch_osm_combined] requesting {missing} for bbox {(south, west, north, east)}{progress_suffix}"
    )

    # ── 3. Network request (hold semaphore only here) ─────────────────────
    if semaphore is not None:
        with semaphore:
            data = _overpass_request(
                query,
                overpass_url,
                method="POST",
                timeout=120,
                max_retries=api_retries,
            )
    else:
        data = _overpass_request(
            query,
            overpass_url,
            method="POST",
            timeout=120,
            max_retries=api_retries,
        )

    if data is None:
        _progress.WarningsOverlay.add_warning(
            f"failed to fetch {missing} elements from Overpass API", "error"
        )
        return result  # return whatever cache hits we already have

    # ── 4. Split response elements by kind ───────────────────────────────
    elements = data.get("elements", [])

    # Index all node elements by id for O(1) lookup
    all_nodes: dict = {e["id"]: e for e in elements if e.get("type") == "node"}

    # Index all way elements by id so relation member ways can be looked up
    all_ways: dict = {e["id"]: e for e in elements if e.get("type") == "way"}

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
        kind_data = per_kind[kind]
        cache_path = _make_cache_path(bbox, kind, settings)
        try:
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump(kind_data, fh)
        except OSError as exc:
            print(f"[fetch_osm_combined] cache write failed for {kind}: {exc}")
        result[kind] = (kind_data, False)

    return result
