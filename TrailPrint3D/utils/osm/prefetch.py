"""Prefetch of the currently enabled OSM elements for the picker pages (map, puzzle, multi-tile).

Split in two halves so the picker's HTTP server thread never touches bpy:

* plan_prefetch()  -- MAIN THREAD ONLY. Reads scene settings and works out the
  tile bboxes / kinds / OsmFetchSettings snapshot the real generation would
  use, so the fetches below land in the very same disk-cache files.
* run_prefetch()   -- worker thread. Fetches (and disk-caches) the tiles via
  the same _fetch_all_kinds_parallel the generator itself uses, then turns
  them into lightweight, clickable features for the 2D map.
"""

import math
import threading

from ... import constants as const
from . import bbox_snap

# Feature/point budgets for the picker page -- a dense city can hold tens of
# thousands of buildings/roads; the page draws them on a canvas, but the JSON
# still has to be sane. Features beyond the cap are still generated as usual,
# they just can't be individually toggled.
MAX_FEATURES = 25000
MAX_RING_POINTS = 400

_LINE_KINDS = frozenset({"STREETS", "COASTLINE"})


# --------------------------------------------------------------------------
# Main-thread planning
# --------------------------------------------------------------------------


def _mercator(lat, lon):
    return (
        math.radians(lon),
        math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)),
    )


def _inverse_mercator(x, y):
    return (
        math.degrees(2 * math.atan(math.exp(y)) - math.pi / 2),
        math.degrees(x),
    )


def tile_bbox(bounds, shape, rotation_deg=0):
    """(min_lat, min_lon, max_lat, max_lon) of the tile the generator would
    build for a picker selection -- mirrors TP3D_OT_map_generator's
    _apply_result_body (padded generation area when shapeRotation != 0, and
    each shape's own outline extents), so this bbox matches
    compute_and_store_tile_bounds() and therefore the generator's cache keys.

    Works in unit-radius Mercator space; every shape scales linearly, so the
    result is independent of the scene's horizontal scale.
    """
    from ..primitives import (
        circle_polygon,
        hexagon_polygon,
        octagon_polygon,
        rectangle_polygon,
    )

    x1, y1 = _mercator(bounds["south"], bounds["west"])
    x2, y2 = _mercator(bounds["north"], bounds["east"])
    tile_w, tile_h = abs(x2 - x1), abs(y2 - y1)
    center_x, center_y = (x1 + x2) / 2, (y1 + y2) / 2
    diameter = max(tile_w, tile_h)

    # "geojson": the tile is the imported polygon's own bbox -- shapeRotation
    # never applies to it (see TP3D_OT_map_picker's GeoJSON branch).
    # "exact": the puzzle/multi-tile pickers send the precise area they will
    # generate, which is never rotated or reshaped.
    if rotation_deg != 0 and shape not in ("circle", "geojson", "exact"):
        rot = math.radians(rotation_deg)
        cos_r, sin_r = abs(math.cos(rot)), abs(math.sin(rot))
        gen_diameter = diameter * (cos_r + sin_r)
        gen_w = tile_w * cos_r + tile_h * sin_r
        gen_h = tile_w * sin_r + tile_h * cos_r
    else:
        gen_diameter, gen_w, gen_h = diameter, tile_w, tile_h

    if shape == "circle":
        poly = circle_polygon(diameter / 2, 64)
    elif shape == "octagon":
        poly = octagon_polygon(gen_diameter / 2)
    elif shape == "hexagon":
        poly = hexagon_polygon(gen_diameter / 2)
    elif shape in ("square", "svg"):
        # svg: the real outline is aspect-preserving inside gen_diameter, so
        # this is only an upper bound -- worst case a cache miss later.
        poly = rectangle_polygon(gen_diameter, gen_diameter)
    else:
        poly = rectangle_polygon(gen_w, gen_h)

    min_x, min_y, max_x, max_y = poly.bounds
    min_lat, min_lon = _inverse_mercator(center_x + min_x, center_y + min_y)
    max_lat, max_lon = _inverse_mercator(center_x + max_x, center_y + max_y)
    return min_lat, min_lon, max_lat, max_lon


def plan_prefetch(tp3d, payload):
    """MAIN THREAD ONLY. Turn a picker payload ({bounds, type}) into a plan
    dict for run_prefetch(), or {"error": str} if nothing can be prefetched.
    """
    from ...props import any_road_active, get_road_active
    from ..geo import haversine
    from ..ui_state import COLORING_ELEMENTS
    from .fetch_utils import OsmFetchSettings
    from .roads import TIER_TAGS

    if tp3d.elementSource != "OSM":
        return {"error": "Prefetch is only available for the OSM element source."}
    bounds = payload.get("bounds")
    if not bounds:
        return {"error": "Draw an area first."}

    shape = payload.get("type") or "rectangle"
    min_lat, min_lon, max_lat, max_lon = tile_bbox(bounds, shape, tp3d.shapeRotation)
    lat_span, lon_span = max_lat - min_lat, max_lon - min_lon
    if lat_span <= 0 or lon_span <= 0:
        return {"error": "The drawn area is empty."}

    # Same tiling as terrain_gen._rg_start_osm_prefetch / elements.py.
    lat_step = min(2.0, lat_span)
    lon_step = min(2.0, lon_span)
    tile_tasks = [
        (
            min_lat + k * lat_step,
            min_lon + l * lon_step,
            min_lat + k * lat_step + lat_step,
            min_lon + l * lon_step + lon_step,
        )
        for k in range(math.ceil(lat_span / lat_step))
        for l in range(math.ceil(lon_span / lon_step))
    ]

    # See bbox_snap: lets the generator's own (float32-jittered) tile bboxes
    # resolve to these same cache files later.
    bbox_snap.register(tile_tasks)

    map_km = haversine(min_lat, min_lon, max_lat, max_lon)
    fetch_settings = OsmFetchSettings(
        disable_cache=tp3d.disableCache,
        api_retries=tp3d.apiRetries,
        mapsize=tp3d.sMapInKm,
        road_tiers={tier: get_road_active(tp3d, tier) for tier in TIER_TAGS},
        water_ponds=bool(tp3d.show_water and tp3d.col_wBodiesActive),
        water_small_rivers=bool(tp3d.show_water and tp3d.col_wMinorActive),
        water_big_rivers=bool(tp3d.show_water and tp3d.col_wMajorActive),
        exclude_alleys=True,
    )

    kinds = []
    skipped = {}

    def consider(kind, enabled, max_size):
        if not enabled:
            return
        if map_km > max_size:
            skipped[kind] = "area too large"
        else:
            kinds.append(kind)

    for key, flag_attr, max_size, _, _ in COLORING_ELEMENTS:
        enabled = flag_attr(tp3d) if callable(flag_attr) else getattr(tp3d, flag_attr) == 1
        if key == "water":
            enabled = enabled and tp3d.show_water
        consider(key.upper(), enabled, max_size)
    consider("BUILDINGS", tp3d.el_bActive == 1, const.BUILDINGS_MAXSIZE)
    consider("STREETS", any_road_active(tp3d), const.ROADS_MAXSIZE)
    consider(
        "COASTLINE",
        tp3d.show_water and tp3d.el_oActive == 1,
        const.COASTLINE_MAXSIZE,
    )

    if not kinds:
        return {
            "error": "The area is too big to generate these elements"
            if skipped else "No elements are enabled.",
            "skipped": skipped,
        }

    return {
        "kind_tasks": [(kind, tile_tasks) for kind in kinds],
        "settings": fetch_settings,
        "skipped": skipped,
        "bbox": [min_lat, min_lon, max_lat, max_lon],
        "tiles": len(tile_tasks),
    }


# --------------------------------------------------------------------------
# Worker thread
# --------------------------------------------------------------------------


def start_prefetch_job(tp3d, request, job):
    """MAIN THREAD ONLY. Plan a picker "Prefetch" click (it reads scene
    settings), then run the fetch on a worker thread so the viewport stays
    responsive. Shared by every picker operator that serves a page with the
    Prefetch button; *job* is picker_server.prefetch_job().
    """
    plan = plan_prefetch(tp3d, request)
    if 'error' in plan:
        job.update(status='error', message=plan['error'], result=None)
        return
    job.update(status='running', message='Fetching elements…', result=None)
    threading.Thread(
        target=run_prefetch, args=(plan, job),
        daemon=True, name='tp3d-prefetch',
    ).start()


def run_prefetch(plan, job):
    """Fetch every planned kind (through the shared disk cache) and store the
    clickable features in *job* ({"status", "message", "result"}) -- see
    picker_server.set_prefetch_job for the reader side."""
    from ..terrain import _fetch_all_kinds_parallel

    try:
        job.update(status="running", message="Fetching elements…")
        # Semaphore(1): one live Overpass request at a time, like the generator.
        fetched = _fetch_all_kinds_parallel(
            plan["kind_tasks"], threading.Semaphore(1), settings=plan["settings"]
        )
        job.update(message="Building preview…")
        features, truncated = features_from_tiles(fetched)
        counts = {}
        for feature in features:
            counts[feature["kind"]] = counts.get(feature["kind"], 0) + 1
        job.update(
            status="done",
            message=f"{len(features)} elements",
            result={
                "features": features,
                "counts": counts,
                "skipped": plan["skipped"],
                "truncated": truncated,
                "bbox": plan["bbox"],
            },
        )
    except Exception as exc:  # noqa: BLE001 - reported to the page, must not kill the thread silently
        import traceback

        traceback.print_exc()
        job.update(status="error", message=f"Prefetch failed: {exc}")


def _simplify(points, tolerance):
    """Iterative Ramer-Douglas-Peucker on [lat, lon] points."""
    n = len(points)
    if n <= 2:
        return points
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        lo, hi = stack.pop()
        ax, ay = points[lo]
        bx, by = points[hi]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        far_i, far_d = -1, 0.0
        for i in range(lo + 1, hi):
            px, py = points[i]
            if length == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                d = abs(dy * (px - ax) - dx * (py - ay)) / length
            if d > far_d:
                far_i, far_d = i, d
        if far_i != -1 and far_d > tolerance:
            keep[far_i] = True
            stack.append((lo, far_i))
            stack.append((far_i, hi))
    return [p for p, k in zip(points, keep) if k]


def _clean_ring(coords, tolerance, closed):
    """(lat, lon, ele) triples -> simplified [[lat, lon], ...] at ~1 m precision."""
    pts = []
    for lat, lon, *_ in coords:
        p = [round(lat, 5), round(lon, 5)]
        if not pts or p != pts[-1]:
            pts.append(p)
    if closed and len(pts) > 1 and pts[0] == pts[-1]:
        pts.pop()
    if len(pts) > 8:
        pts = _simplify(pts, tolerance)
    if len(pts) > MAX_RING_POINTS:  # last resort for pathological rings
        step = math.ceil(len(pts) / MAX_RING_POINTS)
        pts = pts[::step]
    return pts if len(pts) >= (3 if closed else 2) else None


def features_from_tiles(fetched):
    """{kind: {bbox: (data, from_cache)}} -> ([feature, ...], truncated).

    A feature is {"id": "way/1", "kind", "line": bool, "name", "sub", "rings":
    [[[lat, lon], ...], ...]}. Relations carry every outer AND inner ring in
    one feature (the page fills them even-odd, so holes show), which makes one
    click toggle the whole OSM relation -- the same unit the generator drops.

    Road ways also carry "links": [ids of the other same-tier roads touching
    the way's start node, ids touching its end node] -- what the page's
    Alt+click uses to follow a road along until the next junction -- and
    "headings": [compass bearing (0-360, 0=north) the way points at its start
    node, same at its end node], both facing OUTWARD from that node into the
    way. At a junction (more than one same-tier road on that end), the page
    uses these to keep following onto whichever of them continues roughly
    straight ahead instead of stopping outright.
    """
    from .gen import extract_multipolygon_bodies
    from .roads import TIER_TAGS

    # raw highway=* value -> road tier id ("primary" -> "major"), for the legend.
    highway_tier = {tag: tier for tier, tags in TIER_TAGS.items() for tag in tags}

    # Road connectivity for the picker's Alt+click "follow the road" -- built
    # from the full node lists (not the simplified geometry, which can drop a
    # junction node): which OSM ways touch each node, each way's tier, and
    # each node's own coordinate (for road_links' heading calculation below).
    node_ways: dict = {}
    node_coords: dict = {}
    way_tier: dict = {}
    for tile_data, _from_cache in fetched.get("STREETS", {}).values():
        for el in tile_data.get("elements", ()):
            if el.get("type") == "node":
                node_coords[el["id"]] = (el.get("lat"), el.get("lon"))
                continue
            if el.get("type") != "way":
                continue
            tier = highway_tier.get((el.get("tags") or {}).get("highway"))
            if not tier:
                continue
            key = f"way/{el['id']}"
            way_tier[key] = tier
            for node_id in el.get("nodes", ()):
                bucket = node_ways.setdefault(node_id, [])
                if key not in bucket:
                    bucket.append(key)

    def node_heading(from_id, to_id):
        """Compass bearing (0-360) from one node to another, or None if either's
        coordinate wasn't in the fetched data (e.g. right at a tile edge)."""
        a, b = node_coords.get(from_id), node_coords.get(to_id)
        if not a or not b or a[0] is None or b[0] is None:
            return None
        phi1, phi2 = math.radians(a[0]), math.radians(b[0])
        dlon = math.radians(b[1] - a[1])
        x = math.sin(dlon) * math.cos(phi2)
        y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
        return (math.degrees(math.atan2(x, y)) + 360) % 360

    def road_links(key, way):
        """([ids at the start node, ids at the end node] of OTHER ways of the
        same tier, [heading at the start node, heading at the end node])."""
        node_ids = way.get("nodes") or ()
        if len(node_ids) < 2:
            return [[], []], [None, None]
        tier = way_tier.get(key)
        links = [
            [k for k in node_ways.get(n, ()) if k != key and way_tier.get(k) == tier]
            for n in (node_ids[0], node_ids[-1])
        ]
        headings = [
            node_heading(node_ids[0], node_ids[1]),
            node_heading(node_ids[-1], node_ids[-2]),
        ]
        return links, headings

    features = []
    seen = set()
    truncated = False

    for kind, tiles in fetched.items():
        for bbox, (data, _from_cache) in tiles.items():
            elements = data.get("elements", ())
            nodes = {e["id"]: e for e in elements if e.get("type") == "node"}
            ways = {e["id"]: e for e in elements if e.get("type") == "way"}
            member_way_ids = {
                m["ref"]
                for e in elements
                if e.get("type") == "relation"
                for m in e.get("members", ())
                if m.get("type") == "way"
            }
            span = max(bbox[2] - bbox[0], bbox[3] - bbox[1])
            tolerance = span / 4000.0

            for el in elements:
                el_type = el.get("type")
                if el_type not in ("way", "relation"):
                    continue
                key = f"{el_type}/{el['id']}"
                if key in seen:
                    continue
                tags = el.get("tags") or {}
                if el_type == "way" and not tags and el["id"] in member_way_ids:
                    continue  # skeleton way, drawn as part of its relation
                if len(features) >= MAX_FEATURES:
                    truncated = True
                    continue

                if el_type == "relation":
                    members = [
                        ways[m["ref"]]
                        for m in el.get("members", ())
                        if m.get("type") == "way" and m["ref"] in ways
                    ]
                    outer, inner = extract_multipolygon_bodies([el] + members, nodes)
                    rings = [
                        _clean_ring(r, tolerance, True) for r in list(outer) + list(inner)
                    ]
                    is_line = False
                else:
                    coords = [
                        (nodes[n]["lat"], nodes[n]["lon"])
                        for n in el.get("nodes", ())
                        if n in nodes
                    ]
                    is_line = kind in _LINE_KINDS or (
                        "waterway" in tags and tags.get("natural") != "water"
                    )
                    closed = len(coords) > 3 and coords[0] == coords[-1]
                    if not is_line and not closed:
                        continue
                    rings = [_clean_ring(coords, tolerance, not is_line)]

                rings = [r for r in rings if r]
                if not rings:
                    continue
                seen.add(key)
                feature = {
                    "id": key,
                    "kind": kind,
                    "line": is_line,
                    "name": tags.get("name", ""),
                    "sub": highway_tier.get(tags.get("highway"), "") if kind == "STREETS" else "",
                    "rings": rings,
                }
                if kind == "STREETS" and el_type == "way":
                    feature["links"], feature["headings"] = road_links(key, el)
                features.append(feature)
    return features, truncated
