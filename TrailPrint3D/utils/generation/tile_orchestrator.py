import math
import time

import bpy  # type: ignore
from mathutils import Vector  # type: ignore

from ... import progress as _progress
from ..dataclasses import GenerationContext, GenerationError, ValidationError
from ..ui_state import build_fetch_items
from .elements import (
    _rg_apply_single_color_mode,
    _rg_build_terrain_elements,
)
from .input import _rg_validate_inputs
from .output import (
    _rg_apply_texture,
    _rg_assign_materials,
    _rg_finalize_metadata,
    _rg_set_material_preview,
)
from .terrain_gen import _rg_create_satellite_plane, _rg_start_satellite_prefetch

# ---------------------------------------------------------------------------
# createTerrainFromSelected sub-phase helpers
#
# Builds terrain on already-placed tile objects (blanks dropped by the map
# picker / puzzle picker / Extend flows) rather than running runGeneration's
# own from-scratch GPX pipeline -- shares the same _rg_* building blocks
# above, just driven from a different entry point.
# ---------------------------------------------------------------------------


def _rtg_build_gen() -> GenerationContext:
    """Build a GenerationContext for the tile-based flow.

    createTerrainFromSelected drives already-placed tile objects rather than
    runGeneration's own GPX pipeline, so no generation flags are needed --
    this reuses _rg_validate_inputs purely to load current scene settings
    into the same dataclasses the rest of the _rg_* pipeline expects.
    """
    gen = _rg_validate_inputs(frozenset(), gen_type=0)
    gen.runtime.sScaleHor = bpy.context.scene.tp3d.get("sScaleHor", 1)
    return gen


def _rtg_apply_elevation(
    gen: GenerationContext,
    zobj,
    additionalExtrusion,
    progress_cb=None,
    skip_bottom_recess=False,
):
    """Fetch terrain elevation, apply to vertices, extrude bottom face, shift to z=0.

    Returns (lowestZ, highestZ, additionalExtrusion, n_elev_pts).
    The returned additionalExtrusion may differ from the passed-in value
    when indipendendTiles is True.

    skip_bottom_recess: the recess-the-bottom-face safety net below exists to
    keep an EXTENDED tile's surface seamless with a neighbor it has to match
    baselines with -- additionalExtrusion is deliberately locked to that
    neighbor's own lowest point, so this tile's own terrain can legitimately
    dip below it. A fresh single tile with no neighbor to match (e.g. a
    puzzle blank) has additionalExtrusion set to ITS OWN lowest point, so
    clearance should always equal minThickness exactly -- any shortfall there
    is just float-precision noise between the caller's own preview lowestZ
    pass and this function's, not a real seam to protect, and the 1mm-step
    loop below would force at least a 1mm recess off that noise alone.
    """
    from ..elevation import (
        compute_and_store_tile_bounds,  # deferred to avoid circular import at load time
        get_tile_elevation,
    )
    from ..geo import (
        convert_to_geo,  # deferred to avoid circular import at load time
    )

    tp3d = bpy.context.scene.tp3d
    scaleElevation = gen.settings.scaleElevation
    minThickness = gen.settings.minThickness
    indipendendTiles = tp3d.indipendendTiles

    print(f"additionalExtrusion: {additionalExtrusion}")

    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    gen.runtime.mapObject = zobj
    # zobj was built by one of primitives.create_*()/build_tile_from_polygon(), which
    # all stamp map_polygon_wkt via build_mesh_from_polygon() -- restore it into
    # gen.runtime.mapOutline the same way _rg_create_map_object() does for the
    # from-scratch flow. Without this, gen.runtime.mapOutline stays at its dataclass
    # default of None for every tile-based generation (map picker / puzzle picker /
    # Extend), so smooth_polygon_taubin()'s outline-pinning in terrain.py silently
    # treats every element boundary vertex as unpinned instead of raising an error --
    # element (e.g. water) edges get Taubin-smoothed right across the tile's own
    # outline instead of staying pinned to it.
    if "map_polygon_wkt" in zobj:
        from shapely.wkt import loads as _shp_loads  # deferred to avoid circular import at load time

        gen.runtime.mapOutline = _shp_loads(zobj["map_polygon_wkt"])
    # get_tile_elevation only populates gen.runtime.mapKm/tbMin*/tbMax* via its
    # own internal call to compute_and_store_tile_bounds when that call is made
    # with the bare object -- call it here first so _rg_build_terrain_elements
    # (which needs gen.runtime.mapKm) sees it too.
    compute_and_store_tile_bounds(gen)
    # This tile's own bbox is now known -- start the WorldCover prefetch (a
    # no-op unless elementSource == "WORLDCOVER") here so its network request
    # overlaps get_tile_elevation()'s below, same overlap runGeneration gets
    # by starting it alongside its own elevation fetch. Joined later by
    # _rg_create_satellite_plane() in _rtg_process_tile, once this tile's
    # mesh has real elevation to test face normals against.
    _rg_start_satellite_prefetch(gen)
    get_tile_elevation(gen, progress_cb=progress_cb)
    tileVerts = gen.runtime.tileVerts or []

    # Reset scene property to original value before this tile
    tp3d.sAdditionalExtrusion = additionalExtrusion

    if len(tileVerts) < 500:
        _progress.WarningsOverlay.add_warning(
            f"Mesh has only {len(tileVerts)} Points. Increase Resolution for higher Quality",
            "warn",
        )

    # createTerrainFromSelected reads the auto-scale from the scene rather than
    # computing it itself -- callers pre-fetch elevation and set this before
    # invoking this flow (see operators.py's _apply_puzzle_result).
    autoScale = tp3d.sAutoScale

    # Find elevation range
    mesh = zobj.data
    lowestZ = 1000
    highestZ = 0
    _obj_matrix = zobj.matrix_world
    for i, vert in enumerate(mesh.vertices):
        _world_co = _obj_matrix @ vert.co
        _vert_lat, _unused_var = convert_to_geo(_world_co.x, _world_co.y)
        _merc = 1 / math.cos(math.radians(_vert_lat))
        val = tileVerts[i] / 1000 * scaleElevation * autoScale * _merc
        lowestZ = min(lowestZ, val)
        highestZ = max(highestZ, val)

    # Apply elevation to vertices
    for i, vert in enumerate(mesh.vertices):
        _world_co = _obj_matrix @ vert.co
        _vert_lat, _unused_var = convert_to_geo(_world_co.x, _world_co.y)
        _merc = 1 / math.cos(math.radians(_vert_lat))
        vert.co.z = tileVerts[i] / 1000 * scaleElevation * autoScale * _merc
        lowestZ = min(lowestZ, vert.co.z)
        highestZ = max(highestZ, vert.co.z)

    if gen.settings.smoothTerrainTop:
        # Unlike _rg_displace_terrain_with_curve (terrain_gen.py), this tile-based
        # flow (map picker / puzzle picker / Extend) assigns elevation through the
        # plain per-vertex loop above rather than foreach_get/set -- smoothing was
        # simply never wired in here, so Smooth Terrain had zero effect on tiles
        # built this way, at any strength.
        import numpy as np

        from ..terrain import smooth_terrain_top_z

        _n = len(mesh.vertices)
        co_flat = np.empty(_n * 3, dtype=np.float64)
        mesh.vertices.foreach_get("co", co_flat)
        co = co_flat.reshape((_n, 3))
        co[:, 2] = smooth_terrain_top_z(
            co[:, 0], co[:, 1], co[:, 2], iterations=gen.settings.smoothTerrainStrength
        )
        mesh.vertices.foreach_set("co", co.ravel())
        mesh.update()
        lowestZ = float(co[:, 2].min())
        highestZ = float(co[:, 2].max())

    # additionalExtrusion is locked to lowestZ (this tile's own base) only once
    # elevation is in its final, possibly-smoothed form -- computing this earlier
    # (before smoothing) would leave it referencing the pre-smoothing low point.
    if indipendendTiles:
        additionalExtrusion = lowestZ

    # Extrude bottom face and set its z
    bpy.context.view_layer.objects.active = zobj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.extrude_region_move()
    bpy.ops.transform.translate(value=(0, 0, -8))
    bpy.ops.mesh.dissolve_faces()
    bpy.ops.object.mode_set(mode="OBJECT")

    mesh = zobj.data
    selected_faces = [face for face in mesh.polygons if face.select]
    if selected_faces:
        for face in selected_faces:
            for vert_idx in face.vertices:
                mesh.vertices[vert_idx].co.z = additionalExtrusion - minThickness
    else:
        print("No face selected.")

    # Shift geometry so bottom sits at correct z
    bpy.context.view_layer.objects.active = zobj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.transform.translate(value=(0, 0, -additionalExtrusion + minThickness))
    bpy.ops.object.mode_set(mode="OBJECT")

    # When extending, additionalExtrusion is locked to an older tile's own
    # lowest point so the terrain surface stays seamless across the join. If
    # this tile's own terrain dips lower than that, the bottom (always at
    # z=0 by construction above) leaves less than minThickness of material —
    # or goes negative — at the low point. Recess just the bottom face
    # further down in 1mm steps until clearance is restored.
    #
    # Only triggers below HALF of minThickness (not the full value) -- a
    # small shortfall here is normal/harmless (e.g. float-precision noise,
    # or genuinely just a bit thin) and forcing a 1mm recess for every minor
    # case was overzealous; this only steps in once it's actually thin
    # enough to matter structurally.
    if not skip_bottom_recess:
        min_clearance = minThickness / 2
        clearance = lowestZ - additionalExtrusion + minThickness
        bottom_drop = 0.0
        while clearance < min_clearance:
            bottom_drop += 1.0
            clearance += 1.0
        if bottom_drop > 0 and selected_faces:
            for face in selected_faces:
                for vert_idx in face.vertices:
                    mesh.vertices[vert_idx].co.z -= bottom_drop
            _progress.WarningsOverlay.add_warning(
                f"{zobj.name}: base recessed {bottom_drop:.0f}mm to keep the terrain seamless with the existing map",
                "warn",
            )

    return lowestZ, highestZ, additionalExtrusion, len(tileVerts)


def _rtg_handle_trail(zobj, duplicate, singleColorMode):
    """Intersect or project trail curves onto this tile.

    In normal mode (singleColorMode=False): creates one extruded duplicate per
    _Trail curve and intersects each individually, returning a list of results.
    In single-color mode (singleColorMode=True): copies each trail curve for later
    processing by _rg_apply_single_color_mode.

    Returns curveObjs list (may be empty).
    """
    from ..mesh_ops import (
        intersect_trail_with_existing_box,  # deferred to avoid circular import at load time
    )

    def _xy_extents(obj):
        corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        xs = [c.x for c in corners]
        ys = [c.y for c in corners]
        return min(xs), max(xs), min(ys), max(ys)

    tx_min, tx_max, ty_min, ty_max = _xy_extents(zobj)

    def _near_tile(ob):
        cx_min, cx_max, cy_min, cy_max = _xy_extents(ob)
        return (
            cx_min <= tx_max
            and cx_max >= tx_min
            and cy_min <= ty_max
            and cy_max >= ty_min
        )

    search_str = "_Trail"
    matches = [
        ob
        for ob in bpy.context.view_layer.objects
        if search_str in ob.name and _near_tile(ob)
    ]
    curveObjs = []

    print(f"matches: {matches}")

    if singleColorMode and matches:
        for c in matches:
            if c.type == "CURVE":
                cd = c.copy()
                cd.data = c.data.copy()
                bpy.context.collection.objects.link(cd)
                curveObjs.append(cd)

    elif not singleColorMode and matches:
        trail_matches = [
            ob
            for ob in matches
            if ob.type in {"CURVE", "MESH"}
            and not ob.hide_get()
            and ob.name in bpy.context.view_layer.objects
        ]
        for i, trail in enumerate(trail_matches):
            if i == 0 and duplicate is not None:
                dup = duplicate
            else:
                dup = zobj.copy()
                dup.data = zobj.data.copy()
                bpy.context.collection.objects.link(dup)
                for col in zobj.users_collection:
                    if dup.name not in col.objects:
                        col.objects.link(dup)

            bpy.ops.object.select_all(action="DESELECT")
            dup.select_set(True)
            zobj.select_set(False)
            bpy.context.view_layer.objects.active = dup
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.mesh.extrude_region_move()
            bpy.ops.transform.translate(value=(0, 0, 1))
            bpy.ops.object.mode_set(mode="OBJECT")
            dup.name = f"{zobj.name}_TRAIL_{i}"
            dup_name = dup.name
            intersect_trail_with_existing_box(dup, trail)
            if dup_name in bpy.data.objects:
                curveObjs.append(bpy.data.objects[dup_name])

    return curveObjs


def _rtg_build_tile_preview(selected_objects):
    """Build the multi-tile map-preview overlay payload (only meaningful for 2+ tiles).

    Mirrors createTerrainFromSelected's own per-tile skip conditions exactly:
    must be MESH, objType absent OR == "MAP", and not already processed
    (highestZ/lowestZ both already set). Returns (valid_objs, tiles_info,
    tile_size) -- tiles_info stays empty when there's fewer than 2 valid
    tiles, since a single tile has nothing to preview against.
    """
    valid = [
        obj
        for obj in selected_objects
        if obj.type == "MESH"
        and obj.get("objType", "MAP") == "MAP"
        and not (obj.get("highestZ", 0) != 0 and obj.get("lowestZ", 0) != 0)
    ]
    tile_size = float(valid[0].get("objSize", 1.0)) if valid else 1.0
    tiles_info = []
    if len(valid) >= 2:
        tiles_info = [
            {
                "bx": round(float(obj.location.x), 3),
                "by": round(float(obj.location.y), 3),
                "status": "pending",
                "shape": obj.get("Shape", "square").lower().split()[0],
            }
            for obj in valid
        ]
    return valid, tiles_info, tile_size


def _rtg_update_tile_preview_status(overlay, valid, tiles_info, tile_size, zobj):
    """Advance the map-preview's per-tile status markers as the loop reaches zobj."""
    if not tiles_info or zobj not in valid:
        return
    idx = valid.index(zobj)
    for k in range(idx):
        tiles_info[k]["status"] = "done"
    tiles_info[idx]["status"] = "active"
    overlay.set_map_preview({"tiles": tiles_info, "tile_size": round(tile_size, 3)})


def _rtg_process_tile(
    gen: GenerationContext,
    zobj,
    tile_idx: int,
    n_tiles: int,
    additionalExtrusion: float,
    skip_bottom_recess: bool,
    overlay,
    map_km: float,
    prefetched_osm=None,
):
    """Run one tile through the same back-half phases runGeneration itself uses.

    Mirrors runGeneration's phases 14-17 (terrain elements, single-color
    mode, materials, texture) via the shared _rg_* functions, with the
    elevation+trail steps handled by _rtg_apply_elevation/_rtg_handle_trail
    instead of runGeneration's curve-driven displacement (these tiles are
    pre-shaped primitives, not a trail-derived outline).

    prefetched_osm: optional {kind: {bbox: (data, from_cache)}} dataset already
    fetched for the WHOLE multi-tile batch (see
    terrain_gen.fetch_combined_osm_data), forwarded straight to
    _rg_build_terrain_elements so this one tile reuses/clips it instead of
    querying Overpass with its own small bbox -- see that function's
    prefetched_osm docstring for why that matters for an OSM element bigger
    than one physical tile.

    Raises GenerationError on failure -- caught by the caller's per-tile loop.
    Returns (lowestZ, highestZ, additionalExtrusion).
    """
    from ..mesh_ops import (
        recalculateNormals,  # deferred to avoid circular import at load time
    )

    tile_label = f"Tile {tile_idx + 1}/{n_tiles}"
    base_pct = tile_idx / n_tiles
    step = 1.0 / n_tiles

    overlay.set_fetch_items(build_fetch_items(map_km))

    # --- Phase 1: Apply terrain elevation + extrude bottom face ---
    overlay.update(
        base_pct + step * 0.00,
        "Fetching Elevation",
        f"{tile_label} — querying elevation API…",
    )
    overlay.set_fetch_progress("elevation", 0.0)

    def _elev_progress(pct):
        t = pct / 100.0
        overlay.update(
            base_pct + step * t * 0.50,
            "Fetching Elevation",
            f"{tile_label} — {pct}% complete…",
            sub_percent=t,
            sub_label="Elevation tiles",
        )
        overlay.set_fetch_progress("elevation", t)

    lowestZ, highestZ, additionalExtrusion, n_elev_pts = _rtg_apply_elevation(
        gen,
        zobj,
        additionalExtrusion,
        progress_cb=_elev_progress,
        skip_bottom_recess=skip_bottom_recess,
    )
    overlay.sub_percent = None
    overlay.set_fetch_done("elevation", success=True)
    overlay.update(
        base_pct + step * 0.50,
        "Elevation Ready",
        f"{tile_label} — {n_elev_pts} pts, z {lowestZ:.1f}–{highestZ:.1f}",
    )
    overlay.add_completed_step(
        f"{tile_label} — elevation fetched ({n_elev_pts} pts, z {lowestZ:.1f}–{highestZ:.1f})"
    )

    # --- Phase 2: Trail projection / intersection ---
    overlay.update(
        base_pct + step * 0.60,
        "Building Trail",
        f"{tile_label} — projecting trail onto terrain…",
    )
    curveObjs = _rtg_handle_trail(zobj, None, gen.settings.singleColorMode)
    gen.runtime.curveObjs = curveObjs
    _n_trails = len(curveObjs)
    overlay.add_completed_step(
        f"{tile_label} — trail built ({_n_trails} seg{'s' if _n_trails != 1 else ''})"
        if _n_trails
        else f"{tile_label} — no trail"
    )

    # --- Phase 3: Base material ---
    mat = bpy.data.materials.get("BASE")
    zobj.data.materials.clear()
    zobj.data.materials.append(mat)

    # --- Phase 3b: WorldCover reference plane + paint (no-op unless
    # elementSource == "WORLDCOVER") --- mirrors runGeneration's own
    # ordering: after the base material is in materials[0] (so any face the
    # land-cover sampler doesn't confidently classify keeps that as its
    # fallback, same as an unpainted single-flow terrain) and before OSM
    # elements below, so roads/buildings can still take priority over it.
    overlay.update(
        base_pct + step * 0.65, "Land Cover", f"{tile_label} — sampling WorldCover…"
    )
    _rg_create_satellite_plane(gen)

    # --- Phase 4: Terrain overlay elements (water, forest, city, glacier, buildings, roads) ---
    _elem_start = base_pct + step * 0.70
    _elem_end = base_pct + step * 0.93
    overlay.update(
        _elem_start, "Terrain Elements", f"{tile_label} — building overlay layers…"
    )
    terrain = _rg_build_terrain_elements(
        gen,
        phase_start=_elem_start,
        phase_end=_elem_end,
        tile_label=tile_label,
        prefetched_osm=prefetched_osm,
    )
    if terrain["roads"]:
        terrain["roads"].location.z += 0.4
    _found = [k for k, v in terrain.items() if v is not None]
    overlay.add_completed_step(
        f"{tile_label} — elements: {', '.join(_found)}"
        if _found
        else f"{tile_label} — no elements"
    )

    recalculateNormals(zobj)

    # --- Phase 5: Single color mode processing ---
    overlay.update(
        base_pct + step * 0.85,
        "Coloring",
        f"{tile_label} — applying single-color mode…",
    )
    _rg_apply_single_color_mode(gen)

    # --- Phase 6: Assign materials ---
    overlay.update(
        base_pct + step * 0.90, "Finalizing", f"{tile_label} — assigning materials…"
    )
    _rg_assign_materials(gen)

    # --- Phase 7: Rasterise OSM polygons into a UV paint texture, when enabled ---
    # _rg_build_terrain_elements already populated terrain['_osm_polygons']
    # (elements.py) and discarded the road mesh; this just bakes it.
    if gen.texture.useTexture:
        overlay.update(
            base_pct + step * 0.93,
            "Texture",
            f"{tile_label} — rasterising OSM texture…",
        )
        _rg_apply_texture(gen)

    bpy.ops.object.select_all(action="DESELECT")
    zobj.select_set(False)
    zobj["lowestZ"] += additionalExtrusion
    zobj["highestZ"] += additionalExtrusion

    return lowestZ, highestZ, additionalExtrusion


def runTileGeneration(manage_overlay=True, skip_bottom_recess=False, prefetched_osm=None):
    """Run the generation pipeline's back half on already-placed tile objects.

    An orchestrator in its own right, same as runGeneration -- the map
    picker, puzzle picker, and Extend flows all funnel into this rather than
    runGeneration's own from-scratch GPX pipeline (see the module docstring
    above). Shares the exact same _rg_* phase functions runGeneration uses
    for terrain elements/coloring/materials/texture (_rtg_process_tile);
    only the elevation+trail step differs, since these tiles are pre-shaped
    primitives rather than a trail-derived outline.

    manage_overlay: when False the caller owns the ProgressOverlay lifecycle
    (start/finish).  All internal update/step calls still run normally so the
    caller's overlay reflects terrain progress.

    skip_bottom_recess: forwarded to _rtg_apply_elevation -- see its
    docstring. Pass True for fresh single-tile callers with no neighbor
    baseline to protect (e.g. the puzzle generator).

    prefetched_osm: optional {kind: {bbox: (data, from_cache)}} OSM dataset
    already fetched for the combined bbox of every tile in *selected_objects*
    (see generation/terrain_gen.py's fetch_combined_osm_data) -- forwarded to
    every tile's _rtg_process_tile so a multi-tile batch (e.g.
    premium/operators_pe.py's _apply_grid_segments) fetches each OSM kind
    once for the whole batch instead of once per physical tile. None (the
    default) preserves the original per-tile fetch for every other caller.

    Does NOT export -- unlike runGeneration's own Phase 18, a single call
    here doesn't necessarily mean the caller's final deliverable is ready
    (the puzzle generator's blank, for instance, is a single tile here but
    goes on to be cut into multiple pieces afterward). Returns the built
    GenerationContext on success (None on early-exit/failure) so a caller
    whose tile(s) really are the finished, exportable result -- e.g. the map
    picker's normal single map tile -- can pass it to _rg_export itself once
    any of its own post-processing (like merging an imported GPX trail) is
    done.
    """
    from ..primitives import (
        setupColors,  # deferred to avoid circular import at load time
    )

    overlay = _progress.ProgressOverlay.get()
    if manage_overlay:
        overlay.start()
        _progress.WarningsOverlay.clear()

    print("------------------------------------------------")
    print("SCRIPT STARTED - createTerrainFromSelected")
    print("------------------------------------------------")

    # Mirrors runGeneration's own try/except(ValidationError, GenerationError,
    # Exception)/finally -- guarantees overlay.finish() runs even when a tile
    # or a step outside the per-tile loop raises something unexpected, so the
    # overlay never gets stuck open under manage_overlay=False callers either.
    try:
        gen = _rtg_build_gen()

        start_time = time.time()

        tp3d = bpy.context.scene.tp3d
        if tp3d.selfHosted != "" and tp3d.selfHosted is not None and tp3d.api == 1:
            print(f"!!using {tp3d.selfHosted} instead of Opentopodata!!")

        setupColors()

        overlay.update(0.02, "Initializing", "Validating selection…")

        selected_objects = bpy.context.selected_objects
        if not selected_objects:
            from ..scene import (
                show_message_box,  # deferred to avoid circular import at load time
            )

            show_message_box("No objects selected")
            return None

        bpy.ops.object.select_all(action="DESELECT")

        lowestZ = 0
        highestZ = 0
        additionalExtrusion = tp3d.sAdditionalExtrusion

        _map_km = round(bpy.context.scene.tp3d.get("sMapInKm", 0), 1)
        overlay.set_fetch_items(build_fetch_items(_map_km))

        _mp_valid, _mp_tiles_info, _mp_tile_size = _rtg_build_tile_preview(
            selected_objects
        )
        if _mp_tiles_info:
            overlay.set_map_preview(
                {"tiles": _mp_tiles_info, "tile_size": round(_mp_tile_size, 3)}
            )

        n_tiles = len(selected_objects)
        for tile_idx, zobj in enumerate(selected_objects):
            bpy.ops.object.select_all(action="DESELECT")
            bpy.context.scene.cursor.location = zobj.location

            if zobj.type != "MESH":
                continue
            if "objType" in zobj and zobj["objType"] != "MAP":
                continue
            if (
                "highestZ" in zobj
                and "lowestZ" in zobj
                and zobj["highestZ"] != 0
                and zobj["lowestZ"] != 0
            ):
                continue

            if (
                _mp_tiles_info
                and _progress.SubprocessProgress.get().is_cancel_requested()
            ):
                break

            _rtg_update_tile_preview_status(
                overlay, _mp_valid, _mp_tiles_info, _mp_tile_size, zobj
            )

            tile_label = f"Tile {tile_idx + 1}/{n_tiles}"
            try:
                lowestZ, highestZ, additionalExtrusion = _rtg_process_tile(
                    gen,
                    zobj,
                    tile_idx,
                    n_tiles,
                    additionalExtrusion,
                    skip_bottom_recess,
                    overlay,
                    _map_km,
                    prefetched_osm=prefetched_osm,
                )
            except GenerationError as e:
                print(f"{tile_label} — generation phase failed: {e}")
                _progress.WarningsOverlay.add_warning(
                    f"{tile_label}: {e}", icon="error"
                )
                continue
            except Exception as e:  # noqa: BLE001 - a single tile's bpy.ops/mesh-op failure shouldn't abort the whole batch
                import traceback

                traceback.print_exc()
                print(f"{tile_label} — unexpected failure: {e}")
                _progress.WarningsOverlay.add_warning(
                    f"{tile_label}: unexpected failure, check console for details",
                    icon="error",
                )
                continue

        # Mark all tiles done in the preview
        if _mp_tiles_info:
            for _info in _mp_tiles_info:
                _info["status"] = "done"
            overlay.set_map_preview(
                {"tiles": _mp_tiles_info, "tile_size": round(_mp_tile_size, 3)}
            )

        bpy.context.view_layer.objects.active = selected_objects[0]
        for zobj in selected_objects:
            zobj.select_set(True)

        _rg_finalize_metadata(gen, start_time, lowestZ=lowestZ, highestZ=highestZ)

        # Check for/set material preview mode
        _rg_set_material_preview()

        _elapsed = int(time.time() - overlay._start_time) if overlay._start_time else 0
        _m, _s = divmod(_elapsed, 60)
        overlay.add_completed_step(f"Done  —  {_m:02d}:{_s:02d} total")
        if n_tiles > 1:
            _progress.WarningsOverlay.add_warning(
                "Multi-tile maps are not exported automatically — please use the Export buttons to export your tiles manually.",
                "warn",
            )
        return gen
    except ValidationError as e:
        print(f"Validation Failed: {e}")
        _progress.WarningsOverlay.add_warning(f"Error: {e}")
        return None
    except GenerationError as e:
        print(f"Generation phase failed: {e}")
        _progress.WarningsOverlay.add_warning(str(e), icon="error")
        return None
    except Exception as e:  # noqa: BLE001 - createTerrainFromSelected could raise many kinds of errors, mirrors runGeneration's own bare catch-all
        import traceback

        traceback.print_exc()
        print(f"Generation failed: {e}")
        _progress.WarningsOverlay.add_warning(
            "Generation failed, check console for details"
        )
        return None
    finally:
        if manage_overlay:
            overlay.finish()
            _progress.WarningsOverlay.get().show()


def _gjt_bake_into_existing_textures(curve_obj):
    """Rasterize curve_obj's own path onto the MMU_Paint texture of every
    nearby MAP object that already has one baked (CREATE_TEXTURE mode).

    Derives the trail ribbon polygon from curve_obj's own spline points the
    same way _rg_apply_single_color_mode's PAINT-mode branch does for a
    full-generation trail (see its "_Trail" scan there), but for one
    already-known curve instead of scanning the whole scene by name, and
    via bake_trail_into_texture's non-destructive compositing write instead
    of a full setup_paint_texture rebuild (which would wipe every other
    already-baked element back to base colour).

    Returns True if it painted onto at least one map -- the caller should
    then discard the 3D curve, since it's now fully represented in the
    texture(s) it overlapped.
    """
    from ..geometry2d import polylines_to_ribbon
    from ..texture import bake_trail_into_texture

    tp3d = bpy.context.scene.tp3d
    mw = curve_obj.matrix_world
    coords = []
    for sp in curve_obj.data.splines:
        pts = sp.points if len(sp.points) > 0 else sp.bezier_points
        if len(pts) >= 2:
            coords.append([(mw @ Vector((p.co.x, p.co.y, p.co.z)))[:2] for p in pts])
    if not coords:
        return False
    ribbon = polylines_to_ribbon(
        coords, tp3d.pathThickness / 2 + tp3d.tolerance, quad_segs=4
    )
    if ribbon is None or ribbon.is_empty:
        return False

    def _xy_extents(o):
        corners = [o.matrix_world @ Vector(c) for c in o.bound_box]
        xs = [c.x for c in corners]
        ys = [c.y for c in corners]
        return min(xs), max(xs), min(ys), max(ys)

    tx_min, tx_max, ty_min, ty_max = _xy_extents(curve_obj)

    baked = False
    for ob in bpy.context.view_layer.objects:
        if ob.type != "MESH" or ob.get("objType") != "MAP":
            continue
        cx_min, cx_max, cy_min, cy_max = _xy_extents(ob)
        if cx_min > tx_max or cx_max < tx_min or cy_min > ty_max or cy_max < ty_min:
            continue
        _trail_mat = curve_obj.data.materials[0] if curve_obj.data.materials else None
        if bake_trail_into_texture(ob, ribbon, _trail_mat):
            baked = True
    return baked


def generateJustTrail(material="TRAIL"):
    from ..geo import (  # deferred to avoid circular import at load time
        convert_to_blender_coordinates,
        separate_duplicate_xy,
    )
    from ..io_gpx import read_gpx_file  # deferred to avoid circular import at load time
    from ..mesh_ops import (
        RaycastCurveToAnyMesh,  # deferred to avoid circular import at load time
    )
    from ..primitives import (  # deferred to avoid circular import at load time
        create_curve_from_coordinates,
        simplify_curve,
    )
    from ..scene import (
        show_message_box,  # deferred to avoid circular import at load time
    )

    props = bpy.context.scene.tp3d

    minThickness = props.minThickness
    additionalExtrusion = props.sAdditionalExtrusion

    overwritePathElevation = props.overwritePathElevation

    coordinates = []
    separate_paths = []
    blender_coords = []
    blender_coords_separate = []
    type = 0

    bpy.ops.object.select_all(action="DESELECT")

    try:
        separate_paths = read_gpx_file()
    except Exception:  # noqa: BLE001 — GPX/IGC parsing can raise many unpredictable types
        show_message_box(f"Something went Wrong reading the GPX. Type {type}")
    coordinates = [item for sublist in separate_paths for item in sublist]

    # RECALCULATE THE COORDS WITH AUTOSCALE APPLIED
    blender_coords = [
        convert_to_blender_coordinates(lat, lon, ele, timestamp)
        for lat, lon, ele, timestamp in coordinates
    ]

    if type == 1 or len(separate_paths) > 1:
        blender_coords_separate = [
            separate_duplicate_xy(
                [
                    convert_to_blender_coordinates(lat, lon, ele, timestamp)
                    for lat, lon, ele, timestamp in path
                ],
                0.05,
            )
            for path in separate_paths
        ]

    blender_coords = simplify_curve(blender_coords, 0.12)

    # PREVENT CLIPPING OF IDENTICAL COORDINATES
    blender_coords = separate_duplicate_xy(blender_coords, 0.05)

    if separate_paths is None:
        return
    print(len(separate_paths))

    if (type == 1 or len(separate_paths) > 1) and type != 4:
        blender_coords_separate = [
            separate_duplicate_xy(
                [
                    convert_to_blender_coordinates(lat, lon, ele, timestamp)
                    for lat, lon, ele, timestamp in path
                ],
                0.05,
            )
            for path in separate_paths
        ]

    curveObj = None
    try:
        if (type == 0 and len(blender_coords_separate) <= 1) and type != 2 or type == 4:
            if not blender_coords:
                return None
            create_curve_from_coordinates(None, blender_coords)
            curveObj = bpy.context.view_layer.objects.active
        elif (type == 1 or len(blender_coords_separate) > 1) and type != 4:
            for crds in blender_coords_separate:
                create_curve_from_coordinates(None, crds)

                bpy.ops.object.join()
                curveObj = bpy.context.view_layer.objects.active
    except RuntimeError as e:
        show_message_box(e)

    if curveObj:
        bpy.context.view_layer.objects.active = curveObj
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.curve.select_all(action="SELECT")
        bpy.ops.transform.translate(
            value=(0, 0, -additionalExtrusion + minThickness)
        )  # bpy.ops.mesh.select_all(action='DESELECT')
        bpy.ops.object.mode_set(mode="OBJECT")

    # sets 3D cursor to origin of tile
    if curveObj:
        curveObj.select_set(True)
        bpy.ops.object.origin_set(type="ORIGIN_CURSOR")

    # Raycast the curve points onto the Mesh surface
    if overwritePathElevation == True:
        # pass
        RaycastCurveToAnyMesh(curveObj, 1000, True)

    if curveObj:
        mat = bpy.data.materials.get(material)
        curveObj.data.materials.clear()
        curveObj.data.materials.append(mat)

    if (
        curveObj is not None
        and props.tex_use_texture
        and props.tex_include_trail
    ):
        if _gjt_bake_into_existing_textures(curveObj):
            bpy.data.objects.remove(curveObj, do_unlink=True)
            return None

    return curveObj
