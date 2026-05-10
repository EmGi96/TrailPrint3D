import bpy  # type: ignore
import math
import time
import os
import platform
import random
from mathutils import Vector  # type: ignore
from bpy.app.translations import pgettext as _
from .. import progress as _progress
from .. import addon_preferences
from .. import constants as const


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


def _rg_build_terrain_elements(obj, scaleHor, curveObj=None, phase_start=0.83, phase_end=0.95):
    """Create water, forest, city, glacier, building and road overlay meshes.

    Reads all flags directly from bpy.context.scene.tp3d.
    Returns a dict keyed by element name; values may be None if disabled.
    phase_start/phase_end control the overlay progress range for multi-tile callers.
    """
    from .terrain import coloring_main, createOcean, _COLORING_EMPTY, _COLORING_PAINTED  # deferred to avoid circular import at load time
    from .osm import create_buildings, create_roads  # deferred to avoid circular import at load time
    from .scene import set_origin_to_3d_cursor, get_random_world_vertices  # deferred to avoid circular import at load time
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
            _ov.update(percent=pct, phase=phase_label, message=msg)
        _elem_idx[0] += 1

    _water_feat_active = (
        (tp3d.col_wPondsActive or tp3d.col_wSmallRiversActive or tp3d.col_wBigRiversActive)
        and map_km <= const.WATER_MAXSIZE
    )
    _ocean_active = tp3d.el_oActive == 1
    _water_ocean_combined = _water_feat_active and _ocean_active

    terrain = {}
    for key, flag_attr, max_size, phase, msg in COLORING_ELEMENTS:
        terrain[key] = None
        if (flag_attr(tp3d) if callable(flag_attr) else getattr(tp3d, flag_attr) == 1):
            if map_km <= max_size:
                _advance_elem_progress(phase, msg)
                _ov.set_fetch_progress(key, 0.0)
                _result = coloring_main(obj, key.upper())
                if _result is _COLORING_EMPTY:
                    terrain[key] = None
                    _ov.set_fetch_empty(key)
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
        minLat = tp3d.minLat
        minLon = tp3d.minLon
        maxLat = tp3d.maxLat
        maxLon = tp3d.maxLon
        if curveObj is not None:
            landpoints = get_random_world_vertices(curveObj, 200)
        else:
            print("No trail found, deriving land hints from terrain elevation")
            depsgraph = bpy.context.evaluated_depsgraph_get()
            obj_eval  = obj.evaluated_get(depsgraph)
            world_mat = obj_eval.matrix_world
            all_verts = [world_mat @ v.co for v in obj_eval.data.vertices]
            if all_verts:
                avg_z      = sum(v.z for v in all_verts) / len(all_verts)
                high_verts = [v for v in all_verts if v.z > avg_z]
                sample_pool = high_verts if high_verts else all_verts
                landpoints  = random.sample(sample_pool, min(200, len(sample_pool)))
            else:
                landpoints = []
        print("Create Ocean")
        ocean_pad_lat = (maxLat - minLat) * 0.10
        # Compute real geographic longitude span, handling antimeridian crossing
        lon_span = maxLon - minLon
        if lon_span < 0:  # crosses the antimeridian (e.g. NZ: minLon=160, maxLon=-160)
            lon_span += 360
        ocean_pad_lon = lon_span * 0.10
        bbox_west = max(-180.0, minLon - ocean_pad_lon)
        bbox_east = min(180.0, maxLon + ocean_pad_lon)
        terrain['ocean'] = createOcean(
            (minLat - ocean_pad_lat, bbox_west, maxLat + ocean_pad_lat, bbox_east),
            2, scaleHor, landpoints, obj, obj, tp3d.minThickness,
        )
        _ov.set_fetch_done('water', success=terrain['ocean'] is not None)

    print("Base elements Created")

    # --------------------------------------------------
    # Warn if buildings or roads are used together with any singleColorMode.
    # --------------------------------------------------
    _roads_active = any([tp3d.el_sBigActive, tp3d.el_sMedActive, tp3d.el_sSmallActive])
    _any_scm = tp3d.singleColorMode or "SINGLECOLORMODE" in tp3d.elementMode
    if (tp3d.el_bActive == 1 or _roads_active) and _any_scm:
        _progress.WarningsOverlay.add_warning("3D Elements (Buildings/Roads) are not compatible with SingleColorMode", "warn")

    # --------------------------------------------------
    # Buildings — own creation function + intersection post-processing.
    # --------------------------------------------------
    terrain['buildings'] = None
    if tp3d.el_bActive == 1:
        if map_km <= const.BUILDINGS_MAXSIZE:
            _advance_elem_progress("Buildings", "Fetching building data…")
            _ov.set_fetch_progress('buildings', 0.0)

            buildings = create_buildings(obj, 10, scaleHor)

            set_origin_to_3d_cursor(buildings)
            intersectWithTile(obj, buildings)
            buildings.name = obj.name + "_" + "Buildings"
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
            roads = create_roads(obj, 20, scaleHor, map_km)
            if roads is not None:
                roads = bpy.context.active_object
                roads.data.materials.clear()
                roads.data.materials.append(bpy.data.materials.get("BLACK"))
                terrain['roads'] = roads
                roads.name = obj.name + "_" + "Roads"
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
    from .mesh_ops import single_color_mode_curve, single_color_mode_mesh_wireframe, single_color_mode_mesh_remesh, boolean_operation, selectBottomFaces, recalculateNormals  # deferred to avoid circular import at load time
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
            for tcrv in curveObjs:
                thickerCurves.append(single_color_mode_curve(tcrv, obj, True, dpt, dup))
            remove_objects(dup)
            for i in range(len(curveObjs) - 1):
                recalculateNormals(curveObjs[i + 1])
                recalculateNormals(thickerCurves[i])
                thickerCurves[i].scale = (1.01, 1.01, 1.01)
                boolean_operation(curveObjs[i + 1], thickerCurves[i])


    if props['elementMode'] == "SEPARATE":
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

    if props['elementMode'] in ("SINGLECOLORMODE", "SINGLECOLORMODE_REMESH") or 1 == 0:

        _ov = _progress.ProgressOverlay.get()
        if _ov.active:
            _ov.update(message=f"Applying Single-color Mode…")
        # Maps key -> thicker mesh object, filled as each element is processed.
        thicker_by_key = {}

        _scm_fn = single_color_mode_mesh_remesh if props['elementMode'] == "SINGLECOLORMODE_REMESH" else single_color_mode_mesh_wireframe


        for i, key in enumerate(TERRAIN_PRIORITY_ORDER):
            elem_obj = terrain.get(key)
            if not elem_obj:
                continue
            _ov = _progress.ProgressOverlay.get()
            if _ov.active:
                _ov.update(message=f"Merging {key.capitalize()}…")

            thicker = _scm_fn(elem_obj, obj)
            thicker_by_key[key] = thicker

            if _ov.active:
                _ov.update(message=f"subtract other layers from {elem_obj.name}…")

            # Subtract all curve thicker-bodies
            for tcrv in thickerCurves:
                boolean_operation(elem_obj, tcrv)

            # Subtract every higher-priority element that was already processed
            for prev_key in TERRAIN_PRIORITY_ORDER[:i]:
                if prev_key in thicker_by_key:
                    boolean_operation(elem_obj, thicker_by_key[prev_key])
        for thicker in thicker_by_key.values():
            #pass
            remove_objects(thicker)

    if thickerCurves:
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
            if tcrv and tcrv.name in bpy.data.objects:
                writeMetadata(tcrv, "TRAIL")

    # Assign alternating TRAIL/YELLOW materials to trail curve segments
    if curveObjs:
        mats = "TRAIL"
        for tcrv in curveObjs:
            if tcrv and tcrv.name in bpy.data.objects:
                mat = bpy.data.materials.get(mats)
                tcrv.data.materials.clear()
                tcrv.data.materials.append(mat)
                mats = "YELLOW" if mats == "TRAIL" else "TRAIL"

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
                if tcrv and tcrv.name in bpy.data.objects:
                    tcrv.select_set(True)
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
    from .geo import calculate_scale, convert_to_blender_coordinates, convert_to_geo, haversine, separate_duplicate_xy, midpoint_spherical  # deferred to avoid circular import at load time
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
        i = 0
        while i < len(coordinates) - 1:
            p1 = coordinates[i]
            p2 = coordinates[i + 1]
            midpoint = (
                (p1[0] + p2[0]) / 2,
                (p1[1] + p2[1]) / 2,
                (p1[2] + p2[2]) / 2,
                p1[3],
            )
            coordinates.insert(i + 1, midpoint)
            i += 2

    # --- Phase 5: Calculate horizontal scale factor ---
    overlay.update(0.20, "Scale Calculation", "Computing horizontal scale…")
    scalecoords = coordinates
    if props['scalemode'] == "COORDINATES" and "gpx_scale" in flags:
        scalecoords = ((props['scaleLon1'], props['scaleLat1']), (props['scaleLon2'], props['scaleLat2']))
    scaleHor = locked_scale if locked_scale is not None else calculate_scale(props['size'], scalecoords, type)
    bpy.context.scene.tp3d["sScaleHor"] = scaleHor

    # --- Phase 6: Convert to Blender coordinates and find map center ---
    overlay.update(0.24, "Coordinate Conversion", "Converting to Blender space…")
    blender_coords = [
        convert_to_blender_coordinates(lat, lon, ele, timestamp)
        for lat, lon, ele, timestamp in coordinates
    ]
    blender_coords_separate = []
    if "separate_paths" in flags or len(separate_paths) > 1:
        blender_coords_separate = [
            [convert_to_blender_coordinates(lat, lon, ele, timestamp) for lat, lon, ele, timestamp in path]
            for path in separate_paths
        ]
    blender_coords_by_file = []
    if separate_paths_by_file:
        blender_coords_by_file = [
            [[convert_to_blender_coordinates(lat, lon, ele, timestamp) for lat, lon, ele, timestamp in seg]
             for seg in file_segs]
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

    _map_km = round(bpy.context.scene.tp3d.get("sMapInKm", 0), 1)
    overlay.add_completed_step(f"Map shape created  ({props['shape'].capitalize()}, {_map_km} km)")

    # Build the fetch-item strip
    overlay.set_fetch_items(build_fetch_items(_map_km))

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
    blender_coords = [
        convert_to_blender_coordinates(lat, lon, ele, timestamp)
        for lat, lon, ele, timestamp in coordinates
    ]
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
    blender_coords = separate_duplicate_xy(blender_coords, 0.05)
    if ("separate_paths" in flags or len(separate_paths) > 1) and "trail_map" not in flags:
        blender_coords_separate = [
            [convert_to_blender_coordinates(lat, lon, ele, timestamp) for lat, lon, ele, timestamp in path]
            for path in separate_paths
        ]
    if separate_paths_by_file and "trail_map" not in flags:
        blender_coords_by_file = [
            [[convert_to_blender_coordinates(lat, lon, ele, timestamp) for lat, lon, ele, timestamp in seg]
             for seg in file_segs]
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
    lowestZ  = 1000
    highestZ = 0
    _total_verts = len(mesh.vertices)
    _obj_matrix = MapObject.matrix_world
    for i, vert in enumerate(mesh.vertices):
        _world_co = _obj_matrix @ vert.co
        _vert_lat, _unused_var = convert_to_geo(_world_co.x, _world_co.y)
        _merc = 1 / math.cos(math.radians(_vert_lat))
        vert.co.z = tileVerts[i] / 1000 * props['scaleElevation'] * autoScale * _merc
        lowestZ  = min(lowestZ,  vert.co.z)
        highestZ = max(highestZ, vert.co.z)
        if i % 5000 == 0:
            overlay.update(i / _total_verts, "Displacing vertices…")
    overlay.update(_total_verts / _total_verts, "Displacing vertices…")
    overlay.sub_percent = None
    additionalExtrusion = lowestZ
    bpy.context.scene.tp3d.sAdditionalExtrusion = additionalExtrusion
    bpy.context.scene.tp3d.lowestZ  = lowestZ
    bpy.context.scene.tp3d.highestZ = highestZ

    print(f"additionalExtrusion: {additionalExtrusion}")
    print(f"Lowest z: {lowestZ}")
    print(f"Highest z: {highestZ}")
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
        print("Chicken")
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
    elements = _rg_build_terrain_elements(obj, scaleHor, curveObj=curveObjs[0] if curveObjs else None)

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
    bpy.context.scene.tp3d["o_time"] = _("Script ran for {} seconds").format(round(duration))

    print(f"Finished. Generating Map took {duration:.0f} seconds")
    print("----------------------------------------------------------------")
    print(f"")


    _elapsed = int(time.time() - overlay._start_time) if overlay._start_time else 0
    _m, _s = divmod(_elapsed, 60)
    overlay.update(1.0, "Done", "")
    overlay.add_completed_step(f"Done  —  {_m:02d}:{_s:02d} total")
    overlay.finish()
    _progress.WarningsOverlay.get().show()
