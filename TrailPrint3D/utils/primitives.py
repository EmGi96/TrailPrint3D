import bpy  # type: ignore
import bmesh  # type: ignore
import math
from mathutils import Vector  # type: ignore

from .text_objects import _apply_plate_bevel


def _setup_material(name, color):
    if name not in bpy.data.materials:
        mat = bpy.data.materials.new(name=name)
    else:
        mat = bpy.data.materials[name]

    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    bsdf = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if not bsdf:
        bsdf = nodes.new(type="ShaderNodeBsdfPrincipled")
        bsdf.location = (0, 0)

    output = next((n for n in nodes if n.type == 'OUTPUT_MATERIAL'), None)
    if not output:
        output = nodes.new(type="ShaderNodeOutputMaterial")
        output.location = (300, 0)

    if not bsdf.outputs["BSDF"].is_linked:
        links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    bsdf.inputs["Base Color"].default_value = color

def setupColors():
    _setup_material("BASE",      (0.05, 0.7,  0.05, 1.0))
    _setup_material("FOREST",    (0.05, 0.25, 0.05, 1.0))
    _setup_material("MOUNTAIN",  (0.5,  0.5,  0.5,  1.0))
    _setup_material("WATER",     (0.0,  0.0,  0.8,  1.0))
    _setup_material("TRAIL",     (1.0,  0.0,  0.0,  1.0))
    _setup_material("YELLOW",    (1.0,  1.0,  0.0,  1.0))
    _setup_material("CITY",      (0.7,  0.7,  0.1,  1.0))
    _setup_material("GREENSPACE",(0.16,  1.0,  0.16,  1.0))
    _setup_material("GLACIER",   (0.8,  0.9,  0.8,  1.0))
    _setup_material("BLACK",     (0.0,  0.0,  0.0,  1.0))
    _setup_material("WHITE",     (1.0,  1.0,  1.0,  1.0))
    _setup_material("BUILDINGS", (0.4,  0.4,  0.4,  1.0))
    _setup_material("FARMLAND",  (0.3,  0.5,  0.1,  1.0))

def create_curve_from_coordinates(coordinates):
    """
    Create a curve in Blender based on a list of (x, y, z) coordinates.
    """

    pathThickness = bpy.context.scene.tp3d.pathThickness
    name = bpy.context.scene.tp3d.modelname

    # Create a new curve object
    curve_data = bpy.data.curves.new('GPX_Curve', type='CURVE')
    curve_data.dimensions = '3D'
    polyline = curve_data.splines.new('POLY')
    polyline.points.add(count=len(coordinates) - 1)

    # Populate the curve with points
    for i, coord in enumerate(coordinates):
        polyline.points[i].co = (coord[0], coord[1], coord[2], 1)  # (x, y, z, w)

    # Create an object with this curve
    curve_object = bpy.data.objects.new('GPX_Curve_Object', curve_data)
    bpy.context.collection.objects.link(curve_object)
    curve_object.data.bevel_depth = pathThickness/2  # Set the thickness of the curve
    curve_object.data.bevel_resolution = 4  # Set the resolution for smoothness

    mod = curve_object.modifiers.new(name="Remesh",type="REMESH")
    mod.mode = "VOXEL"
    mod.voxel_size = 0.05 * pathThickness * 10/2
    mod.adaptivity = 0.0
    curve_object.data.use_fill_caps = True

    curve_object.data.name = name + "_Trail"
    curve_object.name = name + "_Trail"


    curve_object.select_set(True)


    bpy.context.view_layer.objects.active = curve_object

    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.curve.select_all(action='SELECT')
    bpy.ops.object.mode_set(mode='OBJECT')

    return curve_object


def simplify_curve(points_with_extra, min_distance=0.1000):
    """
    Removes points that are too close to any previously accepted point.
    Keeps the full (x, y, z, time) format.
    """

    if not points_with_extra:
        return []

    simplified = [points_with_extra[0]]
    last_xyz = Vector(points_with_extra[0][:3])
    skipped = 0

    for pt in points_with_extra[1:]:
        current_xyz = Vector(pt[:3])
        if (current_xyz - last_xyz).length >= min_distance:
            simplified.append(pt)
            last_xyz = current_xyz
        else:
            skipped += 1
            pass

    print(f"Smooth curve: Removed {skipped} vertices")
    return simplified

def create_hexagon(size, num_subdivisions = 1, name = "Hexagon"):
    """Creates a hexagon at (0,0,0), subdivides it, and rotates it by 90 degrees."""
    verts = []
    faces = []

    for i in range(6):
        angle = math.radians(60 * i)
        x = size * math.cos(angle)
        y = size * math.sin(angle)
        verts.append((x, y, 0))
    verts.append((0, 0, 0))  # Center vertex
    faces = [[i, (i + 1) % 6, 6] for i in range(6)]
    mesh = bpy.data.meshes.new("Hexagon")
    obj = bpy.data.objects.new("Hexagon", mesh)
    bpy.context.collection.objects.link(obj)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    for _ in range(num_subdivisions):
        bpy.ops.mesh.subdivide(number_cuts=1)  # 1 cut per loop for even refinement
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.name = name
    obj.data.name = name
    return obj

def create_rectangle(width, height, num_subdivisions = 1, name="Rectangle"):
    """Creates a rectangle and adds loop cuts to ensure cells are as square as possible."""

    cuts = 1 + 2**(num_subdivisions+1)

    # 1. Create the basic plane mesh
    verts = [
        (-width / 2, -height / 2, 0),
        (width / 2, -height / 2, 0),
        (width / 2, height / 2, 0),
        (-width / 2, height / 2, 0)
    ]
    faces = [[0, 1, 2, 3]]

    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    # 2. Calculate cuts needed to keep cells square
    target_cell_size = width/cuts
    cuts_y = max(0, int(round(height / target_cell_size)) - 1)

    # 3. Apply the cuts
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')

    bm = bmesh.from_edit_mesh(mesh)

    # Subdivide horizontal edges (cuts along Width)
    horizontal_edges = [e for e in bm.edges if e.verts[0].co.y == e.verts[1].co.y]
    if num_subdivisions > 0:
        bmesh.ops.subdivide_edges(bm, edges=horizontal_edges, cuts=cuts, use_grid_fill=True)

    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    vertical_edges = [e for e in bm.edges if abs(e.verts[0].co.x - e.verts[1].co.x) < 0.001]
    if cuts_y > 0:
        bmesh.ops.subdivide_edges(bm, edges=vertical_edges, cuts=cuts_y, use_grid_fill=True)

    bmesh.update_edit_mesh(mesh)
    bpy.ops.object.mode_set(mode='OBJECT')

    return obj

def create_heart(size, num_subdivisions = 1, name = "Heart"):
    """Creates a full heart-shaped mesh in Blender and applies a Remesh modifier."""
    verts = []
    faces = []

    # Heart parametric equations (full heart)
    steps = 200
    for i in range(steps + 1):
        t = i / steps * (2 * math.pi)
        x = size * (16 * math.sin(t) ** 3) / 16
        y = size * (13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)) / 16
        verts.append((x, y, 0))

    # Add the center vertex for triangulation
    verts.append((0, -size / 2, 0))
    center_index = len(verts) - 1

    # Create faces
    for i in range(steps):
        faces.append([i, (i + 1) % steps, center_index])

    # Create the mesh
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)

    # Set the mesh data
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    bpy.context.view_layer.objects.active = obj

    # Enter Edit mode
    bpy.ops.object.mode_set(mode='EDIT')

    # Extrude the surface
    bpy.ops.mesh.extrude_region_move(TRANSFORM_OT_translate={
        'value': (0, 0, 2)
    })

    bpy.ops.object.mode_set(mode='OBJECT')




    # Add Remesh modifier
    remesh = obj.modifiers.new(name="Remesh", type='REMESH')
    remesh.mode = 'SHARP'
    remesh.octree_depth = num_subdivisions + 1
    remesh.scale = 0.9
    remesh.sharpness = 1.0


    if "Remesh" in obj.modifiers:
        bpy.ops.object.modifier_apply(modifier="Remesh")

    bpy.ops.object.mode_set(mode='EDIT')


    # Get the mesh data
    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)

    # Find the top coplanar faces
    bm.faces.ensure_lookup_table()
    top_faces = [f for f in bm.faces if f.normal == Vector((0, 0, 1))]

    top_normals = {tuple(f.normal) for f in top_faces}

    # Delete faces that are not coplanar with the top surfaces
    faces_to_delete = [f for f in bm.faces if tuple(f.normal) not in top_normals]

    bmesh.ops.delete(bm, geom=faces_to_delete, context='FACES')

    bpy.ops.object.mode_set(mode='OBJECT')

    # Update the mesh
    bm.to_mesh(mesh)
    mesh.update()
    bm.free()


    # Back to Object mode
    bpy.ops.object.mode_set(mode='OBJECT')


    return obj

def create_circle(radius, num_subdivisions = 1, name = "Circle", num_segments=64):

    # Ensure we are in Object Mode
    try:
        bpy.ops.object.mode_set(mode='OBJECT')
    except:
        pass

    # Create a new mesh and object
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)


    # Link object to the scene collection
    bpy.context.collection.objects.link(obj)

    # Generate circle vertices
    verts = []
    faces = []

    for i in range(num_segments):
        angle = math.radians(360 * i / num_segments)
        x = radius * math.cos(angle)
        y = radius * math.sin(angle)
        verts.append((x, y, 0))

    # Create edges between consecutive points
    edges = [(i, (i + 1) % num_segments) for i in range(num_segments)]

    # Create the mesh from data
    mesh.from_pydata(verts, edges, [])  # No center vertex, no faces yet
    mesh.update()

    # Make the object active and switch to Edit Mode
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')

    # Select all vertices and fill the circle
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.fill_grid()

    for _ in range(num_subdivisions-3):
        bpy.ops.mesh.subdivide(number_cuts=1)  # 1 cut per loop for even refinement

    # Switch back to Object Mode
    bpy.ops.object.mode_set(mode='OBJECT')

    return obj

def create_ellipse(radius, num_subdivisions = 1, name = "Ellipse", aspect_ratio = 0.75, num_segments=64, ):
    # Ensure we are in Object Mode
    try:
        bpy.ops.object.mode_set(mode='OBJECT')
    except:
        pass

    # Create a new mesh and object
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)


    # Link object to the scene collection
    bpy.context.collection.objects.link(obj)

    # Generate circle vertices
    verts = []
    faces = []

    for i in range(num_segments):
        angle = math.radians(360 * i / num_segments)
        x = radius * math.cos(angle)
        y = radius * math.sin(angle)
        verts.append((x, y, 0))

    # Create edges between consecutive points
    edges = [(i, (i + 1) % num_segments) for i in range(num_segments)]

    # Create the mesh from data
    mesh.from_pydata(verts, edges, [])  # No center vertex, no faces yet
    mesh.update()

    # Make the object active and switch to Edit Mode
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')

    # Select all vertices and fill the circle
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.fill_grid()

    for _ in range(num_subdivisions-3):
        bpy.ops.mesh.subdivide(number_cuts=1)  # 1 cut per loop for even refinement

    # Switch back to Object Mode
    bpy.ops.object.mode_set(mode='OBJECT')

    obj.scale.y *= aspect_ratio

    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)


    return obj

def create_octagon(size, num_subdivisions = 1, name = "Octagon"):

    """Creates a hexagon at (0,0,0), subdivides it, and rotates it by 90 degrees."""
    verts = []
    faces = []
    for i in range(8):
        angle = math.radians(45 * i + 22.5)
        x = size * math.cos(angle)
        y = size * math.sin(angle)
        verts.append((x, y, 0))
    verts.append((0, 0, 0))  # Center vertex
    faces = [[i, (i + 1) % 8, 8] for i in range(8)]
    mesh = bpy.data.meshes.new("Hexagon")
    obj = bpy.data.objects.new("Hexagon", mesh)
    bpy.context.collection.objects.link(obj)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    for _ in range(num_subdivisions):
        bpy.ops.mesh.subdivide(number_cuts=1)  # 1 cut per loop for even refinement
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.name = name
    obj.data.name = name
    return obj

def col_create_line_mesh(name, coords):
    mesh = bpy.data.meshes.new(name)
    tobj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(tobj)

    bm = bmesh.new()
    verts = [bm.verts.new(c) for c in coords]
    for i in range(len(verts) - 1):
        bm.edges.new((verts[i], verts[i + 1]))
    bm.to_mesh(mesh)
    bm.free()
    return tobj


def col_create_face_mesh(name, coords):

    if len(coords) < 3:
        return  # Need at least 3 points for a face


    mesh = bpy.data.meshes.new(name)
    tobj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(tobj)

    bm = bmesh.new()
    verts = [bm.verts.new(c) for c in coords]
    try:
        bm.faces.new(verts)
    except ValueError as e:
        print(e)  # face might already exist or be invalid
    bm.to_mesh(mesh)
    bm.free()
    return tobj

def create_ribbon_mesh(name, pts, half_width):
    """Build a flat ribbon mesh along a polyline.

    pts        -- list/sequence of Vector (x, y, z)
    half_width -- half the desired ribbon width in Blender units

    Returns a newly linked bpy.types.Object, or None if pts < 2.
    """
    if len(pts) < 2:
        return None

    # Compute per-segment directions
    seg_dirs = []
    for a, b in zip(pts[:-1], pts[1:]):
        d = b - a
        seg_dirs.append(d.normalized() if d.length != 0 else Vector((0.0, 0.0, 0.0)))

    # Compute smoothed perpendicular at each node
    perp_at = []
    npts = len(pts)
    for i_pt in range(npts):
        if i_pt == 0:
            dir_vec = seg_dirs[0]
        elif i_pt == npts - 1:
            dir_vec = seg_dirs[-1]
        else:
            s = seg_dirs[i_pt - 1] + seg_dirs[i_pt]
            dir_vec = s.normalized() if s.length != 0 else seg_dirs[i_pt - 1]
        perp = Vector((-dir_vec.y, dir_vec.x, 0.0))
        perp_at.append(perp.normalized() if perp.length != 0 else perp)

    # Build left/right vertex pairs and quad faces
    verts = []
    faces = []
    for i_pt, (p, perp) in enumerate(zip(pts, perp_at)):
        left  = p + perp * half_width
        right = p - perp * half_width
        verts.append((left.x,  left.y,  left.z))
        verts.append((right.x, right.y, right.z))

    for j in range(npts - 1):
        a_left  = j * 2
        a_right = j * 2 + 1
        b_left  = j * 2 + 2
        b_right = j * 2 + 3
        faces.append((a_left, b_left, b_right, a_right))

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update(calc_edges=True)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj

def col_create_line_curve(name, coords, close=False, collection=None, bevel_depth=0.0):
    """
    Create a Curve object with a POLY spline from coords.
    coords: iterable of (x,y) or (x,y,z)
    close: make spline cyclic
    collection: bpy.types.Collection (defaults to context.collection)
    bevel_depth: >0 will give the curve thickness
    """
    if not coords:
        raise ValueError("coords is empty")

    # normalize coords to 3-tuples
    pts = []
    for c in coords:
        if len(c) == 2:
            pts.append((c[0], c[1], 0.0))
        else:
            pts.append((c[0], c[1], c[2]))

    curve_data = bpy.data.curves.new(name + "_curve", type='CURVE')
    curve_data.dimensions = '3D'
    curve_data.resolution_u = 1

    spline = curve_data.splines.new(type='POLY')
    spline.points.add(len(pts) - 1)  # one point exists by default
    for i, (x, y, z) in enumerate(pts):
        spline.points[i].co = (x, y, z, 1.0)

    spline.use_cyclic_u = bool(close)

    if bevel_depth and bevel_depth > 0.0:
        curve_data.bevel_depth = float(bevel_depth)
        curve_data.fill_mode = 'FULL'

    obj = bpy.data.objects.new(name, curve_data)
    target_col = collection or bpy.context.collection
    target_col.objects.link(obj)

    return obj

def curve_to_mesh_object(curve_obj, name=None, apply_modifiers=True):
    """
    Create and return a new Mesh object built from `curve_obj` evaluation.
    - curve_obj: bpy.types.Object of type 'CURVE'
    - name: optional name for new object (mesh)
    - apply_modifiers: if True, evaluate modifiers and use new_from_object (recommended)
    """
    if curve_obj.type != 'CURVE':
        raise ValueError("curve_obj must be a Curve object")

    mesh_name = (name if name else curve_obj.name + "_mesh")
    coll = bpy.context.collection
    depsgraph = bpy.context.evaluated_depsgraph_get()

    if apply_modifiers:
        # Create a real Mesh datablock from the evaluated object (safe to use with objects.new)
        eval_obj = curve_obj.evaluated_get(depsgraph)
        mesh = bpy.data.meshes.new_from_object(eval_obj,
                                               preserve_all_data_layers=True,
                                               depsgraph=depsgraph)
        new_obj = bpy.data.objects.new(mesh_name, mesh)
        new_obj.matrix_world = curve_obj.matrix_world.copy()
        coll.objects.link(new_obj)
        return new_obj

    else:
        # Create a temporary evaluated mesh, copy it to a real datablock, then clear the temp
        eval_obj = curve_obj.evaluated_get(depsgraph)
        temp_mesh = eval_obj.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
        # copy to real datablock
        real_mesh = temp_mesh.copy()
        real_mesh.name = mesh_name
        new_obj = bpy.data.objects.new(mesh_name, real_mesh)
        new_obj.matrix_world = curve_obj.matrix_world.copy()
        coll.objects.link(new_obj)
        # free the temporary evaluated mesh
        eval_obj.to_mesh_clear()
        return new_obj
