import os
import platform
import time

import bpy  # type: ignore
import numpy as np  # type: ignore

from ... import addon_preferences
from ... import progress as _progress
from ..dataclasses import (
    ElevationSettings,
    FetchState,
    GenerationContext,
    JMapSettings,
    RunSettings,
    RuntimeState,
    TextureSettings,
    ValidationError,
)


def _rg_validate_inputs(flags, gen_type: int = 0, locked_scale: float | None = None):
    """Load all scene properties, and validate the inputs for the requested generation type.

    Returns a GenerationContext on success, or None if validation fails.
    """
    from bpy.types import Scene

    from ...props import (
        any_road_active,  # deferred to avoid circular import at load time
        get_effective_shape,
    )

    start_time = time.time()
    print("\n" * 30, end="")
    print(
        "------------------------------------------------",
        "SCRIPT STARTED",
        "------------------------------------------------",
        " ",
    )
    try:
        tp3d: Scene = bpy.context.scene.tp3d
        gpx_file_path: str = tp3d.file_path
        gpx_chain_path: str = tp3d.chain_path
        exportPath: str = tp3d.export_path
        shape: str = get_effective_shape(tp3d)
        name: str = tp3d.trailName
        size: int = tp3d.objSize
        autoExport: bool = tp3d.disable_auto_export
        scaleElevation: float = tp3d.scaleElevation
        scalemode: str = tp3d.scalemode
        scaleLon1: float = tp3d.scaleLon1
        scaleLat1: float = tp3d.scaleLat1
        scaleLon2: float = tp3d.scaleLon2
        scaleLat2: float = tp3d.scaleLat2
        shapeRotation: int = tp3d.shapeRotation
        overwritePathElev: bool = tp3d.overwritePathElevation
        api: str = tp3d.api
        selfHosted: str = tp3d.selfHosted
        elevationMode: str = tp3d.elevationMode
        fixedHeightMM: float = tp3d.fixedHeightMM
        minThickness: float = tp3d.minThickness
        xTerrainOffset: float = tp3d.xTerrainOffset
        yTerrainOffset: float = tp3d.yTerrainOffset
        singleColorMode: bool = tp3d.singleColorMode
        elementChoice: bool = tp3d.elementChoice
        elementMode: str = tp3d.elementMode
        elementSource: str = tp3d.elementSource
        disableCache: bool = tp3d.disableCache
        num_subdivisions: int = tp3d.num_subdivisions
        textFont: str = tp3d.textFont
        plateThickness: float = tp3d.plateThickness
        el_Smoothing: float = tp3d.col_osmSmoothing
        el_sActive: bool = any_road_active(tp3d)
        el_sHeight: float = tp3d.el_sHeight
        rectangleHeight: int = tp3d.rectangleHeight
        ellipseRatio: float = tp3d.ellipseRatio
        customFilePath: str = bpy.path.abspath(tp3d.customFilePath)
        tolerance: float = tp3d.tolerance
        shellWallThickness: float = tp3d.shellWallThickness
        plateInsertValue: float = tp3d.plateInsertValue
        pathThickness: float = tp3d.pathThickness
        el_sCutTolerance: float = tp3d.el_sCutTolerance
        el_sCutDepth: float = tp3d.el_sCutDepth
        smoothTerrainTop: bool = tp3d.smoothTerrainTop
        smoothTerrainStrength: int = tp3d.smoothTerrainStrength
        jMapLat: float = tp3d.jMapLat
        jMapLon: float = tp3d.jMapLon
        jMapRadius: float = tp3d.jMapRadius
        jMapLat1: float = tp3d.jMapLat1
        jMapLon1: float = tp3d.jMapLon1
        jMapLat2: float = tp3d.jMapLat2
        jMapLon2: float = tp3d.jMapLon2

        useTexture: bool = tp3d.tex_use_texture and elementMode == "PAINT"
        texResolution: int = tp3d.tex_resolution
        texRoads: bool = tp3d.tex_include_roads
        texTrail: bool = tp3d.tex_include_trail
        exportFormat: str = tp3d.exportformat
    except AttributeError as e:
        raise ValidationError(e)
    opentopoAdress: str = "https://api.opentopodata.org/v1/"
    if selfHosted != "" and selfHosted is not None and api == "OPENTOPODATA":
        opentopoAdress = selfHosted
        print(f"!!using {opentopoAdress} instead of Opentopodata!!")
    tp3d.opentopoAdress = opentopoAdress

    # --- Input validation ---
    from ...addon_preferences import get_prefs

    _ot_api_key = get_prefs().openTopographyApiKey

    if elementMode and el_sActive and el_sHeight == 0:
        raise ValidationError(
            "Road Height is 0 in Paint mode — this produces degenerate geometry. "
            "Set Road Height above 0 or disable roads."
        )

    if api == "OPENTOPOGRAPHY" and not _ot_api_key:
        print("No OPENTOPOGRAPHY API key entered")
        raise ValidationError(
            "OpenTopography requires an API key. "
            "Get a free key at portal.opentopography.org and set it in the addon preferences."
        )

    if "gpx_file" in flags:
        if not gpx_file_path or gpx_file_path == "":
            raise ValidationError("File path is empty! Please select a valid file.")
        if not os.path.isfile(gpx_file_path):
            raise ValidationError(
                f"Invalid file path: {gpx_file_path}. Please select a valid file."
            )
        gpx_file_path = bpy.path.abspath(gpx_file_path)
        file_extension = os.path.splitext(gpx_file_path)[1].lower()
        if file_extension != ".gpx" and file_extension != ".igc":
            raise ValidationError("Invalid file format. Please Use a .GPX file")
    if "gpx_chain" in flags:
        if not gpx_chain_path or gpx_chain_path == "":
            raise ValidationError("CHAIN path is empty! Please select a valid folder.")
        gpx_chain_path = bpy.path.abspath(gpx_chain_path)
    if not exportPath:
        exportPath = addon_preferences.get_prefs().default_export_folder
    if not exportPath:
        raise ValidationError("Export path cant be empty")
    exportPath = bpy.path.abspath(exportPath)
    if not exportPath or exportPath == "":
        raise ValidationError("Export path is empty! Please select a valid folder.")
    if not os.path.isdir(exportPath):
        raise ValidationError(
            f"Invalid export Directory: {exportPath}. Please select a valid Directory."
        )
    try:
        test_path = os.path.join(exportPath, ".tp3d_write_test")
        with open(test_path, "w") as f:
            f.write("")
        os.remove(test_path)
    except OSError:
        raise ValidationError(
            f"No write permission for export folder: {exportPath}. Please select a different folder."
        )

    # --- Default font ---
    if textFont == "":
        if platform.system() == "Windows":
            textFont = "C:/WINDOWS/FONTS/ariblk.ttf"
        elif platform.system() == "Darwin":
            textFont = "/System/Library/Fonts/Supplemental/Arial Black.ttf"

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

    modelname: str = name
    tp3d.modelname = modelname

    return GenerationContext(
        settings=RunSettings(
            flags=flags,
            gpx_chain_path=gpx_chain_path,
            exportFormat=exportFormat,
            shape=shape,
            modelname=modelname,
            size=size,
            autoExport=autoExport,
            scaleElevation=scaleElevation,
            scalemode=scalemode,
            scaleLon1=scaleLon1,
            scaleLat1=scaleLat1,
            scaleLon2=scaleLon2,
            scaleLat2=scaleLat2,
            shapeRotation=shapeRotation,
            overwritePathElevation=overwritePathElev,
            elevationMode=elevationMode,
            fixedHeightMM=fixedHeightMM,
            minThickness=minThickness,
            xTerrainOffset=xTerrainOffset,
            yTerrainOffset=yTerrainOffset,
            singleColorMode=singleColorMode,
            elementChoice=elementChoice,
            elementMode=elementMode,
            elementSource=elementSource,
            disableCache=disableCache,
            num_subdivisions=num_subdivisions,
            plateThickness=plateThickness,
            rectangleHeight=rectangleHeight,
            ellipseRatio=ellipseRatio,
            customFilePath=customFilePath,
            tolerance=tolerance,
            shellWallThickness=shellWallThickness,
            plateInsertValue=plateInsertValue,
            pathThickness=pathThickness,
            genType=gen_type,
            lockedScale=locked_scale,
            smoothTerrainTop=smoothTerrainTop,
            smoothTerrainStrength=smoothTerrainStrength,
        ),
        elevation=ElevationSettings(
            el_Smoothing=el_Smoothing,
            el_sHeight=el_sHeight,
            el_sCutTolerance=el_sCutTolerance,
            el_sCutDepth=el_sCutDepth,
        ),
        jmap=JMapSettings(
            jMapLat=jMapLat,
            jMapLon=jMapLon,
            jMapRadius=jMapRadius,
            jMapLat1=jMapLat1,
            jMapLon1=jMapLon1,
            jMapLat2=jMapLat2,
            jMapLon2=jMapLon2,
        ),
        texture=TextureSettings(
            useTexture=useTexture,
            texResolution=texResolution,
            texRoads=texRoads,
            texTrail=texTrail,
        ),
        fetch=FetchState(),
        runtime=RuntimeState(start_time=start_time),
    )


def _rg_load_coordinates(gen: GenerationContext):
    """Load GPX / synthetic coordinate data based on generation type.

    Returns (coordinates, separate_paths, coordinates2) or None on error.
    """
    from ..geo import move_coordinates  # deferred to avoid circular import at load time
    from ..io_gpx import (  # deferred to avoid circular import at load time
        read_gpx_directory,
        read_gpx_file,
    )
    from ..primitives import (
        setupColors,  # deferred to avoid circular import at load time
    )

    setupColors()

    if gen.settings.disableCache == 1:
        print("INFO: Cache Disabled (in Advanced Settings)")
    if not gen.settings.overwritePathElevation and not gen.settings.singleColorMode:
        print(
            "INFO: Overwrite Path Elevation disabled: Path Elevation wont be Adjusted to Map elevation"
        )
    if "gpx_file" in gen.settings.flags or (
        "gpx_chain" in gen.settings.flags and "append_collection" not in gen.settings.flags
    ):
        if gen.settings.xTerrainOffset > 0:
            print(
                f"INFO: Map will be moved in X by {gen.settings.xTerrainOffset} (Advanced Settings -> Map -> xTerrainOffset)"
            )
        if gen.settings.yTerrainOffset > 0:
            print(
                f"INFO: Map will be moved in Y by {gen.settings.yTerrainOffset} (Advanced Settings -> Map -> yTerrainOffset)"
            )

    if bpy.context.object and bpy.context.object.mode == "EDIT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.scene.tool_settings.use_mesh_automerge = False

    coordinates2 = []
    separate_paths = []
    separate_paths_by_file = []  # segments grouped by source file (gpx_chain only)
    try:
        if "gpx_file" in gen.settings.flags and "trail_map" not in gen.settings.flags:
            separate_paths = read_gpx_file()
        if "gpx_chain" in gen.settings.flags:
            separate_paths_by_file = read_gpx_directory(gen.settings.gpx_chain_path)
            separate_paths = [
                seg for file_segs in separate_paths_by_file for seg in file_segs
            ]
        if "jmap" in gen.settings.flags:
            nlat, nlon = move_coordinates(gen.jmap.jMapLat, gen.jmap.jMapLon, gen.jmap.jMapRadius, "e")
            separate_paths.append([(nlat, nlon, 0, 0)])
            nlat, nlon = move_coordinates(gen.jmap.jMapLat, gen.jmap.jMapLon, gen.jmap.jMapRadius, "s")
            separate_paths.append([(nlat, nlon, 0, 0)])
            nlat, nlon = move_coordinates(gen.jmap.jMapLat, gen.jmap.jMapLon, gen.jmap.jMapRadius, "w")
            separate_paths.append([(nlat, nlon, 0, 0)])
            nlat, nlon = move_coordinates(gen.jmap.jMapLat, gen.jmap.jMapLon, gen.jmap.jMapRadius, "n")
            separate_paths.append([(nlat, nlon, 0, 0)])
            if "trail_map" in gen.settings.flags:
                tempcoordinates = read_gpx_file()
                coordinates2 = [item for sublist in tempcoordinates for item in sublist]
        if "jmap_bbox" in gen.settings.flags:
            separate_paths.append([(gen.jmap.jMapLat1, gen.jmap.jMapLon1, 0, 0)])
            separate_paths.append([(gen.jmap.jMapLat2, gen.jmap.jMapLon2, 0, 0)])
    except Exception:  # noqa: BLE001 — GPX/IGC parsing can raise many unpredictable types
        # show_message_box(f"Something went Wrong reading the GPX. Type {type}")
        _progress.WarningsOverlay.add_warning(
            "Something went Wrong reading the GPX file", "error"
        )

    coordinates = [item for sublist in separate_paths for item in sublist]

    gen.runtime.pathCoordinates = coordinates
    gen.runtime.flatCoordinates = coordinates2
    gen.runtime.pathSegs = separate_paths
    gen.runtime.pathSegsByFile = separate_paths_by_file


def _rg_compute_trail_stats(gen: GenerationContext):
    """Calculate trail statistics and store them in scene properties."""
    from ..geo import (  # deferred to avoid circular import at load time
        calculate_date,
        calculate_total_elevation,
        calculate_total_length,
        calculate_total_time,
    )

    if "stats" not in gen.settings.flags:
        return

    stats = gen.runtime.gpx_stats
    stats.length = calculate_total_length(gen.runtime.pathCoordinates)
    stats.elevation = calculate_total_elevation(gen.runtime.pathCoordinates)
    stats.time = calculate_total_time(gen.runtime.pathCoordinates)
    stats.date = calculate_date(gen.runtime.pathCoordinates)

    if stats.time is not None and stats.time > 0:
        stats.avg_speed = stats.length / stats.time

        # TODO: Remove this block, blender context should not be the source of truth for stats, but rather the GPXStats object itself. This is a temporary measure to maintain compatibility with existing code that relies on scene properties.
        hours = int(stats.time)
        minutes = int((stats.time - hours) * 60)
        time_str = f"{hours}h {minutes}m"
        tp3d = bpy.context.scene.tp3d
        tp3d.sTime_str = time_str
        tp3d.total_length = stats.length
        tp3d.total_elevation = stats.elevation
        tp3d.total_time = stats.time
        tp3d.average_speed = stats.avg_speed
        tp3d.trail_date = stats.date


def _rg_interpolate_path_curve(gen: GenerationContext):
    if gen.runtime.pathCoordinates is None:
        return
    while (
        len(gen.runtime.pathCoordinates) < 300
        and len(gen.runtime.pathCoordinates) > 1
        and "trail" in gen.settings.flags
    ):
        n = len(gen.runtime.pathCoordinates)
        xyz = np.array(
            [(c[0], c[1], c[2]) for c in gen.runtime.pathCoordinates], dtype=np.float64
        )
        mids = (xyz[:-1] + xyz[1:]) / 2.0
        # Interleave originals and midpoints: [orig0, mid0, orig1, mid1, ..., origN]
        interleaved: list[tuple[float, float, float, float]] = []
        for i in range(n - 1):
            interleaved.append(gen.runtime.pathCoordinates[i])
            interleaved.append(
                (mids[i, 0], mids[i, 1], mids[i, 2], gen.runtime.pathCoordinates[i][3])
            )
        interleaved.append(gen.runtime.pathCoordinates[-1])
        gen.runtime.pathCoordinates = interleaved


def _rg_calculate_horizontal_scale(gen: GenerationContext):
    from ..geo import calculate_scale

    if gen.settings.lockedScale is not None:
        gen.runtime.sScaleHor = gen.settings.lockedScale
        bpy.context.scene.tp3d["sScaleHor"] = gen.settings.lockedScale
        return

    scalecoords = gen.runtime.pathCoordinates
    if gen.settings.scalemode == "COORDINATES" and "gpx_scale" in gen.settings.flags:
        scalecoords = (
            (gen.settings.scaleLon1, gen.settings.scaleLat1),
            (gen.settings.scaleLon2, gen.settings.scaleLat2),
        )
    scaleHor = calculate_scale(gen.settings.size, scalecoords, gen.settings.genType, diagonal=True)
    bpy.context.scene.tp3d["sScaleHor"] = scaleHor
    gen.runtime.sScaleHor = scaleHor


def _rg_convert_then_center_coordinates(gen: GenerationContext):
    from ..geo import convert_to_blender_coordinates_batch

    blender_coords = convert_to_blender_coordinates_batch(gen.runtime.pathCoordinates)
    if "separate_paths" in gen.settings.flags or len(gen.runtime.pathSegs or []) > 1:
        gen.runtime.blenderPathSegs = [
            convert_to_blender_coordinates_batch(path) for path in gen.runtime.pathSegs or []
        ]
    if gen.runtime.pathSegsByFile:
        gen.runtime.blenderPathSegsByFile = [
            [convert_to_blender_coordinates_batch(seg) for seg in file_segs]
            for file_segs in (gen.runtime.pathSegsByFile or [])
        ]
    min_x = min(p[0] for p in blender_coords)
    max_x = max(p[0] for p in blender_coords)
    min_y = min(p[1] for p in blender_coords)
    max_y = max(p[1] for p in blender_coords)
    centerx = (max_x - min_x) / 2 + min_x
    centery = (max_y - min_y) / 2 + min_y
    bpy.context.scene.tp3d["o_centerx"] = centerx
    bpy.context.scene.tp3d["o_centery"] = centery
    gen.runtime.centerX = centerx
    gen.runtime.centerY = centery


