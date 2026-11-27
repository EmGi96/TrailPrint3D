import math
import platform
import random
import webbrowser

import bmesh  # type: ignore
import bpy  # type: ignore
from bpy.app.translations import pgettext_iface as _
from mathutils import Vector, bvhtree  # type: ignore


def open_website(self, context, url="https://patreon.com/EmGi3D?utm_source=Blender"):
    print(url)
    webbrowser.open(url)


def transform_MapObject(obj, newX, newY):
    obj.location.x += newX
    obj.location.y += newY


def zoom_camera_to_objects(objs):
    """Select every object in *objs* and zoom the 3D viewport to fit all of
    them. Pass a one-item list for a single object; for results made of
    several separate objects (e.g. puzzle pieces) pass them all, since
    fitting to just one would zoom in too far and miss the rest."""
    objs = [o for o in (objs or []) if o is not None]
    if not objs:
        return
    try:
        _ = objs[0].select_set  # raises ReferenceError if the object was freed
    except ReferenceError:
        return

    bpy.ops.object.select_all(action='DESELECT')
    for o in objs:
        try:
            o.select_set(True)
        except ReferenceError:
            continue
    bpy.context.view_layer.objects.active = objs[0]

    area = next(area for area in bpy.context.screen.areas if area.type == "VIEW_3D")
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
        raise TypeError(_("Object '{name}' is not a mesh.").format(name=obj.name))

    obj_eval = None
    if apply_modifiers:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        obj_eval = obj.evaluated_get(depsgraph)
        mesh = obj_eval.to_mesh()
        world_matrix = obj_eval.matrix_world
    else:
        mesh = obj.data
        world_matrix = obj.matrix_world

    try:
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bm.normal_update()

        normal_matrix = world_matrix.to_3x3()
        total_area = 0.0

        for f in bm.faces:
            world_normal = (normal_matrix @ f.normal).normalized()
            if world_normal.z < z_threshold:
                total_area += f.calc_area()

        bm.free()
    finally:
        if obj_eval is not None:
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

    except RuntimeError as e:
        # restore cursor & selection before returning
        scene.cursor.location = prev_cursor
        # restore active & selection
        for o, sel in prev_sel_states.items():
            o.select_set(sel)
        bpy.context.view_layer.objects.active = prev_active
        if prev_mode:
            try:
                bpy.ops.object.mode_set(mode=prev_mode)
            except RuntimeError:
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
        except RuntimeError:
            pass

    return


def closest_distance_between_objects(obj_a, obj_b, apply_modifiers=True):

    if obj_a.type != 'MESH' or obj_b.type != 'MESH':
        raise TypeError(_("Both objects must be mesh objects"))

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
            _unused, _unused2, _unused3, dist = hit
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
            # select_set() is a no-op on a hidden object -- it never reaches
            # context.selected_objects, so bpy.ops.object.delete() silently
            # skips it. Unhide right before deleting to avoid that.
            if obj.hide_get():
                obj.hide_set(False)
            obj.select_set(True)
        bpy.ops.object.delete()
    except RuntimeError:
        # Fallback for background, headless, or modal contexts where bpy.ops is
        # unavailable. Slower for large lists due to per-object depsgraph updates.
        for obj in objects:
            for col in obj.users_collection:
                col.objects.unlink(obj)
            bpy.data.objects.remove(obj)


def remove_generated_objects_at(map_obj, radius=0.2):
    """Remove earlier TrailPrint3D objects sitting at map_obj's own XY spot,
    so regenerating the same area from a generator replaces the old map
    instead of stacking a new one on top of it.

    Generated pieces (map, roads, elements, trails, text, plate) all get
    their origin on their map's center, so "same spot" is the same 0.2-unit
    origin test runGeneration's _cleanup_build_area uses. Unlike that one,
    only addon-made objects are touched (objType/"Object type" metadata, or
    a "_Trail" curve/mesh, which carries no metadata until it's merged) --
    never a camera, light or the user's own mesh.
    """
    target = Vector((map_obj.location.x, map_obj.location.y))
    stale = []
    for obj in bpy.data.objects:
        if obj is map_obj:
            continue
        generated = (
            obj.get("objType") is not None
            or obj.get("Object type") is not None
            or ("_Trail" in obj.name and obj.type in {"CURVE", "MESH"})
        )
        if generated and (Vector((obj.location.x, obj.location.y)) - target).length <= radius:
            stale.append(obj)
    if stale:
        print(f"[TP3D] removing {len(stale)} earlier object(s) at the new map's position")
    for obj in stale:
        bpy.data.objects.remove(obj, do_unlink=True)


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
    if not bpy.app.background:
        bpy.context.window_manager.popup_menu(draw, title=ti, icon=ic)


def importSVGtoMerge(Mapobject, svg_path=None):
    """Import the SVG at *svg_path* (default: the scene's SVG File) as one
    merged mesh, centred on the 3D cursor."""
    from .mesh_ops import (
        merge_objects,  # deferred to avoid circular import at load time
    )

    svg_path = svg_path or bpy.context.scene.tp3d.svg_path
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
        # Straight runs and nearly straight curve segments (e.g. a "q" with
        # almost collinear points) fill into zero-area sliver triangles. The
        # EXACT boolean that clips a bottom-side SVG to the map
        # (intersectWithTile) can drop the whole shape such a triangle
        # belongs to -- one part of a logo vanished. A tiny-angle limited
        # dissolve merges the flat faces and drops the collinear points
        # behind those slivers without visibly changing the outline.
        bpy.ops.mesh.dissolve_limited(angle_limit=math.radians(0.1))
        bpy.ops.mesh.dissolve_degenerate(threshold=0.0001)

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


# ── One collection per generation ────────────────────────────────────────
# Everything a generator page creates (the map with its trail and elements,
# all pieces of a puzzle, every tile of a Multi Tile batch...) is moved into
# a collection of its own once it's done, tagged with GENERATION_COLLECTION_PROP
# (the generator's kind). The Post-processing page lists these to switch
# between and to delete.

GENERATION_COLLECTION_PROP = "tp3d_generation"
# The generator (bpy.ops.tp3d.<op>) a generation came from, and the settings
# it was made with (its page's history entry, JSON) -- Post-processing's way
# back to that generator restores them (picker_server.set_pending_restore).
GENERATOR_PROP = "tp3d_generator"
GENERATOR_STATE_PROP = "tp3d_generator_state"
GENERATOR_OF_KIND = {
    "map": "map_generator",
    "multitile": "map_picker",
    "puzzle": "puzzle_configurator",
    "sliding_puzzle": "sliding_puzzle_configurator",
    "medal_holder": "medal_holder_generator",
    "backplate": "backplate_generator",
}
# Helper collections that keep their objects (debug output).
_KEEP_COLLECTIONS = {"TP3D_Debug"}


def _scene_object_keys(scene):
    return {(o.as_pointer(), o.name) for o in scene.objects}


def collect_new_objects(scene, before, kind, label):
    """Move every object added to *scene* since *before* (a
    _scene_object_keys() set) into a new collection named *label* -- Blender
    appends .001... when taken -- under the scene collection, tagged
    GENERATION_COLLECTION_PROP = *kind*. Returns the collection, or None when
    nothing new is there."""
    new = [o for o in scene.objects if (o.as_pointer(), o.name) not in before]
    new = [o for o in new if not any(c.name in _KEEP_COLLECTIONS for c in o.users_collection)]
    if not new:
        return None
    collection = bpy.data.collections.new(label)
    scene.collection.children.link(collection)
    collection[GENERATION_COLLECTION_PROP] = kind
    for obj in new:
        move_to_collection(obj, collection)
    remove_empty_generation_collections(scene)
    return collection


def move_to_collection(obj, collection):
    """Make *collection* the only collection *obj* is in."""
    if collection not in obj.users_collection:
        collection.objects.link(obj)
    for other in list(obj.users_collection):
        if other is not collection:
            other.objects.unlink(obj)


def remove_empty_generation_collections(scene):
    """Drop generation collections left empty (their maps were replaced or
    deleted) -- never a collection the user made."""
    for collection in list(scene.collection.children_recursive):
        if collection.get(GENERATION_COLLECTION_PROP) and not collection.all_objects and not collection.children:
            bpy.data.collections.remove(collection)


def remove_collection_with_contents(collection):
    """Remove *collection*, its child collections and every object in them,
    plus the meshes, curves, materials and images only they used."""
    datas, materials = [], []
    for obj in list(collection.all_objects):
        data = obj.data
        if data is not None:
            datas.append(data)
            materials.extend(m for m in getattr(data, "materials", ()) if m is not None)
        bpy.data.objects.remove(obj, do_unlink=True)
    for child in list(collection.children_recursive):
        bpy.data.collections.remove(child)
    bpy.data.collections.remove(collection)
    # Leftover unused meshes/curves (tools' temporary ones) that still point
    # at these materials would keep them -- and their paint images -- alive.
    material_names = {m.name for m in materials}
    for block in list(bpy.data.meshes) + list(bpy.data.curves):
        if block.users == 0 and any(m is not None and m.name in material_names for m in block.materials):
            datas.append(block)
    images = []
    for data in datas:
        try:
            if data.users:
                continue
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data, bpy.types.Curve):
                bpy.data.curves.remove(data)
        except ReferenceError:
            continue
    for mat in {m.name: m for m in materials}.values():
        try:
            if mat.users:
                continue
            if mat.use_nodes and mat.node_tree is not None:
                images.extend(n.image for n in mat.node_tree.nodes if n.type == "TEX_IMAGE" and n.image is not None)
            bpy.data.materials.remove(mat)
        except ReferenceError:
            continue
    for image in {i.name: i for i in images}.values():
        try:
            if not image.users:
                bpy.data.images.remove(image)
        except ReferenceError:
            continue


def clear_previous_generations(scene, keep=None):
    """Take every earlier generation out of *scene* before a new one (or
    before opening another map from "My Maps"). Nothing is saved here --
    what the user saved (the Post-processing page's Save) is in "My Maps",
    the rest is gone. *keep* (a collection) stays, and so does everything
    that isn't a generation's collection (maps made before the library, the
    user's own objects). Returns how many were cleared."""
    cleared = 0
    for collection in list(scene.collection.children_recursive):
        try:
            if not collection.get(GENERATION_COLLECTION_PROP) or collection == keep:
                continue
        except ReferenceError:  # removed with a parent collection just now
            continue
        remove_collection_with_contents(collection)
        cleared += 1
    return cleared


def _json_dumps(value):
    import json

    return json.dumps(value)


class generation_collection:
    """``with generation_collection(context, kind, label): run_generation()``
    -- afterwards, everything the generation added to the scene is in a
    collection of its own (collect_new_objects). Only on success: a failed
    run leaves its objects where they are.

    *clear_previous*: first take the earlier generations out of the scene
    (clear_previous_generations -- the saved ones stay in "My Maps"). Off
    for runs that build on what's there (Multi Tile Extend). Nothing is
    saved to "My Maps" here: that's the Post-processing page's Save."""

    def __init__(self, context, kind, label, clear_previous=True, history_id=None):
        self.scene = context.scene
        self.kind = kind
        self.label = label
        self.clear_previous = clear_previous
        # The page's history entry for this run (its /confirm history_id).
        self.history_id = history_id
        self.before = set()
        self.collection = None

    def __enter__(self):
        if self.clear_previous:
            if bpy.context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            clear_previous_generations(self.scene)
        self.before = _scene_object_keys(self.scene)
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.collection = collect_new_objects(self.scene, self.before, self.kind, self.label)
            if self.collection is not None:
                self._remember_settings()
                from ..library import default_name

                # A telling name (the user's, the GPX track's, the place...).
                try:
                    self.collection.name = default_name(self.scene, self.collection, self.kind)
                except (OSError, RuntimeError, ValueError, KeyError) as e:
                    print(f"[TP3D] no default name for {self.collection.name}: {e}")
        return False

    def _remember_settings(self):
        """Keep which generator made it, and its settings, on the collection
        -- saved into the map library with it."""
        op_id = GENERATOR_OF_KIND.get(self.kind)
        if op_id:
            self.collection[GENERATOR_PROP] = op_id
        if not self.history_id:
            return
        from .. import picker_server

        entry = picker_server.history_entry(self.history_id)
        if entry is not None:
            self.collection[GENERATOR_STATE_PROP] = _json_dumps(entry)
