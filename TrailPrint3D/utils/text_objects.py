import bpy  # type: ignore
import bmesh  # type: ignore
import math
import os
import platform
from mathutils import Vector  # type: ignore

from .mesh_ops import recalculateNormals

try:
    from ..utils_pe import textIcon
except ImportError:
    def textIcon(*_):
        return None


def update_text_object(obj_name, new_text):
    """Updates the text of a Blender text object."""
    text_obj = bpy.data.objects.get(obj_name)
    if text_obj and text_obj.type == 'FONT':
        text_obj.data.body = new_text


def create_text(name, text, position, scale_multiplier, rotation=(0, 0, 0), extrude=20):
    txt_data = bpy.data.curves.new(name=name, type='FONT')
    txt_obj = bpy.data.objects.new(name=name, object_data=txt_data)
    bpy.context.collection.objects.link(txt_obj)

    textFont = bpy.context.scene.tp3d.textFont

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

    addon_dir = os.path.dirname(__file__)
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
    titleIcon = bpy.context.scene.tp3d.titleIcon
    iconString1 = bpy.context.scene.tp3d.iconText1
    iconString2 = bpy.context.scene.tp3d.iconText2
    iconString3 = bpy.context.scene.tp3d.iconText3


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

    transform_MapObject(outerHex, centerx, centery)


    dist = (outersize - size)/4 + size/2




    for i, (text_name, angle) in enumerate(zip(["t_name","t_length", "t_elevation", "t_duration"], [90 + text_angle_preset, 210 + text_angle_preset, 270 + text_angle_preset, 330 + text_angle_preset])):
        angle_centered = angle + 90
        x = math.cos(math.radians(angle)) * (dist * math.cos(math.radians(30)))
        y = math.sin(math.radians(angle)) * (dist * math.cos(math.radians(30)))
        rot_z = math.radians(angle_centered)
        if i == 0:
            rot_z += math.radians(180)
        create_text(text_name, text_name.split("_")[1].capitalize(), (x, y,1.4),1,  (0, 0, rot_z), 0.4)

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

    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)


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

    plateObj.name = name
    plateObj.data.name = name
    transform_MapObject(plateObj, centerx, centery)

    # --- Curved text in the ring between coin edge and plate edge ---
    # Text sits at the midpoint of the ring so it fits in the open space the map doesn't cover.
    text_radius = (size / 2 + outersize / 2) / 2

    # Create ONE bezier circle that all text objects will follow.
    # Place it at (centerx, centery, 0.4) — the same Z that create_text ends up at
    # (create_text receives z=1.4 and does location.z -= 1 internally → z = 0.4).
    bpy.ops.curve.primitive_bezier_circle_add(
        radius=text_radius, location=(centerx, centery, 0.4)
    )
    circle_path = bpy.context.active_object
    circle_path.name = name + "_TextCircle"

    # Switch to clockwise so the title at the top reads left-to-right when viewed from +Z.
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.curve.switch_direction()
    bpy.ops.object.mode_set(mode='OBJECT')

    # Angles: title at top (90°), other three distributed around the lower arc.
    text_angles = [
        90  + text_angle_preset,
        210 + text_angle_preset,
        270 + text_angle_preset,
        330 + text_angle_preset,
    ]

    for text_name, angle in zip(
        ["t_name", "t_length", "t_elevation", "t_duration"],
        text_angles
    ):
        # For the CW circle (start = 3-o'clock, going clockwise):
        # to reach the point at angle θ (CCW from right), travel CW by (360 − θ)°.
        arc_offset = text_radius * math.radians(360 - angle % 360)

        # create_text positions at (x, y, 1.4) and does z -= 1 → final z = 0.4.
        # We already include centerx/centery in x/y so transform_MapObject is NOT called.
        txt_obj = create_text(
            text_name,
            text_name.split("_")[1].capitalize(),
            (arc_offset + centerx, centery, 1.4),
            1,
            (0, 0, 0),
            0.4,
        )
        txt_obj.data.follow_curve = circle_path

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

    convert_text_to_mesh("t_name",      plateObj.name, False)
    convert_text_to_mesh("t_elevation", plateObj.name, False)
    convert_text_to_mesh("t_length",    plateObj.name, False)
    convert_text_to_mesh("t_duration",  plateObj.name, False)

    # Remove the helper bezier circle — follow_curve is now baked into the meshes.
    bpy.ops.object.select_all(action='DESELECT')
    if circle_path.name in bpy.data.objects:
        bpy.data.objects.remove(circle_path, do_unlink=True)

    bpy.ops.object.select_all(action='DESELECT')
    if icon0 is not None: icon0.select_set(True)
    if icon1 is not None: icon1.select_set(True)
    if icon2 is not None: icon2.select_set(True)
    if icon3 is not None: icon3.select_set(True)

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
