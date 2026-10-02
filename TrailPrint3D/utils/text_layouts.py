"""Shape-agnostic text layout dispatch.

Replaces HexagonOuterText / HexagonFrontText / OctagonOuterText / MedalText /
HexagonInnerText with four shared layout functions. Each is parameterised by
a small table of (field_name, angle, flip) tuples in this module, so adding
a shape is one row, not a 200-line copy.
"""

import math

import bpy  # type: ignore

from .text_objects import (
    convert_text_to_mesh,
    convert_text_to_mesh_obj,
    create_text,
    replaceShapeText,
    wrap_mesh_around_circle,
)

try:
    from ..premium.utils_pe import textIcon  # type: ignore
except ImportError:

    def textIcon(*_args, **_kwargs):
        return None


# ---------------------------------------------------------------------------
# Field tables. Angles are degrees CCW from +X, before text_angle_preset and
# shapeRotation.
# ---------------------------------------------------------------------------

# (field_object_name, base_angle, flip_180)
OUTER_EDGE_FIELDS = {
    "HEXAGON": [
        ("t_name", 90, True),  # N  — title
        ("t_field5", 150, True),  # NW
        ("t_length", 210, False),  # SW
        ("t_elevation", 270, False),  # S
        ("t_duration", 330, False),  # SE
        ("t_field4", 30, True),  # NE
    ],
    "OCTAGON": [
        ("t_name", 90, True),  # N  — title
        ("t_field5", 135, True),  # NW
        ("t_field6", 180, False),  # W   ← new
        ("t_length", 225, False),  # SW
        ("t_elevation", 270, False),  # S
        ("t_duration", 315, False),  # SE
        ("t_field7", 0, False),  # E   ← new
        ("t_field4", 45, True),  # NE
    ],
    "SQUARE": [
        ("t_name", 90, True),  # N  — title
        ("t_length", 180, False),  # W
        ("t_elevation", 270, False),  # S
        ("t_duration", 0, False),  # E
    ],
}

# (field_object_name, base_angle) — the 90 deg X rotation already faces the
# text outward, so no per-field flip.
FRONT_FACE_FIELDS = {
    "HEXAGON": [
        ("t_name", 90),
        ("t_length", 210),
        ("t_elevation", 270),
        ("t_duration", 330),
    ],
    "OCTAGON": [
        ("t_name", 90),
        ("t_length", 225),
        ("t_elevation", 270),
        ("t_duration", 315),
    ],
    "SQUARE": [
        ("t_name", 90),
        ("t_length", 180),
        ("t_elevation", 270),
        ("t_duration", 0),
    ],
}

# (field_object_name, base_angle)
CURVED_RING_FIELDS = {
    "CIRCLE": [
        ("t_name", 90),
        ("t_length", 180),
        ("t_elevation", 270),
        ("t_duration", 0),
    ],
}

# (field_object_name, default_label, base_angle)
ON_MAP_FIELDS = {
    "HEXAGON": [
        ("t_name", "Name", 90),
        ("t_length", "Length", 210),
        ("t_elevation", "Elevation", 270),
        ("t_duration", "Duration", 330),
    ],
}

_FIELD_TO_PROP = {
    "t_name": "titlefield",
    "t_length": "textfield1",
    "t_elevation": "textfield2",
    "t_duration": "textfield3",
    "t_field4": "textfield4",
    "t_field5": "textfield5",
    "t_field6": "textfield6",
    "t_field7": "textfield7",
}

_PROP_TO_ICON = {
    "titlefield": "titleIcon",
    "textfield1": "iconText1",
    "textfield2": "iconText2",
    "textfield3": "iconText3",
    "textfield4": "iconText4",
    "textfield5": "iconText5",
    "textfield6": "iconText6",
    "textfield7": "iconText7",
}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _ring_radii(map_obj, plate_obj, shape):
    """(inner, outer) distances for text placement: the map's own edge and
    the plate/shell's own edge, both measured from their real stored
    outlines — not the Solid-Plate-only outerBorderSize formula.

    This is the actual fix for Shell + text: a Shell's real footprint comes
    from `tolerance + shellWallThickness`, nothing to do with
    outerBorderSize, so the old `size`/`outer_size = size * (1 +
    outerBorderSize/100)` calc only ever happened to work for Solid Plate.
    Reading back `plate_obj["shell_outer_wkt"]` / `["plate_wkt"]` (both
    already stored — see plate.py's create_generic_plate and
    elements.py's _stamp_shell_wkt) means whichever one actually built this
    object is the one that gets measured, and it also fixes Octagon's
    apothem, which this previously hardcoded to 1.0 instead of its real
    cos(45°) (see measured_inradius).

    Falls back to the old scene-setting formula if the WKT isn't there for
    some reason (e.g. a legacy object from before this was stored), so this
    never raises on an object that predates the WKT metadata.
    """
    from shapely import wkt as _wkt

    from .plate import measured_inradius

    tp3d = bpy.context.scene.tp3d
    size = tp3d.objSize
    fallback_outer = size * (1 + tp3d.outerBorderSize / 100)

    inner = None
    if map_obj is not None and "map_polygon_wkt" in map_obj:
        try:
            inner = measured_inradius(_wkt.loads(map_obj["map_polygon_wkt"]), shape)
        except Exception as exc:
            print(f"[TrailPrint3D] text ring inner-radius fallback: {exc!r}")

    outer = None
    outer_wkt_str = (
        plate_obj.get("shell_outer_wkt") or plate_obj.get("plate_wkt")
        if plate_obj
        else None
    )
    if outer_wkt_str:
        try:
            outer = measured_inradius(_wkt.loads(outer_wkt_str), shape)
        except Exception as exc:
            print(f"[TrailPrint3D] text ring outer-radius fallback: {exc!r}")

    if inner is None:
        inner = size / 2
    if outer is None:
        outer = fallback_outer / 2

    return inner, outer


def _field_values():
    tp3d = bpy.context.scene.tp3d
    return {
        "t_name": (tp3d.titlefield, tp3d.titleIcon, True),
        "t_length": (tp3d.textfield1, tp3d.iconText1, False),
        "t_elevation": (tp3d.textfield2, tp3d.iconText2, False),
        "t_duration": (tp3d.textfield3, tp3d.iconText3, False),
        "t_field4": (tp3d.textfield4, tp3d.iconText4, False),
        "t_field5": (tp3d.textfield5, tp3d.iconText5, False),
        "t_field6": (tp3d.textfield6, tp3d.iconText6, False),
        "t_field7": (tp3d.textfield7, tp3d.iconText7, False),
    }


def _scale_to_mm(text_objs):
    tp3d = bpy.context.scene.tp3d
    bpy.context.view_layer.update()

    current_height = 0.0
    for obj in text_objs.values():
        if obj.dimensions.y > 0:
            current_height = obj.dimensions.y
            break
    if current_height == 0:
        current_height = 5.0

    title_size = tp3d.textSizeTitle or tp3d.textSize
    body_size = tp3d.textSize

    for name, obj in text_objs.items():
        s = (title_size if name == "t_name" else body_size) / current_height
        obj.scale.x *= s
        obj.scale.y *= s

    bpy.ops.object.select_all(action="DESELECT")
    for obj in text_objs.values():
        obj.select_set(True)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)


def _substitute_text(text_objs, active_fields):
    """Run replaceShapeText only — needs the object to still be a FONT."""
    values = _field_values()
    for name, obj in text_objs.items():
        if name not in active_fields:
            continue
        field_text, _icon_id, _is_title = values[name]
        replaceShapeText(field_text, obj)


def _attach_icons(text_objs, plate_obj, active_fields):
    """Run textIcon only — needs the text at its final world position so
    the icon's relative placement math works out."""
    values = _field_values()
    tp3d = bpy.context.scene.tp3d
    icons = {}
    for name, obj in text_objs.items():
        if name not in active_fields:
            continue
        _field_text, icon_id, is_title = values[name]
        if icon_id == "no":
            icons[name] = None
            continue
        icon_size = (tp3d.textSizeTitle or tp3d.textSize) if is_title else tp3d.textSize
        icons[name] = textIcon(icon_id, obj, plate_obj, False, icon_size)
    return icons


def _delete_icon_bottoms(icon_obj):
    """Delete bottom faces of an icon by Z so it sits flush on the plate."""
    import bmesh

    bpy.ops.object.select_all(action="DESELECT")
    icon_obj.select_set(True)
    bpy.context.view_layer.objects.active = icon_obj
    bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="MEDIAN")

    bm = bmesh.new()
    bm.from_mesh(icon_obj.data)
    if bm.verts:
        min_z = min(v.co.z for v in bm.verts)
        faces_to_del = [f for f in bm.faces if f.calc_center_median().z < (min_z + 0.3)]
        bmesh.ops.delete(bm, geom=faces_to_del, context="FACES")
        bm.to_mesh(icon_obj.data)
    bm.free()
    icon_obj.data.update()


def _finalize(text_objs, icons, plate_obj, modelname):
    """Join all text + icon objects into one, name it, apply plate rotation."""
    tp3d = bpy.context.scene.tp3d

    bpy.ops.object.select_all(action="DESELECT")
    active_obj = None
    for obj in text_objs.values():
        obj.select_set(True)
        if active_obj is None:
            active_obj = obj
    for icon in icons.values():
        if icon is not None:
            icon.select_set(True)
    if active_obj is None:
        return None
    bpy.context.view_layer.objects.active = active_obj

    bpy.ops.object.join()
    bpy.ops.object.origin_set(type="ORIGIN_CURSOR", center="MEDIAN")

    active_obj.name = modelname + "_Text"
    active_obj.data.name = active_obj.name

    active_obj["Object type"] = "TEXT"
    active_obj["ExportGroup"] = plate_obj.get("ExportGroup", 0) if plate_obj else 0

    _white = bpy.data.materials.get("WHITE")
    if _white is not None:
        active_obj.data.materials.clear()
        active_obj.data.materials.append(_white)

    if plate_obj is not None:
        plate_obj.name = modelname + "_Plate"
        plate_obj.data.name = plate_obj.name
        plate_obj.rotation_euler[2] += tp3d.shapeRotation * (math.pi / 180)
        bpy.ops.object.select_all(action="DESELECT")
        plate_obj.select_set(True)
        bpy.context.view_layer.objects.active = plate_obj
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
        bpy.ops.object.origin_set(type="ORIGIN_CURSOR", center="MEDIAN")

    return active_obj


def _inset_into_plate(
    plate_obj, text_objs, icons, inset_depth, margin=0.1, modelname=""
):
    import bmesh

    if plate_obj is None or not text_objs:
        return None

    bpy.context.view_layer.update()

    sources = []
    for obj in list(text_objs.values()) + list(icons.values()):
        if obj is None:
            continue
        try:
            _ = obj.name
        except (ReferenceError, RuntimeError):
            continue
        if obj.type == "MESH":
            sources.append(obj)

    if len(sources) < 2:
        print(f"[inset] only {len(sources)} mesh source(s) — skipping.")
        return None

    # --- Work entirely in the plate's LOCAL frame ---
    plate_inv = plate_obj.matrix_world.inverted()

    plate_local_top_z = max(v.co.z for v in plate_obj.data.vertices)

    merged = bmesh.new()
    for src in sources:
        tmp = bmesh.new()
        tmp.from_mesh(src.data)
        rel = plate_inv @ src.matrix_world  # small translation only
        bmesh.ops.transform(tmp, matrix=rel, verts=tmp.verts[:])

        if tmp.verts:
            # Align THIS source's top to a common Z before merging.
            # Without this, the uniform Z remap further down only places
            # the source that owns the merged mesh's global max-Z — every
            # other source lands below, which is the "one icon flush,
            # everything else floating" symptom. Pre-aligning here means
            # the uniform remap maps every top to plate_top.
            src_max_z = max(v.co.z for v in tmp.verts)
            shift = plate_local_top_z - src_max_z
            for v in tmp.verts:
                v.co.z += shift

        scratch = bpy.data.meshes.new("_tp3d_merge_scratch")
        tmp.to_mesh(scratch)
        tmp.free()
        merged.from_mesh(scratch)
        bpy.data.meshes.remove(scratch)

    for src in sources:
        bpy.data.objects.remove(src, do_unlink=True)

    # Cleanup in the small-coordinate frame.
    bmesh.ops.remove_doubles(merged, verts=merged.verts[:], dist=1e-5)
    bmesh.ops.dissolve_degenerate(merged, dist=1e-5, edges=merged.edges[:])
    bmesh.ops.recalc_face_normals(merged, faces=merged.faces[:])

    if not merged.verts:
        merged.free()
        return None

    # Plate's local top: _build_prism builds the plate with its top at
    # local Z=0 and bottom at -thickness. plate_obj.location.z carries the
    # world height. So in local coords, the top surface is just Z=0.
    plate_local_top_z = max(v.co.z for v in plate_obj.data.vertices)

    cur_min = min(v.co.z for v in merged.verts)
    cur_max = max(v.co.z for v in merged.verts)
    cur_extent = cur_max - cur_min
    if cur_extent < 1e-6:
        merged.free()
        print("[inset] merged mesh has zero Z extent — aborting.")
        return None

    # =============================================================
    # Text mesh — spans local Z [plate_top - inset_depth, plate_top]
    # =============================================================
    text_scale_z = inset_depth / cur_extent
    for v in merged.verts:
        v.co.z = (v.co.z - cur_min) * text_scale_z + (plate_local_top_z - inset_depth)

    text_mesh = bpy.data.meshes.new("_InsetText")
    merged.to_mesh(text_mesh)

    text_obj = bpy.data.objects.new(modelname + "_Text", text_mesh)
    bpy.context.collection.objects.link(text_obj)
    # Same world placement as the plate — its mesh was built in plate's
    # local frame, so its object matrix must match.
    text_obj.matrix_world = plate_obj.matrix_world.copy()

    text_obj["Object type"] = "TEXT"
    text_obj["ExportGroup"] = plate_obj.get("ExportGroup", 0)
    _white = bpy.data.materials.get("WHITE")
    if _white is not None:
        text_obj.data.materials.clear()
        text_obj.data.materials.append(_white)

    # =============================================================
    # Cutter mesh — spans local Z [plate_top - inset_depth, plate_top + margin]
    # =============================================================
    cutter_bm = bmesh.new()
    cutter_bm.from_mesh(text_mesh)

    bottom = plate_local_top_z - inset_depth
    target_span = inset_depth + margin
    scale = target_span / inset_depth
    for v in cutter_bm.verts:
        v.co.z = (v.co.z - bottom) * scale + bottom

    cutter_mesh = bpy.data.meshes.new("_InsetCutter")
    cutter_bm.to_mesh(cutter_mesh)
    cutter_bm.free()

    cutter = bpy.data.objects.new("_InsetCutter", cutter_mesh)
    bpy.context.collection.objects.link(cutter)
    cutter.matrix_world = plate_obj.matrix_world.copy()

    merged.free()

    # =============================================================
    # Boolean difference — modifier handles world alignment itself,
    # at double precision, without baking anything into vertices.
    # =============================================================
    bpy.ops.object.select_all(action="DESELECT")
    plate_obj.select_set(True)
    bpy.context.view_layer.objects.active = plate_obj

    mod = plate_obj.modifiers.new(name="InsetBoolean", type="BOOLEAN")
    mod.operation = "DIFFERENCE"
    mod.object = cutter
    mod.solver = "EXACT"

    bpy.ops.object.modifier_apply(modifier=mod.name)

    if any(m.name == mod.name for m in plate_obj.modifiers):
        print("[inset] solver refused — cleaning up.")
        bpy.ops.object.modifier_remove(modifier=mod.name)
        if not bpy.app.debug:
            bpy.data.objects.remove(cutter, do_unlink=True)
        return None

    if not bpy.app.debug:
        bpy.data.objects.remove(cutter, do_unlink=True)

    print(
        f"[inset] carved at local plate_top={plate_local_top_z:.2f} "
        f"depth={inset_depth:.2f}"
    )
    return text_obj


def _inset_into_side_wall(plate_obj, text_objs, icons, margin=0.1, modelname=""):
    """Radial counterpart to _inset_into_plate.

    Carves each text/icon shape into the plate/shell's SIDE wall -- radially
    outward from the plate's vertical center axis, not vertically down. Same
    pattern as the Z version (merge sources, build a cutter that overshoots
    past the surface, boolean DIFFERENCE), but the embed axis is the radial
    unit vector at each vertex, so extending the cutter is a per-vertex
    radial rescale rather than one global Z remap.
    """
    import bmesh

    if plate_obj is None or not text_objs:
        return None

    bpy.context.view_layer.update()

    center = plate_obj.matrix_world.translation.copy()
    center.z = 0.0  # radial math is horizontal only

    sources = []
    for obj in list(text_objs.values()) + list(icons.values()):
        if obj is None:
            continue
        try:
            _ = obj.name
        except (ReferenceError, RuntimeError):
            continue
        if obj.type == "MESH":
            sources.append(obj)

    if not sources:
        return None

    # --- Merge every text/icon mesh into one bmesh, in world space ---
    merged = bmesh.new()
    for src in sources:
        tmp = bmesh.new()
        tmp.from_mesh(src.data)
        bmesh.ops.transform(tmp, matrix=src.matrix_world, verts=tmp.verts[:])
        scratch = bpy.data.meshes.new("_tp3d_merge_scratch")
        tmp.to_mesh(scratch)
        tmp.free()
        merged.from_mesh(scratch)
        bpy.data.meshes.remove(scratch)

    if not merged.verts:
        merged.free()
        return None

    # Radial span of the merged text -- apply_front_face_layout should have
    # placed the text so its front face sits right at the wall's outer
    # radius, so r_max is the wall surface for these fields' angular range.
    radial_r = [math.hypot(v.co.x - center.x, v.co.y - center.y) for v in merged.verts]
    r_min, r_max = min(radial_r), max(radial_r)
    r_extent = r_max - r_min
    if r_extent < 1e-6:
        merged.free()
        return None

    # --- Text mesh: the visible fill in the groove, at original position ---
    text_mesh = bpy.data.meshes.new("_InsetSideText")
    merged.to_mesh(text_mesh)
    merged.free()

    text_obj = bpy.data.objects.new(modelname + "_Text", text_mesh)
    bpy.context.collection.objects.link(text_obj)
    _white = bpy.data.materials.get("WHITE")
    if _white is not None:
        text_obj.data.materials.clear()
        text_obj.data.materials.append(_white)
    text_obj["Object type"] = "TEXT"
    text_obj["ExportGroup"] = plate_obj.get("ExportGroup", 0)

    # --- Cutter: same shape, front pushed radially outward by margin.
    # Rescale around r_min so the base stays put and only the front grows
    # outward -- a uniform outward translate would move the base too and
    # make the cut shallower than intended.
    cutter_bm = bmesh.new()
    cutter_bm.from_mesh(text_mesh)
    stretch = (r_extent + margin) / r_extent
    for v in cutter_bm.verts:
        dx = v.co.x - center.x
        dy = v.co.y - center.y
        r = math.hypot(dx, dy)
        if r < 1e-9:
            continue
        new_r = r_min + (r - r_min) * stretch
        s = new_r / r
        v.co.x = center.x + dx * s
        v.co.y = center.y + dy * s
    cutter_mesh = bpy.data.meshes.new("_InsetSideCutter")
    cutter_bm.to_mesh(cutter_mesh)
    cutter_bm.free()
    cutter_obj = bpy.data.objects.new("_InsetSideCutter", cutter_mesh)
    bpy.context.collection.objects.link(cutter_obj)

    # --- Boolean DIFFERENCE against the plate/shell ---
    bpy.ops.object.select_all(action="DESELECT")
    plate_obj.select_set(True)
    bpy.context.view_layer.objects.active = plate_obj
    mod = plate_obj.modifiers.new(name="InsetSideBoolean", type="BOOLEAN")
    mod.operation = "DIFFERENCE"
    mod.object = cutter_obj
    mod.solver = "EXACT"
    bpy.ops.object.modifier_apply(modifier=mod.name)
    if any(m.name == mod.name for m in plate_obj.modifiers):
        print("[inset-side] solver refused — cleaning up.")
        bpy.ops.object.modifier_remove(modifier=mod.name)
        bpy.data.objects.remove(cutter_obj, do_unlink=True)
        bpy.data.objects.remove(text_obj, do_unlink=True)
        return None
    bpy.data.objects.remove(cutter_obj, do_unlink=True)

    for src in sources:
        try:
            bpy.data.objects.remove(src, do_unlink=True)
        except (ReferenceError, RuntimeError):
            pass

    print(
        f"[inset-side] carved radially, r_min={r_min:.2f} r_max={r_max:.2f} "
        f"margin={margin:.2f}"
    )
    return text_obj


def _import_transform_map_object():
    # Deferred to avoid circular import at load time.
    from . import transform_MapObject  # type: ignore

    return transform_MapObject


# ---------------------------------------------------------------------------
# Layouts
# ---------------------------------------------------------------------------


def apply_outer_edge_layout(map_obj, plate_obj, shape, inset=False, inset_depth=0.8):
    from .text_objects import create_text as _create_text  # local alias

    fields = OUTER_EDGE_FIELDS.get(shape)
    if not fields:
        return None

    tp3d = bpy.context.scene.tp3d
    transform_MapObject = _import_transform_map_object()

    inner, outer = _ring_radii(map_obj, plate_obj, shape)
    dist = (inner + outer) / 2

    text_objs = {}
    for field_name, base_angle, flip in fields:
        angle = base_angle + tp3d.text_angle_preset
        rot_z = math.radians(angle + 90) + (math.pi if flip else 0)
        angle_rad = math.radians(angle)
        r_at_angle = _polygon_radius_at_angle(dist, shape, angle_rad)
        x = math.cos(angle_rad) * r_at_angle
        y = math.sin(angle_rad) * r_at_angle
        text_objs[field_name] = _create_text(
            field_name,
            field_name.split("_")[1].capitalize(),
            (x, y, 1.4),
            1,
            (0, 0, rot_z),
            0.4,
        )

    _scale_to_mm(text_objs)
    active = set(text_objs.keys())
    _substitute_text(text_objs, active)

    for name, obj in text_objs.items():
        text_objs[name] = convert_text_to_mesh_obj(obj)
        transform_MapObject(text_objs[name], tp3d.o_centerx, tp3d.o_centery)

    icons = _attach_icons(text_objs, plate_obj, active)

    if inset:
        inset_text = _inset_into_plate(
            plate_obj, text_objs, icons, inset_depth, modelname=tp3d.modelname
        )
        return inset_text

    if any(ic is not None for ic in icons.values()):
        bpy.ops.object.select_all(action="DESELECT")
        for icon in icons.values():
            if icon is not None:
                icon.select_set(True)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    return _finalize(text_objs, icons, plate_obj, tp3d.modelname)


def _polygon_radius_at_angle(inradius, shape, angle_rad):
    """Distance from center to a regular polygon's boundary at angle_rad.

    inradius is the edge-midpoint distance (what _ring_radii returns).
    Assumes the primitive's first vertex lies on the +X axis, matching
    Blender's primitive_circle_add -- corners at k * (2π/n).
    """
    n_lookup = {"HEXAGON": 6, "OCTAGON": 8}
    n = n_lookup.get(shape)
    if n is None:
        return inradius

    half_step = math.pi / n
    circumradius = inradius / math.cos(half_step)
    deviation = (angle_rad % (2 * half_step)) - half_step
    return circumradius * math.cos(half_step) / math.cos(deviation)


def apply_front_face_layout(map_obj, plate_obj, shape, inset):
    fields = FRONT_FACE_FIELDS.get(shape)
    if not fields:
        return None

    tp3d = bpy.context.scene.tp3d
    transform_MapObject = _import_transform_map_object()

    _inner, outer = _ring_radii(map_obj, plate_obj, shape)

    bpy.context.view_layer.update()

    _inset = 0.4
    mw = plate_obj.matrix_world
    zs = [(mw @ v.co).z for v in plate_obj.data.vertices]
    z_min, z_max = min(zs), max(zs)
    z_pos = z_min + (z_max - z_min) * 0.5

    text_objs = {}

    for field_name, base_angle in fields:
        angle = base_angle + tp3d.text_angle_preset
        angle_rad = math.radians(angle)
        r_at_angle = _polygon_radius_at_angle(outer, shape, angle_rad)
        dist = (r_at_angle - _inset) if inset else (r_at_angle + _inset)
        rot_z = math.radians(angle + 90)
        x = math.cos(angle_rad) * dist
        y = math.sin(angle_rad) * dist
        obj = create_text(
            field_name,
            field_name.split("_")[1].capitalize(),
            (x, y, z_pos),
            1,
            (math.radians(90), 0, rot_z),
            _inset,
        )
        obj.location.z = z_pos
        text_objs[field_name] = obj

    _scale_to_mm(text_objs)
    active = set(text_objs.keys())
    _substitute_text(text_objs, active)

    for name, obj in text_objs.items():
        convert_text_to_mesh(name, plate_obj.name, False)
        transform_MapObject(
            obj,
            tp3d.o_centerx + tp3d.xTerrainOffset,
            tp3d.o_centery + tp3d.yTerrainOffset,
        )

    icons = _attach_icons(text_objs, plate_obj, active)

    if inset:
        return _inset_into_side_wall(
            plate_obj, text_objs, icons, modelname=tp3d.modelname
        )

    if any(ic is not None for ic in icons.values()):
        bpy.ops.object.select_all(action="DESELECT")
        for icon in icons.values():
            if icon is not None:
                icon.select_set(True)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    return _finalize(text_objs, icons, plate_obj, tp3d.modelname)


def apply_curved_ring_layout(map_obj, plate_obj, shape, inset=False, inset_depth=0.8):
    import bmesh

    fields = CURVED_RING_FIELDS.get(shape)
    if not fields:
        return None

    tp3d = bpy.context.scene.tp3d
    transform_MapObject = _import_transform_map_object()

    inner, outer = _ring_radii(map_obj, plate_obj, shape)
    text_radius = (inner + outer) / 2

    # --- 1. Create all labels flat at origin, no rotation ---
    text_objs = {}
    for field_name, _base in fields:
        text_objs[field_name] = create_text(
            field_name,
            field_name.split("_")[1].capitalize(),
            (0, 0, 1.4),
            1,
            (0, 0, 0),
            0.4,
        )

    _scale_to_mm(text_objs)
    active = set(text_objs.keys())
    _substitute_text(text_objs, active)

    # --- 2. Attach icons while text is still flat ---
    # appendTextIcon reads the text's location/rotation to place the icon
    # and shifts the text sideways to make room. Both happen while the
    # label is still at (0, 0, 1.4) with no rotation, so the shift gets
    # baked in by the transform_apply below.
    icons = _attach_icons(text_objs, plate_obj, active)

    # --- 3. Per-field: convert, bake, measure anchor, join icon, wrap ---
    for field_name, base_deg in fields:
        obj = text_objs[field_name]

        # convert_text_to_mesh_obj — object-based, no bpy.data.objects.get
        # name lookup. The name-based version silently targets a stale
        # object when Blender has suffixed the fresh one, which is why the
        # original call left a FONT in place and transform_apply rejected it.
        text_objs[field_name] = convert_text_to_mesh_obj(obj)
        obj = text_objs[field_name]

        # Bake the icon-attachment shift into the mesh and zero the object
        # transform so the anchor measurement below is in local coords.
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.transform_apply(
            location=True,
            rotation=False,
            scale=False,
        )

        # Measure the text's own center BEFORE the icon joins, so an
        # asymmetric icon can't drag the anchor sideways.
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        xs = [v.co.x for v in bm.verts]
        ys = [v.co.y for v in bm.verts]
        anchor_x = (min(xs) + max(xs)) / 2 if xs else 0.0
        anchor_y = (min(ys) + max(ys)) / 2 if ys else 0.0
        bm.free()

        icon = icons.get(field_name)
        if icon is not None:
            bpy.ops.object.select_all(action="DESELECT")
            icon.select_set(True)
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.join()

        # Bend the finished icon+text unit around the plate ring as a
        # single rigid piece. All four fields are bent separately, each
        # centered on its own base angle.
        angle_deg = base_deg + tp3d.text_angle_preset
        upper = math.sin(math.radians(angle_deg)) > 0
        wrap_mesh_around_circle(
            obj,
            text_radius,
            math.radians(angle_deg),
            upper,
            anchor_x,
            anchor_y,
        )
        transform_MapObject(obj, tp3d.o_centerx, tp3d.o_centery)

    if inset:
        # Icons already joined into each text — pass an empty dict so
        # _inset_into_plate only collects the (already-merged) text meshes.
        inset_text = _inset_into_plate(
            plate_obj,
            text_objs,
            {},
            inset_depth,
            modelname=tp3d.modelname,
        )
        return inset_text

    # Same here: icons are inside the text meshes now, so {} for icons.
    return _finalize(text_objs, {}, plate_obj, tp3d.modelname)


def apply_on_map_layout(map_obj, shape):
    from . import projection  # deferred

    fields = ON_MAP_FIELDS.get(shape)
    if not fields:
        return None

    tp3d = bpy.context.scene.tp3d
    transform_MapObject = _import_transform_map_object()

    size = tp3d.objSize
    dist = size / 2 - size / 2 * (1 - 0.8) / 2
    temp_y = math.sin(math.radians(90)) * (dist * math.cos(math.radians(30)))

    text_objs = {}
    text_objs["t_name"] = create_text("t_name", "Name", (0, temp_y, 0.1), 1)

    for field_name, default_label, base_angle in fields:
        if field_name == "t_name":
            continue
        angle = base_angle
        rot_z = math.radians(angle + 90)
        x = math.cos(math.radians(angle)) * (dist * math.cos(math.radians(30)))
        y = math.sin(math.radians(angle)) * (dist * math.cos(math.radians(30)))
        text_objs[field_name] = create_text(
            field_name,
            default_label,
            (x, y, 0.1),
            1,
            (0, 0, rot_z),
            100,
        )

    for obj in text_objs.values():
        transform_MapObject(
            obj,
            tp3d.o_centerx + tp3d.xTerrainOffset,
            tp3d.o_centery + tp3d.yTerrainOffset,
        )

    _scale_to_mm(text_objs)
    active = set(text_objs.keys())
    _substitute_text(text_objs, active)

    for obj in text_objs.values():
        projection("separate", map_obj, obj)
    icons = _attach_icons(text_objs, map_obj, set(text_objs.keys()))
    for icon in icons.values():
        if icon is None:
            continue
        projection("separate", map_obj, icon)

    return _finalize(text_objs, icons, None, tp3d.modelname)


def fields_for_layout(layout, shape):
    """Property names in the order the field table lists them — the panel
    iterates this list to render rows in the same physical order around
    the shape."""
    table = {
        "OUTER_EDGE": OUTER_EDGE_FIELDS,
        "FRONT_FACE": FRONT_FACE_FIELDS,
        "CURVED": CURVED_RING_FIELDS,
        "ON_MAP": ON_MAP_FIELDS,
    }.get(layout, {})
    return [
        _FIELD_TO_PROP[name]
        for name, *_ in table.get(shape, [])
        if name in _FIELD_TO_PROP
    ]


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def apply_text_layout(map_obj, plate_obj, layout, shape, inset=False, inset_depth=0.8):
    if layout == "OUTER_EDGE":
        return apply_outer_edge_layout(map_obj, plate_obj, shape, inset, inset_depth)
    if layout == "FRONT_FACE":
        # Not supported: front-face extrusion runs along the plate's
        # outward normal (horizontal), so an inset would need a radial
        # carve, not a vertical one. Falls back to raised.
        return apply_front_face_layout(map_obj, plate_obj, shape, inset)
    if layout == "CURVED":
        return apply_curved_ring_layout(map_obj, plate_obj, shape, inset, inset_depth)
    if layout == "ON_MAP":
        # ON_MAP already carves into the map surface via projection; the
        # inset flag is a no-op there.
        return apply_on_map_layout(map_obj, shape)
    return None
