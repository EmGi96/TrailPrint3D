import collections as _collections
import math
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import bmesh  # type: ignore
import bpy  # type: ignore
from mathutils import Vector  # type: ignore
from shapely import clip_by_rect
from shapely.geometry import LineString, Point, Polygon, box

from .. import progress as _progress
from .dataclasses import GenerationContext
from .geometry2d import polygonize, union_all

_COLORING_EMPTY = object()
_COLORING_PAINTED = object()
_COLORING_FILTERED = object()
# Returned by coloring_main() when elementMode == CREATE_TEXTURE.
# Carries the Shapely polygon so callers can rasterize it without building Blender geometry.
_ColoringTextureResult = _collections.namedtuple(
    "_ColoringTextureResult", ["kind", "polygon"]
)


def _edt_1d_with_index(f, np):
    """Exact squared Euclidean distance transform of a 1-D cost array
    (0 at source positions, a large sentinel elsewhere). Returns
    (squared_distance, source_index), both length len(f).

    Felzenszwalt & Huttenlocher lower-envelope-of-parabolas algorithm --
    the standard exact O(n) 1-D EDT, applied twice (once per axis) by
    _nearest_fill to build a full 2-D exact transform.
    """
    n = len(f)
    d = np.empty(n)
    src = np.empty(n, dtype=np.intp)
    v = np.empty(n, dtype=np.intp)
    z = np.empty(n + 1)
    k = 0
    v[0] = 0
    z[0] = -np.inf
    z[1] = np.inf
    for q in range(1, n):
        while True:
            vk = v[k]
            s = ((f[q] + q * q) - (f[vk] + vk * vk)) / (2 * q - 2 * vk)
            if s <= z[k]:
                k -= 1
            else:
                break
        k += 1
        v[k] = q
        z[k] = s
        z[k + 1] = np.inf
    k = 0
    for q in range(n):
        while z[k + 1] < q:
            k += 1
        vk = v[k]
        d[q] = (q - vk) ** 2 + f[vk]
        src[q] = vk
    return d, src


def _nearest_fill(grid, filled):
    """Fill grid cells where filled==False with the value of the nearest
    filled cell, via an exact separable 2-D Euclidean distance transform.

    Pass 1 runs _edt_1d_with_index down every column (nearest filled row
    within that column); pass 2 runs it across every row using pass 1's
    squared distances as the new cost function (nearest column, combining
    the vertical distance already found). The source row from pass 1 is
    then looked up at the source column pass 2 landed on to reconstruct
    the true nearest (row, col) for every cell.
    """
    import numpy as np  # type: ignore

    res = grid.shape[0]
    BIG = 1e18
    cost = np.where(filled, 0.0, BIG)

    d_col = np.empty((res, res))
    src_row = np.empty((res, res), dtype=np.intp)
    for c in range(res):
        d, src = _edt_1d_with_index(cost[:, c], np)
        d_col[:, c] = d
        src_row[:, c] = src

    src_col = np.empty((res, res), dtype=np.intp)
    for r in range(res):
        _, src = _edt_1d_with_index(d_col[r, :], np)
        src_col[r, :] = src

    rows_idx = np.arange(res)[:, None]
    final_src_row = src_row[rows_idx, src_col]
    return grid[final_src_row, src_col]


def smooth_terrain_top_z(x, y, z, iterations=2):
    """Smooth per-vertex terrain heights: rasterize -> nearest-fill ->
    box-blur -> resample, pure numpy (no external dependencies).

    The map mesh's top surface isn't a regular grid for every shape (hexagon,
    circle, etc. are fan/remesh topology, not rows/columns), so the scattered
    (x, y, z) vertex data is rasterized onto a small regular grid first, gaps
    outside the shape's footprint (but inside its bounding box) are filled
    from the nearest real value (_nearest_fill), the grid is box-blurred
    (each pass averages the 8 edge-clamped neighbours of every cell), and
    the smoothed grid is resampled back to each vertex's exact fractional
    grid position via bilinear interpolation.

    x, y, z    : 1-D numpy arrays of equal length, one entry per mesh vertex.
    iterations : number of box-blur passes -- higher smooths more aggressively.
    Returns a new Z array, same shape/order as z.
    """
    import numpy as np  # type: ignore

    n = len(z)
    if n < 4:
        return z

    x_min, x_max = x.min(), x.max()
    y_min, y_max = y.min(), y.max()
    if x_max <= x_min or y_max <= y_min:
        return z

    res = int(np.clip(round(math.sqrt(n)), 16, 256))

    col = (x - x_min) / (x_max - x_min) * (res - 1)
    row = (y - y_min) / (y_max - y_min) * (res - 1)
    col_i = np.clip(col.round().astype(np.intp), 0, res - 1)
    row_i = np.clip(row.round().astype(np.intp), 0, res - 1)

    # Rasterize: average every vertex Z that lands in each grid cell.
    sums = np.zeros((res, res), dtype=np.float64)
    counts = np.zeros((res, res), dtype=np.float64)
    np.add.at(sums, (row_i, col_i), z)
    np.add.at(counts, (row_i, col_i), 1.0)
    filled = counts > 0
    grid = np.zeros((res, res), dtype=np.float64)
    grid[filled] = sums[filled] / counts[filled]

    if not filled.all():
        grid = _nearest_fill(grid, filled)

    # Box-blur via edge-padded shifts: pad the grid by 1 cell (replicating
    # the border) so every cell's 8 neighbours are defined, then average
    # the 9 shifted copies of the grid.
    for _ in range(iterations):
        padded = np.pad(grid, 1, mode="edge")
        acc = np.zeros_like(grid)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                acc += padded[1 + dy : 1 + dy + res, 1 + dx : 1 + dx + res]
        grid = acc / 9.0

    # Resample back to each vertex's fractional grid position via bilinear
    # interpolation.
    row_c = np.clip(row, 0, res - 1)
    col_c = np.clip(col, 0, res - 1)
    r0 = np.floor(row_c).astype(np.intp)
    c0 = np.floor(col_c).astype(np.intp)
    r1 = np.clip(r0 + 1, 0, res - 1)
    c1 = np.clip(c0 + 1, 0, res - 1)
    fr = row_c - r0
    fc = col_c - c0
    top = grid[r0, c0] * (1 - fc) + grid[r0, c1] * fc
    bot = grid[r1, c0] * (1 - fc) + grid[r1, c1] * fc
    return top * (1 - fr) + bot * fr


# Material name override for kinds whose material name differs from the kind string.
KIND_MATERIAL_OVERRIDE = {
    "SCREE": "MOUNTAIN",
}


def _fetch_tiles_parallel(tasks, kind, semaphore, settings=None, max_workers=4):
    """Fetch a list of OSM tiles concurrently, honouring Overpass rate limits.

    Parameters
    ----------
    tasks      : list of (south, west, north, east) bbox tuples
    kind       : OSM feature kind string ('WATER', 'FOREST', …)
    semaphore  : threading.Semaphore — limits concurrent live requests to the
                 Overpass API (callers typically use Semaphore(1))
    settings   : OsmFetchSettings snapshot read on the main thread before this
                 function is called.  Passed through to fetch_osm_data so that
                 worker threads never touch bpy.context.
    max_workers: thread-pool size (default 4)

    Returns
    -------
    dict mapping bbox tuple -> (data_dict, from_cache_bool)
    Only tiles that fetched successfully are present in the result.

    NOTE: bpy.* calls are forbidden inside this function — it runs on worker
    threads.  All mesh-building still happens on the main thread in
    coloring_main().
    """
    from .osm.fetch_solo import fetch_osm_data  # deferred to avoid circular import

    results = {}
    lock = threading.Lock()

    def _fetch_one(bbox):
        with semaphore:
            try:
                result = fetch_osm_data(
                    bbox, kind, return_cache_status=True, settings=settings
                )
            except (OSError, ValueError, KeyError) as e:
                print(f"[_fetch_tiles_parallel] tile {bbox} failed: {e}")
                return
        if result:
            resp, from_cache = result
            if resp:
                with lock:
                    results[bbox] = (resp, from_cache)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_one, bbox): bbox for bbox in tasks}
        for fut in as_completed(futures):
            exc = fut.exception()
            if exc:
                print(f"[_fetch_tiles_parallel] worker exception: {exc}")

    return results


def _fetch_all_kinds_parallel(kind_task_pairs, semaphore, settings=None, max_workers=4):
    """Fetch all active OSM kinds — all tiles in one parallel batch.

    Each unique tile bbox is fetched with a **single** Overpass union request
    that covers every active kind for that tile.  This replaces the previous
    N-kinds × T-tiles individual request strategy and drastically reduces the
    number of concurrent Overpass connections, avoiding rate-limit errors.

    The shared *semaphore* still caps the number of live Overpass requests
    (callers use Semaphore(1)); because each tile now maps to exactly one
    request, the semaphore is acquired only during the actual network call.

    Parameters
    ----------
    kind_task_pairs : list of (kind_str, tasks_list) — one entry per active kind
    semaphore       : threading.Semaphore shared across all tile workers
    settings        : OsmFetchSettings snapshot read on the main thread.  Passed
                      through so worker threads never touch bpy.context.
    max_workers     : thread-pool size (default 4; one request per tile now)

    Returns
    -------
    dict[kind_str -> dict[bbox -> (data_dict, from_cache_bool)]]
    Kinds with no successful tiles are present as empty dicts.
    """
    from .osm.exclusions import filter_excluded
    from .osm.fetch_group import fetch_osm_combined  # deferred to avoid circular import

    # Regroup: (kind, [bboxes]) → {bbox: [kinds]} → {bbox: [kinds]}
    tile_kinds: dict = {}
    for kind, bboxes in kind_task_pairs:
        for bbox in bboxes:
            tile_kinds.setdefault(bbox, []).append(kind)

    results = {kind: {} for kind, _ in kind_task_pairs}
    lock = threading.Lock()

    total_tiles = len(tile_kinds)
    tile_counter = [0]  # mutable cell shared across worker threads, guarded by `lock`

    def _fetch_tile(bbox, kinds):
        with lock:
            tile_counter[0] += 1
            tile_progress = (tile_counter[0], total_tiles)
        # Acquire the shared semaphore before the network call (mirrors the
        # original _fetch_one pattern so the semaphore correctly caps the
        # number of concurrent live Overpass requests).
        if semaphore is not None:
            semaphore.acquire()
        try:
            tile_result = fetch_osm_combined(
                bbox, kinds, settings=settings, tile_progress=tile_progress
            )
        except (OSError, ValueError, KeyError) as e:
            print(f"[_fetch_all_kinds_parallel] tile {bbox} failed: {e}")
            return
        finally:
            if semaphore is not None:
                semaphore.release()
        with lock:
            for kind, (data, from_cache) in tile_result.items():
                if data:
                    # Drops any elements the user switched off in the map
                    # generator's prefetch preview (no-op otherwise).
                    results[kind][bbox] = (filter_excluded(data), from_cache)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_fetch_tile, bbox, kinds): bbox
            for bbox, kinds in tile_kinds.items()
        }
        for fut in as_completed(futures):
            exc = fut.exception()
            if exc:
                print(f"[_fetch_all_kinds_parallel] worker exception: {exc}")

    return results


def coloring_main(
    gen: GenerationContext, kind="WATER", prefetched_tiles=None, cutter_out=None
):
    from . import geometry2d as _g2d  # Shapely-based 2D geometry helpers
    from .geo import (
        convert_to_blender_coordinates,  # deferred to avoid circular import at load time
    )
    from .mesh_ops import (
        merge_objects,  # deferred to avoid circular import at load time
    )
    from .metadata import (
        writeMetadata,  # deferred to avoid circular import at load time
    )
    from .osm.fetch_solo import fetch_osm_data
    from .osm.gen import (  # deferred to avoid circular import at load time
        build_osm_nodes,
        extract_multipolygon_bodies,
    )
    from .scene import (
        show_message_box,  # deferred to avoid circular import at load time
    )

    _t_color = time.time()  # master timer: whole coloring_main
    _t_tiles_total = 0.0  # accumulated OSM fetch + Shapely ring building

    minLat = gen.runtime.tbMinLat
    minLon = gen.runtime.tbMinLon
    maxLat = gen.runtime.tbMaxLat
    maxLon = gen.runtime.tbMaxLon

    # Overpass returns a relation's FULL membership once any one of its ways
    # matches the bbox filter -- for a relation tagged along an entire river's
    # length (a real, documented OSM convention: "tagged as one relation with
    # no break in the middle"), that means ways tens/hundreds of km outside
    # the requested area come back too, and extract_multipolygon_bodies has no
    # way to know they're irrelevant. Clip every body/negative/ribbon polygon
    # to this query bbox right after it's built, before it can pollute the
    # union with a giant, mostly-irrelevant shape.
    _qbx1, _qby1, _ = convert_to_blender_coordinates(minLat, minLon, 0, 0)
    _qbx2, _qby2, _ = convert_to_blender_coordinates(maxLat, maxLon, 0, 0)
    # minLat/minLon/maxLat/maxLon come from the terrain mesh's own bounding
    # box (compute_and_store_tile_bounds), so this rectangle would otherwise
    # touch the terrain's true edge exactly -- a 1mm outward margin on every
    # side keeps this coarse pre-clip from ever being the binding boundary,
    # leaving the real map-shape INTERSECT (against the actual terrain mesh,
    # later) as the sole determinant of the final edge everywhere, including
    # right at a non-rectangular shape's tangent points against its own bbox.
    _qb_margin = 1.0
    _qbx_lo, _qbx_hi = min(_qbx1, _qbx2) - _qb_margin, max(_qbx1, _qbx2) + _qb_margin
    _qby_lo, _qby_hi = min(_qby1, _qby2) - _qb_margin, max(_qby1, _qby2) + _qb_margin
    _query_bbox_poly = _g2d.xy_ring_to_polygon(
        [
            (_qbx_lo, _qby_lo),
            (_qbx_hi, _qby_lo),
            (_qbx_hi, _qby_hi),
            (_qbx_lo, _qby_hi),
        ]
    )

    def _clip_to_query_bbox(poly):
        """Intersect poly with the query bbox; returns None if fully outside."""
        if poly is None or poly.is_empty or _query_bbox_poly is None:
            return poly
        clipped = poly.intersection(_query_bbox_poly)
        return clipped if not clipped.is_empty else None

    if kind == "WATER":
        col_Area = bpy.context.scene.tp3d.col_wArea
    elif kind == "FOREST":
        col_Area = bpy.context.scene.tp3d.col_fArea
    elif kind == "SCREE":
        col_Area = bpy.context.scene.tp3d.col_scrArea
    elif kind == "CITY":
        col_Area = bpy.context.scene.tp3d.col_cArea
    elif kind == "GREENSPACE":
        col_Area = bpy.context.scene.tp3d.col_grArea
    elif kind == "FARMLAND":
        col_Area = bpy.context.scene.tp3d.col_faArea
    elif kind == "GLACIER":
        col_Area = bpy.context.scene.tp3d.col_glArea
    else:
        col_Area = 0.0

    elementMode = gen.settings.elementMode
    flatten_water_top = bpy.context.scene.tp3d.col_wFlattenTop
    exportformat = "STL"
    if elementMode == "PAINT":
        exportformat = "OBJ"

    bpy.context.scene.tp3d.exportformat = exportformat
    gen.settings.exportFormat = exportformat
    map = gen.runtime.mapObject
    name = map.name

    lat_step = 2
    lon_step = 2

    waterDeleted = 0
    waterCreated = 0
    total_fetched = 0
    _api_empty = False  # set True when OSM responded with 0 usable features

    lat_step = min(lat_step, maxLat - minLat)
    lon_step = min(lon_step, maxLon - minLon)

    lats = math.ceil((maxLat - minLat) / lat_step)
    lons = math.ceil((maxLon - minLon) / lon_step)

    pos_geoms = []
    neg_geoms = []
    river_geoms = []  # WATER ribbons built from open ways (rivers/streams), tracked separately so smoothing can skip only these
    _dbg_filtered_small = []  # polygons dropped for being below col_Area (debug only)

    scaleHor = gen.runtime.sScaleHor
    streamWidthMultiplier = bpy.context.scene.tp3d.col_wStreamWidth
    half_width = 1.0 * scaleHor * 0.02 * streamWidthMultiplier

    cntr = 0
    _t_tiles_start = time.time()
    _ov = _progress.ProgressOverlay.get()

    if prefetched_tiles is not None:
        # A prefetch dataset may be tiled differently than this tile's own
        # lat_step/lon_step grid -- e.g. one combined fetch shared across
        # every physical tile of a multi-tile batch (see
        # generation/terrain_gen.py's fetch_combined_osm_data), whose bbox
        # keys don't match any single tile's own small grid cells. Iterate
        # whatever was actually fetched instead of recomputing our own grid
        # and doing an exact-bbox-tuple lookup, which would silently miss
        # everything whenever the tiling doesn't line up -- exactly how an
        # OSM element bigger than one tile (a lake, a forest, ...) could
        # vanish even though a wider fetch did see it. Each polygon is still
        # clipped to THIS tile's own query bbox below via
        # _clip_to_query_bbox, so processing extra/irrelevant cells from a
        # wider prefetch is safe -- they just clip away to nothing.
        _tile_sources = list(prefetched_tiles.items())
    elif lats * lons < 20:
        _tile_sources = [
            (
                (
                    minLat + k * lat_step,
                    minLon + l * lon_step,
                    minLat + k * lat_step + lat_step,
                    minLon + l * lon_step + lon_step,
                ),
                None,
            )
            for k in range(lats)
            for l in range(lons)
        ]
    else:
        print(f"Region too big. Cant Fetch All {kind} Sources")
        return None

    maxcntr = len(_tile_sources)
    for idx, (bbox, tile_result) in enumerate(_tile_sources, start=1):
        cntr = idx
        print(f"{kind} loop: {cntr}/{maxcntr}")
        _ov = _progress.ProgressOverlay.get()
        if _ov.active:
            if prefetched_tiles is not None:
                _ov.update(
                    message=f"{kind.capitalize()}: tile {cntr}/{maxcntr} — processing…"
                )
            else:
                _ov.update(
                    message=f"{kind.capitalize()}: tile {cntr}/{maxcntr} — fetching…"
                )
                _ov.set_fetch_progress(kind.lower(), cntr / maxcntr)

        data = []
        try:
            if prefetched_tiles is not None:
                if tile_result is None:
                    continue
                resp, from_cache = tile_result
                if not resp:
                    continue
                src = "cache" if from_cache else "Overpass"
                print(f"OSM tile ({kind}): loaded from {src} (prefetched)")
            else:
                result = fetch_osm_data(bbox, kind, return_cache_status=True)
                if not result:
                    continue
                resp, from_cache = result
                if not resp:
                    continue
                src = "cache" if from_cache else "Overpass"
                print(f"OSM tile ({kind}): loaded from {src} (on-demand)")

        except (OSError, ValueError, KeyError) as e:
            show_message_box(
                f"Something went wrong with fetching OSM data: {e}"
            )
            _progress.WarningsOverlay.add_warning(
                f"Something went wrong with fetching OSM data: {e}", "error"
            )
            continue

        data = resp
        n_features = len([e for e in data["elements"] if e["type"] == "way"])
        if _ov.active:
            src = "cached" if from_cache else "live"
            _ov.update(
                message=f"{kind.capitalize()}: tile {cntr}/{maxcntr} — calculating mesh ({n_features} features, {src})…"
            )
        nodes = build_osm_nodes(data)
        bodies, negatives = extract_multipolygon_bodies(data["elements"], nodes)
        total_fetched += n_features + len(bodies) + len(negatives)

        # Track ways already consumed by relations to avoid duplicate geometry
        relation_way_ids = set()
        for el in data["elements"]:
            if el["type"] == "relation":
                for member in el.get("members", []):
                    if member["type"] == "way":
                        relation_way_ids.add(member["ref"])

        if _ov.active:
            _ov.update(
                message=f"{kind.capitalize()}: tile {cntr}/{maxcntr} — building geometry"
            )

        # Build Shapely polygons from relation outer rings
        for coords in bodies:
            xy = [
                (x, y)
                for x, y, _ in (
                    convert_to_blender_coordinates(lat, lon, ele, 0)
                    for lat, lon, ele in coords
                )
            ]
            poly = _g2d.xy_ring_to_polygon(xy)
            poly = _clip_to_query_bbox(poly)
            if poly is not None and not poly.is_empty:
                pos_geoms.append(poly)
                waterCreated += 1
            else:
                waterDeleted += 1

        # Build Shapely polygons from relation inner rings (negatives / holes)
        for coords in negatives:
            xy = [
                (x, y)
                for x, y, _ in (
                    convert_to_blender_coordinates(lat, lon, ele, 0)
                    for lat, lon, ele in coords
                )
            ]
            poly = _g2d.xy_ring_to_polygon(xy)
            poly = _clip_to_query_bbox(poly)
            if poly is not None and not poly.is_empty and poly.area >= col_Area:
                neg_geoms.append(poly)
                waterCreated += 1
            else:
                if bpy.app.debug and poly is not None and not poly.is_empty:
                    _dbg_filtered_small.append(poly)
                waterDeleted += 1

        # Process standalone ways: closed → polygon, open → buffered ribbon
        for element in data["elements"]:
            if element["type"] != "way":
                waterDeleted += 1
                continue
            if element["id"] in relation_way_ids:
                continue  # already consumed by a relation

            coords = []
            for node_id in element.get("nodes", []):
                if node_id in nodes:
                    node = nodes[node_id]
                    coords.append(
                        convert_to_blender_coordinates(
                            node["lat"], node["lon"], 0, 0
                        )
                    )
            if len(coords) < 2:
                waterDeleted += 1
                continue

            if coords[0] == coords[-1]:
                xy = [(x, y) for x, y, _ in coords]
                poly = _g2d.xy_ring_to_polygon(xy)
                poly = _clip_to_query_bbox(poly)
                if poly is not None and not poly.is_empty:
                    pos_geoms.append(poly)
                    waterCreated += 1
                else:
                    waterDeleted += 1
            else:
                xy = [(x, y) for x, y, _ in coords]
                ribbon = _g2d.line_to_ribbon(xy, half_width)
                ribbon = _clip_to_query_bbox(ribbon)
                if ribbon is not None and not ribbon.is_empty:
                    pos_geoms.append(ribbon)
                    if kind == "WATER":
                        river_geoms.append(ribbon)
                    waterCreated += 1
                else:
                    waterDeleted += 1

        if not from_cache and prefetched_tiles is None:
            time.sleep(
                5
            )  # Pause to prevent request throttling (skipped when worker pre-fetched)

    _t_tiles_total = time.time() - _t_tiles_start
    print(
        f"  [coloring_main] tile fetch + ring build ({kind}): {_t_tiles_total:.3f}s  "
        f"(includes Overpass throttle sleeps)  pos={len(pos_geoms)}  neg={len(neg_geoms)}"
    )

    if cntr < maxcntr:
        print("Not All data fetched")
        pos_geoms.clear()
        neg_geoms.clear()
        print("Timed out. Cached already Fetched Data. Try Regenerating Again")
    else:
        if total_fetched == 0:
            _progress.WarningsOverlay.add_warning(
                f"No {kind.capitalize()} elements returned from API.", "warn"
            )
            _api_empty = True
        elif waterCreated == 0:
            _progress.WarningsOverlay.add_warning(
                f"All {kind.capitalize()} elements are below the area threshold.",
                "warn",
            )
            _api_empty = True

    def _split_loose(obj):
        """Split obj into per-connected-component objects using Blender's native C
        mesh-separate operator (orders-of-magnitude faster than a Python DFS on
        large post-boolean meshes).  Returns a list that includes obj itself (which
        retains one component) plus any newly created objects for additional
        components.  Empty objects are excluded."""
        before = set(bpy.data.objects)
        bpy.ops.object.select_all(action="DESELECT")
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.separate(type="LOOSE")
        bpy.ops.object.mode_set(mode="OBJECT")
        after = set(bpy.data.objects)
        parts = list(after - before) + [obj]
        return [o for o in parts if o.data and len(o.data.vertices) > 0]

    # ── Shapely: union all → subtract negatives → area-filter → ONE mesh ────────────
    _t_shapely = time.time()
    _smooth_r = int(gen.elevation.el_Smoothing * 20)
    merged_pos = _g2d.union(pos_geoms)
    merged_neg = _g2d.union(neg_geoms)
    final_geom = _g2d.subtract(merged_pos, merged_neg)
    # Clip to the map outline so boundary vertices land exactly on mapOutline.boundary
    # before smoothing — the Taubin pin check only works when vertices are precisely on
    # that boundary, which the query-bbox clip above doesn't guarantee for non-rect maps.
    # mapOutline is in local space (origin-centered); translate to absolute Mercator so
    # it matches the coordinate space of final_geom (same space as convert_to_blender_coordinates).
    if (
        gen.runtime.mapOutline is not None
        and gen.runtime.mapObject is not None
        and final_geom is not None
        and not final_geom.is_empty
    ):
        from shapely.affinity import translate as _shp_translate

        _map_outline_abs = _shp_translate(
            gen.runtime.mapOutline,
            xoff=gen.runtime.mapObject.location.x,
            yoff=gen.runtime.mapObject.location.y,
        )
        _clipped = final_geom.intersection(_map_outline_abs)
        if _clipped is not None and not _clipped.is_empty:
            final_geom = _g2d.validate(_clipped)
    _pre_smooth_geom = final_geom if bpy.app.debug else None
    # Rivers (ribbons from open ways) keep their Taubin-unsmoothed shape; lakes/ponds
    # and every other kind still get smoothed as before.
    _river_union = _g2d.union(river_geoms) if river_geoms else None
    if _smooth_r > 0:
        if _river_union is not None and not _river_union.is_empty:
            _to_smooth = _g2d.validate(final_geom.difference(_river_union))
            _river_part = _g2d.validate(final_geom.intersection(_river_union))
        else:
            _to_smooth = final_geom
            _river_part = None

        _smoothed_result = None
        if _to_smooth is not None and not _to_smooth.is_empty:
            smoothed_geom = _g2d.smooth_polygon_taubin(
                gen, _to_smooth, steps=_smooth_r, debug_name=kind.lower()
            )
            print(f"  [smoothing steps] Taubin smoothing steps={_smooth_r}  ")
            # force=True per-polygon before union: splits self-touching rings (figure-8
            # pinch points from Taubin) without touching already-valid unrelated polygons.
            _smooth_parts = [
                _g2d.validate(p, force=True) for p in _g2d.iter_polygons(smoothed_geom)
            ]
            union_smoothed = _g2d.union(
                [p for p in _smooth_parts if p is not None and not p.is_empty]
            )
            _smoothed_result = _g2d.validate(union_smoothed)

        _rejoin_parts = [
            p
            for p in (_smoothed_result, _river_part)
            if p is not None and not p.is_empty
        ]
        if _rejoin_parts:
            final_geom = _g2d.validate(_g2d.union(_rejoin_parts))
    print(
        f"  [coloring_main] Shapely union+subtract ({kind}): {time.time() - _t_shapely:.3f}s  pos={len(pos_geoms)}  neg={len(neg_geoms)}"
    )

    # DEBUG: dump the exact Shapely geometry at each stage as stacked wireframes so
    # the raw rings (incl. self-intersections / slivers) can be inspected directly.
    if bpy.app.debug:
        _dbg = f"TP3D_Debug_{kind}"
        _g2d.debug_dump(f"DBG_{kind}_1_raw_pos", pos_geoms, _dbg, z=0.0)
        _g2d.debug_dump(f"DBG_{kind}_2_raw_neg", neg_geoms, _dbg, z=20.0)
        _g2d.debug_dump(f"DBG_{kind}_3_merged_pos", merged_pos, _dbg, z=40.0)
        _g2d.debug_dump(f"DBG_{kind}_4_merged_neg", merged_neg, _dbg, z=60.0)
        _g2d.debug_dump(f"DBG_{kind}_5_final", final_geom, _dbg, z=80.0)
        print(f"  [coloring_main] DEBUG wireframes dumped to collection '{_dbg}'")

    if final_geom is None or final_geom.is_empty:
        if _api_empty:
            return _COLORING_EMPTY
        _progress.WarningsOverlay.add_warning(
            f"All {kind.capitalize()} objects were filtered out due to their size",
            "warn",
        )
        return _COLORING_FILTERED

    if gen.texture.useTexture:
        if col_Area > 0:
            filtered_parts = [
                p for p in _g2d.iter_polygons(final_geom, min_area=col_Area)
            ]
            final_geom = _g2d.union(filtered_parts) if filtered_parts else final_geom
        return _ColoringTextureResult(kind=kind, polygon=final_geom)

    # Smooth raw OSM GPS-traced nodes so extruded solids have clean edges.
    _simplified = final_geom.simplify(0.075, preserve_topology=True)
    _simplified = _g2d.validate(_simplified)
    if _simplified is not None and not _simplified.is_empty:
        final_geom = _simplified

    _t_mesh = time.time()
    result_meshes = []
    _dbg_kept = []  # area-filtered polygons that actually become meshes (debug)
    for i, poly in enumerate(_g2d.iter_polygons(final_geom, min_area=col_Area)):
        if bpy.app.debug:
            _dbg_kept.append(poly)
        m = _g2d.polygon_to_mesh(f"{kind}_{i}", poly)
        if m is not None:
            result_meshes.append(m)
    if bpy.app.debug:
        for poly in _g2d.iter_polygons(final_geom):
            if poly.area < col_Area:
                _dbg_filtered_small.append(poly)
    print(
        f"  [coloring_main] polygon_to_mesh ({kind}, {len(result_meshes)} parts): {time.time() - _t_mesh:.3f}s"
    )

    if bpy.app.debug and _dbg_kept:
        _g2d.debug_dump(
            f"DBG_{kind}_6_kept_polys", _dbg_kept, f"TP3D_Debug_{kind}", z=100.0
        )
    if bpy.app.debug and _dbg_filtered_small:
        _g2d.debug_dump(
            f"DBG_{kind}_7_filtered_small",
            _dbg_filtered_small,
            f"TP3D_Debug_{kind}",
            z=120.0,
        )

    if not result_meshes:
        if _api_empty:
            return _COLORING_EMPTY
        return _COLORING_FILTERED

    merged_object = (
        merge_objects(result_meshes) if len(result_meshes) > 1 else result_meshes[0]
    )
    if merged_object is None:
        return None

    # Shift vertex coords so the object origin sits at the 3D cursor.
    # Keeps vertex coordinates close to zero and avoids float32 precision
    # artifacts when the map is far from the world origin.
    import numpy as _np  # type: ignore

    _cursor = bpy.context.scene.cursor.location.copy()
    _me = merged_object.data
    _co = _np.empty(len(_me.vertices) * 3, dtype=_np.float32)
    _me.vertices.foreach_get("co", _co)
    _co = _co.reshape(-1, 3)
    _co -= (_cursor.x, _cursor.y, _cursor.z)
    _me.vertices.foreach_set("co", _co.ravel())
    _me.update()
    merged_object.location = _cursor

    # DEBUG: dump the flat (pre-extrusion) polygon mesh so the normal direction
    # and winding of each island can be inspected before any further processing.
    if bpy.app.debug:
        _dbg_flat_coll = f"TP3D_Debug_{kind}"
        _dbg_flat_copy = merged_object.copy()
        _dbg_flat_copy.data = merged_object.data.copy()
        _dbg_flat_copy.name = f"DBG_{kind}_8_flat_mesh"
        _dbg_flat_copy.location = merged_object.location.copy()
        _dbg_flat_copy.location.z += 140.0
        _coll = bpy.data.collections.get(_dbg_flat_coll)
        if _coll is None:
            _coll = bpy.data.collections.new(_dbg_flat_coll)
            bpy.context.scene.collection.children.link(_coll)
        _coll.objects.link(_dbg_flat_copy)
        print(
            f"  [coloring_main] DEBUG flat mesh dumped as '{_dbg_flat_copy.name}' (z+140)"
        )

    # Tessellate_polygon produces upward-facing normals (CCW Shapely exterior → Z-up).
    # Flip them downward so the extruded prism intersects the terrain correctly.
    # NOTE: do NOT run remove_doubles here — merging near-coincident vertices from
    # different polygon parts creates pinch-point non-manifold verts, which is worse
    # than leaving them as separate topological components. Each polygon mesh is
    # already cleaned internally inside polygon_to_mesh.

    # Remove sliver faces before extrusion: Taubin smoothing + clipping-to-original
    # can produce a thin triangle at a rounded corner where two adjacent boundary
    # vertices each gain a spurious third boundary edge. Every such vertex must have
    # exactly 2 boundary edges in a clean polygon; if it has ≥3, the minimum-area
    # face at that vertex is the sliver and should be dissolved.
    _bm_sv = bmesh.new()
    _bm_sv.from_mesh(merged_object.data)
    _bm_sv.edges.ensure_lookup_table()
    _sliver_pass = True
    _slivers_dissolved = 0
    while _sliver_pass:
        _sliver_pass = False
        for _sv in _bm_sv.verts:
            _bnd = [_e for _e in _sv.link_edges if len(_e.link_faces) == 1]
            if len(_bnd) >= 3 and _sv.link_faces:
                _worst = min(_sv.link_faces, key=lambda f: f.calc_area())
                bmesh.ops.delete(_bm_sv, geom=[_worst], context="FACES")
                _bm_sv.edges.ensure_lookup_table()
                _slivers_dissolved += 1
                _sliver_pass = True
                break
    if _slivers_dissolved:
        print(
            f"  [sliver-fix] ({kind}) dissolved {_slivers_dissolved} sliver face(s) at boundary-branching vertices"
        )
    _bm_sv.to_mesh(merged_object.data)
    _bm_sv.free()

    bm = bmesh.new()
    bm.from_mesh(merged_object.data)
    bm.normal_update()
    UP = Vector((0, 0, 1))
    faces_to_flip = [f for f in bm.faces if f.normal.dot(UP) > 0]
    if faces_to_flip:
        bmesh.ops.reverse_faces(bm, faces=faces_to_flip)
    bm.to_mesh(merged_object.data)
    bm.free()

    if _ov.active:
        _ov.update(message=f"{kind.capitalize()}: extrude and boolean with map")

    if elementMode == "PAINT":
        # ── PAINT fast path ──────────────────────────────────────────────────────────
        map_world_verts = [map.matrix_world @ Vector(v) for v in map.bound_box]
        terrain_max_z = max(v.z for v in map_world_verts)
        extrude_z = terrain_max_z + 50.0
        print(
            f"  [PAINT fast path] terrain_max_z={terrain_max_z:.2f}  extrude_z={extrude_z:.2f}"
        )

        mesh = merged_object.data
        bm = bmesh.new()
        bm.from_mesh(mesh)
        if not bm.faces:
            bm.free()
            bpy.data.objects.remove(merged_object, do_unlink=True)
            if _api_empty:
                return _COLORING_EMPTY
            return None
        geom = bm.faces[:]
        ret = bmesh.ops.extrude_face_region(bm, geom=geom)
        extruded_verts = [v for v in ret["geom"] if isinstance(v, bmesh.types.BMVert)]
        bmesh.ops.translate(bm, verts=extruded_verts, vec=Vector((0, 0, extrude_z)))
        bm.to_mesh(mesh)
        bm.free()

        merged_object.name = name + "_" + kind
        writeMetadata(merged_object, kind)
        mat = bpy.data.materials.get(KIND_MATERIAL_OVERRIDE.get(kind, kind))
        merged_object.data.materials.clear()
        merged_object.data.materials.append(mat)

        if _ov.active:
            _ov.update(message=f"{kind.capitalize()}: painting terrain faces")

        _bm_mbd = bmesh.new()
        _bm_mbd.from_mesh(merged_object.data)
        bmesh.ops.remove_doubles(_bm_mbd, verts=_bm_mbd.verts[:], dist=1e-6)
        _bm_mbd.to_mesh(merged_object.data)
        _bm_mbd.free()

        _t_paint = time.time()

        color_map_faces_by_terrain(map, merged_object)

        print(f"PAINTING ({kind})")
        if bpy.app.debug:
            merged_object.location.x += 500.0
            coll = bpy.data.collections.get("TP3D_Debug")
            if coll is None:
                coll = bpy.data.collections.new("TP3D_Debug")
                bpy.context.scene.collection.children.link(coll)
            for c in list(merged_object.users_collection):
                c.objects.unlink(merged_object)
            coll.objects.link(merged_object)
        else:
            mesh_data = merged_object.data
            bpy.data.objects.remove(merged_object, do_unlink=True)
            bpy.data.meshes.remove(mesh_data)
        print(f"  [coloring_main] PAINT total ({kind}): {time.time() - _t_paint:.3f}s")
        print(f"  [coloring_main] TOTAL ({kind}, PAINT): {time.time() - _t_color:.3f}s")
        return _COLORING_PAINTED
        # ── end PAINT fast path ───────────────────────────────────────────────────────

    # ── SINGLECOLORMODE path ──────────────────────────────────────────────
    # Extrude the unified flat mesh, run ONE MANIFOLD boolean-intersect with terrain,
    # then split loose parts (terrain edges can disconnect components) and re-merge.
    tol = 0.1
    DOWN = Vector((0, 0, -1))
    _t_proc = time.time()

    bm = bmesh.new()
    bm.from_mesh(merged_object.data)
    if not bm.faces:
        bm.free()
        bpy.data.objects.remove(merged_object, do_unlink=True)
        return None
    geom = bm.faces[:]
    ret = bmesh.ops.extrude_face_region(bm, geom=geom)
    extruded_verts = [v for v in ret["geom"] if isinstance(v, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, verts=extruded_verts, vec=Vector((0, 0, 200)))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(merged_object.data)
    bm.free()
    merged_object.location.z -= 1

    # SEPARATE mode wants to later cut this same footprint out of the
    # terrain too (see separate_mode_recess_cutter_from_prism in
    # mesh_ops.py). Hand the caller a copy of this tall prism BEFORE the
    # upcoming INTERSECT mutates merged_object -- INTERSECT(map, prism) and
    # DIFFERENCE(map, prism) are complementary halves of the same boolean
    # computation against the same two meshes, so reusing this exact prism
    # (instead of re-deriving a boundary from the already-intersected result)
    # keeps the element's shape and the terrain recess bit-consistent at the
    # map's outer edge, with no coincident-face precision drift.
    if elementMode == "SEPARATE" and cutter_out is not None:
        _prism = merged_object.copy()
        _prism.data = merged_object.data.copy()
        _prism.name = f"{name}_{kind}_prism"
        bpy.context.collection.objects.link(_prism)
        cutter_out["prism"] = _prism

    _t_bool = time.time()

    # ── Pre-boolean manifold diagnostics ────────────────────────────────────

    cutter_nm_v, cutter_nm_e = _count_non_manifold(merged_object)
    map_nm_v, map_nm_e = _count_non_manifold(map)
    print(
        f"  [manifold-check] ({kind}) cutter: {len(merged_object.data.vertices)}v "
        f"non-manifold={cutter_nm_v}v/{cutter_nm_e}e  |  "
        f"map: {len(map.data.vertices)}v non-manifold={map_nm_v}v/{map_nm_e}e"
    )
    if cutter_nm_v > 0 or cutter_nm_e > 0:
        print(
            "  [manifold-check] WARNING: cutter has non-manifold geometry — "
            "boolean may be a no-op or produce garbage"
        )
    # ────────────────────────────────────────────────────────────────────────

    def _apply_boolean(obj, solver):
        mod = obj.modifiers.new(name="Boolean", type="BOOLEAN")
        mod.object = map
        mod.operation = "INTERSECT"
        mod.solver = solver
        dg = bpy.context.evaluated_depsgraph_get()
        result = bpy.data.meshes.new_from_object(obj.evaluated_get(dg))
        obj.modifiers.clear()
        return result

    new_mesh = _apply_boolean(merged_object, "MANIFOLD")
    solver_used = "MANIFOLD"
    result_zs: list = []

    if new_mesh.vertices:
        result_zs = [v.co.z for v in new_mesh.vertices]
        z_max = max(result_zs)
    else:
        z_max = 201.0  # treat empty as no-op

    if z_max > 150:
        # MANIFOLD refused / no-op'd (residual non-manifold cutter) — fall back to
        # the EXACT solver, which tolerates non-manifold inputs. Never FLOAT: it
        # produces self-intersecting garbage with hundreds of spurious loose parts.
        bpy.data.meshes.remove(new_mesh)
        new_mesh = _apply_boolean(merged_object, "EXACT")
        solver_used = "EXACT (fallback)"
        if new_mesh.vertices:
            result_zs = [v.co.z for v in new_mesh.vertices]
            z_max = max(result_zs)
        else:
            z_max = 201.0

    old_mesh = merged_object.data
    merged_object.data = new_mesh
    bpy.data.meshes.remove(old_mesh)

    if new_mesh.vertices:
        print(
            f"  [coloring_main] boolean INTERSECT {solver_used} ({kind}): {time.time() - _t_bool:.3f}s"
            f"  verts={len(new_mesh.vertices)}  z=[{min(result_zs):.2f}, {z_max:.2f}]"
        )
        if z_max > 150:
            print(
                f"  [manifold-check] WARNING: EXACT fallback also failed — z_max={z_max:.1f}"
            )
    else:
        print(
            f"  [coloring_main] boolean ({kind}): {time.time() - _t_bool:.3f}s  verts=0"
        )

    if not new_mesh.vertices:
        bpy.data.objects.remove(merged_object, do_unlink=True)
        return None

    # Split loose parts and fix normals on each component.
    _t_split = time.time()
    surviving = []
    for zobj in _split_loose(merged_object):
        zmesh = zobj.data
        bm = bmesh.new()
        bm.from_mesh(zmesh)
        bm.normal_update()

        # Drop fragments that the boolean-intersection clipped below the
        # per-element area threshold. These appear when a large polygon is
        # sliced at the map boundary, leaving slivers that individually
        # fall below col_Area. Without this filter they become tiny cutters
        # that punch unwanted holes in lower-priority elements.
        fp = _g2d.footprint_with_holes(zobj)
        if fp is None or fp.area < col_Area:
            if bpy.app.debug and fp is not None:
                _dbg_filtered_small.append(fp)
            bm.free()
            bpy.data.objects.remove(zobj, do_unlink=True)
            continue

        lowest_face = None
        lowest_z = float("inf")
        for face in bm.faces:
            z = face.calc_center_median().z
            if z < lowest_z and face.calc_area() > 0:
                lowest_z = z
                lowest_face = face
        if lowest_face and lowest_face.normal.dot(DOWN) <= 0:
            bmesh.ops.reverse_faces(bm, faces=bm.faces[:])

        if kind == "WATER" and elementMode != "PAINT" and flatten_water_top:
            # Flatten this water body's top (terrain-conforming) surface to its
            # own median height, giving it a flat bottom instead of following
            # every terrain bump. Applies in both SEPARATE (flows straight into
            # the top-face extrusion below) and SINGLECOLORMODE* (flattens the
            # visible top surface ahead of that branch's separate bottom-leveling).
            bm.normal_update()
            top_verts = {v for f in bm.faces if f.normal.z > 0.087 for v in f.verts}
            if top_verts:
                median_z = statistics.median(v.co.z for v in top_verts)
                for v in top_verts:
                    v.co.z = median_z

        bm.to_mesh(zmesh)
        bm.free()
        surviving.append(zobj)
    print(
        f"  [coloring_main] split_loose ({kind}): {time.time() - _t_split:.3f}s  parts={len(surviving)}"
    )
    print(
        f"  [coloring_main] solid build total ({kind}, {elementMode}): {time.time() - _t_proc:.3f}s"
    )

    if not surviving:
        return None

    _t_merge = time.time()
    merged_object = merge_objects(surviving) if len(surviving) > 1 else surviving[0]
    print(f"  [coloring_main] merge_objects ({kind}): {time.time() - _t_merge:.3f}s")

    if merged_object is None:
        return None

    bpy.ops.object.origin_set(type="ORIGIN_CURSOR", center="MEDIAN")

    bm = bmesh.new()
    bm.from_mesh(merged_object.data)
    bm.normal_update()

    # SINGLECOLORMODE: flatten the bottom to a consistent level so the cutter
    # prism extends cleanly below the lowest terrain point in the element area.
    min_z = min(v.co.z for v in bm.verts)
    lowestVert = 100
    for v in bm.verts:
        if abs(v.co.z - min_z) > tol and v.co.z >= bpy.context.scene.tp3d.minThickness:
            lowestVert = min(lowestVert, v.co.z)
    for v in bm.verts:
        if abs(v.co.z - min_z) < tol:
            v.co.z = lowestVert - 1

    bm.to_mesh(merged_object.data)
    bm.free()

    # Set the active object before switching modes -- mode_set requires one,
    # and whatever was active from the split_loose/merge_objects steps above
    # can be a dangling reference (e.g. the last col_Area-filtered-out object
    # that got deleted), which makes this poll() fail with "Context missing
    # active object" whenever exactly one water/element body survives.
    bpy.context.view_layer.objects.active = merged_object
    bpy.ops.object.mode_set(mode="OBJECT")

    if elementMode == "SINGLECOLORMODE":
        merged_object.location.z += 0.2
    merged_object.name = name + "_" + kind

    merged_object.select_set(True)

    writeMetadata(merged_object, kind)
    mat = bpy.data.materials.get(KIND_MATERIAL_OVERRIDE.get(kind, kind))
    merged_object.data.materials.clear()
    merged_object.data.materials.append(mat)

    bpy.context.preferences.edit.use_global_undo = True
    print(
        f"  [coloring_main] TOTAL ({kind}, {elementMode}): {time.time() - _t_color:.3f}s"
    )
    return merged_object


def _count_non_manifold(obj):
    bm_d = bmesh.new()
    bm_d.from_mesh(obj.data)
    bm_d.verts.ensure_lookup_table()
    bm_d.edges.ensure_lookup_table()
    nm_verts = sum(1 for v in bm_d.verts if not v.is_manifold)
    nm_edges = sum(1 for e in bm_d.edges if not e.is_manifold)
    bm_d.free()
    return nm_verts, nm_edges


def color_map_faces_by_terrain(map_obj, terrain_obj, up_threshold=0.05):
    """
    Colors faces of map_obj that fall inside the 2D XY footprint of terrain_obj.

    Builds a Shapely polygon from the terrain object's XY projection and checks
    each upward-facing map face center against it — no BVH or ray casting involved.

    up_threshold = dot(normal, Z) must be greater than this value.
    """
    from . import geometry2d as _g2d
    from .mesh_ops import (
        recalculateNormals,  # deferred to avoid circular import at load time
    )

    if map_obj.type != "MESH" or terrain_obj.type != "MESH":
        print("Both inputs must be mesh objects.")
        return

    recalculateNormals(map_obj)

    _t_footprint = time.time()
    footprint = _g2d.footprint_with_holes(terrain_obj)
    if footprint is None or footprint.is_empty:
        print("  [color_faces] terrain footprint is empty — nothing to paint")
        return
    print(f"  [color_faces] footprint build: {time.time() - _t_footprint:.3f}s")

    prepared = _g2d.prep(footprint)

    map_mesh = map_obj.data
    bm = bmesh.new()
    bm.from_mesh(map_mesh)
    bm.faces.ensure_lookup_table()
    mw_map = map_obj.matrix_world

    # Get or create a material for terrain color
    if terrain_obj.active_material:
        mat = terrain_obj.active_material
    else:
        mat = bpy.data.materials.new(name="TerrainColor")
        terrain_obj.data.materials.append(mat)

    if mat.name not in [m.name for m in map_mesh.materials if m is not None]:
        map_mesh.materials.append(mat)
    mat_index = map_mesh.materials.find(mat.name)

    up = Vector((0, 0, 1))
    colored_count = 0

    _t_loop = time.time()
    i = 0
    for i, f in enumerate(bm.faces):
        if f.normal.normalized().dot(up) > up_threshold:
            center = mw_map @ f.calc_center_median()
            if prepared.contains(_g2d.Point(center.x, center.y)):
                f.material_index = mat_index
                colored_count += 1
    print(
        f"  [color_faces] loop: {time.time() - _t_loop:.3f}s  ({i + 1} faces checked, {colored_count} colored)"
    )

    bm.to_mesh(map_mesh)
    bm.free()
    print(
        f"Colored {colored_count} faces on {map_obj.name} based on {terrain_obj.name}"
    )


def plateInsert(plate, map):
    from .mesh_ops import (  # deferred to avoid circular import at load time
        recalculateNormals,
        selectBottomFaces,
    )

    bpy.ops.object.select_all(action="DESELECT")

    tol = bpy.context.scene.tp3d.tolerance
    dist = bpy.context.scene.tp3d.plateInsertValue
    size = bpy.context.scene.tp3d.objSize

    # Duplicate the map object
    map_copy = map.copy()
    map_copy.data = map.data.copy()
    bpy.context.collection.objects.link(map_copy)
    map_copy.scale *= (size + tol) / size

    plate.location.z += dist

    selectBottomFaces(map_copy)
    bpy.ops.mesh.select_all(action="INVERT")
    bpy.ops.mesh.delete(type="FACE")
    bpy.ops.mesh.select_all(action="SELECT")

    bpy.ops.mesh.extrude_region_move()
    bpy.ops.transform.translate(value=(0, 0, 100))
    bpy.ops.object.mode_set(mode="OBJECT")

    recalculateNormals(map_copy)
    bpy.ops.object.select_all(action="DESELECT")

    plate.select_set(True)
    bpy.context.view_layer.objects.active = plate

    mod = plate.modifiers.new(name="Boolean", type="BOOLEAN")
    mod.operation = "DIFFERENCE"
    mod.solver = "MANIFOLD"
    mod.object = map_copy

    bpy.ops.object.modifier_apply(modifier=mod.name)

    bpy.data.objects.remove(map_copy, do_unlink=True)


# ---------------------------------------------------------------------------
# Coastline polygon construction helpers
# ---------------------------------------------------------------------------


def _rdp_simplify(points, epsilon):
    """Ramer-Douglas-Peucker polyline simplification.

    Reduces a dense list of (x, y) points to a subset that deviates by at
    most *epsilon* Blender units from the original path.  This is essential
    before feeding coastline chains into the Manifold boolean solver, which
    can crash on polygons with thousands of nearly-collinear vertices.
    """
    if len(points) < 3:
        return list(points)
    x1, y1 = points[0]
    x2, y2 = points[-1]
    dx, dy = x2 - x1, y2 - y1
    length = math.sqrt(dx * dx + dy * dy)
    if length == 0:
        dists = [math.sqrt((px - x1) ** 2 + (py - y1) ** 2) for px, py in points[1:-1]]
    else:
        dists = [
            abs(dy * (px - x1) - dx * (py - y1)) / length for px, py in points[1:-1]
        ]
    idx = max(range(len(dists)), key=lambda i: dists[i])
    if dists[idx] > epsilon:
        left = _rdp_simplify(points[: idx + 2], epsilon)
        right = _rdp_simplify(points[idx + 1 :], epsilon)
        return left[:-1] + right
    return [points[0], points[-1]]


def _polygonize_ocean_faces(open_chains, closed_loops, bbox_bl, rdp_eps=0.0):
    """Build the ocean region(s) for one tile directly from coastline chains.

    Returns a list of Shapely Polygons -- the ocean, already including
    interior rings (holes) wherever land sits inside water. No separate
    "subtract the islands" step is needed: polygonize() returns every
    enclosed face (ocean AND land) as its own simple polygon, and unioning
    together only the faces tagged "ocean" naturally leaves a hole wherever
    an untagged land face sits inside that union.

    open_chains  -- chains crossing the tile boundary, in OSM land-is-left
                    direction (land on the left of travel, ocean on the right)
    closed_loops -- island/landmass loops fully or partially inside the tile
    bbox_bl      -- (min_x, min_y, max_x, max_y) in local Blender space
    rdp_eps      -- simplification tolerance; 0 to skip
    """
    min_x, min_y, max_x, max_y = bbox_bl
    if max_x <= min_x or max_y <= min_y:
        # Degenerate bbox — scaleHor is 0, or min/max lat or lon are identical.
        print(
            f"  [ocean] WARNING: degenerate bbox_bl {bbox_bl!r} — skipping ocean polygon"
        )
        return []
    tile_box = box(min_x, min_y, max_x, max_y)

    lines = []
    for c in list(open_chains) + list(closed_loops):
        if len(c) < 2:
            continue
        ln = LineString(c)
        if ln.is_empty or ln.length == 0:
            continue
        lines.append(ln)
    if not lines:
        return []

    # Clip first, then simplify — simplifying before clipping can collapse a
    # long chain to just its (outside-bbox) endpoints, making it miss the bbox.
    clipped = []
    for ln in lines:
        c = clip_by_rect(ln, min_x, min_y, max_x, max_y)
        if c.is_empty:
            continue
        if rdp_eps > 0:
            c = c.simplify(rdp_eps)
            if c.is_empty:
                continue
        if c.geom_type == "LineString":
            clipped.append(c)
        elif c.geom_type == "MultiLineString":
            clipped.extend(g for g in c.geoms if not g.is_empty)
    if not clipped:
        return []

    boundary = tile_box.boundary
    noded = union_all(clipped + [boundary])
    faces = list(polygonize(noded))
    if not faces:
        return []

    from shapely.strtree import STRtree

    tree = STRtree(faces)
    eps = max(max_x - min_x, max_y - min_y, 1.0) * 1e-4

    def _right_probe(p1, p2):
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        length = math.hypot(dx, dy)
        if length == 0:
            return None
        mx, my = (p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0
        nx, ny = dy / length, -dx / length  # right-hand normal of travel dir
        return Point(mx + nx * eps, my + ny * eps)

    ocean_idx = set()
    for ln in clipped:  # use clipped — inside the bbox so probes hit actual faces
        coords = list(ln.coords)
        step = max(1, len(coords) // 6)  # spread probes along the chain
        for i in range(0, len(coords) - 1, step):
            probe = _right_probe(coords[i], coords[i + 1])
            if probe is None:
                continue
            # STRtree predicate kwarg is broken in Blender's Shapely build;
            # use bbox-only query then filter manually.
            for idx in tree.query(probe):
                if faces[int(idx)].contains(probe):
                    ocean_idx.add(int(idx))

    if not ocean_idx:
        return []

    ocean_polys = [faces[i] for i in ocean_idx]

    # Small land pockets below the configured minimum area aren't worth
    # cutting as real holes -- fold them back into the ocean instead of
    # excluding them. Replaces the old min_area island-skip logic in
    # _contained_islands.
    tp3d_ctx = getattr(bpy.context.scene, "tp3d", None)
    min_area = getattr(tp3d_ctx, "el_oMinIslandArea", 4.0)
    for i, f in enumerate(faces):
        if i in ocean_idx:
            continue
        if f.area < min_area:
            ocean_polys.append(f)

    merged = union_all(ocean_polys)

    from . import geometry2d as _g2d  # deferred, matches your existing convention

    merged = _g2d.validate(merged)
    if merged is None or merged.is_empty:
        return []
    return list(_g2d.iter_polygons(merged, min_area=1.0))


def _clip_chain_to_bbox(chain, bbox_bl):
    """Clip a coastline chain to the tile bbox using Liang-Barsky per segment.

    A chain may enter and exit the bbox more than once (e.g. a wiggly coastline
    that dips outside and comes back).  Returns a list of contiguous inside
    segments, each a list of (x, y).  Returns an empty list if the chain never
    enters the bbox.
    """
    min_x, min_y, max_x, max_y = bbox_bl

    def _lb_clip(x1, y1, x2, y2):
        dx, dy = x2 - x1, y2 - y1
        t0, t1 = 0.0, 1.0
        for p, q in (
            (-dx, x1 - min_x),
            (dx, max_x - x1),
            (-dy, y1 - min_y),
            (dy, max_y - y1),
        ):
            if abs(p) < 1e-12:
                if q < 0:
                    return None
            elif p < 0:
                t0 = max(t0, q / p)
            else:
                t1 = min(t1, q / p)
        return (t0, t1) if t0 <= t1 else None

    def _lerp(a, b, t):
        return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))

    def _eq(a, b):
        return abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6

    segments = []
    current = []
    for i in range(len(chain) - 1):
        p1, p2 = chain[i], chain[i + 1]
        clip = _lb_clip(p1[0], p1[1], p2[0], p2[1])
        if clip is None:
            # Segment outside — close the current inside run if any
            if current:
                segments.append(current)
                current = []
            continue
        t0, t1 = clip
        enter = _lerp(p1, p2, t0) if t0 > 0 else p1
        exit_ = _lerp(p1, p2, t1) if t1 < 1 else p2
        if not current:
            current.append(enter)
        elif not _eq(current[-1], enter):
            # Gap within a clipped segment (shouldn't normally happen) — start fresh
            segments.append(current)
            current = [enter]
        current.append(exit_)

    if current:
        segments.append(current)

    return [s for s in segments if len(s) >= 2]


def _stitch_coastline_chains(raw_chains, tol=0.0001):
    """Stitch open coastline way fragments into longer chains and closed loops.

    OSM delivers coastline as directed open-ended way segments whose endpoints
    abut where ways were split for editing.  This function joins them
    end-to-start whenever the gap is within *tol* Blender units.

    Returns
    -------
    open_chains  : list of [(x,y), …]  — chains that still start/end on the
                   map-tile boundary (neither endpoint meets the other)
    closed_loops : list of [(x,y), …]  — chains whose first ≈ last point
                   (islands, peninsulas fully inside the tile)
    """
    if not raw_chains:
        return [], []

    chains = [list(c) for c in raw_chains]

    # Greedy closest-match stitch: for each chain A, find the chain B whose
    # endpoint is closest to A's last point (within tol), then merge.  Using
    # closest rather than first-found prevents wrong joins when multiple short
    # segments are near each other in large fetch areas.
    changed = True
    while changed:
        changed = False
        i = 0
        while i < len(chains):
            a = chains[i]
            ax, ay = a[-1]
            best_dist = tol
            best_j = -1
            best_reversed = False
            for j in range(len(chains)):
                if j == i:
                    continue
                b = chains[j]
                bx0, by0 = b[0]
                bxe, bye = b[-1]
                d_start = math.sqrt((ax - bx0) ** 2 + (ay - by0) ** 2)
                d_end = math.sqrt((ax - bxe) ** 2 + (ay - bye) ** 2)
                if d_start < best_dist:
                    best_dist = d_start
                    best_j = j
                    best_reversed = False
                if d_end < best_dist:
                    best_dist = d_end
                    best_j = j
                    best_reversed = True
            if best_j != -1:
                b = chains[best_j]
                if best_reversed:
                    chains[i] = a + list(reversed(b[:-1]))
                else:
                    chains[i] = a + b[1:]
                chains.pop(best_j)
                if best_j < i:
                    i -= 1
                changed = True
            else:
                i += 1

    closed_loops = []
    open_chains = []
    for c in chains:
        if len(c) < 3:
            continue
        dx = c[0][0] - c[-1][0]
        dy = c[0][1] - c[-1][1]
        if math.sqrt(dx * dx + dy * dy) < tol:
            closed_loops.append(c)
        else:
            open_chains.append(c)

    return open_chains, closed_loops


def _close_chain_with_bbox(chain, bbox_bl):
    """Compat wrapper kept for the existing test suite / any external callers.

    The original returned a flat (x, y) point list for the single largest
    closed region formed by one chain against the bbox. Internally this now
    routes through the same GEOS pipeline _build_ocean_mesh uses, rather than
    hand-rolled perimeter walking, but keeps the old signature/return shape.
    """
    polys = _polygonize_ocean_faces([chain], [], bbox_bl)
    if not polys:
        return None
    biggest = max(polys, key=lambda p: p.area)
    return list(biggest.exterior.coords)


def _polygon_area(pts):
    """Signed area of a 2-D polygon via the shoelace formula (always positive)."""
    n = len(pts)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) * 0.5


def _diagnose_polygon(geom, label=""):
    """DEBUG-ONLY: report whether *geom* is a valid simple polygon.

    Replaces the old O(n^2) hand-rolled edge-crossing counter (which gave up
    above 4000 points -- see the original comment). is_valid / explain_validity
    are GEOS-indexed, not brute-force pairwise, so there's no size ceiling:
    this scales to an un-simplified coastline of any length.
    """
    if not bpy.app.debug:
        return
    n = len(geom.exterior.coords) if isinstance(geom, Polygon) else "?"
    if geom.is_valid:
        print(f"    [poly-diag] {label}: {n} pts | SIMPLE (ok)")
    else:
        from shapely.validation import explain_validity

        print(f"    [poly-diag] {label}: {n} pts | INVALID -- {explain_validity(geom)}")


def _point_in_polygon(pt, poly):
    """Ray-casting point-in-polygon test.  poly is a list of (x, y)."""
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / (yj - yi + 1e-30) + xi
        ):
            inside = not inside
        j = i
    return inside


def _close_chains_with_bbox(chains, bbox_bl):
    """Build ocean polygons from clipped+simplified open coastline chains.

    Each open chain crosses the tile bbox, entering at its start and exiting
    at its end, carrying land on its LEFT (OSM convention) and ocean on its
    RIGHT.  A single tile can hold several disjoint ocean regions -- e.g. an
    island that pokes out of three different edges leaves three separate sea
    pockets in the corners.  Each region is traced as its own closed polygon:

      1. Pick an unused chain and follow it forward (start -> end).
      2. From its end, walk the bbox perimeter CLOCKWISE to the *immediately*
         next chain start (this keeps ocean on the right).  Emit the corners
         crossed along the way.
      3. If that next start belongs to a chain already consumed, the region is
         closed.  Otherwise follow that chain forward and repeat from step 2.
      4. Repeat for any chains not yet consumed -> another ocean polygon.

    Returns a list of polygons (each a list of (x, y)); empty list if none.
    """
    if not chains:
        return []

    min_x, min_y, max_x, max_y = bbox_bl
    W = max(max_x - min_x, 1e-9)
    H = max(max_y - min_y, 1e-9)

    def _ccw(pt):
        """CCW perimeter parameter in [0,4): 0=bottom-left, 1=bottom-right,
        2=top-right, 3=top-left."""
        x = max(min_x, min(max_x, pt[0]))
        y = max(min_y, min(max_y, pt[1]))
        ds = [abs(y - min_y), abs(x - max_x), abs(y - max_y), abs(x - min_x)]
        e = ds.index(min(ds))
        if e == 0:
            return (x - min_x) / W
        if e == 1:
            return 1.0 + (y - min_y) / H
        if e == 2:
            return 2.0 + (max_x - x) / W
        return 3.0 + (max_y - y) / H

    def _p2pt(p):
        p %= 4.0
        if p < 1:
            return (min_x + p * W, min_y)
        if p < 2:
            return (max_x, min_y + (p - 1) * H)
        if p < 3:
            return (max_x - (p - 2) * W, max_y)
        return (min_x, max_y - (p - 3) * H)

    def _cw_corners(from_p, to_p):
        """Bbox corner points crossed while walking CW from from_p to to_p."""
        from_p %= 4.0
        to_p %= 4.0
        cw_dist = (from_p - to_p) % 4.0
        if cw_dist < 1e-6:
            return []
        pts = []
        p = from_p
        remaining = cw_dist
        for _ in range(4):
            c = math.floor(p - 1e-9) % 4  # corner index just below p (CW)
            d = (p - c) % 4.0  # distance to that corner going CW
            if d < 1e-9 or d >= remaining - 1e-9:
                break
            pts.append(_p2pt(float(c)))
            p = float(c)
            remaining -= d
        return pts

    # Per-chain perimeter params (start, end) in CCW space.
    info = []
    for ch in chains:
        if len(ch) >= 2:
            info.append(
                {"sp": _ccw(ch[0]), "ep": _ccw(ch[-1]), "chain": ch, "used": False}
            )
    if not info:
        return []

    def _next_start_idx(end_p):
        """Index of the chain whose START is the immediate next one CW from
        end_p.  Walking CW (decreasing CCW param) from a chain end, the very
        next crossing is always a start; this returns whichever that is,
        including the end chain's own start (a single-chain corner pocket)."""
        best_i, best_d = -1, float("inf")
        for i, c in enumerate(info):
            d = (end_p - c["sp"]) % 4.0  # CW distance from end_p to this start
            if d <= 1e-9:
                d += 4.0  # start coincides with end -> full loop
            if d < best_d:
                best_d, best_i = d, i
        return best_i

    polygons = []
    for start_i in range(len(info)):
        if info[start_i]["used"]:
            continue
        poly = []
        idx = start_i
        for _ in range(len(info) + 1):
            cur = info[idx]
            cur["used"] = True
            # Follow chain forward (land on left -> ocean traces on the right).
            poly.extend(cur["chain"])
            # CW perimeter arc from this chain end to the next chain start.
            nxt = _next_start_idx(cur["ep"])
            poly.extend(_cw_corners(cur["ep"], info[nxt]["sp"]))
            if info[nxt]["used"]:
                break  # region closed (returned to a consumed chain)
            idx = nxt
        if len(poly) >= 3:
            polygons.append(poly)

    return polygons


def _debug_add_poly(name, pts2d, z=0.0, offset=(0.0, 0.0, 0.0)):
    """Add a flat polygon to the TP3D_Debug collection (only when bpy.app.debug).
    offset is applied as obj.location so debug objects can be spread out."""
    if not bpy.app.debug:
        return
    from .primitives import col_create_face_mesh  # deferred

    coll = bpy.data.collections.get("TP3D_Debug")
    if coll is None:
        coll = bpy.data.collections.new("TP3D_Debug")
        bpy.context.scene.collection.children.link(coll)
    pts3d = [(x, y, z) for x, y in pts2d]
    obj = col_create_face_mesh(f"_DEBUG_{name}", pts3d)
    if obj is None:
        return
    obj.location = offset
    # Move from default collection into TP3D_Debug
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    coll.objects.link(obj)


def _debug_add_polyline(name, pts2d, z=0.0, offset=(0.0, 0.0, 0.0)):
    """Add an edge-only polyline to the TP3D_Debug collection (only when bpy.app.debug)."""
    if not bpy.app.debug:
        return
    from .primitives import col_create_line_mesh  # deferred

    coll = bpy.data.collections.get("TP3D_Debug")
    if coll is None:
        coll = bpy.data.collections.new("TP3D_Debug")
        bpy.context.scene.collection.children.link(coll)
    pts3d = [(x, y, z) for x, y in pts2d]
    obj = col_create_line_mesh(f"_DEBUG_{name}", pts3d)
    if obj is None:
        return
    obj.location = offset
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    coll.objects.link(obj)


def _taubin_smooth_ocean_polys(ocean_polys, bbox_bl):
    """Taubin-smooth ocean-face polygons, pinning vertices on the tile bbox
    edge so adjacent tiles keep stitching together seamlessly -- only
    interior coastline/island edges actually move. Shared by every ocean
    source (Overpass coastline chains via _smoothed_ocean_polys, and the
    global water-polygon dataset via createOceanFromWaterPolygons) so the
    col_osmSmoothing setting behaves identically regardless of which path
    built the polygon.
    """
    from . import geometry2d as _g2d

    _smooth_steps = int(
        getattr(getattr(bpy.context.scene, "tp3d", None), "col_osmSmoothing", 0.0) * 20
    )
    if not ocean_polys or _smooth_steps <= 0:
        return ocean_polys

    _smoothed_polys = []
    for _poly_idx, poly in enumerate(ocean_polys):
        smoothed = _g2d.smooth_polygon_taubin_bbox_pinned(
            poly, bbox_bl, steps=_smooth_steps, debug_name=f"ocean_{_poly_idx:03d}"
        )
        # force=True: splits self-touching rings (figure-8 pinch points
        # from Taubin) without touching already-valid unrelated polygons.
        _smoothed_polys.extend(
            p
            for p in (
                _g2d.validate(part, force=True) for part in _g2d.iter_polygons(smoothed)
            )
            if p is not None and not p.is_empty
        )
    if not _smoothed_polys:
        return ocean_polys
    return list(_g2d.iter_polygons(_g2d.union(_smoothed_polys), min_area=1.0))


def _smoothed_ocean_polys(open_chains, closed_loops, bbox_bl, rdp_eps):
    """Build closed ocean-face polygons from coastline chains and Taubin-smooth
    them, pinning vertices on the tile bbox edge so adjacent tiles keep
    stitching together seamlessly -- only interior coastline/island edges
    actually move. Shared by the mesh (_build_ocean_mesh) and texture-paint
    (createOcean's useTexture branch) code paths.

    Smoothing must happen AFTER polygonization since Taubin needs closed
    rings, and _polygonize_ocean_faces() is the first point where the
    coastline chains have become closed ocean-face rings.
    """
    ocean_polys = _polygonize_ocean_faces(
        open_chains, closed_loops, bbox_bl, rdp_eps=rdp_eps
    )
    return _taubin_smooth_ocean_polys(ocean_polys, bbox_bl)


def _build_ocean_mesh(open_chains, closed_loops, bbox_bl, tile):
    """Build the flat ocean mesh object from stitched coastline chains.

    *open_chains*  : chains that cross the tile boundary
    *closed_loops* : island/peninsula loops fully or partially inside the tile
    *bbox_bl*      : (min_x, min_y, max_x, max_y) in Blender local space
    *tile*         : the map mesh object (kept for signature compatibility;
                      unused here, same as the original)

    Returns a Blender mesh object or None.
    """
    from . import geometry2d as _g2d
    from .mesh_ops import merge_objects  # deferred to avoid circular import

    min_x, min_y, max_x, max_y = bbox_bl

    rdp_eps = getattr(getattr(bpy.context.scene, "tp3d", None), "el_oRdpEpsilon", 0.1)
    if bpy.app.debug:
        print(f"    [ocean mesh] coastline RDP epsilon = {rdp_eps}")

    ocean_polys = _smoothed_ocean_polys(open_chains, closed_loops, bbox_bl, rdp_eps)

    if not ocean_polys:
        if not open_chains and not closed_loops:
            # No coastline data at all: the tile is 100% open ocean with no
            # holes to cut. Build a flat quad directly rather than routing a
            # plain rectangle through Shapely + earcut -- unnecessary
            # overhead, and earcut always splits even a convex quad into 2
            # triangles, which is avoidable here. Same fast path as before.
            outer = [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)]
            mesh = bpy.data.meshes.new("_OceanFace")
            ocean_obj = bpy.data.objects.new("_OceanFace", mesh)
            bpy.context.collection.objects.link(ocean_obj)
            mesh.from_pydata([(x, y, 0.0) for x, y in outer], [], [[0, 1, 2, 3]])
            mesh.update()
        else:
            return None
    else:
        face_meshes = []
        for poly in ocean_polys:
            if bpy.app.debug:
                _diagnose_polygon(poly, "ocean face")
            for part in _g2d.iter_polygons(poly, min_area=1.0):
                m = _g2d.polygon_to_mesh("_OceanFace", part)
                if m is not None:
                    face_meshes.append(m)
        if not face_meshes:
            return None
        ocean_obj = (
            merge_objects(face_meshes) if len(face_meshes) > 1 else face_meshes[0]
        )

    if not ocean_obj or len(ocean_obj.data.vertices) == 0:
        return None

    ocean_obj.name = "Ocean"
    # Vertices are already in absolute Mercator coordinates -- keep origin at
    # world zero (same reasoning as the original: copying tile.location here
    # would double-count the offset).
    ocean_obj.location = (0.0, 0.0, 0.0)
    ocean_obj["_tp3d_is_ocean"] = True

    return ocean_obj


def createOcean(gen: GenerationContext, prefetched_coastline, scaleHor, tile):
    """Build the ocean layer mesh from pre-fetched coastline data.

    Uses the land-is-left OSM convention to construct the ocean polygon
    directly — no boolean cutters, no EXACT solver.

    Parameters
    ----------
    prefetched_coastline : dict  {bbox -> (data, from_cache)}
                           The COASTLINE slice of the prefetch result dict.
                           May be empty if no coastline exists in this tile.
    scaleHor             : float  horizontal scale factor
    tile                 : bpy.types.Object  the map mesh (used for location)
    """
    from .. import constants as _const  # deferred to avoid circular import at load time
    from .mesh_ops import (  # deferred to avoid circular import at load time  # deferred to avoid circular import at load time
        merge_with_map,
        projection,
        recalculateNormals,
    )
    from .osm.gen import (
        fetch_coastline_ways,  # deferred to avoid circular import at load time
    )
    from .scene import (
        set_origin_to_3d_cursor,  # deferred to avoid circular import at load time
    )

    _t_ocean = time.time()

    raw_chains = fetch_coastline_ways(prefetched_coastline, scaleHor)
    print(
        f"  [ocean] fetch_coastline_ways: {len(raw_chains)} raw ways  ({time.time() - _t_ocean:.3f}s)"
    )

    if bpy.app.debug:
        for ri, rc in enumerate(raw_chains):
            _debug_add_polyline(f"raw_chain_{ri}", rc, z=0.2)

    if not raw_chains:
        _progress.WarningsOverlay.add_warning(
            "No coastline data found for this area — ocean layer skipped.", "warn"
        )
        return None

    open_chains, closed_loops = _stitch_coastline_chains(raw_chains)
    print(
        f"  [ocean] stitched: {len(open_chains)} open chains, {len(closed_loops)} closed loops"
    )
    for i, c in enumerate(open_chains):
        print(f"    open[{i}]: {len(c)} pts  start={c[0]}  end={c[-1]}")
    for i, c in enumerate(closed_loops):
        print(f"    closed[{i}]: {len(c)} pts  start={c[0]}")

    # Build bbox in the same LOCAL Blender coordinate frame used by
    # fetch_coastline_ways (inline Mercator with the same scaleHor).
    # We cannot use tile.bound_box in world space because the tile object may
    # have been translated by xTerrainOffset/yTerrainOffset.
    def _ll_to_bl(lat, lon):
        x = _const.R * math.radians(lon) * scaleHor
        y = (
            _const.R
            * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
            * scaleHor
        )
        return (x, y)

    sw = _ll_to_bl(gen.runtime.tbMinLat, gen.runtime.tbMinLon)
    ne = _ll_to_bl(gen.runtime.tbMaxLat, gen.runtime.tbMaxLon)
    bbox_bl = (
        min(sw[0], ne[0]),
        min(sw[1], ne[1]),
        max(sw[0], ne[0]),
        max(sw[1], ne[1]),
    )
    print(
        f"  [ocean] bbox_bl: x=[{bbox_bl[0]:.3f}, {bbox_bl[2]:.3f}]  y=[{bbox_bl[1]:.3f}, {bbox_bl[3]:.3f}]"
    )

    elementMode = bpy.context.scene.tp3d.elementMode

    if gen.texture.useTexture:
        # Skip building any Blender mesh — return the Shapely polygon so the
        # texture rasterizer can paint it like every other OSM element.
        rdp_eps = getattr(bpy.context.scene.tp3d, "el_oRdpEpsilon", 0.1)
        _ct_polys = _smoothed_ocean_polys(open_chains, closed_loops, bbox_bl, rdp_eps)
        if not _ct_polys:
            _progress.WarningsOverlay.add_warning(
                "Could not build ocean polygon — ocean layer skipped.", "warn"
            )
            return None
        return _ColoringTextureResult(kind="OCEAN", polygon=union_all(_ct_polys))

    ocean_obj = _build_ocean_mesh(open_chains, closed_loops, bbox_bl, tile)
    print(f"  [ocean] _build_ocean_mesh: {time.time() - _t_ocean:.3f}s")
    if ocean_obj is not None:
        print(
            f"  [ocean] mesh verts={len(ocean_obj.data.vertices)}  faces={len(ocean_obj.data.polygons)}"
        )
    else:
        print("  [ocean] mesh: None")

    if ocean_obj is None:
        _progress.WarningsOverlay.add_warning(
            "Could not build ocean polygon — ocean layer skipped.", "warn"
        )
        return None

    set_origin_to_3d_cursor(ocean_obj)

    mat = bpy.data.materials.get("WATER")
    ocean_obj.data.materials.clear()
    ocean_obj.data.materials.append(mat)

    if elementMode == "PAINT":
        projection("paint", tile, ocean_obj)
        return None
    elif elementMode in ("SINGLECOLORMODE", "SINGLECOLORMODE_REMESH"):
        # Only clip ocean_obj to the plate's footprint here -- do NOT cut the
        # ocean recess into the plate yet. `tile` (the real plate object,
        # not a copy) must stay uncut until _rg_apply_single_color_mode has
        # taken its trail-intersection copy, otherwise the trail curves get
        # intersected against a plate that's already missing the ocean area.
        # The recess itself is cut later by _rg_apply_single_color_mode's
        # TERRAIN_PRIORITY_ORDER loop, which handles 'ocean' like every other
        # element.
        merge_with_map(tile, ocean_obj, True)
        mat = bpy.data.materials.get("WATER")
        ocean_obj.data.materials.clear()
        ocean_obj.data.materials.append(mat)
        return ocean_obj

    return ocean_obj


def createOceanFromWaterPolygons(gen: GenerationContext, scaleHor, tile):
    """Build the ocean layer from the prebuilt global OSMData water-polygon
    dataset instead of Overpass coastline ways.

    Fallback path for maps above const.COASTLINE_MAXSIZE (see
    utils/generation/elements.py) -- previously that size band just skipped
    ocean generation entirely. The dataset gives the water area directly, so
    unlike createOcean() there's no coastline-direction ("land-is-left")
    convention to reconstruct and no stitching/polygonize step needed.

    Returns the same shapes createOcean() does: a _ColoringTextureResult for
    PAINT/texture mode, a merged-into-tile mesh object for single color mode,
    or None if no water was found / the dataset couldn't be prepared.
    """
    from . import geometry2d as _g2d
    from .mesh_ops import (
        merge_objects,
        merge_with_map,
    )  # deferred, same convention as createOcean
    from .osm.water_polygons import (
        query_ocean_polygon,
    )  # deferred to avoid circular import at load time
    from .. import constants as _const  # deferred to avoid circular import at load time

    _t_ocean = time.time()

    poly = query_ocean_polygon(
        gen.runtime.tbMinLat,
        gen.runtime.tbMinLon,
        gen.runtime.tbMaxLat,
        gen.runtime.tbMaxLon,
        scaleHor,
    )
    print(
        f"  [ocean/waterpoly] query_ocean_polygon: {time.time() - _t_ocean:.3f}s "
        f"({'found water' if poly is not None else 'no water in range'})"
    )

    if poly is None or poly.is_empty:
        _progress.WarningsOverlay.add_warning(
            "No ocean found in this area (water-polygon dataset).", "warn"
        )
        return None

    tp3d_ctx = bpy.context.scene.tp3d

    # Same local Blender-space bbox as createOcean() (see that function's
    # comment) -- needed so Taubin smoothing below pins the tile-edge
    # vertices correctly and adjacent tiles keep stitching together.
    def _ll_to_bl(lat, lon):
        x = _const.R * math.radians(lon) * scaleHor
        y = (
            _const.R
            * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
            * scaleHor
        )
        return (x, y)

    sw = _ll_to_bl(gen.runtime.tbMinLat, gen.runtime.tbMinLon)
    ne = _ll_to_bl(gen.runtime.tbMaxLat, gen.runtime.tbMaxLon)
    bbox_bl = (
        min(sw[0], ne[0]),
        min(sw[1], ne[1]),
        max(sw[0], ne[0]),
        max(sw[1], ne[1]),
    )

    # Respect the same three settings the Overpass path applies in
    # _smoothed_ocean_polys / _polygonize_ocean_faces, in the same order:
    # min island area -> RDP simplify -> Taubin smoothing. The water-polygon
    # dataset hands back a ready-made polygon (holes = islands) rather than
    # raw coastline chains, so "min island area" here means dropping small
    # interior holes instead of folding small land faces back in -- same
    # visible effect, different starting representation.
    min_island_area = getattr(tp3d_ctx, "el_oMinIslandArea", 4.0)
    poly = _g2d.drop_small_holes(poly, min_island_area)

    rdp_eps = getattr(tp3d_ctx, "el_oRdpEpsilon", 0.1)
    ocean_polys = list(_g2d.iter_polygons(poly, min_area=1.0))
    if rdp_eps > 0:
        simplified = [p.simplify(rdp_eps) for p in ocean_polys]
        simplified = [p for p in simplified if p is not None and not p.is_empty]
        if simplified:
            merged = _g2d.validate(_g2d.union(simplified))
            if merged is not None and not merged.is_empty:
                ocean_polys = list(_g2d.iter_polygons(merged, min_area=1.0))

    ocean_polys = _taubin_smooth_ocean_polys(ocean_polys, bbox_bl)
    if not ocean_polys:
        _progress.WarningsOverlay.add_warning(
            "Could not build ocean polygon — ocean layer skipped.", "warn"
        )
        return None

    elementMode = bpy.context.scene.tp3d.elementMode

    if gen.texture.useTexture:
        # Same texture-paint short-circuit as createOcean(): hand back the
        # Shapely polygon for the rasterizer, no Blender mesh needed.
        return _ColoringTextureResult(kind="OCEAN", polygon=union_all(ocean_polys))

    face_meshes = []
    for part in ocean_polys:
        m = _g2d.polygon_to_mesh("_OceanFace", part)
        if m is not None:
            face_meshes.append(m)
    if not face_meshes:
        _progress.WarningsOverlay.add_warning(
            "Could not build ocean polygon — ocean layer skipped.", "warn"
        )
        return None

    ocean_obj = merge_objects(face_meshes) if len(face_meshes) > 1 else face_meshes[0]
    if not ocean_obj or len(ocean_obj.data.vertices) == 0:
        _progress.WarningsOverlay.add_warning(
            "Could not build ocean polygon — ocean layer skipped.", "warn"
        )
        return None

    ocean_obj.name = "Ocean"
    # Same reasoning as createOcean(): vertices are already in absolute
    # Mercator coordinates, keep origin at world zero.
    ocean_obj.location = (0.0, 0.0, 0.0)
    ocean_obj["_tp3d_is_ocean"] = True

    mat = bpy.data.materials.get("WATER")
    ocean_obj.data.materials.clear()
    ocean_obj.data.materials.append(mat)

    if elementMode == "PAINT":
        from .mesh_ops import projection  # deferred, same convention as createOcean

        projection("paint", tile, ocean_obj)
        return None
    elif elementMode in ("SINGLECOLORMODE", "SINGLECOLORMODE_REMESH"):
        # Same ordering note as createOcean(): only clip to the plate's
        # footprint here, the actual recess cut happens later in
        # _rg_apply_single_color_mode's TERRAIN_PRIORITY_ORDER loop.
        merge_with_map(tile, ocean_obj, True)
        mat = bpy.data.materials.get("WATER")
        ocean_obj.data.materials.clear()
        ocean_obj.data.materials.append(mat)
        return ocean_obj

    return ocean_obj


def exaggeratedLayers(objs):
    from .metadata import (
        writeMetadata,  # deferred to avoid circular import at load time
    )
    from .scene import (
        show_message_box,  # deferred to avoid circular import at load time
    )

    selected_objects = objs

    layerThickness = 1

    size = bpy.context.scene.tp3d.objSize

    if not selected_objects:
        show_message_box("No Object Selected. Please select a Map first")
        return {"CANCELLED"}

    for obj in selected_objects:
        if "Object type" not in obj:
            continue
        if obj["Object type"] != "MAP":
            continue

        objs = list(bpy.context.scene.objects)
        for o in objs:
            if (
                "Object type" in o
                and "PARENT" in o
                and o["PARENT"] == obj
                and o["Object type"] == "LINES"
            ):
                bpy.data.objects.remove(o, do_unlink=True)

        # Deselect everything
        bpy.ops.object.select_all(action="DESELECT")

        # Create plane at 3D cursor
        bpy.ops.mesh.primitive_plane_add(
            size=size + 10,
            enter_editmode=False,
            align="WORLD",
            location=bpy.context.scene.cursor.location,
        )
        plane = bpy.context.active_object
        if plane is None:
            continue
        plane.name = "CuttingPlane"
        plane.location.z += 0.1 + layerThickness / 2

        # Add Array modifier in Z direction
        array_mod = plane.modifiers.new(name="ArrayZ", type="ARRAY")
        array_mod.relative_offset_displace = (0, 0, 0)  # disable relative offset
        array_mod.constant_offset_displace = (0, 0, layerThickness)  # fixed step in Z
        array_mod.use_relative_offset = False
        array_mod.use_constant_offset = True
        array_mod.count = 30  # you can adjust how many slices

        # Apply modifiers up to solidify
        bpy.context.view_layer.objects.active = plane
        bpy.ops.object.modifier_apply(modifier=array_mod.name)

        # Add Boolean modifier with INTERSECT mode
        bool_mod = plane.modifiers.new(name="Boolean", type="BOOLEAN")
        bool_mod.operation = "INTERSECT"
        bool_mod.solver = "FLOAT"  # or 'EXACT'
        bool_mod.use_self = False
        bool_mod.use_hole_tolerant = True  # helps with manifold issues
        bool_mod.object = obj

        plane.name = obj.name + "_LAYERS"

        bpy.ops.object.modifier_apply(modifier=bool_mod.name)

        # Add Solidify modifier for thickness
        solidify_mod = plane.modifiers.new(name="Solidify", type="SOLIDIFY")
        solidify_mod.thickness = layerThickness
        solidify_mod.offset = 0

        bpy.ops.object.modifier_apply(modifier=solidify_mod.name)

        mat = bpy.data.materials.get("WHITE")
        plane.data.materials.clear()
        plane.data.materials.append(mat)

        writeMetadata(plane, "LINES")
        plane["PARENT"] = obj

    bpy.ops.object.select_all(action="DESELECT")
    for obj in selected_objects:
        obj.select_set(True)
    if selected_objects:
        bpy.context.view_layer.objects.active = selected_objects[0]


def _elevation_mm_per_meter(obj):
    """Model-space mm per real-world elevation meter for this specific map object.

    Derived from the elevation-scale metadata frozen onto the object at
    generation time (so it stays correct even if the scene's current
    scaleElevation/autoScale belong to a differently-scaled map generated
    afterward). Falls back to the scene's current elevation-scale settings
    if the object predates this metadata.
    """
    elev_range_m = obj.get("Elevation Range (m)")
    lowestZ = obj.get("lowestZ")
    highestZ = obj.get("highestZ")
    if elev_range_m and lowestZ is not None and highestZ is not None:
        z_range = highestZ - lowestZ
        if z_range:
            return z_range / elev_range_m

    scale_elevation = bpy.context.scene.tp3d.scaleElevation
    auto_scale = bpy.context.scene.tp3d.sAutoScale
    return scale_elevation * auto_scale / 1000


def contourLines(objs):
    from .mesh_ops import (
        boolean_operation,  # deferred to avoid circular import at load time
    )
    from .metadata import (
        writeMetadata,  # deferred to avoid circular import at load time
    )
    from .scene import (
        show_message_box,  # deferred to avoid circular import at load time
    )

    selected_objects = objs
    cl_thickness = bpy.context.scene.tp3d.cl_thickness
    cl_distance = bpy.context.scene.tp3d.cl_distance
    cl_offset = bpy.context.scene.tp3d.cl_offset
    cl_useRealMeters = bpy.context.scene.tp3d.cl_useRealMeters

    size = bpy.context.scene.tp3d.objSize

    if not selected_objects:
        show_message_box("No Object Selected. Please select a Map first")
        return {"CANCELLED"}

    for obj in selected_objects:
        if "Object type" not in obj:
            continue
        if obj["Object type"] != "MAP":
            continue

        # cl_distance/cl_offset are entered in real-world elevation meters
        # when the toggle is on -- convert to this map's own model-space mm
        # using the elevation scale frozen onto it at generation time.
        if cl_useRealMeters:
            mm_per_meter = _elevation_mm_per_meter(obj)
            cl_distance_eff = cl_distance * mm_per_meter
            cl_offset_eff = cl_offset * mm_per_meter
        else:
            cl_distance_eff = cl_distance
            cl_offset_eff = cl_offset

        if cl_distance_eff <= cl_thickness:
            distance_label = (
                f"{cl_distance:g}m = {cl_distance_eff:.3f}mm"
                if cl_useRealMeters
                else f"{cl_distance_eff:.3f}mm"
            )
            show_message_box(
                f"Contour Line distance ({distance_label}) must be greater than the Contour Line "
                f"thickness ({cl_thickness:.3f}mm) for '{obj.name}'. Skipping."
            )
            continue

        objs = list(bpy.context.scene.objects)
        for o in objs:
            if (
                "Object type" in o
                and "PARENT" in o
                and o["PARENT"] == obj
                and o["Object type"] == "LINES"
            ):
                bpy.data.objects.remove(o, do_unlink=True)

        # Deselect everything
        bpy.ops.object.select_all(action="DESELECT")

        # Create plane at the map object's own origin
        bpy.ops.mesh.primitive_plane_add(
            size=size + 10, enter_editmode=False, align="WORLD", location=obj.location
        )
        plane = bpy.context.active_object
        if plane is None:
            continue
        plane.name = "CuttingPlane"
        plane.location.z += cl_offset_eff

        # Add Array modifier in Z direction
        array_mod = plane.modifiers.new(name="ArrayZ", type="ARRAY")
        array_mod.relative_offset_displace = (0, 0, 0)  # disable relative offset
        array_mod.constant_offset_displace = (0, 0, cl_distance_eff)  # fixed step in Z
        array_mod.use_relative_offset = False
        array_mod.use_constant_offset = True
        array_mod.count = 100  # you can adjust how many slices

        # Add Solidify modifier for thickness
        solidify_mod = plane.modifiers.new(name="Solidify", type="SOLIDIFY")
        solidify_mod.thickness = cl_thickness

        # Apply modifiers up to solidify
        bpy.context.view_layer.objects.active = plane
        bpy.ops.object.modifier_apply(modifier=array_mod.name)
        bpy.ops.object.modifier_apply(modifier=solidify_mod.name)

        # Duplicate the still-blank stack of squares before it gets cut down
        # to the map's shape -- this copy is used below to carve the same
        # bands out of the map so the lines don't sit flush on top of it.
        bpy.ops.object.select_all(action="DESELECT")
        plane.select_set(True)
        bpy.context.view_layer.objects.active = plane
        bpy.ops.object.duplicate()
        cutter = bpy.context.active_object
        cutter.name = "CuttingPlaneCutter"

        # Add Boolean modifier with INTERSECT mode
        bool_mod = plane.modifiers.new(name="Boolean", type="BOOLEAN")
        bool_mod.operation = "INTERSECT"
        bool_mod.solver = "MANIFOLD"  # or 'EXACT'
        bool_mod.use_self = False
        bool_mod.use_hole_tolerant = True  # helps with manifold issues
        bool_mod.object = obj

        plane.name = obj.name + "_LINES"

        mat = bpy.data.materials.get("WHITE")
        plane.data.materials.clear()
        plane.data.materials.append(mat)

        writeMetadata(plane, "LINES")
        plane["PARENT"] = obj

        # Apply Boolean
        bpy.context.view_layer.objects.active = plane

        bpy.ops.object.modifier_apply(modifier=bool_mod.name)

        # Subtract the same bands from the map itself so the lines aren't
        # duplicated (coincident) geometry sitting on top of the map surface.
        boolean_operation(obj, cutter, operation="DIFFERENCE", solver="MANIFOLD")
        bpy.data.objects.remove(cutter, do_unlink=True)

    bpy.ops.object.select_all(action="DESELECT")
    for obj in selected_objects:
        obj.select_set(True)
    if selected_objects:
        bpy.context.view_layer.objects.active = selected_objects[0]
