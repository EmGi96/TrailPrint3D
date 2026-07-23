import bpy  # type: ignore
import bmesh  # type: ignore
import math
from mathutils import Vector, Matrix, bvhtree  # type: ignore


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
    # filter only mesh objects
    #mesh_objs = [o for o in objects if o.type == 'MESH']
    mesh_objs = objects
    if not mesh_objs:
        return None
    if len(mesh_objs) == 1:
        bpy.ops.object.select_all(action='DESELECT')
        mesh_objs[0].select_set(True)
        bpy.context.view_layer.objects.active = mesh_objs[0]
        return mesh_objs[0]


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


def boolean_operation(obj_a, obj_b, operation='DIFFERENCE', solver='MANIFOLD'):
    """
    Performs a Boolean operation on obj_a with obj_b.

    operation: 'UNION', 'INTERSECT', or 'DIFFERENCE'
    solver:    'MANIFOLD' (both inputs must be watertight-manifold) or 'EXACT'
               (tolerates non-manifold input -- use for OSM element meshes).
    """
    # Ensure both objects exist
    if obj_a is None or obj_b is None:
        print("Error: One of the objects is None")
        return None

    # Add Boolean modifier to obj_a
    mod = obj_a.modifiers.new(name="BooleanManifold", type='BOOLEAN')
    mod.object = obj_b
    mod.operation = operation
    mod.solver = solver

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

        # First pass: raycast every point, recording the local-space hit (or
        # None on a miss/bad hit) without touching the curve yet. A miss
        # usually means this particular point fell just past the terrain's
        # edge -- falling back to the point's own pre-raycast position (the
        # old behaviour) left a sharp drop to whatever raw elevation it had
        # there instead of continuing the terrain's actual surface height.
        #
        # A "successful" hit can still be wrong: near a jigsaw piece's
        # boundary the straight-down ray can graze the piece's near-vertical
        # side wall instead of its flat top, which ray_cast reports as a
        # normal success just at whatever height it happened to clip that
        # wall -- producing an isolated steep spike, not a clean miss. Only
        # accept a hit whose surface normal points mostly upward (a true top
        # face); anything else is treated as a miss too.
        #
        # Threshold is 0.1 (not 0.5): jigsaw walls are truly vertical
        # (normal.z ≈ 0) so 0.1 still catches them, but 0.5 incorrectly
        # rejected steep terrain at high elevation scales — at elev scale 5
        # a real-world 20° slope appears as ~64°, normal.z ≈ 0.44 < 0.5,
        # causing valid hits to be discarded and replaced with the last-valid
        # Z, which produced flat plateaus followed by sudden vertical steps.
        hits = []
        originals = []
        for point in points:
            if spline.type == 'BEZIER':
                co_world = curve_world @ point.co
            else:
                co_world = curve_world @ point.co.xyz
            originals.append(point.co.xyz if spline.type != 'BEZIER' else point.co.copy())

            co_local = mesh_world_inv @ co_world
            success, hit_loc, normal, face_index = eval_mesh_obj.ray_cast(co_local, direction_local)
            if success:
                world_normal = (mesh_world.to_3x3() @ normal).normalized()
                if world_normal.z < 0.1:
                    success = False
            hits.append(curve_world_inv @ (mesh_world @ hit_loc) if success else None)

        # Fill gaps from the nearest point along the spline that DID hit --
        # carried forward first, then backward (covers a run of misses at
        # the very start of the spline, before any hit has happened yet).
        # Only the HEIGHT is borrowed from that neighbour, not its full
        # position -- copying the whole hit vector collapsed every point in
        # a miss run onto that one neighbour's x/y.
        filled_z = [h.z if h is not None else None for h in hits]
        last_z = None
        for i, z in enumerate(filled_z):
            if z is not None:
                last_z = z
            elif last_z is not None:
                filled_z[i] = last_z
        next_z = None
        for i in range(len(filled_z) - 1, -1, -1):
            if filled_z[i] is not None:
                next_z = filled_z[i]
            elif next_z is not None:
                filled_z[i] = next_z

        # Second pass: apply. If literally nothing on this spline ever hit
        # (no terrain below it at all), restore each point to where it
        # started rather than leaving it stranded 1000 units up in the air.
        for point, local_hit, orig, z in zip(points, hits, originals, filled_z):
            if local_hit is not None:
                if spline.type == 'BEZIER':
                    point.co = local_hit
                    point.handle_left_type = point.handle_right_type = 'AUTO'
                else:
                    point.co = (local_hit.x, local_hit.y, local_hit.z, 1.0)
                continue

            if z is None:
                point.co = orig - offset if spline.type == 'BEZIER' else (orig.x, orig.y, orig.z - offset.z, 1.0)
            elif spline.type == 'BEZIER':
                point.co = Vector((orig.x, orig.y, z))
                point.handle_left_type = point.handle_right_type = 'AUTO'
            else:
                point.co = (orig.x, orig.y, z, 1.0)

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

        # First pass: raycast every point, recording the local-space hit (or
        # None on a miss) without touching the curve yet. A miss usually
        # means this particular point fell just past the terrain's edge --
        # falling back to the point's own pre-raycast position (the old
        # behaviour, or here simply leaving it untouched) left a sharp step
        # to whatever raw elevation it had there (or stranded it offset_z
        # units up in the air) instead of continuing the terrain's actual
        # surface height.
        hits = []
        originals = []
        ws = []
        for point in points:
            if spline.type == 'BEZIER':
                co_world = curve_world @ point.co
                originals.append(point.co.copy())
            else:
                co_world = curve_world @ point.co.xyz
                originals.append(point.co.xyz)
                ws.append(getattr(point.co, "w", 1.0))

            hit_result = scene.ray_cast(depsgraph, co_world, direction_world)
            # scene.ray_cast checks the WHOLE scene, not just the terrain --
            # it was landing on OTHER trail objects (leftover/duplicate
            # curves converted to mesh elsewhere) sitting in the scene, which
            # often have some upward-facing surface too and so passed the
            # normal check below while still being completely the wrong
            # object. Only accept a hit on something actually tagged as a
            # MAP/terrain object; everything else (trails, or anything else
            # in the scene) is rejected regardless of its normal.
            hit_obj = hit_result[4] if hit_result[0] else None
            is_map_hit = hit_obj is not None and (hit_obj.get("objType") == "MAP" or hit_obj.get("Object type") == "MAP")
            # Same reasoning as RaycastCurveToMesh on top of that: only
            # accept a hit whose normal points mostly upward (a true top
            # face) -- near a jigsaw piece's boundary the ray can otherwise
            # graze a near-vertical side wall and report a "successful" hit
            # at the wrong height.
            hit_ok = is_map_hit and hit_result[2].z >= 0.5
            if not hit_ok:
                if not hit_result[0]:
                    reason = "no hit"
                elif not is_map_hit:
                    reason = f"hit non-map object"
                else:
                    reason = f"normal.z={hit_result[2].z:.2f} (not upward)"
                hit_obj_name = hit_obj.name if hit_obj else None
            hits.append(curve_world_inv @ hit_result[1] if hit_ok else None)

        # Fill gaps from the nearest point along the spline that DID hit --
        # carried forward first, then backward (covers a run of misses at
        # the very start of the spline, before any hit has happened yet).
        # Only the HEIGHT is borrowed from that neighbour, not its full
        # position -- copying the whole hit vector collapsed every point in
        # a miss run onto that one neighbour's x/y (e.g. when a long trail
        # only clips the edge of a much smaller map tile, almost every point
        # misses and they all landed on the same single spot).
        filled_z = [h.z if h is not None else None for h in hits]
        last_z = None
        for i, z in enumerate(filled_z):
            if z is not None:
                last_z = z
            elif last_z is not None:
                filled_z[i] = last_z
        next_z = None
        for i in range(len(filled_z) - 1, -1, -1):
            if filled_z[i] is not None:
                next_z = filled_z[i]
            elif next_z is not None:
                filled_z[i] = next_z

        # Second pass: apply. If literally nothing on this spline ever hit
        # (no terrain below it at all), restore each point to where it
        # started rather than leaving it stranded offset_z units up.
        for i, point in enumerate(points):
            local_hit = hits[i]
            if local_hit is not None:
                if spline.type == 'BEZIER':
                    point.co = local_hit
                    # keep handles auto to get a reasonable shape; alternatively compute
                    point.handle_left_type = point.handle_right_type = 'AUTO'
                else:
                    point.co = (local_hit.x, local_hit.y, local_hit.z, ws[i])
                continue

            z = filled_z[i]
            if z is None:
                point.co = originals[i] - offset if spline.type == 'BEZIER' else \
                    (originals[i].x, originals[i].y, originals[i].z - offset.z, ws[i])
            elif spline.type == 'BEZIER':
                orig = originals[i]
                point.co = Vector((orig.x, orig.y, z))
                point.handle_left_type = point.handle_right_type = 'AUTO'
            else:
                orig = originals[i]
                point.co = (orig.x, orig.y, z, ws[i])

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
        # EXACT (not MANIFOLD): buildings/roads footprints can be non-manifold
        # (self-touching OSM outlines). The MANIFOLD solver refuses non-manifold
        # input and silently no-ops, which left elements spanning the whole bbox.
        # EXACT tolerates non-manifold input so the clip to the map shape works.
        bool_mod.solver = 'EXACT'

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

    # Replace cutobject's own geometry with a CLEAN tall prism built from
    # just its flat 2D footprint, before anything else. The old approach
    # instead scaled cutobject's REAL mesh 1000x in Z to build a "cookie
    # cutter" tall enough to safely envelop the trail tube regardless of
    # terrain height -- harmless for a flat-ish tile, but a puzzle piece has
    # its own terrain-following top AND a small bottom-rim bevel; scaling
    # those tiny Z variations 1000x explodes them into wildly distorted
    # slopes right at the piece's edge, which then leak into the final
    # boolean below as spike artifacts unrelated to the trail itself. The
    # final clipped result still ends up baked into cutobject either way
    # (every caller relies on that), just built from clean geometry now.
    from . import geometry2d as g2d  # deferred to avoid circular import at load time
    footprint = g2d.footprint_with_holes(cutobject, down_only=True)
    if footprint is not None and not footprint.is_empty:
        mc = [cutobject.matrix_world @ Vector(c) for c in cutobject.bound_box]
        bottom_z = min(v.z for v in mc) - 1000.0
        top_z = max(v.z for v in mc) + 1000.0
        verts, faces = [], []
        for part in g2d.iter_polygons(footprint):
            _extrude_flat_polygon(g2d, part, bottom_z, top_z, verts, faces)
        if verts:
            new_mesh = bpy.data.meshes.new(cutobject.data.name)
            new_mesh.from_pydata(verts, [], faces)
            new_mesh.update()
            _clean_solid_mesh(new_mesh)
            old_mesh = cutobject.data
            # verts above are WORLD-space (footprint_with_holes/bound_box
            # both already account for matrix_world) -- reset the object's
            # own transform so it doesn't get re-applied on top.
            cutobject.matrix_world = Matrix.Identity(4)
            cutobject.data = new_mesh
            bpy.data.meshes.remove(old_mesh)

    print(f"Trail name {trail.name}")

    robj2 = None

    bpy.ops.object.select_all(action='DESELECT')

    cutobject.select_set(True)
    bpy.context.view_layer.objects.active = cutobject

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
            trail_mesh = None


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

        if len(cube.data.polygons) == 0:
            # is_point_inside_cube above is only a coarse bbox/point pre-check —
            # a trail vertex can fall just inside the tile's AABB while the
            # actual boolean INTERSECT still yields no geometry (e.g. the trail
            # only grazes a corner). Without this, an empty "_TRAIL_n" object
            # with no mesh data is left behind in the scene.
            bpy.data.objects.remove(cube, do_unlink=True)
            return

        # Keep whatever material the original trail had (e.g. trails
        # generated with alternating TRAIL/YELLOW materials) instead of
        # forcing TRAIL on every merge.
        mat = trail.data.materials[0] if trail.data.materials else bpy.data.materials.get("TRAIL")
        cube.data.materials.clear()
        cube.data.materials.append(mat)

        from .metadata import writeMetadata  # deferred to avoid circular import at load time
        writeMetadata(cube,"TRAIL")


def _clean_solid_mesh(mesh, dist=1e-6):
    """Weld near-duplicate verts and dissolve degenerate/zero-area geometry.

    Earcut can leave a few near-coincident vertices that make a solid
    technically non-manifold. Blender's MANIFOLD boolean solver doesn't error
    on that, it silently no-ops (documented failure mode in
    docs/roads-shapely-approach.md and reproduced in terrain.py's
    manifold-check fallback), which looks like "the boolean just didn't
    happen". This is cheap insurance against that.
    """
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=dist)
    bmesh.ops.dissolve_degenerate(bm, dist=dist, edges=bm.edges[:])
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()


def _extrude_flat_polygon(g2d_mod, polygon, bottom_z, top_z, verts, faces):
    """Append a flat-bottomed, flat-topped solid prism for one Shapely polygon.

    Same manifold construction as buildings (`osm._append_building`): earcut
    caps with shared, index-referenced vertices plus consistently-wound wall
    quads, so normals come out correct with no `recalculateNormals` call.

    This is a fast (pure 2D) replacement for the old curve bevel+extrude
    tessellation -- the actual terrain-following shape is produced afterward
    by a real 3D boolean against the map, not by this flat prism itself.
    """
    # The floor/roof/wall winding below assumes a CCW exterior ring and CW
    # holes (the standard convention earcut/this function rely on). Shapely
    # buffer()/intersection() output doesn't guarantee that orientation --
    # if it comes back reversed, every triangle and wall quad winds backwards
    # and every normal ends up flipped, which silently breaks the boolean
    # against the map. Normalize it explicitly rather than assume.
    g2d_mod._require_shapely()
    polygon = g2d_mod.orient(polygon, sign=1.0)

    ext = list(polygon.exterior.coords)
    if len(ext) > 1 and ext[0] == ext[-1]:
        ext = ext[:-1]
    if len(ext) < 3:
        return
    holes = []
    for interior in polygon.interiors:
        ring = list(interior.coords)
        if len(ring) > 1 and ring[0] == ring[-1]:
            ring = ring[:-1]
        if len(ring) >= 3:
            holes.append(ring)
    ec = g2d_mod._earcut_triangulate(ext, holes)
    if ec is None:
        return
    verts2d, cap_tris = ec
    n2 = len(verts2d)
    base = len(verts)
    for (vx, vy) in verts2d:
        verts.append((vx, vy, bottom_z))
    for (vx, vy) in verts2d:
        verts.append((vx, vy, top_z))
    for (ia, ib, ic) in cap_tris:
        faces.append((base + ic, base + ib, base + ia))                 # floor (down)
        faces.append((base + n2 + ia, base + n2 + ib, base + n2 + ic))   # roof (up)
    start = 0
    for ring in [ext] + holes:
        rn = len(ring)
        for i in range(rn):
            a = base + start + i
            b = base + start + (i + 1) % rn
            c = base + n2 + start + (i + 1) % rn
            d = base + n2 + start + i
            faces.append((a, b, c, d))
        start += rn


def _ensure_outward_normals(obj):
    """Make sure obj's normals point outward, robustly.

    recalculateNormals()'s normals_make_consistent() only guarantees every
    face is consistent with its neighbors -- for a thin/concave solid like a
    jigsaw piece it can end up fully consistent but globally INVERTED (a
    known limitation of that heuristic), which is exactly what happened to
    one piece. A closed manifold's signed volume (divergence theorem: sum
    each face's contribution to the volume integral) is a deterministic,
    purely geometric way to tell which way is actually outward -- positive
    means correctly outward, negative means the whole mesh is inside-out --
    independent of whatever normals_make_consistent() decided.
    """
    recalculateNormals(obj)

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    volume = 0.0
    for f in bm.faces:
        if len(f.verts) < 3:
            continue
        v0 = f.verts[0].co
        for i in range(1, len(f.verts) - 1):
            volume += v0.dot(f.verts[i].co.cross(f.verts[i + 1].co))
    volume /= 6.0
    if volume < 0:
        bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
        bm.to_mesh(obj.data)
        obj.data.update()
    bm.free()


def _bevel_bottom_edges(obj, bevel_width):
    """Chamfer the rim where the bottom face meets the side walls.

    Only the BOTTOM rim -- not the top edge or the vertical tab/blank side
    walls -- so pieces still print flat and the interlocking shape on top
    stays exact; the bevel just eases the bottom corner (helps pieces seat
    into each other without snagging, and softens the first-layer edge).

    Uses the actual mesh.bevel operator on a real face/edge selection
    (select the bottom faces -> region_to_loop for their boundary -> bevel
    that edge loop) rather than driving bmesh.ops.bevel directly, since
    that's simpler to verify.

    The bottom faces are identified purely by Z POSITION (every vertex near
    the mesh's minimum Z), never by face-normal direction. normals_make_
    consistent() (still run below, since correct normals matter for export/
    printing) can produce a set of normals that's internally CONSISTENT but
    globally INVERTED for some piece shapes -- a known limitation on thin/
    concave solids. Selecting by normal.z direction would then grab the TOP
    face on an affected piece and bevel the wrong side, which is exactly
    what happened. Z position is a plain geometric fact, unaffected by which
    way the normals ended up pointing.
    """
    if bevel_width <= 0 or not obj.data.vertices:
        return

    _ensure_outward_normals(obj)

    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='FACE')
    bpy.ops.mesh.select_all(action='DESELECT')

    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    if not bm.verts:
        bpy.ops.object.mode_set(mode='OBJECT')
        return
    z_values = [v.co.z for v in bm.verts]
    min_z, max_z = min(z_values), max(z_values)
    z_tol = max(1e-3, (max_z - min_z) * 0.01)
    n_bottom = 0
    for f in bm.faces:
        if all(abs(v.co.z - min_z) < z_tol for v in f.verts):
            f.select = True
            n_bottom += 1
    bmesh.update_edit_mesh(obj.data)
    print(f"[TP3D puzzle bevel] {obj.name}: {n_bottom} bottom face(s) found (by Z position)")

    if n_bottom == 0:
        bpy.ops.object.mode_set(mode='OBJECT')
        return

    bpy.ops.mesh.region_to_loop()
    bpy.ops.mesh.select_mode(type='EDGE')

    bm = bmesh.from_edit_mesh(obj.data)
    n_edges = sum(1 for e in bm.edges if e.select)
    print(f"[TP3D puzzle bevel] {obj.name}: {n_edges} boundary edge(s) selected, beveling {bevel_width}mm")

    if n_edges > 0:
        bpy.ops.mesh.bevel(offset=bevel_width, offset_type='OFFSET', segments=1, affect='EDGES')

    bpy.ops.object.mode_set(mode='OBJECT')


def cut_into_puzzle_pieces(terrain_obj, pieces, tolerance_mm=0.3):
    """Cut a single finished map tile into separate jigsaw puzzle piece objects.

    `terrain_obj` -- a normal, already-generated (and trail-merged, if
    applicable) map tile object. Cut apart AFTER full generation rather than
    generating each piece independently, so every piece shares one continuous
    terrain/trail layout with no per-piece elevation-fetch seams.

    `pieces` -- list of dicts {'row', 'col', 'points': [[x, y], ...]} where
    (x, y) are normalized [0, 1] coordinates over terrain_obj's own footprint
    (0,0 / 1,1 = opposite corners of its world-space bounding box) -- matches
    how the picker page lays out its rows x columns jigsaw grid over the
    drawn rectangle. Each piece's boundary is expected to already include its
    interlocking tab/blank curves; this function does not generate jigsaw
    geometry itself.

    `tolerance_mm` -- real-world gap to leave between adjacent pieces.
    Applied as a uniform inward `buffer()` on every piece polygon, which
    gives every shared edge -- straight or curved tab/blank alike -- the same
    gap without any jigsaw-specific tolerance math.

    Reuses the exact same flat-prism-then-INTERSECT technique already proven
    for `single_color_mode_curve`: `_extrude_flat_polygon` for a clean
    manifold solid, `_clean_solid_mesh` to keep MANIFOLD booleans from
    silently no-opping on near-degenerate geometry, `boolean_operation` for
    the cut itself.

    Each piece's bottom rim is also chamfered (`_bevel_bottom_edges`) by
    min(0.5mm, minThickness / 2), so pieces seat into each other more easily
    and the bottom edge isn't perfectly sharp.

    Returns the list of newly created piece objects. `terrain_obj` itself is
    removed once every piece has been extracted.
    """
    from . import geometry2d as g2d  # deferred to avoid circular import at load time

    mc = [terrain_obj.matrix_world @ Vector(c) for c in terrain_obj.bound_box]
    x_min = min(v.x for v in mc); x_max = max(v.x for v in mc)
    y_min = min(v.y for v in mc); y_max = max(v.y for v in mc)
    z_min = min(v.z for v in mc); z_max = max(v.z for v in mc)
    bottom_z = z_min - 10.0
    top_z = z_max + 10.0

    materials = list(terrain_obj.data.materials)
    # terrain_obj already carries the full writeMetadata("MAP") dump (Horizontal
    # Scale, objSize, Shape, edge_south/north/west/east, etc. -- set by
    # createTerrainFromSelected before this function runs) plus whatever the
    # caller tagged it with. Brand-new piece objects don't inherit custom
    # properties through bpy.data.objects.new()/booleans, so copy everything
    # over verbatim rather than re-deriving a subset of fields by hand.
    terrain_metadata = dict(terrain_obj.items())
    bevel_width = min(0.5, bpy.context.scene.tp3d.minThickness / 2)
    piece_objs = []

    # DEBUG ONLY: flat (z=0) copies of each piece's actual cutter polygon
    # (the same outline -- post tolerance-buffer -- that gets extruded into
    # the 3D prism below), laid out in a row below the real puzzle so the
    # 2D jigsaw shapes themselves can be inspected independently of the
    # boolean-INTERSECT result. Shifted in -Y by the puzzle's own Y extent
    # plus a fixed gap; X stays untouched so each cutter lines up directly
    # under its real piece for easy comparison.
    debug_y_offset = -(y_max - y_min) - 20.0
    debug_coll = g2d.debug_collection("TP3D_Debug_PuzzleCutters") if bpy.app.debug else None

    for piece in pieces:
        world_xy = [
            (x_min + nx * (x_max - x_min), y_min + ny * (y_max - y_min))
            for nx, ny in piece['points']
        ]
        poly = g2d.xy_ring_to_polygon(world_xy)
        if poly is None or poly.is_empty:
            continue
        if tolerance_mm > 0:
            # join_style='mitre' (not the default 'round'): a round join adds
            # up to 8 small arc segments at every convex corner it shrinks,
            # and the jigsaw curve is already a polyline with many slightly-
            # angled segments -- every one of those bends would otherwise
            # sprout its own little cluster of extra vertices. Mitre just
            # extends the two adjacent edges to meet at a single sharp point.
            poly = g2d.validate(poly.buffer(-tolerance_mm / 2, join_style='mitre'))
        if poly is None or poly.is_empty:
            continue

        verts, faces = [], []
        for part in g2d.iter_polygons(poly):
            _extrude_flat_polygon(g2d, part, bottom_z, top_z, verts, faces)
        if not verts:
            continue

        row, col = piece.get('row', 0), piece.get('col', 0)

        if debug_coll is not None:
            for i, part in enumerate(g2d.iter_polygons(poly)):
                dbg_obj = g2d.polygon_to_mesh(f"{terrain_obj.name}_piece_{row}_{col}_cutter_{i}", part)
                if dbg_obj is None:
                    continue
                for coll in list(dbg_obj.users_collection):
                    coll.objects.unlink(dbg_obj)
                debug_coll.objects.link(dbg_obj)
                dbg_obj.location.y = debug_y_offset

        mesh = bpy.data.meshes.new(f"{terrain_obj.name}_piece_{row}_{col}")
        mesh.from_pydata(verts, [], faces)
        mesh.update()
        _clean_solid_mesh(mesh)
        for m in materials:
            mesh.materials.append(m)

        piece_obj = bpy.data.objects.new(mesh.name, mesh)
        bpy.context.collection.objects.link(piece_obj)

        boolean_operation(piece_obj, terrain_obj, 'INTERSECT')
        if len(piece_obj.data.vertices) == 0:
            bpy.data.objects.remove(piece_obj, do_unlink=True)
            continue

        _bevel_bottom_edges(piece_obj, bevel_width)

        for k, v in terrain_metadata.items():
            piece_obj[k] = v
        # Override/add after the bulk copy so these always win and are never
        # shadowed by a same-named key from terrain_obj.
        piece_obj["objType"] = "MAP"
        piece_obj["Object type"] = "MAP"
        piece_obj["PuzzleRow"] = row
        piece_obj["PuzzleCol"] = col
        piece_objs.append(piece_obj)

    bpy.data.objects.remove(terrain_obj, do_unlink=True)
    return piece_objs


def _rounded_rect_polygon(width, height, radius, quad_segs=8):
    """A Shapely Polygon for a width x height rectangle, centered at the
    origin, with its outer corners rounded to *radius* (clamped so the
    rounding never exceeds half the rectangle's own width/height).
    """
    from . import geometry2d as _g2d  # deferred to avoid circular import at load time
    _g2d._require_shapely()
    radius = max(0.0, min(radius, width / 2, height / 2))
    if radius <= 1e-6:
        return _g2d.box(-width / 2, -height / 2, width / 2, height / 2)
    inner = _g2d.box(-width / 2 + radius, -height / 2 + radius, width / 2 - radius, height / 2 - radius)
    return inner.buffer(radius, quad_segs=quad_segs, join_style='round')


def _resolve_holder_font(font_filename):
    """Map an HTML <select> font filename (e.g. "ariblk.ttf") to a real path.

    Only Windows is resolved explicitly here -- matches the level of cross-
    platform support `create_text`'s own existing fallback already has (one
    hardcoded Mac path, nothing for Linux). Returns None (meaning "use
    create_text's own default resolution") if there's no usable match.
    """
    if not font_filename:
        return None
    import sys, os
    if sys.platform != 'win32':
        return None
    candidate = f"C:/WINDOWS/FONTS/{font_filename}"
    return candidate if os.path.isfile(candidate) else None


def _emboss_holder_text(holder_obj, text, outer_w, outer_h, wall_width, top_z,
                         font="", text_size_mm=None):
    """Emboss *text* centered on the front (south, -Y) rim of holder_obj and
    join it in as one printable part, in the WHITE material.

    The text's natural size is measured at scale 1 (instead of assuming a
    fixed font size) so it's scaled to *text_size_mm* (or, if not given, to
    fit the rim strip's available height) -- and always clamped by available
    width too, for long strings -- regardless of font metrics or string
    length.
    """
    from . import text_objects as txt  # deferred: text_objects imports from this module
    from .primitives import setupColors  # deferred to avoid circular import at load time

    text = (text or "").strip()
    if not text:
        return holder_obj

    setupColors()

    name = "PuzzleHolderText"
    old = bpy.data.objects.get(name)
    if old:
        bpy.data.objects.remove(old, do_unlink=True)

    text_obj = txt.create_text(name, text, (0, 0, 0), 1.0, font_path=_resolve_holder_font(font))

    depsgraph = bpy.context.evaluated_depsgraph_get()
    obj_eval = text_obj.evaluated_get(depsgraph)
    bbox = [Vector(c) for c in obj_eval.bound_box]
    natural_w = max(c.x for c in bbox) - min(c.x for c in bbox)
    natural_h = max(c.y for c in bbox) - min(c.y for c in bbox)
    if natural_w <= 1e-6 or natural_h <= 1e-6:
        bpy.data.objects.remove(text_obj, do_unlink=True)
        return holder_obj

    target_h = text_size_mm if text_size_mm and text_size_mm > 0 else max(0.5, wall_width - 1.5)
    available_w = max(1.0, outer_w - 6.0)   # margin so text clears the rim's outer/inner edges
    scale = min(target_h / natural_h, available_w / natural_w)
    text_obj.scale = (scale, scale, 1)

    raised_height = 0.6
    text_obj.data.extrude = 1.0
    # Z is set so the text is embedded well into the wall and only
    # ~raised_height pokes up above its top surface, regardless of whether
    # Curve.extrude turns out to be one- or two-sided.
    text_obj.location = (0, -outer_h / 2 + wall_width / 2, top_z - 1.0 + raised_height)

    bpy.context.view_layer.objects.active = text_obj
    txt.convert_text_to_mesh(text_obj.name, holder_obj.name, False)

    white_mat = bpy.data.materials.get("WHITE")
    text_obj.data.materials.clear()
    if white_mat:
        text_obj.data.materials.append(white_mat)

    bpy.ops.object.select_all(action='DESELECT')
    text_obj.select_set(True)
    holder_obj.select_set(True)
    bpy.context.view_layer.objects.active = holder_obj
    bpy.ops.object.join()

    return holder_obj


def build_puzzle_holder(piece_objs, text="", wall_width=4.0, wall_height=4.0,
                         floor_thickness=2.0, clearance=0.1, corner_radius=5.0,
                         pocket_corner_radius=0.0, font="", text_size_mm=None):
    """Build a rounded-rectangle tray sized to hold an already-generated
    jigsaw puzzle (cut_into_puzzle_pieces' output).

    The combined world-space XY bounding box of every object in *piece_objs*
    reconstructs the puzzle's true assembled footprint -- every outer grid
    edge is always straight, only internal seams have tabs, so the union of
    every piece's own bbox equals the original rectangle's footprint
    regardless of the per-piece tolerance shrink. The inner pocket is that
    footprint plus *clearance*; the outer footprint adds *wall_width* of rim
    on every side, with the OUTSIDE corners rounded to *corner_radius*. The
    pocket's own corners are rounded separately to *pocket_corner_radius* --
    normally passed in matching the puzzle's own corner radius (the puzzle
    itself is rounded client-side, baked directly into each piece's
    polygon), so the pocket visually matches whatever the puzzle's actual
    outer corners look like instead of always being sharp.
    The pocket is recessed `wall_height - floor_thickness` deep into the
    top, leaving a solid floor of *floor_thickness* underneath -- the rim
    outside the pocket keeps the full *wall_height*. The holder gets the
    BLACK material; embossed text (see `_emboss_holder_text`) gets WHITE.

    Reuses the same flat-prism + boolean technique as
    `cut_into_puzzle_pieces` / `single_color_mode_curve`.
    """
    from . import geometry2d as g2d  # deferred to avoid circular import at load time
    from .primitives import setupColors  # deferred to avoid circular import at load time

    objs = [o for o in (piece_objs or []) if o is not None]
    if not objs:
        return None

    x_min = y_min = float('inf')
    x_max = y_max = float('-inf')
    for obj in objs:
        corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        x_min = min(x_min, min(c.x for c in corners))
        x_max = max(x_max, max(c.x for c in corners))
        y_min = min(y_min, min(c.y for c in corners))
        y_max = max(y_max, max(c.y for c in corners))

    puzzle_w = x_max - x_min
    puzzle_h = y_max - y_min
    center_x, center_y = (x_min + x_max) / 2, (y_min + y_max) / 2

    pocket_w = puzzle_w + clearance
    pocket_h = puzzle_h + clearance
    outer_w = pocket_w + 2 * wall_width
    outer_h = pocket_h + 2 * wall_width

    outer_poly = _rounded_rect_polygon(outer_w, outer_h, corner_radius)
    pocket_poly = _rounded_rect_polygon(pocket_w, pocket_h, pocket_corner_radius)

    verts, faces = [], []
    for part in g2d.iter_polygons(outer_poly):
        _extrude_flat_polygon(g2d, part, 0.0, wall_height, verts, faces)
    if not verts:
        return None

    mesh = bpy.data.meshes.new("PuzzleHolder")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    _clean_solid_mesh(mesh)
    setupColors()
    black_mat = bpy.data.materials.get("BLACK")
    if black_mat:
        mesh.materials.append(black_mat)

    holder_obj = bpy.data.objects.new(mesh.name, mesh)
    bpy.context.collection.objects.link(holder_obj)

    # Small overshoot below the pocket's true floor (proportional to
    # floor_thickness, so it can never undercut the floor entirely even if
    # floor_thickness is set very thin) -- cheap insurance against a
    # perfectly coplanar cut face confusing the boolean solver.
    pocket_bottom_z = floor_thickness - min(0.05, floor_thickness * 0.25)
    pocket_verts, pocket_faces = [], []
    for part in g2d.iter_polygons(pocket_poly):
        _extrude_flat_polygon(g2d, part, pocket_bottom_z, wall_height + 5.0, pocket_verts, pocket_faces)
    cutter_mesh = bpy.data.meshes.new("PuzzleHolderPocketCutter")
    cutter_mesh.from_pydata(pocket_verts, [], pocket_faces)
    cutter_mesh.update()
    _clean_solid_mesh(cutter_mesh)
    cutter_obj = bpy.data.objects.new(cutter_mesh.name, cutter_mesh)
    bpy.context.collection.objects.link(cutter_obj)

    boolean_operation(holder_obj, cutter_obj, 'DIFFERENCE')
    bpy.data.objects.remove(cutter_obj, do_unlink=True)

    if text:
        _emboss_holder_text(holder_obj, text, outer_w, outer_h, wall_width, wall_height,
                             font=font, text_size_mm=text_size_mm)

    # Positioned at the puzzle's own XY center; Z so the pocket floor's TOP
    # surface (local Z = floor_thickness, where an assembled puzzle would
    # rest) lands exactly on world Z=0 -- the same plane the puzzle pieces
    # themselves sit on.
    holder_obj.location = (center_x, center_y, -floor_thickness)

    holder_obj["objType"] = "HOLDER"
    holder_obj["Object type"] = "HOLDER"
    # ExportGroup 2 == "Printed with Plate" (utils/metadata.py) -- reused
    # here for the holder so it groups together on export the same way.
    holder_obj["ExportGroup"] = 2

    return holder_obj


def single_color_mode_curve(crv, map, keepTolTrail = False, cutDepth = 2, projectionObj = None):
    """Build the single-color-mode trail strip + groove cutter for one curve.

    Builds two flat-topped/flat-bottomed prisms from the trail's 2D footprint
    via Shapely (fast -- no curve bevel/extrude tessellation), positioned so
    their bottom sits `trailCutDepth` below the curve and their top is well
    above any terrain. The exact-width prism is then INTERSECTed with the
    real map so it conforms to the terrain surface and stops exactly at the
    map's true edges (no height-sampling approximation, no artificial 2D-clip
    wall); the wider prism is DIFFERENCEd from `map` directly to carve the
    groove, for the same reason.
    """

    if projectionObj == None:
        projectionObj = map

    tol = bpy.context.scene.tp3d.tolerance #Tolerance between Map and the Trail on each side (0.2 worked great so far)
    minThickness = bpy.context.scene.tp3d.minThickness
    pathThickness = bpy.context.scene.tp3d.pathThickness

    trailCutDepth = min(cutDepth, minThickness/2) # How deep the trail will be placed into the map
                                            # Either 2mm or for flatter maps half of the minThickness

    from . import geometry2d as g2d  # deferred to avoid circular import at load time

    lowest_z = None

    if crv.type == "CURVE":
        # Ensure the curve is selected and active
        bpy.ops.object.select_all(action='DESELECT')
        crv.select_set(True)
        bpy.context.view_layer.objects.active = crv

        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.curve.select_all(action='SELECT')
        bpy.ops.curve.smooth()
        bpy.ops.object.mode_set(mode='OBJECT')

        mw = crv.matrix_world
        coords_list = []
        for spline in crv.data.splines:
            pts = spline.points if len(spline.points) > 0 else spline.bezier_points
            if len(pts) < 2:
                continue
            line = []
            for p in pts:
                w = mw @ Vector((p.co.x, p.co.y, p.co.z))
                if lowest_z is None or w.z < lowest_z:
                    lowest_z = w.z
                line.append((w.x, w.y))
            coords_list.append(line)
        if not coords_list:
            bpy.data.objects.remove(crv, do_unlink=True)
            return None

        ribbon = g2d.polylines_to_ribbon(coords_list, pathThickness / 2, quad_segs=4)
        thick_ribbon = g2d.polylines_to_ribbon(coords_list, pathThickness / 2 + tol, quad_segs=4)

    elif crv.type == "MESH":
        ribbon = g2d.footprint_with_holes(crv)
        # No tolerance growth here -- matches the original behaviour, which
        # duplicated the MESH-type input verbatim for the carving tool
        # (Mesh data has no bevel_depth to widen, unlike CURVE data).
        thick_ribbon = ribbon
        if crv.data.vertices:
            lowest_z = min((crv.matrix_world @ v.co).z for v in crv.data.vertices)
    else:
        bpy.data.objects.remove(crv, do_unlink=True)
        return None

    if ribbon is None or ribbon.is_empty or lowest_z is None:
        bpy.data.objects.remove(crv, do_unlink=True)
        return None

    bottom_z = lowest_z - trailCutDepth
    top_z = bottom_z + 100.0  # tall enough to clear any terrain

    verts, faces = [], []
    for poly in g2d.iter_polygons(ribbon):
        _extrude_flat_polygon(g2d, poly, bottom_z, top_z, verts, faces)

    if not verts:
        if not g2d._HAS_EARCUT:
            from .. import progress as _progress
            _progress.WarningsOverlay.add_warning(
                "Trail strip is empty -- mapbox_earcut failed to load (see the sidebar warning)", "error"
            )
        bpy.data.objects.remove(crv, do_unlink=True)
        return None

    t_verts, t_faces = [], []
    if thick_ribbon is not None and not thick_ribbon.is_empty:
        for poly in g2d.iter_polygons(thick_ribbon):
            _extrude_flat_polygon(g2d, poly, bottom_z, top_z, t_verts, t_faces)

    # Convert crv to MESH in place (preserves object identity -- other code
    # holds references to this exact object for later material/metadata
    # assignment), then replace its data with the flat prism above.
    bpy.ops.object.select_all(action='DESELECT')
    crv.select_set(True)
    bpy.context.view_layer.objects.active = crv
    bpy.ops.object.convert(target='MESH')

    new_mesh = bpy.data.meshes.new(crv.data.name)
    new_mesh.from_pydata(verts, [], faces)
    new_mesh.update()
    _clean_solid_mesh(new_mesh)
    old_mesh = crv.data
    crv.data = new_mesh
    bpy.data.meshes.remove(old_mesh)
    # verts/faces above are in WORLD space. crv may still carry a
    # non-identity transform from before conversion, which would otherwise
    # re-apply on top and shift the mesh.
    crv.matrix_world = Matrix.Identity(4)

    # Make the visible trail strip follow the terrain: INTERSECT against the
    # real map clips the tall prism down to (terrain top surface) above,
    # (bottom_z, flat) below -- exact, no sampling approximation, and bounded
    # by the map's true edges with no artificial wall.
    remeshClearing(crv, 0.2, 0, map)
    boolean_operation(crv, projectionObj, 'INTERSECT')

    if len(crv.data.vertices) == 0:
        bpy.data.objects.remove(crv, do_unlink=True)
        return None

    # crv.matrix_world was reset to identity above (verts/faces are already
    # in world space), which left its origin sitting at world (0,0,0)
    # instead of the map it belongs to. Re-home it to the 3D cursor, which
    # createTerrainFromSelected's per-tile loop already parks at the map's
    # own location before trail processing runs.
    from .scene import set_origin_to_3d_cursor  # deferred to avoid circular import at load time
    set_origin_to_3d_cursor(crv)

    # Build the wider carving tool and cut the groove directly into `map`
    # with a real 3D boolean -- same reasoning, naturally bounded at the
    # map's true edges.
    set_origin_to_3d_cursor(crv)
    crv_thick = None
    if t_verts:
        thick_mesh = bpy.data.meshes.new(f"{crv.name}_thick")
        thick_mesh.from_pydata(t_verts, [], t_faces)
        thick_mesh.update()
        _clean_solid_mesh(thick_mesh)
        crv_thick = bpy.data.objects.new(f"{crv.name}_thick", thick_mesh)
        bpy.context.collection.objects.link(crv_thick)
        set_origin_to_3d_cursor(crv_thick)
        bpy.ops.object.select_all(action='DESELECT')
        crv_thick.select_set(True)
        bpy.context.view_layer.objects.active = crv_thick
        remeshClearing(crv_thick, 0.2, 0, map)
        #boolean_operation(map, crv_thick, 'DIFFERENCE', solver='EXACT')
        boolean_operation(map, crv_thick, 'DIFFERENCE')

    if not keepTolTrail:
        if crv_thick is not None:
            if bpy.app.debug:
                # DEBUG ONLY: keep the groove-carving solid around (parked in
                # its own collection) instead of deleting it, so a map that
                # comes out empty after the DIFFERENCE above can be inspected
                # -- e.g. a non-manifold/pinch-point crv_thick is the known
                # cause (see docs/roads-shapely-approach.md).
                debug_coll = g2d.debug_collection("TP3D_Debug_GrooveCutters")
                for coll in list(crv_thick.users_collection):
                    coll.objects.unlink(crv_thick)
                debug_coll.objects.link(crv_thick)
            else:
                bpy.data.objects.remove(crv_thick, do_unlink=True)
        return (crv, None)
    return (crv, crv_thick)


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

    # The flat bottom shell carries the ocean region's full triangulation
    # (every internal diagonal plus the bridge edges that represent island
    # holes).  The wireframe modifier below turns EVERY remaining edge into an
    # engraved groove, so those internal diagonals would be imprinted all over
    # the land surface.  Dissolving the coplanar bottom faces back into clean
    # n-gons collapses all of that interior geometry, leaving only the true
    # region boundary -- the coastline and each island outline.
    bm = bmesh.from_edit_mesh(obj.data)
    bmesh.ops.dissolve_limit(
        bm,
        angle_limit=math.radians(1.0),
        verts=bm.verts[:],
        edges=bm.edges[:],
    )
    bmesh.update_edit_mesh(obj.data)
    bpy.ops.object.mode_set(mode='OBJECT')

    # Guard: if the face deletion wiped all vertices (can happen when the
    # intersection left only upward-facing faces), bail out gracefully.
    if not obj.data.vertices:
        bpy.data.objects.remove(obj, do_unlink=True)
        return

    # Record the z level of the bottom plane before wireframe
    bottom_z = min(v.co.z for v in obj.data.vertices)

    mw_obj = obj.matrix_world
    bottom_z_world = min((mw_obj @ v.co).z for v in obj.data.vertices)
    if map is not None and map.data.vertices:
        mw_map = map.matrix_world
        map_top_z = max((mw_map @ v.co).z for v in map.data.vertices)
        _extrude_height = max(10.0, map_top_z - bottom_z_world + 2.0)
    else:
        _extrude_height = 50.0

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




    # Fill the edge loop to create a face (equivalent of pressing F)
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.edge_face_add()

    # Extrude upward past the top of the map
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.extrude_region_move(
        TRANSFORM_OT_translate={"value": (0, 0, _extrude_height)}
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


def remeshClearing(obj, voxelSize2, tolerance, map_obj=None):


    # Nothing valid to measure if the element is empty.
    if not obj.data.vertices:
        print("[remeshClearing] object has no vertices on entry -- skipping")
        return

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
    remesh.use_smooth_shade = False



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
    if not xs:
        print("[remeshClearing] object has no vertices after remesh -- skipping")
        return
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
    cube_obj.scale    = (sx, sy, 150.0)
    cube_obj.location = (cx, cy, 75.0+bottom_z)   # bottom face lands at z=0, top at z=50

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bool_mod = obj.modifiers.new(name="_BoolCube", type='BOOLEAN')
    bool_mod.operation = 'DIFFERENCE'
    bool_mod.object    = cube_obj
    bool_mod.solver    = 'MANIFOLD'


    applyModifier(obj, bool_mod)
    bpy.data.objects.remove(cube_obj, do_unlink=True)

    # The cube DIFFERENCE can remove every face if the element sat entirely
    # inside the cut volume.  Bail out before measuring an empty mesh.
    if not obj.data.vertices:
        print("[remeshClearing] object emptied by cube cut -- skipping")
        return

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
    # Drop the stale reference BEFORE the operator modifies the mesh.
    # Keeping bm2 alive past mesh.delete causes a dangling C pointer:
    # on the second generation Blender reuses that freed memory and
    # python313.dll crashes when mode_set later finalises the edit mesh.
    del bm2
    bpy.ops.mesh.delete(type='VERT')

    # Flatten remaining verts to exactly z bottom_z
    bm = bmesh.from_edit_mesh(obj.data)
    for v in bm.verts:
        v.co.z = bottom_z
    bmesh.update_edit_mesh(obj.data)
    del bm  # discard before exiting edit mode to avoid the same issue

    if map_obj is not None and map_obj.data.vertices:
        mw = map_obj.matrix_world
        map_top_z = max((mw @ v.co).z for v in map_obj.data.vertices)
        extrude_height = max(10.0, map_top_z - bottom_z + 2.0)
    else:
        extrude_height = 30.0

    bpy.ops.mesh.select_mode(type='FACE')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.extrude_region_move(
        TRANSFORM_OT_translate={"value": (0, 0, extrude_height)}
    )
    bpy.ops.object.mode_set(mode='OBJECT')



    recalculateNormals(obj)


def _select_bottom_and_sides(obj):
    """Select obj's bottom face (lowest-Z face -- the single dissolved N-gon
    the generation pipeline leaves at the bottom) plus every face directly
    touching it: one "select more" step. Since the bottom is one big N-gon
    bordering every side-wall face directly, that single step selects
    exactly bottom + sides, leaving the top (terrain) faces unselected --
    without needing any normal-angle threshold, so it isn't fooled by steep
    terrain the way selectTopFaces was. Leaves obj in Edit Mode, face-select
    mode, with bottom+sides selected."""
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='FACE')
    bpy.ops.mesh.select_all(action='DESELECT')
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bottom_face = min(bm.faces, key=lambda f: sum(v.co.z for v in f.verts) / len(f.verts))
    bottom_face.select = True
    bmesh.update_edit_mesh(obj.data)
    bpy.ops.mesh.select_more()


def _extract_bottom_and_sides(map_obj, name):
    """Duplicate map_obj and keep only its bottom + side-wall faces (the
    real, terrain-following outer shell) -- discards the top/terrain cap."""
    bpy.ops.object.select_all(action='DESELECT')
    map_obj.select_set(True)
    bpy.context.view_layer.objects.active = map_obj
    bpy.ops.object.duplicate()
    shell_obj = bpy.context.view_layer.objects.active
    shell_obj.name = name

    _select_bottom_and_sides(shell_obj)  # leaves Edit Mode, bottom+sides selected
    bpy.ops.mesh.select_all(action='INVERT')
    bpy.ops.mesh.delete(type='FACE')
    bpy.ops.object.mode_set(mode='OBJECT')
    return shell_obj


def _offset_shell_xy(shell_obj, amount, cx, cy):
    """Grow every vertex of shell_obj radially outward in the XY plane
    (about world (cx, cy)) by `amount`, leaving Z untouched entirely -- the
    tolerance/wall gap, without disturbing the terrain-following heights
    that came along for free by extracting the map's own real geometry.

    Round-trips through matrix_world rather than assuming world = local +
    location -- ELLIPSE objects carry a real object-level Y scale (their
    aspect ratio never gets baked into the mesh), so that shortcut computes
    the wrong world position and corrupts the offset for that shape.
    """
    if amount == 0:
        return
    mesh = shell_obj.data
    mw = shell_obj.matrix_world
    mw_inv = mw.inverted()
    for v in mesh.vertices:
        w = mw @ v.co
        dx, dy = w.x - cx, w.y - cy
        d = math.hypot(dx, dy)
        if d > 1e-9:
            new_world = Vector((cx + dx / d * (d + amount), cy + dy / d * (d + amount), w.z))
            v.co = mw_inv @ new_world
    mesh.update()


def _push_bottom_face(obj, amount):
    """Translate obj's bottom (lowest-Z) face straight down by `amount` --
    the floor's own tolerance-gap / wall-thickness, kept independent of the
    XY side offset since a flat floor doesn't get one "for free" the way
    the sides do."""
    if amount == 0:
        return
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='FACE')
    bpy.ops.mesh.select_all(action='DESELECT')
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bottom_face = min(bm.faces, key=lambda f: sum(v.co.z for v in f.verts) / len(f.verts))
    bottom_face.select = True
    bmesh.update_edit_mesh(obj.data)
    bpy.ops.transform.translate(value=(0, 0, -amount))
    bpy.ops.object.mode_set(mode='OBJECT')


def _stretch_up_from_bottom(obj, factor):
    """Scale obj's Z coordinates by `factor` about its own bottom (lowest-Z)
    point -- stretches everything above the floor further upward while
    keeping the floor itself fixed. Used to grow the cavity cutter taller
    than the outer wall's own highest point, so its cap safely removes
    everything above it (any residual sliver between the two independently
    fan-triangulated caps) instead of just meeting it edge-to-edge."""
    if factor == 1:
        return
    mesh = obj.data
    bottom_z = min(v.co.z for v in mesh.vertices)
    for v in mesh.vertices:
        v.co.z = bottom_z + (v.co.z - bottom_z) * factor
    mesh.update()


def _cap_boundary(obj):
    """Close obj's single open boundary loop (where the top/terrain cap
    used to be) by fanning triangles out from a center point to every
    boundary edge -- turns the open bottom+sides shell into a valid closed
    solid, right at its own natural (terrain-following) height.

    A fan instead of bpy.ops.mesh.fill(): fill() can leave gaps on a
    genuinely bumpy (non-planar) boundary loop -- fine on a flat rim, but a
    real terrain-following one trips it up. A fan is reliable here because
    the map's outline is always convex, so every boundary edge is visible
    from an interior point regardless of how bumpy the rim's height is; the
    fan's own shape doesn't matter beyond that; only its outer ring survives
    the later boolean difference anyway.
    """
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()

    remaining = {e for e in bm.edges if len(e.link_faces) == 1}
    rings = []
    while remaining:
        e0 = next(iter(remaining))
        remaining.discard(e0)
        ring = [e0.verts[0], e0.verts[1]]
        while True:
            last = ring[-1]
            nxt_e = next((e for e in remaining if last in e.verts), None)
            if nxt_e is None:
                break
            remaining.discard(nxt_e)
            ring.append(nxt_e.other_vert(last))
        if len(ring) > 1 and ring[0] == ring[-1]:
            ring = ring[:-1]
        if len(ring) >= 3:
            rings.append(ring)

    for ring in rings:
        n = len(ring)
        cx = sum(v.co.x for v in ring) / n
        cy = sum(v.co.y for v in ring) / n
        cz = sum(v.co.z for v in ring) / n
        center = bm.verts.new((cx, cy, cz))
        for i in range(n):
            a, b = ring[i], ring[(i + 1) % n]
            try:
                bm.faces.new((center, a, b))
            except ValueError:
                pass  # degenerate/duplicate face -- skip

    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    recalculateNormals(obj)


def build_map_shell(map_obj, tolerance, wall=2.0, bottom_wall=1.0):
    """Build a snug, open-top shell from the map's own real bottom+side
    geometry, instead of reconstructing/approximating the shape.

    Selects the bottom face and grows the selection once (bottom + every
    directly-touching face = the side walls, since the bottom is a single
    dissolved N-gon touching every side face -- the same thing pressing
    Ctrl+Numpad+ once in the viewport would select), discards the top
    (terrain) cap, then grows that shell outward in X/Y only for the
    tolerance gap and wall thickness. Because the shell comes from the
    map's actual mesh, the wall's top edge follows the real terrain exactly
    -- not sampled, interpolated, or threshold-detected, so it can't drift
    off the true height the way the previous normal-angle-based approaches
    did on steep terrain.
    """
    cx, cy = map_obj.location.x, map_obj.location.y

    # Fully process one shell (extract -> offset -> push -> cap) before
    # starting the next -- interleaving the same step across both objects
    # (all extracts, then all offsets, ...) has been observed to leave the
    # second object's edit-mode boundary selection empty in background mode.
    outer_obj = _extract_bottom_and_sides(map_obj, "_ShellOuter")
    _offset_shell_xy(outer_obj, tolerance + wall, cx, cy)
    _push_bottom_face(outer_obj, tolerance + bottom_wall)
    _cap_boundary(outer_obj)

    inner_obj = _extract_bottom_and_sides(map_obj, "_ShellInner")
    _offset_shell_xy(inner_obj, tolerance, cx, cy)
    _push_bottom_face(inner_obj, tolerance)
    _stretch_up_from_bottom(inner_obj, 1.5)  # grow past outer's own rim height, so it cuts the top fully open
    _cap_boundary(inner_obj)

    boolean_operation(outer_obj, inner_obj, 'DIFFERENCE')
    bpy.data.objects.remove(inner_obj, do_unlink=True)

    if not outer_obj.data.polygons:
        print("[build_map_shell] empty after hollowing -- skipping")
        bpy.data.objects.remove(outer_obj, do_unlink=True)
        return None

    recalculateNormals(outer_obj)
    outer_obj.name = map_obj.name + "_Shell"
    return outer_obj


def single_color_mode_mesh_remesh(original, map, tolerance = None):

    #Original = Element usually

    if tolerance == None:
        tolerance = bpy.context.scene.tp3d.toleranceElements

    from . import geometry2d as _g2d  # deferred to avoid circular import at load time

    # ── Build the cutter from the element's 2D footprint (interior holes kept) ──
    # The old path isolated the bottom cap and voxel-remeshed it into a solid.
    # That voxel remesh filled every interior hole narrower than the voxel
    # (river-loop islands, lake islets, courtyards), so the map DIFFERENCE
    # carved the enclosed land into a void. Instead we union the element's
    # downward faces into a Shapely footprint -- which preserves those holes as
    # interior rings by construction -- dilate it by the tolerance gap, earcut
    # it into a flat manifold cap (holes intact), and extrude that into a prism.
    fp = _g2d.footprint_with_holes(original, down_only=True)
    if fp is None or fp.is_empty:
        print("[single_color_mode_mesh_remesh] empty footprint -- skipping element")
        return None

    if tolerance > 0:
        # Dilate the footprint OUTWARD by tolerance * SCM_ELEMENT_GAP_FACTOR.
        # This makes the recess in the terrain slightly larger than the
        # element, leaving a clean printed gap around it. Growing (not
        # insetting) also: thickens thin rivers so they still cut instead of
        # collapsing; shrinks the island holes by the same amount, giving a
        # matching gap around enclosed land; and extends the cutter past the
        # map edge at the boundary, so the terrain side walls get cut away too.
        from .. import constants as _const  # deferred to avoid circular import at load time
        gap = tolerance * _const.SCM_ELEMENT_GAP_FACTOR
        if gap > 0:
            fp = fp.buffer(gap)
            fp = _g2d.validate(fp)
            if fp is None or fp.is_empty:
                print("[single_color_mode_mesh_remesh] footprint empty after tolerance buffer -- skipping")
                return None

    # World-space bottom of the element: the prism floor (recess depth) sits here.
    mw = original.matrix_world
    bottom_z = min((mw @ v.co).z for v in original.data.vertices)
    if map is not None and map.data.vertices:
        mw_map = map.matrix_world
        map_top_z = max((mw_map @ v.co).z for v in map.data.vertices)
        PRISM_HEIGHT = max(10.0, map_top_z - bottom_z + 2.0)
    else:
        PRISM_HEIGHT = 30.0

    # Earcut a flat cap (holes preserved) for every polygon part, then merge.
    caps = []
    for poly in _g2d.iter_polygons(fp):
        cap = _g2d.polygon_to_mesh("_cutter_cap", poly)
        if cap is not None:
            caps.append(cap)
    if not caps:
        print("[single_color_mode_mesh_remesh] no cap geometry -- skipping")
        return None
    obj = caps[0] if len(caps) == 1 else merge_objects(caps)
    if obj is None:
        return None

    # Drop the caps to the recess floor, orient them downward, and extrude up
    # into a watertight manifold prism (holes become clean tunnels through it).
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    for v in bm.verts:
        v.co.z = bottom_z
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    up_faces = [f for f in bm.faces if f.normal.z > 0]
    if up_faces:
        bmesh.ops.reverse_faces(bm, faces=up_faces)
    ret = bmesh.ops.extrude_face_region(bm, geom=bm.faces[:])
    ext_verts = [g for g in ret["geom"] if isinstance(g, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, verts=ext_verts, vec=Vector((0, 0, PRISM_HEIGHT)))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(obj.data)
    bm.free()
    obj.name = f"{original.name}_cutter"

    # Boolean subtract from map
    boolean = map.modifiers.new(name="Boolean", type='BOOLEAN')
    boolean.operation = 'DIFFERENCE'
    boolean.object = obj
    boolean.solver = 'MANIFOLD'
    applyModifier(map, boolean)

    if "type" in original and original["type"] == "OTHER":
        print("Setting ExportGroup to 0 for OTHER type")
        original["ExportGroup"] = 0


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

    # The ocean object is tagged in _build_ocean_mesh; identify it so its flat
    # bottom can be kept flush with the terrain base (water must never dip
    # below the print's base plane).
    is_ocean = mergeobject.get("_tp3d_is_ocean", False)

    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.extrude_region_move()
    bpy.ops.transform.translate(value=(0, 0, 200))
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




        bottom_drop = secondlowestprojection - lowestprojection - 1
        if is_ocean:
            # Water must never sit below the terrain base.  The selected bottom
            # face is already flush with the map base (z = lowestprojection);
            # clamp the skirt so it is never pushed below that plane.
            bottom_drop = max(bottom_drop, 0.0)
        bpy.ops.transform.translate(value=(0, 0, bottom_drop), orient_type='LOCAL')

        #bpy.ops.mesh.select_all(action='DESELECT')
        pass


    bmesh.update_edit_mesh(mergeobject.data)
    bpy.ops.object.mode_set(mode="OBJECT")


    if not singleColorMode:
        mergeobject.location.z += 0.05

    return mergeobject


def merge_active_with_map(map_obj, active_obj):
    """Merge `active_obj` onto `map_obj`, dispatching by type.

    MESH/FONT objects go through the interactive `tp3d.popup_merge` operator;
    CURVE objects (trails) go through `merge_with_map` or, in Single Color
    Mode, a duplicated `single_color_mode_curve` pass with the TRAIL material
    applied. Shared by TP3D_OT_merge_with_map so the same logic can be reused
    from other operators/scripts without going through that operator's UI.

    Returns False if `map_obj` failed validation (caller should abort the
    whole batch, matching the original operator's early-return behaviour),
    True otherwise -- including the "invalid active type" case, which only
    warns and lets the caller continue on to the next object.
    """
    from .scene import show_message_box  # deferred to avoid circular import at load time

    if map_obj is None:
        show_message_box("No Map Selected")
        return False

    if "objSize" not in map_obj.keys():
        show_message_box("Selected object is not a Map")
        return False

    if active_obj.type in ("MESH", "FONT"):
        bpy.context.scene.tp3d.currentMap = map_obj
        active_obj.select_set(True)
        bpy.context.view_layer.objects.active = active_obj
        bpy.ops.object.select_all(action='DESELECT')
        bpy.ops.tp3d.popup_merge('INVOKE_DEFAULT')

    elif active_obj.type == "CURVE":
        if not bpy.context.scene.tp3d.singleColorMode:
            merge_with_map(map_obj, active_obj, True, False)
            active_obj.hide_set(True)
        else:
            dup = active_obj.copy()
            dup.data = active_obj.data.copy()
            bpy.context.collection.objects.link(dup)
            result = single_color_mode_curve(dup, map_obj, False)
            if result is None:
                # No intersection with the map -- single_color_mode_curve
                # already deleted `dup` itself, so there's nothing left to
                # assign a material to.
                print("Trail does not intersect the Map")
            else:
                # Keep whatever material the original trail had (e.g. trails
                # generated with alternating TRAIL/YELLOW materials) instead
                # of forcing TRAIL on every merge.
                mat = active_obj.data.materials[0] if active_obj.data.materials else bpy.data.materials.get("TRAIL")
                dup.data.materials.clear()
                dup.data.materials.append(mat)

    else:
        show_message_box("Selected object has a invalid type")

    return True


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
        if o.type not in ('MESH', 'FONT') or o.data is None:
            raise ValueError(f"projection: '{label}' ('{name}') is not a valid mesh object (type={o.type!r})")

    if operation == "paint":
        merge_with_map(Mapobject, obj)

        #obj.location.z += 1


        bpy.ops.object.origin_set(type='ORIGIN_CURSOR', center='MEDIAN')
        color_map_faces_by_terrain(Mapobject, obj)
        mesh_data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh_data)

    if operation == "separate":
        merge_with_map(Mapobject, obj, False)

        obj.data.materials.clear()

        obj.location.z += 0.2
        if "TYPE" in obj and obj["TYPE"] == "OTHER":
                obj["ExportGroup"] = 1

    if operation == "singleColorMode":

        merge_with_map(Mapobject, obj, True)

        obj.data.materials.clear()

        single_color_mode_mesh_wireframe(obj, Mapobject)


    if operation == "singleColorMode_remesh":

        merge_with_map(Mapobject, obj, True)

        obj.data.materials.clear()

        thicker = single_color_mode_mesh_remesh(obj, Mapobject)


        remove_objects(thicker)

    if operation == "negative":
        merge_with_map(Mapobject, obj, True)
        boolean_operation(Mapobject, obj, "DIFFERENCE")
        remove_objects(obj)
