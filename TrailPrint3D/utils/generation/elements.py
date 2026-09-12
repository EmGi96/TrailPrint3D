import math
import threading
from typing import Any, cast

import bpy  # type: ignore
from bpy.types import Object  # type: ignore

from ... import constants as const
from ... import progress as _progress
from ..dataclasses import GenerationContext, GenerationError
from ..terrain import _ColoringTextureResult

# Set after each road-enabled generation; read by the puzzle flow to clip road
# geometry per piece without re-running the road pipeline.
_puzzle_roads_data: tuple | None = None

def _rg_create_text_and_overlays(gen: GenerationContext):
    from math import pi

    from ..scene import set_origin_to_3d_cursor, transform_MapObject

    try:
        from ...premium.utils_pe import (
            build_map_shell,  # Premium-only: Shell shape extra
        )
    except ImportError:

        def build_map_shell(*_args, **_kwargs):
            return None

    from ..terrain import plateInsert  # deferred to avoid circular import at load time
    from ..text_objects import (  # deferred to avoid circular import at load time
        HexagonFrontText,
        HexagonInnerText,
        HexagonOuterText,
        MedalText,
        OctagonOuterText,
    )

    textobj = None
    plateobj = None
    shellobj = None
    bpy.ops.object.select_all(action="DESELECT")

    if "append_collection" not in gen.settings.flags:
        if gen.settings.shape == "HEXAGON INNER TEXT":
            textobj = HexagonInnerText(gen.runtime.mapObject)
        elif gen.settings.shape == "HEXAGON OUTER TEXT":
            textobj, plateobj = HexagonOuterText()
            gen.runtime.mapObject.location.z += gen.settings.plateThickness
        elif gen.settings.shape == "OCTAGON OUTER TEXT":
            textobj, plateobj = OctagonOuterText()
            gen.runtime.mapObject.location.z += gen.settings.plateThickness
        elif gen.settings.shape == "HEXAGON FRONT TEXT":
            textobj, plateobj = HexagonFrontText()
            gen.runtime.mapObject.location.z += gen.settings.plateThickness
        elif gen.settings.shape == "CIRCLE OUTER TEXT":
            textobj, plateobj = MedalText()
            gen.runtime.mapObject.location.z += gen.settings.plateThickness
        elif gen.settings.shape.endswith(" SHELL"):
            shellobj = build_map_shell(
                gen.runtime.mapObject,
                gen.settings.tolerance,
                wall=gen.settings.shellWallThickness,
                bottom_wall=1.0,
            )

            if shellobj:
                set_origin_to_3d_cursor(shellobj)
        else:
            pass  # BottomText() — currently disabled

    if (
        "TEXT" in gen.settings.shape
        and gen.runtime.curveObjs is not None
        and "INNER TEXT" not in gen.settings.shape
    ) or (gen.settings.shape == "CIRCLE OUTER TEXT" and gen.runtime.curveObjs is not None):
        for tcrv in gen.runtime.curveObjs:
            tcrv.location.z += gen.settings.plateThickness

    # Plate insert
    bpy.ops.object.select_all(action="DESELECT")
    dist = gen.settings.plateInsertValue
    if (
        gen.settings.shape
        in {
            "HEXAGON OUTER TEXT",
            "OCTAGON OUTER TEXT",
            "HEXAGON FRONT TEXT",
            "CIRCLE OUTER TEXT",
        }
        and plateobj
        and textobj
    ):
        transform_MapObject(plateobj, gen.settings.xTerrainOffset, gen.settings.yTerrainOffset)
        transform_MapObject(textobj, gen.settings.xTerrainOffset, gen.settings.yTerrainOffset)
        set_origin_to_3d_cursor(plateobj)
        set_origin_to_3d_cursor(textobj)
        if dist > 0:
            plateInsert(plateobj, gen.runtime.mapObject)
            textobj.location.z += dist
        if gen.settings.shapeRotation != 0:
            textobj.rotation_euler[2] += gen.settings.shapeRotation * (pi / 180)

    gen.runtime.textObj = textobj
    gen.runtime.plateObj = plateobj
    gen.runtime.shellObj = shellobj


def _rg_build_terrain_elements(
    gen: GenerationContext,
    phase_start=0.83,
    phase_end=0.95,
    prefetched_osm=None,
    tile_label=None,
) -> dict[str, Any]:
    """Create water, forest, city, glacier, building and road overlay meshes.

    Reads all flags directly from bpy.context.scene.tp3d.
    Returns a dict keyed by element name; values may be None if disabled.
    phase_start/phase_end control the overlay progress range for multi-tile callers.
    prefetched_osm: result dict from _rg_start_osm_prefetch; if provided the
    per-kind OSM fetch is skipped (data was already downloaded in the background).
    tile_label: optional prefix for progress messages (e.g. "Tile 2/6") used by
    multi-tile callers so element messages keep their tile context visible.
    """
    from ...props import (  # deferred to avoid circular import
        any_road_active,
        get_road_active,
    )
    from ..metadata import (
        writeMetadata,  # deferred to avoid circular import at load time
    )
    from ..osm import buildings as _bld_mod
    from ..osm.buildings import create_buildings
    from ..osm.fetch_utils import OsmFetchSettings
    from ..osm.roads import TIER_TAGS, create_roads
    from ..scene import set_origin_to_3d_cursor

    # Cleared unconditionally (not just when disabled) so the puzzle flow's
    # getattr(module, '_puzzle_roads_data'/'_puzzle_buildings_data') lookup
    # never reuses stale data from an earlier generation that had roads/
    # buildings enabled -- these module globals are only ever (re)written
    # below when that element is actually built for THIS generation.
    global _puzzle_roads_data
    _puzzle_roads_data = None
    _bld_mod._puzzle_buildings_data = None
    from ..terrain import (  # deferred to avoid circular import at load time
        _COLORING_EMPTY,
        _COLORING_FILTERED,
        _COLORING_PAINTED,
        _fetch_all_kinds_parallel,
        coloring_main,
        createOcean,
        createOceanFromWaterPolygons,
    )

    tp3d = bpy.context.scene.tp3d
    map_km: float | None = gen.runtime.mapKm
    _ov = _progress.ProgressOverlay.get()

    # --------------------------------------------------
    # Standard coloring elements (all share the same pattern).
    # To add a new layer: append one tuple here and nothing else.
    #   (result_key, active_flag_attr, max_size_const, phase_label, fetch_message)
    # --------------------------------------------------
    COLORING_ELEMENTS = [
        (
            "forest",
            "col_fActive",
            const.FOREST_MAXSIZE,
            "Forest",
            "Fetching forest data…",
        ),
        (
            "water",
            lambda t: (
                t.col_wBodiesActive or t.col_wMinorActive or t.col_wMajorActive
            ),
            const.WATER_MAXSIZE,
            "Water",
            "Fetching water data…",
        ),
        (
            "scree",
            "col_scrActive",
            const.SCREE_MAXSIZE,
            "Scree",
            "Fetching scree data…",
        ),
        ("city", "col_cActive", const.CITY_MAXSIZE, "City", "Fetching city data…"),
        (
            "greenspace",
            "col_grActive",
            const.GREENSPACE_MAXSIZE,
            "Greenspace",
            "Fetching greenspace data…",
        ),
        (
            "farmland",
            "col_faActive",
            const.FARMLAND_MAXSIZE,
            "Farmland",
            "Fetching farmland data…",
        ),
        (
            "glacier",
            "col_glActive",
            const.GLACIER_MAXSIZE,
            "Glacier",
            "Fetching glacier data…",
        ),
    ]

    # Count total active elements (coloring + optional ocean/buildings/roads) for progress spread
    _ELEM_PHASE_START = phase_start
    _ELEM_PHASE_END = phase_end
    if map_km is None:
        raise GenerationError("map_km value not set properly.")
    _active_elem_flags = (
        [
            flag
            for _, flag, size, _, _ in COLORING_ELEMENTS
            if (flag(tp3d) if callable(flag) else getattr(tp3d, flag) == 1)
            and map_km <= size
        ]
        + (
            ["_ocean"]
            if tp3d.el_oActive == 1 and map_km <= const.COASTLINE_WATERPOLY_MAXSIZE
            else []
        )
        + (
            ["_buildings"]
            if tp3d.el_bActive == 1 and map_km <= const.BUILDINGS_MAXSIZE
            else []
        )
        + (
            ["_roads"]
            if any_road_active(tp3d) and map_km <= const.ROADS_MAXSIZE
            else []
        )
    )
    obj: Object = gen.runtime.mapObject
    scaleHor = gen.runtime.sScaleHor
    _total_active = max(len(_active_elem_flags), 1)
    _elem_step = (_ELEM_PHASE_END - _ELEM_PHASE_START) / _total_active
    _elem_idx = [0]  # mutable counter

    def _advance_elem_progress(phase_label, msg):
        if _ov.active:
            pct = _ELEM_PHASE_START + _elem_idx[0] * _elem_step
            full_msg = f"{tile_label} — {msg}" if tile_label else msg
            _ov.update(percent=pct, phase=phase_label, message=full_msg)
        _elem_idx[0] += 1

    _water_feat_active = (
        gen.settings.elementSource == "OSM"
        and (
            tp3d.col_wBodiesActive
            or tp3d.col_wMinorActive
            or tp3d.col_wMajorActive
        )
        and map_km <= const.WATER_MAXSIZE
    )
    _ocean_active = tp3d.el_oActive == 1 and map_km <= const.COASTLINE_WATERPOLY_MAXSIZE
    _water_ocean_combined = _water_feat_active and _ocean_active

    # --------------------------------------------------
    # Fetch all active OSM kinds unless already done by the background thread
    # started before elevation (prefetched_osm != None means data is ready).
    # --------------------------------------------------
    if prefetched_osm is None:
        _lat_step = min(2.0, gen.runtime.tbMaxLat - gen.runtime.tbMinLat)
        _lon_step = min(2.0, gen.runtime.tbMaxLon - gen.runtime.tbMinLon)
        _tile_lats = math.ceil((gen.runtime.tbMaxLat - gen.runtime.tbMinLat) / _lat_step)
        _tile_lons = math.ceil((gen.runtime.tbMaxLon - gen.runtime.tbMinLon) / _lon_step)
        _tile_tasks = [
            (
                gen.runtime.tbMinLat + k * _lat_step,
                gen.runtime.tbMinLon + l * _lon_step,
                gen.runtime.tbMinLat + k * _lat_step + _lat_step,
                gen.runtime.tbMinLon + l * _lon_step + _lon_step,
            )
            for k in range(_tile_lats)
            for l in range(_tile_lons)
        ]
        _overpass_semaphore = threading.Semaphore(
            1
        )  # max 1 concurrent live Overpass request (avoid 429s on the public instance)
        _fetch_settings = OsmFetchSettings(
            disable_cache=tp3d.disableCache,
            api_retries=tp3d.apiRetries,
            mapsize=tp3d.sMapInKm,
            road_tiers={tier: get_road_active(tp3d, tier) for tier in TIER_TAGS},
            water_ponds=bool(tp3d.col_wBodiesActive),
            water_small_rivers=bool(tp3d.col_wMinorActive),
            water_big_rivers=bool(tp3d.col_wMajorActive),
            exclude_alleys=True,
        )
        _active_kind_tasks = (
            [
                (key.upper(), _tile_tasks)
                for key, flag_attr, max_size, _, _ in COLORING_ELEMENTS
                if (
                    flag_attr(tp3d)
                    if callable(flag_attr)
                    else getattr(tp3d, flag_attr) == 1
                )
                and map_km <= max_size
            ]
            if gen.settings.elementSource == "OSM"
            else []
        )
        # Buildings/roads/coastline aren't in COLORING_ELEMENTS but are fetched
        # in this same combined batch (mirrors _rg_start_osm_prefetch) so
        # create_buildings/create_roads/createOcean below can reuse the
        # already-fetched + disk-cached tiles instead of re-querying Overpass.
        if tp3d.el_bActive == 1 and map_km <= const.BUILDINGS_MAXSIZE:
            _active_kind_tasks.append(("BUILDINGS", _tile_tasks))
        if any_road_active(tp3d) and map_km <= const.ROADS_MAXSIZE:
            _active_kind_tasks.append(("STREETS", _tile_tasks))
        if tp3d.el_oActive == 1 and map_km <= const.COASTLINE_MAXSIZE:
            _active_kind_tasks.append(("COASTLINE", _tile_tasks))
        _all_prefetched = _fetch_all_kinds_parallel(
            _active_kind_tasks, _overpass_semaphore, settings=_fetch_settings
        )
    else:
        _all_prefetched = prefetched_osm

    # After batch download completes, show 100% for all fetched kinds so the
    # strip indicates the download is done while mesh building is still pending.
    # The final set_fetch_done/empty/filtered below flips each badge to ✓ once
    # the mesh operations for that kind are complete.
    if _ov.active:
        for key, flag_attr, max_size, _, _ in COLORING_ELEMENTS:
            if (
                (
                    flag_attr(tp3d)
                    if callable(flag_attr)
                    else getattr(tp3d, flag_attr) == 1
                )
                and map_km <= max_size
                and _all_prefetched.get(key.upper())
            ):
                _ov.set_fetch_ready(key)
        # Buildings, roads, and ocean are pre-fetched in the same batch but aren't
        # in COLORING_ELEMENTS, so mark them ready here too.
        if (
            tp3d.el_bActive == 1
            and map_km <= const.BUILDINGS_MAXSIZE
            and _all_prefetched.get("BUILDINGS")
        ):
            _ov.set_fetch_ready("buildings")
        if (
            any_road_active(tp3d)
            and map_km <= const.ROADS_MAXSIZE
            and _all_prefetched.get("STREETS")
        ):
            _ov.set_fetch_ready("roads")
        if (
            tp3d.el_oActive == 1
            and map_km <= const.COASTLINE_MAXSIZE
            and _all_prefetched.get("COASTLINE")
        ):
            _ov.set_fetch_ready("water")

    terrain: dict[str, Any] = {}
    terrain["_osm_polygons"] = {}  # populated only when creating a texture
    _water_result = None  # raw coloring_main() result for 'water' -- replayed below if ocean finds nothing
    for key, flag_attr, max_size, phase, msg in COLORING_ELEMENTS:
        terrain[key] = None
        if gen.settings.elementSource != "OSM":
            continue
        if flag_attr(tp3d) if callable(flag_attr) else getattr(tp3d, flag_attr) == 1:
            if map_km <= max_size:
                _advance_elem_progress(phase, msg)
                _cutter_out = {} if gen.settings.elementMode == "SEPARATE" else None
                _result = coloring_main(
                    gen,
                    key.upper(),
                    prefetched_tiles=_all_prefetched.get(key.upper(), {}),
                    cutter_out=_cutter_out,
                )
                if key == "water":
                    _water_result = _result
                if _result is _COLORING_EMPTY:
                    terrain[key] = None
                    _ov.set_fetch_empty(key)
                elif _result is _COLORING_FILTERED:
                    terrain[key] = None
                    _ov.set_fetch_filtered(key)
                elif _result is _COLORING_PAINTED:
                    terrain[key] = None  # object was deleted after painting
                    _ov.set_fetch_done(key, success=True)
                elif isinstance(_result, _ColoringTextureResult):
                    terrain["_osm_polygons"][_result.kind] = _result.polygon
                    terrain[key] = None
                    _ov.set_fetch_done(key, success=True)
                elif _result is None:
                    terrain[key] = None
                    _ov.set_fetch_done(key, success=False)
                else:
                    terrain[key] = _result
                    _ov.set_fetch_done(key, success=True)
                    if gen.settings.elementMode == "SEPARATE":
                        from ..mesh_ops import (
                            separate_mode_recess_cutter,
                            separate_mode_recess_cutter_from_prism,
                        )

                        _prism = (_cutter_out or {}).get("prism")
                        if _prism is not None:
                            separate_mode_recess_cutter_from_prism(_prism, _result, obj)
                            bpy.data.objects.remove(_prism, do_unlink=True)
                        else:
                            separate_mode_recess_cutter(_result, obj)
                if key == "water" and _water_ocean_combined:
                    # Ocean will complete this chip; hold at 50%
                    _ov.set_fetch_progress("water", 0.5)
            else:
                print(
                    f"INFO: MAP IS TOO BIG FOR {key.upper()} (< {max_size} km required)"
                )
                _progress.WarningsOverlay.add_warning(
                    f"Map too big for {phase} layer.", "warn"
                )

    # --------------------------------------------------
    # Ocean — unique creation logic.
    # --------------------------------------------------
    terrain["ocean"] = None
    if tp3d.el_oActive == 1:
        if map_km <= const.COASTLINE_MAXSIZE:
            _advance_elem_progress("Ocean", "Creating ocean…")
            _ov.set_fetch_progress("water", 0.5 if _water_feat_active else 0.0)
            print("Create Ocean")
            _coastline_tiles = _all_prefetched.get("COASTLINE", {})
            terrain["ocean"] = createOcean(gen, _coastline_tiles, scaleHor, obj)
        elif map_km <= const.COASTLINE_WATERPOLY_MAXSIZE:
            # Too big for the Overpass coastline-way pipeline, but still within
            # range of the prebuilt global water-polygon dataset -- use that
            # instead of skipping ocean generation outright. See
            # TP3D_water_polygon_integration_plan.md for why this is a
            # separate, much larger size band than COASTLINE_MAXSIZE.
            _advance_elem_progress("Ocean", "Creating ocean (water polygon dataset)…")
            _ov.set_fetch_progress("water", 0.5 if _water_feat_active else 0.0)
            print("Create Ocean (water polygon dataset fallback)")
            terrain["ocean"] = createOceanFromWaterPolygons(gen, scaleHor, obj)
        else:
            terrain["ocean"] = "_TOO_BIG"

        if terrain["ocean"] == "_TOO_BIG":
            terrain["ocean"] = None
            print(
                f"INFO: MAP IS TOO BIG FOR COASTLINE "
                f"(< {const.COASTLINE_WATERPOLY_MAXSIZE}km required)"
            )
            _progress.WarningsOverlay.add_warning(
                "Map too big for Ocean/Coastline layer.", "warn"
            )
        elif isinstance(terrain["ocean"], _ColoringTextureResult):
            terrain["_osm_polygons"][terrain["ocean"].kind] = terrain[
                "ocean"
            ].polygon
            terrain["ocean"] = None
            _ov.set_fetch_done("water", success=True)
        elif terrain["ocean"] is not None:
            _ov.set_fetch_done("water", success=True)
        elif _water_ocean_combined:
            # No coastline/water nearby (or it failed to build) -- that's normal
            # for an inland map, not a failure. Fall back to the water-features
            # chip's own result instead of marking the combined chip red just
            # because there's no ocean in this area.
            if _water_result is _COLORING_EMPTY:
                _ov.set_fetch_empty("water")
            elif _water_result is _COLORING_FILTERED:
                _ov.set_fetch_filtered("water")
            else:
                _ov.set_fetch_done("water", success=_water_result is not None)
        else:
            _ov.set_fetch_done("water", success=False)

    print("Base elements Created")

    # --------------------------------------------------
    # Buildings — own creation function + intersection post-processing.
    # --------------------------------------------------
    terrain["buildings"] = None
    if tp3d.el_bActive == 1:
        if map_km <= const.BUILDINGS_MAXSIZE:
            _advance_elem_progress("Buildings", "Fetching building data…")
            _ov.set_fetch_progress("buildings", 0.0)
            _ov.set_fetch_ready("buildings")
            buildings = create_buildings(
                gen, 10, gen.runtime.sScaleHor or 1,
                prefetched_tiles=_all_prefetched.get("BUILDINGS"),
            )

            if buildings is not None:
                # Buildings are already clipped to the map shape in 2D inside
                # create_buildings, so no 3D boolean clip is needed here.
                set_origin_to_3d_cursor(buildings)
                buildings.name = obj.name + "_" + "BUILDINGS"
                terrain["buildings"] = buildings
                writeMetadata(buildings, type="BUILDINGS")
            _ov.set_fetch_done("buildings", success=buildings is not None)
        else:
            print("INFO: MAP IS TOO BIG FOR BUILDINGS (< 10Km Map size Required)")
            _progress.WarningsOverlay.add_warning("Map too big for Buildings.", "warn")

    # --------------------------------------------------
    # Roads — own creation function + clipping + material post-processing.
    # --------------------------------------------------
    terrain["roads"] = None
    if any_road_active(tp3d):
        if map_km <= const.ROADS_MAXSIZE:
            _advance_elem_progress("Roads", "Fetching road data…")
            _ov.set_fetch_progress("roads", 0.0)
            _ov.set_fetch_ready("roads")
            if gen.runtime.sScaleHor is None:
                raise GenerationError("ScaleHor not Set")
            # Cache the terrain's own triangulated grid NOW, while terrain is
            # still pristine (no boolean cuts yet) -- both create_roads' own
            # cutter (so it stops exactly at the terrain surface instead of
            # the model's bounding box) and finalize_roads() (called later,
            # after roads is used as the cheap boolean cutter) need this
            # original height data under the road footprint, which a cut
            # would otherwise destroy.
            from ..mesh_ops import recalculateNormals as _rg_recalc_normals
            from ..osm.roads import (
                _triangulated_terrain_faces,
                compute_full_depth_bottom_z,
            )

            _rg_recalc_normals(obj)
            _terrain_tris_cache = _triangulated_terrain_faces(obj)
            # PAINT mode: roads is fused visually onto a single-piece terrain,
            # never printed standalone -- a thin raised strip is fine. Every
            # other mode (SINGLECOLORMODE*) needs roads to stand on
            # its own as a base-to-top piece, like the coloring elements and
            # the SCM trail groove insert, so it can be printed/assembled
            # separately instead of being a sliver with nothing to sit on.
            result = create_roads(
                gen,
                gen.elevation.el_sHeight,
                gen.runtime.sScaleHor,
                full_depth=(tp3d.elementMode != "PAINT"),
                terrain_tris=_terrain_tris_cache,
                prefetched_tiles=_all_prefetched.get("STREETS"),
            )
            if result is not None:
                roads, roads_polygon = result
                terrain["roads_polygon"] = roads_polygon
                terrain["_terrain_tris_cache"] = _terrain_tris_cache
                _puzzle_roads_data = (
                    roads_polygon,
                    terrain["_terrain_tris_cache"],
                    gen.elevation.el_sHeight,
                )
                terrain["roads_bottom_z"] = None
                if tp3d.elementMode != "PAINT" and roads_polygon is not None:
                    terrain["roads_bottom_z"] = compute_full_depth_bottom_z(
                        terrain["_terrain_tris_cache"], roads_polygon, gen.elevation.el_sHeight
                    )
                if gen.texture.useTexture and roads_polygon is not None and gen.texture.texRoads:
                    terrain["_osm_polygons"]["ROADS"] = roads_polygon
                if gen.texture.useTexture and gen.texture.texRoads:
                    # tex_include_roads on — polygon stored above; discard the mesh.
                    bpy.data.objects.remove(roads, do_unlink=True)
                    roads = None
                    gen.runtime.roadObj = None
                # tex_include_roads off — fall through so roads is stored as a PAINT-style overlay
                if roads is not None:
                    set_origin_to_3d_cursor(roads)
                    roads.data.materials.clear()
                    roads.data.materials.append(bpy.data.materials.get("BLACK"))
                    terrain["roads"] = roads
                    roads.name = obj.name + "_" + "ROADS"
                    writeMetadata(roads, type="ROADS")
                    gen.runtime.roadObj = roads
                    gen.runtime.roadUnion = roads_polygon
                _ov.set_fetch_done("roads", success=True)
            else:
                print("INFO: No road data returned, skipping road processing.")
                _progress.WarningsOverlay.add_warning("No road data returned.", "warn")
                _ov.set_fetch_done("roads", success=False)
        else:
            print("INFO: MAP IS TOO BIG FOR STREETS (< 100Km Map size Required)")
            _progress.WarningsOverlay.add_warning("Map too big for Roads.", "warn")

    gen.runtime.elements = terrain

    return terrain


def _rg_apply_single_color_mode(gen: GenerationContext):
    """Apply all final boolean cuts: single‑color mode, roads, trails, and road finalisation.

    This orchestrates the following steps:
      1. Process curve objects for single‑color remesh (thicker curves and trail ribbons).
      2. In PAINT mode, extract trail footprints from original trail curves.
      3. Store trail union for texture rasterisation if needed.
      4. Apply single‑color mode booleans between terrain elements and curves.
      5. Cut roads out of terrain and terrain elements.
      6. Subtract trail grooves from buildings.
      7. Finalise roads (rebuild mesh, subtract trails in 2D, repair non‑manifold).
      8. Clean up temporary thicker curve objects.
    """

    # ----------------------------------------------------------------------
    # Helper functions (each raises GenerationError on failure)
    # ----------------------------------------------------------------------

    def _process_curve_projections(gen: GenerationContext, obj):
        """Process curve objects for single-color remesh.

        Returns:
            (thickerCurves, trail_thick_ribbons) - both are lists of mesh objects.
        """
        from ..mesh_ops import (
            boolean_operation,
            recalculateNormals,
            single_color_mode_curve,
        )
        from ..scene import remove_objects

        thickerCurves = []
        trail_thick_ribbons = []
        survivingCurveObjs = []

        try:
            dpt = 1
            dup = obj.copy()
            dup.data = obj.data.copy()
            dup.name = f"{obj.name}_dup_for_projection"
            if obj.users_collection:
                for coll in obj.users_collection:
                    coll.objects.link(dup)

            for tcrv in gen.runtime.curveObjs:
                result = single_color_mode_curve(tcrv, obj, True, dpt, dup)
                if result is not None:
                    if result[1] is not None:
                        survivingCurveObjs.append(result[0])
                        thickerCurves.append(result[1])
                    if result[2] is not None and not result[2].is_empty:
                        trail_thick_ribbons.append(result[2])

            remove_objects(dup)

            # Select all thicker curves for subsequent operations
            for tcrv in thickerCurves:
                bpy.ops.object.select_all(action="DESELECT")
                tcrv.select_set(True)
                bpy.context.view_layer.objects.active = tcrv

            # Boolean subtract among curves
            for i in range(len(thickerCurves)):
                recalculateNormals(thickerCurves[i])
                thickerCurves[i].location.z -= 0.001
                for j in range(i + 1, len(survivingCurveObjs)):
                    recalculateNormals(survivingCurveObjs[j])
                    boolean_operation(survivingCurveObjs[j], thickerCurves[i])

            return thickerCurves, trail_thick_ribbons

        except Exception as e:
            raise GenerationError(f"Failed to process curve projections: {e}") from e

    def _clip_paint_trail_curves_to_map(gen: GenerationContext, map_obj):
        """Trim each PAINT-mode trail curve's spline points to the map's true
        2D outline, so a path that runs outside the printed shape (common
        with a tight custom SVG/GeoJSON border, less so with a generous
        default shape sized to the GPX bounding box) doesn't poke out past
        the model's edge.

        Skipped when the trail is about to be baked into the texture (or
        dropped) by _rg_apply_texture instead -- clipping the curve here
        would be wasted work, since the texture only ever paints onto the
        terrain's own faces regardless of how far the trail's footprint
        extends past them.

        Uses a 2D Shapely clip (path ∩ outline) on the curve's centerline
        rather than a 3D boolean against the actual map solid: the trail is
        a bevelled tube resting ON TOP of the terrain surface, so
        intersecting its solid volume against the terrain's solid volume
        would remove almost all of it -- only the thin sliver embedded at
        trailCutDepth actually overlaps the map's own solid. Clipping the
        2D centerline instead and letting GEOS linearly interpolate Z at the
        new boundary-crossing points keeps the curve's real (raycasted)
        height intact everywhere it's still inside the map.

        That centerline clip alone still leaves the tube's bevelled radius
        poking out past the boundary at any point that landed exactly on
        it, since pathThickness/2 of width extends outward from a
        centerline point on either side. So for any curve whose centerline
        was actually cut (i.e. it used to run outside the outline), we
        additionally convert that one curve to a mesh and INTERSECT it
        against a tall vertical prism built straight from the outline
        polygon -- a plain XY "cookie cutter" spanning far above and below
        any possible trail height, so it trims the tube flush with the
        boundary without conforming/clipping it to the terrain's height at
        all. Curves that were never near the boundary are left as
        lightweight curve objects and skip this conversion entirely.
        """
        if (gen.texture.useTexture and gen.texture.texTrail) or map_obj is None:
            return
        outline = gen.runtime.mapOutline
        if outline is None or outline.is_empty:
            return

        from mathutils import Vector
        from shapely.affinity import translate as _shp_translate
        from shapely.geometry import LineString

        from ..mesh_ops import _clean_solid_mesh, _extrude_flat_polygon, boolean_operation
        from ..scene import remove_objects
        from .. import geometry2d as _g2d

        def _iter_lines(geom):
            if geom is None or geom.is_empty:
                return
            if geom.geom_type == "LineString":
                if geom.length > 0:
                    yield geom
            elif geom.geom_type in ("MultiLineString", "GeometryCollection"):
                for g in geom.geoms:
                    yield from _iter_lines(g)

        def _build_outline_cutter(outline_poly):
            """A plain vertical prism of outline_poly, tall enough to clear
            any trail height without ever clipping it in Z -- only the XY
            side walls (the outline's true edge) can cut anything."""
            verts, faces = [], []
            for poly in _g2d.iter_polygons(outline_poly):
                _extrude_flat_polygon(_g2d, poly, -1e4, 1e4, verts, faces)
            if not verts:
                return None
            mesh = bpy.data.meshes.new("TrailClipCutter")
            mesh.from_pydata(verts, [], faces)
            mesh.update()
            _clean_solid_mesh(mesh)
            cutter = bpy.data.objects.new("TrailClipCutter", mesh)
            bpy.context.collection.objects.link(cutter)
            return cutter

        # mapOutline is stored in the map's LOCAL space (pre-transform); the
        # curve's spline points are read in WORLD space below, so translate
        # the outline to match (same convention used for roads/SCM elsewhere
        # in this file).
        outline_ws = _shp_translate(
            outline, xoff=map_obj.location.x, yoff=map_obj.location.y
        )

        cutter_obj = None
        try:
            clipped_objs = []
            for crv in gen.runtime.curveObjs or []:
                if crv is None or crv.type != "CURVE":
                    if crv is not None:
                        clipped_objs.append(crv)
                    continue

                mw = crv.matrix_world
                inv = mw.inverted()
                new_splines_coords = []
                was_cut = False
                for spline in crv.data.splines:
                    pts = spline.points if len(spline.points) > 0 else spline.bezier_points
                    if len(pts) < 2:
                        continue
                    world_coords = [
                        (mw @ Vector((p.co.x, p.co.y, p.co.z)))[:] for p in pts
                    ]
                    line = LineString(world_coords)
                    clipped = line.intersection(outline_ws)
                    kept_parts = list(_iter_lines(clipped))
                    if sum(p.length for p in kept_parts) < line.length - 1e-4:
                        was_cut = True
                    for part in kept_parts:
                        coords = list(part.coords)
                        if len(coords) >= 2:
                            new_splines_coords.append(
                                [inv @ Vector(c) for c in coords]
                            )

                if not new_splines_coords:
                    # Whole curve fell outside the map -- drop it entirely.
                    remove_objects(crv)
                    continue

                for sp in list(crv.data.splines):
                    crv.data.splines.remove(sp)
                for coords in new_splines_coords:
                    sp = crv.data.splines.new("POLY")
                    sp.points.add(len(coords) - 1)
                    for i, co in enumerate(coords):
                        sp.points[i].co = (co.x, co.y, co.z, 1)

                if was_cut:
                    # The centerline landed exactly on the boundary at the
                    # cut point(s) -- only the tube's own bevelled surface
                    # can still poke past it, so bake it to a mesh and trim
                    # that with the cookie-cutter prism.
                    if cutter_obj is None:
                        cutter_obj = _build_outline_cutter(outline_ws)
                    if cutter_obj is not None:
                        bpy.ops.object.select_all(action="DESELECT")
                        crv.select_set(True)
                        bpy.context.view_layer.objects.active = crv
                        bpy.ops.object.convert(target="MESH")
                        boolean_operation(crv, cutter_obj, "INTERSECT")
                        if not crv.data.vertices:
                            remove_objects(crv)
                            continue

                clipped_objs.append(crv)

            if cutter_obj is not None:
                bpy.data.objects.remove(cutter_obj, do_unlink=True)

            gen.runtime.curveObjs = clipped_objs
        except Exception as e:
            raise GenerationError(f"Failed to clip trail to map shape: {e}") from e

    def _collect_paint_trail_ribbons(gen: GenerationContext):
        """In PAINT mode, derive 2D ribbon footprints from _Trail curve objects."""
        from mathutils import Vector

        from ..geometry2d import polylines_to_ribbon

        trail_thick_ribbons = []
        _tol = gen.settings.tolerance
        _pt = gen.settings.pathThickness

        try:
            # Use the pre-built trail objects from generation context
            trail_objects = gen.runtime.curveObjs or []
            for _ob in trail_objects:
                if _ob is None or _ob.type != "CURVE":
                    continue

                _mw = _ob.matrix_world
                _coords = []

                for _sp in _ob.data.splines:
                    _pts = _sp.points if len(_sp.points) > 0 else _sp.bezier_points
                    if len(_pts) >= 2:
                        _coords.append(
                            [
                                (_mw @ Vector((_p.co.x, _p.co.y, _p.co.z)))[:2]
                                for _p in _pts
                            ]
                        )
                if not _coords:
                    continue
                _r = polylines_to_ribbon(_coords, _pt / 2 + _tol, quad_segs=4)
                if _r and not _r.is_empty:
                    trail_thick_ribbons.append(_r)
            return trail_thick_ribbons

        except Exception as e:
            raise GenerationError(f"Failed to collect paint trail ribbons: {e}") from e

    def _store_trail_union_for_texture(
        gen: GenerationContext, terrain: dict, trail_thick_ribbons
    ):
        """Store the union of trail ribbons into the terrain's OSM polygon cache."""
        from .. import geometry2d as _g2d

        try:
            osm_polygons = gen.runtime.elements.get("_osm_polygons")
            if osm_polygons is None:
                osm_polygons = {}
                terrain["_osm_polygons"] = osm_polygons
            osm_polygons["TRAIL"] = _g2d.union(trail_thick_ribbons)
        except Exception as e:
            raise GenerationError(
                f"Failed to store trail union for texture: {e}"
            ) from e

    def _apply_single_color_mode_booleans(
        gen: GenerationContext, obj, terrain: dict, thickerCurves
    ):
        """Perform remesh/wireframe and boolean subtractions between terrain elements and curves."""
        from ..mesh_ops import (
            boolean_operation,
            single_color_mode_mesh_remesh,
        )

        try:
            _ov = _progress.ProgressOverlay.get()
            thicker_by_key = {}

            TERRAIN_PRIORITY_ORDER = [
                "water",
                "forest",
                "scree",
                "city",
                "greenspace",
                "farmland",
                "glacier",
                "ocean",
            ]

            _active_scm_keys = [k for k in TERRAIN_PRIORITY_ORDER if terrain.get(k)]
            _n_scm = max(1, len(_active_scm_keys))
            _scm_done = 0

            # Shared bottom so every element piece and its recess slot to the same depth.
            _scm_bottom_z = None
            for _k in _active_scm_keys:
                _e = terrain[_k]
                _mw = _e.matrix_world
                _z = min((_mw @ v.co).z for v in _e.data.vertices)
                if _scm_bottom_z is None or _z < _scm_bottom_z:
                    _scm_bottom_z = _z

            for i, key in enumerate(TERRAIN_PRIORITY_ORDER):
                elem_obj = terrain.get(key)
                if not elem_obj:
                    continue
                _ov = _progress.ProgressOverlay.get()
                if _ov.active:
                    _ov.update(
                        percent=0.95 + 0.02 * (_scm_done / _n_scm),
                        message=f"Single-color: remeshing {key.capitalize()} ({_scm_done + 1}/{_n_scm})…",
                    )

                thicker = single_color_mode_mesh_remesh(elem_obj, obj, map_outline=gen.runtime.mapOutline, shared_bottom_z=_scm_bottom_z)
                thicker_by_key[key] = thicker

                if _ov.active:
                    _ov.update(
                        percent=0.95 + 0.02 * ((_scm_done + 0.5) / _n_scm),
                        message=f"Single-color: subtracting from {key.capitalize()}…",
                    )

                # Subtract all curve thicker-bodies
                for tcrv in thickerCurves:
                    boolean_operation(elem_obj, tcrv)

                # Subtract every higher-priority element that was already processed
                for prev_key in TERRAIN_PRIORITY_ORDER[:i]:
                    if prev_key in thicker_by_key:
                        boolean_operation(elem_obj, thicker_by_key[prev_key])

                _scm_done += 1

            if bpy.app.debug:
                obj_size = gen.settings.size
                for thicker in thicker_by_key.values():
                    thicker.location.x += obj_size
            else:
                from ..scene import remove_objects

                for thicker in thicker_by_key.values():
                    remove_objects(thicker)

        except Exception as e:
            raise GenerationError(
                f"Failed to apply single-color mode booleans: {e}"
            ) from e

    def _cut_roads_from_terrain_and_elements(
        gen: GenerationContext, obj, terrain: dict
    ):
        """Cut roads out of the main terrain and all terrain elements."""
        from mathutils import Vector

        from .. import geometry2d as _g2d
        from ..mesh_ops import (
            boolean_operation,
            is_mesh_manifold,
            recalculateNormals,
        )
        from ..osm.roads import _build_extruded_mesh
        from ..scene import remove_objects

        try:
            roads_obj = gen.runtime.roadObj
            if roads_obj is None:
                return

            # Build a cutter from the Shapely road polygon, bounded properly.
            cut_tolerance = gen.elevation.el_sCutTolerance
            roads_cutter = roads_obj
            _cutter_tmp = None
            _road_poly = gen.runtime.roadUnion
            _roads_bz = terrain.get("roads_bottom_z")
            if (
                _road_poly is not None
                and not _road_poly.is_empty
                and _roads_bz is not None
            ):
                _buffered = (
                    _road_poly.buffer(max(0.025, cut_tolerance), join_style="mitre")
                    if cut_tolerance > 0
                    else _road_poly.buffer(0.025, join_style="mitre")
                )
                if _buffered and not _buffered.is_empty:
                    _all_v2d, _all_tris = [], []
                    for _part in _g2d.iter_polygons(_buffered):
                        _part = _g2d.orient(_part)
                        _ext = list(_part.exterior.coords)[:-1]
                        _holes = [
                            list(r.coords)[:-1]
                            for r in _part.interiors
                            if len(r.coords) >= 4
                        ]
                        _ec = _g2d._cdt_triangulate(_part, _ext, _holes)
                        if _ec:
                            _v2, _t2, _ = _ec
                            _base = len(_all_v2d)
                            _all_v2d.extend(_v2)
                            _all_tris += [
                                (a + _base, b + _base, c + _base) for a, b, c in _t2
                            ]
                    if _all_v2d and _all_tris:
                        _mc = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
                        _bz = _roads_bz - gen.elevation.el_sCutDepth
                        _tz = max(v.z for v in _mc) + 20.0
                        _cutter_tmp = _build_extruded_mesh(
                            _all_v2d, _all_tris, _bz, _tz
                        )
                        recalculateNormals(_cutter_tmp)
                        roads_cutter = _cutter_tmp

            roads_manifold = is_mesh_manifold(roads_obj)

            # Cut roads out of main terrain
            _ov = _progress.ProgressOverlay.get()
            if _ov.active:
                _ov.update(message="Cutting road from Terrain…")
            solver = (
                "MANIFOLD" if (roads_manifold and is_mesh_manifold(obj)) else "EXACT"
            )
            boolean_operation(obj, roads_cutter, solver=solver)

            # Cut roads out of each terrain element
            TERRAIN_PRIORITY_ORDER = [
                "water",
                "forest",
                "scree",
                "city",
                "greenspace",
                "farmland",
                "glacier",
                "ocean",
            ]
            for key in TERRAIN_PRIORITY_ORDER:
                elem_obj = terrain.get(key)
                if not elem_obj:
                    continue
                _ov = _progress.ProgressOverlay.get()
                if _ov.active:
                    _ov.update(message=f"Cutting road from {key.capitalize()}…")
                solver = (
                    "MANIFOLD"
                    if (roads_manifold and is_mesh_manifold(elem_obj))
                    else "EXACT"
                )
                boolean_operation(elem_obj, roads_cutter, solver=solver)

            if _cutter_tmp is not None:
                remove_objects(_cutter_tmp)

        except Exception as e:
            raise GenerationError(
                f"Failed to cut roads from terrain and elements: {e}"
            ) from e

    def _subtract_trail_from_buildings(
        gen: GenerationContext, thickerCurves, terrain: dict
    ):
        """Subtract the trail groove from buildings so it isn't blocked by 3D geometry."""
        from ..mesh_ops import boolean_operation, is_mesh_manifold

        try:
            elem_obj = terrain.get("buildings")
            if elem_obj is None:
                return

            _ov = _progress.ProgressOverlay.get()
            if _ov.active:
                _ov.update(message="Subtracting trail from Buildings…")
            for tcrv in thickerCurves:
                solver = (
                    "MANIFOLD"
                    if (is_mesh_manifold(elem_obj) and is_mesh_manifold(tcrv))
                    else "EXACT"
                )
                boolean_operation(elem_obj, tcrv, solver=solver)

        except Exception as e:
            raise GenerationError(
                f"Failed to subtract trail from buildings: {e}"
            ) from e

    def _finalise_roads(gen: GenerationContext, terrain: dict, trail_thick_ribbons):
        """Rebuild the road top surface, subtract trails in 2D, and repair non‑manifold edges."""
        from .. import geometry2d as _g2d
        from ..osm.roads import finalize_roads

        try:
            roads_obj = gen.runtime.roadObj
            if roads_obj is None:
                return

            _ov = _progress.ProgressOverlay.get()
            if _ov.active:
                _ov.update(message="Roads: adding terrain detail…")

            el_sHeight = gen.elevation.el_sHeight
            full_depth = gen.settings.elementMode != "PAINT"
            _roads_poly = gen.runtime.roadUnion
            terrain_tris: list = terrain.get("_terrain_tris_cache")
            if trail_thick_ribbons and _roads_poly is not None:
                _trail_union = _g2d.union(trail_thick_ribbons)
                _roads_poly = _roads_poly.difference(_trail_union)
                print(
                    f"[TP3D roads] subtracted {len(trail_thick_ribbons)} trail ribbon(s) from roads_polygon in 2D"
                )
            print(f"Full depth value before finallizing roads: {full_depth}")
            # mapOutline is in local object space; road polygon is in world space — translate to match.
            _map_outline_world = None
            if gen.runtime.mapOutline is not None and gen.runtime.mapObject is not None:
                from shapely.affinity import translate as _shp_translate
                _map_outline_world = _shp_translate(
                    gen.runtime.mapOutline,
                    xoff=gen.runtime.mapObject.location.x,
                    yoff=gen.runtime.mapObject.location.y,
                )
            finalize_roads(
                roads_obj,
                terrain_tris,
                _roads_poly,
                el_sHeight,
                full_depth,
                map_polygon=_map_outline_world,
                cut_depth=gen.elevation.el_sCutDepth,
            )

            # Repair non‑manifold boundary edges
            bpy.ops.object.select_all(action="DESELECT")
            roads_obj.select_set(True)
            bpy.context.view_layer.objects.active = roads_obj
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.context.tool_settings.mesh_select_mode = (True, False, False)
            bpy.ops.mesh.select_all(action="DESELECT")
            bpy.ops.mesh.select_non_manifold(
                extend=False,
                use_wire=False,
                use_boundary=True,
                use_multi_face=False,
                use_non_contiguous=False,
                use_verts=False,
            )
            bpy.ops.mesh.dissolve_limited(angle_limit=0.0872665)  # 5°
            bpy.ops.mesh.remove_doubles(threshold=0.001)
            bpy.ops.mesh.select_non_manifold(
                extend=False,
                use_wire=False,
                use_boundary=True,
                use_multi_face=False,
                use_non_contiguous=False,
                use_verts=False,
            )
            bpy.ops.mesh.fill_holes(sides=0)
            bpy.ops.object.mode_set(mode="OBJECT")

        except Exception as e:
            raise GenerationError(f"Failed to finalise roads: {e}") from e

    def _cleanup_thicker_curves(thickerCurves, debug: bool, map_size: float):
        """Either move thicker curves aside (debug) or delete them."""
        try:
            if debug:
                for tcrv in thickerCurves:
                    tcrv.location.x += map_size
            else:
                from ..scene import remove_objects

                remove_objects(thickerCurves)
        except Exception as e:
            raise GenerationError(f"Failed to clean up thicker curves: {e}") from e

    obj = gen.runtime.mapObject
    terrain: dict[str, Any] = cast(dict[str, Any], gen.runtime.elements)
    assert terrain is not None

    # Initialize lists that will be filled by helpers
    thickerCurves = []
    trail_thick_ribbons = []

    # Step 1: Process curve projections (only for SINGLECOLORMODE_REMESH with curves)
    if gen.settings.elementMode == "SINGLECOLORMODE_REMESH" and gen.runtime.curveObjs:
        thickerCurves, trail_thick_ribbons = _process_curve_projections(gen, obj)

    # Step 2: In PAINT mode, clip trail curves to the map shape and collect
    # trail ribbons from the (now-clipped) _Trail curves
    if gen.settings.elementMode == "PAINT":
        _clip_paint_trail_curves_to_map(gen, obj)
        paint_ribbons = _collect_paint_trail_ribbons(gen)
        trail_thick_ribbons.extend(paint_ribbons)

    # Step 3: Store trail union for texture rasterisation
    if gen.texture.useTexture and gen.texture.texTrail and trail_thick_ribbons:
        _store_trail_union_for_texture(gen, terrain, trail_thick_ribbons)

    # Step 4: Apply single‑color mode booleans (only for SINGLECOLORMODE_REMESH)
    if gen.settings.elementMode == "SINGLECOLORMODE_REMESH":
        _apply_single_color_mode_booleans(gen, obj, terrain, thickerCurves)

    # Step 5: Cut roads out of terrain and elements
    if gen.settings.elementMode == "SINGLECOLORMODE_REMESH":
        _cut_roads_from_terrain_and_elements(gen, obj, terrain)

    # Step 6: Subtract trail grooves from buildings
    if thickerCurves:
        _subtract_trail_from_buildings(gen, thickerCurves, terrain)

    # Step 7: Finalise roads
    if gen.runtime.roadObj is not None:
        _finalise_roads(gen, terrain, trail_thick_ribbons)

    # Step 8: Clean up temporary thicker curves
    _cleanup_thicker_curves(thickerCurves, bpy.app.debug, gen.settings.size)


