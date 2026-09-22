import time
from typing import Any

import bpy  # type: ignore
from bpy.app.translations import pgettext as _
from bpy.types import Object

from ...progress import ProgressOverlay as _progress
from ..dataclasses import GenerationContext


def _rg_finalize_metadata(
    gen: GenerationContext, start_time: float, lowestZ=None, highestZ=None
) -> float:
    """Write the run-duration text and bump the persistent generation counter.

    Shared tail step for both runGeneration and createTerrainFromSelected --
    lowestZ/highestZ are optional since runGeneration's own displace step
    already writes them to the scene earlier in its own pipeline.
    Returns the elapsed duration in seconds.
    """
    from ..elevation import load_generation_counter, save_generation_counter

    duration = time.time() - start_time
    tp3d = bpy.context.scene.tp3d
    tp3d["o_time"] = _("Script ran for {} seconds").format(round(duration))
    if lowestZ is not None:
        tp3d.lowestZ = lowestZ
    if highestZ is not None:
        tp3d.highestZ = highestZ

    _total_maps = load_generation_counter() + 1
    save_generation_counter(_total_maps)
    tp3d["o_mapsGenerated"] = f"Maps Generated: {_total_maps}"

    return duration


def _rg_apply_texture(gen: GenerationContext):
    from mathutils import Vector  # deferred to avoid circular import at load time

    from ..geometry2d import polylines_to_ribbon
    from ..mesh_ops import (  # deferred to avoid circular import at load time
        merge_with_map,
    )
    from ..scene import (  # deferred to avoid circular import at load time
        remove_objects,
    )
    from ..texture import bake_trail_into_texture, setup_paint_texture

    elements: dict[str, Any] = gen.runtime.elements
    _mmu_palette = setup_paint_texture(gen)
    elements["_mmu_palette"] = _mmu_palette
    # When SCM trail is on, curveObjs hold the converted trail-strip meshes
    # (single_color_mode_curve converts in-place); keep them as 3D geometry.
    # When tex_include_trail is off, keep curveObjs as PAINT-style overlay objects.
    _tex_trail = gen.texture.texTrail
    if gen.runtime.curveObjs and not gen.settings.singleColorMode and _tex_trail:
        tp3d = bpy.context.scene.tp3d
        for tcrv in list(gen.runtime.curveObjs):
            if not (tcrv and tcrv.name in bpy.data.objects):
                continue
            # Rasterize this curve's own footprint into the just-baked terrain
            # texture, using its own material (TRAIL/YELLOW/...) -- setup_paint_texture
            # above has no notion of trail geometry at all (it only knows about
            # _osm_polygons), so without this step the trail's shape and colour
            # would simply vanish the moment its 3D curve is discarded below.
            mw = tcrv.matrix_world
            coords = []
            for sp in tcrv.data.splines:
                pts = sp.points if len(sp.points) > 0 else sp.bezier_points
                if len(pts) >= 2:
                    coords.append([(mw @ Vector((p.co.x, p.co.y, p.co.z)))[:2] for p in pts])
            if coords:
                ribbon = polylines_to_ribbon(
                    coords, tp3d.pathThickness / 2 + tp3d.tolerance, quad_segs=4
                )
                if ribbon is not None and not ribbon.is_empty:
                    mat = tcrv.data.materials[0] if tcrv.data.materials else None
                    bake_trail_into_texture(gen.runtime.mapObject, ribbon, mat)
            bpy.data.objects.remove(tcrv, do_unlink=True)
        gen.runtime.curveObjs.clear()

    if gen.runtime.curveObjs and type == 20:
        for i, crv in enumerate(gen.runtime.curveObjs):
            tmp: Object = merge_with_map(gen.runtime.mapObject, crv)
            remove_objects(crv)
            gen.runtime.curveObjs[i] = tmp


def _rg_assign_materials(gen: GenerationContext):
    """Write metadata and assign materials to all generated objects."""
    from ..metadata import (
        writeMetadata,  # deferred to avoid circular import at load time
    )

    obj = gen.runtime.mapObject
    curveObjs = gen.runtime.curveObjs
    textobj = gen.runtime.textObj
    plateobj = gen.runtime.plateObj
    shellobj = gen.runtime.shellObj
    shape = gen.settings.shape

    bpy.ops.object.select_all(action="DESELECT")

    writeMetadata(obj, "MAP")
    if curveObjs:
        for tcrv in curveObjs:
            try:
                if tcrv and tcrv.name in bpy.data.objects:
                    writeMetadata(tcrv, "TRAIL")
            except ReferenceError:
                pass

    if curveObjs:
        mats = "TRAIL"
        for tcrv in curveObjs:
            try:
                if tcrv and tcrv.name in bpy.data.objects:
                    mat = bpy.data.materials.get(mats)
                    tcrv.data.materials.clear()
                    tcrv.data.materials.append(mat)
                    mats = "YELLOW" if mats == "TRAIL" else "TRAIL"
            except ReferenceError:
                pass

    if (
        shape
        in {
            "HEXAGON INNER TEXT",
            "HEXAGON OUTER TEXT",
            "OCTAGON OUTER TEXT",
            "HEXAGON FRONT TEXT",
            "CIRCLE OUTER TEXT",
        }
        and textobj
    ):
        mat_name = "TRAIL" if shape == "HEXAGON INNER TEXT" else "WHITE"
        mat = bpy.data.materials.get(mat_name)
        textobj.data.materials.clear()
        textobj.data.materials.append(mat)
        writeMetadata(textobj, type="TEXT")

    if (
        shape
        in {
            "HEXAGON OUTER TEXT",
            "OCTAGON OUTER TEXT",
            "HEXAGON FRONT TEXT",
            "CIRCLE OUTER TEXT",
        }
        and plateobj
    ):
        mat = bpy.data.materials.get("BLACK")
        plateobj.data.materials.clear()
        plateobj.data.materials.append(mat)
        writeMetadata(plateobj, type="PLATE")

    if shellobj:
        mat = bpy.data.materials.get("BLACK")
        shellobj.data.materials.clear()
        if mat:
            shellobj.data.materials.append(mat)
        writeMetadata(shellobj, type="SHELL")


def _rg_set_material_preview():
    # --- Material preview mode ---
    for area in bpy.context.screen.areas:
        if area.type == "VIEW_3D":
            for space in area.spaces:
                if space.type == "VIEW_3D" and space.shading.type != "MATERIAL":
                    space.shading.type = "MATERIAL"


def _rg_export(gen: GenerationContext):
    """Export all geometry, update API counters, and zoom camera."""
    from ...export import (  # deferred to avoid circular import at load time
        export_selected_to_3mf,
        export_to_STL,
        is_3mf_extension_installed,
    )
    from ..elevation import (
        load_counter,  # deferred to avoid circular import at load time
    )
    from ..scene import (
        zoom_camera_to_selected,  # deferred to avoid circular import at load time
    )

    shape = gen.settings.shape
    elements = gen.runtime.elements
    curveObjs = gen.runtime.curveObjs
    textobj = gen.runtime.textObj
    shellobj = gen.runtime.shellObj
    plateobj = gen.runtime.plateObj
    exportformat = gen.settings.exportFormat

    # PAINT mode bakes terrain-element colors as per-face materials on a single
    # mesh; STL cannot store material data at all, so PAINT-mode maps must be
    # exported as OBJ to keep the colors. Mirrors the equivalent computation in
    # terrain.coloring_main().
    # When using a texture: 3MF is the primary export; STL serves as no-addon fallback.
    if gen.settings.elementMode == "PAINT":
        exportformat = "OBJ"
    elif gen.texture.useTexture:
        exportformat = "STL"  # fallback only; 3MF addon handles the real export
    else:
        exportformat = "STL"
    if gen.settings.autoExport:
        print("Auto export disabled, skipping export")
        return

    if is_3mf_extension_installed():
        print("Exporting to 3mf")
        if gen.runtime.curveObjs and (
            not gen.texture.useTexture or not gen.texture.texTrail
        ):
            for tcrv in curveObjs:
                try:
                    if tcrv and tcrv.name in bpy.data.objects:
                        tcrv.select_set(True)
                except ReferenceError:
                    pass
        gen.runtime.mapObject.select_set(True)

        if elements and gen.settings.elementMode == "SINGLECOLORMODE_REMESH":
            for elem_obj in elements.values():
                if (
                    elem_obj
                    and isinstance(elem_obj, bpy.types.Object)
                    and elem_obj.name in bpy.data.objects
                ):
                    elem_obj.select_set(True)
        elif elements and gen.settings.elementMode == "PAINT":
            for key in ("roads", "buildings"):
                elem_obj = elements.get(key)
                if elem_obj and elem_obj.name in bpy.data.objects:
                    elem_obj.select_set(True)

        if (
            shape
            in {
                "HEXAGON INNER TEXT",
                "HEXAGON OUTER TEXT",
                "OCTAGON OUTER TEXT",
                "HEXAGON FRONT TEXT",
                "CIRCLE OUTER TEXT",
            }
            and textobj
        ):
            textobj.select_set(True)

        if (
            shape
            in {
                "HEXAGON OUTER TEXT",
                "OCTAGON OUTER TEXT",
                "HEXAGON FRONT TEXT",
                "CIRCLE OUTER TEXT",
            }
            and plateobj
        ):
            plateobj.select_set(True)

        if shellobj:
            shellobj.select_set(True)

        export_selected_to_3mf(is_auto=True)
    else:
        print("exporting as STL/OBJ")
        if curveObjs and (not gen.texture.useTexture or not gen.texture.texTrail):
            for tcrv in curveObjs:
                export_to_STL(tcrv, exportformat)
        export_to_STL(gen.runtime.mapObject, exportformat)

        if elements and gen.settings.elementMode == "SINGLECOLORMODE":
            for elem_obj in elements.values():
                if (
                    elem_obj
                    and isinstance(elem_obj, bpy.types.Object)
                    and elem_obj.name in bpy.data.objects
                ):
                    export_to_STL(elem_obj, exportformat)

        if (
            shape
            in {
                "HEXAGON INNER TEXT",
                "HEXAGON OUTER TEXT",
                "OCTAGON OUTER TEXT",
                "HEXAGON FRONT TEXT",
                "CIRCLE OUTER TEXT",
            }
            and textobj
        ):
            export_to_STL(textobj, exportformat)

        if (
            shape
            in {
                "HEXAGON OUTER TEXT",
                "OCTAGON OUTER TEXT",
                "HEXAGON FRONT TEXT",
                "CIRCLE OUTER TEXT",
            }
            and plateobj
        ):
            export_to_STL(plateobj, exportformat)

        if shellobj:
            export_to_STL(shellobj, exportformat)

    count_openTopoData, _dt1, count_openElevation, _dt2 = load_counter()
    tp3d = bpy.context.scene.tp3d
    tp3d["o_apiCounter_OpenTopoData"] = (
        f"API Limit: {count_openTopoData:.0f}/1000 daily"
        if count_openTopoData < 1000
        else f"API Limit: {count_openTopoData:.0f}/1000 (daily limit reached. might cause problems)"
    )
    tp3d["o_apiCounter_OpenElevation"] = (
        f"API Limit: {count_openElevation:.0f}/1000 Monthly"
        if count_openElevation < 1000
        else f"API Limit: {count_openElevation:.0f}/1000 (Monthly limit reached. might cause problems)"
    )

    if gen.runtime.buggyData != 0:
        _progress.WarningsOverlay.add_warning(
            "API might have faulty DATA. Maybe try diffrent Resolution or API", "warn"
        )

    zoom_camera_to_selected(gen.runtime.mapObject)
