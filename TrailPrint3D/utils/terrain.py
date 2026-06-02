from .scene import remove_objects
from .mesh_ops import extrude_plane, recalculateNormals
import bpy  # type: ignore
import bmesh  # type: ignore
import math
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from mathutils import Vector, Matrix, bvhtree  # type: ignore
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
    """Fetch all active OSM kinds × all tiles in one parallel batch.

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

    # ── Regroup: (kind, [bboxes]) → {bbox: [kinds]} ──────────────────────
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
    from .osm import fetch_osm_data, build_osm_nodes, extract_multipolygon_bodies, calculate_polygon_area_2d, is_bbox_overlapping  # deferred to avoid circular import at load time
    from .geo import convert_to_blender_coordinates  # deferred to avoid circular import at load time
    from .primitives import col_create_face_mesh, create_ribbon_mesh, create_rectangle, create_hexagon, create_heart, create_octagon, create_circle, create_ellipse  # deferred to avoid circular import at load time
    from .mesh_ops import recalculateNormals, boolean_operation, merge_objects, getBottomFacesArea  # deferred to avoid circular import at load time
    from .scene import remove_objects, set_origin_to_3d_cursor, set_origin_to_geometry, show_message_box  # deferred to avoid circular import at load time
    from .metadata import writeMetadata  # deferred to avoid circular import at load time

    minLat = bpy.context.scene.tp3d.minLat
    minLon = bpy.context.scene.tp3d.minLon
    maxLat = bpy.context.scene.tp3d.maxLat
    maxLon = bpy.context.scene.tp3d.maxLon

    col_KeepManifold = (bpy.context.scene.tp3d.col_KeepManifold)
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

    created_objects = []
    negative_object = []
    ribbon_objects = []

    scaleHor = bpy.context.scene.tp3d.sScaleHor
    streamWidthMultiplier = bpy.context.scene.tp3d.col_wStreamWidth
    half_width = 1.0 * scaleHor * 0.02 * streamWidthMultiplier

    cntr = 0
    maxcntr = lats * lons
    if lats * lons < 20 or prefetched_tiles is not None:
        for k in range(lats):
            for l in range(lons):
                cntr = (k) * lons + l + 1
                print(f"{kind} loop: {((k) * lons + l + 1)}/{maxcntr}")
                _ov = _progress.ProgressOverlay.get()
                if _ov.active:
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
                    _ov.update(message=f"{kind.capitalize()}: tile {cntr}/{maxcntr} — creating bodies")

                for i, coords in enumerate(bodies):
                    blender_coords = [convert_to_blender_coordinates(lat, lon, ele, 0) for lat, lon, ele in coords]
                    calcArea = calculate_polygon_area_2d(blender_coords)
                    if calcArea > col_Area:
                        tobj = col_create_face_mesh(f"Relation_{i}", blender_coords)
                        created_objects.append(tobj)
                        waterCreated += 1
                    else:
                        waterDeleted += 1

                if _ov.active:
                    _ov.update(message=f"{kind.capitalize()}: tile {cntr}/{maxcntr} — creating negative bodies")

                for i, coords in enumerate(negatives):
                    blender_coords = [convert_to_blender_coordinates(lat, lon, ele, 0) for lat, lon, ele in coords]
                    calcArea = calculate_polygon_area_2d(blender_coords)
                    if calcArea > col_Area:
                        tobj = col_create_face_mesh(f"Relation_{i}", blender_coords)
                        negative_object.append(tobj)
                        waterCreated += 1
                    else:
                        waterDeleted += 1

                if _ov.active:
                    _ov.update(message=f"{kind.capitalize()}: tile {cntr}/{maxcntr} — creating ways")

                for i, element in enumerate(data['elements']):
                    if element['type'] != 'way':
                        waterDeleted += 1
                        continue
                    if element['id'] in relation_way_ids:
                        continue  # already processed as part of a relation

                    coords = []
                    for node_id in element.get('nodes', []):
                        if node_id in nodes:
                            node = nodes[node_id]
                            coord = convert_to_blender_coordinates(
                                node['lat'], node['lon'], 0,0
                            )
                            coords.append(coord)
                    if len(coords) < 2:
                        waterDeleted += 1
                        continue

                    if coords[0] == coords[-1]:
                        tArea = calculate_polygon_area_2d(coords)
                        if tArea < col_Area:
                            waterDeleted += 1
                            continue
                        tobj = col_create_face_mesh(f"coloredObject_{i}", coords)
                        created_objects.append(tobj)
                        waterCreated += 1
                    else:
                        pts = [Vector(c) for c in coords]
                        tobj = create_ribbon_mesh(f"OpenObject_{i}", pts, half_width)
                        if tobj:
                            ribbon_objects.append(tobj)
                            waterCreated += 1

                if not from_cache and prefetched_tiles is None:
                    time.sleep(5)  # Pause to prevent request throttling (skipped when worker pre-fetched)
    else:
        print(f"Region too big. Cant Fetch All {kind} Sources")
        return None

    if cntr < maxcntr:
        print("Not All data fetched")
        remove_objects(created_objects)

        print("Timed out. Cached already Fetched Data. Try Regenerating Again")
    else:
        if total_fetched == 0:
            _progress.WarningsOverlay.add_warning(f"No {kind.capitalize()} elements returned from API.", "warn")
            _api_empty = True
        elif waterCreated == 0:
            _progress.WarningsOverlay.add_warning(f"All {kind.capitalize()} elements are below the area threshold.", "warn")
            _api_empty = True


    # Merge all ribbon (open-way) objects into one before the boolean pass.
    # This reduces N individual boolean operations down to a single one.
    if ribbon_objects:
        valid_ribbons = [o for o in ribbon_objects if o and o.type == 'MESH']
        if valid_ribbons:
            if len(valid_ribbons) == 1:
                valid_ribbons[0].name = "OpenObject_merged"
                created_objects.append(valid_ribbons[0])
            else:
                # Merge all ribbon meshes using bmesh (no bpy.ops.object.join)
                bm_merged = bmesh.new()
                target_world_inv = valid_ribbons[0].matrix_world.inverted()
                bm_merged.from_mesh(valid_ribbons[0].data)
                for ro in valid_ribbons[1:]:
                    bm_part = bmesh.new()
                    bm_part.from_mesh(ro.data)
                    xform = target_world_inv @ ro.matrix_world
                    bmesh.ops.transform(bm_part, verts=bm_part.verts[:], matrix=xform)
                    tmp_mesh = bpy.data.meshes.new("_tp3d_merge_tmp")
                    bm_part.to_mesh(tmp_mesh)
                    bm_part.free()
                    bm_merged.from_mesh(tmp_mesh)
                    bpy.data.meshes.remove(tmp_mesh)
                    bpy.data.objects.remove(ro, do_unlink=True)
                bm_merged.to_mesh(valid_ribbons[0].data)
                bm_merged.free()
                valid_ribbons[0].name = "OpenObject_merged"
                created_objects.append(valid_ribbons[0])

    #Make sure the flat faces have the correct normal orientation
    UP = Vector((0,0,1))

    if created_objects:
        for obj in created_objects:
            mesh = obj.data
            bm = bmesh.new()
            bm.from_mesh(mesh)

            bm.normal_update()

            faces_to_flip = []

            for face in bm.faces:
                # Face normal is in object-local space
                if face.normal.dot(UP) > 0:
                    faces_to_flip.append(face)

            if faces_to_flip:
                bmesh.ops.reverse_faces(bm, faces=faces_to_flip)

            # Write back to mesh
            bm.to_mesh(mesh)
            bm.free()


    def _split_loose(obj):
        """Split obj into per-connected-component objects. Removes obj and returns list."""
        src_mesh = obj.data
        bm_src = bmesh.new()
        bm_src.from_mesh(src_mesh)
        bm_src.verts.ensure_lookup_table()

        visited = set()
        components = []
        for start in bm_src.verts:
            if start.index in visited:
                continue
            comp = set()
            stack = [start]
            while stack:
                v = stack.pop()
                if v.index in visited:
                    continue
                visited.add(v.index)
                comp.add(v.index)
                for edge in v.link_edges:
                    other = edge.other_vert(v)
                    if other.index not in visited:
                        stack.append(other)
            components.append(comp)

        collection = obj.users_collection[0] if obj.users_collection else bpy.context.scene.collection
        world_matrix = obj.matrix_world.copy()
        parts = []
        for comp_indices in components:
            comp_faces = [f for f in bm_src.faces
                          if all(v.index in comp_indices for v in f.verts)]
            bm_new = bmesh.new()
            idx_map = {}
            for vi in comp_indices:
                nv = bm_new.verts.new(bm_src.verts[vi].co.copy())
                idx_map[vi] = nv
            bm_new.verts.ensure_lookup_table()
            for f in comp_faces:
                try:
                    bm_new.faces.new([idx_map[v.index] for v in f.verts])
                except ValueError:
                    pass  # duplicate face edge — skip
            new_mesh = bpy.data.meshes.new(obj.name)
            bm_new.to_mesh(new_mesh)
            bm_new.free()
            part = bpy.data.objects.new(obj.name, new_mesh)
            part.matrix_world = world_matrix
            collection.objects.link(part)
            parts.append(part)

        bm_src.free()
        bpy.data.objects.remove(obj, do_unlink=True)
        return parts

    def _process_coloring_object(tobj, map_obj, tol=0.1, extrudeVal = 200):
        """Extrude, boolean-intersect with map, separate loose parts, fix normals.
        Returns (area, [resulting_objects]). Removes tobj if it becomes empty."""
        _t_pco = time.time()

        bpy.ops.object.select_all(action='DESELECT')

        mesh = tobj.data

        # Compute area and extrude in a single bmesh pass
        _t = time.time()
        bm = bmesh.new()
        bm.from_mesh(mesh)
        if not bm.faces:
            bm.free()
            bpy.data.objects.remove(tobj, do_unlink=True)
            return 0, []
        area = sum(f.calc_area() for f in bm.faces)
        geom = bm.faces[:]
        ret = bmesh.ops.extrude_face_region(bm, geom=geom)
        extruded_verts = [v for v in ret["geom"] if isinstance(v, bmesh.types.BMVert)]
        bmesh.ops.translate(bm, verts=extruded_verts, vec=Vector((0, 0, extrudeVal)))
        bm.to_mesh(mesh)
        bm.free()
        print(f"    [pco:{tobj.name}] extrude: {time.time()-_t:.3f}s")

        tobj.location.z -= 1
        recalculateNormals(tobj)

    

        # Boolean intersect with map
        _t = time.time()
        bool_mod = tobj.modifiers.new(name="Boolean", type='BOOLEAN')
        bool_mod.object = map_obj
        bool_mod.operation = 'INTERSECT'
        bool_mod.solver = 'MANIFOLD'

        depsgraph = bpy.context.evaluated_depsgraph_get()
        eval_obj = tobj.evaluated_get(depsgraph)
        new_mesh = bpy.data.meshes.new_from_object(eval_obj)
        tobj.modifiers.clear()
        old_mesh = tobj.data
        tobj.data = new_mesh
        bpy.data.meshes.remove(old_mesh)
        print(f"    [pco:{tobj.name}] boolean intersect (MANIFOLD): {time.time()-_t:.3f}s  verts={len(new_mesh.vertices)}")

        # Mark lowest vertices
        bm = bmesh.new()
        bm.from_mesh(tobj.data)
        if not bm.verts:
            bm.free()
            bpy.data.objects.remove(tobj, do_unlink=True)
            return 0, []
        min_z = min(v.co.z for v in bm.verts)
        lowestVert = float("inf")
        for v in bm.verts:
            if abs(v.co.z - min_z) < tol:
                v.select = True
            else:
                v.select = False
                lowestVert = min(lowestVert, v.co.z)
        bm.to_mesh(tobj.data)
        bm.free()

        recalculateNormals(tobj)

        # Separate loose parts — uses outer _split_loose helper
        _t = time.time()
        _tobj_name = tobj.name  # capture before _split_loose removes the object

        result_objects = []
        objects_to_remove = []
        for zobj in _split_loose(tobj):

            mesh_area = getBottomFacesArea(zobj)
            if mesh_area < col_Area:
                objects_to_remove.append(zobj)
                continue


            DOWN = Vector((0, 0, -1))
            zmesh = zobj.data
            bm = bmesh.new()
            bm.from_mesh(zmesh)
            bm.normal_update()


            lowest_face = None
            lowest_z = float('inf')
            for face in bm.faces:
                z = face.calc_center_median().z
                if z < lowest_z and face.calc_area() > 0:
                    lowest_z = z
                    lowest_face = face

            if lowest_face and lowest_face.normal.dot(DOWN) <= 0:
                bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
                print(f"Reversing obj {zobj} at z:{lowest_z}")

            bm.to_mesh(zmesh)
            bm.free()
            result_objects.append(zobj)

        remove_objects(objects_to_remove)

        print(f"    [pco:{_tobj_name}] split_loose: {time.time()-_t:.3f}s  parts={len(result_objects)}")
        print(f"    [pco] total: {time.time()-_t_pco:.3f}s")
        return area, result_objects


    created_objects_booleaned = []
    created_negatives_booleaned = []
    merged_object = None

    if _ov.active:
        _ov.update(message=f"{kind.capitalize()}: process parts, boolean with map, and merge")

    if created_objects:
        print(f"Unique objects from element:{ len(created_objects)}")
        bpy.ops.object.select_all(action='DESELECT')
        biggestArea = 0
        tol = 0.1

        if elementMode == "PAINT":
            # ── PAINT-mode fast path ──────────────────────────────────────────
            # Skip per-object MANIFOLD boolean against terrain.
            # color_map_faces_by_terrain builds its BVH from the cutter's LOCAL
            # mesh (world transform is not applied). OSM polygons sit at Z=0 in
            # local space (objects are at origin, so local == world). We extrude
            # them to (terrain_max_z + 50) so the top face is:
            #   • above the terrain  (> terrain_max_z)
            #   • within ray range   (ray distance=100, starts at face_z-5)
            map_world_verts = [map.matrix_world @ Vector(v) for v in map.bound_box]
            terrain_max_z = max(v.z for v in map_world_verts)
            extrude_z = terrain_max_z + 50.0
            print(f"  [PAINT fast path] terrain_max_z={terrain_max_z:.2f}  extrude_z={extrude_z:.2f}")

            _t_paint_fast = time.time()
            paint_cutters = []
            for tobj in list(created_objects):
                area01 = getBottomFacesArea(tobj)
                if area01 < col_Area*3 and "OpenObject_" in tobj.name:
                    bpy.data.objects.remove(tobj, do_unlink=True)
                    continue
                if area01 < col_Area and "coloredObject_" in tobj.name:
                    bpy.data.objects.remove(tobj, do_unlink=True)
                    continue
                mesh = tobj.data
                bm = bmesh.new()
                bm.from_mesh(mesh)
                if not bm.faces:
                    bm.free()
                    bpy.data.objects.remove(tobj, do_unlink=True)
                    continue
                biggestArea = max(biggestArea, sum(f.calc_area() for f in bm.faces))
                geom = bm.faces[:]
                ret = bmesh.ops.extrude_face_region(bm, geom=geom)
                extruded_verts = [v for v in ret["geom"] if isinstance(v, bmesh.types.BMVert)]
                # Extrude to just above terrain so top face falls within the
                # 100-unit ray distance used by color_map_faces_by_terrain.
                bmesh.ops.translate(bm, verts=extruded_verts, vec=Vector((0, 0, extrude_z)))
                bm.to_mesh(mesh)
                bm.free()
                # Keep location at (0,0,0) — local == world, BVH matches ray space.
                paint_cutters.append(tobj)
            # ribbon_objects were bmesh-merged into created_objects above as
            # "OpenObject_merged" — do NOT re-iterate here (stale refs).

            print(f"  [coloring_main] PAINT extrude ({kind}, {len(paint_cutters)} objs): {time.time()-_t_paint_fast:.3f}s")

            for obj in list(negative_object):
                bpy.data.objects.remove(obj, do_unlink=True)
            negative_object.clear()
            ribbon_objects.clear()

            if not paint_cutters:
                if _api_empty:
                    return _COLORING_EMPTY
                return None

            _t_merge = time.time()
            cutter = merge_objects(paint_cutters, name=f"{name}_{kind}")
            print(f"  [coloring_main] PAINT merge ({kind}): {time.time()-_t_merge:.3f}s")

            if cutter is None:
                return None

            writeMetadata(cutter, kind)
            mat = bpy.data.materials.get(KIND_MATERIAL_OVERRIDE.get(kind, kind))
            cutter.data.materials.clear()
            cutter.data.materials.append(mat)

            if _ov.active:
                _ov.update(message=f"{kind.capitalize()}: painting terrain faces")

            print(f"PAINTING ({kind})")
            _t_paint = time.time()
            color_map_faces_by_terrain(map, cutter)
            mesh_data = cutter.data
            bpy.data.objects.remove(cutter, do_unlink=True)
            bpy.data.meshes.remove(mesh_data)
            print(f"  [coloring_main] PAINT total ({kind}): {time.time()-_t_paint:.3f}s")
            return _COLORING_PAINTED
            # ── end PAINT-mode fast path ──────────────────────────────────────

        # Per-object extrude + MANIFOLD boolean — no recalculateNormals (avoids mode-switch cost).
        _t_proc_loop = time.time()
        DOWN = Vector((0, 0, -1))
        _t_extrude_total = _t_bool_total = _t_split_total = 0.0
        for tobj in list(created_objects):
            area01 = getBottomFacesArea(tobj)
            if area01 < col_Area*3 and "OpenObject_" in tobj.name:
                bpy.data.objects.remove(tobj, do_unlink=True)
                continue
            if area01 < col_Area and "coloredObject_" in tobj.name:
                bpy.data.objects.remove(tobj, do_unlink=True)
                continue
            mesh = tobj.data
            _ta = time.time()
            bm = bmesh.new()
            bm.from_mesh(mesh)
            if not bm.faces:
                bm.free()
                bpy.data.objects.remove(tobj, do_unlink=True)
                continue
            biggestArea = max(biggestArea, sum(f.calc_area() for f in bm.faces))
            geom = bm.faces[:]
            ret = bmesh.ops.extrude_face_region(bm, geom=geom)
            extruded_verts = [v for v in ret["geom"] if isinstance(v, bmesh.types.BMVert)]
            bmesh.ops.translate(bm, verts=extruded_verts, vec=Vector((0, 0, 200)))
            # Recalc normals via bmesh (no mode-switch overhead).
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
            bm.to_mesh(mesh)
            bm.free()
            tobj.location.z -= 1
            _t_extrude_total += time.time() - _ta

            # Per-object MANIFOLD boolean intersect with the map.
            _tb = time.time()
            bool_mod = tobj.modifiers.new(name="Boolean", type='BOOLEAN')
            bool_mod.object = map
            bool_mod.operation = 'INTERSECT'
            bool_mod.solver = 'MANIFOLD'
            depsgraph = bpy.context.evaluated_depsgraph_get()
            eval_obj = tobj.evaluated_get(depsgraph)
            new_mesh = bpy.data.meshes.new_from_object(eval_obj)
            tobj.modifiers.clear()
            old_mesh = tobj.data
            tobj.data = new_mesh
            bpy.data.meshes.remove(old_mesh)
            _t_bool_total += time.time() - _tb

            if not new_mesh.vertices:
                bpy.data.objects.remove(tobj, do_unlink=True)
                continue
            

            # Split loose parts, fix normals, filter by area.
            _tc = time.time()
            for zobj in _split_loose(tobj):
                mesh_area = getBottomFacesArea(zobj)
                if mesh_area < col_Area:
                    bpy.data.objects.remove(zobj, do_unlink=True)
                    continue
                zmesh = zobj.data
                bm = bmesh.new()
                bm.from_mesh(zmesh)
                bm.normal_update()
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
                created_objects_booleaned.append(zobj)
            _t_split_total += time.time() - _tc
        print(f"  [coloring_main] process objects ({kind}, {len(created_objects)} objs): {time.time()-_t_proc_loop:.3f}s  {len(created_objects_booleaned)} results")
        print(f"    extrude={_t_extrude_total:.3f}s  boolean={_t_bool_total:.3f}s  split={_t_split_total:.3f}s  other={time.time()-_t_proc_loop-_t_extrude_total-_t_bool_total-_t_split_total:.3f}s")

        for cntr, tobj in enumerate(list(negative_object), start = 1):
            area, new_objs = _process_coloring_object(tobj,map,tol, extrudeVal = 200)
            biggestArea = max(biggestArea, area)
            for to in new_objs:
                set_origin_to_geometry(to)
                to.scale.z *= 2

            created_negatives_booleaned.extend(new_objs)
        print(f"subtracting negatives from element ({len(negative_object)} objs)")
        _t_neg = time.time()
        if created_negatives_booleaned:
            # Union all negatives into one cutter so each positive needs only one boolean op
            if len(created_negatives_booleaned) > 1:
                merged_negative = merge_objects(created_negatives_booleaned, name="MergedNegative")
            else:
                merged_negative = created_negatives_booleaned[0]
            for o1 in created_objects_booleaned:
                if is_bbox_overlapping(o1, merged_negative):
                    boolean_operation(o1, merged_negative)
                    recalculateNormals(o1)
            bpy.data.objects.remove(merged_negative, do_unlink=True)
        print(f"finished subtracting ({time.time()-_t_neg:.3f}s)")

        if biggestArea == 0:
            print(f"No {kind} Found on Tile")
            _progress.WarningsOverlay.add_warning(f"All {kind.capitalize()} objects were filtered out due to their size", "warn")
            return _COLORING_FILTERED

        print(f"{kind} objects to merge: {len(created_objects_booleaned)}")
        _t_merge = time.time()
        merged_object = merge_objects(created_objects_booleaned)
        print(f"  [coloring_main] merge_objects ({kind}): {time.time()-_t_merge:.3f}s")

        bpy.ops.object.origin_set(type='ORIGIN_CURSOR', center='MEDIAN')

        if merged_object is None:
            print("No Mesh left after Merging")
            return

        bm = bmesh.new()
        bm.from_mesh(merged_object.data)

        min_z = min(v.co.z for v in bm.verts)
        lowestVert = 100
        for v in bm.verts:
            if abs(v.co.z - min_z) < tol:
                pass
            else:
                if v.co.z < lowestVert and v.co.z >= bpy.context.scene.tp3d.minThickness:
                    lowestVert = v.co.z
        for v in bm.verts:
                if abs(v.co.z - min_z) < tol:
                    pass
                    v.co.z = lowestVert - 1

        bm.to_mesh(merged_object.data)
        bm.free()

        bpy.ops.object.mode_set(mode="OBJECT")


        merged_object.location.z += 0.2
        merged_object.name = name + "_" + kind

        bpy.context.view_layer.objects.active = merged_object
        merged_object.select_set(True)

        if merged_object:
            writeMetadata(merged_object, kind)
            mat = bpy.data.materials.get(KIND_MATERIAL_OVERRIDE.get(kind, kind))
            merged_object.data.materials.clear()
            merged_object.data.materials.append(mat)

        if _ov.active:
            _ov.update(message=f"{kind.capitalize()}: applying Element handling option ({elementMode})")

        if elementMode == "PAINT":
            print(f"PAINTING ({kind})")
            _t_paint = time.time()
            recalculateNormals(map)
            merged_object.location.z += 1
            color_map_faces_by_terrain(map, merged_object)
            mesh_data = merged_object.data

            #Delete the elements afterwards
            bpy.data.objects.remove(merged_object, do_unlink=True)
            bpy.data.meshes.remove(mesh_data)
            print(f"  [coloring_main] PAINT total ({kind}): {time.time()-_t_paint:.3f}s")

    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':  # make sure it's a 3D Viewport
            for space in area.spaces:
                if space.type == 'VIEW_3D':
                    space.shading.type = 'MATERIAL'  # switch shading


    bpy.context.preferences.edit.use_global_undo = True

    if elementMode != "PAINT" and merged_object is not None:
        return merged_object
    if elementMode == "PAINT" and merged_object is not None:
        return _COLORING_PAINTED   # objects were painted onto the map then deleted
    if _api_empty:
        return _COLORING_EMPTY
    return None

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
    terrain_mesh = terrain_obj.data
 

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


def createOcean(bboxBigger, waterHeight, scaleHor, landpoints, baseplate, tile, baseHeight):
    from .osm import create_element  # deferred to avoid circular import at load time
    from .scene import set_origin_to_3d_cursor  # deferred to avoid circular import at load time
    from .mesh_ops import projection  # deferred to avoid circular import at load time
    from .primitives import create_rectangle  # deferred to avoid circular import at load time

    _t_ocean = time.time()
    coastcurve = create_element(bboxBigger, waterHeight, scaleHor, "COASTLINE", baseHeight * 3)
    print(f"  [ocean] create_element (coastline fetch): {time.time()-_t_ocean:.3f}s")
    if coastcurve is not None:
        set_origin_to_3d_cursor(coastcurve)

        shape = bpy.context.scene.tp3d.shape
        objSize = bpy.context.scene.tp3d.objSize
        rHeight = bpy.context.scene.tp3d.rectangleHeight
        if shape == "SQUARE":
            coastobj = create_rectangle(objSize, rHeight)
        else:
            coastobj = create_rectangle(objSize, objSize)

        coastobj.name = "Ocean"
        coastobj.location.x = tile.location.x
        coastobj.location.y = tile.location.y
        coastobj.location.z = 0

        flip_override = bpy.context.scene.tp3d.el_oFlip
        _t_cut = time.time()
        merged_object = cut_coastline(coastcurve, coastobj, land_hints=landpoints, flip_override=flip_override)
        print(f"  [ocean] cut_coastline: {time.time()-_t_cut:.3f}s  ({len(merged_object.data.polygons) if merged_object else 0} faces)")

        #REMOVE THE CUTTER OBJECT
        remove_objects(coastcurve)

        mat = bpy.data.materials.get("WATER")
        merged_object.data.materials.clear()
        merged_object.data.materials.append(mat)


        #merged_object.location.z = tile["lowestZ"] - 0.5
        #return merged_object

        elementMode = bpy.context.scene.tp3d.elementMode
        #
        if elementMode == "PAINT":
            projection("paint", tile, merged_object)
            return None
        elif elementMode == "SINGLECOLORMODE" or elementMode == "SINGLECOLORMODE_REMESH":
            projection("singleColorMode", tile, merged_object)
            mat = bpy.data.materials.get("WATER")
            merged_object.data.materials.clear()
            merged_object.data.materials.append(mat)
            return merged_object
        if elementMode == "SEPARATE":
            projection("separate", tile, merged_object)
            print(f"  [ocean] projection (separate): {time.time()-_t_proj:.3f}s")
            mat = bpy.data.materials.get("WATER")
            merged_object.data.materials.clear()
            merged_object.data.materials.append(mat)
            print(f"  [ocean] total: {time.time()-_t_ocean:.3f}s")
            return merged_object

    else:
        from .. import progress as _progress  # deferred to avoid circular import at load time
        _progress.WarningsOverlay.add_warning("No coastline data found for this area — ocean layer skipped.", "warn")
        return None


def cut_coastline(curve_obj, target_obj, land_hints=None, flip_override=False):
    """
    Cut the target_obj using the curve_obj so there are multiple separate objects
    Main use case -> cut it at the coastline
    land_hints: optional list of (x, y[, z]) points known to be on land,
                used to ensure the boolean removes the land side.
    flip_override: if True, inverts the auto-detected flip decision (manual correction).
    """
    from .primitives import curve_to_mesh_object  # deferred to avoid circular import at load time
    from .mesh_ops import removeDoubles, extrude_plane  # deferred to avoid circular import at load time
    from .scene import remove_objects  # deferred to avoid circular import at load time

    extrude_depth = -0.5
    solidify = False
    solidify_thickness = 0.02

    ctx = bpy.context
    scene = ctx.scene

    # sanity checks
    if curve_obj is None or target_obj is None:
        raise ValueError("curve_obj and target_obj must be provided")
    if curve_obj.type != 'CURVE':
        raise ValueError("curve_obj must be a Curve object")
    if target_obj.type != 'MESH':
        raise ValueError("target_obj must be a Mesh object")

    # remember current state to restore later
    prev_mode = ctx.mode

    created_objects = []
    try:
        # --- 1) get world-space points from the curve (first spline) ---
        spline = None
        if len(curve_obj.data.splines) == 0:
            raise RuntimeError("Curve has no splines")
        spline = curve_obj.data.splines[0]

        cutter_obj = curve_to_mesh_object(curve_obj, "Cutter")

        removeDoubles(cutter_obj)


        bm = bmesh.new()
        bm.from_mesh(cutter_obj.data)


        for e in bm.edges:
            e.select = True

        #Extrude cutter
        bmesh.ops.translate(bm, verts=bm.verts, vec=(0, 0, extrude_depth/2))
        geom_extrude = bmesh.ops.extrude_edge_only(bm, edges=[e for e in bm.edges])

        # Move new geometry
        verts_extruded = [v for v in geom_extrude["geom"] if isinstance(v, bmesh.types.BMVert)]
        bmesh.ops.translate(bm, verts=verts_extruded, vec=(0, 0, -extrude_depth))

        # Write back to mesh
        bm.to_mesh(cutter_obj.data)
        bm.free()

        extrude_plane(target_obj,-0.1)



        # --- 2b) flip cutter normals so they point away from land (toward ocean) ---
        if land_hints:
            # 1) Winding: signed area of the curve polygon (shoelace)
            spline0 = curve_obj.data.splines[0]
            mw_curve = curve_obj.matrix_world
            if spline0.type == 'BEZIER':
                pts2d = [mw_curve @ bp.co for bp in spline0.bezier_points]
            else:
                pts2d = [mw_curve @ Vector((p.co.x, p.co.y, p.co.z)) for p in spline0.points]
            pts2d = [Vector((v.x, v.y)) for v in pts2d]

            signed_area = 0.0
            n_pts = len(pts2d)
            for i in range(n_pts):
                j = (i + 1) % n_pts
                signed_area += pts2d[i].x * pts2d[j].y - pts2d[j].x * pts2d[i].y
            is_ccw = signed_area > 0  # CCW -> normals outward; CW -> normals inward

            # 2) Majority vote: are most land points inside the curve polygon?
            # Ray-casting point-in-polygon (counts edge crossings along +X ray)
            def point_in_poly(px, py, poly):
                inside = False
                j = len(poly) - 1
                for i in range(len(poly)):
                    xi, yi = poly[i].x, poly[i].y
                    xj, yj = poly[j].x, poly[j].y
                    if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
                        inside = not inside
                    j = i
                return inside

            inside_count = sum(
                1 for p in land_hints
                if point_in_poly(float(p[0]), float(p[1]), pts2d)
            )
            land_is_inside = inside_count > len(land_hints) / 2

            # 3) Flip when normals point toward land; flip_override inverts the decision
            auto_flip = (is_ccw and not land_is_inside) or (not is_ccw and land_is_inside)
            should_flip = auto_flip != flip_override
            print(f"cut_coastline: is_ccw={is_ccw}, land_inside={land_is_inside}, auto_flip={auto_flip}, override={flip_override}, final={should_flip}")


            if should_flip:
                bm_flip = bmesh.new()
                bm_flip.from_mesh(cutter_obj.data)
                bmesh.ops.reverse_faces(bm_flip, faces=list(bm_flip.faces))
                bm_flip.to_mesh(cutter_obj.data)
                bm_flip.free()
                cutter_obj.data.update()


        # ensure cutter has correct transform (we added world coords directly so keep identity)
        cutter_obj.matrix_world = Matrix() if hasattr(bpy, 'Matrix') else cutter_obj.matrix_world  # type: ignore

        created_objects.append(cutter_obj)


        # --- 3) optionally add solidify modifier (useful for thin geometry) ---
        if solidify:
            sm = cutter_obj.modifiers.new(name="CutterSolidify", type='SOLIDIFY')
            sm.thickness = solidify_thickness
            ctx.view_layer.objects.active = cutter_obj
            cutter_obj.select_set(True)
            try:
                pass
            except Exception as e:
                print("Warning: failed to apply solidify modifier:", e)

        # --- 4) perform boolean difference: target_obj = target_obj - cutter_obj ---
        # ensure we are in object mode
        if ctx.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        # store list of existing objects for later diff
        before_objs = set(bpy.data.objects)


        # add boolean modifier on target
        ctx.view_layer.objects.active = target_obj
        target_obj.select_set(True)
        cutter_obj.select_set(True)

        bool_mod = target_obj.modifiers.new(name="AutoBooleanCut", type='BOOLEAN')
        bool_mod.operation = 'DIFFERENCE'
        bool_mod.object = cutter_obj
        bool_mod.solver = 'EXACT'


        # try applying modifier
        try:
            print("APPLYING MODIFIER")
            _t_bool = time.time()
            bpy.ops.object.modifier_apply(modifier=bool_mod.name)
            print(f"  [cut_coastline] boolean apply: {time.time()-_t_bool:.3f}s")
        except Exception as e:
            print("Warning: boolean modifier apply failed:", e)
            raise RuntimeError("Boolean operation failed: " + str(e))

        #REMOVE THE CUTTER OBJECT
        remove_objects(cutter_obj)


        # Remove all vertices from target_obj that are not on Z=0
        bpy.context.view_layer.objects.active = target_obj
        bpy.ops.object.mode_set(mode='EDIT')
        bm = bmesh.from_edit_mesh(target_obj.data)
        bm.verts.ensure_lookup_table()
        verts_to_delete = [v for v in bm.verts if abs(v.co.z) > 1e-6]
        bmesh.ops.delete(bm, geom=verts_to_delete, context='VERTS')
        bmesh.update_edit_mesh(target_obj.data)
        bpy.ops.object.mode_set(mode='OBJECT')


        return target_obj

    finally:
        # ensure we return to previous mode
        try:
            if prev_mode != bpy.context.mode:
                bpy.ops.object.mode_set(mode=prev_mode)
        except Exception:
            pass


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

        if not "Object type" in obj:
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

        if not "Object type" in obj:
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
