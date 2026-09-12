import math
import threading
import time

import bpy  # type: ignore
import numpy as np  # type: ignore
from mathutils import Vector  # type: ignore

from ... import constants as const
from ..dataclasses import GenerationContext, GenerationError
from ..elevation import compute_and_store_tile_bounds
from ..ui_state import COLORING_ELEMENTS


def _cleanup_build_area(gen: GenerationContext):
    """Remove any existing objects in the build area before generating new geometry."""
    xOff = gen.settings.xTerrainOffset
    yOff = gen.settings.yTerrainOffset
    target_2d = Vector((gen.runtime.centerX or 0.0, gen.runtime.centerY or 0.0))
    target_2d_offset = Vector((gen.runtime.centerX or 0.0 + xOff, gen.runtime.centerY or 0.0 + yOff))
    for obs in bpy.data.objects:
        obj_2d = Vector((obs.location.x, obs.location.y))
        obj_2d_offset = obj_2d
        if "xTerrainOffset" in obs or "yTerrainOffset" in obs:
            obj_2d_offset = Vector(
                (
                    obs.location.x - obs["xTerrainOffset"],
                    obs.location.y - obs["yTerrainOffset"],
                )
            )
        if (
            (obj_2d - target_2d).length <= 0.2
            or (obj_2d - target_2d_offset).length <= 0.2
            or (obj_2d_offset - target_2d).length <= 0.2
            or (obj_2d_offset - target_2d_offset).length <= 0.2
        ):
            bpy.data.objects.remove(obs, do_unlink=True)
    bpy.ops.object.select_all(action="DESELECT")


def _rg_create_map_object(gen: GenerationContext):
    """Create, rotate, and position the base map shape object."""
    from shapely.wkt import loads

    from ..geo import (  # deferred to avoid circular import at load time
        convert_to_blender_coordinates,
        midpoint_spherical,
    )
    from ..mesh_ops import (
        recalculateNormals,  # deferred to avoid circular import at load time
    )
    from ..presets import (
        appendCollection,  # deferred to avoid circular import at load time
    )
    from ..primitives import (  # deferred to avoid circular import at load time
        create_circle,
        create_custom_geojson,
        create_custom_svg,
        create_ellipse,
        create_heart,
        create_hexagon,
        create_octagon,
        create_rectangle,
    )
    from ..scene import (
        transform_MapObject,  # deferred to avoid circular import at load time
        zoom_camera_to_selected,
    )

    MapObject = None

    if "append_collection" not in gen.settings.flags and "use_active_object" not in gen.settings.flags:
        print(
            f"[map_object] creating '{gen.settings.shape}' N={gen.settings.num_subdivisions} size={gen.settings.size:.1f}…"
        )
        _t_shape = time.time()
        if gen.settings.shape in {"SQUARE", "SQUARE SHELL"}:
            MapObject = create_rectangle(
                gen.settings.size, gen.settings.rectangleHeight, gen.settings.num_subdivisions, gen.settings.modelname
            )
        elif gen.settings.shape in {
            "HEXAGON",
            "HEXAGON SHELL",
            "HEXAGON INNER TEXT",
            "HEXAGON OUTER TEXT",
            "HEXAGON FRONT TEXT",
        }:
            MapObject = create_hexagon(
                gen.settings.size / 2, gen.settings.num_subdivisions, gen.settings.modelname
            )
        elif gen.settings.shape == "HEART":
            MapObject = create_heart(gen.settings.size / 2, gen.settings.num_subdivisions, gen.settings.modelname)
        elif gen.settings.shape in {"OCTAGON", "OCTAGON SHELL", "OCTAGON OUTER TEXT"}:
            MapObject = create_octagon(
                gen.settings.size / 2, gen.settings.num_subdivisions, gen.settings.modelname
            )
        elif gen.settings.shape in {"CIRCLE", "CIRCLE SHELL", "CIRCLE OUTER TEXT"}:
            MapObject = create_circle(gen.settings.size / 2, gen.settings.num_subdivisions, gen.settings.modelname)
        elif gen.settings.shape in {"ELLIPSE", "ELLIPSE SHELL"}:
            MapObject = create_ellipse(
                gen.settings.size / 2, gen.settings.num_subdivisions, gen.settings.modelname, gen.settings.ellipseRatio
            )
        elif gen.settings.shape == "GEOJSON":
            MapObject = create_custom_geojson(
                gen.settings.customFilePath, gen.settings.size, gen.settings.num_subdivisions, gen.settings.modelname
            )
        elif gen.settings.shape == "SVG":
            MapObject = create_custom_svg(
                gen.settings.customFilePath, gen.settings.size, gen.settings.num_subdivisions, gen.settings.modelname
            )
        else:
            MapObject = create_hexagon(
                gen.settings.size / 2, gen.settings.num_subdivisions, gen.settings.modelname
            )
        print(f"[map_object] shape created in {time.time() - _t_shape:.3f}s")
    if "append_collection" in gen.settings.flags:
        appendCollection()
        MapObject = bpy.context.view_layer.objects.active
        MapObject.location = Vector((0, 0, 0))
    if "use_active_object" in gen.settings.flags:
        MapObject = bpy.context.view_layer.objects.active
        return MapObject

    recalculateNormals(MapObject)

    MapObject.rotation_euler[2] += gen.settings.shapeRotation * (3.14159265 / 180)
    MapObject.select_set(True)
    bpy.context.view_layer.objects.active = MapObject
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)

    targetx = (gen.runtime.centerX or 0.0) + gen.settings.xTerrainOffset
    targety = (gen.runtime.centerY or 0.0) + gen.settings.yTerrainOffset
    if gen.settings.scalemode == "COORDINATES" and "chain_coords_center" in gen.settings.flags:
        midLat, midLon = midpoint_spherical(
            gen.settings.scaleLat1,
            gen.settings.scaleLon1,
            gen.settings.scaleLat2,
            gen.settings.scaleLon2,
        )
        targetx, targety, _el = convert_to_blender_coordinates(midLat, midLon, 0, 0)

    transform_MapObject(MapObject, targetx, targety)
    if MapObject and "map_polygon_wkt" in MapObject:
        outline = loads(MapObject["map_polygon_wkt"])
        gen.runtime.mapOutline = outline
        gen.runtime.mapObject = MapObject
        bpy.context.scene.tp3d.currentMap = MapObject

    zoom_camera_to_selected(MapObject)
    compute_and_store_tile_bounds(gen)
    return MapObject


def _rg_start_osm_prefetch(gen: GenerationContext):
    """Snapshot all bpy values on the main thread and launch a daemon thread
    that pre-fetches every active OSM coloring kind before mesh-building begins.

    The caller must call thread.join() before consuming the result dict.
    Returns (None, {}) immediately if no coloring elements are active.
    """
    from ...props import (  # deferred to avoid circular import
        any_road_active,
        get_road_active,
    )
    from ..osm.fetch_utils import (
        OsmFetchSettings,  # deferred to avoid circular import at load time
    )
    from ..osm.roads import TIER_TAGS  # deferred to avoid circular import at load time
    from ..terrain import (
        _fetch_all_kinds_parallel,  # deferred to avoid circular import at load time
    )

    _lat_span = gen.runtime.tbMaxLat - gen.runtime.tbMinLat
    _lon_span = gen.runtime.tbMaxLon - gen.runtime.tbMinLon
    if _lat_span <= 0 or _lon_span <= 0:
        return None, {}
    _lat_step = min(2.0, _lat_span)
    _lon_step = min(2.0, _lon_span)
    _tile_lats = math.ceil(_lat_span / _lat_step)
    _tile_lons = math.ceil(_lon_span / _lon_step)
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
    _semaphore = threading.Semaphore(
        1
    )  # max 1 concurrent live Overpass request (avoid 429s on the public instance)
    tp3d = bpy.context.scene.tp3d
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
    map_km = gen.runtime.mapKm if gen.runtime.mapKm is not None else tp3d.sMapInKm
    _active_kind_tasks = (
        [
            (key.upper(), _tile_tasks)
            for key, flag_attr, max_size, _, _ in COLORING_ELEMENTS
            if (flag_attr(tp3d) if callable(flag_attr) else getattr(tp3d, flag_attr) == 1)
            and map_km <= max_size
        ]
        if gen.settings.elementSource == "OSM"
        else []
    )
    if tp3d.el_bActive == 1 and map_km <= const.BUILDINGS_MAXSIZE:
        _active_kind_tasks.append(("BUILDINGS", _tile_tasks))
    if any_road_active(tp3d) and map_km <= const.ROADS_MAXSIZE:
        _active_kind_tasks.append(("STREETS", _tile_tasks))
    if tp3d.el_oActive == 1 and map_km <= const.COASTLINE_MAXSIZE:
        _active_kind_tasks.append(("COASTLINE", _tile_tasks))
    if not _active_kind_tasks:
        return None, {}

    result = {}

    def _run():
        fetched = _fetch_all_kinds_parallel(
            _active_kind_tasks, _semaphore, settings=_fetch_settings
        )
        result.update(fetched)

    t = threading.Thread(target=_run, daemon=True, name="osm-prefetch")
    t.start()
    gen.fetch.fetchThread = t
    gen.fetch.fetchResult = result


def _rg_start_satellite_prefetch(gen: GenerationContext):
    """Launch a daemon thread that pre-fetches the ESA WorldCover land-cover
    crop (and, in debug mode, a companion true-color photo) for the map's
    tile bounds, overlapping the request with the elevation/OSM fetches.

    No-op if elementSource isn't WORLDCOVER. The caller must join the thread
    before consuming gen.fetch.satelliteResult.
    """
    if gen.settings.elementSource != "WORLDCOVER":
        return

    from ...progress import ProgressOverlay
    from ..satellite import get_cached_landcover_image, get_cached_photo_image

    tp3d = bpy.context.scene.tp3d
    min_lat, max_lat = gen.runtime.tbMinLat, gen.runtime.tbMaxLat
    min_lon, max_lon = gen.runtime.tbMinLon, gen.runtime.tbMaxLon
    disable_cache = bool(tp3d.disableCache)
    debug = bool(bpy.app.debug)

    # set_fetch_progress() only mutates plain state + writes a JSON file (no
    # bpy.* calls) -- safe to call from the worker/pool threads below, unlike
    # set_fetch_items/set_fetch_done which force a viewport redraw.
    overlay = ProgressOverlay.get()
    overlay.set_fetch_progress("landcover", 0.0)

    result: dict = {}

    def _run():
        landcover = get_cached_landcover_image(
            min_lat, max_lat, min_lon, max_lon, disable_cache=disable_cache,
            progress_cb=lambda frac: overlay.set_fetch_progress("landcover", frac),
        )
        photo = (
            get_cached_photo_image(
                min_lat, max_lat, min_lon, max_lon, disable_cache=disable_cache
            )
            if debug
            else None
        )
        result["landcover"] = landcover
        result["photo"] = photo

    t = threading.Thread(target=_run, daemon=True, name="satellite-prefetch")
    t.start()
    gen.fetch.satelliteThread = t
    gen.fetch.satelliteResult = result


def _rg_create_satellite_plane(gen: GenerationContext):
    """Join the satellite prefetch thread and build the land-cover reference
    plane, painting the terrain's up-facing faces from it in PAINT mode.

    No-op if elementSource isn't WORLDCOVER or the fetch produced nothing.
    """
    if gen.settings.elementSource != "WORLDCOVER" or gen.fetch.satelliteThread is None:
        return

    from ...progress import ProgressOverlay
    from ..satellite import create_satellite_plane, paint_terrain_from_landcover

    gen.fetch.satelliteThread.join()
    result = gen.fetch.satelliteResult or {}
    landcover = result.get("landcover")
    ProgressOverlay.get().set_fetch_done("landcover", success=landcover is not None)
    if landcover is None:
        print("WorldCover: no land-cover data returned for this area, skipping.")
        return

    min_lat, max_lat = gen.runtime.tbMinLat, gen.runtime.tbMaxLat
    min_lon, max_lon = gen.runtime.tbMinLon, gen.runtime.tbMaxLon
    tp3d = bpy.context.scene.tp3d
    z_height = float(tp3d.highestZ) + 1.0

    create_satellite_plane(
        landcover, min_lat, max_lat, min_lon, max_lon, z_height,
        debug_photo_tiled=result.get("photo"),
    )

    if gen.settings.elementMode == "PAINT":
        paint_terrain_from_landcover(gen.runtime.mapObject, min_lat, max_lat, min_lon, max_lon)


def _rg_fetch_elevation(gen: GenerationContext):
    from ...progress import ProgressOverlay, WarningsOverlay
    from ..elevation import get_tile_elevation

    overlay = ProgressOverlay.get()
    warning = WarningsOverlay.get()

    def _elevation_progress(pct):
        t = pct / 100.0
        overlay.set_fetch_progress("elevation", t)
        overlay.update(
            0.38 + t * (0.65 - 0.38),
            "Fetching Elevation Data",
            "Querying elevation API…",
            sub_percent=t,
            sub_label="Tiles processed",
        )

    print(
        "------------------------------------------------",
        "FETCHING ELEVATION DATA FOR THE MAP",
        "------------------------------------------------",
    )

    get_tile_elevation(gen, progress_cb=_elevation_progress)

    if gen.runtime.elDiff is None:
        raise GenerationError(
            "Elevation fetch returned no data — check your API settings and connection"
        )
    if gen.settings.elevationMode == "FIXED":
        autoScale = gen.settings.fixedHeightMM / (gen.runtime.elDiff / 1000) if gen.runtime.elDiff > 0 else gen.settings.fixedHeightMM
    else:
        autoScale = gen.runtime.sScaleHor
    bpy.context.scene.tp3d.sAutoScale = autoScale
    gen.runtime.autoScale = autoScale

    if gen.runtime.tileVerts and len(gen.runtime.tileVerts) < 1000:
        warning.add_warning(
            f"Mesh has only {len(gen.runtime.tileVerts)} Points. Increase Resolution for higher Quality",
            "warn",
        )
    if gen.settings.elevationMode != "FIXED" and (
        gen.runtime.elDiff == 0 or (gen.runtime.elDiff / 1000) * autoScale * gen.settings.scaleElevation < 2
    ):
        warning.add_warning(
            "Terrain seems to be really flat. If not intended, increase Elevation scale",
            icon="warn",
        )


def _rg_prepare_trail_coords(gen: GenerationContext):
    """Convert GPX coordinates to Blender space and prepare all trail geometry arrays.

    Selects the correct coordinate set for the generation type, converts to Blender
    coordinates, simplifies, removes duplicates, and subdivides long segments to
    prevent trail clipping through terrain.  Stores results back into gen:
      gen.runtime.blenderCoords         — processed main path
      gen.runtime.blenderPathSegs       — processed per-segment paths (replaces Phase-6 raw version)
      gen.runtime.blenderPathSegsByFile — processed per-file paths   (replaces Phase-6 raw version)
    Also writes the real-world map scale to the scene property store.
    """
    def _subdivide_long_segments(coords, max_xy_dist, depsgraph=None):
        """Split trail segments longer than max_xy_dist Blender units to prevent clipping through hills.

        Inserts linearly-spaced intermediate points and raycasts downward against the
        terrain mesh to get the correct Z for each one.  Falls back to linear Z if the
        ray misses (e.g. point outside map bounds).
        """
        if len(coords) < 2:
            return coords
        result = [coords[0]]
        for i in range(1, len(coords)):
            x1, y1, z1 = result[-1]
            x2, y2, z2 = coords[i]
            dx, dy = x2 - x1, y2 - y1
            dist_xy = math.sqrt(dx * dx + dy * dy)
            if dist_xy > max_xy_dist:
                n = math.ceil(dist_xy / max_xy_dist)
                for j in range(1, n):
                    t = j / n
                    xi, yi = x1 + t * dx, y1 + t * dy
                    zi = z1 + t * (z2 - z1)
                    if depsgraph is not None:
                        hit, loc, _, _, _, _ = bpy.context.scene.ray_cast(
                            depsgraph, (xi, yi, zi + 500.0), (0.0, 0.0, -1.0)
                        )
                        if hit:
                            zi = loc.z
                    result.append((xi, yi, zi))
            result.append(coords[i])
        return result

    from ..geo import (
        convert_to_blender_coordinates_batch,
        haversine,
        separate_duplicate_xy,
    )
    from ..primitives import simplify_curve

    _MAX_TRAIL_SEG_BU = 0.25
    _depsgraph = bpy.context.evaluated_depsgraph_get()

    # Select coordinate set: trail_map uses the flat/synthetic path, not the GPX trail
    coordinates = (
        gen.runtime.flatCoordinates if "trail_map" in gen.settings.flags else gen.runtime.pathCoordinates
    ) or []

    # --- Main path: convert → simplify → deduplicate → subdivide ---
    blender_coords = convert_to_blender_coordinates_batch(coordinates)

    if bpy.app.debug:
        # Log average slope using the pre-processed per-segment coords when available
        _pre_segs = gen.runtime.blenderPathSegs or [blender_coords]
        _g_slopes = []
        for _seg in _pre_segs:
            for _i in range(len(_seg) - 1):
                x1, y1, z1 = _seg[_i]
                x2, y2, z2 = _seg[_i + 1]
                _h = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
                if _h > 0:
                    _g_slopes.append(abs(z2 - z1) / _h)
        if _g_slopes:
            _avg_g = sum(_g_slopes) / len(_g_slopes)
            print(
                f"[DEBUG] GPX avg slope:     {_avg_g:.4f}  ({math.degrees(math.atan(_avg_g)):.2f}°)"
            )

    blender_coords = simplify_curve(blender_coords, 0.12)
    print("Removing duplicates")
    blender_coords = separate_duplicate_xy(blender_coords, 0.05)
    gen.runtime.blenderCoords = _subdivide_long_segments(
        blender_coords, _MAX_TRAIL_SEG_BU, _depsgraph
    )

    # --- Per-segment paths ---
    if (
        "separate_paths" in gen.settings.flags or len(gen.runtime.pathSegs or []) > 1
    ) and "trail_map" not in gen.settings.flags:
        gen.runtime.blenderPathSegs = [
            _subdivide_long_segments(
                separate_duplicate_xy(
                    simplify_curve(convert_to_blender_coordinates_batch(path), 0.12),
                    0.05,
                ),
                _MAX_TRAIL_SEG_BU,
                _depsgraph,
            )
            for path in (gen.runtime.pathSegs or [])
        ]
    else:
        gen.runtime.blenderPathSegs = None

    # --- Per-file paths ---
    if gen.runtime.pathSegsByFile and "trail_map" not in gen.settings.flags:
        gen.runtime.blenderPathSegsByFile = [
            [
                _subdivide_long_segments(
                    separate_duplicate_xy(
                        simplify_curve(convert_to_blender_coordinates_batch(seg), 0.12),
                        0.05,
                    ),
                    _MAX_TRAIL_SEG_BU,
                    _depsgraph,
                )
                for seg in file_segs
            ]
            for file_segs in gen.runtime.pathSegsByFile
        ]
    else:
        gen.runtime.blenderPathSegsByFile = None

    # --- Store real-world map scale ---
    if len(coordinates) >= 2:
        lat1, lon1 = coordinates[0][0], coordinates[0][1]
        lat2, lon2 = coordinates[-1][0], coordinates[-1][1]
        tdist = haversine(lat1, lon1, lat2, lon2)
        mscale = (tdist / gen.settings.size) * 1_000_000
        bpy.context.scene.tp3d["o_mapScale"] = f"{mscale:.0f}"


def _rg_build_trail_curves(gen: GenerationContext):
    """Create Blender curve objects from the processed trail coordinate arrays.

    Uses gen.runtime.blenderCoords, gen.runtime.blenderPathSegs, and gen.runtime.blenderPathSegsByFile
    (populated by _rg_prepare_trail_coords) and gen.settings.flags to pick the right
    curve-creation strategy.

    Raises Exception on Runtime error
    """
    from ..mesh_ops import splitCurves
    from ..primitives import create_curve_from_coordinates

    blender_coords = gen.runtime.blenderCoords or []
    blender_coords_separate = gen.runtime.blenderPathSegs or []
    blender_coords_by_file = gen.runtime.blenderPathSegsByFile or []
    flags = gen.settings.flags

    # Pure "jmap"/"jmap_bbox" generation (center+radius or two-corner-points) has
    # no GPX/trail data at all -- none of the branches below can ever match, so
    # bail out early instead of falling through to "No trail curves created".
    if not ({"gpx_file", "gpx_chain", "trail_map"} & flags):
        gen.runtime.curveObjs = []
        return

    curveObj = None
    curveObjs = None
    print("Building trail curve(s)")
    try:
        if (
            "gpx_file" in flags
            and "trail_map" not in flags
            and len(blender_coords_separate) <= 1
        ) or "trail_map" in flags:
            # Single segment or trail_map: one curve directly
            curveObj = create_curve_from_coordinates(gen, blender_coords)
        elif (
            "gpx_chain" in flags
            and blender_coords_by_file
            and "trail_map" not in flags
            and "trail" in flags
        ):
            # Multi-file: one object per file, joining its segments as separate splines
            curveObjs = []
            for file_segs in blender_coords_by_file:
                bpy.ops.object.select_all(action="DESELECT")
                for crds in file_segs:
                    create_curve_from_coordinates(gen, crds)
                if len(file_segs) > 1:
                    bpy.ops.object.join()
                curveObjs.append(bpy.context.view_layer.objects.active)
                gen.runtime.curveObjs = curveObjs
        elif (
            ("separate_paths" in flags or len(blender_coords_separate) > 1)
            and "trail_map" not in flags
            and "trail" in flags
        ):
            # Single file with multiple segments: join all into one object
            bpy.ops.object.select_all(action="DESELECT")
            for crds in blender_coords_separate:
                create_curve_from_coordinates(gen, crds)
            bpy.ops.object.join()
            curveObjs = [bpy.context.view_layer.objects.active]
    except RuntimeError:
        raise GenerationError(
            "Bad Response from API while creating the curve. If this happens everytime contact dev"
        )

    if curveObj is None and curveObjs is None:
        raise GenerationError("No trail curves created")

    if curveObj is not None and curveObjs is None:
        curveObjs = splitCurves(curveObj)

    if curveObjs is None:
        raise GenerationError("Failed to split curveObj")

    gen.runtime.curveObjs = curveObjs
    print(f"Curve objects created: {len(curveObjs) or 'unknown'}")

    bpy.ops.object.select_all(action="DESELECT")


def _rg_displace_terrain_with_curve(gen: GenerationContext):
    """Displace terrain mesh vertices using Mercator‑corrected elevation data,
    then snap trail curves onto the displaced surface.

    Raises GenerationError if input data is missing or invalid.
    """
    import math

    from ...utils.mesh_ops import RaycastCurveToMesh

    # --- Validate input ---
    if gen.runtime.mapObject is None:
        raise GenerationError("No map object assigned; cannot displace terrain.")
    if gen.runtime.mapObject.type != "MESH":
        raise GenerationError(f"Map object '{gen.runtime.mapObject.name}' is not a mesh.")
    if (
        not hasattr(gen.runtime, "tileVerts")
        or gen.runtime.tileVerts is None
        or len(gen.runtime.tileVerts) == 0
    ):
        raise GenerationError(
            "Missing or empty 'tileVerts' – elevation data not available."
        )

    mesh = gen.runtime.mapObject.data
    _total_verts = len(mesh.vertices)
    if _total_verts == 0:
        raise GenerationError("Map object has no vertices.")

    print(f"Displacing terrain: {mesh.name} ({_total_verts} vertices)")

    # --- Bulk read vertex coordinates ---
    co_flat = np.empty(_total_verts * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", co_flat)
    co = co_flat.reshape((_total_verts, 3))

    # --- World‑space Y for Mercator correction ---
    try:
        m = np.array(gen.runtime.mapObject.matrix_world, dtype=np.float64)
        co_h = np.hstack([co, np.ones((_total_verts, 1), dtype=np.float64)])
        world_y = (m @ co_h.T).T[:, 1]
    except Exception as e:  # noqa: BLE001
        raise GenerationError(f"Failed to transform vertex coordinates: {e}")

    # --- Mercator latitude correction ---
    try:
        lat_rad = 2.0 * np.arctan(np.exp(world_y / (const.R * gen.runtime.sScaleHor))) - (
            np.pi / 2.0
        )
        merc = 1.0 / np.cos(lat_rad)
    except Exception as e:  # noqa: BLE001
        raise GenerationError(f"Mercator correction failed: {e}")

    # --- Compute new Z for all vertices ---
    try:
        tile_verts = np.array(gen.runtime.tileVerts, dtype=np.float64)
        if tile_verts.shape != (_total_verts,):
            # if tileVerts is a list of lists? adapt as needed – here assume flat array
            raise ValueError(
                f"tileVerts length {len(tile_verts)} doesn't match vertices {_total_verts}"
            )
        new_z = (tile_verts / 1000.0) * gen.settings.scaleElevation * gen.runtime.autoScale * merc
        if gen.settings.smoothTerrainTop:
            from ..terrain import smooth_terrain_top_z

            new_z = smooth_terrain_top_z(
                co[:, 0], co[:, 1], new_z, iterations=gen.settings.smoothTerrainStrength
            )
        co[:, 2] = new_z
        mesh.vertices.foreach_set("co", co.ravel())
        mesh.update()
    except Exception as e:  # noqa: BLE001
        raise GenerationError(f"Failed to apply elevation displacement: {e}")

    # --- Store min/max and extrusion offset ---
    lowestZ = float(new_z.min())
    highestZ = float(new_z.max())
    additionalExtrusion = lowestZ
    gen.runtime.addExtrusion = additionalExtrusion

    # Update scene properties if they exist (with fallback)
    try:
        bpy.context.scene.tp3d.sAdditionalExtrusion = additionalExtrusion
        bpy.context.scene.tp3d.lowestZ = lowestZ
        bpy.context.scene.tp3d.highestZ = highestZ
    except AttributeError:
        # Property group may not be registered yet; warn but continue
        print(
            "Warning: tp3d property group not found; elevation stats not saved to scene."
        )

    print(f"additionalExtrusion: {additionalExtrusion}")
    print(f"Lowest Z: {lowestZ}")
    print(f"Highest Z: {highestZ}")

    # --- Optional debug: slope statistics ---
    if bpy.app.debug:
        try:
            _t_slopes = []
            for edge in mesh.edges:
                v1 = mesh.vertices[edge.vertices[0]].co  # type: ignore[index]
                v2 = mesh.vertices[edge.vertices[1]].co  # type: ignore[index]
                _h = math.sqrt((v2.x - v1.x) ** 2 + (v2.y - v1.y) ** 2)
                if _h > 0:
                    _t_slopes.append(abs(v2.z - v1.z) / _h)
            if _t_slopes:
                _avg_t = sum(_t_slopes) / len(_t_slopes)
                print(
                    f"[DEBUG] Terrain avg slope: {_avg_t:.4f}  ({math.degrees(math.atan(_avg_t)):.2f}°)"
                )
        except Exception as e:  # noqa: BLE001
            raise GenerationError(f"[DEBUG] Slope computation failed: {e}")

    # --- Snap trail curves to the displaced surface ---
    if gen.settings.overwritePathElevation:
        curves_to_snap = []
        if gen.runtime.curveObj is not None:
            curves_to_snap.append(gen.runtime.curveObj)
        if gen.runtime.curveObjs is not None:
            curves_to_snap.extend(gen.runtime.curveObjs)

        if not curves_to_snap:
            print(
                "No trail curves to snap (overwritePathElevation is True but no curves found)."
            )
        else:
            for curve in curves_to_snap:
                if curve is not None and curve.type == "CURVE":
                    try:
                        RaycastCurveToMesh(curve, gen.runtime.mapObject)
                    except Exception as e:  # noqa: BLE001
                        raise GenerationError(
                            f"Failed to snap curve '{curve.name}' to terrain: {e}"
                        )
                else:
                    print(f"Skipping invalid curve object: {curve}")


def _rg_extrude_terrain(gen: GenerationContext):
    # Extrude ONLY outer boundary down to form walls + single bottom cap
    import bmesh
    import shapely.geometry as sg
    import shapely.ops as so
    from shapely.ops import polygonize

    from .. import geometry2d as g2d

    obj: bpy.types.Mesh = gen.runtime.mapObject
    if obj.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    bm = bmesh.new()
    bm.from_mesh(obj.data)

    if gen.runtime.addExtrusion is None:
        return
    target_bottom_z = gen.runtime.addExtrusion - gen.settings.minThickness
    shift_z = -gen.runtime.addExtrusion + gen.settings.minThickness

    # Extrude boundary edges downward
    top_boundary_edges = [e for e in bm.edges if e.is_boundary]
    extrude_res = bmesh.ops.extrude_edge_only(bm, edges=top_boundary_edges)

    extruded_verts = [
        elem for elem in extrude_res["geom"] if isinstance(elem, bmesh.types.BMVert)
    ]
    extruded_edges = [
        elem for elem in extrude_res["geom"] if isinstance(elem, bmesh.types.BMEdge)
    ]

    # 1. Flatten all bottom vertices
    for v in extruded_verts:
        v.co.z = target_bottom_z

    # 2. Build direct 2D coordinate lookup for extruded bottom vertices
    bottom_boundary_edges = [e for e in extruded_edges if e.is_boundary]
    loops = g2d.group_boundary_loops(bottom_boundary_edges)

    rings = []
    if loops:
        # 3. Convert mesh boundary loops into 2D Shapely LinearRings (contains ALL lattice verts)
        for loop in loops:
            if len(loop) >= 3:
                pts = [(v.co.x, v.co.y) for v in loop]
                if pts[0] != pts[-1]:
                    pts.append(pts[0])
                rings.append(sg.LinearRing(pts))

    # 4. Automatically reconstruct shell & hole hierarchy from the mesh boundary rings
    mesh_polygons = list(polygonize(rings))

    # Filter out empty space voids using original vector outline
    map_polygon = gen.runtime.mapOutline
    if map_polygon:
        mesh_polygons = [
            p for p in mesh_polygons if map_polygon.covers(p.representative_point())
        ]

    # Lookup mapping 2D coords back to the exact bottom BMVerts
    vert_lookup = {
        (round(v.co.x, 6), round(v.co.y, 6)): v for loop in loops for v in loop
    }

    for poly in mesh_polygons:
        if poly.is_empty:
            continue

        if not poly.is_valid:
            poly = poly.buffer(0)

        exterior_ring = list(poly.exterior.coords)[:-1]
        interior_rings = [list(h.coords)[:-1] for h in poly.interiors]

        # Triangulate using the mesh-derived boundary vertices
        cdt_res = g2d._cdt_triangulate(poly, exterior_ring, interior_rings)

        if cdt_res is None:
            # Fallback
            delaunay_tris = [t for t in so.triangulate(poly) if poly.covers(t.centroid)]
            for tri in delaunay_tris:
                tri_coords = list(tri.exterior.coords)[:3]
                bm_verts = [
                    vert_lookup.get((round(pt[0], 6), round(pt[1], 6)))
                    for pt in tri_coords
                ]

                if None not in bm_verts and len(set(bm_verts)) == 3:
                    v1, v2, v3 = bm_verts
                    assert v1 is not None and v2 is not None and v3 is not None
                    normal = (v2.co - v1.co).cross(v3.co - v1.co)
                    if normal.z > 0:
                        bm.faces.new((v1, v3, v2))
                    else:
                        bm.faces.new((v1, v2, v3))
        else:
            verts2d, tris, _ = cdt_res
            bm_vert_list = []

            for x, y in verts2d:
                key = (round(x, 6), round(y, 6))
                if key in vert_lookup:
                    bm_vert_list.append(vert_lookup[key])
                else:
                    new_v = bm.verts.new((x, y, target_bottom_z))
                    bm_vert_list.append(new_v)
                    vert_lookup[key] = new_v

            for ia, ib, ic in tris:
                v1, v2, v3 = bm_vert_list[ia], bm_vert_list[ib], bm_vert_list[ic]
                if len({v1, v2, v3}) == 3:
                    normal = (v2.co - v1.co).cross(v3.co - v1.co)
                    if normal.z > 0:
                        bm.faces.new((v1, v3, v2))
                    else:
                        bm.faces.new((v1, v2, v3))

    for v in bm.verts:
        v.co.z += shift_z

    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()

    # Apply BASE material to the map mesh
    mat = bpy.data.materials.get("BASE")
    obj.data.materials.clear()
    obj.data.materials.append(mat)

    # Shift curve objects in Python space
    if gen.runtime.curveObjs:
        for tcrv in gen.runtime.curveObjs:
            tcrv.location.z += shift_z

    # Set object origin to cursor position
    location = obj.location
    bpy.context.scene.cursor.location = location
    if gen.runtime.curveObjs:
        for tcrv in gen.runtime.curveObjs:
            tcrv.select_set(True)
            bpy.ops.object.origin_set(type="ORIGIN_CURSOR")


