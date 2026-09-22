import time

import bpy

from ... import progress as _progress
from ..dataclasses import GenerationContext, GenerationError, ValidationError
from ..ui_state import build_fetch_items
from .elements import (
    _rg_apply_single_color_mode,
    _rg_build_terrain_elements,
    _rg_create_text_and_overlays,
)
from .input import (
    _rg_calculate_horizontal_scale,
    _rg_compute_trail_stats,
    _rg_convert_then_center_coordinates,
    _rg_interpolate_path_curve,
    _rg_load_coordinates,
    _rg_validate_inputs,
)
from .output import (
    _rg_apply_texture,
    _rg_assign_materials,
    _rg_export,
    _rg_finalize_metadata,
    _rg_set_material_preview,
)
from .terrain_gen import (
    _cleanup_build_area,
    _rg_build_trail_curves,
    _rg_create_map_object,
    _rg_create_satellite_plane,
    _rg_displace_terrain_with_curve,
    _rg_extrude_terrain,
    _rg_fetch_elevation,
    _rg_prepare_trail_coords,
    _rg_start_osm_prefetch,
    _rg_start_satellite_prefetch,
)

# ---------------------------------------------------------------------------
# Generation feature flags
# Each type integer maps to a frozenset of capability strings used throughout
# the pipeline instead of scattered `if type == X` comparisons.
# ---------------------------------------------------------------------------

_GEN_FLAGS = {
    0: frozenset({"gpx_file", "trail", "stats", "gpx_scale"}),
    1: frozenset(
        {
            "gpx_chain",
            "trail",
            "stats",
            "gpx_scale",
            "separate_paths",
            "chain_coords_center",
        }
    ),
    2: frozenset({"jmap"}),
    3: frozenset({"jmap_bbox"}),
    4: frozenset({"gpx_file", "jmap", "trail", "trail_map"}),
    10: frozenset({"gpx_file", "stats", "gpx_scale"}),
    11: frozenset(
        {"gpx_chain", "stats", "gpx_scale", "separate_paths", "chain_coords_center"}
    ),
    20: frozenset({"gpx_file", "trail", "stats", "gpx_scale", "append_collection"}),
    21: frozenset(
        {
            "gpx_chain",
            "trail",
            "stats",
            "gpx_scale",
            "separate_paths",
            "chain_coords_center",
            "append_collection",
        }
    ),
}

# ---------------------------------------------------------------------------
# Main generation orchestrator
# ---------------------------------------------------------------------------


def runGeneration(type, locked_scale=None):
    """Orchestrate the full 3D map generation pipeline."""

    flags = _GEN_FLAGS[type]
    overlay = _progress.ProgressOverlay.get()

    try:
        _progress.WarningsOverlay.clear()
        # --- Phase 1: Validate inputs and load all scene settings ---
        gen: GenerationContext = _rg_validate_inputs(
            flags, gen_type=type, locked_scale=locked_scale
        )
        overlay.start()
        overlay.update(0.03, "Initializing", "Validating inputs…")

        start_time = gen.runtime.start_time

        overlay.add_completed_step("Inputs validated")

        # --- Phase 2: Load coordinate data from GPX / synthetic source ---
        overlay.update(0.08, "Loading Data", "Reading GPX file…")
        _rg_load_coordinates(gen)

        # --- Phase 3: Calculate and store trail statistics ---
        overlay.update(
            0.12, "Trail Statistics", "Computing distances & elevation gain…"
        )
        _rg_compute_trail_stats(gen)

        # --- Phase 4: Interpolate path to at least 300 points for a smooth curve ---
        overlay.update(0.16, "Path Interpolation", "Smoothing trail curve…")
        _rg_interpolate_path_curve(gen)

        # --- Phase 5: Calculate horizontal scale factor ---
        overlay.update(0.20, "Scale Calculation", "Computing horizontal scale…")
        _rg_calculate_horizontal_scale(gen)

        # --- Phase 6: Convert to Blender coordinates and find map center ---
        overlay.update(0.24, "Coordinate Conversion", "Converting to Blender space…")
        _rg_convert_then_center_coordinates(gen)

        # --- Phase 7: Remove previously generated objects at the same location ---
        overlay.update(0.28, "Scene Cleanup", "Removing previous objects…")
        _cleanup_build_area(gen)

        overlay.add_completed_step(
            f"GPX loaded  —  {gen.runtime.gpx_stats.length:.1f} km, {int(gen.runtime.gpx_stats.elevation)} m gain"
            if "stats" in flags and gen.runtime.gpx_stats.length > 0
            else "GPX data loaded"
        )

        # --- Phase 8: Create base map shape ---
        overlay.update(0.33, "Building Map Shape", "Creating base mesh…")

        _rg_create_map_object(gen)

        overlay.add_completed_step(
            f"Map shape created  ({gen.settings.shape.capitalize()}, {round(gen.runtime.mapKm or 0, 1)} km)"
        )
        overlay.set_fetch_items(build_fetch_items(gen.runtime.mapKm))

        # --- OSM background prefetch: start now so Overpass requests overlap with elevation download ---
        _rg_start_osm_prefetch(gen)
        _rg_start_satellite_prefetch(gen)

        if gen.fetch.fetchThread is not None:
            print("OSM prefetch started (overlapping elevation download)")

        # --- Phase 9: Fetch terrain elevation data ---
        overlay.update(
            0.38, "Fetching Elevation Data", "Querying API — this may take a moment…"
        )

        _rg_fetch_elevation(gen)

        print("Elevation Data fetched")
        overlay.sub_percent = None  # hide sub-bar now that elevation is done
        overlay.set_fetch_done("elevation", success=True)
        overlay.add_completed_step(
            f"Elevation fetched  ({len(gen.runtime.tileVerts or [])} pts)"
        )
        overlay.update(
            0.65, "Elevation Data Ready", f"{len(gen.runtime.tileVerts or [])} points fetched"
        )

        # --- Phase 10a: Prepare trail Blender coordinates ---
        overlay.update(
            0.67, "Preparing Trail", "Converting and simplifying coordinates…"
        )
        _rg_prepare_trail_coords(gen)

        # --- Phase 10b: Build trail curves ---
        overlay.update(0.70, "Building Trail", "Creating curve objects…")
        _rg_build_trail_curves(gen)

        curveObjs = gen.runtime.curveObjs
        if curveObjs:
            bpy.context.scene.tp3d.currentTrail = curveObjs[0]

        _n_segs = len(curveObjs) if curveObjs else 0
        _n_pts = len(gen.runtime.blenderCoords) if gen.runtime.blenderCoords else 0
        overlay.add_completed_step(
            f"Trail built  —  {_n_segs} seg{'s' if _n_segs != 1 else ''}, {_n_pts} pts"
        )

        # --- Phase 11: Apply terrain elevation to mesh vertices ---
        overlay.update(0.75, "Applying Terrain", "Displacing mesh vertices…")

        _rg_displace_terrain_with_curve(gen)

        overlay.update(0.80, "Terrain Ready", "Vertices displaced…")
        overlay.sub_percent = None

        _rg_extrude_terrain(gen)

        # --- Phase 12-13: Create text / plate overlays for text-based shapes ---
        overlay.update(0.82, "Shape Overlays", "Adding text and plate elements…")

        _rg_create_text_and_overlays(gen)

        # --- Phase 14: Create terrain overlay elements ---
        overlay.update(0.83, "Terrain Elements", "Adding elements…")
        _rg_create_satellite_plane(gen)
        if gen.fetch.fetchThread is not None:
            gen.fetch.fetchThread.join()
        _rg_build_terrain_elements(gen, prefetched_osm=gen.fetch.fetchResult)

        # --- Phase 15: Single color mode processing ---
        overlay.update(0.95, "Coloring", "Applying single-color mode…")
        _rg_apply_single_color_mode(gen)

        # --- Phase 16: Assign materials ---
        overlay.update(0.95, "Finalizing", "Assigning materials…")
        _rg_assign_materials(gen)

        # --- Phase 17: Rasterise OSM polygons into UV texture ---
        if gen.texture.useTexture and gen.settings.elementChoice:
            overlay.update(0.96, "Texture", "Rasterising OSM texture…")
            _rg_apply_texture(gen)
            _lo = bpy.context.scene.tp3d.lowestZ
            _hi = bpy.context.scene.tp3d.highestZ
            overlay.add_completed_step(f"Terrain applied  —  z {_lo:.1f} to {_hi:.1f}")

        # --- Phase 18: Export ---
        overlay.update(0.97, "Exporting", "Exporting files…")
        _rg_export(gen)

        # Calculate script durations for prints and overlay updates
        duration = _rg_finalize_metadata(gen, start_time)
        bpy.context.scene.tp3d.sRunDuration = round(duration)

        if gen.runtime.mapObject:
            gen.runtime.mapObject["GenerationTime"] = round(duration)

        print(
            f"Finished. Generating Map took {duration:.0f} seconds",
            "----------------------------------------------------------------",
            " ",
        )
        
        # Check for/set material preview mode
        _rg_set_material_preview()

        _elapsed = int(time.time() - overlay._start_time) if overlay._start_time else 0
        _m, _s = divmod(_elapsed, 60)
        overlay.update(1.0, "Done", "")
        overlay.add_completed_step(f"Done  —  {_m:02d}:{_s:02d} total")
    except ValidationError as e:
        print(f"Validation Failed: {e}")
        _progress.WarningsOverlay.add_warning(f"Error: {e}")

    except GenerationError as e:
        print(f"Generation phase failed: {e}")
        _progress.WarningsOverlay.add_warning(str(e), icon="error")

    except Exception as e:  # noqa: BLE001 - runGeneration could raise many kinds of errors, for now I don't have a concrete list so.. bare exception.
        import traceback

        traceback.print_exc()
        print(f"Generation failed: {e}")
        _progress.WarningsOverlay.add_warning(
            "Generation failed, check console for details"
        )

    finally:
        overlay.finish()
        _progress.WarningsOverlay.get().show()


