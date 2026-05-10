import bpy  # type: ignore
import bmesh  # type: ignore
import random
import platform
import webbrowser
from mathutils import Vector, bvhtree  # type: ignore


def open_website(self, context, url="https://patreon.com/EmGi3D?utm_source=Blender"):
    print(url)
    webbrowser.open(url)


def transform_MapObject(obj, newX, newY):
    obj.location.x += newX
    obj.location.y += newY


def zoom_camera_to_selected(obj):
    if obj is None:
        return
    try:
        obj.select_set  # raises ReferenceError if the object was freed
    except ReferenceError:
        return

    bpy.ops.object.select_all(action='DESELECT')

    obj.select_set(True)  # Select the object

    area = [area for area in bpy.context.screen.areas if area.type == "VIEW_3D"][0]
    region = area.regions[-1]

    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.view3d.view_selected(use_all_regions=False)


def set_origin_to_3d_cursor(tobj=None):
    if tobj is None:
        tobj = bpy.context.active_object

    bpy.ops.object.select_all(action='DESELECT')

    bpy.context.view_layer.objects.active = tobj
    tobj.select_set(True)
    bpy.ops.object.origin_set(type='ORIGIN_CURSOR')

def set_origin_to_geometry(tobj=None):
    if tobj is None:
        tobj = bpy.context.active_object

    bpy.ops.object.select_all(action='DESELECT')

    bpy.context.view_layer.objects.active = tobj
    tobj.select_set(True)
    bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='BOUNDS')

def get_random_world_vertices(obj, count=5):

    """
    Returns a list of random vertex world coordinates from an object.
    Works with Meshes, Curves, Text, Surfaces, etc. by evaluating to a mesh.
    """

    depsgraph = bpy.context.evaluated_depsgraph_get()
    obj_eval = obj.evaluated_get(depsgraph)

    temp_mesh = None
    needs_free = False

    try:
        if obj_eval.type == 'MESH':
            # Direct mesh access
            mesh = obj_eval.to_mesh()
            needs_free = True
        else:
            # Convert curve / text / surface to mesh
            temp_mesh = bpy.data.meshes.new_from_object(
                obj_eval,
                depsgraph=depsgraph
            )
            mesh = temp_mesh

        if not mesh or not mesh.vertices:
            return []

        count = min(count, len(mesh.vertices))
        chosen = random.sample(mesh.vertices[:], count)

        world_mat = obj_eval.matrix_world
        world_points = [world_mat @ v.co for v in chosen]

        return world_points

    finally:
        # Cleanup
        if needs_free:
            obj_eval.to_mesh_clear()
        if temp_mesh:
            bpy.data.meshes.remove(temp_mesh)


def get_object_surface_area(obj, apply_modifiers=True, z_threshold = 0.00):

    if obj.type != 'MESH':
        raise TypeError(f"Object '{obj.name}' is not a mesh.")

    # Get evaluated mesh if needed
    if apply_modifiers:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        obj_eval = obj.evaluated_get(depsgraph)
        mesh = obj_eval.to_mesh()
        world_matrix = obj_eval.matrix_world
    else:
        mesh = obj.data
        world_matrix = obj.matrix_world

    # Build bmesh
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.normal_update()

    # World-space normal transform
    normal_matrix = world_matrix.to_3x3()

    total_area = 0.0

    for f in bm.faces:
        world_normal = (normal_matrix @ f.normal).normalized()

        # Count only faces pointing downward
        if world_normal.z < z_threshold:
            total_area += f.calc_area()

    # Cleanup
    bm.free()
    if apply_modifiers:
        obj_eval.to_mesh_clear()

    return total_area

def setOriginToTerrainFace(obj,tol=0.1,seed=None,max_tries=200):

    mesh = obj.data
    if len(mesh.polygons) == 0:
        return {"success": False, "face_index": None, "center": None, "message": "Mesh has no faces"}

    # optionally seed RNG
    if seed is not None:
        random.seed(seed)

    mw = obj.matrix_world
    rot3 = mw.to_3x3()
    def face_world_normal_z(poly):
        n = poly.normal
        wn = rot3 @ n
        wn.normalize()
        return wn.z

    # collect candidate face indices that are not vertical +- tolerance
    candidates = [p.index for p in mesh.polygons if abs(face_world_normal_z(p)) >= abs(tol)]

    # Try picking random candidates (to avoid always picking the same face)
    face_idx = None
    # attempt a few random picks first
    tries = 0
    while tries < min(max_tries, len(candidates)):
        tries += 1
        idx = random.choice(candidates)
        # accept immediately (we already filtered), but keep tries logic in case you add more checks
        face_idx = idx
        break

    # fallback: if not found via random attempts, take first candidate
    if face_idx is None and candidates:
        face_idx = candidates[0]

    poly = mesh.polygons[face_idx]

    # compute face center in world coordinates (average of vertex world coords)
    center_local = Vector((0.0, 0.0, 0.0))
    for vid in poly.vertices:
        v_local = mesh.vertices[vid].co
        center_local += v_local
    center_local /= len(poly.vertices)
    center_world = mw @ center_local

    # store previous cursor location to restore later
    scene = bpy.context.scene
    prev_cursor = scene.cursor.location.copy()

    # store selection & active object & mode
    prev_active = bpy.context.view_layer.objects.active
    prev_mode = prev_active.mode if prev_active is not None else None
    prev_sel_states = {o: o.select_get() for o in bpy.context.view_layer.objects}

    try:
        # place 3D cursor at face center
        scene.cursor.location = center_world

        # make our object active and selected
        bpy.context.view_layer.objects.active = obj
        # ensure object selected
        for o in bpy.context.view_layer.objects:
            o.select_set(False)
        obj.select_set(True)

        # ensure object mode (origin_set requires object mode)
        bpy.ops.object.mode_set(mode='OBJECT', toggle=False)

        # set origin to cursor
        bpy.ops.object.origin_set(type='ORIGIN_CURSOR')

    except Exception as e:
        # restore cursor & selection before returning
        scene.cursor.location = prev_cursor
        # restore active & selection
        for o, sel in prev_sel_states.items():
            o.select_set(sel)
        bpy.context.view_layer.objects.active = prev_active
        if prev_mode:
            try:
                bpy.ops.object.mode_set(mode=prev_mode)
            except Exception:
                pass
        return {"success": False, "face_index": face_idx, "center": center_world,
                "message": f"Error while setting origin: {e}"}

    # restore cursor to previous location
    scene.cursor.location = prev_cursor

    # restore active object & selection & mode
    for o, sel in prev_sel_states.items():
        o.select_set(sel)
    bpy.context.view_layer.objects.active = prev_active
    if prev_mode:
        try:
            bpy.ops.object.mode_set(mode=prev_mode)
        except Exception:
            pass

    return


def closest_distance_between_objects(obj_a, obj_b, apply_modifiers=True):

    if obj_a.type != 'MESH' or obj_b.type != 'MESH':
        raise TypeError("Both objects must be mesh objects")

    depsgraph = bpy.context.evaluated_depsgraph_get()

    def eval_mesh(obj):
        if apply_modifiers:
            obj_eval = obj.evaluated_get(depsgraph)
            mesh = obj_eval.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph)
            mat = obj_eval.matrix_world
        else:
            mesh = obj.data
            mat = obj.matrix_world
            obj_eval = None
        return obj_eval, mesh, mat

    # --- Object A: BVH ---
    obj_a_eval, mesh_a, mat_a = eval_mesh(obj_a)

    bm_a = bmesh.new()
    bm_a.from_mesh(mesh_a)
    bm_a.transform(mat_a)

    bvh_a = bvhtree.BVHTree.FromBMesh(bm_a)

    bm_a.free()
    if obj_a_eval:
        obj_a_eval.to_mesh_clear()

    # --- Object B: iterate vertices ---
    obj_b_eval, mesh_b, mat_b = eval_mesh(obj_b)

    min_dist = float("inf")

    for v in mesh_b.vertices:
        world_co = mat_b @ v.co
        hit = bvh_a.find_nearest(world_co)

        if hit:
            _, _, _, dist = hit
            if dist < min_dist:
                min_dist = dist
                if min_dist == 0.0:
                    break  # early exit

    if obj_b_eval:
        obj_b_eval.to_mesh_clear()

    return min_dist


def remove_objects(objects):
    """
    Remove a single object or a list of objects efficiently (script-friendly).
    """
    if not objects:
        return

    # Ensure it's always a list
    if not isinstance(objects, (list, tuple)):
        objects = [objects]

    objects = [obj for obj in objects if obj is not None]
    if not objects:
        return

    try:
        # Fast path: select all and delete in one operator call, triggering only
        # a single depsgraph update instead of one per object. Requires a valid
        # viewport context, so it will raise RuntimeError in background/headless mode.
        bpy.ops.object.select_all(action='DESELECT')
        for obj in objects:
            obj.select_set(True)
        bpy.ops.object.delete()
    except RuntimeError:
        # Fallback for background, headless, or modal contexts where bpy.ops is
        # unavailable. Slower for large lists due to per-object depsgraph updates.
        for obj in objects:
            for col in obj.users_collection:
                col.objects.unlink(obj)
            bpy.data.objects.remove(obj)


def getHighestLowest(obj):

    # Get the bounding box corners in world space
    world_matrix = obj.matrix_world
    corners = [world_matrix @ Vector(corner) for corner in obj.bound_box]

    # Extract the Z values
    z_values = [c.z for c in corners]

    highest_z = max(z_values)
    lowest_z = min(z_values)

    return lowest_z, highest_z

def show_message_box(message, ic = "ERROR", ti = "ERROR"):
    def draw(self, context):
        self.layout.label(text=message)
    print(message)
    bpy.context.window_manager.popup_menu(draw, title=ti, icon=ic)


def toggle_console():
    try:
        if platform.system() == "Windows":
            bpy.ops.wm.console_toggle()
    except Exception as e:
        print(f"Could not toggle console: {e}")


def importSVGtoMerge(Mapobject):
    from .mesh_ops import merge_objects  # deferred to avoid circular import at load time

    svg_path = bpy.context.scene.tp3d.svg_path
    extrude_depth = 0.01
    scale_factor = 100.0

    # -----------------------
    # 1. Import SVG
    # -----------------------

    # Objects before import
    before = set(bpy.data.objects)
    before_collections = set(bpy.data.collections)

    bpy.ops.import_curve.svg(filepath=svg_path)

    # Objects after import
    after = set(bpy.data.objects)

    # Move SVG objects from the auto-created collection to Scene Collection and remove it
    scene_col = bpy.context.scene.collection
    for new_col in set(bpy.data.collections) - before_collections:
        for obj in list(new_col.all_objects):
            if not scene_col.objects.get(obj.name):
                scene_col.objects.link(obj)
        bpy.data.collections.remove(new_col, do_unlink=True)

    # Newly created objects
    svg_objs = [obj for obj in after - before if obj.type == 'CURVE']



    for obj in svg_objs:
        curve = obj.data

        # Curve setup (preserve holes)
        curve.dimensions = '2D'
        curve.fill_mode = 'BOTH'
        curve.use_fill_caps = True

        curve.bevel_depth = 0
        curve.bevel_resolution = 0

        curve.extrude = extrude_depth


        # Scale SVG
        obj.scale *= scale_factor


        # Convert to mesh
        bpy.ops.object.select_all(action='DESELECT')

        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        bpy.ops.object.convert(target='MESH')

        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)


        bpy.ops.object.mode_set(mode='EDIT')

        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.remove_doubles(threshold=0.0001)  # Works in all 2.8+ versions

        bpy.ops.object.mode_set(mode='OBJECT')

    bpy.ops.object.select_all(action='DESELECT')
    svg = merge_objects(svg_objs)

    svg.select_set(True)
    bpy.context.view_layer.objects.active = svg

    bpy.ops.object.origin_set(
        type='ORIGIN_GEOMETRY',
        center='BOUNDS'
    )

    # Move object so origin is at world center
    svg.location = bpy.context.scene.cursor.location

    return svg
