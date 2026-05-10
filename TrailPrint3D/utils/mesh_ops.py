import bpy  # type: ignore
import bmesh  # type: ignore
from mathutils import Vector, bvhtree  # type: ignore


def applyModifier(obj, modifier):
        depsgraph = bpy.context.evaluated_depsgraph_get()
        eval_obj = obj.evaluated_get(depsgraph)

        new_mesh = bpy.data.meshes.new_from_object(eval_obj)

        old_mesh = obj.data
        obj.data = new_mesh
        obj.modifiers.remove(modifier)

        bpy.data.meshes.remove(old_mesh)


def recalculateNormals(obj, ins = False):
    '''
    OLD WAY THAT DIDNT WORK FOR COMPLETELY FLIPPED VOLUMES


    mesh = obj.data

    bm = bmesh.new()
    bm.from_mesh(mesh)

    # recalc normals outward
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    '''
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.normals_make_consistent(inside=ins)
    bpy.ops.object.mode_set(mode='OBJECT')


def selectBottomFaces(obj):

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    if obj is None or obj.type != 'MESH':
        raise Exception("Please select a mesh object.")


    # Enter Edit Mode
    bpy.ops.object.mode_set(mode='EDIT')
    mesh = bmesh.from_edit_mesh(obj.data)

    # Recalculate normals
    #bmesh.ops.recalc_face_normals(mesh, faces=mesh.faces)

    # Threshold for downward-facing
    threshold = -0.95

    # Object world matrix for local-to-global transformation
    world_matrix = obj.matrix_world


    for f in mesh.faces:
        if f.normal.normalized().z < threshold:
            f.select = True  # Optional: visually select in viewport
        else:
            f.select = False

    # Update the mesh
    bmesh.update_edit_mesh(obj.data, loop_triangles=False)


def selectBottomFacesByZ(obj, tolerance=0.01):
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    bpy.ops.object.mode_set(mode='OBJECT')
    bottom_z = min(v.co.z for v in obj.data.vertices)

    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='VERT')
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    for v in bm.verts:
        v.select = abs(v.co.z - bottom_z) <= tolerance
    bmesh.update_edit_mesh(obj.data)


def getBottomFacesArea(obj):
    if obj is None or obj.type != 'MESH':
        return 0.0
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    area = sum(f.calc_area() for f in bm.faces if f.normal.normalized().z < -0.95)
    bm.free()
    return area


def selectTopFaces(obj):
    if obj is None or obj.type != 'MESH':
        raise Exception("Please select a mesh object.")


    # Enter Edit Mode
    bpy.ops.object.mode_set(mode='EDIT')
    mesh = bmesh.from_edit_mesh(obj.data)

    # Recalculate normals
    bmesh.ops.recalc_face_normals(mesh, faces=mesh.faces)

    # Threshold for downward-facing
    threshold = 0.99

    # Object world matrix for local-to-global transformation
    world_matrix = obj.matrix_world


    for f in mesh.faces:
        if f.normal.normalized().z > threshold:
            f.select = True  # Optional: visually select in viewport
        else:
            f.select = False

    # Update the mesh
    bmesh.update_edit_mesh(obj.data, loop_triangles=False)


def extrude_plane(obj, value=1.0, bydistance = True):

    #bydistance = True: Extrudes by value
    #bydistance = False: Extrudes and sets all vertices to the value

    # Ensure we are working on a mesh
    if obj.type != 'MESH':
        print("Not a mesh object.")
        return

    # Get mesh data and make an editable BMesh copy
    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)

    # Select all faces (or you could select specific ones)
    for f in bm.faces:
        f.select = True

    # Perform extrusion
    ret = bmesh.ops.extrude_face_region(bm, geom=bm.faces)

    # Move the newly extruded region along its normals
    verts_extruded = [ele for ele in ret["geom"] if isinstance(ele, bmesh.types.BMVert)]

    if bydistance == True:
        bmesh.ops.translate(bm, verts=verts_extruded, vec=(0, 0, value))
    if bydistance == False:
        for v in verts_extruded:
            v.co.z = value

    # Write back to mesh
    bm.to_mesh(mesh)
    bm.free()

    mesh.update()
    bpy.context.view_layer.update()


def merge_by_distance(obj, distance=0.01):
    # Make sure we're in Object Mode
    bpy.ops.object.mode_set(mode='OBJECT')

    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)

    # Merge
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=distance)

    # Write back
    bm.to_mesh(mesh)
    bm.free()

    # Update mesh
    mesh.update()


def merge_objects(objects, name="MergedObject"):
    """
    Simple UI-style merge: select objects, make the first one active, and call join.
    This is fast but requires changing selection/context.
    """

    print("This one")
    # filter only mesh objects
    #mesh_objs = [o for o in objects if o.type == 'MESH']
    mesh_objs = objects
    if not mesh_objs:
        return None


    # ensure in same collection / visible
    bpy.ops.object.select_all(action='DESELECT')
    for o in mesh_objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = mesh_objs[0]

    # join (applies no modifiers by default; if you want modifiers applied, make sure to apply them beforehand)
    bpy.ops.object.join()

    # the joined object is now the active object
    joined = bpy.context.view_layer.objects.active
    joined.name = name


    return joined


def removeDoubles(obj):

    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    bpy.ops.object.mode_set(mode="EDIT")

    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=0.0001)

    bpy.ops.object.mode_set(mode="OBJECT")


def delete_non_manifold(object):

    bpy.ops.object.select_all(action="DESELECT")

    #if the mergeobject is a Text object -> Convert it into a mesh
    object.select_set(True)
    bpy.context.view_layer.objects.active = object

    # Make sure you're in edit mode
    bpy.ops.object.mode_set(mode='EDIT')

    # Get the active mesh
    obj = bpy.context.edit_object
    me = obj.data

    # Access the BMesh representation
    bm = bmesh.from_edit_mesh(me)

    # Ensure the mesh has up-to-date normals and edges
    bm.normal_update()

    # Deselect everything first (optional)
    bpy.ops.mesh.select_all(action='DESELECT')

    # Select non-manifold edges
    bpy.ops.mesh.select_non_manifold()

    # (Optional) Update the mesh to reflect selection in UI
    bmesh.update_edit_mesh(me, loop_triangles=True)

    bpy.ops.mesh.delete(type='VERT')

    bpy.ops.object.mode_set(mode='OBJECT')


def delete_selected_verts(obj):
    # Must be in Edit Mode
    if obj.mode != 'EDIT':
        bpy.ops.object.mode_set(mode='EDIT')

    # Get the BMesh representation
    me = obj.data
    bm = bmesh.from_edit_mesh(me)

    # Gather selected vertices
    verts_to_delete = [v for v in bm.verts if v.select]

    # Use bmesh.ops to delete them
    # context='VERTS' also deletes connected edges and faces
    bmesh.ops.delete(bm, geom=verts_to_delete, context='VERTS')

    # Update the mesh and viewport
    bmesh.update_edit_mesh(me)


def boolean_operation(obj_a, obj_b, operation='DIFFERENCE'):
    """
    Performs a Boolean operation on obj_a with obj_b using the MANIFOLD solver.

    operation: 'UNION', 'INTERSECT', or 'DIFFERENCE'
    """
    # Ensure both objects exist
    if obj_a is None or obj_b is None:
        print("Error: One of the objects is None")
        return None

    # Add Boolean modifier to obj_a
    mod = obj_a.modifiers.new(name="BooleanManifold", type='BOOLEAN')
    mod.object = obj_b
    mod.operation = operation
    mod.solver = 'MANIFOLD'

    # Apply the modifier — obj_b must be viewport-visible or Blender refuses to apply
    prev_hide = obj_b.hide_viewport
    obj_b.hide_viewport = False
    bpy.context.view_layer.objects.active = obj_a
    bpy.ops.object.modifier_apply(modifier=mod.name)
    obj_b.hide_viewport = prev_hide

    return obj_a


def splitCurves(obj):
    if not obj or obj.type != 'CURVE':
        return []

    original_spline_count = len(obj.data.splines)
    if original_spline_count <= 1:
        return [obj]

    new_objects = []

    #Create a duplicate for every spline
    for i in range(original_spline_count):
        # Create a full copy of the object and its data
        new_obj = obj.copy()
        new_obj.data = obj.data.copy()
        bpy.context.collection.objects.link(new_obj)

        # Remove all splines EXCEPT the one for this index
        # loop backwards to avoid index shifting errors during removal
        splines = new_obj.data.splines
        for j in range(len(splines) - 1, -1, -1):
            if j != i:
                splines.remove(splines[j])

        new_objects.append(new_obj)

    #Clean up the original consolidated object
    bpy.data.objects.remove(obj, do_unlink=True)

    return new_objects


def point_inside(obj, point, direction=(0,0,-1), eps=1e-6):
    """
    Check if a world-space point is inside a mesh object using raycasting.
    Handles global coordinates properly.
    """
    if not obj or obj.type != 'MESH':
        return False

    deps = bpy.context.evaluated_depsgraph_get()
    eval_obj = obj.evaluated_get(deps)

    # Build BVH in **object-local space**
    tree = bvhtree.BVHTree.FromObject(eval_obj, deps)
    if not tree:
        return False

    # Convert world-space point and direction into **object local space**
    inv_mat = eval_obj.matrix_world.inverted()
    local_point = inv_mat @ Vector(point)
    local_dir   = inv_mat.to_3x3() @ Vector(direction)
    local_dir.normalize()

    # offset slightly backward to avoid starting exactly on geometry
    origin = local_point - local_dir * eps

    hit = tree.ray_cast(origin, local_dir, 1e12)

    return bool(hit and hit[0])


def RaycastPointToMeshZ(point, mesh_obj, offset_z=1000.0):
    """
    Raycast downward from a point onto a mesh and return the hit Z value.

    :param point: Vector or tuple (x, y, z)
    :param mesh_obj: Blender mesh object to raycast against
    :param offset_z: How far up to move the ray start before casting down
    :return: float Z value of hit location in world space, or None if no hit
    """

    point = Vector(point)

    # Move start point up so we can raycast downward
    ray_origin_world = point + Vector((0, 0, offset_z))
    ray_direction_world = Vector((0, 0, -1))

    # Get evaluated mesh (important for modifiers)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    eval_mesh_obj = mesh_obj.evaluated_get(depsgraph)

    # Transform into mesh local space
    mesh_world = eval_mesh_obj.matrix_world
    mesh_world_inv = mesh_world.inverted()

    ray_origin_local = mesh_world_inv @ ray_origin_world
    ray_direction_local = (mesh_world_inv.to_3x3() @ ray_direction_world).normalized()

    # Raycast
    success, hit_loc, normal, face_index = eval_mesh_obj.ray_cast(
        ray_origin_local,
        ray_direction_local
    )

    if not success:
        return None

    # Convert hit back to world space
    hit_world = mesh_world @ hit_loc

    return hit_world.z


def RaycastCurveToMesh(curve_obj, mesh_obj):

    #MOVE EVERY POINT UP BY 100 SO ITS POSSIBLE TO RAYCAST IT DOWNARDS ONTO THE MESH
    offset = Vector((0, 0, 1000))
    for spline in curve_obj.data.splines:
        if spline.type == 'BEZIER':
            for p in spline.bezier_points:
                p.co += offset
                # if you want to move the handles too:
                p.handle_left += offset
                p.handle_right += offset
        else:  # POLY / NURBS
            for p in spline.points:
                p.co = (p.co.x, p.co.y, p.co.z + offset.z, p.co.w)

    depsgraph = bpy.context.evaluated_depsgraph_get()
    eval_mesh_obj = mesh_obj.evaluated_get(depsgraph)

    curve_world     = curve_obj.matrix_world
    curve_world_inv = curve_world.inverted()

    mesh_world     = eval_mesh_obj.matrix_world
    mesh_world_inv = mesh_world.inverted()

    direction_world = Vector((0, 0, -1))  # world -Z
    direction_local = (mesh_world_inv.to_3x3() @ direction_world).normalized()

    for spline in curve_obj.data.splines:
        if spline.type == 'BEZIER':
            points = spline.bezier_points
        else:
            points = spline.points

        for point in points:
            # world-space position of curve point
            if spline.type == 'BEZIER':
                co_world = curve_world @ point.co
            else:
                co_world = curve_world @ point.co.xyz

            # convert origin to mesh local
            co_local = mesh_world_inv @ co_world

            # raycast in mesh local space
            success, hit_loc, normal, face_index = eval_mesh_obj.ray_cast(co_local, direction_local)

            if success:
                # back to world space
                hit_world = mesh_world @ hit_loc
                # then into curve local
                local_hit = curve_world_inv @ hit_world

                if spline.type == 'BEZIER':
                    point.co = local_hit
                    point.handle_left_type = point.handle_right_type = 'AUTO'
                else:
                    point.co = (local_hit.x, local_hit.y, local_hit.z, 1.0)
            else:
                point.co -= Vector((offset.x, offset.y, offset.z, 1.0))

    bpy.context.view_layer.objects.active = curve_obj
    bpy.ops.object.mode_set(mode='EDIT')

    # select all points if you want to smooth everything
    bpy.ops.curve.select_all(action='SELECT')

    # run the smooth operator
    bpy.ops.curve.smooth()

    # back to Object Mode if you like
    bpy.ops.object.mode_set(mode='OBJECT')

    print("Path Elevation Overwritten")


def RaycastCurveToAnyMesh(curve_obj, offset_z=1000.0, smooth_after=True):
    """Move curve points up by offset_z then raycast straight down onto any mesh below.
    Uses scene.ray_cast so the nearest mesh hit is used automatically.
    """

    scene = bpy.context.scene
    depsgraph = bpy.context.evaluated_depsgraph_get()

    offset = Vector((0.0, 0.0, offset_z))

    # Move points up by offset so we can raycast downwards
    for spline in curve_obj.data.splines:
        if spline.type == 'BEZIER':
            for p in spline.bezier_points:
                p.co += offset
                p.handle_left += offset
                p.handle_right += offset
        else:  # POLY / NURBS
            for p in spline.points:
                # p.co is (x, y, z, w)
                p.co = (p.co.x, p.co.y, p.co.z + offset_z, p.co.w)

    curve_world     = curve_obj.matrix_world
    curve_world_inv = curve_world.inverted()

    # ray direction in world space: straight down
    direction_world = Vector((0.0, 0.0, -1.0))

    for spline in curve_obj.data.splines:
        if spline.type == 'BEZIER':
            points = spline.bezier_points
        else:
            points = spline.points

        for point in points:
            # get world-space position of the point
            if spline.type == 'BEZIER':
                co_world = curve_world @ point.co
            else:
                co_world = curve_world @ point.co.xyz

            # cast ray from this origin straight down in world-space
            # scene.ray_cast returns (result, location, normal, face_index, object, matrix)
            hit_result = scene.ray_cast(depsgraph, co_world, direction_world)
            hit_success = hit_result[0]

            if hit_success:
                hit_loc_world = hit_result[1]

                # convert hit back into curve local space
                local_hit = curve_world_inv @ hit_loc_world

                if spline.type == 'BEZIER':
                    point.co = local_hit
                    # keep handles auto to get a reasonable shape; alternatively compute
                    point.handle_left_type = point.handle_right_type = 'AUTO'
                else:
                    # preserve w component
                    w = getattr(point.co, "w", 1.0)
                    point.co = (local_hit.x, local_hit.y, local_hit.z, w)

    # optional smoothing (go into edit mode, smooth, come back)
    if smooth_after:
        bpy.context.view_layer.objects.active = curve_obj
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.curve.select_all(action='SELECT')
        bpy.ops.curve.smooth()
        bpy.ops.object.mode_set(mode='OBJECT')


def intersectWithTile(tile, element, extrude_amount=1.0):
#'Intersects Element with Tile in x and y so the element fits on the tile shape'

    try:
        # Validate input objects

        if tile.type != 'MESH':
            raise ValueError(f"Tile object '{tile.name}' is not a mesh (type={tile.type}).")

        if element.type != 'MESH':
            print("Obj is not a mesh")
            raise ValueError(f"Element object '{element.name}' is not a mesh (type={element.type}).")

        # Remember current mode and active object so we can restore later
        prev_mode = bpy.context.mode
        prev_active = bpy.context.view_layer.objects.active

        # Duplicate the tile (object + mesh data copy)
        dup = tile.copy()
        dup.data = tile.data.copy()
        dup.name = f"{tile.name}_dup_for_bool"
        # Link to the same collection(s) as the original (common behaviour)
        # If the original isn't in any collection, link to current collection
        if tile.users_collection:
            for coll in tile.users_collection:
                coll.objects.link(dup)
        else:
            bpy.context.collection.objects.link(dup)

        # Make sure duplicate is selected and active
        bpy.ops.object.select_all(action='DESELECT')
        dup.select_set(True)
        bpy.context.view_layer.objects.active = dup

        # Add Solidify modifier to create an extrusion (thickness in Blender units)
        # We set use_rim to True so caps are created, giving a closed volume suitable for Boolean
        #solid_mod = dup.modifiers.new(name="__auto_solidify__", type='SOLIDIFY')
        #solid_mod.thickness = extrude_amount
        #solid_mod.offset = 1.0    # push outwards relative to normals
        #solid_mod.use_rim = True
        #solid_mod.use_even_offset = True
        dup.scale.z = 50

        # Apply the Solidify modifier (ensure we're in OBJECT mode)
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        #bpy.ops.object.modifier_apply(modifier=solid_mod.name)

        # Ensure the duplicate has up-to-date transforms applied for Boolean reliability
        # (optional but often useful)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

        # Prepare the Element object for boolean
        bpy.ops.object.select_all(action='DESELECT')
        element.select_set(True)
        bpy.context.view_layer.objects.active = element

        bool_mod = element.modifiers.new(name="__auto_boolean__", type='BOOLEAN')
        bool_mod.operation = 'INTERSECT'
        bool_mod.object = dup
        bool_mod.solver = 'MANIFOLD'

        # Apply the boolean modifier
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.modifier_apply(modifier=bool_mod.name)

        # Delete the duplicated tile
        bpy.ops.object.select_all(action='DESELECT')
        dup.select_set(True)
        bpy.context.view_layer.objects.active = dup
        bpy.ops.object.delete()

        # Restore previous active object and mode (if possible)
        if prev_active and prev_active.name in bpy.data.objects:
            bpy.context.view_layer.objects.active = prev_active
        try:
            if prev_mode != bpy.context.mode:
                bpy.ops.object.mode_set(mode=prev_mode)
        except Exception:
            # ignoring mode restore errors (some modes cannot be restored trivially)
            pass

        return True, "Boolean INTERSECT applied and duplicate tile removed."

    except Exception as e:
        # Attempt to clean up the duplicate if it exists
        dup_obj = bpy.data.objects.get(f"{tile.name}_duplicate_for_bool")
        if dup_obj:
            try:
                bpy.ops.object.select_all(action='DESELECT')
                dup_obj.select_set(True)
                bpy.context.view_layer.objects.active = dup_obj
                bpy.ops.object.delete()
            except Exception:
                pass
        return False, f"Error: {e}"


def intersect_alltrails_with_existing_box(cutobject):
    #cutobject is the object that will be cut to the Map shapes
    cutobject.scale.z = 1000


    robj2 = None

    bpy.ops.object.select_all(action='DESELECT')

    cutobject.select_set(True)
    bpy.context.view_layer.objects.active = cutobject
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    #cube = bpy.data.objects.get(cutobject)
    cube = cutobject
    if not cube:
        print(f"Object named '{cutobject}' not found.")
        return

    # Get cube's bounding box in world coordinates
    cube_bb = [cube.matrix_world @ Vector(corner) for corner in cube.bound_box]


    def is_point_inside_cube(point, bb):
        min_corner = Vector((min(v[0] for v in bb),
                             min(v[1] for v in bb),
                             min(v[2] for v in bb)))
        max_corner = Vector((max(v[0] for v in bb),
                             max(v[1] for v in bb),
                             max(v[2] for v in bb)))
        return all(min_corner[i] <= point[i] <= max_corner[i] for i in range(3))
    done = False
    boolObjects = []
    trail_mesh = None
    for robj in bpy.data.objects:
        if "_Trail" in robj.name and robj.type in {'CURVE', 'MESH'}:
            if not robj.hide_get():
                # Convert curve to mesh
                if robj.type == 'CURVE':
                    bpy.context.view_layer.objects.active = robj
                    bpy.ops.object.select_all(action='DESELECT')
                    robj2 = robj.copy()
                    robj2.data = robj.data.copy()
                    bpy.context.collection.objects.link(robj2)
                    robj2.select_set(True)
                    bpy.ops.object.convert(target='MESH')
                    trail_mesh = robj2
                else:
                    trail_mesh = robj

                #robj.hide_set(True)

                if trail_mesh:
                    if trail_mesh.type == "MESH" and len(trail_mesh.data.vertices) > 0:
                        # Check if any vertex is inside the cube
                        for v in trail_mesh.data.vertices:
                            global_coord = trail_mesh.matrix_world @ v.co
                            if is_point_inside_cube(global_coord, cube_bb):
                                # Apply Boolean modifier
                                #print(f"{trail_mesh.name} is inside the Boundaries")
                                if trail_mesh not in boolObjects:
                                    boolObjects.append(trail_mesh)
                                #Set done to True so it doesnt delete the object later
                                done = True
                                #Change Collection
                                continue  # No need to keep checking this object
                            else:
                                pass
                                #print(f"{trail_mesh.name} is NOT inside the Boundaries")
                    else:
                        print("No Vertices for Trail Found")
                        bpy.data.objects.remove(trail_mesh, do_unlink=True)

                #bpy.data.objects.remove(robj, do_unlink=True)
                #break
    if done == False:
        bpy.data.objects.remove(cutobject, do_unlink=True)
        if trail_mesh and trail_mesh.name in bpy.data.objects:
            bpy.data.objects.remove(trail_mesh, do_unlink=True)

    #Pfade kopieren, zusammenfügen und die boolean operation mit allen trails kombiniert ausführen
    if done == True:
        copied_objects = []
        #Copy objects
        for obj in boolObjects:
            obj_copy = obj.copy()
            obj_copy.data = obj.data.copy()
            bpy.context.collection.objects.link(obj_copy)
            copied_objects.append(obj_copy)

        #Deselect all
        bpy.ops.object.select_all(action='DESELECT')

        #Select all copied objects and make one active
        for obj in copied_objects:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = copied_objects[0]

        #Join them into a single object
        bpy.ops.object.join()

        merged_object = bpy.context.active_object

        bool_mod = cube.modifiers.new(name=f"Intersect", type='BOOLEAN')
        bool_mod.operation = 'INTERSECT'
        bool_mod.object = merged_object
        bpy.context.view_layer.objects.active = cube
        bpy.ops.object.modifier_apply(modifier=bool_mod.name)

        bpy.data.objects.remove(merged_object, do_unlink=True)
        if robj2 != None:
            bpy.data.objects.remove(robj2, do_unlink=True)

        mat = bpy.data.materials.get("TRAIL")
        cube.data.materials.clear()
        cube.data.materials.append(mat)

        from .metadata import writeMetadata  # deferred to avoid circular import at load time
        writeMetadata(cube,"TRAIL")


def intersect_trail_with_existing_box(cutobject,trail):
    #cutobject is the object that will be cut to the Map shapes
    cutobject.scale.z = 1000

    print(f"Trail name {trail.name}")

    robj2 = None

    bpy.ops.object.select_all(action='DESELECT')

    cutobject.select_set(True)
    bpy.context.view_layer.objects.active = cutobject
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    #cube = bpy.data.objects.get(cutobject)
    cube = cutobject
    if not cube:
        print(f"Object named '{cutobject}' not found.")
        return

    # Get cube's bounding box in world coordinates
    cube_bb = [cube.matrix_world @ Vector(corner) for corner in cube.bound_box]


    def is_point_inside_cube(point, bb):
        min_corner = Vector((min(v[0] for v in bb),
                             min(v[1] for v in bb),
                             min(v[2] for v in bb)))
        max_corner = Vector((max(v[0] for v in bb),
                             max(v[1] for v in bb),
                             max(v[2] for v in bb)))
        return all(min_corner[i] <= point[i] <= max_corner[i] for i in range(3))
    done = False
    boolObjects = []
    trail_mesh = trail if trail.type == 'MESH' else None
    if trail.type == 'CURVE':
        bpy.context.view_layer.objects.active = trail
        bpy.ops.object.select_all(action='DESELECT')
        robj2 = trail.copy()
        robj2.data = trail.data.copy()
        bpy.context.collection.objects.link(robj2)
        robj2.select_set(True)
        bpy.ops.object.convert(target='MESH')
        trail_mesh = robj2

    #robj.hide_set(True)

    if trail_mesh:
        if trail_mesh.type == "MESH" and len(trail_mesh.data.vertices) > 0:
            # Check if any vertex is inside the cube
            for v in trail_mesh.data.vertices:
                global_coord = trail_mesh.matrix_world @ v.co
                if is_point_inside_cube(global_coord, cube_bb):
                    # Apply Boolean modifier
                    #print(f"{trail_mesh.name} is inside the Boundaries")
                    if trail_mesh not in boolObjects:
                        boolObjects.append(trail_mesh)
                    #Set done to True so it doesnt delete the object later
                    done = True
                    #Change Collection
                    continue  # No need to keep checking this object
                else:
                    pass
                    #print(f"{trail_mesh.name} is NOT inside the Boundaries")
        else:
            print("No Vertices for Trail Found")
            bpy.data.objects.remove(trail_mesh, do_unlink=True)


    if done == False:
        bpy.data.objects.remove(cutobject, do_unlink=True)
        if trail_mesh and trail_mesh.name in bpy.data.objects:
            bpy.data.objects.remove(trail_mesh, do_unlink=True)
    #Pfade kopieren, zusammenfügen und die boolean operation mit allen trails kombiniert ausführen
    if done == True:
        copied_objects = []
        #Copy objects
        for obj in boolObjects:
            obj_copy = obj.copy()
            obj_copy.data = obj.data.copy()
            bpy.context.collection.objects.link(obj_copy)
            copied_objects.append(obj_copy)

        #Deselect all
        bpy.ops.object.select_all(action='DESELECT')

        #Select all copied objects and make one active
        for obj in copied_objects:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = copied_objects[0]

        #Join them into a single object
        bpy.ops.object.join()

        merged_object = bpy.context.active_object

        bool_mod = cube.modifiers.new(name=f"Intersect", type='BOOLEAN')
        bool_mod.operation = 'INTERSECT'
        bool_mod.object = merged_object
        bpy.context.view_layer.objects.active = cube
        bpy.ops.object.modifier_apply(modifier=bool_mod.name)

        bpy.data.objects.remove(merged_object, do_unlink=True)
        if robj2 != None:
            bpy.data.objects.remove(robj2, do_unlink=True)

        mat = bpy.data.materials.get("TRAIL")
        cube.data.materials.clear()
        cube.data.materials.append(mat)

        from .metadata import writeMetadata  # deferred to avoid circular import at load time
        writeMetadata(cube,"TRAIL")


def single_color_mode_curve(crv, map, keepTolTrail = False, cutDepth = 2, projectionObj = None):

    if projectionObj == None:
        projectionObj = map

    tol = bpy.context.scene.tp3d.tolerance #Tolerance between Map and the Trail on each side (0.2 worked great so far)
    minThickness = bpy.context.scene.tp3d.minThickness
    pathThickness = bpy.context.scene.tp3d.pathThickness

    lowestZonCurve = 1000

    trailCutDepth = min(cutDepth, minThickness/2) # How deep the trail will be placed into the map
                                            # Either 2mm or for flatter maps half of the minThickness

    if crv.type == "CURVE":

        #Getting the lowest Point of the Curve
        curve = crv.data
        for spline in curve.splines:
            if hasattr(spline, "points") and len(spline.points) > 0:
                for p in spline.points:
                    co_local = Vector((p.co.x, p.co.y, p.co.z))
                    co_world = crv.matrix_world @ co_local
                    z = co_local.z
                    if lowestZonCurve is None or z < lowestZonCurve:
                        lowestZonCurve = z

        #print(f"lowestzoncurve: {lowestZonCurve}")


        crv_data = crv.data
        crv_data.dimensions = "2D"
        crv_data.dimensions = "3D"
        crv_data.extrude = 200


        # Ensure the text object is selected and active
        bpy.ops.object.select_all(action='DESELECT')
        crv.select_set(True)
        bpy.context.view_layer.objects.active = crv

        bpy.ops.object.mode_set(mode='EDIT')

        # select all points if you want to smooth everything
        bpy.ops.curve.select_all(action='SELECT')

        # run the smooth operator
        bpy.ops.curve.smooth()

        # back to Object Mode if you like
        bpy.ops.object.mode_set(mode='OBJECT')

        #Create a duplicate object of the curve that will be slightly thicker
        crv_thick = crv.copy()
        crv_thick.data = crv.data.copy()
        crv_thick.data.bevel_depth = pathThickness/2 + tol  # Set the thickness of the curve
        bpy.context.collection.objects.link(crv_thick)
    elif crv.type == "MESH":

        depsgraph = bpy.context.evaluated_depsgraph_get()
        eval_obj = crv.evaluated_get(depsgraph)
        mesh = eval_obj.to_mesh()
        lowestZonCurve = min((crv.matrix_world @ v.co).z for v in mesh.vertices)

        #crv.scale.z = 100

        crv_thick = crv.copy()
        crv_thick.data = crv.data.copy()
        #crv_thick.data.scale = 1.02
        bpy.context.collection.objects.link(crv_thick)

    bpy.ops.object.convert(target='MESH')

    recalculateNormals(crv)

    # Add boolean modifier
    bool_mod = crv.modifiers.new(name="Boolean", type='BOOLEAN')
    bool_mod.object = projectionObj
    bool_mod.operation = 'INTERSECT'
    bool_mod.solver = 'MANIFOLD'


    bpy.ops.object.modifier_apply(modifier=bool_mod.name)


    recalculateNormals(crv)

    #Adding another Intersect Modifier to make the path "Plane" with the Map
    # Add boolean modifier
    bool_mod = crv.modifiers.new(name="Boolean", type='BOOLEAN')
    bool_mod.object = projectionObj
    bool_mod.operation = 'INTERSECT'
    bool_mod.solver = 'MANIFOLD'

    recalculateNormals(crv)


    bpy.ops.object.modifier_apply(modifier=bool_mod.name)

    #doing the same for the duplicate
    bpy.ops.object.select_all(action='DESELECT')
    crv_thick.select_set(True)
    bpy.context.view_layer.objects.active = crv_thick
    bpy.ops.object.convert(target='MESH')


    # Boolean to Cut off the Extruded trail thats sticking out at the bottom and the top
    bool_mod = crv_thick.modifiers.new(name="Boolean", type='BOOLEAN')
    bool_mod.object = projectionObj
    bool_mod.operation = 'INTERSECT'
    bool_mod.solver = 'MANIFOLD'

    bpy.ops.object.modifier_apply(modifier=bool_mod.name)
    bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='BOUNDS')
    crv_thick.scale = (1.002,1.002,1)

    recalculateNormals(crv_thick)

    # thicker curve is currently flat with the bottom of the Map so we need to move it upwards before creating the cutout
    # Move the thicker curve upwards
    crv_thick.location.z += lowestZonCurve - trailCutDepth
    #crv_thick.location.z += 1


    bpy.ops.object.select_all(action='DESELECT')
    map.select_set(True)
    bpy.context.view_layer.objects.active = map

    # Boolean to create the Cutout
    bool_mod = map.modifiers.new(name="Boolean", type="BOOLEAN")
    bool_mod.object = crv_thick
    bool_mod.operation = "DIFFERENCE"
    bool_mod.solver = "MANIFOLD"
    bpy.ops.object.modifier_apply(modifier = bool_mod.name)

    recalculateNormals(map)

    bpy.ops.object.select_all(action='DESELECT')
    crv.select_set(True)
    bpy.context.view_layer.objects.active = crv

    recalculateNormals(crv)
    #crv.scale = (0.998,0.998,1)
    crv.location.z += 0.1
    map.scale = (1.002,1.002,1)
    bool_mod = crv.modifiers.new(name = "Boolean", type = "BOOLEAN")
    bool_mod.object = map
    bool_mod.operation = "DIFFERENCE"
    bool_mod.solver = "MANIFOLD"
    bpy.ops.object.modifier_apply(modifier = bool_mod.name)

    map.scale = (1.00,1.00,1)

    crv_thick.location.z -= 0.1
    #crv.location.z += lowestZonCurve - trailCutDepth
    recalculateNormals(crv)

    #Remove the last material from the MAP as the boolean operation adds it to the list witout using it
    #Without removing it will color the next boolean operation in the color of the trail
    mats = map.data.materials



    if keepTolTrail == False:
        bpy.data.objects.remove(crv_thick, do_unlink = True)
    else:
        return(crv_thick)


def single_color_mode_mesh_wireframe(original, map, tolerance = None):



    #Original = Element usually
    if tolerance == None:
        tolerance = bpy.context.scene.tp3d.toleranceElements

    voxelSize = 0.1


    #recalculateNormals(original)

    obj = original.copy()             # copy the object
    obj.data = obj.data.copy()   # copy the mesh (optional: if you want unique mesh)
    bpy.context.collection.objects.link(obj)  # link to current collection
    obj.name = "Duplicate"


    # Delete all faces except downward-facing ones
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')



    bm = bmesh.from_edit_mesh(obj.data)
    for f in bm.faces:
        f.select = f.normal.normalized().z >= -0.95  # select non-downward faces
    bmesh.update_edit_mesh(obj.data)
    bpy.ops.mesh.delete(type='FACE')
    bpy.ops.object.mode_set(mode='OBJECT')



    # Record the z level of the bottom plane before wireframe
    bottom_z = min(v.co.z for v in obj.data.vertices)

    # Apply Wireframe modifier with -tolerance as thickness
    wire = obj.modifiers.new(name="Wireframe", type='WIREFRAME')
    wire.thickness = -tolerance
    wire.offset = 0
    wire.use_replace = True
    wire.use_even_offset = True
    applyModifier(obj, wire)


    # Remove top and bottom vertices, keep only those coplanar with bottom_z
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    for v in bm.verts:
        v.select = abs(v.co.z - bottom_z) > 0.001
    bmesh.update_edit_mesh(obj.data)
    bpy.ops.mesh.delete(type='VERT')

    #raise Exception("debug stop")



    # Fill the edge loop to create a face (equivalent of pressing F)
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.edge_face_add()

    # Extrude upward by 50
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.extrude_region_move(
        TRANSFORM_OT_translate={"value": (0, 0, 50)}
    )
    bpy.ops.object.mode_set(mode='OBJECT')


    # Clear materials from the duplicate before subtracting
    obj.data.materials.clear()

    # Separate obj into loose parts, subtract each individually from map
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.separate(type='LOOSE')
    bpy.ops.object.mode_set(mode='OBJECT')

    loose_parts = list(bpy.context.selected_objects)

    for part in loose_parts:
        boolean = map.modifiers.new(name="Boolean", type='BOOLEAN')
        boolean.operation = 'DIFFERENCE'
        boolean.object = part
        boolean.solver = 'MANIFOLD'
        applyModifier(map, boolean)
        bpy.data.objects.remove(part, do_unlink=True)

    # Remove empty material slots from map
    for i in reversed(range(len(map.material_slots))):
        if map.material_slots[i].material is None:
            map.active_material_index = i
            bpy.context.view_layer.objects.active = map
            bpy.ops.object.material_slot_remove()

    return None


def remeshClearing(obj, voxelSize2, tolerance):


    # Record the z level of the bottom faces
    bottom_z = min(v.co.z for v in obj.data.vertices)

    print(f"Bottom_z: {bottom_z}")

    # Extrude bottom faces upward by 1, then shift all vertices down by 0.5
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.extrude_region_move(
        TRANSFORM_OT_translate={"value": (0, 0, 4)}
    )

    bpy.ops.object.mode_set(mode='OBJECT')

    if tolerance > 0:
        # Solidify to create the tolerance thickness
        solid = obj.modifiers.new(name="Solidify", type='SOLIDIFY')
        solid.offset = 1.0
        solid.thickness = -tolerance / 2
        applyModifier(obj, solid)

    remesh = obj.modifiers.new(name="Remesh", type='REMESH')
    remesh.mode = 'VOXEL'
    remesh.voxel_size = voxelSize2
    remesh.use_smooth_shade = True



    applyModifier(obj, remesh)


    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.transform.translate(value=(0, 0, -2.025), snap=False) # -0.51, die 0.01 damit es außermittig liegt und später nicht mehr als eine outer edge übrig bleibt
    bpy.ops.object.mode_set(mode='OBJECT')


    #----------------------
    bpy.ops.object.mode_set(mode='OBJECT')

    # Build a cube that's slightly larger than obj in XY, bottom face at z=0, 50 units tall.
    # Subtracting it from obj keeps only whatever is above z=50 and cuts a clean plane there.
    mw  = obj.matrix_world
    xs  = [(mw @ v.co).x for v in obj.data.vertices]
    ys  = [(mw @ v.co).y for v in obj.data.vertices]
    pad = 0.5
    cx  = (min(xs) + max(xs)) / 2
    cy  = (min(ys) + max(ys)) / 2
    sx  = (max(xs) - min(xs)) + pad * 2
    sy  = (max(ys) - min(ys)) + pad * 2

    print(f"Cube center: ({cx}, {cy}), size: ({sx}, {sy})")

    bm_c = bmesh.new()
    bmesh.ops.create_cube(bm_c, size=1.0)
    _cube_mesh = bpy.data.meshes.new("_BoolCube")
    bm_c.to_mesh(_cube_mesh)
    bm_c.free()

    cube_obj = bpy.data.objects.new("_BoolCube", _cube_mesh)
    bpy.context.collection.objects.link(cube_obj)
    cube_obj.scale    = (sx, sy, 50.0)
    cube_obj.location = (cx, cy, 25.0+bottom_z)   # bottom face lands at z=0, top at z=50

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bool_mod = obj.modifiers.new(name="_BoolCube", type='BOOLEAN')
    bool_mod.operation = 'DIFFERENCE'
    bool_mod.object    = cube_obj
    bool_mod.solver    = 'MANIFOLD'

    applyModifier(obj, bool_mod)
    bpy.data.objects.remove(cube_obj, do_unlink=True)

    # Keep only the topmost vertices (the flat cap left by the cube's top face)
    top_z = max(v.co.z for v in obj.data.vertices)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='VERT')
    bm2 = bmesh.from_edit_mesh(obj.data)
    bm2.verts.ensure_lookup_table()
    for v in bm2.verts:
        v.select = abs(v.co.z - top_z) > 0.001
    bmesh.update_edit_mesh(obj.data)
    bpy.ops.mesh.delete(type='VERT')
    #bpy.ops.object.mode_set(mode='OBJECT')
    #----------------
    #bpy.ops.mesh.delete(type='VERT')


    # Flatten remaining verts to exactly z bottom_z
    bm = bmesh.from_edit_mesh(obj.data)
    for v in bm.verts:
        v.co.z = bottom_z
    bmesh.update_edit_mesh(obj.data)



    # Extrude upward by 30
    bpy.ops.mesh.select_mode(type='FACE')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.extrude_region_move(
        TRANSFORM_OT_translate={"value": (0, 0, 30)}
    )
    bpy.ops.object.mode_set(mode='OBJECT')



    recalculateNormals(obj)


def single_color_mode_mesh_remesh(original, map, tolerance = None):

    #Original = Element usually
    if tolerance == None:
        tolerance = bpy.context.scene.tp3d.toleranceElements

    voxelSize = 0.1
    voxelSize2 = 0.2

    #recalculateNormals(original)

    obj = original.copy()
    obj.data = obj.data.copy()
    bpy.context.collection.objects.link(obj)

    # Select bottom faces — leaves obj in Edit Mode with bottom faces selected
    selectBottomFaces(obj)

    # Invert and delete non-bottom faces, leaving only the bottom faces
    bpy.ops.mesh.select_mode(type='FACE')
    bpy.ops.mesh.select_all(action='INVERT')
    bpy.ops.mesh.delete(type='FACE')

    bpy.ops.object.mode_set(mode='OBJECT')

    remeshClearing(obj, voxelSize2, tolerance)



    # Boolean subtract from map
    boolean = map.modifiers.new(name="Boolean", type='BOOLEAN')
    boolean.operation = 'DIFFERENCE'
    boolean.object = obj
    boolean.solver = 'MANIFOLD'
    applyModifier(map, boolean)

    # Remesh original for cleaner print geometry
    remesh = original.modifiers.new(name="Remesh", type='REMESH')
    remesh.mode = 'VOXEL'
    remesh.voxel_size = voxelSize
    remesh.use_smooth_shade = True
    applyModifier(original, remesh)

    return obj


def merge_with_map(mapobject, mergeobject, flatBottom = False, singleColorMode = False,):

    if mergeobject == None:
        print("func merge_with_map: No Object to merge with Map")
        return None

    if mergeobject.type == "CURVE":
        print("MERGE CURVE WITH MAP")


        duplicate  = mapobject.copy()
        duplicate.data = mapobject.data.copy()
        bpy.context.collection.objects.link(duplicate)
        #intersect_alltrails_with_existing_box(duplicate)
        intersect_trail_with_existing_box(duplicate,mergeobject)
        return duplicate


    bpy.ops.object.select_all(action="DESELECT")

    #if the mergeobject is a Text object -> Convert it into a mesh
    if mergeobject.type == "FONT":
        mergeobject.select_set(True)
        bpy.context.view_layer.objects.active = mergeobject
        bpy.ops.object.convert(target='MESH')

    if mergeobject.type == "CURVE":
        mergeobject.select_set(True)
        bpy.context.view_layer.objects.active = mergeobject
        bpy.ops.object.convert(target='MESH')

    bpy.context.view_layer.objects.active = mergeobject
    mergeobject.select_set(True)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.extrude_region_move()
    bpy.ops.transform.translate(value=(0, 0, 200))#bpy.ops.mesh.select_all(action='DESELECT')
    bpy.ops.object.mode_set(mode='OBJECT')
    mergeobject.location.z = -1

    recalculateNormals(mergeobject)



    # Add boolean modifier
    bool_mod = mergeobject.modifiers.new(name="Boolean", type='BOOLEAN')
    bool_mod.object = mapobject
    bool_mod.operation = 'INTERSECT'
    bool_mod.solver = 'MANIFOLD'

    #apply boolean modifier
    bpy.ops.object.modifier_apply(modifier=bool_mod.name)

    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(mergeobject.data)

    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()



    try:
        min_z = min(v.co.z for v in bm.verts)
    except:
        bm.free()
        bpy.ops.object.mode_set(mode='OBJECT')
        return

    tol = 0.1

    lowestVert = 100



    for v in bm.verts:
        if abs(v.co.z - min_z) < tol:
            v.select = True
        else:
            v.select = False
            if v.co.z < lowestVert:
                lowestVert = v.co.z


    if flatBottom == False: #Extrudes terrain shape down 1mm
        bpy.context.tool_settings.mesh_select_mode = (True, False, False)
        #bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.select], context="FACES")
        #bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context='VERTS')
        bmesh.ops.delete(bm, geom=[elem for elem in bm.verts[:] + bm.edges[:] + bm.faces[:] if elem.select], context='VERTS')

        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.extrude_region_move()
        bpy.ops.transform.translate(value=(0, 0, -1))#bpy.ops.mesh.select_all(action='DESELECT')
    elif flatBottom == True: #Extrudes and sets new faces flat to set value
        #bpy.ops.transform.translate(value=(0, 0, 1))#bpy.ops.mesh.select_all(action='DESELECT')

        lowestprojection = 100
        secondlowestprojection = 200

        bm = bmesh.from_edit_mesh(mergeobject.data)

        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        for v in bm.verts:
                if v.co.z < lowestprojection:
                    lowestprojection = v.co.z
                if v.co.z < secondlowestprojection and v.co.z > lowestprojection:
                    secondlowestprojection = v.co.z




        bpy.ops.transform.translate(value=(0, 0, secondlowestprojection - lowestprojection - 1), orient_type='LOCAL')

        #bpy.ops.mesh.select_all(action='DESELECT')
        pass




    bmesh.update_edit_mesh(mergeobject.data)
    bpy.ops.object.mode_set(mode="OBJECT")



    mergeobject.location.z += 0.05

    return mergeobject


def projection(operation, Mapobject, obj):
    from .terrain import color_map_faces_by_terrain  # deferred to avoid circular import at load time
    from .scene import remove_objects  # deferred to avoid circular import at load time

    for label, o in (("Mapobject", Mapobject), ("obj", obj)):
        if o is None:
            raise ValueError(f"projection: '{label}' is None")
        try:
            name = o.name  # raises ReferenceError if the Blender object was removed
        except ReferenceError:
            raise ValueError(f"projection: '{label}' refers to a removed Blender object")
        if name not in bpy.data.objects:
            raise ValueError(f"projection: '{label}' ('{name}') is not in the current scene")
        if o.type != 'MESH' or o.data is None:
            raise ValueError(f"projection: '{label}' ('{name}') is not a valid mesh object (type={o.type!r})")

    if operation == "paint":
        merge_with_map(Mapobject, obj)

        obj.location.z += 1

        bpy.ops.object.origin_set(type='ORIGIN_CURSOR', center='MEDIAN')
        color_map_faces_by_terrain(Mapobject, obj)
        mesh_data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh_data)

    if operation == "separate":
        merge_with_map(Mapobject, obj, False)

        obj.data.materials.clear()

        obj.location.z += 0.2

    if operation == "singleColorMode":

        merge_with_map(Mapobject, obj, True)

        obj.data.materials.clear()

        single_color_mode_mesh_wireframe(obj, Mapobject)

    if operation == "singleColorMode_remesh":

        merge_with_map(Mapobject, obj, True)

        obj.data.materials.clear()

        single_color_mode_mesh_remesh(obj, Mapobject)

    if operation == "negative":
        merge_with_map(Mapobject, obj, True)
        boolean_operation(Mapobject, obj, "DIFFERENCE")
        remove_objects(obj)
