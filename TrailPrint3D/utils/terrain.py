import bpy  # type: ignore
import bmesh  # type: ignore
import math
import time
from mathutils import Vector, Matrix, bvhtree  # type: ignore
from .. import progress as _progress

_COLORING_EMPTY = object()
_COLORING_PAINTED = object()


def coloring_main(map, kind = "WATER"):
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
    if lats * lons < 20:
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
                print("Fetching OSM")
                try:
                    result = fetch_osm_data(bbox, kind, return_cache_status=True)
                    if not result:
                        continue
                    resp, from_cache = result
                    if not resp:
                        continue

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
                    if calcArea > col_Area or 1 == 1:
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

                if not from_cache:
                    time.sleep(5)  # Pause to prevent request throttling
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
            bpy.ops.object.select_all(action='DESELECT')
            for ro in valid_ribbons:
                ro.select_set(True)
            bpy.context.view_layer.objects.active = valid_ribbons[0]
            bpy.ops.object.join()
            merged_ribbons = bpy.context.view_layer.objects.active
            merged_ribbons.name = "OpenObject_merged"
            created_objects.append(merged_ribbons)

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


    def _process_coloring_object(tobj, map_obj, tol=0.1, extrudeVal = 200):
        """Extrude, boolean-intersect with map, separate loose parts, fix normals.
        Returns (area, [resulting_objects]). Removes tobj if it becomes empty."""

        bpy.ops.object.select_all(action='DESELECT')

        mesh = tobj.data

        # Compute area and extrude in a single bmesh pass
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

        tobj.location.z -= 1
        recalculateNormals(tobj)

        # Boolean intersect with map
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

        # Separate loose parts
        bpy.ops.object.select_all(action='DESELECT')
        tobj.select_set(True)
        bpy.context.view_layer.objects.active = tobj
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.separate(type='LOOSE')
        bpy.ops.object.mode_set(mode='OBJECT')

        result_objects = []
        objects_to_remove = []
        for zobj in bpy.context.selected_objects:

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

        for cntr, tobj in enumerate(list(created_objects), start=1):
            area01 = getBottomFacesArea(tobj)
            if area01 < col_Area*3 and "OpenObject_" in tobj.name:
                print("Skipping OpenObject")
                bpy.data.objects.remove(tobj, do_unlink=True)
                continue
            if area01 < col_Area and "coloredObject_" in tobj.name:
                print("Skipping ClosedObject")
                bpy.data.objects.remove(tobj, do_unlink=True)
                continue

            area, new_objs = _process_coloring_object(tobj, map, tol, extrudeVal= 150)
            biggestArea = max(biggestArea, area)
            created_objects_booleaned.extend(new_objs)


        for cntr, tobj in enumerate(list(negative_object), start = 1):
            area, new_objs = _process_coloring_object(tobj,map,tol, extrudeVal = 200)
            biggestArea = max(biggestArea, area)
            for to in new_objs:
                set_origin_to_geometry(to)
                to.scale.z *= 2

            created_negatives_booleaned.extend(new_objs)
        print("subtracting negatives from element")
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
        print("finished subtracting")

        if biggestArea == 0:
            print("No Water Found on Tile")
            return

        print(f"{kind} objects to merge: {len(created_objects_booleaned)}")
        merged_object = merge_objects(created_objects_booleaned)

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
            KIND_MATERIAL_OVERRIDE = {
                "SCREE": "MOUNTAIN",
            }
            mat = bpy.data.materials.get(KIND_MATERIAL_OVERRIDE.get(kind, kind))
            merged_object.data.materials.clear()
            merged_object.data.materials.append(mat)

        if _ov.active:
            _ov.update(message=f"{kind.capitalize()}: applying Element handling option ({elementMode})")

        if "SINGLECOLORMODE" in elementMode and 1 == 0:
            _VALID_SHAPES = {
                "SQUARE", "HEXAGON", "HEXAGON INNER TEXT", "HEXAGON OUTER TEXT",
                "HEXAGON FRONT TEXT", "HEART", "OCTAGON", "OCTAGON OUTER TEXT",
                "CIRCLE", "MEDAL", "ELLIPSE",
            }
            _map_shape  = bpy.context.scene.tp3d.shape
            _obj_size   = bpy.context.scene.tp3d.objSize
            _clip_inset = bpy.context.scene.tp3d.elementModeInset

            if _map_shape in _VALID_SHAPES and _obj_size > 0:
                _clip_size = _obj_size - _clip_inset
                _half      = _clip_size / 2
                bpy.ops.object.select_all(action="DESELECT")
                if _map_shape == "SQUARE":
                    _rH = bpy.context.scene.tp3d.rectangleHeight
                    _clip_shape = create_rectangle(_clip_size, _rH, 1, "_clip_shape")
                elif _map_shape in {"HEXAGON", "HEXAGON INNER TEXT", "HEXAGON OUTER TEXT", "HEXAGON FRONT TEXT"}:
                    _clip_shape = create_hexagon(_half, 1, "_clip_shape")
                elif _map_shape == "HEART":
                    _clip_shape = create_heart(_half, 1, "_clip_shape")
                elif _map_shape in {"OCTAGON", "OCTAGON OUTER TEXT"}:
                    _clip_shape = create_octagon(_half, 1, "_clip_shape")
                elif _map_shape in {"CIRCLE", "MEDAL"}:
                    _clip_shape = create_circle(_half, 1, "_clip_shape")
                elif _map_shape == "ELLIPSE":
                    _ratio = bpy.context.scene.tp3d.ellipseRatio
                    _clip_shape = create_ellipse(_half, 1, "_clip_shape", _ratio)
                # Position at map XY, start 1 unit below to guarantee overlap
                _clip_shape.location.x = map.location.x
                _clip_shape.location.y = map.location.y
                _clip_shape.location.z = map.location.z - 1
                # Extrude upward by 50
                _bm = bmesh.new()
                _bm.from_mesh(_clip_shape.data)
                _geom = _bm.faces[:]
                _ret  = bmesh.ops.extrude_face_region(_bm, geom=_geom)
                _ext_verts = [v for v in _ret["geom"] if isinstance(v, bmesh.types.BMVert)]
                bmesh.ops.translate(_bm, verts=_ext_verts, vec=Vector((0, 0, 50)))
                _bm.to_mesh(_clip_shape.data)
                _bm.free()

                print("Yessir")
                # Intersect element against the clip shape (manifold solver)
                boolean_operation(merged_object, _clip_shape, 'INTERSECT')

                remove_objects(_clip_shape)

                recalculateNormals(merged_object)


        if elementMode == "PAINT":
            print("PAINTING")
            recalculateNormals(map)
            merged_object.location.z += 1
            color_map_faces_by_terrain(map, merged_object)
            mesh_data = merged_object.data

            #Delete the elements afterwards
            bpy.data.objects.remove(merged_object, do_unlink=True)
            bpy.data.meshes.remove(mesh_data)

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

    terrain_obj.location.z = 10
    bpy.context.view_layer.update()

    # Ensure both have mesh data
    map_mesh = map_obj.data
    terrain_mesh = terrain_obj.data

    # Build bmesh for Map
    bm = bmesh.new()
    bm.from_mesh(map_mesh)
    bm.faces.ensure_lookup_table()

    depsgraph = bpy.context.evaluated_depsgraph_get()
    eval_obj = terrain_obj.evaluated_get(depsgraph)

    eval_mesh = eval_obj.to_mesh()

    bm2 = bmesh.new()
    bm2.from_mesh(eval_mesh)

    bvh = bvhtree.BVHTree.FromBMesh(bm2)

    # Get or create a material for terrain color
    if terrain_obj.active_material:
        mat = terrain_obj.active_material
    else:
        mat = bpy.data.materials.new(name="TerrainColor")
        terrain_obj.data.materials.append(mat)

    # Make sure Map has material slots
    if mat.name not in [m.name for m in map_mesh.materials]:
        map_mesh.materials.append(mat)
    mat_index = map_mesh.materials.find(mat.name)

    up = Vector((0, 0, 1))
    colored_count = 0

    i = 0
    for i, f in enumerate(bm.faces):
        normal = f.normal.normalized()
        dot = normal.dot(up)
        # Only consider faces facing upward
        if dot > up_threshold:
            center = f.calc_center_median()
            center.z -= 5
            loc, norm, idx, dist = bvh.ray_cast(center, up,100)

            if loc is not None:
                # Assign terrain material to this face
                f.material_index = mat_index
                colored_count += 1

    bm.to_mesh(map_mesh)
    bm.free()
    bm2.free()
    eval_obj.to_mesh_clear()
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
    from .scene import set_origin_to_3d_cursor, projection  # deferred to avoid circular import at load time
    from .primitives import create_rectangle  # deferred to avoid circular import at load time

    coastcurve = create_element(bboxBigger, waterHeight, scaleHor, "COASTLINE", baseHeight * 3)
    set_origin_to_3d_cursor(coastcurve)
    print("coastcurve created")
    if coastcurve != None:

        shape = bpy.context.scene.tp3d.shape
        objSize = bpy.context.scene.tp3d.objSize
        if shape == "SQUARE":
            coastobj = create_rectangle(objSize,objSize)
        else:
            coastobj = create_rectangle(objSize,objSize)

        coastobj.name = "Ocean"
        coastobj.location.x = tile.location.x
        coastobj.location.y = tile.location.y
        coastobj.location.z = 0

        flip_override = bpy.context.scene.tp3d.el_oFlip
        merged_object = cut_coastline(coastcurve, coastobj, land_hints=landpoints, flip_override=flip_override)

        mat = bpy.data.materials.get("WATER")
        merged_object.data.materials.clear()
        merged_object.data.materials.append(mat)

        elementMode = bpy.context.scene.tp3d.elementMode
        if elementMode == "PAINT":
            projection("paint", tile, merged_object)
            return None
        elif elementMode == "SINGLECOLORMODE" or elementMode == "SINGLECOLORMODE_REMESH":
            projection("singleColorMode", tile, merged_object)
            mat = bpy.data.materials.get("WATER")
            merged_object.data.materials.clear()
            merged_object.data.materials.append(mat)
            return merged_object
        elif elementMode == "SEPARATE":
            projection("separate", tile, merged_object)
            return merged_object

    else:
        print("NO COASTCURVE FOR SOME REASON")


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
            bpy.ops.object.modifier_apply(modifier=bool_mod.name)
        except Exception as e:
            print("Warning: boolean modifier apply failed:", e)
            raise RuntimeError("Boolean operation failed: " + str(e))

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
