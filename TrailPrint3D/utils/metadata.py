import bpy  # type: ignore
from .. import constants as const


def writeMetadata(obj, type = "MAP"):

    if obj is None:
        return

    if type == "MAP":
        obj["Object type"] = type
        obj["Addon"] = const.ADDON_NAME
        obj["Version"] = const.ADDON_VERSION

        obj["Generation Duration"] = str( bpy.context.scene.tp3d.sRunDuration) + " seconds"
        obj["Shape"] = bpy.context.scene.tp3d.shape
        obj["Resolution"] = bpy.context.scene.tp3d.num_subdivisions
        obj["Elevation Scale"] = bpy.context.scene.tp3d.scaleElevation
        obj["objSize"] = bpy.context.scene.tp3d.objSize
        obj["pathThickness"] = round(bpy.context.scene.tp3d.pathThickness,2)
        obj["overwritePathElevation"] = bpy.context.scene.tp3d.overwritePathElevation
        obj["api"] = bpy.context.scene.tp3d.api
        obj["scalemode"] = bpy.context.scene.tp3d.scalemode
        obj["fixedElevationScale"] = bpy.context.scene.tp3d.fixedElevationScale
        obj["minThickness"] = bpy.context.scene.tp3d.minThickness
        obj["xTerrainOffset"] = bpy.context.scene.tp3d.xTerrainOffset
        obj["yTerrainOffset"] = bpy.context.scene.tp3d.yTerrainOffset
        obj["singleColorMode"] = bpy.context.scene.tp3d.singleColorMode
        obj["selfHosted"] = bpy.context.scene.tp3d.selfHosted
        obj["Horizontal Scale"] = round(bpy.context.scene.tp3d.sScaleHor,6)
        obj["Generate Water"] = any([bpy.context.scene.tp3d.col_wPondsActive, bpy.context.scene.tp3d.col_wSmallRiversActive, bpy.context.scene.tp3d.col_wBigRiversActive])
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
        obj["scaleLon1"] = bpy.context.scene.tp3d.scaleLon1
        obj["scaleLat1"] = bpy.context.scene.tp3d.scaleLat1
        obj["scaleLon2"] = bpy.context.scene.tp3d.scaleLon2
        obj["scaleLat2"] = bpy.context.scene.tp3d.scaleLat2

        obj["shapeRotation"] = bpy.context.scene.tp3d.shapeRotation
        obj["pathVertices"] = bpy.context.scene.tp3d.o_verticesPath
        obj["mapVertices"] = bpy.context.scene.tp3d.o_verticesMap
        obj["mapScale"] = bpy.context.scene.tp3d.o_mapScale
        obj["centerx"] = bpy.context.scene.tp3d.o_centerx
        obj["centery"] = bpy.context.scene.tp3d.o_centery
        from .geo import convert_to_geo  # deferred to avoid circular import at load time
        obj["latitude"], obj["longitude"] = convert_to_geo(bpy.context.scene.tp3d.o_centerx,bpy.context.scene.tp3d.o_centery)
        _scale_elev = bpy.context.scene.tp3d.scaleElevation
        _auto_scale = bpy.context.scene.tp3d.sAutoScale
        if _scale_elev != 0 and _auto_scale != 0:
            obj["Elevation Range (m)"] = round((bpy.context.scene.tp3d.highestZ - bpy.context.scene.tp3d.lowestZ) * 1000 / _scale_elev / _auto_scale, 1)
        else:
            obj["Elevation Range (m)"] = 0
        obj["sMapInKm"] = bpy.context.scene.tp3d.sMapInKm

        obj["col_wPondsActive"] = bpy.context.scene.tp3d.col_wPondsActive
        obj["col_wSmallRiversActive"] = bpy.context.scene.tp3d.col_wSmallRiversActive
        obj["col_wBigRiversActive"] = bpy.context.scene.tp3d.col_wBigRiversActive
        obj["col_wArea"] = bpy.context.scene.tp3d.col_wArea
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

        obj["el_bActive"] = bpy.context.scene.tp3d.el_bActive
        obj["el_sActive"] = any([bpy.context.scene.tp3d.el_sBigActive, bpy.context.scene.tp3d.el_sMedActive, bpy.context.scene.tp3d.el_sSmallActive])
        obj["el_sMultiplier"] = bpy.context.scene.tp3d.el_sMultiplier
        obj["el_sBigActive"] = bpy.context.scene.tp3d.el_sBigActive
        obj["el_sMedActive"] = bpy.context.scene.tp3d.el_sMedActive
        obj["el_sSmallActive"] = bpy.context.scene.tp3d.el_sSmallActive
        obj["el_oActive"] = bpy.context.scene.tp3d.el_oActive
        obj["el_oFlip"] = bpy.context.scene.tp3d.el_oFlip

        obj["elementMode"] = bpy.context.scene.tp3d.elementMode
        obj["tolerance"] = bpy.context.scene.tp3d.tolerance
        obj["toleranceElements"] = bpy.context.scene.tp3d.toleranceElements

        obj["ellipseRatio"] = bpy.context.scene.tp3d.ellipseRatio
        obj["rectangleHeight"] = bpy.context.scene.tp3d.rectangleHeight
        obj["indipendendTiles"] = bpy.context.scene.tp3d.indipendendTiles
        obj["tileSpacing"] = bpy.context.scene.tp3d.tileSpacing

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
        obj["singleColorModeTrail"] = bpy.context.scene.tp3d.singleColorMode

        obj["overwritePathElevation"] = bpy.context.scene.tp3d.overwritePathElevation

        obj["ExportGroup"] = 0 if bpy.context.scene.tp3d.singleColorMode else 1

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

        obj["ExportGroup"] = 1

    if type == "PLATE":
        obj["Object type"] = type
        obj["Addon"] = const.ADDON_NAME
        obj["Version"] = const.ADDON_VERSION
        obj["Shape"] = bpy.context.scene.tp3d.shape
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
