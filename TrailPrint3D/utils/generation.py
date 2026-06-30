import bpy  # type: ignore
import math
import time
import os
import platform
import random
import threading
import numpy as np
from mathutils import Vector  # type: ignore
from bpy.app.translations import pgettext as _
from .. import progress as _progress
from .. import addon_preferences
from .. import constants as const
from .elevation import compute_and_store_tile_bounds


# ---------------------------------------------------------------------------
# runGeneration sub-phase helpers
# ---------------------------------------------------------------------------

def _rg_validate_inputs(flags):
    """Load all scene properties, validate inputs, and open the console.

    Returns a props dict on success, or None if validation fails (console
    is toggled closed before returning None).
    """
    from .scene import show_message_box  # deferred to avoid circular import at load time

    start_time = time.time()
    for i in range(30):
        print(" ")
    print("------------------------------------------------")
    print("SCRIPT STARTED - DO NOT CLOSE THIS WINDOW")
    print("------------------------------------------------")
    print(" ")

    tp3d = bpy.context.scene.tp3d
    gpx_file_path      = tp3d.get('file_path', None)
    gpx_chain_path     = tp3d.get('chain_path', None)
    exportPath         = tp3d.get('export_path', None)
    shape              = tp3d.shape
    name               = tp3d.get('trailName', "")
    size               = tp3d.get('objSize', 100)
    scaleElevation     = tp3d.get('scaleElevation', 1)
    scalemode          = tp3d.scalemode
    scaleLon1          = tp3d.get('scaleLon1', 0)
    scaleLat1          = tp3d.get('scaleLat1', 0)
    scaleLon2          = tp3d.get('scaleLon2', 0)
    scaleLat2          = tp3d.get('scaleLat2', 0)
    shapeRotation      = tp3d.get('shapeRotation', 0)
    overwritePathElev  = tp3d.get('overwritePathElevation', True)
    api                = tp3d.api
    selfHosted         = tp3d.get("selfHosted", "")
    fixedElevScale     = tp3d.get('fixedElevationScale', False)
    minThickness       = tp3d.get("minThickness", 2)
    xTerrainOffset     = tp3d.get("xTerrainOffset", 0)
    yTerrainOffset     = tp3d.get("yTerrainOffset", 0)
    singleColorMode    = tp3d.get("singleColorMode", 0)
    elementMode        = tp3d.elementMode
    disableCache       = tp3d.get("disableCache", 0)
    num_subdivisions   = tp3d.num_subdivisions
    textFont           = tp3d.get("textFont", "")
    plateThickness     = tp3d.get("plateThickness", 5)
    col_wActive        = any([tp3d.col_wPondsActive, tp3d.col_wSmallRiversActive, tp3d.col_wBigRiversActive])
    col_fActive        = tp3d.col_fActive
    col_cActive        = tp3d.col_cActive
    col_grActive       = tp3d.col_grActive
    col_glActive       = tp3d.col_glActive
    el_bActive         = tp3d.el_bActive
    el_sActive         = any([tp3d.el_sBigActive, tp3d.el_sMedActive, tp3d.el_sSmallActive])
    jMapLat            = tp3d.get("jMapLat", 49)
    jMapLon            = tp3d.get("jMapLon", 9)
    jMapRadius         = tp3d.get("jMapRadius", 50)
    jMapLat1           = tp3d.get("jMapLat1", 48)
    jMapLon1           = tp3d.get("jMapLon1", 8)
    jMapLat2           = tp3d.get("jMapLat2", 49)
    jMapLon2           = tp3d.get("jMapLon2", 9)

    opentopoAdress = "https://api.opentopodata.org/v1/"
    if selfHosted != "" and selfHosted is not None and api == "OPENTOPODATA":
        opentopoAdress = selfHosted
        print(f"!!using {opentopoAdress} instead of Opentopodata!!")
    tp3d.opentopoAdress = opentopoAdress

    # --- Input validation ---
    from ..addon_preferences import get_prefs
    _ot_api_key = get_prefs().openTopographyApiKey
    if api == "OPENTOPOGRAPHY" and not _ot_api_key:
        print("No OPENTOPOGRAPHY API key entered")
        show_message_box(
            "OpenTopography requires an API key. "
            "Get a free key at portal.opentopography.org and set it in the addon preferences."
        )
        return None

    #if singleColorMode and elementMode == "SEPARATE":
    #    show_message_box("Single Color Mode and Separate Element Mode cannot be used together. either disable Single-color Mode for the trail or switch to SingleColorMode for elements.")
    #    return None

    if "gpx_file" in flags:
        if not gpx_file_path or gpx_file_path == "":
            show_message_box("File path is empty! Please select a valid file.")
            return None
        if not os.path.isfile(gpx_file_path):
            show_message_box(f"Invalid file path: {gpx_file_path}. Please select a valid file.")
            return None
        gpx_file_path = bpy.path.abspath(gpx_file_path)
        file_extension = os.path.splitext(gpx_file_path)[1].lower()
        if file_extension != '.gpx' and file_extension != ".igc":
            show_message_box(f"Invalid file format. Please Use a .GPX file")
            return None
    if "gpx_chain" in flags:
        if not gpx_chain_path or gpx_chain_path == "":
            show_message_box("CHAIN path is empty! Please select a valid folder.")
            return None
        gpx_chain_path = bpy.path.abspath(gpx_chain_path)
    if not exportPath:
        exportPath = addon_preferences.get_prefs().default_export_folder
    if not exportPath:
        show_message_box("Export path cant be empty")
        return None
    exportPath = bpy.path.abspath(exportPath)
    if not exportPath or exportPath == "":
        show_message_box("Export path is empty! Please select a valid folder.")
        return None
    if not os.path.isdir(exportPath):
        show_message_box(f"Invalid export Directory: {exportPath}. Please select a valid Directory.")
        return None
    try:
        test_path = os.path.join(exportPath, ".tp3d_write_test")
        with open(test_path, "w") as f:
            f.write("")
        os.remove(test_path)
    except OSError:
        show_message_box(f"No write permission for export folder: {exportPath}. Please select a different folder.")
        return None

    # --- Default font ---
    if textFont == "":
        if platform.system() == "Windows":
            textFont = "C:/WINDOWS/FONTS/ariblk.ttf"
        elif platform.system() == "Darwin":
            textFont = "/System/Library/Fonts/Supplemental/Arial Black.ttf"
        else:
            textFont = ""

    # --- Default model name from file/folder ---
    if name == "":
        if "gpx_file" in flags:
            name_with_ext = os.path.basename(gpx_file_path)
            name = os.path.splitext(name_with_ext)[0]
        if "gpx_chain" in flags and "append_collection" not in flags:
            name_with_ext = os.path.basename(os.path.normpath(gpx_chain_path))
            name = os.path.splitext(name_with_ext)[0]
        if "gpx_file" not in flags and "gpx_chain" not in flags:
            name = "Terrain"

    modelname = name
    tp3d.modelname = modelname

    return {
        'start_time':            start_time,
        'gpx_file_path':         gpx_file_path,
        'gpx_chain_path':        gpx_chain_path,
        'exportPath':            exportPath,
        'shape':                 shape,
        'name':                  name,
        'modelname':             modelname,
        'size':                  size,
        'scaleElevation':        scaleElevation,
        'scalemode':             scalemode,
        'scaleLon1':             scaleLon1,
        'scaleLat1':             scaleLat1,
        'scaleLon2':             scaleLon2,
        'scaleLat2':             scaleLat2,
        'shapeRotation':         shapeRotation,
        'overwritePathElevation': overwritePathElev,
        'api':                   api,
        'selfHosted':            selfHosted,
        'fixedElevationScale':   fixedElevScale,
        'minThickness':          minThickness,
        'xTerrainOffset':        xTerrainOffset,
        'yTerrainOffset':        yTerrainOffset,
        'singleColorMode':       singleColorMode,
        'elementMode':           elementMode,
        'disableCache':          disableCache,
        'num_subdivisions':      num_subdivisions,
        'textFont':              textFont,
        'plateThickness':        plateThickness,
        'col_wActive':           col_wActive,
        'col_fActive':           col_fActive,
        'col_cActive':           col_cActive,
        'col_grActive':          col_grActive,
        'col_glActive':          col_glActive,
        'el_bActive':            el_bActive,
        'el_sActive':            el_sActive,
        'jMapLat':               jMapLat,
        'jMapLon':               jMapLon,
        'jMapRadius':            jMapRadius,
        'jMapLat1':              jMapLat1,
        'jMapLon1':              jMapLon1,
        'jMapLat2':              jMapLat2,
        'jMapLon2':              jMapLon2,
    }


def _rg_load_coordinates(flags, props):
    """Load GPX / synthetic coordinate data based on generation type.

    Returns (coordinates, separate_paths, coordinates2) or None on error.
    """
    from .primitives import setupColors  # deferred to avoid circular import at load time
    from .io_gpx import read_gpx_file, read_gpx_directory  # deferred to avoid circular import at load time
    from .geo import move_coordinates  # deferred to avoid circular import at load time

    setupColors()

    if props['disableCache'] == 1:
        print("INFO: Cache Disabled (in Advanced Settings)")
    if not props['overwritePathElevation'] and not props['singleColorMode']:
        print("INFO: Overwrite Path Elevation disabled: Path Elevation wont be Adjusted to Map elevation")
    if "gpx_file" in flags or ("gpx_chain" in flags and "append_collection" not in flags):
        if props['xTerrainOffset'] > 0:
            print(f"INFO: Map will be moved in X by {props['xTerrainOffset']} (Advanced Settings -> Map -> xTerrainOffset)")
        if props['yTerrainOffset'] > 0:
            print(f"INFO: Map will be moved in Y by {props['yTerrainOffset']} (Advanced Settings -> Map -> yTerrainOffset)")

    if bpy.context.object and bpy.context.object.mode == 'EDIT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.context.scene.tool_settings.use_mesh_automerge = False

    coordinates2 = []
    separate_paths = []
    separate_paths_by_file = []  # segments grouped by source file (gpx_chain only)
    try:
        if "gpx_file" in flags and "trail_map" not in flags:
            separate_paths = read_gpx_file()
        if "gpx_chain" in flags not in flags:
            separate_paths_by_file = read_gpx_directory(props['gpx_chain_path'])
            separate_paths = [seg for file_segs in separate_paths_by_file for seg in file_segs]
        if "jmap" in flags:
            nlat, nlon = move_coordinates(props['jMapLat'], props['jMapLon'], props['jMapRadius'], "e")
            separate_paths.append([(nlat, nlon, 0, 0)])
            nlat, nlon = move_coordinates(props['jMapLat'], props['jMapLon'], props['jMapRadius'], "s")
            separate_paths.append([(nlat, nlon, 0, 0)])
            nlat, nlon = move_coordinates(props['jMapLat'], props['jMapLon'], props['jMapRadius'], "w")
            separate_paths.append([(nlat, nlon, 0, 0)])
            nlat, nlon = move_coordinates(props['jMapLat'], props['jMapLon'], props['jMapRadius'], "n")
            separate_paths.append([(nlat, nlon, 0, 0)])
            if "trail_map" in flags:
                tempcoordinates = read_gpx_file()
                coordinates2 = [item for sublist in tempcoordinates for item in sublist]
        if "jmap_bbox" in flags:
            separate_paths.append([(props['jMapLat1'], props['jMapLon1'], 0, 0)])
            separate_paths.append([(props['jMapLat2'], props['jMapLon2'], 0, 0)])
    except Exception as e:
        #show_message_box(f"Something went Wrong reading the GPX. Type {type}")
        _progress.WarningsOverlay.add_warning("Something went Wrong reading the GPX file", "error")
        return None

    coordinates = [item for sublist in separate_paths for item in sublist]

    return (coordinates, separate_paths, coordinates2, separate_paths_by_file)


def _rg_compute_trail_stats(flags, coordinates):
    """Calculate trail statistics and store them in scene properties."""
    from .geo import calculate_total_length, calculate_total_elevation, calculate_total_time, calculate_date  # deferred to avoid circular import at load time

    total_length = 0
    total_elevation = 0
    total_time = 0
    average_speed = 0
    trail_date = ""
    if "stats" in flags:
        total_length    = calculate_total_length(coordinates)
        total_elevation = calculate_total_elevation(coordinates)
        total_time      = calculate_total_time(coordinates)
        trail_date      = calculate_date(coordinates)
        if total_time is not None and total_time > 0:
            average_speed = total_length / total_time

    hours = int(total_time)
    minutes = int((total_time - hours) * 60)
    time_str = f"{hours}h {minutes}m"

    tp3d = bpy.context.scene.tp3d
    tp3d.sTime_str      = time_str
    tp3d.total_length   = total_length
    tp3d.total_elevation = total_elevation
    tp3d.total_time     = total_time
    tp3d.average_speed  = average_speed
    tp3d.trail_date     = trail_date


def _rg_create_map_object(flags, props, modelname, centerx, centery):
    """Create, rotate, and position the base map shape object."""
    from .primitives import create_rectangle, create_hexagon, create_heart, create_octagon, create_circle, create_ellipse  # deferred to avoid circular import at load time
    from .presets import appendCollection  # deferred to avoid circular import at load time
    from .mesh_ops import recalculateNormals  # deferred to avoid circular import at load time
    from .geo import midpoint_spherical, convert_to_blender_coordinates  # deferred to avoid circular import at load time
    from .scene import transform_MapObject  # deferred to avoid circular import at load time

    shape          = props['shape']
    size           = props['size']
    num_subdivisions = props['num_subdivisions']
    shapeRotation  = props['shapeRotation']
    scalemode      = props['scalemode']
    xTerrainOffset = props['xTerrainOffset']
    yTerrainOffset = props['yTerrainOffset']

    if "append_collection" not in flags and "use_active_object" not in flags:
        print(f"[map_object] creating '{shape}' N={num_subdivisions} size={size:.1f}…")
        _t_shape = time.time()
        if shape == "SQUARE":
            rHeight = bpy.context.scene.tp3d.rectangleHeight
            MapObject = create_rectangle(size, rHeight, num_subdivisions, modelname)
        elif shape in {"HEXAGON", "HEXAGON INNER TEXT", "HEXAGON OUTER TEXT", "HEXAGON FRONT TEXT"}:
            MapObject = create_hexagon(size / 2, num_subdivisions, modelname)
        elif shape == "HEART":
            MapObject = create_heart(size / 2, num_subdivisions, modelname)
        elif shape in {"OCTAGON", "OCTAGON OUTER TEXT"}:
            MapObject = create_octagon(size / 2, num_subdivisions, modelname)
        elif shape in {"CIRCLE", "MEDAL"}:
            MapObject = create_circle(size / 2, num_subdivisions, modelname)
        elif shape == "ELLIPSE":
            ratio = bpy.context.scene.tp3d.ellipseRatio
            MapObject = create_ellipse(size / 2, num_subdivisions, modelname, ratio)
        else:
            MapObject = create_hexagon(size / 2, num_subdivisions, modelname)
        print(f"[map_object] shape created in {time.time()-_t_shape:.3f}s")
    if "append_collection" in flags:
        appendCollection()
        MapObject = bpy.context.view_layer.objects.active
        MapObject.location = Vector((0,0,0))
    if "use_active_object" in flags:
        MapObject = bpy.context.view_layer.objects.active
        return MapObject

    recalculateNormals(MapObject)

    MapObject.rotation_euler[2] += shapeRotation * (3.14159265 / 180)
    MapObject.select_set(True)
    bpy.context.view_layer.objects.active = MapObject
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)

    targetx = centerx + xTerrainOffset
    targety = centery + yTerrainOffset
    if scalemode == "COORDINATES" and "chain_coords_center" in flags:
        midLat, midLon = midpoint_spherical(props['scaleLat1'], props['scaleLon1'], props['scaleLat2'], props['scaleLon2'])
        targetx, targety, el = convert_to_blender_coordinates(midLat, midLon, 0, 0)

    transform_MapObject(MapObject, targetx, targety)
    return MapObject


# ---------------------------------------------------------------------------
# Coloring-element definitions — used by both the OSM prefetch helper and the
# main terrain-element builder.  Tuple layout:
#   (result_key, active_flag_attr, max_size_const, phase_label, fetch_message)
# ---------------------------------------------------------------------------
COLORING_ELEMENTS = [
    ('forest',  'col_fActive',   const.FOREST_MAXSIZE,  "Forest",  "Fetching forest data\u2026"),
    ('water',   lambda t: t.col_wPondsActive or t.col_wSmallRiversActive or t.col_wBigRiversActive, const.WATER_MAXSIZE, "Water", "Fetching water data\u2026"),
    ('scree',   'col_scrActive', const.SCREE_MAXSIZE,   "Scree",   "Fetching scree data\u2026"),
    ('city',       'col_cActive',   const.CITY_MAXSIZE,       "City",       "Fetching city data\u2026"),
    ('greenspace', 'col_grActive', const.GREENSPACE_MAXSIZE, "Greenspace", "Fetching greenspace data\u2026"),
    ('farmland',   'col_faActive', const.FARMLAND_MAXSIZE,   "Farmland",   "Fetching farmland data\u2026"),
    ('glacier',  'col_glActive',  const.GLACIER_MAXSIZE,  "Glacier",  "Fetching glacier data\u2026"),
]


def _rg_start_osm_prefetch(tp3d, map_km):
    """Snapshot all bpy values on the main thread and launch a daemon thread
    that pre-fetches every active OSM coloring kind before mesh-building begins.

    The caller must call thread.join() before consuming the result dict.
    Returns (None, {}) immediately if no coloring elements are active.
    """
    from .terrain import _fetch_all_kinds_parallel  # deferred to avoid circular import at load time
    from .osm import OsmFetchSettings               # deferred to avoid circular import at load time

    _lat_span  = tp3d.maxLat - tp3d.minLat
    _lon_span  = tp3d.maxLon - tp3d.minLon
    if _lat_span <= 0 or _lon_span <= 0:
        return None, {}
    _lat_step  = min(2.0, _lat_span)
    _lon_step  = min(2.0, _lon_span)
    _tile_lats = math.ceil(_lat_span / _lat_step)
    _tile_lons = math.ceil(_lon_span / _lon_step)
    _tile_tasks = [
        (tp3d.minLat + k * _lat_step,
         tp3d.minLon + l * _lon_step,
         tp3d.minLat + k * _lat_step + _lat_step,
         tp3d.minLon + l * _lon_step + _lon_step)
        for k in range(_tile_lats)
        for l in range(_tile_lons)
    ]
    _semaphore = threading.Semaphore(1)  # max 1 concurrent live Overpass request (avoid 429s on the public instance)
    _fetch_settings = OsmFetchSettings(
        disable_cache       = tp3d.disableCache,
        api_retries         = tp3d.apiRetries,
        mapsize             = tp3d.sMapInKm,
        road_big            = bool(tp3d.el_sBigActive),
        road_med            = bool(tp3d.el_sMedActive),
        road_small          = bool(tp3d.el_sSmallActive),
        water_ponds         = bool(tp3d.col_wPondsActive),
        water_small_rivers  = bool(tp3d.col_wSmallRiversActive),
        water_big_rivers    = bool(tp3d.col_wBigRiversActive),
    )
    _active_kind_tasks = [
        (key.upper(), _tile_tasks)
        for key, flag_attr, max_size, _, _ in COLORING_ELEMENTS
        if (flag_attr(tp3d) if callable(flag_attr) else getattr(tp3d, flag_attr) == 1)
        and map_km <= max_size
    ]
    if tp3d.el_bActive == 1 and map_km <= const.BUILDINGS_MAXSIZE:
        _active_kind_tasks.append(("BUILDINGS", _tile_tasks))
    if any([tp3d.el_sBigActive, tp3d.el_sMedActive, tp3d.el_sSmallActive]) and map_km <= const.ROADS_MAXSIZE:
        _active_kind_tasks.append(("STREETS", _tile_tasks))
    if tp3d.el_oActive == 1:
        _active_kind_tasks.append(("COASTLINE", _tile_tasks))
    if not _active_kind_tasks:
        return None, {}

    result = {}

    def _run():
        fetched = _fetch_all_kinds_parallel(_active_kind_tasks, _semaphore,
                                            settings=_fetch_settings)
        result.update(fetched)

    t = threading.Thread(target=_run, daemon=True, name="osm-prefetch")
    t.start()
    return t, result


def _rg_build_terrain_elements(obj, scaleHor, curveObj=None, phase_start=0.83, phase_end=0.95,
                               prefetched_osm=None, tile_label=None):
    """Create water, forest, city, glacier, building and road overlay meshes.

    Reads all flags directly from bpy.context.scene.tp3d.
    Returns a dict keyed by element name; values may be None if disabled.
    phase_start/phase_end control the overlay progress range for multi-tile callers.
    prefetched_osm: result dict from _rg_start_osm_prefetch; if provided the
    per-kind OSM fetch is skipped (data was already downloaded in the background).
    tile_label: optional prefix for progress messages (e.g. "Tile 2/6") used by
    multi-tile callers so element messages keep their tile context visible.
    """
    from .terrain import coloring_main, createOcean, _COLORING_EMPTY, _COLORING_PAINTED, _COLORING_FILTERED, _fetch_all_kinds_parallel  # deferred to avoid circular import at load time
    from .osm import OsmFetchSettings  # deferred to avoid circular import at load time
    from .osm import create_buildings, create_roads  # deferred to avoid circular import at load time
    from .scene import set_origin_to_3d_cursor  # deferred to avoid circular import at load time
    from .mesh_ops import intersectWithTile  # deferred to avoid circular import at load time
    from .metadata import writeMetadata  # deferred to avoid circular import at load time

    tp3d   = bpy.context.scene.tp3d
    map_km = tp3d["sMapInKm"]
    _ov    = _progress.ProgressOverlay.get()

    # --------------------------------------------------
    # Standard coloring elements (all share the same pattern).
    # To add a new layer: append one tuple here and nothing else.
    #   (result_key, active_flag_attr, max_size_const, phase_label, fetch_message)
    # --------------------------------------------------
    COLORING_ELEMENTS = [
        ('forest',  'col_fActive',   const.FOREST_MAXSIZE,  "Forest",  "Fetching forest data…"),
        ('water',   lambda t: t.col_wPondsActive or t.col_wSmallRiversActive or t.col_wBigRiversActive, const.WATER_MAXSIZE, "Water", "Fetching water data…"),
        ('scree',   'col_scrActive', const.SCREE_MAXSIZE,   "Scree",   "Fetching scree data…"),
        ('city',       'col_cActive',   const.CITY_MAXSIZE,       "City",       "Fetching city data…"),
        ('greenspace', 'col_grActive', const.GREENSPACE_MAXSIZE, "Greenspace", "Fetching greenspace data…"),
        ('farmland',   'col_faActive', const.FARMLAND_MAXSIZE,   "Farmland",   "Fetching farmland data…"),
        ('glacier',  'col_glActive',  const.GLACIER_MAXSIZE,  "Glacier",  "Fetching glacier data…"),
    ]

    # Count total active elements (coloring + optional ocean/buildings/roads) for progress spread
    _ELEM_PHASE_START = phase_start
    _ELEM_PHASE_END   = phase_end
    _active_elem_flags = (
        [flag for _, flag, size, _, _ in COLORING_ELEMENTS if (flag(tp3d) if callable(flag) else getattr(tp3d, flag) == 1) and map_km <= size]
        + (['_ocean']    if tp3d.el_oActive == 1 else [])
        + (['_buildings'] if tp3d.el_bActive == 1 and map_km <= const.BUILDINGS_MAXSIZE else [])
        + (['_roads']    if any([tp3d.el_sBigActive, tp3d.el_sMedActive, tp3d.el_sSmallActive]) and map_km <= const.ROADS_MAXSIZE else [])
    )
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
        (tp3d.col_wPondsActive or tp3d.col_wSmallRiversActive or tp3d.col_wBigRiversActive)
        and map_km <= const.WATER_MAXSIZE
    )
    _ocean_active = tp3d.el_oActive == 1
    _water_ocean_combined = _water_feat_active and _ocean_active

    # --------------------------------------------------
    # Fetch all active OSM kinds unless already done by the background thread
    # started before elevation (prefetched_osm != None means data is ready).
    # --------------------------------------------------
    if prefetched_osm is None:
        _lat_step = min(2.0, tp3d.maxLat - tp3d.minLat)
        _lon_step = min(2.0, tp3d.maxLon - tp3d.minLon)
        _tile_lats = math.ceil((tp3d.maxLat - tp3d.minLat) / _lat_step)
        _tile_lons = math.ceil((tp3d.maxLon - tp3d.minLon) / _lon_step)
        _tile_tasks = [
            (tp3d.minLat + k * _lat_step,
             tp3d.minLon + l * _lon_step,
             tp3d.minLat + k * _lat_step + _lat_step,
             tp3d.minLon + l * _lon_step + _lon_step)
            for k in range(_tile_lats)
            for l in range(_tile_lons)
        ]
        _overpass_semaphore = threading.Semaphore(1)  # max 1 concurrent live Overpass request (avoid 429s on the public instance)
        _fetch_settings = OsmFetchSettings(
            disable_cache       = tp3d.disableCache,
            api_retries         = tp3d.apiRetries,
            mapsize             = tp3d.sMapInKm,
            road_big            = bool(tp3d.el_sBigActive),
            road_med            = bool(tp3d.el_sMedActive),
            road_small          = bool(tp3d.el_sSmallActive),
            water_ponds         = bool(tp3d.col_wPondsActive),
            water_small_rivers  = bool(tp3d.col_wSmallRiversActive),
            water_big_rivers    = bool(tp3d.col_wBigRiversActive),
        )
        _active_kind_tasks = [
            (key.upper(), _tile_tasks)
            for key, flag_attr, max_size, _, _ in COLORING_ELEMENTS
            if (flag_attr(tp3d) if callable(flag_attr) else getattr(tp3d, flag_attr) == 1)
            and map_km <= max_size
        ]
        _all_prefetched = _fetch_all_kinds_parallel(_active_kind_tasks, _overpass_semaphore,
                                                    settings=_fetch_settings)
    else:
        _all_prefetched = prefetched_osm

    # After batch download completes, show 100% for all fetched kinds so the
    # strip indicates the download is done while mesh building is still pending.
    # The final set_fetch_done/empty/filtered below flips each badge to ✓ once
    # the mesh operations for that kind are complete.
    if _ov.active:
        for key, flag_attr, max_size, _, _ in COLORING_ELEMENTS:
            if (flag_attr(tp3d) if callable(flag_attr) else getattr(tp3d, flag_attr) == 1) and map_km <= max_size:
                if _all_prefetched.get(key.upper()):
                    _ov.set_fetch_ready(key)
        # Buildings, roads, and ocean are pre-fetched in the same batch but aren't
        # in COLORING_ELEMENTS, so mark them ready here too.
        if tp3d.el_bActive == 1 and map_km <= const.BUILDINGS_MAXSIZE and _all_prefetched.get('BUILDINGS'):
            _ov.set_fetch_ready('buildings')
        if any([tp3d.el_sBigActive, tp3d.el_sMedActive, tp3d.el_sSmallActive]) and map_km <= const.ROADS_MAXSIZE and _all_prefetched.get('STREETS'):
            _ov.set_fetch_ready('roads')
        if tp3d.el_oActive == 1 and _all_prefetched.get('COASTLINE'):
            _ov.set_fetch_ready('water')

    terrain = {}
    for key, flag_attr, max_size, phase, msg in COLORING_ELEMENTS:
        terrain[key] = None
        if (flag_attr(tp3d) if callable(flag_attr) else getattr(tp3d, flag_attr) == 1):
            if map_km <= max_size:
                _advance_elem_progress(phase, msg)
                _result = coloring_main(obj, key.upper(), prefetched_tiles=_all_prefetched.get(key.upper(), {}))
                if _result is _COLORING_EMPTY:
                    terrain[key] = None
                    _ov.set_fetch_empty(key)
                elif _result is _COLORING_FILTERED:
                    terrain[key] = None
                    _ov.set_fetch_filtered(key)
                elif _result is _COLORING_PAINTED:
                    terrain[key] = None          # object was deleted after painting
                    _ov.set_fetch_done(key, success=True)
                elif _result is None:
                    terrain[key] = None
                    _ov.set_fetch_done(key, success=False)
                else:
                    terrain[key] = _result
                    _ov.set_fetch_done(key, success=True)
                if key == 'water' and _water_ocean_combined:
                    # Ocean will complete this chip; hold at 50%
                    _ov.set_fetch_progress('water', 0.5)
            else:
                print(f"INFO: MAP IS TOO BIG FOR {key.upper()} (< {max_size} km required)")
                _progress.WarningsOverlay.add_warning(f"Map too big for {phase} layer.", "warn")


    # --------------------------------------------------
    # Ocean — unique creation logic, no size cap.
    # --------------------------------------------------
    terrain['ocean'] = None
    if tp3d.el_oActive == 1:
        _advance_elem_progress("Ocean", "Creating ocean…")
        _ov.set_fetch_progress('water', 0.5 if _water_feat_active else 0.0)
        print("Create Ocean")
        _coastline_tiles = _all_prefetched.get("COASTLINE", {})
        terrain['ocean'] = createOcean(_coastline_tiles, scaleHor, obj)
        _ov.set_fetch_done('water', success=terrain['ocean'] is not None)

    print("Base elements Created")


    # --------------------------------------------------
    # Warn if buildings or roads are used together with any singleColorMode.
    # --------------------------------------------------
    _roads_active = any([tp3d.el_sBigActive, tp3d.el_sMedActive, tp3d.el_sSmallActive])
    _any_scm = tp3d.singleColorMode or "SINGLECOLORMODE" in tp3d.elementMode
    #if (tp3d.el_bActive == 1 or _roads_active) and _any_scm:
    #    _progress.WarningsOverlay.add_warning("3D Elements (Buildings/Roads) are not compatible with SingleColorMode", "warn")

    # --------------------------------------------------
    # Buildings — own creation function + intersection post-processing.
    # --------------------------------------------------
    terrain['buildings'] = None
    if tp3d.el_bActive == 1:
        if map_km <= const.BUILDINGS_MAXSIZE:
            _advance_elem_progress("Buildings", "Fetching building data…")
            _ov.set_fetch_progress('buildings', 0.0)
            _ov.set_fetch_ready('buildings')
            buildings = create_buildings(obj, 10, scaleHor)

            if buildings is not None:
                # Buildings are already clipped to the map shape in 2D inside
                # create_buildings, so no 3D boolean clip is needed here.
                set_origin_to_3d_cursor(buildings)
                buildings.name = obj.name + "_" + "BUILDINGS"
                terrain['buildings'] = buildings
                writeMetadata(buildings, type="BUILDINGS")
            _ov.set_fetch_done('buildings', success=buildings is not None)
        else:
            print("INFO: MAP IS TOO BIG FOR BUILDINGS (< 10Km Map size Required)")
            _progress.WarningsOverlay.add_warning("Map too big for Buildings.", "warn")

    # --------------------------------------------------
    # Roads — own creation function + clipping + material post-processing.
    # --------------------------------------------------
    terrain['roads'] = None
    if any([tp3d.el_sBigActive, tp3d.el_sMedActive, tp3d.el_sSmallActive]):
        if map_km <= const.ROADS_MAXSIZE:
            _advance_elem_progress("Roads", "Fetching road data…")
            _ov.set_fetch_progress('roads', 0.0)
            _ov.set_fetch_ready('roads')
            roads = create_roads(obj, tp3d.el_sHeight, scaleHor, map_km)
            if roads is not None:
                roads = bpy.context.active_object
                roads.data.materials.clear()
                roads.data.materials.append(bpy.data.materials.get("BLACK"))
                terrain['roads'] = roads
                roads.name = obj.name + "_" + "ROADS"
                writeMetadata(roads, type="ROADS")
                _ov.set_fetch_done('roads', success=True)
            else:
                print("INFO: No road data returned, skipping road processing.")
                _progress.WarningsOverlay.add_warning("No road data returned.", "warn")
                _ov.set_fetch_done('roads', success=False)
        else:
            print("INFO: MAP IS TOO BIG FOR STREETS (< 100Km Map size Required)")
            _progress.WarningsOverlay.add_warning("Map too big for Roads.", "warn")

    return terrain


def _rg_apply_single_color_mode(obj, curveObjs, terrain, props):

    print("SCM-----")
    """Apply single-color-mode boolean projection between terrain layers and curves.

    Terrain elements are processed in priority order: each element subtracts
    thicker versions of all higher-priority elements that were already processed.
    To add a new terrain layer, append its key to TERRAIN_PRIORITY_ORDER and make
    sure it is populated in the terrain dict passed by the caller.
    """
    from .mesh_ops import single_color_mode_curve, single_color_mode_mesh_wireframe, single_color_mode_mesh_remesh, boolean_operation, selectBottomFaces, recalculateNormals, remeshClearing  # deferred to avoid circular import at load time
    from .scene import remove_objects  # deferred to avoid circular import at load time

    # Priority order: index 0 = highest priority (subtracted from everything below it).
    # Add new terrain keys here to include them automatically.
    TERRAIN_PRIORITY_ORDER = ['water', 'forest', 'scree', 'city', 'greenspace', 'farmland', 'glacier', 'ocean']
 

    thickerCurves = []
    if props['singleColorMode']:
        if curveObjs:
            dpt = 1
            dup = obj.copy()
            dup.data = obj.data.copy()
            dup.name = f"{obj.name}_dup_for_projection"
            if obj.users_collection:
                for coll in obj.users_collection:
                    coll.objects.link(dup)
            survivingCurveObjs = []
            for tcrv in curveObjs:
                result = single_color_mode_curve(tcrv, obj, True, dpt, dup)
                if result is not None and result[1] is not None:
                    survivingCurveObjs.append(result[0])
                    thickerCurves.append(result[1])
            remove_objects(dup)
            for tcrv in thickerCurves:
                bpy.ops.object.select_all(action='DESELECT')
                tcrv.select_set(True)
                bpy.context.view_layer.objects.active = tcrv
            for i in range(len(thickerCurves)):
                recalculateNormals(thickerCurves[i])
                thickerCurves[i].location.z -= 0.001
                for j in range(i + 1, len(survivingCurveObjs)):
                    recalculateNormals(survivingCurveObjs[j])
                    boolean_operation(survivingCurveObjs[j], thickerCurves[i])

    if props['elementMode'] == "SEPARATE" and 1 == 0:
        for i, key in enumerate(TERRAIN_PRIORITY_ORDER):

            elem_obj = terrain.get(key)

            if not elem_obj:
                continue
            _ov = _progress.ProgressOverlay.get()
            if _ov.active:
                _ov.update(message=f"Processing {key.capitalize()}…")

            recalculateNormals(elem_obj)

            selectBottomFaces(elem_obj)
            bpy.ops.mesh.select_more()
            bpy.ops.mesh.delete(type='FACE')
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.mesh.extrude_region_move(TRANSFORM_OT_translate={"value": (0, 0, -1)})
            bpy.ops.object.mode_set(mode='OBJECT')


            if props['singleColorMode']:
                for tcrv in curveObjs:
                    boolean_operation(elem_obj, tcrv)

    if props['elementMode'] in ("SINGLECOLORMODE", "SINGLECOLORMODE_REMESH"):

        _ov = _progress.ProgressOverlay.get()
        if _ov.active:
            _ov.update(message=f"Applying Single-color Mode…")
        # Maps key -> thicker mesh object, filled as each element is processed.
        thicker_by_key = {}

        _scm_fn = single_color_mode_mesh_remesh if props['elementMode'] == "SINGLECOLORMODE_REMESH" else single_color_mode_mesh_wireframe

        _active_scm_keys = [k for k in TERRAIN_PRIORITY_ORDER if terrain.get(k)]
        _n_scm = max(1, len(_active_scm_keys))
        _scm_done = 0

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

            thicker = _scm_fn(elem_obj, obj)
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

        for thicker in thicker_by_key.values():
            #pass
            remove_objects(thicker)

    if props['elementMode'] == "SEPARATE" and thickerCurves:
        for key in TERRAIN_PRIORITY_ORDER:
            elem_obj = terrain.get(key)
            if not elem_obj:
                continue
            _ov = _progress.ProgressOverlay.get()
            if _ov.active:
                _ov.update(message=f"Cutting trail from {key.capitalize()}…")
            for tcrv in thickerCurves:
                boolean_operation(elem_obj, tcrv)

    # Subtract the trail groove from buildings and roads so the trail cutout
    # isn't blocked by 3D elements regardless of element mode.
    if thickerCurves:
        for key in ('buildings', 'roads'):
            elem_obj = terrain.get(key)
            if not elem_obj:
                continue
            _ov = _progress.ProgressOverlay.get()
            if _ov.active:
                _ov.update(message=f"Subtracting trail from {key.capitalize()}…")
            for tcrv in thickerCurves:
                boolean_operation(elem_obj, tcrv)

    if thickerCurves:
        if bpy.app.debug:
            obj_size = props.get('size', 100)
            for tcrv in thickerCurves:
                tcrv.location.x += obj_size
        else:
            remove_objects(thickerCurves)


def _rg_assign_materials_and_export(obj, curveObjs, textobj, plateobj, props, buggyDataset, start_time, exportformat, elements=None):
    """Assign trail/text materials, export all STL files, write metadata, and finalize."""
    from .metadata import writeMetadata  # deferred to avoid circular import at load time
    from ..export import export_to_STL, export_selected_to_3mf, is_3mf_extension_installed  # deferred to avoid circular import at load time
    from .elevation import load_counter  # deferred to avoid circular import at load time
    from .scene import zoom_camera_to_selected  # deferred to avoid circular import at load time

    if "shape" in props.keys():
        shape = props['shape']
    else:
        shape = None

    bpy.ops.object.select_all(action='DESELECT')

    # Embed metadata into scene objects
    writeMetadata(obj, "MAP")
    if curveObjs:
        for tcrv in curveObjs:
            try:
                if tcrv and tcrv.name in bpy.data.objects:
                    writeMetadata(tcrv, "TRAIL")
            except ReferenceError:
                pass

    # Assign alternating TRAIL/YELLOW materials to trail curve segments
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

    # Assign materials to text/plate objects (always, regardless of export settings)
    if shape in {"HEXAGON INNER TEXT", "HEXAGON OUTER TEXT", "OCTAGON OUTER TEXT", "HEXAGON FRONT TEXT", "MEDAL"} and textobj:
        mat_name = "TRAIL" if shape == "HEXAGON INNER TEXT" else "WHITE"
        mat = bpy.data.materials.get(mat_name)
        textobj.data.materials.clear()
        textobj.data.materials.append(mat)
        writeMetadata(textobj, type="TEXT")

    if shape in {"HEXAGON OUTER TEXT", "OCTAGON OUTER TEXT", "HEXAGON FRONT TEXT", "MEDAL"} and plateobj:
        mat = bpy.data.materials.get("BLACK")
        plateobj.data.materials.clear()
        plateobj.data.materials.append(mat)
        writeMetadata(plateobj, type="PLATE")

    # Export all geometry
    tp3d_props = bpy.context.scene.tp3d
    if getattr(tp3d_props, 'disable_auto_export', False):
        print("Auto export disabled, skipping export")
        return

    if is_3mf_extension_installed() and not getattr(tp3d_props, 'disable_3mf_export', False):
        print("Exporting to 3mf")
        # Select all objects to export, then use the 3MF exporter
        if curveObjs:
            for tcrv in curveObjs:
                try:
                    if tcrv and tcrv.name in bpy.data.objects:
                        tcrv.select_set(True)
                except ReferenceError:
                    pass
        obj.select_set(True)

        # Terrain elements in SEPARATE mode: select for export
        if elements and (props.get('elementMode') == "SEPARATE" or "SINGLECOLORMODE" in props.get('elementMode')):
            for elem_obj in elements.values():
                if elem_obj and elem_obj.name in bpy.data.objects:
                    elem_obj.select_set(True)
        elif elements and props.get('elementMode') == "PAINT":
            for key in ("roads", "buildings"):
                elem_obj = elements.get(key)
                if elem_obj and elem_obj.name in bpy.data.objects:
                    elem_obj.select_set(True)

        if shape in {"HEXAGON INNER TEXT", "HEXAGON OUTER TEXT", "OCTAGON OUTER TEXT", "HEXAGON FRONT TEXT", "MEDAL"} and textobj:
            textobj.select_set(True)

        if shape in {"HEXAGON OUTER TEXT", "OCTAGON OUTER TEXT", "HEXAGON FRONT TEXT", "MEDAL"} and plateobj:
            plateobj.select_set(True)

        export_selected_to_3mf()
    else:
        print("exporting as STL/OBJ")
        if curveObjs:
            for tcrv in curveObjs:
                export_to_STL(tcrv, exportformat)
        export_to_STL(obj, exportformat)

        # Terrain elements in SEPARATE mode: export individually
        if elements and props.get('elementMode') == "SEPARATE":
            for elem_obj in elements.values():
                if elem_obj and elem_obj.name in bpy.data.objects:
                    export_to_STL(elem_obj, exportformat)

        if shape in {"HEXAGON INNER TEXT", "HEXAGON OUTER TEXT", "OCTAGON OUTER TEXT", "HEXAGON FRONT TEXT", "MEDAL"} and textobj:
            export_to_STL(textobj, exportformat)

        if shape in {"HEXAGON OUTER TEXT", "OCTAGON OUTER TEXT", "HEXAGON FRONT TEXT", "MEDAL"} and plateobj:
            export_to_STL(plateobj, exportformat)




    # API counter update
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

    if buggyDataset != 0:
        _progress.WarningsOverlay.add_warning("API might have faulty DATA. Maybe try diffrent Resolution or API", "warn")

    zoom_camera_to_selected(obj)


# ---------------------------------------------------------------------------
# Generation feature flags
# Each type integer maps to a frozenset of capability strings used throughout
# the pipeline instead of scattered `if type == X` comparisons.
# ---------------------------------------------------------------------------

_GEN_FLAGS = {
    0:  frozenset({"gpx_file",  "trail", "stats", "gpx_scale"}),
    1:  frozenset({"gpx_chain", "trail", "stats", "gpx_scale", "separate_paths", "chain_coords_center"}),
    2:  frozenset({"jmap"}),
    3:  frozenset({"jmap_bbox"}),
    4:  frozenset({"gpx_file",  "jmap",  "trail", "trail_map"}),
    10: frozenset({"gpx_file", "stats", "gpx_scale"}),
    11: frozenset({"gpx_chain", "stats", "gpx_scale", "separate_path", "chain_coords_center"}),
    20: frozenset({"gpx_file", "trail", "stats", "gpx_scale","append_collection"}),
    21: frozenset({"gpx_chain", "trail", "stats", "gpx_scale", "separate_paths", "chain_coords_center","append_collection"}),
}

# ---------------------------------------------------------------------------
# Shared helper: build the fetch-item list for the progress chip strip
# ---------------------------------------------------------------------------

def build_fetch_items(map_km=None):
    """Return the list of fetch-item dicts for the active scene settings."""
    tp3d = bpy.context.scene.tp3d
    if map_km is None:
        map_km = round(tp3d.get("sMapInKm", 0), 1)
    items = [{'key': 'elevation', 'icon': 'E', 'label': 'Elevation'}]
    defs = [
        ('forest',     'col_fActive',   const.FOREST_MAXSIZE,     'F', 'Forest'),
        ('water',      None,            const.WATER_MAXSIZE,       'W', 'Water'),
        ('scree',      'col_scrActive', const.SCREE_MAXSIZE,       'S', 'Scree'),
        ('city',       'col_cActive',   const.CITY_MAXSIZE,        'C', 'City'),
        ('greenspace', 'col_grActive',  const.GREENSPACE_MAXSIZE,  'G', 'Green'),
        ('farmland',   'col_faActive',  const.FARMLAND_MAXSIZE,    'A', 'Farm'),
        ('glacier',    'col_glActive',  const.GLACIER_MAXSIZE,     'I', 'Glacr'),
        ('buildings',  'el_bActive',    const.BUILDINGS_MAXSIZE,   'B', 'Build'),
        ('roads',      None,            const.ROADS_MAXSIZE,       'R', 'Roads'),
    ]
    for key, flag, max_size, icon, label in defs:
        if key == 'water':
            water_feats = ((tp3d.col_wPondsActive or tp3d.col_wSmallRiversActive
                            or tp3d.col_wBigRiversActive) and map_km <= const.WATER_MAXSIZE)
            active = water_feats or (tp3d.el_oActive == 1)
            max_size = None
        elif key == 'roads':
            active = any([tp3d.el_sBigActive, tp3d.el_sMedActive, tp3d.el_sSmallActive])
        else:
            active = bool(flag and getattr(tp3d, flag, 0) == 1)
        if active and (max_size is None or map_km <= max_size):
            items.append({'key': key, 'icon': icon, 'label': label})
    return items


# ---------------------------------------------------------------------------
# Main generation orchestrator
# ---------------------------------------------------------------------------

def runGeneration(type, locked_scale=None):

    """Orchestrate the full 3D map generation pipeline."""
    from .geo import calculate_scale, convert_to_blender_coordinates_batch, haversine, separate_duplicate_xy  # deferred to avoid circular import at load time
    from .primitives import simplify_curve, create_curve_from_coordinates  # deferred to avoid circular import at load time
    from .elevation import get_tile_elevation  # deferred to avoid circular import at load time
    from .scene import zoom_camera_to_selected, show_message_box, transform_MapObject, set_origin_to_3d_cursor, remove_objects  # deferred to avoid circular import at load time
    from .mesh_ops import RaycastCurveToMesh, splitCurves, recalculateNormals, merge_with_map  # deferred to avoid circular import at load time
    from .text_objects import HexagonInnerText, HexagonOuterText, HexagonFrontText, OctagonOuterText, MedalText  # deferred to avoid circular import at load time
    from .terrain import plateInsert  # deferred to avoid circular import at load time

    flags = _GEN_FLAGS[type]

    overlay = _progress.ProgressOverlay.get()
    overlay.start()
    _progress.WarningsOverlay.clear()

    # --- Phase 1: Validate inputs and load all scene settings ---
    overlay.update(0.03, "Initializing", "Validating inputs…")
    props = _rg_validate_inputs(flags)
    if props is None:
        overlay.finish()
        return
    start_time = props['start_time']
    buggyDataset = 0
    exportformat = "STL"

    overlay.add_completed_step("Inputs validated")

    # --- Phase 2: Load coordinate data from GPX / synthetic source ---
    overlay.update(0.08, "Loading Data", "Reading GPX file…")
    coord_data = _rg_load_coordinates(flags, props)
    if coord_data is None:
        overlay.finish()
        return
    coordinates, separate_paths, coordinates2, separate_paths_by_file = coord_data

    # --- Phase 3: Calculate and store trail statistics ---
    overlay.update(0.12, "Trail Statistics", "Computing distances & elevation gain…")
    _rg_compute_trail_stats(flags, coordinates)

    # --- Phase 4: Interpolate path to at least 300 points for a smooth curve ---
    overlay.update(0.16, "Path Interpolation", "Smoothing trail curve…")
    while len(coordinates) < 300 and len(coordinates) > 1 and "trail" in flags:
        n = len(coordinates)
        xyz = np.array([(c[0], c[1], c[2]) for c in coordinates], dtype=np.float64)
        mids = (xyz[:-1] + xyz[1:]) / 2.0
        # Interleave originals and midpoints: [orig0, mid0, orig1, mid1, ..., origN]
        out = [None] * (2 * n - 1)
        out[0::2] = coordinates
        out[1::2] = [
            (mids[i, 0], mids[i, 1], mids[i, 2], coordinates[i][3])
            for i in range(n - 1)
        ]
        coordinates = out

    # --- Phase 5: Calculate horizontal scale factor ---
    overlay.update(0.20, "Scale Calculation", "Computing horizontal scale…")
    scalecoords = coordinates
    if props['scalemode'] == "COORDINATES" and "gpx_scale" in flags:
        scalecoords = ((props['scaleLon1'], props['scaleLat1']), (props['scaleLon2'], props['scaleLat2']))
    scaleHor = locked_scale if locked_scale is not None else calculate_scale(props['size'], scalecoords, type, diagonal=True)
    bpy.context.scene.tp3d["sScaleHor"] = scaleHor

    # --- Phase 6: Convert to Blender coordinates and find map center ---
    overlay.update(0.24, "Coordinate Conversion", "Converting to Blender space…")
    blender_coords = convert_to_blender_coordinates_batch(coordinates)
    blender_coords_separate = []
    if "separate_paths" in flags or len(separate_paths) > 1:
        blender_coords_separate = [
            convert_to_blender_coordinates_batch(path)
            for path in separate_paths
        ]
    blender_coords_by_file = []
    if separate_paths_by_file:
        blender_coords_by_file = [
            [convert_to_blender_coordinates_batch(seg) for seg in file_segs]
            for file_segs in separate_paths_by_file
        ]
    min_x = min(p[0] for p in blender_coords)
    max_x = max(p[0] for p in blender_coords)
    min_y = min(p[1] for p in blender_coords)
    max_y = max(p[1] for p in blender_coords)
    centerx = (max_x - min_x) / 2 + min_x
    centery = (max_y - min_y) / 2 + min_y
    bpy.context.scene.tp3d["o_centerx"] = centerx
    bpy.context.scene.tp3d["o_centery"] = centery

    # --- Phase 7: Remove previously generated objects at the same location ---
    overlay.update(0.28, "Scene Cleanup", "Removing previous objects…")
    xOff = props['xTerrainOffset']
    yOff = props['yTerrainOffset']
    target_2d        = Vector((centerx, centery))
    target_2d_offset = Vector((centerx + xOff, centery + yOff))
    for obs in bpy.data.objects:
        obj_2d        = Vector((obs.location.x, obs.location.y))
        obj_2d_offset = obj_2d
        if "xTerrainOffset" in obs.keys() or "yTerrainOffset" in obs.keys():
            obj_2d_offset = Vector((obs.location.x - obs["xTerrainOffset"], obs.location.y - obs["yTerrainOffset"]))
        if (obj_2d - target_2d).length <= 0.2 or (obj_2d - target_2d_offset).length <= 0.2:
            bpy.data.objects.remove(obs, do_unlink=True)
        elif (obj_2d_offset - target_2d).length <= 0.2 or (obj_2d_offset - target_2d_offset).length <= 0.2:
            bpy.data.objects.remove(obs, do_unlink=True)
    bpy.ops.object.select_all(action='DESELECT')

    _tp3d = bpy.context.scene.tp3d
    if "stats" in flags and _tp3d.total_length > 0:
        overlay.add_completed_step(f"GPX loaded  —  {_tp3d.total_length:.1f} km, {int(_tp3d.total_elevation)} m gain")
    else:
        overlay.add_completed_step("GPX data loaded")

    # --- Phase 8: Create base map shape ---
    overlay.update(0.33, "Building Map Shape", "Creating base mesh…")
    MapObject = _rg_create_map_object(flags, props, props['modelname'], centerx, centery)

    props["currentMap"] = MapObject
    bpy.context.scene.tp3d.currentMap = MapObject

    zoom_camera_to_selected(MapObject)

    # Swap in trail_map GPX coordinates after the shape is positioned
    if "trail_map" in flags:
        coordinates = coordinates2

    compute_and_store_tile_bounds(MapObject)

    _map_km = round(bpy.context.scene.tp3d.get("sMapInKm", 0), 1)
    overlay.add_completed_step(f"Map shape created  ({props['shape'].capitalize()}, {_map_km} km)")

    # Build the fetch-item strip
    overlay.set_fetch_items(build_fetch_items(_map_km))

    # --- OSM background prefetch: start now so Overpass requests overlap with elevation download ---
    _tp3d_snap = bpy.context.scene.tp3d
    _osm_prefetch_thread, _osm_prefetched = _rg_start_osm_prefetch(_tp3d_snap, _map_km)
    if _osm_prefetch_thread is not None:
        print("OSM prefetch started (overlapping elevation download)")

    # --- Phase 9: Fetch terrain elevation data ---
    overlay.update(0.38, "Fetching Elevation Data", "Querying API — this may take a moment…")
    print("------------------------------------------------")
    print("FETCHING ELEVATION DATA FOR THE MAP")
    print("------------------------------------------------")
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

    def _elevation_progress(pct):
        t = pct / 100.0
        overlay.set_fetch_progress('elevation', t)
        overlay.update(
            0.38 + t * (0.65 - 0.38),
            "Fetching Elevation Data",
            f"Querying elevation API…",
            sub_percent=t,
            sub_label="Tiles processed",
        )

    tileVerts, diff = get_tile_elevation(MapObject, progress_cb=_elevation_progress)
    print("Elevation Data fetched")
    overlay.sub_percent = None   # hide sub-bar now that elevation is done
    overlay.set_fetch_done('elevation', success=True)
    overlay.add_completed_step(f"Elevation fetched  ({len(tileVerts)} pts)")
    overlay.update(0.65, "Elevation Data Ready", f"{len(tileVerts)} points fetched")

    if len(tileVerts) < 1000:
        _progress.WarningsOverlay.add_warning(f"Mesh has only {len(tileVerts)} Points. Increase Resolution for higher Quality", "warn")
    if props['fixedElevationScale']:
        autoScale = 10 / (diff / 1000) if diff > 0 else 10
    else:
        autoScale = scaleHor
    bpy.context.scene.tp3d.sAutoScale = autoScale

    if not props['fixedElevationScale']:
        if diff == 0:
            _progress.WarningsOverlay.add_warning("Terrain seems to be really flat. If not intended, increase Elevation scale", icon="warn")
        elif (diff / 1000) * autoScale * props['scaleElevation'] < 2:
            _progress.WarningsOverlay.add_warning("Terrain seems to be really flat. If not intended, increase Elevation scale", icon="warn")

    # Recalculate blender coords with elevation applied, simplify, deduplicate
    blender_coords = convert_to_blender_coordinates_batch(coordinates)
    if bpy.app.debug:
        _g_slopes = []
        _all_segs = blender_coords_separate if blender_coords_separate else [blender_coords]
        for _seg in _all_segs:
            for _i in range(len(_seg) - 1):
                x1, y1, z1 = _seg[_i]
                x2, y2, z2 = _seg[_i+1]
                _h = math.sqrt((x2-x1)**2 + (y2-y1)**2)
                if _h > 0:
                    _g_slopes.append(abs(z2 - z1) / _h)
        if _g_slopes:
            _avg_g = sum(_g_slopes) / len(_g_slopes)
            print(f"[DEBUG] GPX avg slope:     {_avg_g:.4f}  ({math.degrees(math.atan(_avg_g)):.2f}°)")
    blender_coords = simplify_curve(blender_coords, .12)
    print("Removing duplicates")
    blender_coords = separate_duplicate_xy(blender_coords, 0.05)
    if ("separate_paths" in flags or len(separate_paths) > 1) and "trail_map" not in flags:
        blender_coords_separate = [
            separate_duplicate_xy(convert_to_blender_coordinates_batch(path), 0.05)
            for path in separate_paths
        ]
    if separate_paths_by_file and "trail_map" not in flags:
        blender_coords_by_file = [
            [separate_duplicate_xy(convert_to_blender_coordinates_batch(seg), 0.05) for seg in file_segs]
            for file_segs in separate_paths_by_file
        ]

    # Store real-world map scale
    lat1, lon1 = coordinates[0][0], coordinates[0][1]
    lat2, lon2 = coordinates[-1][0], coordinates[-1][1]
    tdist  = haversine(lat1, lon1, lat2, lon2)
    mscale = (tdist / props['size']) * 1000000
    bpy.context.scene.tp3d["o_mapScale"] = f"{mscale:.0f}"

    # --- Phase 10: Build trail curves ---
    overlay.update(0.70, "Building Trail", "Creating curve objects…")
    curveObj  = None
    curveObjs = None
    print("Building trail curve(s)")
    try:
        if ("gpx_file" in flags and "trail_map" not in flags and len(blender_coords_separate) <= 1) or "trail_map" in flags:
            # Single segment or trail_map: one curve directly
            create_curve_from_coordinates(blender_coords)
            curveObj = bpy.context.view_layer.objects.active
        elif "gpx_chain" in flags and blender_coords_by_file and "trail_map" not in flags and "trail" in flags:
            # Multi-file: create one object per file by joining its segments as separate splines
            curveObjs = []
            for file_segs in blender_coords_by_file:
                bpy.ops.object.select_all(action='DESELECT')
                for crds in file_segs:
                    create_curve_from_coordinates(crds)
                if len(file_segs) > 1:
                    bpy.ops.object.join()
                curveObjs.append(bpy.context.view_layer.objects.active)
        elif ("separate_paths" in flags or len(blender_coords_separate) > 1) and "trail_map" not in flags and "trail" in flags:
            # Single file with multiple segments: join all into one object
            bpy.ops.object.select_all(action='DESELECT')
            for crds in blender_coords_separate:
                create_curve_from_coordinates(crds)
            bpy.ops.object.join()
            curveObjs = [bpy.context.view_layer.objects.active]
    except Exception as e:
        show_message_box("Bad Response from API while creating the curve. If this happens everytime contact dev")
        overlay.finish()
        return
    
    print(f"Curve objects created: {len(curveObjs) if curveObjs else 0}")

    if curveObjs is None:
        curveObjs = splitCurves(curveObj)
    curveObj  = None
    bpy.ops.object.select_all(action='DESELECT')

    if curveObjs:
        props["currentTrail"] = curveObjs[0]
        bpy.context.scene.tp3d.currentTrail = curveObjs[0]

    _n_segs = len(curveObjs) if curveObjs else 0
    _n_pts  = len(blender_coords)
    overlay.add_completed_step(f"Trail built  —  {_n_segs} seg{'s' if _n_segs != 1 else ''}, {_n_pts} pts")

    # --- Phase 11: Apply terrain elevation to mesh vertices ---
    overlay.update(0.75, "Applying Terrain", "Displacing mesh vertices…")
    mesh = MapObject.data
    _total_verts = len(mesh.vertices)

    # Bulk-read vertex coords into numpy array
    co_flat = np.empty(_total_verts * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", co_flat)
    co = co_flat.reshape((_total_verts, 3))

    # Transform local coords to world space and extract world Y for Mercator correction
    m = np.array(MapObject.matrix_world, dtype=np.float64)
    co_h = np.hstack([co, np.ones((_total_verts, 1), dtype=np.float64)])
    world_y = (m @ co_h.T).T[:, 1]

    # Mercator latitude correction: stay in radians — skip the degrees roundtrip
    # convert_to_geo: lat_deg = degrees(2*atan(exp(y/(R*scaleHor))) - pi/2)
    # We need cos(radians(lat_deg)) = cos(lat_rad), so compute lat_rad directly
    lat_rad = 2.0 * np.arctan(np.exp(world_y / (const.R * scaleHor))) - (np.pi / 2.0)
    merc = 1.0 / np.cos(lat_rad)

    # Compute new Z for all vertices at once and write back
    new_z = np.array(tileVerts, dtype=np.float64) / 1000.0 * props['scaleElevation'] * autoScale * merc
    co[:, 2] = new_z
    mesh.vertices.foreach_set("co", co.ravel())
    mesh.update()

    lowestZ  = float(new_z.min())
    highestZ = float(new_z.max())
    overlay.update(0.80, "Terrain Ready", "Vertices displaced…")
    overlay.sub_percent = None
    additionalExtrusion = lowestZ
    bpy.context.scene.tp3d.sAdditionalExtrusion = additionalExtrusion
    bpy.context.scene.tp3d.lowestZ  = lowestZ
    bpy.context.scene.tp3d.highestZ = highestZ

    print(f"additionalExtrusion: {additionalExtrusion}")
    print(f"Lowest z: {lowestZ}")
    print(f"Highest z: {highestZ}")
    if bpy.app.debug:
        _t_slopes = []
        for edge in mesh.edges:
            v1 = mesh.vertices[edge.vertices[0]].co
            v2 = mesh.vertices[edge.vertices[1]].co
            _h = math.sqrt((v2.x-v1.x)**2 + (v2.y-v1.y)**2)
            if _h > 0:
                _t_slopes.append(abs(v2.z - v1.z) / _h)
        if _t_slopes:
            _avg_t = sum(_t_slopes) / len(_t_slopes)
            print(f"[DEBUG] Terrain avg slope: {_avg_t:.4f}  ({math.degrees(math.atan(_avg_t)):.2f}°)")


    # Snap trail curves onto terrain surface
    if props['overwritePathElevation'] and curveObj is not None:
        RaycastCurveToMesh(curveObj, MapObject)
    if props['overwritePathElevation'] and curveObjs is not None:
        for tcrv in curveObjs:
            RaycastCurveToMesh(tcrv, MapObject)

    # Extrude map shape downward and set bottom face z
    bpy.context.view_layer.objects.active = MapObject
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.extrude_region_move()
    bpy.ops.mesh.dissolve_faces()
    bpy.ops.transform.translate(value=(0, 0, -1))
    bpy.ops.object.mode_set(mode='OBJECT')

    obj = bpy.context.object
    mesh = obj.data
    selected_faces = [face for face in mesh.polygons if face.select]
    if selected_faces:
        for face in selected_faces:
            for vert_idx in face.vertices:
                mesh.vertices[vert_idx].co.z = additionalExtrusion - props['minThickness']
    else:
        print("No face selected.")

    # Shift all geometry so bottom face sits at the correct z
    bpy.context.view_layer.objects.active = MapObject
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.transform.translate(value=(0, 0, -additionalExtrusion + props['minThickness']))
    bpy.ops.object.mode_set(mode='OBJECT')

    if curveObjs:
        for tcrv in curveObjs:
            bpy.context.view_layer.objects.active = tcrv
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.curve.select_all(action='SELECT')
            bpy.ops.transform.translate(value=(0, 0, -additionalExtrusion + props['minThickness']))
            bpy.ops.object.mode_set(mode='OBJECT')

    # Set object origins to tile location
    location = obj.location
    bpy.context.scene.cursor.location = location
    if curveObjs:
        for tcrv in curveObjs:
            tcrv.select_set(True)
            bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
    recalculateNormals(obj)

    # --- Phase 12-13: Create text / plate overlays for text-based shapes ---
    overlay.update(0.82, "Shape Overlays", "Adding text and plate elements…")
    textobj   = None
    plateobj  = None
    shape     = props['shape']
    plateThickness = props['plateThickness']
    shapeRotation  = props['shapeRotation']
    bpy.ops.object.select_all(action='DESELECT')

    if "append_collection" not in flags:
        if shape == "HEXAGON INNER TEXT":
            textobj = HexagonInnerText(MapObject)
        elif shape == "HEXAGON OUTER TEXT":
            textobj, plateobj = HexagonOuterText()
            obj.location.z += plateThickness
        elif shape == "OCTAGON OUTER TEXT":
            textobj, plateobj = OctagonOuterText()
            obj.location.z += plateThickness
        elif shape == "HEXAGON FRONT TEXT":
            textobj, plateobj = HexagonFrontText()
            obj.location.z += plateThickness
        elif shape == "MEDAL":
            textobj, plateobj = MedalText()
            obj.location.z += plateThickness
        else:
            pass  # BottomText() — currently disabled

    if ("TEXT" in shape and curveObjs is not None and "INNER TEXT" not in shape) or \
       (shape == "MEDAL" and curveObjs is not None):
        for tcrv in curveObjs:
            tcrv.location.z += plateThickness

    # Plate insert
    bpy.ops.object.select_all(action='DESELECT')
    dist = bpy.context.scene.tp3d.plateInsertValue
    if shape in {"HEXAGON OUTER TEXT", "OCTAGON OUTER TEXT", "HEXAGON FRONT TEXT", "MEDAL"}:
        if plateobj and textobj:
            transform_MapObject(plateobj, props['xTerrainOffset'], props['yTerrainOffset'])
            transform_MapObject(textobj,  props['xTerrainOffset'], props['yTerrainOffset'])
            set_origin_to_3d_cursor(plateobj)
            set_origin_to_3d_cursor(textobj)
            if dist > 0:
                plateInsert(plateobj, obj)
                textobj.location.z += dist
            if shapeRotation != 0:
                textobj.rotation_euler[2] += shapeRotation * (3.14159265 / 180)

    # --- Material preview mode ---
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            for space in area.spaces:
                if space.type == 'VIEW_3D':
                    space.shading.type = 'MATERIAL'

    # Apply BASE material to the map mesh
    mat = bpy.data.materials.get("BASE")
    obj.data.materials.clear()
    obj.data.materials.append(mat)

    # --- Phase 14: Create terrain overlay elements ---
    overlay.update(0.83, "Terrain Elements", "Adding elements…")
    if _osm_prefetch_thread is not None:
        _osm_prefetch_thread.join()
    elements = _rg_build_terrain_elements(obj, scaleHor, curveObj=curveObjs[0] if curveObjs else None,
                                          prefetched_osm=_osm_prefetched)
    

    # --- Phase 15: Single color mode processing ---
    overlay.update(0.95, "Coloring", "Applying single-color mode…") 
    _rg_apply_single_color_mode(obj, curveObjs, elements, props)

    _lo = bpy.context.scene.tp3d.lowestZ
    _hi = bpy.context.scene.tp3d.highestZ
    overlay.add_completed_step(f"Terrain applied  —  z {_lo:.1f} to {_hi:.1f}")

    if type == 20:
        for i, crv in enumerate(curveObjs):
            tmp = merge_with_map(obj, crv, False, False)
            remove_objects(crv)
            curveObjs[i] = tmp


    # --- Phases 16-18: Assign materials, export, and finalize ---
    overlay.update(0.97, "Finalizing", "Exporting files...")
    _rg_assign_materials_and_export(
        obj, curveObjs, textobj, plateobj, props, buggyDataset, start_time, exportformat, elements
    )
    # Script duration
    end_time = time.time()
    duration = end_time - start_time
    bpy.context.scene.tp3d.sRunDuration = round(duration)
    bpy.context.scene.tp3d["o_time"] = _("Script ran for {} seconds").format(round(duration))

    from .elevation import load_generation_counter, save_generation_counter
    _total_maps = load_generation_counter() + 1
    save_generation_counter(_total_maps)
    bpy.context.scene.tp3d["o_mapsGenerated"] = f"Maps Generated: {_total_maps}"

    if obj:
        obj["GenerationTime"] = round(duration)


    print(f"Finished. Generating Map took {duration:.0f} seconds")
    print("----------------------------------------------------------------")
    print(f"")


    _elapsed = int(time.time() - overlay._start_time) if overlay._start_time else 0
    _m, _s = divmod(_elapsed, 60)
    overlay.update(1.0, "Done", "")
    overlay.add_completed_step(f"Done  —  {_m:02d}:{_s:02d} total")
    overlay.finish()
    _progress.WarningsOverlay.get().show()


# ---------------------------------------------------------------------------
# createTerrainFromSelected sub-phase helpers
#
# Builds terrain on already-placed tile objects (blanks dropped by the map
# picker / puzzle picker / Extend flows) rather than running runGeneration's
# own from-scratch GPX pipeline -- shares the same _rg_* building blocks
# above, just driven from a different entry point.
# ---------------------------------------------------------------------------

def _ctfs_load_props():
    """Load all settings needed by createTerrainFromSelected from the scene."""
    tp3d = bpy.context.scene.tp3d
    return {
        'scaleElevation':     tp3d.scaleElevation,
        'api':                tp3d.api,
        'minThickness':       tp3d.minThickness,
        'autoScale':          tp3d.sAutoScale,
        'singleColorMode':    tp3d.singleColorMode,
        'elementMode':        tp3d.elementMode,
        'selfHosted':         tp3d.selfHosted,
        'indipendendTiles':   tp3d.indipendendTiles,
        'additionalExtrusion': tp3d.sAdditionalExtrusion,
        'scaleHor':           tp3d.get("sScaleHor", 1),
    }


def _ctfs_apply_elevation(zobj, props, progress_cb=None, skip_bottom_recess=False):
    """Fetch terrain elevation, apply to vertices, extrude bottom face, shift to z=0.

    Returns (lowestZ, highestZ, additionalExtrusion).
    The returned additionalExtrusion may differ from props['additionalExtrusion']
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
    from .elevation import get_tile_elevation  # deferred to avoid circular import at load time
    from .geo import convert_to_geo  # deferred to avoid circular import at load time

    scaleElevation      = props['scaleElevation']
    autoScale           = props['autoScale']
    minThickness        = props['minThickness']
    additionalExtrusion = props['additionalExtrusion']
    indipendendTiles    = props['indipendendTiles']

    print(f"additionalExtrusion: {additionalExtrusion}")

    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    tileVerts, diff = get_tile_elevation(zobj, progress_cb=progress_cb)

    # Reset scene property to original value before this tile
    bpy.context.scene.tp3d.sAdditionalExtrusion = additionalExtrusion

    if len(tileVerts) < 500:
        _progress.WarningsOverlay.add_warning(
            f"Mesh has only {len(tileVerts)} Points. Increase Resolution for higher Quality", "warn"
        )

    # Find elevation range
    mesh = zobj.data
    lowestZ  = 1000
    highestZ = 0
    _obj_matrix = zobj.matrix_world
    for i, vert in enumerate(mesh.vertices):
        _world_co = _obj_matrix @ vert.co
        _vert_lat, _unused_var = convert_to_geo(_world_co.x, _world_co.y)
        _merc = 1 / math.cos(math.radians(_vert_lat))
        val = tileVerts[i] / 1000 * scaleElevation * autoScale * _merc
        lowestZ  = min(lowestZ,  val)
        highestZ = max(highestZ, val)

    if indipendendTiles:
        additionalExtrusion = lowestZ

    # Apply elevation to vertices
    for i, vert in enumerate(mesh.vertices):
        _world_co = _obj_matrix @ vert.co
        _vert_lat, _unused_var = convert_to_geo(_world_co.x, _world_co.y)
        _merc = 1 / math.cos(math.radians(_vert_lat))
        vert.co.z = tileVerts[i] / 1000 * scaleElevation * autoScale * _merc
        lowestZ  = min(lowestZ,  vert.co.z)
        highestZ = max(highestZ, vert.co.z)

    # Extrude bottom face and set its z
    bpy.context.view_layer.objects.active = zobj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.extrude_region_move()
    bpy.ops.transform.translate(value=(0, 0, -8))
    bpy.ops.mesh.dissolve_faces()
    bpy.ops.object.mode_set(mode='OBJECT')

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
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.transform.translate(value=(0, 0, -additionalExtrusion + minThickness))
    bpy.ops.object.mode_set(mode='OBJECT')

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
                f"{zobj.name}: base recessed {bottom_drop:.0f}mm to keep the terrain seamless with the existing map", "warn"
            )

    return lowestZ, highestZ, additionalExtrusion, len(tileVerts)


def _ctfs_handle_trail(zobj, duplicate, singleColorMode):
    """Intersect or project trail curves onto this tile.

    In normal mode (singleColorMode=False): creates one extruded duplicate per
    _Trail curve and intersects each individually, returning a list of results.
    In single-color mode (singleColorMode=True): copies each trail curve for later
    processing by _rg_apply_single_color_mode.

    Returns curveObjs list (may be empty).
    """
    from .mesh_ops import intersect_trail_with_existing_box  # deferred to avoid circular import at load time

    def _xy_extents(obj):
        corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        xs = [c.x for c in corners]
        ys = [c.y for c in corners]
        return min(xs), max(xs), min(ys), max(ys)

    tx_min, tx_max, ty_min, ty_max = _xy_extents(zobj)

    def _near_tile(ob):
        cx_min, cx_max, cy_min, cy_max = _xy_extents(ob)
        return cx_min <= tx_max and cx_max >= tx_min and cy_min <= ty_max and cy_max >= ty_min

    search_str = "_Trail"
    matches = [ob for ob in bpy.context.view_layer.objects if search_str in ob.name and _near_tile(ob)]
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
        trail_matches = [ob for ob in matches if ob.type in {'CURVE', 'MESH'} and not ob.hide_get() and ob.name in bpy.context.view_layer.objects]
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

            bpy.ops.object.select_all(action='DESELECT')
            dup.select_set(True)
            zobj.select_set(False)
            bpy.context.view_layer.objects.active = dup
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.mesh.extrude_region_move()
            bpy.ops.transform.translate(value=(0, 0, 1))
            bpy.ops.object.mode_set(mode='OBJECT')
            dup.name = f"{zobj.name}_TRAIL_{i}"
            dup_name = dup.name
            intersect_trail_with_existing_box(dup, trail)
            if dup_name in bpy.data.objects:
                curveObjs.append(bpy.data.objects[dup_name])

    return curveObjs


def createTerrainFromSelected(manage_overlay=True, skip_bottom_recess=False):
    """Apply terrain elevation and overlays to already-placed tile objects.

    manage_overlay: when False the caller owns the ProgressOverlay lifecycle
    (start/finish).  All internal update/step calls still run normally so the
    caller's overlay reflects terrain progress.

    skip_bottom_recess: forwarded to _ctfs_apply_elevation -- see its
    docstring. Pass True for fresh single-tile callers with no neighbor
    baseline to protect (e.g. the puzzle generator).
    """
    from .primitives import setupColors  # deferred to avoid circular import at load time
    from .metadata import writeMetadata  # deferred to avoid circular import at load time
    from .mesh_ops import recalculateNormals  # deferred to avoid circular import at load time

    props = _ctfs_load_props()
    start_time = time.time()

    overlay = _progress.ProgressOverlay.get()
    if manage_overlay:
        overlay.start()
        _progress.WarningsOverlay.clear()

    print("------------------------------------------------")
    print("SCRIPT STARTED - createTerrainFromSelected")
    print("------------------------------------------------")


    if props['selfHosted'] != "" and props['selfHosted'] is not None and props['api'] == 1:
        print(f"!!using {props['selfHosted']} instead of Opentopodata!!")

    setupColors()

    overlay.update(0.02, "Initializing", "Validating selection…")

    selected_objects = bpy.context.selected_objects
    if not selected_objects:
        from .scene import show_message_box  # deferred to avoid circular import at load time
        show_message_box("No objects selected")
        if manage_overlay:
            overlay.finish()
        return {'FINISHED'}

    bpy.ops.object.select_all(action='DESELECT')

    lowestZ  = 0
    highestZ = 0
    additionalExtrusion = props['additionalExtrusion']


    _map_km = round(bpy.context.scene.tp3d.get("sMapInKm", 0), 1)
    _fetch_items = build_fetch_items(_map_km)
    overlay.set_fetch_items(_fetch_items)

    # Build multi-tile map preview (only when 2+ valid tiles)
    # Mirror the loop's own skip conditions exactly:
    #   - must be MESH
    #   - objType absent OR == "MAP"  (objects without objType are valid map tiles)
    #   - not already processed (highestZ and lowestZ both non-zero)
    _mp_valid = [
        obj for obj in selected_objects
        if obj.type == "MESH"
        and obj.get("objType", "MAP") == "MAP"
        and not (obj.get("highestZ", 0) != 0 and obj.get("lowestZ", 0) != 0)
    ]
    _mp_tiles_info = []
    _mp_tile_size = float(_mp_valid[0].get("objSize", 1.0)) if _mp_valid else 1.0
    if len(_mp_valid) >= 2:
        for obj in _mp_valid:
            _mp_tiles_info.append({
                'bx': round(float(obj.location.x), 3),
                'by': round(float(obj.location.y), 3),
                'status': 'pending',
                'shape': obj.get("Shape", "square").lower().split()[0],
            })
        overlay.set_map_preview({'tiles': _mp_tiles_info, 'tile_size': round(_mp_tile_size, 3)})

    n_tiles = len(selected_objects)
    for tile_idx, zobj in enumerate(selected_objects):
        tile_label = f"Tile {tile_idx + 1}/{n_tiles}"
        bpy.ops.object.select_all(action='DESELECT')
        bpy.context.scene.cursor.location = zobj.location

        if zobj.type != "MESH":
            continue
        if "objType" in zobj and zobj["objType"] != "MAP":
            continue
        if "highestZ" in zobj and "lowestZ" in zobj:
            if zobj["highestZ"] != 0 and zobj["lowestZ"] != 0:
                continue

        if _mp_tiles_info and _progress.SubprocessProgress.get().is_cancel_requested():
            break

        base_pct = tile_idx / n_tiles
        step = 1.0 / n_tiles

        # Update tile statuses in map preview
        if _mp_tiles_info and zobj in _mp_valid:
            _mp_idx = _mp_valid.index(zobj)
            for k in range(_mp_idx):
                _mp_tiles_info[k]['status'] = 'done'
            _mp_tiles_info[_mp_idx]['status'] = 'active'
            overlay.set_map_preview({'tiles': _mp_tiles_info, 'tile_size': round(_mp_tile_size, 3)})

        # Reset chip strip for this tile
        overlay.set_fetch_items(build_fetch_items(_map_km))

        # Create flat duplicate for trail boolean (normal mode only)
        duplicate = None
        if not props['singleColorMode']:
            pass
            #COMMENTED OUT FOR NOW, NOT SURE IF NEEDED CURRENTLY (MAYBE FOR SINGLE COLOR MODE)
            #duplicate = zobj.copy()
            #duplicate.data = zobj.data.copy()
            #bpy.context.collection.objects.link(duplicate)
            #for col in zobj.users_collection:
            #    if duplicate.name not in col.objects:
            #        col.objects.link(duplicate)
            #duplicate.name = "Bool"
            #duplicate.select_set(False)

        # Apply terrain elevation + extrude bottom face (0% → 50% of this tile)
        overlay.update(base_pct + step * 0.00, "Fetching Elevation", f"{tile_label} — querying elevation API…")
        overlay.set_fetch_progress('elevation', 0.0)

        def _elev_progress(pct):
            t = pct / 100.0
            overlay.update(base_pct + step * t * 0.50,
                           "Fetching Elevation",
                           f"{tile_label} — {pct}% complete…",
                           sub_percent=t,
                           sub_label="Elevation tiles")
            overlay.set_fetch_progress('elevation', t)

        props['additionalExtrusion'] = additionalExtrusion
        lowestZ, highestZ, additionalExtrusion, n_elev_pts = _ctfs_apply_elevation(
            zobj, props, progress_cb=_elev_progress, skip_bottom_recess=skip_bottom_recess
        )
        props['additionalExtrusion'] = additionalExtrusion
        overlay.sub_percent = None
        overlay.set_fetch_done('elevation', success=True)
        overlay.update(base_pct + step * 0.50, "Elevation Ready", f"{tile_label} — {n_elev_pts} pts, z {lowestZ:.1f}–{highestZ:.1f}")
        overlay.add_completed_step(f"{tile_label} — elevation fetched ({n_elev_pts} pts, z {lowestZ:.1f}–{highestZ:.1f})")

        # Handle trail projection / intersection
        overlay.update(base_pct + step * 0.60, "Building Trail", f"{tile_label} — projecting trail onto terrain…")
        print(f"duplicate: {duplicate}")
        curveObjs = _ctfs_handle_trail(zobj, duplicate, props['singleColorMode'])
        _n_trails = len(curveObjs)
        print(f"_n_trails: {_n_trails}")
        overlay.add_completed_step(
            f"{tile_label} — trail built ({_n_trails} seg{'s' if _n_trails != 1 else ''})"
            if _n_trails else f"{tile_label} — no trail"
        )

        # Base material
        mat = bpy.data.materials.get("BASE")
        zobj.data.materials.clear()
        zobj.data.materials.append(mat)

        # Terrain overlay elements (water, forest, city, glacier, buildings, roads)
        _elem_start = base_pct + step * 0.70
        _elem_end   = base_pct + step * 0.93
        overlay.update(_elem_start, "Terrain Elements", f"{tile_label} — building overlay layers…")
        terrain = _rg_build_terrain_elements(zobj, props['scaleHor'], phase_start=_elem_start, phase_end=_elem_end, tile_label=tile_label)
        if terrain['roads']:
            terrain['roads'].location.z += 0.4
        _found = [k for k, v in terrain.items() if v is not None]
        overlay.add_completed_step(
            f"{tile_label} — elements: {', '.join(_found)}" if _found else f"{tile_label} — no elements"
        )

        recalculateNormals(zobj)

        # Single color mode processing
        overlay.update(base_pct + step * 0.85, "Coloring", f"{tile_label} — applying single-color mode…")
        print(f"curveObjs: {curveObjs} ")
        _rg_apply_single_color_mode(zobj, curveObjs, terrain, props)

        # Finalize tile
        overlay.update(base_pct + step * 0.93, "Finalizing", f"{tile_label} — writing metadata…")
        writeMetadata(zobj)
        bpy.ops.object.select_all(action='DESELECT')
        zobj.select_set(False)
        zobj["lowestZ"] += additionalExtrusion
        zobj["highestZ"] += additionalExtrusion

        elementMode = (bpy.context.scene.tp3d.elementMode)
        exportformat = "STL"
        if elementMode == "PAINT":
            exportformat = "OBJ"

        _rg_assign_materials_and_export(
            zobj, curveObjs, None, None, props, False, start_time, exportformat
        )

        #utils.export_to_STL(zobj)

    # Mark all tiles done in the preview
    if _mp_tiles_info:
        for _info in _mp_tiles_info:
            _info['status'] = 'done'
        overlay.set_map_preview({'tiles': _mp_tiles_info, 'tile_size': round(_mp_tile_size, 3)})


    bpy.context.view_layer.objects.active = selected_objects[0]
    for zobj in selected_objects:
        zobj.select_set(True)

    end_time = time.time()
    duration = end_time - start_time

    bpy.context.scene.tp3d.lowestZ  = lowestZ
    bpy.context.scene.tp3d.highestZ = highestZ
    bpy.context.scene.tp3d["o_time"] = f"Script ran for {duration:.0f} seconds"

    from .elevation import load_generation_counter, save_generation_counter
    _total_maps = load_generation_counter() + 1
    save_generation_counter(_total_maps)
    bpy.context.scene.tp3d["o_mapsGenerated"] = f"Maps Generated: {_total_maps}"

    _elapsed = int(time.time() - overlay._start_time) if overlay._start_time else 0
    _m, _s = divmod(_elapsed, 60)
    overlay.add_completed_step(f"Done  —  {_m:02d}:{_s:02d} total")
    if manage_overlay:
        overlay.finish()
        _progress.WarningsOverlay.get().show()


def generateJustTrail(material="TRAIL"):
    from .scene import show_message_box  # deferred to avoid circular import at load time
    from .io_gpx import read_gpx_file  # deferred to avoid circular import at load time
    from .geo import convert_to_blender_coordinates, separate_duplicate_xy  # deferred to avoid circular import at load time
    from .primitives import simplify_curve, create_curve_from_coordinates  # deferred to avoid circular import at load time
    from .mesh_ops import RaycastCurveToAnyMesh  # deferred to avoid circular import at load time

    props = bpy.context.scene.tp3d

    minThickness = props.minThickness
    additionalExtrusion = props.sAdditionalExtrusion

    overwritePathElevation = props.overwritePathElevation

    coordinates = []
    separate_paths = []
    blender_coords = []
    blender_coords_separate = []
    type = 0

    bpy.ops.object.select_all(action='DESELECT')


    try:
        separate_paths = read_gpx_file()
    except Exception as e:
        show_message_box(f"Something went Wrong reading the GPX. Type {type}")
    coordinates = [item for sublist in separate_paths for item in sublist]


    #RECALCULATE THE COORDS WITH AUTOSCALE APPLIED
    blender_coords = [convert_to_blender_coordinates(lat, lon, ele,timestamp) for lat, lon, ele, timestamp in coordinates]

    if type == 1 or len(separate_paths) > 1:
        blender_coords_separate = [
            separate_duplicate_xy([convert_to_blender_coordinates(lat, lon, ele, timestamp) for lat, lon, ele, timestamp in path], 0.05)
            for path in separate_paths
            ]

    blender_coords = simplify_curve(blender_coords, .12)

    #PREVENT CLIPPING OF IDENTICAL COORDINATES
    blender_coords = separate_duplicate_xy(blender_coords, 0.05)


    print(len(separate_paths))


    if (type == 1 or len(separate_paths) > 1) and type != 4:
        blender_coords_separate = [
            separate_duplicate_xy([convert_to_blender_coordinates(lat, lon, ele, timestamp) for lat, lon, ele, timestamp in path], 0.05)
            for path in separate_paths
            ]

    curveObj = None
    try:
        if (type == 0 and len(blender_coords_separate) <= 1) and type != 2 or type == 4:
            if not blender_coords:
                return None
            create_curve_from_coordinates(blender_coords)
            curveObj = bpy.context.view_layer.objects.active
        elif (type == 1 or len(blender_coords_separate) > 1) and type != 4:
            for crds in blender_coords_separate:
                create_curve_from_coordinates(crds)

                bpy.ops.object.join()
                curveObj = bpy.context.view_layer.objects.active
    except Exception as e:
        show_message_box(e)


    if curveObj:
        bpy.context.view_layer.objects.active = curveObj
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.curve.select_all(action='SELECT')
        bpy.ops.transform.translate(value=(0, 0, -additionalExtrusion+minThickness))#bpy.ops.mesh.select_all(action='DESELECT')
        bpy.ops.object.mode_set(mode='OBJECT')



    #sets 3D cursor to origin of tile
    if curveObj:
        curveObj.select_set(True)
        bpy.ops.object.origin_set(type="ORIGIN_CURSOR")

    #Raycast the curve points onto the Mesh surface
    if overwritePathElevation == True:
        #pass
        RaycastCurveToAnyMesh(curveObj,1000,True)


    if curveObj:
        mat = bpy.data.materials.get(material)
        curveObj.data.materials.clear()
        curveObj.data.materials.append(mat)

    return curveObj
