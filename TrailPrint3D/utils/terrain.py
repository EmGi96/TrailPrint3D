import bpy  # type: ignore
import bmesh  # type: ignore
import math
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from mathutils import Vector, bvhtree  # type: ignore
from .. import progress as _progress

_COLORING_EMPTY = object()
_COLORING_PAINTED = object()
_COLORING_FILTERED = object()

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
                 Overpass API (callers typically use Semaphore(2))
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
    from .osm import fetch_osm_data  # deferred to avoid circular import

    results = {}
    lock = threading.Lock()

    def _fetch_one(bbox):
        with semaphore:
            try:
                result = fetch_osm_data(bbox, kind, return_cache_status=True,
                                        settings=settings)
            except Exception as e:
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
    (callers use Semaphore(2)); because each tile now maps to exactly one
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
    from .osm import fetch_osm_combined  # deferred to avoid circular import

    # Regroup: (kind, [bboxes]) → {bbox: [kinds]} → {bbox: [kinds]}
    tile_kinds: dict = {}
    for kind, bboxes in kind_task_pairs:
        for bbox in bboxes:
            tile_kinds.setdefault(bbox, []).append(kind)

    results = {kind: {} for kind, _ in kind_task_pairs}
    lock = threading.Lock()

    def _fetch_tile(bbox, kinds):
        # Acquire the shared semaphore before the network call (mirrors the
        # original _fetch_one pattern so the semaphore correctly caps the
        # number of concurrent live Overpass requests).
        if semaphore is not None:
            semaphore.acquire()
        try:
            tile_result = fetch_osm_combined(bbox, kinds, settings=settings)
        except Exception as e:
            print(f"[_fetch_all_kinds_parallel] tile {bbox} failed: {e}")
            return
        finally:
            if semaphore is not None:
                semaphore.release()
        with lock:
            for kind, (data, from_cache) in tile_result.items():
                if data:
                    results[kind][bbox] = (data, from_cache)

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


def coloring_main(map, kind="WATER", prefetched_tiles=None):
    from .osm import fetch_osm_data, build_osm_nodes, extract_multipolygon_bodies  # deferred to avoid circular import at load time
    from .geo import convert_to_blender_coordinates  # deferred to avoid circular import at load time
    from .mesh_ops import merge_objects  # deferred to avoid circular import at load time
    from .scene import show_message_box  # deferred to avoid circular import at load time
    from .metadata import writeMetadata  # deferred to avoid circular import at load time
    from . import geometry2d as _g2d  # Shapely-based 2D geometry helpers

    _t_color = time.time()          # master timer: whole coloring_main
    _t_tiles_total = 0.0            # accumulated OSM fetch + Shapely ring building

    minLat = bpy.context.scene.tp3d.minLat
    minLon = bpy.context.scene.tp3d.minLon
    maxLat = bpy.context.scene.tp3d.maxLat
    maxLon = bpy.context.scene.tp3d.maxLon

    if kind == "WATER":
        col_Area = (bpy.context.scene.tp3d.col_wArea)
    if kind == "FOREST":
        col_Area = (bpy.context.scene.tp3d.col_fArea)
    if kind == "SCREE":
        col_Area = (bpy.context.scene.tp3d.col_scrArea)
    if kind == "CITY":
        col_Area = (bpy.context.scene.tp3d.col_cArea)
    if kind == "GREENSPACE":
        col_Area = (bpy.context.scene.tp3d.col_grArea)
    if kind == "FARMLAND":
        col_Area = (bpy.context.scene.tp3d.col_faArea)
    if kind == "GLACIER":
        col_Area = (bpy.context.scene.tp3d.col_glArea)

    elementMode = (bpy.context.scene.tp3d.elementMode)
    exportformat = "STL"
    if elementMode == "PAINT":
        exportformat = "OBJ"

    bpy.context.scene.tp3d.exportformat = exportformat

    name = map.name

    lat_step = 2
    lon_step = 2

    waterDeleted = 0
    waterCreated = 0
    total_fetched = 0
    _api_empty    = False   # set True when OSM responded with 0 usable features

    if maxLat - minLat < lat_step:
        lat_step = maxLat - minLat
    if maxLon - minLon < lon_step:
        lon_step = maxLon - minLon

    lats = math.ceil((maxLat - minLat) / lat_step)
    lons = math.ceil((maxLon - minLon) / lon_step)

    pos_geoms = []
    neg_geoms = []

    scaleHor = bpy.context.scene.tp3d.sScaleHor
    streamWidthMultiplier = bpy.context.scene.tp3d.col_wStreamWidth
    half_width = 1.0 * scaleHor * 0.02 * streamWidthMultiplier

    cntr = 0
    maxcntr = lats * lons
    _t_tiles_start = time.time()
    if lats * lons < 20 or prefetched_tiles is not None:
        for k in range(lats):
            for l in range(lons):
                cntr = (k) * lons + l + 1
                print(f"{kind} loop: {((k) * lons + l + 1)}/{maxcntr}")
                _ov = _progress.ProgressOverlay.get()
                if _ov.active:
                    if prefetched_tiles is not None:
                        _ov.update(message=f"{kind.capitalize()}: tile {cntr}/{maxcntr} — processing…")
                    else:
                        _ov.update(message=f"{kind.capitalize()}: tile {cntr}/{maxcntr} — fetching…")
                        _ov.set_fetch_progress(kind.lower(), cntr / maxcntr)
                south = minLat + k * lat_step
                north = south + lat_step
                west = minLon + l * lon_step
                east = west + lon_step

                bbox = (south, west, north, east)
                data = []
                try:
                    if prefetched_tiles is not None:
                        tile_result = prefetched_tiles.get(bbox)
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

                except Exception as e:
                    show_message_box(f"Something went wrong with fetching OSM data: {e}")
                    _progress.WarningsOverlay.add_warning(f"Something went wrong with fetching OSM data: {e}", "error")
                    continue

                data = resp
                n_features = len([e for e in data['elements'] if e['type'] == 'way'])
                if _ov.active:
                    src = "cached" if from_cache else "live"
                    _ov.update(message=f"{kind.capitalize()}: tile {cntr}/{maxcntr} — calculating mesh ({n_features} features, {src})…")
                nodes = build_osm_nodes(data)
                bodies, negatives = extract_multipolygon_bodies(data['elements'], nodes)
                total_fetched += n_features + len(bodies) + len(negatives)

                # Track ways already consumed by relations to avoid duplicate geometry
                relation_way_ids = set()
                for el in data['elements']:
                    if el['type'] == 'relation':
                        for member in el.get('members', []):
                            if member['type'] == 'way':
                                relation_way_ids.add(member['ref'])

                if _ov.active:
                    _ov.update(message=f"{kind.capitalize()}: tile {cntr}/{maxcntr} — building geometry")

                # Build Shapely polygons from relation outer rings
                for coords in bodies:
                    xy = [(x, y) for x, y, _ in
                          (convert_to_blender_coordinates(lat, lon, ele, 0) for lat, lon, ele in coords)]
                    poly = _g2d.xy_ring_to_polygon(xy)
                    if poly is not None and not poly.is_empty:
                        pos_geoms.append(poly)
                        waterCreated += 1
                    else:
                        waterDeleted += 1

                # Build Shapely polygons from relation inner rings (negatives / holes)
                for coords in negatives:
                    xy = [(x, y) for x, y, _ in
                          (convert_to_blender_coordinates(lat, lon, ele, 0) for lat, lon, ele in coords)]
                    poly = _g2d.xy_ring_to_polygon(xy)
                    if poly is not None and not poly.is_empty and poly.area >= col_Area:
                        neg_geoms.append(poly)
                        waterCreated += 1
                    else:
                        waterDeleted += 1

                # Process standalone ways: closed → polygon, open → buffered ribbon
                for element in data['elements']:
                    if element['type'] != 'way':
                        waterDeleted += 1
                        continue
                    if element['id'] in relation_way_ids:
                        continue  # already consumed by a relation

                    coords = []
                    for node_id in element.get('nodes', []):
                        if node_id in nodes:
                            node = nodes[node_id]
                            coords.append(convert_to_blender_coordinates(
                                node['lat'], node['lon'], 0, 0
                            ))
                    if len(coords) < 2:
                        waterDeleted += 1
                        continue

                    if coords[0] == coords[-1]:
                        xy = [(x, y) for x, y, _ in coords]
                        poly = _g2d.xy_ring_to_polygon(xy)
                        if poly is not None and not poly.is_empty:
                            pos_geoms.append(poly)
                            waterCreated += 1
                        else:
                            waterDeleted += 1
                    else:
                        xy = [(x, y) for x, y, _ in coords]
                        ribbon = _g2d.line_to_ribbon(xy, half_width)
                        if ribbon is not None and not ribbon.is_empty:
                            pos_geoms.append(ribbon)
                            waterCreated += 1
                        else:
                            waterDeleted += 1

                if not from_cache and prefetched_tiles is None:
                    time.sleep(5)  # Pause to prevent request throttling (skipped when worker pre-fetched)
    else:
        print(f"Region too big. Cant Fetch All {kind} Sources")
        return None

    _t_tiles_total = time.time() - _t_tiles_start
    print(f"  [coloring_main] tile fetch + ring build ({kind}): {_t_tiles_total:.3f}s  "
          f"(includes Overpass throttle sleeps)  pos={len(pos_geoms)}  neg={len(neg_geoms)}")

    if cntr < maxcntr:
        print("Not All data fetched")
        pos_geoms.clear()
        neg_geoms.clear()
        print("Timed out. Cached already Fetched Data. Try Regenerating Again")
    else:
        if total_fetched == 0:
            _progress.WarningsOverlay.add_warning(f"No {kind.capitalize()} elements returned from API.", "warn")
            _api_empty = True
        elif waterCreated == 0:
            _progress.WarningsOverlay.add_warning(f"All {kind.capitalize()} elements are below the area threshold.", "warn")
            _api_empty = True


    def _split_loose(obj):
        """Split obj into per-connected-component objects using Blender's native C
        mesh-separate operator (orders-of-magnitude faster than a Python DFS on
        large post-boolean meshes).  Returns a list that includes obj itself (which
        retains one component) plus any newly created objects for additional
        components.  Empty objects are excluded."""
        before = set(bpy.data.objects)
        bpy.ops.object.select_all(action='DESELECT')
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.separate(type='LOOSE')
        bpy.ops.object.mode_set(mode='OBJECT')
        after = set(bpy.data.objects)
        parts = list(after - before) + [obj]
        return [o for o in parts if o.data and len(o.data.vertices) > 0]

    # ── Shapely: union all → subtract negatives → area-filter → ONE mesh ────────────
    _t_shapely = time.time()
    merged_pos = _g2d.union(pos_geoms)
    merged_neg = _g2d.union(neg_geoms)
    final_geom = _g2d.subtract(merged_pos, merged_neg)
    print(f"  [coloring_main] Shapely union+subtract ({kind}): {time.time()-_t_shapely:.3f}s  pos={len(pos_geoms)}  neg={len(neg_geoms)}")

    # DEBUG: dump the exact Shapely geometry at each stage as stacked wireframes so
    # the raw rings (incl. self-intersections / slivers) can be inspected directly.
    if bpy.app.debug:
        _dbg = f"TP3D_Debug_{kind}"
        _g2d.debug_dump(f"DBG_{kind}_1_raw_pos",   pos_geoms,  _dbg, z=0.0)
        _g2d.debug_dump(f"DBG_{kind}_2_raw_neg",   neg_geoms,  _dbg, z=20.0)
        _g2d.debug_dump(f"DBG_{kind}_3_merged_pos", merged_pos, _dbg, z=40.0)
        _g2d.debug_dump(f"DBG_{kind}_4_merged_neg", merged_neg, _dbg, z=60.0)
        _g2d.debug_dump(f"DBG_{kind}_5_final",     final_geom, _dbg, z=80.0)
        print(f"  [coloring_main] DEBUG wireframes dumped to collection '{_dbg}'")

    if final_geom is None or final_geom.is_empty:
        if _api_empty:
            return _COLORING_EMPTY
        _progress.WarningsOverlay.add_warning(f"All {kind.capitalize()} objects were filtered out due to their size", "warn")
        return _COLORING_FILTERED

    _t_mesh = time.time()
    result_meshes = []
    _dbg_kept = []   # area-filtered polygons that actually become meshes (debug)
    for i, poly in enumerate(_g2d.iter_polygons(final_geom, min_area=col_Area)):
        if bpy.app.debug:
            _dbg_kept.append(poly)
        m = _g2d.polygon_to_mesh(f"{kind}_{i}", poly)
        if m is not None:
            result_meshes.append(m)
    print(f"  [coloring_main] polygon_to_mesh ({kind}, {len(result_meshes)} parts): {time.time()-_t_mesh:.3f}s")

    if bpy.app.debug and _dbg_kept:
        _g2d.debug_dump(f"DBG_{kind}_6_kept_polys", _dbg_kept, f"TP3D_Debug_{kind}", z=100.0)

    if not result_meshes:
        if _api_empty:
            return _COLORING_EMPTY
        return _COLORING_FILTERED

    merged_object = merge_objects(result_meshes) if len(result_meshes) > 1 else result_meshes[0]
    if merged_object is None:
        return None

    # Tessellate_polygon produces upward-facing normals (CCW Shapely exterior → Z-up).
    # Flip them downward so the extruded prism intersects the terrain correctly.
    # NOTE: do NOT run remove_doubles here — merging near-coincident vertices from
    # different polygon parts creates pinch-point non-manifold verts, which is worse
    # than leaving them as separate topological components. Each polygon mesh is
    # already cleaned internally inside polygon_to_mesh.
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
        print(f"  [PAINT fast path] terrain_max_z={terrain_max_z:.2f}  extrude_z={extrude_z:.2f}")

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

        print(f"PAINTING ({kind})")
        _t_paint = time.time()
        color_map_faces_by_terrain(map, merged_object)
        mesh_data = merged_object.data
        bpy.data.objects.remove(merged_object, do_unlink=True)
        bpy.data.meshes.remove(mesh_data)
        print(f"  [coloring_main] PAINT total ({kind}): {time.time()-_t_paint:.3f}s")
        print(f"  [coloring_main] TOTAL ({kind}, PAINT): {time.time()-_t_color:.3f}s")
        return _COLORING_PAINTED
        # ── end PAINT fast path ───────────────────────────────────────────────────────

    # ── SEPARATE / SINGLECOLORMODE path ──────────────────────────────────────────────
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

    _t_bool = time.time()

    # ── Pre-boolean manifold diagnostics ────────────────────────────────────
    def _count_non_manifold(obj):
        bm_d = bmesh.new()
        bm_d.from_mesh(obj.data)
        bm_d.verts.ensure_lookup_table()
        bm_d.edges.ensure_lookup_table()
        nm_verts = sum(1 for v in bm_d.verts if not v.is_manifold)
        nm_edges = sum(1 for e in bm_d.edges if not e.is_manifold)
        bm_d.free()
        return nm_verts, nm_edges

    cutter_nm_v, cutter_nm_e = _count_non_manifold(merged_object)
    map_nm_v, map_nm_e = _count_non_manifold(map)
    print(f"  [manifold-check] ({kind}) cutter: {len(merged_object.data.vertices)}v "
          f"non-manifold={cutter_nm_v}v/{cutter_nm_e}e  |  "
          f"map: {len(map.data.vertices)}v non-manifold={map_nm_v}v/{map_nm_e}e")
    if cutter_nm_v > 0 or cutter_nm_e > 0:
        print(f"  [manifold-check] WARNING: cutter has non-manifold geometry — "
              f"boolean may be a no-op or produce garbage")
    # ────────────────────────────────────────────────────────────────────────

    def _apply_boolean(obj, solver):
        mod = obj.modifiers.new(name="Boolean", type='BOOLEAN')
        mod.object = map
        mod.operation = 'INTERSECT'
        mod.solver = solver
        dg = bpy.context.evaluated_depsgraph_get()
        result = bpy.data.meshes.new_from_object(obj.evaluated_get(dg))
        obj.modifiers.clear()
        return result

    new_mesh = _apply_boolean(merged_object, 'MANIFOLD')
    solver_used = 'MANIFOLD'

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
        new_mesh = _apply_boolean(merged_object, 'EXACT')
        solver_used = 'EXACT (fallback)'
        if new_mesh.vertices:
            result_zs = [v.co.z for v in new_mesh.vertices]
            z_max = max(result_zs)
        else:
            z_max = 201.0

    old_mesh = merged_object.data
    merged_object.data = new_mesh
    bpy.data.meshes.remove(old_mesh)

    if new_mesh.vertices:
        print(f"  [coloring_main] boolean INTERSECT {solver_used} ({kind}): {time.time()-_t_bool:.3f}s"
              f"  verts={len(new_mesh.vertices)}  z=[{min(result_zs):.2f}, {z_max:.2f}]")
        if z_max > 150:
            print(f"  [manifold-check] WARNING: EXACT fallback also failed — z_max={z_max:.1f}")
    else:
        print(f"  [coloring_main] boolean ({kind}): {time.time()-_t_bool:.3f}s  verts=0")

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
            bm.free()
            bpy.data.objects.remove(zobj, do_unlink=True)
            continue

        lowest_face = None
        lowest_z = float('inf')
        for face in bm.faces:
            z = face.calc_center_median().z
            if z < lowest_z and face.calc_area() > 0:
                lowest_z = z
                lowest_face = face
        if lowest_face and lowest_face.normal.dot(DOWN) <= 0:
            bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
        bm.to_mesh(zmesh)
        bm.free()
        surviving.append(zobj)
    print(f"  [coloring_main] split_loose ({kind}): {time.time()-_t_split:.3f}s  parts={len(surviving)}")
    print(f"  [coloring_main] SEPARATE total ({kind}): {time.time()-_t_proc:.3f}s")

    if not surviving:
        return None

    _t_merge = time.time()
    merged_object = merge_objects(surviving) if len(surviving) > 1 else surviving[0]
    print(f"  [coloring_main] merge_objects ({kind}): {time.time()-_t_merge:.3f}s")

    if merged_object is None:
        return None

    bpy.ops.object.origin_set(type='ORIGIN_CURSOR', center='MEDIAN')

    bm = bmesh.new()
    bm.from_mesh(merged_object.data)
    min_z = min(v.co.z for v in bm.verts)
    lowestVert = 100
    for v in bm.verts:
        if abs(v.co.z - min_z) > tol and v.co.z >= bpy.context.scene.tp3d.minThickness:
            if v.co.z < lowestVert:
                lowestVert = v.co.z
    for v in bm.verts:
        if abs(v.co.z - min_z) < tol:
            v.co.z = lowestVert - 1
    bm.to_mesh(merged_object.data)
    bm.free()

    bpy.ops.object.mode_set(mode="OBJECT")

    if "SINGLECOLORMODE" not in elementMode:
        merged_object.location.z += 0.2
    merged_object.name = name + "_" + kind

    bpy.context.view_layer.objects.active = merged_object
    merged_object.select_set(True)

    writeMetadata(merged_object, kind)
    mat = bpy.data.materials.get(KIND_MATERIAL_OVERRIDE.get(kind, kind))
    merged_object.data.materials.clear()
    merged_object.data.materials.append(mat)

    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            for space in area.spaces:
                if space.type == 'VIEW_3D':
                    space.shading.type = 'MATERIAL'

    bpy.context.preferences.edit.use_global_undo = True
    print(f"  [coloring_main] TOTAL ({kind}, SEPARATE): {time.time()-_t_color:.3f}s")
    return merged_object

def color_map_faces_by_terrain(map_obj, terrain_obj, up_threshold=0.05):
    """
    Loops through every face of map_obj.
    If face is facing upwards, raycasts upwards to see if terrain_obj is above.
    If yes, colors the face with terrain_obj's material.

    up_threshold = dot(normal, Z) must be greater than this (0.5 ~ 60° angle limit).
    """
    from .mesh_ops import recalculateNormals  # deferred to avoid circular import at load time

    if map_obj.type != 'MESH' or terrain_obj.type != 'MESH':
        print("Both inputs must be mesh objects.")
        return

    recalculateNormals(map_obj)

    terrain_obj.location.z += 10
    bpy.context.view_layer.update()

    # Ensure both have mesh data
    map_mesh = map_obj.data
 

    # Build bmesh for Map — read LOCAL mesh, transform centers to WORLD space via matrix_world
    bm = bmesh.new()
    bm.from_mesh(map_mesh)
    bm.faces.ensure_lookup_table()
    mw_map = map_obj.matrix_world

    depsgraph = bpy.context.evaluated_depsgraph_get()
    eval_obj = terrain_obj.evaluated_get(depsgraph)

    eval_mesh = eval_obj.to_mesh()

    # Build BVH in WORLD space by applying the cutter's matrix_world to each vertex
    bm2 = bmesh.new()
    bm2.from_mesh(eval_mesh)
    mw_terrain = terrain_obj.matrix_world
    for v in bm2.verts:
        v.co = mw_terrain @ v.co

    _t_bvh = time.time()
    bvh = bvhtree.BVHTree.FromBMesh(bm2)
    print(f"  [color_faces] BVH build: {time.time()-_t_bvh:.3f}s  ({len(bm2.faces)} terrain faces)")

    # Get or create a material for terrain color
    if terrain_obj.active_material:
        mat = terrain_obj.active_material
    else:
        mat = bpy.data.materials.new(name="TerrainColor")
        terrain_obj.data.materials.append(mat)

    # Make sure Map has material slots
    if mat.name not in [m.name for m in map_mesh.materials if m is not None]:
        map_mesh.materials.append(mat)
    mat_index = map_mesh.materials.find(mat.name)

    up = Vector((0, 0, 1))
    colored_count = 0

    _t_raycast = time.time()
    i = 0
    for i, f in enumerate(bm.faces):
        normal = f.normal.normalized()
        dot = normal.dot(up)
        # Only consider faces facing upward
        if dot > up_threshold:
            center = mw_map @ f.calc_center_median()  # world space
            center.z -= 5
            loc, norm, idx, dist = bvh.ray_cast(center, up,200)

            if loc is not None:
                # Assign terrain material to this face
                f.material_index = mat_index
                colored_count += 1
    print(f"  [color_faces] ray-cast loop: {time.time()-_t_raycast:.3f}s  ({i+1} faces checked, {colored_count} colored)")

    _t_sync = time.time()
    bm.to_mesh(map_mesh)
    bm.free()
    bm2.free()
    eval_obj.to_mesh_clear()
    print(f"  [color_faces] bm.to_mesh sync: {time.time()-_t_sync:.3f}s")
    print(f"Colored {colored_count} faces on {map_obj.name} based on {terrain_obj.name}")


def plateInsert(plate, map):
    from .mesh_ops import selectBottomFaces, recalculateNormals  # deferred to avoid circular import at load time

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
    bpy.ops.mesh.select_all(action='INVERT')
    bpy.ops.mesh.delete(type='FACE')
    bpy.ops.mesh.select_all(action='SELECT')

    bpy.ops.mesh.extrude_region_move()
    bpy.ops.transform.translate(value=(0, 0, 100))
    bpy.ops.object.mode_set(mode='OBJECT')

    recalculateNormals(map_copy)
    bpy.ops.object.select_all(action="DESELECT")

    plate.select_set(True)
    bpy.context.view_layer.objects.active = plate

    mod = plate.modifiers.new(name="Boolean", type='BOOLEAN')
    mod.operation = 'DIFFERENCE'
    mod.solver = "MANIFOLD"
    mod.object = map_copy

    bpy.ops.object.modifier_apply(modifier = mod.name)

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
        dists = [math.sqrt((px - x1) ** 2 + (py - y1) ** 2)
                 for px, py in points[1:-1]]
    else:
        dists = [abs(dy * (px - x1) - dx * (py - y1)) / length
                 for px, py in points[1:-1]]
    idx = max(range(len(dists)), key=lambda i: dists[i])
    if dists[idx] > epsilon:
        left  = _rdp_simplify(points[:idx + 2], epsilon)
        right = _rdp_simplify(points[idx + 1:], epsilon)
        return left[:-1] + right
    return [points[0], points[-1]]


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
            ( dx, max_x - x1),
            (-dy, y1 - min_y),
            ( dy, max_y - y1),
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
                d_end   = math.sqrt((ax - bxe) ** 2 + (ay - bye) ** 2)
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
    """Close an open coastline chain by walking the tile bbox boundary.

    *chain*   : list of (x,y) in Blender space — land-is-left direction.
    *bbox_bl* : (min_x, min_y, max_x, max_y) Blender-space tile rectangle.

    The chain enters and exits the tile through the bbox perimeter.  We close
    it by walking the perimeter on the **ocean side** (to the right of travel
    direction) back from the chain's end to its start.  That ensures the
    resulting polygon encloses ocean, not land.

    Returns a list of (x,y) forming a closed polygon, or None if the chain
    is too short to make sense.
    """
    if len(chain) < 2:
        return None

    min_x, min_y, max_x, max_y = bbox_bl

    # The four corners of the bbox, in CCW order (standard polygon winding)
    corners_ccw = [
        (min_x, min_y),
        (max_x, min_y),
        (max_x, max_y),
        (min_x, max_y),
    ]

    def _snap_to_perimeter(pt):
        """Return which edge (0=bottom,1=right,2=top,3=left) and parameter t."""
        x, y = pt
        candidates = []
        # bottom: y == min_y
        if abs(y - min_y) < 1.0:
            t = (x - min_x) / max(max_x - min_x, 1e-9)
            candidates.append((abs(y - min_y), 0, t))
        # right: x == max_x
        if abs(x - max_x) < 1.0:
            t = (y - min_y) / max(max_y - min_y, 1e-9)
            candidates.append((abs(x - max_x), 1, t))
        # top: y == max_y
        if abs(y - max_y) < 1.0:
            t = (max_x - x) / max(max_x - min_x, 1e-9)
            candidates.append((abs(y - max_y), 2, t))
        # left: x == min_x
        if abs(x - min_x) < 1.0:
            t = (max_y - y) / max(max_y - min_y, 1e-9)
            candidates.append((abs(x - min_x), 3, t))
        if not candidates:
            # Point is not near any edge — clamp to nearest
            distances = [
                (abs(y - min_y), 0, (x - min_x) / max(max_x - min_x, 1e-9)),
                (abs(x - max_x), 1, (y - min_y) / max(max_y - min_y, 1e-9)),
                (abs(y - max_y), 2, (max_x - x) / max(max_x - min_x, 1e-9)),
                (abs(x - min_x), 3, (max_y - y) / max(max_y - min_y, 1e-9)),
            ]
            distances.sort()
            return distances[0][1], distances[0][2]
        candidates.sort()
        return candidates[0][1], candidates[0][2]

    def _edge_to_point(edge, t):
        if edge == 0:
            return (min_x + t * (max_x - min_x), min_y)
        elif edge == 1:
            return (max_x, min_y + t * (max_y - min_y))
        elif edge == 2:
            return (max_x - t * (max_x - min_x), max_y)
        else:
            return (min_x, max_y - t * (max_y - min_y))

    start_edge, start_t = _snap_to_perimeter(chain[0])
    end_edge, end_t = _snap_to_perimeter(chain[-1])

    # Walk the bbox perimeter CW from end_edge/end_t back to start_edge/start_t.
    # CW means decreasing edge index (mod 4), reversed t within each edge.
    # This keeps ocean to the right of the chain direction.
    perimeter_pts = []
    edge = end_edge
    t_cur = end_t
    iterations = 0
    while True:
        iterations += 1
        if iterations > 8:
            break
        if edge == start_edge:
            # On the same edge: walk directly to start_t (CW means decreasing t)
            if t_cur > start_t:
                perimeter_pts.append(_edge_to_point(edge, start_t))
            elif abs(t_cur - start_t) < 1e-6:
                # Start and end are the same point on the bbox — degenerate
                return None
            else:
                # end_t < start_t on the same edge: the chain enters and exits
                # through the same bbox edge in a way that requires a full
                # perimeter walk.  Only do one full loop (iterations guard
                # already limits this), add the corner and continue CW.
                next_edge = (edge - 1) % 4
                perimeter_pts.append(corners_ccw[edge])
                edge = next_edge
                t_cur = 1.0
                continue
            break
        else:
            # Walk to the beginning of this edge (t=0, which is the CCW corner)
            perimeter_pts.append(corners_ccw[edge])
            edge = (edge - 1) % 4
            t_cur = 1.0

    polygon = list(chain) + perimeter_pts
    return polygon


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


def _diagnose_polygon(poly, label=""):
    """DEBUG-ONLY: report whether a closed polygon is geometrically simple.

    Tessellation (and every downstream boolean) silently produces garbage when
    the outline crosses itself or doubles back.  This counts proper crossings
    between non-adjacent edges, flags duplicate consecutive points and reports
    the coordinate magnitude (a precision-risk indicator).  O(n^2); debug only.
    """
    if not bpy.app.debug:
        return
    n = len(poly)
    if n < 4:
        print(f"    [poly-diag] {label}: {n} pts (too few to self-test)")
        return
    if n > 4000:
        # O(n^2) self-test would stall Blender on huge polygons (e.g. an
        # un-simplified coastline of tens of thousands of points).
        print(f"    [poly-diag] {label}: {n} pts (too large for O(n^2) self-test -- skipped)")
        return

    def _seg_cross(p1, p2, p3, p4):
        def _o(a, b, c):
            v = (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])
            if v > 1e-9: return 1
            if v < -1e-9: return -1
            return 0
        o1, o2 = _o(p1, p2, p3), _o(p1, p2, p4)
        o3, o4 = _o(p3, p4, p1), _o(p3, p4, p2)
        return o1 != o2 and o3 != o4   # proper crossing only

    crossings = 0
    first_hit = None
    for i in range(n):
        a1, a2 = poly[i], poly[(i + 1) % n]
        for j in range(i + 1, n):
            # skip adjacent / shared-endpoint edges
            if j == i or (j + 1) % n == i or (i + 1) % n == j:
                continue
            b1, b2 = poly[j], poly[(j + 1) % n]
            if _seg_cross(a1, a2, b1, b2):
                crossings += 1
                if first_hit is None:
                    first_hit = (i, j)

    dupes = sum(1 for k in range(n)
                if abs(poly[k][0]-poly[(k+1) % n][0]) < 1e-6
                and abs(poly[k][1]-poly[(k+1) % n][1]) < 1e-6)
    mags = [max(abs(x), abs(y)) for x, y in poly]
    print(f"    [poly-diag] {label}: {n} pts | self-crossings={crossings}"
          f"{f' (first at edges {first_hit})' if first_hit else ''}"
          f" | dup-consecutive={dupes} | coord-mag~{max(mags):.0f}"
          f" | {'SIMPLE (ok)' if crossings == 0 else 'NON-SIMPLE (breaks tessellation)'}")


def _point_in_polygon(pt, poly):
    """Ray-casting point-in-polygon test.  poly is a list of (x, y)."""
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and \
           (x < (xj - xi) * (y - yi) / (yj - yi + 1e-30) + xi):
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
        if e == 0: return (x - min_x) / W
        if e == 1: return 1.0 + (y - min_y) / H
        if e == 2: return 2.0 + (max_x - x) / W
        return       3.0 + (max_y - y) / H

    def _p2pt(p):
        p %= 4.0
        if p < 1: return (min_x + p * W,       min_y)
        if p < 2: return (max_x,                min_y + (p - 1) * H)
        if p < 3: return (max_x - (p - 2) * W, max_y)
        return           (min_x,                max_y - (p - 3) * H)

    def _cw_corners(from_p, to_p):
        """Bbox corner points crossed while walking CW from from_p to to_p."""
        from_p %= 4.0
        to_p   %= 4.0
        cw_dist = (from_p - to_p) % 4.0
        if cw_dist < 1e-6:
            return []
        pts = []
        p = from_p
        remaining = cw_dist
        for _ in range(4):
            c = math.floor(p - 1e-9) % 4  # corner index just below p (CW)
            d = (p - c) % 4.0              # distance to that corner going CW
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
            info.append({'sp': _ccw(ch[0]), 'ep': _ccw(ch[-1]),
                         'chain': ch, 'used': False})
    if not info:
        return []

    def _next_start_idx(end_p):
        """Index of the chain whose START is the immediate next one CW from
        end_p.  Walking CW (decreasing CCW param) from a chain end, the very
        next crossing is always a start; this returns whichever that is,
        including the end chain's own start (a single-chain corner pocket)."""
        best_i, best_d = -1, float('inf')
        for i, c in enumerate(info):
            d = (end_p - c['sp']) % 4.0   # CW distance from end_p to this start
            if d <= 1e-9:
                d += 4.0                    # start coincides with end -> full loop
            if d < best_d:
                best_d, best_i = d, i
        return best_i

    polygons = []
    for start_i in range(len(info)):
        if info[start_i]['used']:
            continue
        poly = []
        idx = start_i
        for _ in range(len(info) + 1):
            cur = info[idx]
            cur['used'] = True
            # Follow chain forward (land on left -> ocean traces on the right).
            poly.extend(cur['chain'])
            # CW perimeter arc from this chain end to the next chain start.
            nxt = _next_start_idx(cur['ep'])
            poly.extend(_cw_corners(cur['ep'], info[nxt]['sp']))
            if info[nxt]['used']:
                break          # region closed (returned to a consumed chain)
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


def _build_ocean_mesh(open_chains, closed_loops, bbox_bl, tile):
    """Build the flat ocean mesh object from stitched coastline chains.

    *open_chains*  : chains that cross the tile boundary â†' close via bbox walk
    *closed_loops* : island/peninsula loops entirely inside the tile (unused
                     here — island subtraction on a flat 2D polygon is
                     unreliable with boolean solvers; projection() clips to
                     actual terrain geometry which handles it naturally)
    *bbox_bl*      : (min_x, min_y, max_x, max_y) in Blender local space
    *tile*         : the map mesh object (used only for location reference)

    Returns a Blender mesh object or None.
    """
    from .mesh_ops import merge_objects  # deferred to avoid circular import at load time
    from . import geometry2d as _g2d    # Shapely-based 2D geometry helpers

    ocean_faces = []

    min_x, min_y, max_x, max_y = bbox_bl
    W = max(max_x - min_x, 1e-9)
    H = max(max_y - min_y, 1e-9)
    border_eps = max(W, H) * 1e-4

    def _on_border(pt):
        x, y = pt
        return (abs(x - min_x) <= border_eps or abs(x - max_x) <= border_eps or
                abs(y - min_y) <= border_eps or abs(y - max_y) <= border_eps)

    def _edges_of(pt):
        """Set of bbox edges a border point lies on (0=bottom,1=right,2=top,
        3=left).  A corner point belongs to two edges."""
        x, y = pt
        e = set()
        if abs(y - min_y) <= border_eps: e.add(0)
        if abs(x - max_x) <= border_eps: e.add(1)
        if abs(y - max_y) <= border_eps: e.add(2)
        if abs(x - min_x) <= border_eps: e.add(3)
        return e

    def _rotate_outside(loop):
        """Rotate a closed loop so it starts at a vertex outside the bbox.
        Returns (rotated_loop, crosses_border)."""
        for k, (x, y) in enumerate(loop):
            if (x < min_x - border_eps or x > max_x + border_eps or
                    y < min_y - border_eps or y > max_y + border_eps):
                return loop[k:] + loop[:k], True
        return loop, False

    # Clip every coastline loop (open fragments + closed island/landmass
    # loops) to the tile bbox.  A loop that crosses the tile border -- even
    # one the stitcher closed because the fetch area was larger than the tile
    # (e.g. Mallorca) -- yields clipped segments whose endpoints land ON the
    # border; those are the ocean-bounding chains the tracer needs.  Loops
    # that sit entirely inside the tile clip to themselves and stay islands.
    border_chains = []   # endpoints on the tile border -> bound ocean
    island_loops = []    # closed loops fully inside the tile

    rdp_eps = getattr(bpy.context.scene.tp3d, 'el_oRdpEpsilon', 0.1)
    if bpy.app.debug:
        print(f"    [ocean mesh] coastline RDP epsilon = {rdp_eps}")

    def _add_clipped(chain):
        for clipped in _clip_chain_to_bbox(chain, bbox_bl):
            simplified = _rdp_simplify(clipped, epsilon=rdp_eps) if rdp_eps > 0 else clipped
            if len(simplified) < 2:
                continue
            if _on_border(simplified[0]) and _on_border(simplified[-1]):
                # A border fragment whose two endpoints sit on the SAME bbox
                # edge runs along the tile boundary (the coastline briefly
                # dips out and back across one edge).  It encloses negligible
                # area but breaks the entry/exit alternation the perimeter
                # tracer relies on -- producing a self-intersecting polygon.
                # Drop it from the main border walk.
                if _edges_of(simplified[0]) & _edges_of(simplified[-1]):
                    if bpy.app.debug:
                        print(f"      [ocean mesh] dropping same-edge border fragment "
                              f"({len(simplified)} pts)")
                    continue
                border_chains.append(simplified)
            elif len(simplified) >= 3:
                island_loops.append(simplified)

    for chain in open_chains:
        _add_clipped(chain)
    for loop in closed_loops:
        rotated, crosses = _rotate_outside(loop)
        if crosses:
            _add_clipped(rotated)
        else:
            simplified = _rdp_simplify(loop, epsilon=rdp_eps) if rdp_eps > 0 else loop
            if len(simplified) >= 3:
                island_loops.append(simplified)

    tp3d_ctx = bpy.context.scene.tp3d
    min_area = getattr(tp3d_ctx, 'el_oMinIslandArea', 4.0)

    if bpy.app.debug:
        print(f"    [ocean mesh] {len(border_chains)} border chains, {len(island_loops)} interior islands")
        for ii, isl in enumerate(island_loops):
            area = _polygon_area(isl)
            kept = area >= min_area
            print(f"      island[{ii}]: {len(isl)} pts  area={area:.3f}  {'KEEP' if kept else f'SKIP (<{min_area})'}")
            _debug_add_poly(f"island_{'kept' if kept else 'skipped'}_{ii}", isl, offset=(150.0 * (ii % 8), -600.0 - 150.0 * (ii // 8), 0.1))

    def _contained_islands(outer_poly, label):
        """Return the island loops whose centroid lies inside outer_poly and
        whose area is at or above min_area (these become real holes)."""
        if not island_loops:
            return []
        contained = []
        for isl in island_loops:
            cx = sum(p[0] for p in isl) / len(isl)
            cy = sum(p[1] for p in isl) / len(isl)
            if _point_in_polygon((cx, cy), outer_poly):
                contained.append(isl)
        if not contained:
            return []
        kept = [s for s in contained if _polygon_area(s) >= min_area]
        skipped = len(contained) - len(kept)
        print(f"    [ocean mesh] {label}: cutting {len(kept)}/{len(contained)} island holes (skipped {skipped} below {min_area})")
        return kept

    def _make_ocean_face(outer_poly, label):
        """Build one ocean face using Shapely to repair the polygon and subtract islands.

        make_valid(method='structure') fixes any self-intersections caused by
        stitch errors or collapsed port/dock features — no voxel remesh needed.
        Islands are subtracted via Shapely difference, giving correct holes
        without bridge-slit workarounds.
        """
        if len(outer_poly) < 3:
            return None
        outer_shp = _g2d.xy_ring_to_polygon(outer_poly)
        if outer_shp is None or outer_shp.is_empty:
            return None
        holes = _contained_islands(outer_poly, label)
        if holes:
            hole_polys = [_g2d.xy_ring_to_polygon(h) for h in holes if len(h) >= 3]
            hole_polys = [h for h in hole_polys if h is not None and not h.is_empty]
            merged_holes = _g2d.union(hole_polys)
            if merged_holes and not merged_holes.is_empty:
                outer_shp = _g2d.subtract(outer_shp, merged_holes)
        outer_shp = _g2d.validate(outer_shp)
        if outer_shp is None or outer_shp.is_empty:
            return None
        if bpy.app.debug:
            _debug_add_poly(f"{label}_shapely_outer", outer_poly, offset=(0.0, -300.0, 0.1))
        face_meshes = []
        for poly in _g2d.iter_polygons(outer_shp, min_area=1.0):
            m = _g2d.polygon_to_mesh("_OceanFace", poly)
            if m is not None:
                face_meshes.append(m)
        if not face_meshes:
            return None
        return merge_objects(face_meshes) if len(face_meshes) > 1 else face_meshes[0]

    if border_chains:
        polys = _close_chains_with_bbox(border_chains, bbox_bl)
        for pi, poly in enumerate(polys):
            if len(poly) < 3 or _polygon_area(poly) < 1.0:
                if bpy.app.debug and len(poly) >= 3:
                    print(f"    [ocean mesh] dropping sliver polygon {pi} "
                          f"({len(poly)} pts, area={_polygon_area(poly):.4f})")
                continue
            if bpy.app.debug:
                print(f"    [ocean mesh] ocean polygon {pi}: {len(poly)} pts")
                _diagnose_polygon(poly, f"poly {pi} (outer, pre-islands)")
                _debug_add_poly(f"ocean_polygon_{pi}_pre_islands", poly, offset=(150.0 * pi, -450.0, 0.1))
            face_obj = _make_ocean_face(poly, f"poly {pi}")
            if face_obj and len(face_obj.data.vertices) > 0:
                ocean_faces.append(face_obj)

    if not ocean_faces:
        # No coastline crosses the tile border: the tile is either entirely
        # ocean, or all-water with islands wholly inside it.  Ocean = full
        # tile MINUS those interior islands.
        outer = [
            (min_x, min_y),
            (max_x, min_y),
            (max_x, max_y),
            (min_x, max_y),
        ]
        face_obj = _make_ocean_face(outer, "full-tile ocean")
        if face_obj and len(face_obj.data.vertices) > 0:
            ocean_faces.append(face_obj)

    if not ocean_faces:
        return None

    ocean_obj = merge_objects(ocean_faces) if len(ocean_faces) > 1 else ocean_faces[0]

    if not ocean_obj or len(ocean_obj.data.vertices) == 0:
        return None

    ocean_obj.name = "Ocean"
    # Do NOT copy tile.location here.  Ocean polygon vertices are already in
    # absolute Mercator coordinates (same world space as every other coloring
    # element) so the object origin must stay at (0, 0, 0).  Copying
    # tile.location would double-count the center offset and push the polygon
    # completely out of the tile bounds, causing the INTERSECT boolean inside
    # merge_with_map to return an empty mesh.
    ocean_obj.location = (0.0, 0.0, 0.0)

    # Record whether the cutter polygon self-intersects so createOcean can
    # decide between the fast direct boolean (simple coast) and the
    # voxel-remesh clean-up (self-crossing coast).
    # Tag the ocean object so merge_with_map can apply the flatBottom clamping
    # that prevents ocean from dipping below the terrain base plane.
    ocean_obj["_tp3d_is_ocean"] = True

    return ocean_obj


def createOcean(prefetched_coastline, scaleHor, tile):
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
    from .osm import fetch_coastline_ways  # deferred to avoid circular import at load time
    from .scene import set_origin_to_3d_cursor  # deferred to avoid circular import at load time
    from .mesh_ops import projection, recalculateNormals  # deferred to avoid circular import at load time
    from .. import constants as _const  # deferred to avoid circular import at load time

    _t_ocean = time.time()

    raw_chains = fetch_coastline_ways(prefetched_coastline, scaleHor)
    print(f"  [ocean] fetch_coastline_ways: {len(raw_chains)} raw ways  ({time.time()-_t_ocean:.3f}s)")

    if not raw_chains:
        _progress.WarningsOverlay.add_warning(
            "No coastline data found for this area — ocean layer skipped.", "warn"
        )
        return None

    open_chains, closed_loops = _stitch_coastline_chains(raw_chains)
    print(f"  [ocean] stitched: {len(open_chains)} open chains, {len(closed_loops)} closed loops")
    for i, c in enumerate(open_chains):
        print(f"    open[{i}]: {len(c)} pts  start={c[0]}  end={c[-1]}")
    for i, c in enumerate(closed_loops):
        print(f"    closed[{i}]: {len(c)} pts  start={c[0]}")

    # Build bbox in the same LOCAL Blender coordinate frame used by
    # fetch_coastline_ways (inline Mercator with the same scaleHor).
    # We cannot use tile.bound_box in world space because the tile object may
    # have been translated by xTerrainOffset/yTerrainOffset.
    tp3d = bpy.context.scene.tp3d
    def _ll_to_bl(lat, lon):
        x = _const.R * math.radians(lon) * scaleHor
        y = _const.R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * scaleHor
        return (x, y)
    sw = _ll_to_bl(tp3d.minLat, tp3d.minLon)
    ne = _ll_to_bl(tp3d.maxLat, tp3d.maxLon)
    bbox_bl = (min(sw[0], ne[0]), min(sw[1], ne[1]), max(sw[0], ne[0]), max(sw[1], ne[1]))
    print(f"  [ocean] bbox_bl: x=[{bbox_bl[0]:.3f}, {bbox_bl[2]:.3f}]  y=[{bbox_bl[1]:.3f}, {bbox_bl[3]:.3f}]")

    ocean_obj = _build_ocean_mesh(open_chains, closed_loops, bbox_bl, tile)
    print(f"  [ocean] _build_ocean_mesh: {time.time()-_t_ocean:.3f}s")
    if ocean_obj is not None:
        print(f"  [ocean] mesh verts={len(ocean_obj.data.vertices)}  faces={len(ocean_obj.data.polygons)}")
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

    elementMode = bpy.context.scene.tp3d.elementMode

    if elementMode == "PAINT":
        projection("paint", tile, ocean_obj)
        return None
    elif elementMode in ("SINGLECOLORMODE", "SINGLECOLORMODE_REMESH"):
        projection("singleColorMode_remesh", tile, ocean_obj)
        mat = bpy.data.materials.get("WATER")
        ocean_obj.data.materials.clear()
        ocean_obj.data.materials.append(mat)
        return ocean_obj
    elif elementMode == "SEPARATE":
        _t_proj = time.time()
        projection("separate", tile, ocean_obj)
        print(f"  [ocean] projection (separate): {time.time()-_t_proj:.3f}s")
        mat = bpy.data.materials.get("WATER")
        ocean_obj.data.materials.clear()
        ocean_obj.data.materials.append(mat)
        print(f"  [ocean] total: {time.time()-_t_ocean:.3f}s")
        recalculateNormals(ocean_obj)
        return ocean_obj

    return ocean_obj


def exaggeratedLayers(objs):
    from .scene import show_message_box  # deferred to avoid circular import at load time
    from .metadata import writeMetadata  # deferred to avoid circular import at load time

    selected_objects = objs

    layerThickness = 1

    size = bpy.context.scene.tp3d.objSize



    if not selected_objects:
        show_message_box("No Object Selected. Please select a Map first")
        return {'CANCELLED'}

    for obj in selected_objects:

        if "Object type" not in obj:
            continue
        if obj["Object type"] != "MAP":
            continue

        objs = list(bpy.context.scene.objects)
        for o in objs:
            if "Object type" in o and "PARENT" in o:
                if o["PARENT"] == obj and  o["Object type"] == "LINES":
                    bpy.data.objects.remove(o, do_unlink=True)

        # Deselect everything
        bpy.ops.object.select_all(action='DESELECT')

        # Create plane at 3D cursor
        bpy.ops.mesh.primitive_plane_add(size=size + 10, enter_editmode=False, align='WORLD',
                                        location=bpy.context.scene.cursor.location)
        plane = bpy.context.active_object
        plane.name = "CuttingPlane"
        plane.location.z += 0.1 + layerThickness/2

        # Add Array modifier in Z direction
        array_mod = plane.modifiers.new(name="ArrayZ", type='ARRAY')
        array_mod.relative_offset_displace = (0, 0, 0)   # disable relative offset
        array_mod.constant_offset_displace = (0, 0, layerThickness)   # fixed step in Z
        array_mod.use_relative_offset = False
        array_mod.use_constant_offset = True
        array_mod.count = 30  # you can adjust how many slices


        # Apply modifiers up to solidify
        bpy.context.view_layer.objects.active = plane
        bpy.ops.object.modifier_apply(modifier=array_mod.name)


        # Add Boolean modifier with INTERSECT mode
        bool_mod = plane.modifiers.new(name="Boolean", type='BOOLEAN')
        bool_mod.operation = 'INTERSECT'
        bool_mod.solver = 'FLOAT'  # or 'EXACT'
        bool_mod.use_self = False
        bool_mod.use_hole_tolerant = True  # helps with manifold issues
        bool_mod.object = obj

        plane.name = obj.name + "_LAYERS"

        bpy.ops.object.modifier_apply(modifier=bool_mod.name)


        # Add Solidify modifier for thickness
        solidify_mod = plane.modifiers.new(name="Solidify", type='SOLIDIFY')
        solidify_mod.thickness = layerThickness
        solidify_mod.offset = 0

        bpy.ops.object.modifier_apply(modifier=solidify_mod.name)

        mat = bpy.data.materials.get("WHITE")
        plane.data.materials.clear()
        plane.data.materials.append(mat)

        writeMetadata(plane,"LINES")
        plane["PARENT"] = obj




    bpy.ops.object.select_all(action='DESELECT')
    for obj in selected_objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = selected_objects[0]

def contourLines(objs):
    from .scene import show_message_box  # deferred to avoid circular import at load time
    from .metadata import writeMetadata  # deferred to avoid circular import at load time

    selected_objects = objs
    cl_thickness = bpy.context.scene.tp3d.cl_thickness
    cl_distance = bpy.context.scene.tp3d.cl_distance
    cl_offset = bpy.context.scene.tp3d.cl_offset

    size = bpy.context.scene.tp3d.objSize



    if not selected_objects:
        show_message_box("No Object Selected. Please select a Map first")
        return {'CANCELLED'}

    for obj in selected_objects:

        if "Object type" not in obj:
            continue
        if obj["Object type"] != "MAP":
            continue

        objs = list(bpy.context.scene.objects)
        for o in objs:
            if "Object type" in o and "PARENT" in o:
                if o["PARENT"] == obj and  o["Object type"] == "LINES":
                    bpy.data.objects.remove(o, do_unlink=True)

        # Deselect everything
        bpy.ops.object.select_all(action='DESELECT')

        # Create plane at 3D cursor
        bpy.ops.mesh.primitive_plane_add(size=size + 10, enter_editmode=False, align='WORLD',
                                        location=bpy.context.scene.cursor.location)
        plane = bpy.context.active_object
        plane.name = "CuttingPlane"
        plane.location.z += cl_offset

        # Add Array modifier in Z direction
        array_mod = plane.modifiers.new(name="ArrayZ", type='ARRAY')
        array_mod.relative_offset_displace = (0, 0, 0)   # disable relative offset
        array_mod.constant_offset_displace = (0, 0, cl_distance)   # fixed step in Z
        array_mod.use_relative_offset = False
        array_mod.use_constant_offset = True
        array_mod.count = 100  # you can adjust how many slices

        # Add Solidify modifier for thickness
        solidify_mod = plane.modifiers.new(name="Solidify", type='SOLIDIFY')
        solidify_mod.thickness = cl_thickness

        # Apply modifiers up to solidify
        bpy.context.view_layer.objects.active = plane
        bpy.ops.object.modifier_apply(modifier=array_mod.name)
        bpy.ops.object.modifier_apply(modifier=solidify_mod.name)

        # Add Boolean modifier with INTERSECT mode
        bool_mod = plane.modifiers.new(name="Boolean", type='BOOLEAN')
        bool_mod.operation = 'INTERSECT'
        bool_mod.solver = 'MANIFOLD'  # or 'EXACT'
        bool_mod.use_self = False
        bool_mod.use_hole_tolerant = True  # helps with manifold issues
        bool_mod.object = obj

        plane.name = obj.name + "_LINES"

        mat = bpy.data.materials.get("WHITE")
        plane.data.materials.clear()
        plane.data.materials.append(mat)

        writeMetadata(plane,"LINES")
        plane["PARENT"] = obj


        # Apply Boolean
        bpy.context.view_layer.objects.active = plane

        bpy.ops.object.modifier_apply(modifier=bool_mod.name)



    bpy.ops.object.select_all(action='DESELECT')
    for obj in selected_objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = selected_objects[0]
