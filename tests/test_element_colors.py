"""Scene color style and recolor tests.

Run with:
  blender --background --factory-startup --python-exit-code 1 -P tests/test_element_colors.py
"""

import json
import os
import sys
import tempfile
import traceback

import bpy
import numpy as np

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

if "TrailPrint3D" not in bpy.context.preferences.addons:
    bpy.ops.preferences.addon_enable(module="TrailPrint3D")

from TrailPrint3D import constants
from TrailPrint3D.utils import color_styles, presets, primitives, texture
from TrailPrint3D.utils.color import material_to_srgb

_passed = 0
_failed = 0
_TEMP_DIR = tempfile.TemporaryDirectory()
constants.color_style_dir = os.path.join(_TEMP_DIR.name, "color_styles")
constants.preset_dir = os.path.join(_TEMP_DIR.name, "presets")


def _run(name, fn):
    global _passed, _failed
    _reset_scene_colors()
    try:
        fn()
        print(f"  PASS  {name}")
        _passed += 1
    except Exception:
        print(f"  FAIL  {name}")
        traceback.print_exc()
        _failed += 1


def _tp3d():
    return bpy.context.scene.tp3d


def _reset_scene_colors():
    tp3d = _tp3d()
    tp3d.color_style = color_styles.DEFAULT_STYLE
    color_styles.set_scene_colors(tp3d, color_styles.default_colors())
    primitives.setupColors()


def _hex(rgb):
    return color_styles.srgb_to_hex(rgb)


def _paint_mesh(name, pixels_rgb, roles=None, palette=None):
    mesh = bpy.data.meshes.new(name)
    image = bpy.data.images.new(f"{mesh.name}_MMU_Paint", width=len(pixels_rgb), height=1)
    flat = []
    for rgb in pixels_rgb:
        flat.extend([channel / 255.0 for channel in rgb] + [1.0])
    image.pixels.foreach_set(flat)
    mesh["3mf_is_paint_texture"] = True
    mesh["3mf_paint_extruder_colors"] = str(
        palette or {index: _hex(rgb) for index, rgb in enumerate(pixels_rgb)}
    )
    if roles is not None:
        color_styles.write_mesh_color_roles(mesh, roles)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return mesh, image


def _remove_paint_mesh(mesh):
    for obj in list(bpy.data.objects):
        if obj.type == "MESH" and obj.data == mesh:
            bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.meshes.remove(mesh)


def _image_rgb(image):
    pixels = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(pixels)
    return [
        tuple(int(round(channel * 255.0)) for channel in pixel[:3])
        for pixel in pixels.reshape(-1, 4)
    ]


def test_scene_colors_drive_materials():
    tp3d = _tp3d()
    tp3d.element_color_base = color_styles.srgb_to_color((12, 34, 56))
    tp3d.element_color_roads = color_styles.srgb_to_color((210, 90, 30))
    primitives.setupColors()

    assert material_to_srgb(bpy.data.materials["BASE"]) == (12, 34, 56)
    assert material_to_srgb(bpy.data.materials["ROADS"]) == (210, 90, 30)
    assert material_to_srgb(bpy.data.materials["BLACK"]) == (0, 0, 0)
    assert constants._KIND_MATERIAL_NAME["ROADS"] == "ROADS"


def test_built_in_styles_are_complete():
    expected = {"FOREST", "URBAN", "DESERT", "SNOW"}
    assert set(color_styles.BUILT_IN_STYLES) == expected
    for _label, _description, colors in color_styles.BUILT_IN_STYLES.values():
        assert set(colors) == set(color_styles.ROLE_NAMES)
        color_styles.normalize_colors(colors)


def test_style_selection_reset_and_modified_state():
    tp3d = _tp3d()
    tp3d.color_style = "DESERT"
    desert = color_styles.style_colors("DESERT")
    assert color_styles.scene_colors(tp3d) == desert
    assert not color_styles.style_is_modified(tp3d)

    tp3d.element_color_water = color_styles.srgb_to_color((1, 2, 3))
    assert color_styles.style_is_modified(tp3d)
    assert bpy.ops.tp3d.reset_color_style() == {"FINISHED"}
    assert color_styles.scene_colors(tp3d) == desert


def test_user_style_json_round_trip():
    tp3d = _tp3d()
    tp3d.element_color_city = color_styles.srgb_to_color((10, 20, 30))
    tp3d.color_style_name = "My Coast"
    assert bpy.ops.tp3d.save_color_style() == {"FINISHED"}
    assert tp3d.color_style == "USER:My Coast"

    path = os.path.join(constants.color_style_dir, "My Coast.json")
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    assert data["version"] == color_styles.STYLE_VERSION
    assert data["colors"]["CITY"] == "#0A141E"

    tp3d.color_style = color_styles.DEFAULT_STYLE
    tp3d.color_style = "USER:My Coast"
    assert color_styles.scene_colors(tp3d)["CITY"] == (10, 20, 30)

    assert bpy.ops.tp3d.delete_color_style() == {"FINISHED"}
    assert not os.path.exists(path)
    assert tp3d.color_style == color_styles.DEFAULT_STYLE
    assert color_styles.scene_colors(tp3d)["CITY"] == (10, 20, 30)


def test_invalid_and_reserved_style_names_are_rejected():
    for name in ("", "bad/name", "Desert", "FOREST", "x" * 65):
        try:
            color_styles.save_user_style(name, color_styles.default_colors())
        except ValueError:
            continue
        raise AssertionError(f"accepted invalid style name {name!r}")


def _select_only(*objects):
    for obj in bpy.context.view_layer.objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0] if objects else None


def _object_of(mesh):
    return next(obj for obj in bpy.context.scene.objects if obj.data == mesh)


def test_apply_recolors_paint_texture():
    old = color_styles.default_colors()
    mesh, image = _paint_mesh(
        "RecolorTexture",
        [old["BASE"], old["WATER"], old["ROADS"], (0, 0, 0), (1, 1, 1), tuple(c + 1 for c in old["BASE"])],
        roles=old,
        palette={0: _hex(old["BASE"]), 1: _hex(old["WATER"]), 2: "#000000", 3: "#FFFFFF", 4: "#FF0000"},
    )
    try:
        new = color_styles.style_colors("URBAN")
        color_styles.set_scene_colors(_tp3d(), new)
        _select_only(_object_of(mesh))
        assert bpy.ops.tp3d.apply_color_style() == {"FINISHED"}

        pixels = _image_rgb(image)
        # BLACK roads are indistinguishable from the BLACK utility colour in the
        # texture, so both move with ROADS; the ±3 tolerance catches near-BASE.
        assert pixels == [
            new["BASE"], new["WATER"], new["ROADS"], new["ROADS"], new["ROADS"], new["BASE"],
        ]
        palette = eval(mesh["3mf_paint_extruder_colors"])
        assert palette[0] == _hex(new["BASE"])
        assert palette[1] == _hex(new["WATER"])
        assert "#000000" in palette.values()
        assert _hex(new["TRAIL"]) in palette.values()
        assert len(palette.values()) == len(set(palette.values()))
        assert color_styles.read_mesh_color_roles(mesh, {}) == new
    finally:
        bpy.data.images.remove(image)
        _remove_paint_mesh(mesh)


def test_legacy_texture_uses_material_colors():
    old = color_styles.default_colors()
    mesh, image = _paint_mesh("LegacyTexture", [old["BASE"], old["FOREST"]])
    try:
        assert color_styles.COLOR_ROLES_PROP not in mesh
        new = color_styles.style_colors("DESERT")
        color_styles.set_scene_colors(_tp3d(), new)
        _select_only(_object_of(mesh))
        assert bpy.ops.tp3d.apply_color_style() == {"FINISHED"}
        assert _image_rgb(image) == [new["BASE"], new["FOREST"]]
        assert color_styles.COLOR_ROLES_PROP in mesh
    finally:
        bpy.data.images.remove(image)
        _remove_paint_mesh(mesh)


def _element_object(name, material_name):
    mesh = bpy.data.meshes.new(name)
    mesh.materials.append(bpy.data.materials[material_name])
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def test_apply_recolors_only_selected_materials():
    old = color_styles.default_colors()
    selected = _element_object("SelectedWater", "WATER")
    other = _element_object("OtherWater", "WATER")
    copy = None
    try:
        new = color_styles.style_colors("SNOW")
        color_styles.set_scene_colors(_tp3d(), new)
        _select_only(selected)
        assert bpy.ops.tp3d.apply_color_style() == {"FINISHED"}

        copy = selected.material_slots[0].material
        assert copy.name != "WATER"
        assert color_styles.material_role(copy) == "WATER"
        assert material_to_srgb(copy) == new["WATER"]
        assert other.material_slots[0].material == bpy.data.materials["WATER"]
        assert material_to_srgb(bpy.data.materials["WATER"]) == old["WATER"]

        # A copy only the selection uses is recolored in place, not copied again.
        color_styles.set_scene_colors(_tp3d(), color_styles.style_colors("DESERT"))
        assert bpy.ops.tp3d.apply_color_style() == {"FINISHED"}
        assert selected.material_slots[0].material == copy
        assert material_to_srgb(copy) == color_styles.style_colors("DESERT")["WATER"]
    finally:
        for obj in (selected, other):
            mesh = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.meshes.remove(mesh)
        if copy is not None and copy.name != "WATER":
            bpy.data.materials.remove(copy)


def test_apply_without_selection_is_disabled():
    _select_only()
    assert not bpy.ops.tp3d.apply_color_style.poll()


def test_shared_colors_report_collision():
    old = color_styles.default_colors()
    old["FOREST"] = old["WATER"]
    new = color_styles.default_colors()
    new["WATER"] = (1, 2, 3)
    new["FOREST"] = (4, 5, 6)
    mapping, collisions = texture.color_role_mapping(old, new)
    assert mapping[_hex(old["WATER"])] == "#010203"
    assert collisions == {_hex(old["WATER"])}


def test_recolor_ignores_deleted_scene_objects():
    old = color_styles.default_colors()
    new = color_styles.style_colors("SNOW")
    mesh, image = _paint_mesh("DeletedTexture", [old["BASE"]], roles=old)
    image_name = image.name
    obj = next(obj for obj in bpy.context.scene.objects if obj.data == mesh)
    bpy.data.objects.remove(obj, do_unlink=True)
    try:
        stats = texture.recolor_paint_textures(new, old, bpy.context.scene.objects)
        assert stats["meshes"] == 0
        assert _image_rgb(image) == [old["BASE"]]
        assert color_styles.read_mesh_color_roles(mesh, {}) == old
        bpy.data.images.remove(image)
        stats = texture.recolor_paint_textures(new, old, bpy.context.scene.objects)
        assert mesh.name not in stats["missing"]
    finally:
        if image_name in bpy.data.images:
            bpy.data.images.remove(bpy.data.images[image_name])
        bpy.data.meshes.remove(mesh)


def test_height_bake_undo_history_is_recolored():
    old = color_styles.default_colors()
    mesh, image = _paint_mesh("HistoryTexture", [old["BASE"], old["WATER"]], roles=old)
    try:
        texture.save_height_bake_state(image, mesh)
        new = color_styles.style_colors("SNOW")
        texture.recolor_paint_textures(new, old, bpy.context.scene.objects)
        obj = bpy.data.objects.new("HistoryTexture", mesh)
        assert texture.restore_last_height_bake(obj)
        assert _image_rgb(image) == [new["BASE"], new["WATER"]]
        assert eval(mesh["3mf_paint_extruder_colors"])[0] == _hex(new["BASE"])
        bpy.data.objects.remove(obj)
    finally:
        texture._HEIGHT_BAKE_UNDO.pop(image.name, None)
        bpy.data.images.remove(image)
        _remove_paint_mesh(mesh)


def test_recolor_targets_scene_meshes_once():
    old = color_styles.default_colors()
    new = color_styles.style_colors("SNOW")
    mesh, image = _paint_mesh("SharedTexture", [old["BASE"]], roles=old)
    other_mesh, other_image = _paint_mesh("OtherSceneTexture", [old["WATER"]], roles=old)
    other_scene = bpy.data.scenes.new("OtherColorScene")
    other_obj = next(obj for obj in bpy.context.scene.objects if obj.data == other_mesh)
    other_scene.collection.objects.link(other_obj)
    bpy.context.scene.collection.objects.unlink(other_obj)
    duplicate = bpy.data.objects.new("SharedTextureCopy", mesh)
    bpy.context.scene.collection.objects.link(duplicate)
    unlinked_mesh, unlinked_image = _paint_mesh("UnlinkedTexture", [old["FOREST"]], roles=old)
    unlinked_obj = next(obj for obj in bpy.context.scene.objects if obj.data == unlinked_mesh)
    bpy.context.scene.collection.objects.unlink(unlinked_obj)
    try:
        stats = texture.recolor_paint_textures(new, old, bpy.context.scene.objects)
        assert stats["meshes"] == 1
        assert stats["pixels"] == 1
        assert stats["missing"] == []
        assert _image_rgb(image) == [new["BASE"]]
        assert _image_rgb(other_image) == [old["WATER"]]
        assert _image_rgb(unlinked_image) == [old["FOREST"]]
        assert color_styles.read_mesh_color_roles(other_mesh, {}) == old
        assert color_styles.read_mesh_color_roles(unlinked_mesh, {}) == old
    finally:
        for target_image in (image, other_image, unlinked_image):
            bpy.data.images.remove(target_image)
        for target_mesh in (mesh, other_mesh, unlinked_mesh):
            _remove_paint_mesh(target_mesh)
        bpy.data.scenes.remove(other_scene)


def test_full_preset_round_trip_keeps_custom_colors():
    tp3d = _tp3d()
    tp3d.color_style = "DESERT"
    tp3d.element_color_glacier = color_styles.srgb_to_color((7, 8, 9))
    presets.save_myproperties_to_csv("colors")
    _reset_scene_colors()

    presets.load_myproperties_from_csv("colors")
    assert tp3d.color_style == "DESERT"
    assert color_styles.scene_colors(tp3d)["GLACIER"] == (7, 8, 9)
    assert color_styles.scene_colors(tp3d)["BASE"] == color_styles.style_colors("DESERT")["BASE"]


for _test in (
    test_scene_colors_drive_materials,
    test_built_in_styles_are_complete,
    test_style_selection_reset_and_modified_state,
    test_user_style_json_round_trip,
    test_invalid_and_reserved_style_names_are_rejected,
    test_apply_recolors_paint_texture,
    test_legacy_texture_uses_material_colors,
    test_apply_recolors_only_selected_materials,
    test_apply_without_selection_is_disabled,
    test_shared_colors_report_collision,
    test_recolor_ignores_deleted_scene_objects,
    test_recolor_targets_scene_meshes_once,
    test_height_bake_undo_history_is_recolored,
    test_full_preset_round_trip_keeps_custom_colors,
):
    _run(_test.__name__, _test)

_reset_scene_colors()
_TEMP_DIR.cleanup()
print(f"\n{_passed} passed, {_failed} failed")
if _failed:
    raise SystemExit(1)
