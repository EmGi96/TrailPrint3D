"""Offline regressions for tile ocean clipping and trail/road finalization.

Run with Blender --background --factory-startup --python-exit-code 1
-P tests/test_tile_generation.py. Network inputs are replaced with polygons;
the mesh intersections, trail cuts, and road finalization use real Blender.
"""

import math
import os
import sys
import traceback
from types import SimpleNamespace
from unittest.mock import patch

import bpy

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
if "TrailPrint3D" not in bpy.context.preferences.addons:
    bpy.ops.preferences.addon_enable(module="TrailPrint3D")

from shapely.affinity import translate
from shapely.geometry import Polygon, box

from TrailPrint3D.utils import terrain
from TrailPrint3D.utils.dataclasses import (
    ElevationSettings,
    GenerationError,
    RuntimeState,
    TextureSettings,
)
from TrailPrint3D.utils.generation.elements import _rg_apply_single_color_mode
from TrailPrint3D.utils.generation.output import _rg_assign_materials
from TrailPrint3D.utils.osm.roads import (
    _build_extruded_mesh,
    _triangulated_terrain_faces,
    compute_full_depth_bottom_z,
)
from TrailPrint3D.utils.primitives import setupColors

_passed = 0
_failed = 0
_SETTINGS = {
    "elementMode": "SINGLECOLORMODE_REMESH",
    "minThickness": 2,
    "pathThickness": 1.2,
    "tolerance": 0.2,
    "singleColorModeHeight": 0.4,
    "col_osmSmoothing": 0,
}


def _run(name, fn):
    global _passed, _failed
    before = set(bpy.data.objects)
    cursor = bpy.context.scene.cursor.location.copy()
    tp3d = bpy.context.scene.tp3d
    settings_before = {key: getattr(tp3d, key) for key in _SETTINGS}
    try:
        fn()
        print(f"  PASS  {name}")
        _passed += 1
    except Exception:
        traceback.print_exc()
        print(f"  FAIL  {name}")
        _failed += 1
    finally:
        if bpy.context.object and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in set(bpy.data.objects) - before:
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if data is not None and data.users == 0:
                if isinstance(data, bpy.types.Mesh):
                    bpy.data.meshes.remove(data)
                elif isinstance(data, bpy.types.Curve):
                    bpy.data.curves.remove(data)
        bpy.context.scene.cursor.location = cursor
        for key, value in settings_before.items():
            setattr(tp3d, key, value)


def _tile_context():
    tp3d = bpy.context.scene.tp3d
    for key, value in _SETTINGS.items():
        setattr(tp3d, key, value)
    setupColors()
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=6, radius=125, depth=2.5, location=(-10781.4, 7164.16, 1.25)
    )
    tile = bpy.context.object
    tile.name = "_test_hex_tile"
    bpy.context.scene.cursor.location = (-10781.4, 7164.16, 0)
    outline = Polygon([
        (125 * math.cos(i * math.pi / 3), 125 * math.sin(i * math.pi / 3))
        for i in range(6)
    ])
    return SimpleNamespace(
        settings=SimpleNamespace(
            singleColorMode=True,
            elementMode="SINGLECOLORMODE_REMESH",
            size=250,
        ),
        elevation=ElevationSettings(el_Smoothing=0, el_sHeight=0.4),
        texture=TextureSettings(),
        runtime=RuntimeState(
            mapObject=tile, mapOutline=outline,
            tbMinLat=42.506, tbMaxLat=42.5485,
            tbMinLon=-70.909, tbMaxLon=-70.8424,
        ),
    )


def _ocean_for_tile(gen, polygon, dataset=False):
    if dataset:
        with patch(
            "TrailPrint3D.utils.osm.water_polygons.query_ocean_polygon",
            return_value=polygon,
        ):
            return terrain.createOceanFromWaterPolygons(gen, 1, gen.runtime.mapObject)
    with (
        patch("TrailPrint3D.utils.osm.gen.fetch_coastline_ways", return_value=[[(0, 0), (1, 1)]]),
        patch.object(terrain, "_smoothed_ocean_polys", return_value=[polygon]),
    ):
        return terrain.createOcean(gen, {}, 1, gen.runtime.mapObject)


def _corner_ocean(gen):
    loc = gen.runtime.mapObject.location
    return translate(box(100, 90, 124, 107), xoff=loc.x, yoff=loc.y)


def test_ocean_outside_hex_is_absent():
    for dataset in (False, True):
        gen = _tile_context()
        before = set(bpy.data.objects)
        ocean = _ocean_for_tile(gen, _corner_ocean(gen), dataset)
        assert ocean is None, "Ocean in the bbox corner does not overlap the hex tile"
        assert set(bpy.data.objects) == before, "Empty clipped ocean must be removed"


def test_ocean_overlapping_hex_is_preserved():
    for dataset in (False, True):
        gen = _tile_context()
        polygon = translate(box(-20, -20, 20, 20), xoff=-10781.4, yoff=7164.16)
        ocean = _ocean_for_tile(gen, polygon, dataset)
        assert ocean is not None
        assert len(ocean.data.vertices) > 0
        assert len(ocean.data.polygons) > 0
        assert ocean.data.materials[0].name == "WATER"


def _add_trail(gen):
    data = bpy.data.curves.new("_test_long_Trail", "CURVE")
    data.dimensions = "3D"
    spline = data.splines.new("POLY")
    spline.points.add(3556)
    for i, point in enumerate(spline.points):
        point.co = (-11000 + i * 0.125, 7199.16, 2.5, 1)
    trail = bpy.data.objects.new(data.name, data)
    bpy.context.collection.objects.link(trail)
    gen.runtime.curveObjs = [trail]
    return trail


def test_corner_ocean_does_not_abort_trail_and_roads():
    gen = _tile_context()
    tile = gen.runtime.mapObject
    ocean = _ocean_for_tile(gen, _corner_ocean(gen))
    water_polygon = translate(box(-35, -40, -10, -20), xoff=-10781.4, yoff=7164.16)
    water = _ocean_for_tile(gen, water_polygon)
    road_poly = translate(box(-80, -5, 80, 5), xoff=-10781.4, yoff=7164.16)
    verts = list(road_poly.exterior.coords)[:-1]
    roads = _build_extruded_mesh(verts, [(0, 1, 2), (0, 2, 3)], 1, 20)
    tris = _triangulated_terrain_faces(tile)
    gen.runtime.roadObj = roads
    gen.runtime.roadUnion = road_poly
    gen.runtime.elements = {
        "ocean": ocean,
        "water": water,
        "roads": roads,
        "_terrain_tris_cache": tris,
        "roads_bottom_z": compute_full_depth_bottom_z(tris, road_poly, 0.4),
    }
    trail = _add_trail(gen)
    _rg_apply_single_color_mode(gen)
    _rg_assign_materials(gen)
    assert roads.data.get("tp3d_roads_finalized"), "Coarse road cutter was not finalized"
    world_z = [(roads.matrix_world @ v.co).z for v in roads.data.vertices]
    assert abs(max(world_z) - 2.9) < 0.01, world_z
    assert abs(min(world_z) - 2.45) < 0.01, world_z
    assert trail.data.materials[0].name == "TRAIL"
    assert water.data.vertices and water.data.polygons
    assert water.data.materials[0].name == "WATER"
    assert not any("_thick" in obj.name for obj in bpy.data.objects)
    assert not any("_dup_for_projection" in obj.name for obj in bpy.data.objects)


def test_trail_cutters_removed_when_later_phase_fails():
    gen = _tile_context()
    _add_trail(gen)
    gen.runtime.elements = {}
    roads = gen.runtime.mapObject.copy()
    roads.data = roads.data.copy()
    roads.location.x += 1000
    bpy.context.collection.objects.link(roads)
    with patch(
        "TrailPrint3D.utils.osm.roads.finalize_roads",
        side_effect=RuntimeError("test finalization failure"),
    ):
        gen.runtime.roadObj = roads
        try:
            _rg_apply_single_color_mode(gen)
        except GenerationError as exc:
            assert "test finalization failure" in str(exc), exc
        else:
            raise AssertionError("Generation failure must propagate")
    assert not any("_thick" in obj.name for obj in bpy.data.objects)
    assert not any("_dup_for_projection" in obj.name for obj in bpy.data.objects)


def test_trail_cutters_removed_when_projection_fails():
    from TrailPrint3D.utils import mesh_ops

    gen = _tile_context()
    first = _add_trail(gen)
    second = _add_trail(gen)
    gen.runtime.curveObjs = [first, second]
    gen.runtime.elements = {}
    project_curve = mesh_ops.single_color_mode_curve

    def _project(curve, *args):
        if curve is second:
            raise RuntimeError("test projection failure")
        return project_curve(curve, *args)

    with patch.object(mesh_ops, "single_color_mode_curve", side_effect=_project):
        try:
            _rg_apply_single_color_mode(gen)
        except GenerationError as exc:
            assert "test projection failure" in str(exc), exc
        else:
            raise AssertionError("Projection failure must propagate")
    assert not any("_thick" in obj.name for obj in bpy.data.objects)
    assert not any("_dup_for_projection" in obj.name for obj in bpy.data.objects)


def test_empty_element_mesh_does_not_abort_booleans():
    gen = _tile_context()
    water_polygon = translate(box(-35, -40, -10, -20), xoff=-10781.4, yoff=7164.16)
    water = _ocean_for_tile(gen, water_polygon)
    empty = bpy.data.objects.new("_test_empty_ocean", bpy.data.meshes.new("_test_empty_ocean"))
    bpy.context.collection.objects.link(empty)
    gen.runtime.elements = {"ocean": empty, "water": water}
    trail = _add_trail(gen)
    _rg_apply_single_color_mode(gen)
    _rg_assign_materials(gen)
    assert "_test_empty_ocean" not in bpy.data.objects
    assert gen.runtime.elements.get("ocean") is None
    assert water.data.vertices and water.data.polygons
    assert trail.data.materials[0].name == "TRAIL"
    assert not any("_thick" in obj.name for obj in bpy.data.objects)


if __name__ == "__main__":
    _run("ocean outside hex is absent for both sources", test_ocean_outside_hex_is_absent)
    _run("ocean overlapping hex is preserved for both sources", test_ocean_overlapping_hex_is_preserved)
    _run("bbox-corner ocean does not abort trail and roads", test_corner_ocean_does_not_abort_trail_and_roads)
    _run("trail cutters are removed on later failure", test_trail_cutters_removed_when_later_phase_fails)
    _run("trail cutters are removed on projection failure", test_trail_cutters_removed_when_projection_fails)
    _run("empty element mesh does not abort booleans", test_empty_element_mesh_does_not_abort_booleans)
    print(f"\n{_passed} passed, {_failed} failed")
    if _failed:
        raise SystemExit(1)
