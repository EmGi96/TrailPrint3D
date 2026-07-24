import math
import os
import platform

import bmesh  # type: ignore
import bpy  # type: ignore
from mathutils import Vector  # type: ignore

from .mesh_ops import recalculateNormals

try:
    from ..premium.utils_pe import textIcon
except ImportError:
    def textIcon(*_):
        return None


def update_text_object(obj_name, new_text):
    """Updates the text of a Blender text object."""
    text_obj = bpy.data.objects.get(obj_name)
    if text_obj and text_obj.type == 'FONT':
        text_obj.data.body = new_text


def create_text(name, text, position, scale_multiplier, rotation=(0, 0, 0), extrude=20, font_path=None):
    txt_data = bpy.data.curves.new(name=name, type='FONT')
    txt_obj = bpy.data.objects.new(name=name, object_data=txt_data)
    bpy.context.collection.objects.link(txt_obj)

    textFont = font_path or bpy.context.scene.tp3d.textFont

    if textFont == "":
        if platform.system() == "Windows":
            textFont = "C:/WINDOWS/FONTS/ariblk.ttf"
        elif platform.system() == "Darwin":
            textFont = "/System/Library/Fonts/Supplemental/Arial Black.ttf"
        else:
            textFont = ""

    txt_data.body = text
    txt_data.extrude = extrude
    #txt_data.font = bpy.data.fonts.load("C:/Windows/Fonts/ariblk.ttf")  # Adjust path if needed
    txt_data.font = bpy.data.fonts.load(textFont)
    txt_data.align_x = 'CENTER'
    txt_data.align_y = "CENTER"

    txt_obj.scale = (scale_multiplier, scale_multiplier, 1)
    txt_obj.location = position
    txt_obj.rotation_euler = rotation

    txt_obj.location.z -= 1

    return txt_obj


def appendTextIcon(textobject, icon, scaleM= 1):

    addon_dir = os.path.dirname(os.path.dirname(__file__))
    filepath = os.path.join(addon_dir, "assets", "other.blend")

    object_name = icon
    unique_name = f"{object_name}_{textobject.name}"

    # Remove existing objects with the same unique name (from a previous run)
    # and the base name, to ensure a clean append
    for name_to_remove in [unique_name, object_name]:
        old_obj = bpy.data.objects.get(name_to_remove)
        if old_obj:
            bpy.data.objects.remove(old_obj, do_unlink=True)

    # Append the object from the blend file
    with bpy.data.libraries.load(filepath, link=False) as (data_from, data_to):
        if object_name in data_from.objects:
            data_to.objects.append(object_name)
        else:
            print(f"Object '{object_name}' not found in file.")
            return None

    # Get the appended object and rename it to avoid conflicts when
    # multiple slots use the same icon asset
    obj = bpy.data.objects.get(object_name)
    if not obj:
        return None
    obj.name = unique_name

    # Link object to the scene collection
    bpy.context.scene.collection.objects.link(obj)

    # Move object to cursor
    obj.location = textobject.location.copy()
    obj.rotation_euler = textobject.rotation_euler.copy()
    obj.scale.x = scaleM / 5
    obj.scale.y = scaleM / 5


    depsgraph = bpy.context.evaluated_depsgraph_get()
    obj_eval = obj.evaluated_get(depsgraph)
    bbox = [Vector(corner) for corner in obj_eval.bound_box]
    icon_xSize = max(v.x for v in bbox) - min(v.x for v in bbox)

    icon_xSize = icon_xSize * obj.scale.x

    depsgraph = bpy.context.evaluated_depsgraph_get()
    obj_eval = textobject.evaluated_get(depsgraph)
    bbox = [Vector(corner) for corner in obj_eval.bound_box]
    text_xSize = max(v.x for v in bbox) - min(v.x for v in bbox)

    text_xSize = text_xSize / obj.scale.x

    obj.location -= obj.matrix_world.to_3x3() @ Vector((text_xSize/2 + 0.5, 0, 0))
    textobject.location += textobject.matrix_world.to_3x3() @ Vector((icon_xSize/2 + 0.5, 0, 0))


    # Make the object active and selected
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    textobject.select_set(True)
    bpy.context.view_layer.objects.active = obj

    return obj


def replaceShapeText(textfield, textobj):

    total_elevation = bpy.context.scene.tp3d.total_elevation
    total_length = bpy.context.scene.tp3d.total_length
    time_str = bpy.context.scene.tp3d.sTime_str
    average_speed = bpy.context.scene.tp3d.average_speed
    trail_date =  bpy.context.scene.tp3d.trail_date

    if "{length}" in textfield:
        textfield = textfield.replace("{length}", f"{total_length:.2f}km")
        update_text_object(textobj.name, textfield)
    elif "{elevation}" in textfield:
        textfield = textfield.replace("{elevation}", f"{total_elevation:.2f}m")
        update_text_object(textobj.name, textfield)
    elif "{duration}" in textfield:
        textfield = textfield.replace("{duration}", f"{time_str}")
        update_text_object(textobj.name, textfield)
    elif "{date}" in textfield:
        textfield = textfield.replace("{date}", trail_date)
        update_text_object(textobj.name, textfield)
    elif "{speed}" in textfield:
        textfield = textfield.replace("{speed}", f"{average_speed:.2f} km/h")
        update_text_object(textobj.name, textfield)
    elif "{name}" in textfield:
        nm = bpy.context.scene.tp3d.modelname
        print(f"Name: {nm}")
        textfield = textfield.replace("{name}", f"{nm}")
        update_text_object(textobj.name, textfield)
    else:
        update_text_object(textobj.name, textfield)

    return textfield


def convert_text_to_mesh(text_obj_name, mesh_obj_name, merge = True):
    # Get the text and mesh objects
    text_obj = bpy.data.objects.get(text_obj_name)
    mesh_obj = bpy.data.objects.get(mesh_obj_name)

    if not text_obj or not mesh_obj:
        print("One or both objects not found")
        return

    # Ensure the text object is selected and active
    bpy.ops.object.select_all(action='DESELECT')
    text_obj.select_set(True)
    bpy.context.view_layer.objects.active = text_obj

    # Convert text to mesh
    bpy.ops.object.convert(target='MESH')

    # Enter edit mode
    bpy.ops.object.mode_set(mode='EDIT')

    # Enable auto-merge vertices
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.remove_doubles(threshold=0.002)
    #bpy.context.tool_settings.use_mesh_automerge = True

    # Switch back to object mode to move it
    bpy.ops.object.mode_set(mode='OBJECT')

    recalculateNormals(text_obj)

    # Move the text object up by 1
    text_obj.location.z += 1

    # Move the text object down by 1 (merging overlapping vertices)
    text_obj.location.z -= 1

    # Disable auto-merge vertices
    bpy.context.tool_settings.use_mesh_automerge = False

    if merge == True:
        # Add boolean modifier
        bool_mod = text_obj.modifiers.new(name="Boolean", type='BOOLEAN')
        bool_mod.object = mesh_obj
        bool_mod.operation = 'INTERSECT'
        bool_mod.solver = 'MANIFOLD'


        # Apply the boolean modifier
        bpy.ops.object.select_all(action='DESELECT')
        text_obj.select_set(True)
        bpy.context.view_layer.objects.active = text_obj
        bpy.ops.object.modifier_apply(modifier=bool_mod.name)

        # Move the text object up by 1
        text_obj.location.z += 0.4


def _apply_plate_bevel(obj, bevel_amount, thickness):
    """Bevel the top and bottom perimeter edges of a plate object."""
    if bevel_amount <= 0:
        return
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bevel_edges = []
    for edge in bm.edges:
        v0_z = edge.verts[0].co.z
        v1_z = edge.verts[1].co.z
        same_top = abs(v0_z) < 0.01 and abs(v1_z) < 0.01
        same_bottom = abs(v0_z + thickness) < 0.01 and abs(v1_z + thickness) < 0.01
        if same_top or same_bottom:
            for face in edge.link_faces:
                face_z_vals = [v.co.z for v in face.verts]
                if min(face_z_vals) < -0.01 and max(face_z_vals) > -0.01:
                    bevel_edges.append(edge)
                    break
    if bevel_edges:
        bmesh.ops.bevel(bm, geom=bevel_edges, offset=bevel_amount, segments=1, affect='EDGES', profile = 0.5)
        bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def _add_box(half_x, half_y, center_y, z_top, z_bottom, name):
    """Axis-aligned box in local space: x in [-half_x, half_x], y in
    [center_y - half_y, center_y + half_y], z in [z_bottom, z_top]."""
    near_y, far_y = center_y - half_y, center_y + half_y
    verts = [
        (-half_x, near_y, z_top), (half_x, near_y, z_top),
        (half_x, far_y, z_top), (-half_x, far_y, z_top),
        (-half_x, near_y, z_bottom), (half_x, near_y, z_bottom),
        (half_x, far_y, z_bottom), (-half_x, far_y, z_bottom),
    ]
    faces = [
        (0, 1, 2, 3),
        (7, 6, 5, 4),
        (0, 4, 5, 1),
        (1, 5, 6, 2),
        (2, 6, 7, 3),
        (3, 7, 4, 0),
    ]
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    return obj


def _add_dome_ring(radius, hole_radius, near_y, far_y, hole_center_y, z_top, z_bottom, name, segments=32):
    """A tombstone-shaped ring: the outline from _add_dome with a round
    hole of hole_radius built directly into the mesh topology (bridging
    the outer and an inner loop of matching vertex count index-for-index,
    the same fix already used for the plain circular ROUND hole earlier
    in this project's history) instead of cut with a boolean -- booleans
    against this outline (a large flat-bottom/semicircle-top face) have
    proven unreliable with both of Blender's solvers: EXACT leaves dozens
    of non-manifold edges, and MANIFOLD silently no-ops on a session's
    first attempt at this specific shape, before working fine afterward.

    near_y and hole_center_y are independent, not a shared center: the
    outer shape's near (plate-facing) edge is pulled down as needed to
    keep it embedded in the plate (see _handle_near_y), but the hole must
    stay fixed just above the plate's own topmost point regardless of
    that -- dragging the hole down along with the outer shape would push
    it back into the plate's own material near the shape's center and
    block it.

    near_y and far_y set the outer shape's actual vertical span; radius
    only controls the semicircle's curvature (and so the rectangle
    portion's half-width) -- it is *not* forced to be half of (far_y -
    near_y). Keeping those independent matters because near_y can get
    pulled well below where a fixed-radius dome's near edge would
    naturally land (embedding for a beveled or curved plate can require
    more room than the nominal radius provides) -- if the whole shape's
    height were locked to 2*radius, the far (outward) wall above the
    hole would thin out or vanish entirely whenever that happens, instead
    of the rectangle portion simply stretching to cover the gap.
    """
    split_y = far_y - radius

    # Corner points aren't evenly spaced in angle from the arc points (two
    # corners cover a 90 deg jump in one step, versus a few degrees per
    # arc step) -- so the inner loop is built from the *same* per-point
    # angles as the outer loop, not its own uniform spacing, keeping each
    # inner point exactly radially under its outer counterpart. Otherwise
    # the bridged quad spanning that 90 deg outer jump against only a few
    # degrees of inner motion comes out badly skewed, showing up as a
    # dark, inverted-looking face right at the hole's corner-side edge.
    angles = [math.atan2(-radius, -radius), math.atan2(-radius, radius)]
    outer2d = [(-radius, near_y), (radius, near_y)]
    for i in range(segments + 1):
        angle = math.pi * i / segments
        outer2d.append((radius * math.cos(angle), split_y + radius * math.sin(angle)))
        angles.append(angle)
    n = len(outer2d)

    inner2d = [
        (hole_radius * math.cos(a), hole_center_y + hole_radius * math.sin(a))
        for a in angles
    ]

    verts = (
        [(x, y, z_top) for x, y in outer2d] + [(x, y, z_top) for x, y in inner2d]
        + [(x, y, z_bottom) for x, y in outer2d] + [(x, y, z_bottom) for x, y in inner2d]
    )

    def ot(i): return i % n
    def it(i): return n + (i % n)
    def ob(i): return 2 * n + (i % n)
    def ib(i): return 3 * n + (i % n)

    faces = []
    for i in range(n):
        j = i + 1
        faces.append([ot(i), ot(j), it(j), it(i)])
        faces.append([ob(i), ib(i), ib(j), ob(j)])
        faces.append([ot(i), ob(i), ob(j), ot(j)])
        faces.append([it(i), it(j), ib(j), ib(i)])

    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    return obj


def _handle_near_y(max_y, outer_half_width, fallback_margin):
    """Y-coordinate for a handle's near (plate-facing) edge, chosen so it
    never floats above the plate's actual surface at the shape's widest
    corners, regardless of the plate's shape.

    A flat-topped plate (hexagon, octagon) keeps a straight edge safely
    embedded just by tucking it a few mm below the plate's topmost point
    (max_y - fallback_margin) -- their top is already flat there. But a
    circular plate curves away on both sides of that same point, so that
    fixed offset alone leaves a wide shape's corners floating above the
    plate's actual curve instead of touching it (the bug this fixes).
    Solving for where the corners would land on a circle of max_y's
    radius handles that case -- and is always safe to compare against the
    flat assumption even for a plate that isn't actually circular,
    because it only ever pulls the edge further into the plate, never out
    of it. Taking whichever candidate sits lower covers both plate shapes
    without needing to know which one this is.
    """
    flat_candidate = max_y - fallback_margin
    curved_candidate = math.sqrt(max(max_y ** 2 - outer_half_width ** 2, 0.0)) - 0.5
    return min(flat_candidate, curved_candidate)


def _add_medal_handle(plate_obj, thickness, style, bevel_amount):
    """Attach a small ring-shaped handle to the top of a plate object,
    like a medal's hanging loop, so a cord can be threaded through it --
    ROUND is a "tombstone" outline (flat-bottomed rectangle topped with a
    semicircle, see _add_dome_ring) sized 16mm across with a 10mm round
    hole, its flat side facing the plate; FLAT is a straight-sided
    rectangle 3mm larger on every side than a 40mm x 3mm opening (not a
    stretched cylinder, so its corners are flat, not rounded).

    Centered on the plate's own topmost point (found by scanning its
    actual vertices, not assumed from a formula, since different shapes
    reach their highest point differently) -- see _handle_near_y for how
    the near edge is actually placed relative to that point.

    bevel_amount matters because _apply_plate_bevel chamfers the plate's
    flat top/bottom faces inward by that amount -- the plate's *overall*
    topmost vertex (max_y below) still sits at the original, unbeveled
    radius (that edge just moves to mid-thickness instead of vanishing),
    but the flat top face the handle's near edge actually has to touch is
    smaller by bevel_amount. Using unadjusted max_y there would anchor
    the near edge out past where the flat face actually ends, leaving a
    gap -- so the anchor position used for embedding is pulled in by
    bevel_amount, while the hole keeps using the unadjusted max_y (it
    still has to clear that wider mid-thickness edge, not just the top
    face).
    """
    max_y = max(v.co.y for v in plate_obj.data.vertices)
    anchor_y = max_y - bevel_amount
    # The handle gets its own top/bottom edges beveled too, further below
    # -- which insets its near edge by another bevel_amount, same as the
    # plate's own bevel already did once. Anchoring the *pre-bevel*
    # position an extra bevel_amount inside anchor_y means that, after
    # the handle's own bevel pulls it back out by exactly that much, it
    # still lands flush on the plate's true (already-inset) flat face
    # instead of reopening a gap.
    near_edge_anchor_y = anchor_y - bevel_amount

    bpy.ops.object.select_all(action='DESELECT')

    if style == 'FLAT':
        inner_half_x, inner_half_y = 20.0, 1.5
        border = 3.0
        outer_half_x = inner_half_x + border

        # The hole stays anchored just above the plate's topmost point,
        # independent of the outer shape's own (possibly pulled-down)
        # position -- see _add_dome_ring's docstring for why.
        hole_center_y = max_y + inner_half_y
        near_y = _handle_near_y(near_edge_anchor_y, outer_half_x, border)
        # The far (outer, away-from-plate) edge is pinned to the hole's
        # own far edge plus the border, guaranteeing that side of the
        # frame is always exactly `border` thick regardless of how far
        # near_y got pulled in for embedding -- fixing outer_half_y at a
        # constant instead (as if the outer box were always centered on
        # the hole) would let the far wall thin out or vanish entirely
        # whenever near_y sits below the hole's own center, since the
        # box's height wouldn't stretch to cover the extra embedding depth.
        far_y = hole_center_y + inner_half_y + border
        outer_half_y = (far_y - near_y) / 2
        outer_center_y = (far_y + near_y) / 2

        # At the point this function runs, the plate's own solid spans
        # z=0 (top) to z=-thickness (bottom) -- it's only shifted to
        # [0, thickness] by a later step in the calling shape function,
        # well after this handle is already built and joined on. Building
        # the handle to that later range instead would leave it a
        # disconnected volume floating away from the plate's actual
        # z-extent at this point in the pipeline (invisible from directly
        # above, but broken for a real print).
        handle_obj = _add_box(outer_half_x, outer_half_y, outer_center_y, 0.0, -thickness, "Handle")
        # Z-overshoot on the cutter so its own top/bottom faces don't land
        # exactly on the outer shape's -- a coincident-face boolean is
        # unreliable, but an overshooting cutter only ever removes
        # material, so it can't add a bump from the overshoot itself.
        cutter_obj = _add_box(inner_half_x, inner_half_y, hole_center_y, 2.0, -(thickness + 2.0), "HandleCutter")

        bpy.context.view_layer.objects.active = handle_obj
        diff_mod = handle_obj.modifiers.new(name="HandleHoleCut", type='BOOLEAN')
        diff_mod.operation = 'DIFFERENCE'
        diff_mod.object = cutter_obj
        diff_mod.solver = 'MANIFOLD'
        bpy.ops.object.modifier_apply(modifier=diff_mod.name)
        bpy.data.objects.remove(cutter_obj, do_unlink=True)
    else:
        inner_radius, outer_radius = 5.0, 8.0
        border = outer_radius - inner_radius
        hole_center_y = max_y + inner_radius
        near_y = _handle_near_y(near_edge_anchor_y, outer_radius, border)
        # Far edge pinned to the hole's own far edge plus the border --
        # see _add_dome_ring's docstring and the matching comment on the
        # FLAT branch's far_y above for why a fixed offset from near_y
        # alone isn't enough once near_y gets pulled in for embedding.
        far_y = (hole_center_y + inner_radius) + border
        # Built directly, not cut with a boolean -- see _add_dome_ring.
        # z spans 0 (top) to -thickness (bottom) -- see the comment on the
        # FLAT branch's box above for why, at this point in the pipeline.
        handle_obj = _add_dome_ring(outer_radius, inner_radius, near_y, far_y, hole_center_y, 0.0, -thickness, "Handle")

    # Bevel the handle's own top/bottom perimeter edges the same way the
    # plate's are -- _apply_plate_bevel's edge selection also catches the
    # hole's own rim (just another wall-face boundary between z=0 and
    # z=-thickness), so this chamfers the hole opening too, matching the
    # plate's edges instead of leaving the handle sharp-edged against a
    # beveled plate.
    _apply_plate_bevel(handle_obj, bevel_amount, thickness)
    recalculateNormals(handle_obj)

    bpy.ops.object.select_all(action='DESELECT')
    handle_obj.select_set(True)
    plate_obj.select_set(True)
    bpy.context.view_layer.objects.active = plate_obj
    bpy.ops.object.join()

    recalculateNormals(plate_obj)


def HexagonInnerText(MapObject):

    from . import transform_MapObject, projection  # deferred to avoid circular import at load time

    size = bpy.context.scene.tp3d.objSize
    name = bpy.context.scene.tp3d.modelname
    centerx = bpy.context.scene.tp3d.o_centerx
    centery = bpy.context.scene.tp3d.o_centery
    titlefield = bpy.context.scene.tp3d.titlefield
    textfield1 = bpy.context.scene.tp3d.textfield1
    textfield2 = bpy.context.scene.tp3d.textfield2
    textfield3 = bpy.context.scene.tp3d.textfield3
    titleIcon = bpy.context.scene.tp3d.titleIcon
    iconString1 = bpy.context.scene.tp3d.iconText1
    iconString2 = bpy.context.scene.tp3d.iconText2
    iconString3 = bpy.context.scene.tp3d.iconText3
    xTerrainOffset = bpy.context.scene.tp3d.xTerrainOffset
    yTerrainOffset = bpy.context.scene.tp3d.yTerrainOffset
    pathScale = bpy.context.scene.tp3d.pathScale

    textSize = bpy.context.scene.tp3d.textSize
    textSize2 = bpy.context.scene.tp3d.textSizeTitle
    shapeRotation = bpy.context.scene.tp3d.shapeRotation

    if textSize2 == 0:
        textSize2 = textSize


    #dist =  (size/2 - size/2 * (1-pathScale)/2)
    dist =  (size/2 - size/2 * (1-0.8)/2)

    temp_y = math.sin(math.radians(90)) * (dist  * math.cos(math.radians(30)))



    tName = create_text("t_name", "Name", (0, temp_y, 0.1),1)

    for i, (text_name, angle) in enumerate(zip(["t_length", "t_elevation", "t_duration"], [210, 270, 330])):
        angle_centered = angle + 90
        x = math.cos(math.radians(angle)) * (dist * math.cos(math.radians(30)))
        y = math.sin(math.radians(angle)) * (dist * math.cos(math.radians(30)))
        rot_z = math.radians(angle_centered)
        create_text(text_name, text_name.split("_")[1].capitalize(), (x, y, 0.1),1,  (0, 0, rot_z), 100)

    tElevation = bpy.data.objects.get("t_elevation")
    tLength = bpy.data.objects.get("t_length")
    tDuration = bpy.data.objects.get("t_duration")



    transform_MapObject(tName, centerx + xTerrainOffset, centery + yTerrainOffset)
    transform_MapObject(tElevation, centerx + xTerrainOffset, centery + yTerrainOffset)
    transform_MapObject(tLength, centerx + xTerrainOffset, centery + yTerrainOffset)
    transform_MapObject(tDuration,centerx + xTerrainOffset, centery + yTerrainOffset)

    #Scale text sizes to mm values (blender units)
    bpy.context.view_layer.update()
    current_height = tName.dimensions.y
    if current_height == 0:
        current_height = tElevation.dimensions.y
    if current_height == 0:
        current_height = tLength.dimensions.y
    if current_height == 0:
        current_height = 5
    scale_factor_title = textSize2 / current_height
    tName.scale.x *= scale_factor_title
    tName.scale.y *= scale_factor_title

    scale_factor = textSize / current_height
    tElevation.scale.x *= scale_factor
    tLength.scale.x *= scale_factor
    tDuration.scale.x *= scale_factor
    tElevation.scale.y *= scale_factor
    tLength.scale.y *= scale_factor
    tDuration.scale.y *= scale_factor

    bpy.ops.object.select_all(action='DESELECT')

    tName.select_set(True)
    tElevation.select_set(True)
    tLength.select_set(True)
    tDuration.select_set(True)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    titlefield = replaceShapeText(titlefield, tName)
    textfield1 = replaceShapeText(textfield1, tLength)
    textfield2 = replaceShapeText(textfield2, tElevation)
    textfield3 = replaceShapeText(textfield3, tDuration)

    icon0 = None
    icon1 = None
    icon2 = None
    icon3 = None


    icon0 = textIcon(titleIcon,tName,MapObject,False, textSize2 )
    icon1 = textIcon(iconString1,tLength,MapObject,False, textSize)
    icon2 = textIcon(iconString2,tElevation,MapObject,False,textSize)
    icon3 = textIcon(iconString3,tDuration,MapObject,False,textSize)


    projection("separate",MapObject,tName)
    projection("separate",MapObject,tLength)
    projection("separate",MapObject,tElevation)
    projection("separate",MapObject,tDuration)

    for _icon in (icon0, icon1, icon2, icon3):
        if _icon is None:
            continue
        bpy.ops.object.select_all(action='DESELECT')
        _icon.select_set(True)
        bpy.context.view_layer.objects.active = _icon
        bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='MEDIAN')

        # Delete bottom faces by z-position instead of face-normal direction.
        # Normal-based selection (selectBottomFaces threshold -0.95) fails when
        # recalc_normals flips the bottom copy upward for taller icons (high minThickness).
        bm = bmesh.new()
        bm.from_mesh(_icon.data)
        if bm.verts:
            min_z = min(v.co.z for v in bm.verts)
            faces_to_del = [f for f in bm.faces if f.calc_center_median().z < (min_z + 0.3)]
            bmesh.ops.delete(bm, geom=faces_to_del, context='FACES')
            bm.to_mesh(_icon.data)
        bm.free()


    if icon0 != None:
        projection("separate", MapObject, icon0)
    if icon1 != None:
        projection("separate", MapObject, icon1)
    if icon2 != None:
        projection("separate", MapObject, icon2)
    if icon3 != None:
        projection("separate", MapObject, icon3)

    bpy.ops.object.select_all(action='DESELECT')

    if icon0 != None: icon0.select_set(True)
    if icon1 != None: icon1.select_set(True)
    if icon2 != None: icon2.select_set(True)
    if icon3 != None: icon3.select_set(True)


    tName.select_set(True)
    tElevation.select_set(True)
    tLength.select_set(True)
    tDuration.select_set(True)
    #curveObj.select_set(True)


    bpy.context.view_layer.objects.active = tName

    bpy.ops.object.join()
    bpy.ops.object.origin_set(type='ORIGIN_CURSOR', center='MEDIAN')

    tName.name = name + "_Text"
    tName.rotation_euler[2] += shapeRotation * (3.14159265 / 180)

    tName.select_set(True)
    bpy.context.view_layer.objects.active = tName
    bpy.ops.object.transform_apply(location = False, rotation=True, scale = False)
    bpy.ops.object.origin_set(type='ORIGIN_CURSOR', center='MEDIAN')


    textobj = tName
    return textobj


def HexagonOuterText():

    from . import transform_MapObject  # deferred to avoid circular import at load time

    size = bpy.context.scene.tp3d.objSize
    outerBorderSize = bpy.context.scene.tp3d.outerBorderSize
    plateThickness = bpy.context.scene.tp3d.plateThickness
    name = bpy.context.scene.tp3d.modelname
    centerx = bpy.context.scene.tp3d.o_centerx
    centery = bpy.context.scene.tp3d.o_centery
    text_angle_preset = bpy.context.scene.tp3d.text_angle_preset
    titlefield = bpy.context.scene.tp3d.titlefield
    textfield1 = bpy.context.scene.tp3d.textfield1
    textfield2 = bpy.context.scene.tp3d.textfield2
    textfield3 = bpy.context.scene.tp3d.textfield3
    textfield4 = bpy.context.scene.tp3d.textfield4
    textfield5 = bpy.context.scene.tp3d.textfield5
    titleIcon = bpy.context.scene.tp3d.titleIcon
    iconString1 = bpy.context.scene.tp3d.iconText1
    iconString2 = bpy.context.scene.tp3d.iconText2
    iconString3 = bpy.context.scene.tp3d.iconText3
    iconString4 = bpy.context.scene.tp3d.iconText4
    iconString5 = bpy.context.scene.tp3d.iconText5


    outersize = size * ( 1 + outerBorderSize/100)
    thickness = plateThickness
    textSize = bpy.context.scene.tp3d.textSize
    textSize2 = bpy.context.scene.tp3d.textSizeTitle
    shapeRotation = bpy.context.scene.tp3d.shapeRotation

    if textSize2 == 0:
        textSize2 = textSize


    verts = []
    faces = []
    for i in range(6):
        angle = math.radians(60 * i)
        x = outersize/2 * math.cos(angle)
        y = outersize/2 * math.sin(angle)
        verts.append((x, y, 0))
    verts.append((0, 0, 0))  # Center vertex
    faces = [[i, (i + 1) % 6, 6] for i in range(6)]
    mesh = bpy.data.meshes.new("HexagonOuter")
    outerHex = bpy.data.objects.new("HexagonOuter", mesh)
    bpy.context.collection.objects.link(outerHex)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    outerHex.name = name
    outerHex.data.name = name

    bpy.context.view_layer.objects.active = outerHex
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.extrude_region_move()
    bpy.ops.transform.translate(value=(0, 0, -8))#bpy.ops.mesh.select_all(action='DESELECT')

    bpy.ops.object.mode_set(mode='OBJECT')

    # Get the mesh data
    mesh = outerHex.data

    # Get selected faces
    selected_faces = [face for face in mesh.polygons if face.select]

    if selected_faces:
        for face in selected_faces:
            for vert_idx in face.vertices:
                vert = mesh.vertices[vert_idx]
                vert.co.z =  - thickness
    else:
        print("No face selected.")

    _apply_plate_bevel(outerHex, bpy.context.scene.tp3d.plateBevel, thickness)
    recalculateNormals(outerHex)

    if bpy.context.scene.tp3d.handleStyle != 'NONE':
        _add_medal_handle(outerHex, thickness, bpy.context.scene.tp3d.handleStyle, bpy.context.scene.tp3d.plateBevel)

    transform_MapObject(outerHex, centerx, centery)


    dist = (outersize - size)/4 + size/2



    # Hexagon sides sit at 30/90/150/210/270/330 deg. Title takes the flat
    # top (90), text1-3 take the three bottom sides (210/270/330), and
    # field4/field5 take the two remaining upper sides (30/150) -- the only
    # ones left empty. Upper-half sides (sin(angle) > 0, same half as the
    # title) need the same +180 flip as the title to read right-side up;
    # the lower-half sides don't.
    text_specs = [
        ("t_name", 90, True),
        ("t_length", 210, False),
        ("t_elevation", 270, False),
        ("t_duration", 330, False),
        ("t_field4", 30, True),
        ("t_field5", 150, True),
    ]
    for text_name, base_angle, flip in text_specs:
        angle = base_angle + text_angle_preset
        angle_centered = angle + 90
        x = math.cos(math.radians(angle)) * (dist * math.cos(math.radians(30)))
        y = math.sin(math.radians(angle)) * (dist * math.cos(math.radians(30)))
        rot_z = math.radians(angle_centered)
        if flip:
            rot_z += math.radians(180)
        create_text(text_name, text_name.split("_")[1].capitalize(), (x, y,1.4),1,  (0, 0, rot_z), 0.4)

    tName = bpy.data.objects.get("t_name")
    tElevation = bpy.data.objects.get("t_elevation")
    tLength = bpy.data.objects.get("t_length")
    tDuration = bpy.data.objects.get("t_duration")
    tField4 = bpy.data.objects.get("t_field4")
    tField5 = bpy.data.objects.get("t_field5")



    transform_MapObject(tName, centerx, centery)
    transform_MapObject(tElevation, centerx, centery)
    transform_MapObject(tLength, centerx, centery)
    transform_MapObject(tDuration, centerx, centery)
    transform_MapObject(tField4, centerx, centery)
    transform_MapObject(tField5, centerx, centery)


    #Scale text sizes to mm values (blender units)
    bpy.context.view_layer.update()
    current_height = tName.dimensions.y
    if current_height == 0:
        current_height = tElevation.dimensions.y
    if current_height == 0:
        current_height = tLength.dimensions.y
    if current_height == 0:
        current_height = 5
    scale_factor_title = textSize2 / current_height
    tName.scale.x *= scale_factor_title
    tName.scale.y *= scale_factor_title

    scale_factor = textSize / current_height
    tElevation.scale.x *= scale_factor
    tLength.scale.x *= scale_factor
    tDuration.scale.x *= scale_factor
    tField4.scale.x *= scale_factor
    tField5.scale.x *= scale_factor
    tElevation.scale.y *= scale_factor
    tLength.scale.y *= scale_factor
    tDuration.scale.y *= scale_factor
    tField4.scale.y *= scale_factor
    tField5.scale.y *= scale_factor

    bpy.ops.object.select_all(action='DESELECT')

    tName.select_set(True)
    tElevation.select_set(True)
    tLength.select_set(True)
    tDuration.select_set(True)
    tField4.select_set(True)
    tField5.select_set(True)

    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)


    titlefield = replaceShapeText(titlefield, tName)
    textfield1 = replaceShapeText(textfield1, tLength)
    textfield2 = replaceShapeText(textfield2, tElevation)
    textfield3 = replaceShapeText(textfield3, tDuration)
    textfield4 = replaceShapeText(textfield4, tField4)
    textfield5 = replaceShapeText(textfield5, tField5)

    icon0 = None
    icon1 = None
    icon2 = None
    icon3 = None
    icon4 = None
    icon5 = None


    icon0 = textIcon(titleIcon,tName,outerHex, False, textSize2)
    icon1 = textIcon(iconString1,tLength,outerHex, False, textSize)
    icon2 = textIcon(iconString2,tElevation,outerHex, False, textSize)
    icon3 = textIcon(iconString3,tDuration,outerHex, False, textSize)
    icon4 = textIcon(iconString4,tField4,outerHex, False, textSize)
    icon5 = textIcon(iconString5,tField5,outerHex, False, textSize)


    convert_text_to_mesh("t_name", outerHex.name, False)
    convert_text_to_mesh("t_elevation", outerHex.name, False)
    convert_text_to_mesh("t_length", outerHex.name, False)
    convert_text_to_mesh("t_duration", outerHex.name, False)
    convert_text_to_mesh("t_field4", outerHex.name, False)
    convert_text_to_mesh("t_field5", outerHex.name, False)



    bpy.ops.object.select_all(action='DESELECT')

    if icon0 != None: icon0.select_set(True)
    if icon1 != None: icon1.select_set(True)
    if icon2 != None: icon2.select_set(True)
    if icon3 != None: icon3.select_set(True)
    if icon4 != None: icon4.select_set(True)
    if icon5 != None: icon5.select_set(True)

    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)


    tName.select_set(True)
    tElevation.select_set(True)
    tLength.select_set(True)
    tDuration.select_set(True)
    tField4.select_set(True)
    tField5.select_set(True)


    bpy.context.view_layer.objects.active = tName


    bpy.ops.object.join()

    bpy.ops.object.origin_set(type='ORIGIN_CURSOR', center='MEDIAN')

    tName.name = name + "_Text"
    outerHex.name = name + "_Plate"

    tName.location.z += plateThickness
    outerHex.location.z += plateThickness


    #SHAPE ROTATION
    outerHex.rotation_euler[2] += shapeRotation * (3.14159265 / 180)
    outerHex.select_set(True)
    bpy.context.view_layer.objects.active = outerHex
    bpy.ops.object.transform_apply(location = False, rotation=True, scale = False)

    bpy.ops.object.origin_set(type='ORIGIN_CURSOR', center='MEDIAN')


    plateobj = outerHex

    textobj = tName
    return textobj, plateobj


def HexagonFrontText():

    from . import transform_MapObject  # deferred to avoid circular import at load time

    size = bpy.context.scene.tp3d.objSize
    outerBorderSize = bpy.context.scene.tp3d.outerBorderSize
    plateThickness = bpy.context.scene.tp3d.plateThickness
    name = bpy.context.scene.tp3d.modelname
    centerx = bpy.context.scene.tp3d.o_centerx
    centery = bpy.context.scene.tp3d.o_centery
    text_angle_preset = bpy.context.scene.tp3d.text_angle_preset
    titlefield = bpy.context.scene.tp3d.titlefield
    textfield1 = bpy.context.scene.tp3d.textfield1
    textfield2 = bpy.context.scene.tp3d.textfield2
    textfield3 = bpy.context.scene.tp3d.textfield3
    titleIcon = bpy.context.scene.tp3d.titleIcon
    iconString1 = bpy.context.scene.tp3d.iconText1
    iconString2 = bpy.context.scene.tp3d.iconText2
    iconString3 = bpy.context.scene.tp3d.iconText3
    minThickness = bpy.context.scene.tp3d.minThickness

    outersize = size * ( 1 + outerBorderSize/100)
    thickness = plateThickness
    textSize = bpy.context.scene.tp3d.textSize
    textSize2 = bpy.context.scene.tp3d.textSizeTitle
    shapeRotation = bpy.context.scene.tp3d.shapeRotation



    if textSize2 == 0:
        textSize2 = textSize


    verts = []
    faces = []
    for i in range(6):
        angle = math.radians(60 * i)
        x = outersize/2 * math.cos(angle)
        y = outersize/2 * math.sin(angle)
        verts.append((x, y, 0))
    verts.append((0, 0, 0))  # Center vertex
    faces = [[i, (i + 1) % 6, 6] for i in range(6)]
    mesh = bpy.data.meshes.new("HexagonOuter")
    outerHex = bpy.data.objects.new("HexagonOuter", mesh)
    bpy.context.collection.objects.link(outerHex)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    outerHex.name = name
    outerHex.data.name = name

    bpy.context.view_layer.objects.active = outerHex
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.extrude_region_move()
    bpy.ops.transform.translate(value=(0, 0, -8))#bpy.ops.mesh.select_all(action='DESELECT')

    bpy.ops.object.mode_set(mode='OBJECT')

    # Get the mesh data
    mesh = outerHex.data

    # Get selected faces
    selected_faces = [face for face in mesh.polygons if face.select]

    if selected_faces:
        for face in selected_faces:
            for vert_idx in face.vertices:
                vert = mesh.vertices[vert_idx]
                vert.co.z =  - thickness;
    else:
        print("No face selected.")

    _apply_plate_bevel(outerHex, bpy.context.scene.tp3d.plateBevel, thickness)
    recalculateNormals(outerHex)

    if bpy.context.scene.tp3d.handleStyle != 'NONE':
        _add_medal_handle(outerHex, thickness, bpy.context.scene.tp3d.handleStyle, bpy.context.scene.tp3d.plateBevel)

    transform_MapObject(outerHex, centerx, centery)

    dist = outersize/2

    temp_y = math.sin(math.radians(90)) * (dist  * math.cos(math.radians(30)))



    for i, (text_name, angle) in enumerate(zip(["t_name","t_length", "t_elevation", "t_duration"], [90 + text_angle_preset, 210 + text_angle_preset, 270 + text_angle_preset, 330 + text_angle_preset])):
        angle_centered = angle + 90
        x = math.cos(math.radians(angle)) * (dist * math.cos(math.radians(30)))
        y = math.sin(math.radians(angle)) * (dist * math.cos(math.radians(30)))
        rot_z = math.radians(angle_centered)
        #if i == 0:
            #rot_z += math.radians(180)
        create_text(text_name, text_name.split("_")[1].capitalize(), (x, y,minThickness/2 - plateThickness / 2),1,  (math.radians(90), 0, rot_z), 0.4)

    tName = bpy.data.objects.get("t_name")
    tElevation = bpy.data.objects.get("t_elevation")
    tLength = bpy.data.objects.get("t_length")
    tDuration = bpy.data.objects.get("t_duration")


    transform_MapObject(tName, centerx, centery)
    transform_MapObject(tElevation, centerx, centery)
    transform_MapObject(tLength, centerx, centery)
    transform_MapObject(tDuration, centerx, centery)

    #Scale text sizes to mm values (blender units)
    bpy.context.view_layer.update()
    current_height = tName.dimensions.y
    if current_height == 0:
        current_height = tElevation.dimensions.y
    if current_height == 0:
        current_height = tLength.dimensions.y
    if current_height == 0:
        current_height = 5
    scale_factor = textSize2 / current_height
    tName.scale.x *= scale_factor
    tName.scale.y *= scale_factor

    scale_factor = textSize / current_height
    tElevation.scale.x *= scale_factor
    tLength.scale.x *= scale_factor
    tDuration.scale.x *= scale_factor
    tElevation.scale.y *= scale_factor
    tLength.scale.y *= scale_factor
    tDuration.scale.y *= scale_factor


    bpy.ops.object.select_all(action='DESELECT')

    tName.select_set(True)
    tElevation.select_set(True)
    tLength.select_set(True)
    tDuration.select_set(True)

    bpy.ops.object.transform_apply(location = False, rotation=False, scale = True)



    titlefield = replaceShapeText(titlefield, tName)
    textfield1 = replaceShapeText(textfield1, tLength)
    textfield2 = replaceShapeText(textfield2, tElevation)
    textfield3 = replaceShapeText(textfield3, tDuration)

    icon0 = None
    icon1 = None
    icon2 = None
    icon3 = None

    icon0 = textIcon(titleIcon,tName,outerHex, False, textSize2)
    icon1 = textIcon(iconString1,tLength,outerHex, False, textSize)
    icon2 = textIcon(iconString2,tElevation,outerHex, False, textSize)
    icon3 = textIcon(iconString3,tDuration,outerHex, False, textSize)

    convert_text_to_mesh("t_name", outerHex.name, False)
    convert_text_to_mesh("t_elevation", outerHex.name, False)
    convert_text_to_mesh("t_length", outerHex.name, False)
    convert_text_to_mesh("t_duration", outerHex.name, False)



    bpy.ops.object.select_all(action='DESELECT')

    if icon0 != None: icon0.select_set(True)
    if icon1 != None: icon1.select_set(True)
    if icon2 != None: icon2.select_set(True)
    if icon3 != None: icon3.select_set(True)

    tName.select_set(True)
    tElevation.select_set(True)
    tLength.select_set(True)
    tDuration.select_set(True)

    bpy.context.view_layer.objects.active = tName

    bpy.ops.object.join()

    bpy.ops.object.origin_set(type='ORIGIN_CURSOR', center='MEDIAN')

    tName.name = name + "_Text"
    outerHex.name = name + "_Plate"

    tName.location.z += plateThickness
    outerHex.location.z += plateThickness

    #SHAPE ROTATION
    outerHex.rotation_euler[2] += shapeRotation * (3.14159265 / 180)
    outerHex.select_set(True)
    bpy.context.view_layer.objects.active = outerHex
    bpy.ops.object.transform_apply(location = False, rotation=True, scale = False)
    bpy.ops.object.origin_set(type='ORIGIN_CURSOR', center='MEDIAN')


    plateobj = outerHex

    textobj = tName

    return textobj, plateobj


def OctagonOuterText():

    from . import transform_MapObject  # deferred to avoid circular import at load time

    size = bpy.context.scene.tp3d.objSize
    outerBorderSize = bpy.context.scene.tp3d.outerBorderSize
    plateThickness = bpy.context.scene.tp3d.plateThickness
    name = bpy.context.scene.tp3d.modelname
    centerx = bpy.context.scene.tp3d.o_centerx
    centery = bpy.context.scene.tp3d.o_centery
    text_angle_preset = bpy.context.scene.tp3d.text_angle_preset
    titlefield = bpy.context.scene.tp3d.titlefield
    textfield1 = bpy.context.scene.tp3d.textfield1
    textfield2 = bpy.context.scene.tp3d.textfield2
    textfield3 = bpy.context.scene.tp3d.textfield3
    titleIcon = bpy.context.scene.tp3d.titleIcon
    iconString1 = bpy.context.scene.tp3d.iconText1
    iconString2 = bpy.context.scene.tp3d.iconText2
    iconString3 = bpy.context.scene.tp3d.iconText3


    num_sides = 8
    outersize = size * (1 + outerBorderSize / 100)
    thickness = plateThickness
    textSize = bpy.context.scene.tp3d.textSize
    textSize2 = bpy.context.scene.tp3d.textSizeTitle
    shapeRotation = bpy.context.scene.tp3d.shapeRotation

    if textSize2 == 0:
        textSize2 = textSize

    verts = []
    faces = []

    # Create vertices for octagon
    for i in range(num_sides):
        angle = math.radians(360 / num_sides * i + 22.5)
        x = outersize / 2 * math.cos(angle)
        y = outersize / 2 * math.sin(angle)
        verts.append((x, y, 0))
    verts.append((0, 0, 0))  # center vertex
    faces = [[i, (i + 1) % num_sides, num_sides] for i in range(num_sides)]

    mesh = bpy.data.meshes.new("OctagonOuter")
    outerOct = bpy.data.objects.new("OctagonOuter", mesh)
    bpy.context.collection.objects.link(outerOct)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    outerOct.name = name
    outerOct.data.name = name

    bpy.context.view_layer.objects.active = outerOct
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.extrude_region_move()
    bpy.ops.transform.translate(value=(0, 0, -8))
    bpy.ops.object.mode_set(mode='OBJECT')

    mesh = outerOct.data
    selected_faces = [face for face in mesh.polygons if face.select]

    if selected_faces:
        for face in selected_faces:
            for vert_idx in face.vertices:
                vert = mesh.vertices[vert_idx]
                vert.co.z = -thickness
    else:
        print("No face selected.")

    _apply_plate_bevel(outerOct, bpy.context.scene.tp3d.plateBevel, thickness)
    recalculateNormals(outerOct)

    if bpy.context.scene.tp3d.handleStyle != 'NONE':
        _add_medal_handle(outerOct, thickness, bpy.context.scene.tp3d.handleStyle, bpy.context.scene.tp3d.plateBevel)

    transform_MapObject(outerOct, centerx, centery)

    #Text placement
    dist = (outersize - size) / 4 + size / 2
    text_labels = ["t_name", "t_length", "t_elevation", "t_duration"]

    # Choose 4 corners of the octagon
    base_angles = [90 + text_angle_preset, 225 + text_angle_preset, 270 + text_angle_preset, 315 + text_angle_preset]

    for i, (text_name, angle) in enumerate(zip(text_labels, base_angles)):
        angle_centered = angle + 90
        x = math.cos(math.radians(angle)) * (dist * math.cos(math.radians(22.5)))
        y = math.sin(math.radians(angle)) * (dist * math.cos(math.radians(22.5)))
        rot_z = math.radians(angle_centered)
        if i == 0:
            rot_z += math.radians(180)
        create_text(text_name, text_name.split("_")[1].capitalize(), (x, y,1.4),1,  (0, 0, rot_z), 0.4)



    # Get text objects
    tName = bpy.data.objects.get("t_name")
    tElevation = bpy.data.objects.get("t_elevation")
    tLength = bpy.data.objects.get("t_length")
    tDuration = bpy.data.objects.get("t_duration")

    # Position relative to plate
    transform_MapObject(tName, centerx, centery)
    transform_MapObject(tElevation, centerx, centery)
    transform_MapObject(tLength, centerx, centery)
    transform_MapObject(tDuration, centerx, centery)

    #Scale text sizes to mm values (blender units)
    bpy.context.view_layer.update()
    current_height = tName.dimensions.y
    if current_height == 0:
        current_height = tElevation.dimensions.y
    if current_height == 0:
        current_height = tLength.dimensions.y
    if current_height == 0:
        current_height = 5
    scale_factor = textSize2 / current_height
    tName.scale.x *= scale_factor
    tName.scale.y *= scale_factor

    scale_factor = textSize / current_height
    tElevation.scale.x *= scale_factor
    tLength.scale.x *= scale_factor
    tDuration.scale.x *= scale_factor
    tElevation.scale.y *= scale_factor
    tLength.scale.y *= scale_factor
    tDuration.scale.y *= scale_factor

    bpy.ops.object.select_all(action='DESELECT')

    tName.select_set(True)
    tElevation.select_set(True)
    tLength.select_set(True)
    tDuration.select_set(True)

    bpy.ops.object.transform_apply(location = False, rotation=False, scale = True)

    titlefield = replaceShapeText(titlefield, tName)
    textfield1 = replaceShapeText(textfield1, tLength)
    textfield2 = replaceShapeText(textfield2, tElevation)
    textfield3 = replaceShapeText(textfield3, tDuration)

    icon0 = None
    icon1 = None
    icon2 = None
    icon3 = None

    icon0 = textIcon(titleIcon,tName,outerOct, False, textSize2)
    icon1 = textIcon(iconString1,tLength,outerOct, False, textSize)
    icon2 = textIcon(iconString2,tElevation,outerOct, False, textSize)
    icon3 = textIcon(iconString3,tDuration,outerOct, False, textSize)

    convert_text_to_mesh("t_name", outerOct.name, False)
    convert_text_to_mesh("t_elevation", outerOct.name, False)
    convert_text_to_mesh("t_length", outerOct.name, False)
    convert_text_to_mesh("t_duration", outerOct.name, False)


    bpy.ops.object.select_all(action='DESELECT')

    if icon0 != None: icon0.select_set(True)
    if icon1 != None: icon1.select_set(True)
    if icon2 != None: icon2.select_set(True)
    if icon3 != None: icon3.select_set(True)

    tName.select_set(True)
    tElevation.select_set(True)
    tLength.select_set(True)
    tDuration.select_set(True)
    bpy.context.view_layer.objects.active = tName
    bpy.ops.object.join()
    bpy.ops.object.origin_set(type='ORIGIN_CURSOR', center='MEDIAN')

    tName.name = name + "_Text"
    outerOct.name = name + "_Plate"

    tName.location.z += plateThickness
    outerOct.location.z += plateThickness


    #SHAPE ROTATION
    outerOct.rotation_euler[2] += shapeRotation * (3.14159265 / 180)
    outerOct.select_set(True)
    bpy.context.view_layer.objects.active = outerOct
    bpy.ops.object.transform_apply(location = False, rotation=True, scale = False)
    bpy.ops.object.origin_set(type='ORIGIN_CURSOR', center='MEDIAN')


    plateobj = outerOct

    textobj = tName

    return textobj, plateobj


def _wrap_mesh_around_circle(obj, radius, base_angle_rad, upper, anchor_x=0.0, anchor_y=0.0):
    """Bend a flat mesh into an arc of the given radius, centered on base_angle_rad.

    Local X (the flat reading direction) becomes angle around the circle;
    local Y (the flat "up" direction) becomes radial offset from `radius`.
    Local Z (extrusion depth) is left untouched.

    The math treats local (anchor_x, anchor_y) as the piece's own visual
    center, i.e. local coordinates ARE the final world-space position
    (obj.location must be (0, 0, 0) — see caller). Callers with an icon
    joined onto the text should pass the anchor measured from the *text
    alone*, before the icon was joined in: the icon's own bounding box
    rarely matches the text's, so folding it into the center-of-mass would
    drag the text off both its target angle and its target radius by
    however lopsided that particular icon happens to be.

    Upper-half placements keep their "up" pointing outward and read
    clockwise (angle decreases as X increases); lower-half placements read
    counter-clockwise with "up" pointing inward. That split is what makes
    text sitting anywhere on the ring come out right-side up and left-to-
    right readable to a viewer looking straight down at the medal, matching
    how text is conventionally arced on a coin/medal.
    """
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    for v in bm.verts:
        x, y, z = v.co.x - anchor_x, v.co.y - anchor_y, v.co.z
        if upper:
            theta = base_angle_rad - x / radius
            r = radius + y
        else:
            theta = base_angle_rad + x / radius
            r = radius - y
        v.co.x = r * math.cos(theta)
        v.co.y = r * math.sin(theta)
        v.co.z = z
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def MedalText():

    from . import transform_MapObject  # deferred to avoid circular import at load time

    size = bpy.context.scene.tp3d.objSize
    outerBorderSize = bpy.context.scene.tp3d.outerBorderSize
    plateThickness = bpy.context.scene.tp3d.plateThickness
    name = bpy.context.scene.tp3d.modelname
    centerx = bpy.context.scene.tp3d.o_centerx
    centery = bpy.context.scene.tp3d.o_centery
    text_angle_preset = bpy.context.scene.tp3d.text_angle_preset
    titlefield = bpy.context.scene.tp3d.titlefield
    textfield1 = bpy.context.scene.tp3d.textfield1
    textfield2 = bpy.context.scene.tp3d.textfield2
    textfield3 = bpy.context.scene.tp3d.textfield3
    titleIcon = bpy.context.scene.tp3d.titleIcon
    iconString1 = bpy.context.scene.tp3d.iconText1
    iconString2 = bpy.context.scene.tp3d.iconText2
    iconString3 = bpy.context.scene.tp3d.iconText3

    outersize = size * (1 + outerBorderSize / 100)
    thickness = plateThickness
    textSize = bpy.context.scene.tp3d.textSize
    textSize2 = bpy.context.scene.tp3d.textSizeTitle
    shapeRotation = bpy.context.scene.tp3d.shapeRotation

    if textSize2 == 0:
        textSize2 = textSize

    # --- Plate circle (slightly bigger, same center as coin, behind it in Z) ---
    plate_radius = outersize / 2
    num_segments = 64

    verts = []
    for i in range(num_segments):
        angle = 2 * math.pi * i / num_segments
        x = plate_radius * math.cos(angle)
        y = plate_radius * math.sin(angle)
        verts.append((x, y, 0))

    edges = [(i, (i + 1) % num_segments) for i in range(num_segments)]
    plate_mesh_data = bpy.data.meshes.new("MedalPlate")
    plateObj = bpy.data.objects.new("MedalPlate", plate_mesh_data)
    bpy.context.collection.objects.link(plateObj)
    plate_mesh_data.from_pydata(verts, edges, [])
    plate_mesh_data.update()

    bpy.context.view_layer.objects.active = plateObj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.fill()
    bpy.ops.mesh.extrude_region_move()
    bpy.ops.transform.translate(value=(0, 0, -8))
    bpy.ops.object.mode_set(mode='OBJECT')

    plate_mesh_data = plateObj.data
    selected_faces = [face for face in plate_mesh_data.polygons if face.select]
    if selected_faces:
        for face in selected_faces:
            for vert_idx in face.vertices:
                vert = plate_mesh_data.vertices[vert_idx]
                vert.co.z = -thickness
    else:
        print("No face selected.")

    _apply_plate_bevel(plateObj, bpy.context.scene.tp3d.plateBevel, thickness)
    recalculateNormals(plateObj)

    if bpy.context.scene.tp3d.handleStyle != 'NONE':
        _add_medal_handle(plateObj, thickness, bpy.context.scene.tp3d.handleStyle, bpy.context.scene.tp3d.plateBevel)

    plateObj.name = name
    plateObj.data.name = name
    transform_MapObject(plateObj, centerx, centery)

    # --- Curved text in the ring between coin edge and plate edge ---
    # Text sits at the midpoint of the ring so it fits in the open space the map doesn't cover.
    # Angles: title at top (90°), other three spaced 90° apart around the
    # lower arc (left/bottom/right), symmetric about the bottom (270°).
    text_radius = (size / 2 + outersize / 2) / 2
    text_specs = [
        ("t_name",      90),
        ("t_length",    180),
        ("t_elevation", 270),
        ("t_duration",  0),
    ]

    # Build each label flat and unrotated at the local origin. Placing icons
    # and measuring bounding boxes against flat, un-deformed text (rather
    # than an already-curved one) is what makes the icon/text spacing land
    # right — the arc bend is applied afterwards, once, to the finished
    # icon+text mesh as a single rigid unit.
    for text_name, base_deg in text_specs:
        create_text(text_name, text_name.split("_")[1].capitalize(), (0, 0, 1.4), 1, (0, 0, 0), 0.4)

    tName      = bpy.data.objects.get("t_name")
    tElevation = bpy.data.objects.get("t_elevation")
    tLength    = bpy.data.objects.get("t_length")
    tDuration  = bpy.data.objects.get("t_duration")

    # Scale text to physical mm values
    bpy.context.view_layer.update()
    current_height = tName.dimensions.y
    if current_height == 0:
        current_height = tElevation.dimensions.y
    if current_height == 0:
        current_height = tLength.dimensions.y
    if current_height == 0:
        current_height = 5

    scale_factor_title = textSize2 / current_height
    tName.scale.x *= scale_factor_title
    tName.scale.y *= scale_factor_title

    scale_factor = textSize / current_height
    tElevation.scale.x *= scale_factor
    tLength.scale.x    *= scale_factor
    tDuration.scale.x  *= scale_factor
    tElevation.scale.y *= scale_factor
    tLength.scale.y    *= scale_factor
    tDuration.scale.y  *= scale_factor

    bpy.ops.object.select_all(action='DESELECT')
    tName.select_set(True)
    tElevation.select_set(True)
    tLength.select_set(True)
    tDuration.select_set(True)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    titlefield  = replaceShapeText(titlefield,  tName)
    textfield1  = replaceShapeText(textfield1,  tLength)
    textfield2  = replaceShapeText(textfield2,  tElevation)
    textfield3  = replaceShapeText(textfield3,  tDuration)

    icon0 = textIcon(titleIcon,   tName,      plateObj, False, textSize2)
    icon1 = textIcon(iconString1, tLength,    plateObj, False, textSize)
    icon2 = textIcon(iconString2, tElevation, plateObj, False, textSize)
    icon3 = textIcon(iconString3, tDuration,  plateObj, False, textSize)

    text_group = {
        "t_name":      (tName,      icon0),
        "t_length":    (tLength,    icon1),
        "t_elevation": (tElevation, icon2),
        "t_duration":  (tDuration,  icon3),
    }

    # Convert each label to a mesh, fold its icon (still flat) into it, then
    # bend the combined flat unit into an arc as one rigid piece.
    for text_name, base_deg in text_specs:
        txt_obj, icon = text_group[text_name]

        convert_text_to_mesh(text_name, plateObj.name, False)

        # Bake the icon-accommodation shift (appendTextIcon nudges the text
        # object sideways to make room for the icon) into the mesh and
        # zero out the object transform. The wrap below treats local
        # coordinates as final world-space coordinates, so a leftover
        # object.location here would silently offset the whole label away
        # from the ring. Measure the text's own center now, before the
        # icon is joined in, so the icon's shape can't skew it.
        bpy.ops.object.select_all(action='DESELECT')
        txt_obj.select_set(True)
        bpy.context.view_layer.objects.active = txt_obj
        bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)

        bm = bmesh.new()
        bm.from_mesh(txt_obj.data)
        xs = [v.co.x for v in bm.verts]
        ys = [v.co.y for v in bm.verts]
        anchor_x = (min(xs) + max(xs)) / 2 if xs else 0.0
        anchor_y = (min(ys) + max(ys)) / 2 if ys else 0.0
        bm.free()

        if icon is not None:
            bpy.ops.object.select_all(action='DESELECT')
            icon.select_set(True)
            txt_obj.select_set(True)
            bpy.context.view_layer.objects.active = txt_obj
            bpy.ops.object.join()

        angle_deg = base_deg + text_angle_preset
        # Strict > (not >=) so the two fields that now sit exactly on the
        # left/right cardinal points (180°/0°, sin == 0) fall in with the
        # rest of the bottom arc's "lower" convention rather than flipping
        # to match the title's "upper" one right at that boundary.
        upper = math.sin(math.radians(angle_deg)) > 0
        _wrap_mesh_around_circle(txt_obj, text_radius, math.radians(angle_deg), upper, anchor_x, anchor_y)
        transform_MapObject(txt_obj, centerx, centery)

    bpy.ops.object.select_all(action='DESELECT')
    tName.select_set(True)
    tElevation.select_set(True)
    tLength.select_set(True)
    tDuration.select_set(True)
    bpy.context.view_layer.objects.active = tName
    bpy.ops.object.join()
    bpy.ops.object.origin_set(type='ORIGIN_CURSOR', center='MEDIAN')

    tName.name    = name + "_Text"
    plateObj.name = name + "_Plate"

    tName.location.z    += plateThickness
    plateObj.location.z += plateThickness

    # Shape rotation applied to plate
    plateObj.rotation_euler[2] += shapeRotation * (3.14159265 / 180)
    plateObj.select_set(True)
    bpy.context.view_layer.objects.active = plateObj
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
    bpy.ops.object.origin_set(type='ORIGIN_CURSOR', center='MEDIAN')

    return tName, plateObj


def BottomText(obj):

    from . import transform_MapObject  # deferred to avoid circular import at load time

    pathScale = bpy.context.scene.tp3d.pathScale


    name = obj.name
    if "objSize" in obj:
        size = obj["objSize"]
    else:
        return

        # Place text objects
    text_size = (size / 10)



    tName = create_text("t_name", "Name", (0, 0,1.1),text_size)


    cx = obj.location.x
    cy = obj.location.y

    transform_MapObject(tName, cx, cy)

    tName.location.z = obj.location.z + 0.1
    tName.data.extrude = 0.1

    tName.scale.x *= -1


    update_text_object("t_name", name)

    convert_text_to_mesh("t_name", obj.name, False)

    tName.name = name + "_Mark"

    bpy.ops.object.select_all(action='DESELECT')

    tName.select_set(True)

    bpy.context.view_layer.objects.active = tName

    mat = bpy.data.materials.get("TRAIL")
    tName.data.materials.clear()
    tName.data.materials.append(mat)

    return tName
