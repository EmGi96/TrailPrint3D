import math

import bpy  # type: ignore

from .. import constants as const
from .generation_snapshot import GenerationSnapshot


def writeMetadata(obj, type="MAP"):

    if obj is None:
        return

    from ..props import (
        any_road_active,  # deferred to avoid circular import at load time
        get_road_active,
        single_extruder_active,
    )
    from .osm.roads import TIER_TAGS  # deferred to avoid circular import at load time

    if type == "MAP":
        obj["Object type"] = type
        obj["Addon"] = const.ADDON_NAME
        obj["Version"] = const.ADDON_VERSION

        # get_effective_shape() reads the base-shape dropdown (HEXAGON/SQUARE/...),
        # which GeoJSON boundary tiles never go through -- their footprint comes
        # from the imported polygon instead, so that dropdown's leftover value
        # (from whatever shape was last picked elsewhere in the UI) would be
        # meaningless here.
        #
        # Callers that already know their own tile's shape (multitile grid
        # segments, the puzzle picker) stamp "Shape" on the object themselves
        # before terrain generation runs -- respect that instead of deriving
        # it from the scene's mapmode, which is the *main* Generate panel's
        # dropdown and has nothing to do with those flows. Overwriting it
        # unconditionally meant a stale mapmode == "GEOJSON" (left over from
        # trying the boundary-import feature) mislabeled every subsequently
        # generated square/hex multitile tile as "CUSTOM", which made Extend
        # mode treat them as having no regular neighbor grid at all.
        if "Shape" not in obj.keys():
            if bpy.context.scene.tp3d.mapmode == "GEOJSON":
                obj["Shape"] = "CUSTOM"
            else:
                obj["Shape"] = bpy.context.scene.tp3d.shape
        obj["Resolution"] = bpy.context.scene.tp3d.num_subdivisions
        obj["Elevation Scale"] = bpy.context.scene.tp3d.scaleElevation
        obj["objSize"] = bpy.context.scene.tp3d.objSize
        obj["pathThickness"] = round(bpy.context.scene.tp3d.pathThickness,2)
        obj["overwritePathElevation"] = bpy.context.scene.tp3d.overwritePathElevation
        obj["singleColorModeHeight"] = bpy.context.scene.tp3d.singleColorModeHeight
        obj["api"] = bpy.context.scene.tp3d.api
        obj["elevationMode"] = bpy.context.scene.tp3d.elevationMode
        obj["fixedHeightMM"] = bpy.context.scene.tp3d.fixedHeightMM
        obj["minThickness"] = bpy.context.scene.tp3d.minThickness
        obj["smoothTerrainTop"] = bpy.context.scene.tp3d.smoothTerrainTop
        obj["smoothTerrainStrength"] = bpy.context.scene.tp3d.smoothTerrainStrength
        obj["disableElevationOutlierFix"] = (
            bpy.context.scene.tp3d.disableElevationOutlierFix
        )
        obj["xTerrainOffset"] = bpy.context.scene.tp3d.xTerrainOffset
        obj["yTerrainOffset"] = bpy.context.scene.tp3d.yTerrainOffset
        obj["singleColorMode"] = bpy.context.scene.tp3d.singleColorMode
        obj["elementChoice"] = bpy.context.scene.tp3d.elementChoice
        obj["elementSource"] = bpy.context.scene.tp3d.elementSource
        obj["show_water"] = bpy.context.scene.tp3d.show_water
        obj["selfHosted"] = bpy.context.scene.tp3d.selfHosted
        obj["exportformat"] = bpy.context.scene.tp3d.exportformat
        obj["Horizontal Scale"] = round(bpy.context.scene.tp3d.sScaleHor,6)
        obj["Generate Water"] = any([bpy.context.scene.tp3d.col_wBodiesActive, bpy.context.scene.tp3d.col_wMinorActive, bpy.context.scene.tp3d.col_wMajorActive])
        obj["MinWaterSize"] = bpy.context.scene.tp3d.col_wArea
        obj["Keep Non-Manifold"] = bpy.context.scene.tp3d.col_KeepManifold
        obj["Map Size in Km"] = round(bpy.context.scene.tp3d.sMapInKm,2)
        obj["Dovetail"] = False
        obj["MagnetHoles"] = False
        obj["BottomMark"] = False
        obj["AdditionalExtrusion"] = bpy.context.scene.tp3d.sAdditionalExtrusion
        obj["lowestZ"] = bpy.context.scene.tp3d.lowestZ
        obj["highestZ"] = bpy.context.scene.tp3d.highestZ
        obj["dataset"] = bpy.context.scene.tp3d.dataset
        obj["name"] = bpy.context.scene.tp3d.name
        obj["pathScale"] = bpy.context.scene.tp3d.pathScale

        obj["shapeRotation"] = bpy.context.scene.tp3d.shapeRotation
        obj["pathVertices"] = bpy.context.scene.tp3d.o_verticesPath
        obj["mapVertices"] = bpy.context.scene.tp3d.o_verticesMap
        obj["mapScale"] = bpy.context.scene.tp3d.o_mapScale
        obj["centerx"] = bpy.context.scene.tp3d.o_centerx
        obj["centery"] = bpy.context.scene.tp3d.o_centery
        from .geo import (  # deferred to avoid circular import at load time
            convert_to_geo,
            map_scale_ratio,
        )
        _latitude, obj["longitude"] = convert_to_geo(bpy.context.scene.tp3d.o_centerx,bpy.context.scene.tp3d.o_centery)
        obj["latitude"] = _latitude
        _scale_elev = bpy.context.scene.tp3d.scaleElevation
        _auto_scale = bpy.context.scene.tp3d.sAutoScale
        # Vertex Z is displaced by scaleElevation * autoScale * merc (merc = the
        # per-vertex Mercator latitude factor applied in generation.py/geo.py's
        # elevation pass), so recovering real-world meters from the model's Z
        # range has to divide that same factor back out -- otherwise this value
        # is inflated by ~1/cos(latitude) away from the equator.
        _merc = 1 / math.cos(math.radians(_latitude))
        if _scale_elev != 0 and _auto_scale != 0 and _merc != 0:
            obj["Elevation Range (m)"] = round((bpy.context.scene.tp3d.highestZ - bpy.context.scene.tp3d.lowestZ) * 1000 / _scale_elev / _auto_scale / _merc, 1)
        else:
            obj["Elevation Range (m)"] = 0
        obj["Map Scale Ratio"] = map_scale_ratio()

        obj["col_wBodiesActive"] = bpy.context.scene.tp3d.col_wBodiesActive
        obj["col_wMinorActive"] = bpy.context.scene.tp3d.col_wMinorActive
        obj["col_wMajorActive"] = bpy.context.scene.tp3d.col_wMajorActive
        obj["col_wArea"] = bpy.context.scene.tp3d.col_wArea
        obj["col_wStreamWidth"] = bpy.context.scene.tp3d.col_wStreamWidth
        obj["col_wFlattenTop"] = bpy.context.scene.tp3d.col_wFlattenTop
        obj["col_wInsert"] = bpy.context.scene.tp3d.col_wInsert
        obj["col_fActive"] = bpy.context.scene.tp3d.col_fActive
        obj["col_fArea"] = bpy.context.scene.tp3d.col_fArea
        obj["col_cActive"] = bpy.context.scene.tp3d.col_cActive
        obj["col_cArea"] = bpy.context.scene.tp3d.col_cArea
        obj["col_glActive"] = bpy.context.scene.tp3d.col_glActive
        obj["col_glArea"] = bpy.context.scene.tp3d.col_glArea
        obj["col_scrActive"] = bpy.context.scene.tp3d.col_scrActive
        obj["col_scrArea"] = bpy.context.scene.tp3d.col_scrArea
        obj["col_faActive"] = bpy.context.scene.tp3d.col_faActive
        obj["col_faArea"] = bpy.context.scene.tp3d.col_faArea
        obj["col_grActive"] = bpy.context.scene.tp3d.col_grActive
        obj["col_grArea"] = bpy.context.scene.tp3d.col_grArea
        obj["col_lcForestActive"] = bpy.context.scene.tp3d.col_lcForestActive
        obj["col_lcGreenspaceActive"] = bpy.context.scene.tp3d.col_lcGreenspaceActive
        obj["col_lcFarmlandActive"] = bpy.context.scene.tp3d.col_lcFarmlandActive
        obj["col_lcCityActive"] = bpy.context.scene.tp3d.col_lcCityActive
        obj["col_lcWaterActive"] = bpy.context.scene.tp3d.col_lcWaterActive
        obj["col_lcMountainActive"] = bpy.context.scene.tp3d.col_lcMountainActive
        obj["col_lcGlacierActive"] = bpy.context.scene.tp3d.col_lcGlacierActive

        obj["elementModeInset"] = bpy.context.scene.tp3d.elementModeInset

        obj["el_bActive"] = bpy.context.scene.tp3d.el_bActive
        obj["el_sActive"] = any_road_active(bpy.context.scene.tp3d)
        obj["el_sMultiplier"] = bpy.context.scene.tp3d.el_sMultiplier
        obj["el_sEnforceMinWidth"] = bpy.context.scene.tp3d.el_sEnforceMinWidth
        obj["el_Smoothing"] = bpy.context.scene.tp3d.col_osmSmoothing
        obj["el_sHeight"] = bpy.context.scene.tp3d.el_sHeight
        obj["el_sCutTolerance"] = bpy.context.scene.tp3d.el_sCutTolerance
        obj["el_sCutDepth"] = bpy.context.scene.tp3d.el_sCutDepth
        for _tier in TIER_TAGS:
            obj[f"road_{_tier}_active"] = get_road_active(bpy.context.scene.tp3d, _tier)
        obj["el_oActive"] = bpy.context.scene.tp3d.el_oActive

        obj["elementMode"] = bpy.context.scene.tp3d.elementMode
        obj["tolerance"] = bpy.context.scene.tp3d.tolerance
        obj["toleranceElements"] = bpy.context.scene.tp3d.toleranceElements
        obj["elementShellThickness"] = bpy.context.scene.tp3d.elementShellThickness

        obj["ellipseRatio"] = bpy.context.scene.tp3d.ellipseRatio
        obj["rectangleHeight"] = bpy.context.scene.tp3d.rectangleHeight
        obj["indipendendTiles"] = bpy.context.scene.tp3d.indipendendTiles
        obj["tileSpacing"] = bpy.context.scene.tp3d.tileSpacing
        obj["shapeExtrasActive"] = bpy.context.scene.tp3d.shapeExtrasActive
        obj["plateMode"] = bpy.context.scene.tp3d.plateMode
        obj["plateThickness"] = bpy.context.scene.tp3d.plateThickness
        obj["plateBevel"] = bpy.context.scene.tp3d.plateBevel
        obj["plateInsertValue"] = bpy.context.scene.tp3d.plateInsertValue
        obj["textLayout"] = bpy.context.scene.tp3d.textLayout
        obj["textPlacement"] = bpy.context.scene.tp3d.textPlacement
        obj["textFont"] = bpy.context.scene.tp3d.textFont
        obj["textSize"] = bpy.context.scene.tp3d.textSize
        obj["text1"] = bpy.context.scene.tp3d.textfield1
        obj["text2"] = bpy.context.scene.tp3d.textfield2
        obj["text3"] = bpy.context.scene.tp3d.textfield3
        obj["textAngle"] = bpy.context.scene.tp3d.text_angle_preset
        obj["wallThickness"] = bpy.context.scene.tp3d.shellWallThickness
        obj["floorThickness"] = bpy.context.scene.tp3d.plateThickness

        obj["tex_use_texture"] = bpy.context.scene.tp3d.tex_use_texture
        obj["tex_include_roads"] = bpy.context.scene.tp3d.tex_include_roads
        obj["tex_include_trail"] = bpy.context.scene.tp3d.tex_include_trail
        obj["tex_resolution"] = bpy.context.scene.tp3d.tex_resolution

        obj["generation_mode"] = bpy.context.scene.tp3d.generation_mode
        obj["mapmode"] = bpy.context.scene.tp3d.mapmode
        obj["jMapLat"] = bpy.context.scene.tp3d.jMapLat
        obj["jMapLon"] = bpy.context.scene.tp3d.jMapLon
        obj["jMapRadius"] = bpy.context.scene.tp3d.jMapRadius
        obj["jMapLat1"] = bpy.context.scene.tp3d.jMapLat1
        obj["jMapLat2"] = bpy.context.scene.tp3d.jMapLat2
        obj["jMapLon1"] = bpy.context.scene.tp3d.jMapLon1
        obj["jMapLon2"] = bpy.context.scene.tp3d.jMapLon2

        obj["openTopographyDataset"] = bpy.context.scene.tp3d.openTopographyDataset
        obj["disableCache"] = bpy.context.scene.tp3d.disableCache
        obj["ccacheSize"] = bpy.context.scene.tp3d.ccacheSize
        obj["apiRetries"] = bpy.context.scene.tp3d.apiRetries

        obj["disable_auto_export"] = bpy.context.scene.tp3d.disable_auto_export
        obj["disable_3mf_export"] = bpy.context.scene.tp3d.disable_3mf_export

        obj["ExportGroup"] = 1

        '''
        About ExportGroups
        0 = Printed Separate without Group
        1 = Printed with Map
        2 = Printed with Plate
        '''



    if type =="TRAIL":
        obj["Object type"] = type
        obj["Addon"] = const.ADDON_NAME
        obj["Version"] = const.ADDON_VERSION
        obj["xTerrainOffset"] = bpy.context.scene.tp3d.xTerrainOffset
        obj["yTerrainOffset"] = bpy.context.scene.tp3d.yTerrainOffset
        obj["singleColorModeTrail"] = single_extruder_active(bpy.context.scene.tp3d)

        obj["overwritePathElevation"] = bpy.context.scene.tp3d.overwritePathElevation

        obj["ExportGroup"] = 0 if (bpy.context.scene.tp3d.singleColorMode or "SINGLECOLORMODE" in bpy.context.scene.tp3d.elementMode) else 1

    if type == "CITY" or type == "WATER" or type == "FOREST" or type == "GLACIER" or type == "FARMLAND" or type == "SCREE" or type == "GREENSPACE":
        obj["Object type"] = type
        obj["Addon"] = const.ADDON_NAME
        obj["Version"] = const.ADDON_VERSION
        obj["minThickness"] = bpy.context.scene.tp3d.minThickness
        obj["xTerrainOffset"] = bpy.context.scene.tp3d.xTerrainOffset
        obj["yTerrainOffset"] = bpy.context.scene.tp3d.yTerrainOffset
        obj["elementMode"] = bpy.context.scene.tp3d.elementMode

        obj["ExportGroup"] = 0 if "SINGLECOLORMODE" in bpy.context.scene.tp3d.elementMode else 1

    if type == "BUILDINGS" or type == "ROADS":

        obj["Object type"] = type
        obj["Addon"] = const.ADDON_NAME
        obj["Version"] = const.ADDON_VERSION
        obj["minThickness"] = bpy.context.scene.tp3d.minThickness
        obj["xTerrainOffset"] = bpy.context.scene.tp3d.xTerrainOffset
        obj["yTerrainOffset"] = bpy.context.scene.tp3d.yTerrainOffset
        obj["elementMode"] = bpy.context.scene.tp3d.elementMode

        # Roads is a real standalone base-to-top piece in Single-Color mode
        # (see finalize_roads/full_depth in generation.py) so it belongs in
        # its own print group, like the other coloring elements above.
        # Buildings always sit on top of both terrain and elements untouched,
        # so they always print with the map.
        if type == "ROADS":
            obj["ExportGroup"] = 0 if "SINGLECOLORMODE" in bpy.context.scene.tp3d.elementMode else 1
        else:
            obj["ExportGroup"] = 1

    if type == "PLATE":
        obj["Object type"] = type
        obj["Addon"] = const.ADDON_NAME
        obj["Version"] = const.ADDON_VERSION
        obj["Shape"] = bpy.context.scene.tp3d.shape
        obj["shapeExtrasActive"] = bpy.context.scene.tp3d.shapeExtrasActive
        obj["plateMode"] = bpy.context.scene.tp3d.plateMode
        obj["plateBevel"] = bpy.context.scene.tp3d.plateBevel
        obj["textLayout"] = bpy.context.scene.tp3d.textLayout
        obj["textPlacement"] = bpy.context.scene.tp3d.textPlacement
        obj["textFont"] = bpy.context.scene.tp3d.textFont
        obj["textSize"] = bpy.context.scene.tp3d.textSize
        obj["text1"] = bpy.context.scene.tp3d.textfield1
        obj["text2"] = bpy.context.scene.tp3d.textfield2
        obj["text3"] = bpy.context.scene.tp3d.textfield3
        obj["outerBorderSize"] = bpy.context.scene.tp3d.outerBorderSize
        obj["shapeRotation"] = bpy.context.scene.tp3d.shapeRotation
        obj["name"] = bpy.context.scene.tp3d.name
        obj["plateThickness"] = bpy.context.scene.tp3d.plateThickness
        obj["plateInsertValue"] = bpy.context.scene.tp3d.plateInsertValue
        obj["textAngle"] = bpy.context.scene.tp3d.text_angle_preset
        obj["objSize"] = bpy.context.scene.tp3d.objSize * ((100 + bpy.context.scene.tp3d.outerBorderSize)/100)
        obj["MagnetHoles"] = False
        obj["Dovetail"] = False
        obj["xTerrainOffset"] = bpy.context.scene.tp3d.xTerrainOffset
        obj["yTerrainOffset"] = bpy.context.scene.tp3d.yTerrainOffset

        obj["ExportGroup"] = 2 if bpy.context.scene.tp3d.plateInsertValue > 0 else 1

    if type == "TEXT":
        obj["Object type"] = type
        obj["Addon"] = const.ADDON_NAME
        obj["Version"] = const.ADDON_VERSION
        obj["Shape"] = bpy.context.scene.tp3d.shape
        obj["shapeExtrasActive"] = bpy.context.scene.tp3d.shapeExtrasActive
        obj["plateMode"] = bpy.context.scene.tp3d.plateMode
        obj["plateBevel"] = bpy.context.scene.tp3d.plateBevel
        obj["textLayout"] = bpy.context.scene.tp3d.textLayout
        obj["textPlacement"] = bpy.context.scene.tp3d.textPlacement
        obj["textFont"] = bpy.context.scene.tp3d.textFont
        obj["textSize"] = bpy.context.scene.tp3d.textSize
        obj["text1"] = bpy.context.scene.tp3d.textfield1
        obj["text2"] = bpy.context.scene.tp3d.textfield2
        obj["text3"] = bpy.context.scene.tp3d.textfield3
        obj["outerBorderSize"] = bpy.context.scene.tp3d.outerBorderSize
        obj["shapeRotation"] = bpy.context.scene.tp3d.shapeRotation
        obj["name"] = bpy.context.scene.tp3d.name
        obj["plateThickness"] = bpy.context.scene.tp3d.plateThickness
        obj["plateInsertValue"] = bpy.context.scene.tp3d.plateInsertValue
        obj["textAngle"] = bpy.context.scene.tp3d.text_angle_preset
        obj["objSize"] = bpy.context.scene.tp3d.objSize * ((100 + bpy.context.scene.tp3d.outerBorderSize)/100)
        obj["MagnetHoles"] = False
        obj["Dovetail"] = False
        obj["xTerrainOffset"] = bpy.context.scene.tp3d.xTerrainOffset
        obj["yTerrainOffset"] = bpy.context.scene.tp3d.yTerrainOffset

        obj["ExportGroup"] = 2 if bpy.context.scene.tp3d.plateInsertValue > 0 else 1

    if type == "SHELL":
        obj["Object type"] = type
        obj["Addon"] = const.ADDON_NAME
        obj["Version"] = const.ADDON_VERSION
        obj["Shape"] = bpy.context.scene.tp3d.shape
        obj["shapeExtrasActive"] = bpy.context.scene.tp3d.shapeExtrasActive
        obj["plateMode"] = bpy.context.scene.tp3d.plateMode
        obj["tolerance"] = bpy.context.scene.tp3d.tolerance
        obj["wallThickness"] = bpy.context.scene.tp3d.shellWallThickness
        obj["floorThickness"] = bpy.context.scene.tp3d.plateThickness
        obj["xTerrainOffset"] = bpy.context.scene.tp3d.xTerrainOffset
        obj["yTerrainOffset"] = bpy.context.scene.tp3d.yTerrainOffset
        obj["shapeRotation"] = bpy.context.scene.tp3d.shapeRotation
        obj["Dovetail"] = False
        obj["MagnetHoles"] = False

        obj["ExportGroup"] = 0  # Printed separate -- it wraps around the map, not stacked with it

    if type == "LINES":
        obj["Object type"] = type
        obj["cl_thickness"] = bpy.context.scene.tp3d.cl_thickness
        obj["cl_distance"] = bpy.context.scene.tp3d.cl_distance
        obj["cl_offset"] = bpy.context.scene.tp3d.cl_offset
        obj["xTerrainOffset"] = bpy.context.scene.tp3d.xTerrainOffset
        obj["yTerrainOffset"] = bpy.context.scene.tp3d.yTerrainOffset

        obj["ExportGroup"] = 1 #Print the lines with the Map

    if type == "PIN":
        obj["Object type"] = type

        obj["ExportGroup"] = 1

    if type == "OTHER":
        obj["Object type"] = type

        obj["ExportGroup"] = 1

    snapshot = GenerationSnapshot.from_mapping(obj)
    obj[const.GENERATION_SNAPSHOT_PROPERTY] = snapshot.to_json()


def next_multitile_index(objects) -> int:
    """First MultitileIndex not used by any of *objects* -- so a later batch
    (e.g. a Multi Tile Generator Extend) never shares an index with tiles
    already in the scene."""
    used = [obj["MultitileIndex"] for obj in objects if "MultitileIndex" in obj]
    return max(used) + 1 if used else 0


def set_multitile_index(objects, index: int) -> None:
    """Stamp *index* on every object of one multitile tile (the tile itself,
    its element meshes and trail pieces). Export groups objects by
    (MultitileIndex, ExportGroup), so each tile exports as its own group.
    Re-snapshots objects writeMetadata already ran on, so the Generation
    Settings popup shows the index too."""
    for obj in objects:
        try:
            obj["MultitileIndex"] = index
            if const.GENERATION_SNAPSHOT_PROPERTY in obj:
                obj[const.GENERATION_SNAPSHOT_PROPERTY] = (
                    GenerationSnapshot.from_mapping(obj).to_json()
                )
        except ReferenceError:
            pass
