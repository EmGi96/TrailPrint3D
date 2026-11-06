import bpy  # type: ignore
from mathutils import Color  # type: ignore


COLOR_DECODED_PROP = "tp3d_color_decoded"


def srgb_to_material_color(rgb8: tuple[int, int, int]) -> tuple[float, float, float, float]:
    """Convert an sRGB 8-bit RGB color to scene-linear RGBA."""
    color = Color(tuple(channel / 255.0 for channel in rgb8)).from_srgb_to_scene_linear()
    return (color.r, color.g, color.b, 1.0)


def set_material_srgb(material: bpy.types.Material, rgb8: tuple[int, int, int]) -> None:
    """Write an sRGB 8-bit color to a material and mark it as scene-linear."""
    rgba = srgb_to_material_color(rgb8)
    material.diffuse_color = rgba

    if material.use_nodes and material.node_tree:
        bsdf = next(
            (node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"),
            None,
        )
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = rgba

    material[COLOR_DECODED_PROP] = True


def material_to_srgb(
    material: bpy.types.Material | None,
    fallback: tuple[int, int, int] = (0, 0, 0),
) -> tuple[int, int, int]:
    """Read a material's Base Color as sRGB bytes.

    Tagged materials use the scene-linear-to-sRGB conversion. Untagged
    materials retain the legacy plain-scale interpretation so saved files
    keep their existing texture and palette colors until their materials are
    rewritten.
    """
    if material is None or not material.use_nodes:
        return fallback

    bsdf = next(
        (node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"),
        None,
    )
    if bsdf is None:
        return fallback

    base_color = bsdf.inputs["Base Color"].default_value
    if material.get(COLOR_DECODED_PROP):
        srgb = Color(base_color[:3]).from_scene_linear_to_srgb()
        return (
            round(max(0.0, min(1.0, srgb.r)) * 255.0),
            round(max(0.0, min(1.0, srgb.g)) * 255.0),
            round(max(0.0, min(1.0, srgb.b)) * 255.0),
        )

    return (
        round(max(0.0, min(1.0, base_color[0])) * 255.0),
        round(max(0.0, min(1.0, base_color[1])) * 255.0),
        round(max(0.0, min(1.0, base_color[2])) * 255.0),
    )
