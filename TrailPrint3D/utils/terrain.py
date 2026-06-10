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
    kind       : OSM feature kind string ('WATER', 'FOREST', â€¦)
    semaphore  : threading.Semaphore â€” limits concurrent live requests to the
                 Overpass API (callers typically use Semaphore(2))
    settings   : OsmFetchSettings snapshot read on the main thread before this
                 function is called.  Passed through to fetch_osm_data so that
                 worker threads never touch bpy.context.
    max_workers: thread-pool size (default 4)

    Returns
    -------
    dict mapping bbox tuple -> (data_dict, from_cache_bool)
    Only tiles that fetched successfully are present in the result.

    NOTE: bpy.* calls are forbidden inside this function â€” it runs on worker
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
    """Fetch all active OSM kinds Ã— all tiles in one parallel batch.

    Each unique tile bbox is fetched with a **single** Overpass union request
    that covers every active kind for that tile.  This replaces the previous
    N-kinds Ã— T-tiles individual request strategy and drastically reduces the
    number of concurrent Overpass connections, avoiding rate-limit errors.

    The shared *semaphore* still caps the number of live Overpass requests
    (callers use Semaphore(2)); because each tile now maps to exactly one
    request, the semaphore is acquired only during the actual network call.

    Parameters
    ----------
    kind_task_pairs : list of (kind_str, tasks_list) â€” one entry per active kind
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

    # â"€â"€ Regroup: (kind, [bboxes]) â†' {bbox: [kinds]} â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
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
    from .primitives import col_create_face_mesh, create_ribbon_mesh  # deferred to avoid circular import at load time
    from .mesh_ops import recalculateNormals, boolean_operation, merge_objects, getBottomFacesArea  # deferred to avoid circular import at load time
    from .scene import remove_objects, set_origin_to_geometry, show_message_box  # deferred to avoid circular import at load time
    from .metadata import writeMetadata  # deferred to avoid circular import at load time

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
                    if prefetched_tiles is not None:
                        _ov.update(message=f"{kind.capitalize()}: tile {cntr}/{maxcntr} â€” processingâ€¦")
                    else:
                        _ov.update(message=f"{kind.capitalize()}: tile {cntr}/{maxcntr} â€” fetchingâ€¦")
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
                    _ov.update(message=f"{kind.capitalize()}: tile {cntr}/{maxcntr} â€” calculating mesh ({n_features} features, {src})â€¦")
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
                    _ov.update(message=f"{kind.capitalize()}: tile {cntr}/{maxcntr} â€” creating bodies")

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
                    _ov.update(message=f"{kind.capitalize()}: tile {cntr}/{maxcntr} â€” creating negative bodies")

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
                    _ov.update(message=f"{kind.capitalize()}: tile {cntr}/{maxcntr} â€” creating ways")

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
                    pass  # duplicate face edge â€” skip
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

        # Separate loose parts â€” uses outer _split_loose helper
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
            # â"€â"€ PAINT-mode fast path â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
            # Skip per-object MANIFOLD boolean against terrain.
            # color_map_faces_by_terrain builds its BVH from the cutter's LOCAL
            # mesh (world transform is not applied). OSM polygons sit at Z=0 in
            # local space (objects are at origin, so local == world). We extrude
            # them to (terrain_max_z + 50) so the top face is:
            #   â€¢ above the terrain  (> terrain_max_z)
            #   â€¢ within ray range   (ray distance=100, starts at face_z-5)
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
                # Keep location at (0,0,0) â€” local == world, BVH matches ray space.
                paint_cutters.append(tobj)
            # ribbon_objects were bmesh-merged into created_objects above as
            # "OpenObject_merged" â€” do NOT re-iterate here (stale refs).

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
            # â"€â"€ end PAINT-mode fast path â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

        # Per-object extrude + MANIFOLD boolean â€” no recalculateNormals (avoids mode-switch cost).
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

    up_threshold = dot(normal, Z) must be greater than this (0.5 ~ 60Â° angle limit).
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
 

    # Build bmesh for Map â€” read LOCAL mesh, transform centers to WORLD space via matrix_world
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
    open_chains  : list of [(x,y), â€¦]  â€” chains that still start/end on the
                   map-tile boundary (neither endpoint meets the other)
    closed_loops : list of [(x,y), â€¦]  â€” chains whose first â‰ˆ last point
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

    *chain*   : list of (x,y) in Blender space â€” land-is-left direction.
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
            # Point is not near any edge â€” clamp to nearest
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
                # Start and end are the same point on the bbox â€” degenerate
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


def _punch_island_holes(outer_poly, island_loops, min_area=0.0):
    """Connect island loops into outer_poly via bridge edges (zero-width slits).

    Converts a polygon-with-holes into a single simply-connected polygon
    without any boolean operations.  For each island we:
      1. Find the outer poly vertex closest to the island centroid.
      2. Find the island vertex closest to that outer vertex.
      3. Insert the island loop at that connection, creating a slit that
         goes: outer→bridge→island loop→bridge back→outer continues.

    Islands whose area is below *min_area* (Blender units²) are skipped —
    they are too small to be worth a colour change on a 3D print.

    The result is a flat list of (x,y) that col_create_face_mesh can
    create as a single face — Blender sees one face with an interior hole
    traced as a boundary re-entrant path.
    """
    result = list(outer_poly)
    # Track which indices in `result` are original outer-boundary vertices.
    # After each punch, the outer indices grow by (len(island)+1) but the
    # original outer verts are still at the same relative positions — we
    # must never pick a bridge-slit vertex as the anchor for the next island
    # or the new bridge will cross the previous slit.
    outer_indices = list(range(len(result)))

    for island in island_loops:
        if min_area > 0 and _polygon_area(island) < min_area:
            continue
        if len(island) < 3:
            continue
        # Centroid of island → nearest OUTER-BOUNDARY vertex (never a bridge vertex)
        cx = sum(p[0] for p in island) / len(island)
        cy = sum(p[1] for p in island) / len(island)
        best_oi = min(outer_indices,
                      key=lambda i: (result[i][0] - cx) ** 2 + (result[i][1] - cy) ** 2)
        ox, oy = result[best_oi]
        best_ii = min(range(len(island)),
                      key=lambda i: (island[i][0] - ox) ** 2 + (island[i][1] - oy) ** 2)
        # Rotate island so the closest vertex (best_ii) is at index 0,
        # giving the shortest possible bridge edge.
        island_rot = island[best_ii:] + island[:best_ii]
        # Bridge: outer[0..best_oi] → island_rot → island_rot[0] → outer[best_oi..]
        insert_at = result.index(result[best_oi], best_oi, best_oi + 1)
        result = result[:insert_at + 1] + island_rot + [island_rot[0]] + result[insert_at:]
        # The inserted block is (len(island_rot) + 1) new entries.
        # Shift all outer_indices that came after the insertion point.
        shift = len(island_rot) + 1
        outer_indices = [
            i if i <= insert_at else i + shift
            for i in outer_indices
        ]
    return result


def _close_chains_with_bbox(chains, bbox_bl):
    """Build a single ocean polygon from ALL clipped+simplified open chains.

    Building one polygon per chain and merging causes overlap when multiple
    chains exist â€” their individual ocean polygons union to cover the wrong
    side.  Instead, we interleave ALL chain segments with CW bbox perimeter
    walks to form one correct closed polygon.

    Algorithm:
    - Each chain carries land on its left (OSM convention).  The CW perimeter
      walk between chain endpoints traces the ocean-side boundary.
    - Starting from the chain whose END has the largest CCW perimeter param,
      we alternate: CW perimeter arc â†' follow chain forward â†' CW perimeter
      arc â†' follow next chain forward â†' â€¦ until all chains are consumed.

    Returns a list of (x,y), or None if degenerate.
    """
    if not chains:
        return None

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

    # Build per-chain CCW params
    info = []
    for ch in chains:
        if len(ch) >= 2:
            info.append([_ccw(ch[0]), _ccw(ch[-1]), ch, False])

    if not info:
        return None

    # Start from the chain whose START has the largest CCW param (first in CW
    # processing order).  Output it directly without a pre-chain arc, add CW
    # arcs between consecutive chains, then close with one final arc from the
    # last chain's end back to the first chain's start.
    #
    # The old code used max(end_ccw) and placed the closing arc at the
    # beginning via _cw_corners(si.end, si.start).  For diagonal two-land
    # tiles (land top-left + bottom-right) that arc goes the long way around
    # the perimeter and drags in wrong corners.
    si = max(range(len(info)), key=lambda i: info[i][0])
    first_start = info[si][0]
    polygon = []
    idx = si
    current_end = None

    for _ in range(len(info)):
        sp, ep, chain, used = info[idx]
        if used:
            break
        info[idx][3] = True

        if current_end is not None:
            # CW perimeter corners from previous chain's end to this chain's start
            polygon.extend(_cw_corners(current_end, sp))
        # Follow chain forward (land on left -> ocean polygon traces correctly)
        polygon.extend(chain)
        current_end = ep

        # Next chain: smallest positive CW distance from current_end to a start
        best_i, best_d = -1, float("inf")
        for i, (s, e, c, v) in enumerate(info):
            if v:
                continue
            d = (current_end - s) % 4.0
            if 0 < d < best_d:
                best_d, best_i = d, i
        if best_i < 0:
            break
        idx = best_i

    # Closing arc: CW from the last chain's end back to the first chain's start.
    if current_end is not None:
        polygon.extend(_cw_corners(current_end, first_start))

    return polygon if len(polygon) >= 3 else None


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
                     here â€” island subtraction on a flat 2D polygon is
                     unreliable with boolean solvers; projection() clips to
                     actual terrain geometry which handles it naturally)
    *bbox_bl*      : (min_x, min_y, max_x, max_y) in Blender local space
    *tile*         : the map mesh object (used only for location reference)

    Returns a Blender mesh object or None.
    """
    from .primitives import col_create_face_mesh  # deferred to avoid circular import at load time
    from .mesh_ops import merge_objects  # deferred to avoid circular import at load time

    ocean_faces = []

    if open_chains:
        # Clip each chain to the bbox and simplify, then build ONE polygon from
        # all chains combined (not one per chain â€” separate polygons overlap).
        good_chains = []
        for chain in open_chains:
            segments = _clip_chain_to_bbox(chain, bbox_bl)
            if not segments:
                print(f"    [ocean mesh] chain {len(chain)} pts â†' clipped to nothing, skip")
                continue
            for clipped in segments:
                simplified = _rdp_simplify(clipped, epsilon=0.1)
                if len(simplified) < 3:
                    print(f"    [ocean mesh] chain {len(chain)} pts â†' clipped {len(clipped)} â†' RDP {len(simplified)}, skip (degenerate)")
                    continue
                print(f"    [ocean mesh] chain {len(chain)} pts -> clipped {len(clipped)} -> RDP {len(simplified)}")
                if bpy.app.debug:
                    print(f"      raw  start={chain[0]}  end={chain[-1]}")
                    print(f"      clip start={clipped[0]}  end={clipped[-1]}")
                    print(f"      rdp  start={simplified[0]}  end={simplified[-1]}")
                    _ci = len(good_chains)
                    _dbg_x = 0.0
                    _dbg_step = 150.0
                    _debug_add_poly(f"chain_raw_{_ci}",     chain,      offset=(_dbg_x,                  -_dbg_step * (_ci + 1), 0.1))
                    _debug_add_poly(f"chain_clipped_{_ci}", clipped,    offset=(_dbg_x + _dbg_step,     -_dbg_step * (_ci + 1), 0.1))
                    _debug_add_poly(f"chain_rdp_{_ci}",     simplified, offset=(_dbg_x + _dbg_step * 2, -_dbg_step * (_ci + 1), 0.1))
                good_chains.append(simplified)

        if good_chains:
            # Filter out chains whose endpoints are so close together on the bbox
            # perimeter that they form a degenerate sliver (e.g. a tiny inlet that
            # clips to just a notch on one edge).  Minimum CCW span = 0.05 (5% of
            # one edge length).
            min_x, min_y, max_x, max_y = bbox_bl
            W = max(max_x - min_x, 1e-9)
            H = max(max_y - min_y, 1e-9)
            def _ccw_param(pt):
                x = max(min_x, min(max_x, pt[0]))
                y = max(min_y, min(max_y, pt[1]))
                ds = [abs(y - min_y), abs(x - max_x), abs(y - max_y), abs(x - min_x)]
                e = ds.index(min(ds))
                if e == 0: return (x - min_x) / W
                if e == 1: return 1.0 + (y - min_y) / H
                if e == 2: return 2.0 + (max_x - x) / W
                return       3.0 + (max_y - y) / H
            filtered = []
            for ch in good_chains:
                sp = _ccw_param(ch[0])
                ep = _ccw_param(ch[-1])
                span = (ep - sp) % 4.0   # CCW span from start to end
                #commented out for now as it was filtering out valid chains in some cases
                #if span < 0.05 or span > 3.95:
                #    print(f"    [ocean mesh] skipping sliver chain ({len(ch)} pts, CCW span={span:.3f})")
                #    continue
                print(f"    [ocean mesh] chain {len(ch)} pts: start CCW={sp:.3f}  end CCW={ep:.3f}  span={span:.3f}")
                filtered.append(ch)
            good_chains = filtered
            poly = _close_chains_with_bbox(good_chains, bbox_bl)
            if poly and len(poly) >= 3:
                if bpy.app.debug:
                    print(f"    [ocean mesh] raw closed polygon: {len(poly)} pts")
                    _debug_add_poly("ocean_polygon_pre_islands",  poly, offset=(0.0,   -450.0, 0.1))
                # Punch island holes directly into the polygon using bridge edges.
                # This is done in Python geometry (no booleans) so it works on a
                # flat non-manifold face and survives the projection pipeline intact.
                if closed_loops:
                    simplified_islands = [
                        s for s in (
                            _rdp_simplify(loop, epsilon=0.1)
                            for loop in closed_loops
                            if len(loop) >= 3
                        )
                        if len(s) >= 3
                    ]
                    if simplified_islands:
                        tp3d_ctx = bpy.context.scene.tp3d
                        min_area = getattr(tp3d_ctx, 'el_oMinIslandArea', 4.0)
                        before = len(simplified_islands)
                        if bpy.app.debug:
                            for ii, isl in enumerate(simplified_islands):
                                area = _polygon_area(isl)
                                kept = area >= min_area
                                print(f"      island[{ii}]: {len(isl)} pts  area={area:.3f}  {'KEEP' if kept else f'SKIP (<{min_area})'}")
                                _debug_add_poly(f"island_{'kept' if kept else 'skipped'}_{ii}", isl, offset=(150.0 * (ii % 8), -600.0 - 150.0 * (ii // 8), 0.1))
                        poly = _punch_island_holes(poly, simplified_islands, min_area=min_area)
                        skipped = sum(1 for s in simplified_islands if _polygon_area(s) < min_area)
                        print(f"    [ocean mesh] punched {before - skipped}/{before} island holes (skipped {skipped} below {min_area} units²)")
                        if bpy.app.debug:
                            _debug_add_poly("ocean_polygon_post_islands", poly, offset=(150.0, -450.0, 0.1))
                pts3d = [(x, y, 0.0) for x, y in poly]
                face_obj = col_create_face_mesh("_OceanFace", pts3d)
                if face_obj and len(face_obj.data.vertices) > 0:
                    ocean_faces.append(face_obj)

    if not ocean_faces:
        # Either no open chains (tile entirely ocean) or all chain polys were
        # degenerate â†' fall back to full bbox rectangle.
        min_x, min_y, max_x, max_y = bbox_bl
        pts3d = [
            (min_x, min_y, 0.0),
            (max_x, min_y, 0.0),
            (max_x, max_y, 0.0),
            (min_x, max_y, 0.0),
        ]
        face_obj = col_create_face_mesh("_OceanFace", pts3d)
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

    return ocean_obj


def createOcean(prefetched_coastline, scaleHor, tile):
    """Build the ocean layer mesh from pre-fetched coastline data.

    Uses the land-is-left OSM convention to construct the ocean polygon
    directly â€” no boolean cutters, no EXACT solver.

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
    from .mesh_ops import projection  # deferred to avoid circular import at load time
    from .. import constants as _const  # deferred to avoid circular import at load time

    _t_ocean = time.time()

    raw_chains = fetch_coastline_ways(prefetched_coastline, scaleHor)
    print(f"  [ocean] fetch_coastline_ways: {len(raw_chains)} raw ways  ({time.time()-_t_ocean:.3f}s)")

    if not raw_chains:
        _progress.WarningsOverlay.add_warning(
            "No coastline data found for this area â€” ocean layer skipped.", "warn"
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
            "Could not build ocean polygon â€” ocean layer skipped.", "warn"
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
        projection("singleColorMode", tile, ocean_obj)
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
