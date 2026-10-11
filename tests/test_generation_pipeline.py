"""End-to-end integration tests for the full runGeneration() pipeline.

Drives the real map-generation pipeline (shape creation, elevation draping,
trail curve building, OSM terrain elements, material/coloring, booleans)
against real GPX fixtures with NOTHING mocked: every scenario hits the real
MapTerhorn elevation service and the real Overpass API.

This is intentional — the point of these tests is to exercise the boolean
logic (SEPARATE mode's boolean-intersect + split-loose, SINGLECOLORMODE_
REMESH's chained element-vs-element subtraction, singleColorMode's curve-vs-
terrain cutting) against genuinely complex, irregular real-world forest/
water polygon geometry, not simplified synthetic fixtures. A hand-built
4-vertex rectangle can't reveal a boolean-robustness bug the way a real
221-way, 27-part forest polygon can.

Shape / "Shape Extras" (shapeTextStyle) / Medal Handle (handleStyle)
coverage lives in test_model_shape_matrix.py instead (synthetic elevation,
no network, every shape/extra/handle combination) — every scenario here
stays on the default HEXAGON. test_terrain_offset is the one exception,
covering a non-shape parameter (xTerrainOffset/yTerrainOffset) as a paired
comparison against a zero-offset baseline.

Consequences of this:
  - Every run requires network access and will be considerably slower than a
    typical unit test (real Overpass/MapTerhorn round-trips + real boolean
    ops on real geometry).
  - The addon's normal on-disk caches (Overpass + MapTerhorn tiles) are left
    enabled (disableCache=False), so repeated runs during iteration are much
    faster after the first, and so the shared public Overpass instance isn't
    hammered on every run.
  - Assertions check sane invariants (object/vertex/face counts > 0, expected
    colors present, no crash through the booleans) rather than exact
    snapshots, since live OSM content can change over time.

Every scenario runs with real export enabled (disable_auto_export=False),
writing into a shared tests/output/GenerationTests/ folder (gitignored,
each scenario's own trailName keeps filenames from colliding) so the
actual generated files can be inspected after a run. PAINT-mode scenarios export as
.obj/.mtl (the only format that can carry the painted per-face terrain-
element colors); SEPARATE/SINGLECOLORMODE_REMESH scenarios export .stl per
object, since each object already carries exactly one material.

For each scenario this collects: object count, per-object vertex/face
counts, face count broken down by material ("color"), and the combined
world-space bounding box of every object the run created.

Run with:
  blender --background --factory-startup --python-exit-code 1 -P tests/test_generation_pipeline.py
  or
  & "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" --background --factory-startup --python-exit-code 1 -P tests/test_generation_pipeline.py

--python-exit-code 1 exits Blender with code 1 on any unhandled exception
(including AssertionError), making failures visible to CI.
"""  # noqa: W605

import math
import os
import sys
import traceback
from collections import Counter

import bpy  # type: ignore  — provided by Blender's Python
from mathutils import Vector  # type: ignore

# ---------------------------------------------------------------------------
# Path setup — makes TrailPrint3D importable as a package from source
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_RESOURCES_DIR = os.path.join(os.path.dirname(__file__), "Resources")
_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
_BUNDLE_DIR = os.path.join(_OUTPUT_DIR, "GenerationTests")

if "TrailPrint3D" not in bpy.context.preferences.addons:
    bpy.ops.preferences.addon_enable(module="TrailPrint3D")

from TrailPrint3D.utils.generation import runGeneration

# ---------------------------------------------------------------------------
# Minimal test runner (matches the pattern used by the other tests/*.py files)
# ---------------------------------------------------------------------------
_passed = 0
_failed = 0


def _run(name, fn):
    global _passed, _failed
    try:
        fn()
        print(f"  PASS  {name}")
        _passed += 1
    except Exception:  # noqa: BLE001 - wide exception needeed to keep test runner going
        print(f"  FAIL  {name}")
        traceback.print_exc()
        _failed += 1


def _assert_all_passed():
    print(f"\n{'='*60}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"{'='*60}\n")
    if _failed:
        raise SystemExit(1)   # non-zero → Blender exits with code 1


# ---------------------------------------------------------------------------
# Scene setup / teardown helpers
# ---------------------------------------------------------------------------

def _reset_scene_defaults():
    tp3d = bpy.context.scene.tp3d
    tp3d.shape = "HEXAGON"
    tp3d.shapeTextStyle = "NONE"
    tp3d.objSize = 100
    tp3d.num_subdivisions = 4
    tp3d.scaleElevation = 1.0
    tp3d.fixedElevationScale = False
    tp3d.singleColorMode = False
    tp3d.elementMode = "PAINT"
    tp3d.disableCache = False  # reuse the addon's real cache across runs
    tp3d.disable_auto_export = False
    tp3d.disable_3mf_export = True
    tp3d.trailName = ""
    tp3d.api = "MAPTERHORN"
    tp3d.col_fActive = False
    tp3d.col_wPondsActive = False
    tp3d.col_wSmallRiversActive = False
    tp3d.col_wBigRiversActive = False
    tp3d.col_cActive = False
    tp3d.col_scrActive = False
    tp3d.col_grActive = False
    tp3d.col_faActive = False
    tp3d.col_glActive = False
    tp3d.el_bActive = False
    tp3d.el_sBigActive = False
    tp3d.el_sMedActive = False
    tp3d.el_sSmallActive = False
    tp3d.el_oActive = False
    tp3d.ellipseRatio = 0.75
    tp3d.rectangleHeight = 100


def _cleanup_objects(objects):
    for obj in objects:
        try:
            data = obj.data
        except ReferenceError:
            continue
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except ReferenceError:
            pass
        if data is not None and data.users == 0:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data, bpy.types.Curve):
                bpy.data.curves.remove(data)


# ---------------------------------------------------------------------------
# Stats collection
# ---------------------------------------------------------------------------

def _collect_stats(objects):
    stats = {
        "object_count": len(objects),
        "objects": [],
        "total_vertices": 0,
        "total_mesh_faces": 0,
        "faces_by_color": Counter(),
        "bbox_min": [math.inf, math.inf, math.inf],
        "bbox_max": [-math.inf, -math.inf, -math.inf],
    }
    for obj in objects:
        entry = {"name": obj.name, "type": obj.type}
        if obj.type == 'MESH':
            mesh = obj.data
            mats = mesh.materials
            entry["vertices"] = len(mesh.vertices)
            entry["faces"] = len(mesh.polygons)
            stats["total_vertices"] += entry["vertices"]
            stats["total_mesh_faces"] += entry["faces"]
            for poly in mesh.polygons:
                idx = poly.material_index
                mat = mats[idx] if 0 <= idx < len(mats) else None
                stats["faces_by_color"][mat.name if mat else "(none)"] += 1
            entry_min = [math.inf, math.inf, math.inf]
            entry_max = [-math.inf, -math.inf, -math.inf]
            for corner in obj.bound_box:
                world_co = obj.matrix_world @ Vector(corner)
                for i in range(3):
                    entry_min[i] = min(entry_min[i], world_co[i])
                    entry_max[i] = max(entry_max[i], world_co[i])
                    stats["bbox_min"][i] = min(stats["bbox_min"][i], world_co[i])
                    stats["bbox_max"][i] = max(stats["bbox_max"][i], world_co[i])
            entry["bbox_min"] = tuple(entry_min)
            entry["bbox_max"] = tuple(entry_max)
        elif obj.type == 'CURVE':
            entry["points"] = sum(
                len(spl.points) + len(spl.bezier_points) for spl in obj.data.splines
            )
        stats["objects"].append(entry)
    if stats["object_count"] == 0 or math.isinf(stats["bbox_min"][0]):
        stats["bbox_min"] = [0.0, 0.0, 0.0]
        stats["bbox_max"] = [0.0, 0.0, 0.0]
    return stats


def _print_stats(name, stats):
    print(f"\n--- {name} ---")
    print(f"  objects created: {stats['object_count']}")
    for entry in stats["objects"]:
        if entry["type"] == 'MESH':
            print(f"    [MESH]  {entry['name']:35s} verts={entry['vertices']:6d} faces={entry['faces']:6d}")
        else:
            print(f"    [{entry['type']:5s}] {entry['name']:35s} points={entry.get('points', '?')}")
    print(f"  total mesh vertices: {stats['total_vertices']}")
    print(f"  total mesh faces:    {stats['total_mesh_faces']}")
    print("  faces by color:")
    for cname, cnt in sorted(stats["faces_by_color"].items(), key=lambda kv: -kv[1]):
        print(f"    {cname:15s}: {cnt}")
    bmin, bmax = stats["bbox_min"], stats["bbox_max"]
    dims = [bmax[i] - bmin[i] for i in range(3)]
    print(f"  bbox min:  ({bmin[0]:8.2f}, {bmin[1]:8.2f}, {bmin[2]:8.2f})")
    print(f"  bbox max:  ({bmax[0]:8.2f}, {bmax[1]:8.2f}, {bmax[2]:8.2f})")
    print(f"  bbox size: ({dims[0]:8.2f}, {dims[1]:8.2f}, {dims[2]:8.2f})")
    if stats.get("exported_files"):
        print(f"  exported files ({os.path.join('tests', 'output', 'GenerationTests')}/...):")
        for fname in stats["exported_files"]:
            print(f"    {fname}")


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------

def _run_generation_scenario(name, gpx_filename, overrides):
    """Configure the scene, run the real generation pipeline (real elevation,
    real OSM — nothing mocked), collect stats on every object the run
    created, clean the scene back up, and return the stats dict.

    Real export is left ON (disable_auto_export=False), writing into a
    shared tests/output/GenerationTests/ folder (every scenario's own
    trailName keeps filenames from colliding) so the actual generated files
    can be inspected afterwards.
    """
    _reset_scene_defaults()
    tp3d = bpy.context.scene.tp3d

    gpx_path = os.path.join(_RESOURCES_DIR, gpx_filename)
    tp3d.file_path = gpx_path

    tp3d.trailName = name  # -> modelname -> export filename, unique per scenario in the shared bundle folder
    tp3d.export_path = _BUNDLE_DIR + os.sep  # export_to_STL concatenates path + filename directly

    for key, value in overrides.items():
        setattr(tp3d, key, value)

    before = set(bpy.data.objects)
    runGeneration(0)
    after = set(bpy.data.objects)
    new_objects = list(after - before)

    stats = _collect_stats(new_objects)
    # Only this scenario's own files -- the folder is shared, so a plain
    # listdir() would also pick up every other scenario's output.
    stats["exported_files"] = sorted(
        f for f in os.listdir(_BUNDLE_DIR)
        if f == name or f.startswith(name + ".") or f.startswith(name + "_")
    ) if os.path.isdir(_BUNDLE_DIR) else []
    _cleanup_objects(new_objects)

    return stats


# ---------------------------------------------------------------------------
# Scenarios — every one hits real MapTerhorn elevation + real Overpass data
# ---------------------------------------------------------------------------

def test_hexagon_paint_forest_water():
    """3BergeTour hike, HEXAGON shape, PAINT mode, real forest + water."""
    stats = _run_generation_scenario(
        "hexagon_paint_forest_water",
        "3BergeTour.gpx",
        {"col_fActive": True, "col_wPondsActive": True},
    )
    _print_stats("hexagon / paint / real forest+water (3BergeTour)", stats)

    assert stats["object_count"] >= 2
    assert stats["total_vertices"] > 0
    assert stats["faces_by_color"].get("BASE", 0) > 0
    assert stats["faces_by_color"].get("FOREST", 0) > 0, "Expected real FOREST-painted faces"
    assert stats["faces_by_color"].get("WATER", 0) > 0, "Expected real WATER-painted faces"
    assert any(f.endswith(".obj") for f in stats["exported_files"]), \
        f"PAINT mode should export .obj, got {stats['exported_files']}"


def test_separate_mode_forest_water_city():
    """3BergeTour hike, SEPARATE element mode with real forest, water, and
    city — each becomes its own object via a real boolean-intersect with the
    terrain, followed by split-loose, against genuinely irregular OSM shapes."""
    stats = _run_generation_scenario(
        "separate_forest_water_city",
        "3BergeTour.gpx",
        {
            "elementMode": "SEPARATE",
            "col_fActive": True,
            "col_wPondsActive": True,
            "col_cActive": True,
        },
    )
    _print_stats("hexagon / separate / real forest+water+city (3BergeTour)", stats)

    # Base map + trail + forest + water + city = 5 distinct objects.
    assert stats["object_count"] >= 5, \
        f"Expected map+trail+3 terrain-element objects, got {stats['object_count']}"
    assert stats["faces_by_color"].get("FOREST", 0) > 0
    assert stats["faces_by_color"].get("WATER", 0) > 0
    assert stats["faces_by_color"].get("CITY", 0) > 0
    # In SEPARATE mode the base map itself should be untouched (all BASE).
    # Object names follow trailName (set to the scenario name so exports
    # don't collide in the shared bundle folder), not the GPX filename.
    base_map = next(o for o in stats["objects"] if o["type"] == 'MESH' and o["name"] == "separate_forest_water_city")
    assert base_map["faces"] > 0
    # Real OSM shapes are complex, multi-part polygons — a simple 4-8 vertex
    # box could never come from real forest/water data at this scale.
    forest_obj = next(o for o in stats["objects"] if o["name"] == "separate_forest_water_city_FOREST")
    assert forest_obj["vertices"] > 50, \
        f"Real forest geometry should be far more complex than a box, got {forest_obj['vertices']} verts"


def test_singlecolormode_remesh_forest_water():
    """3BergeTour hike, elementMode=SINGLECOLORMODE_REMESH with singleColorMode
    trail cutting also enabled — the most boolean-heavy code path: each
    terrain element gets remeshed, then has the trail groove AND every
    higher-priority element subtracted from it in sequence, against real
    forest+water geometry."""
    stats = _run_generation_scenario(
        "singlecolormode_remesh_forest_water",
        "3BergeTour.gpx",
        {
            "elementMode": "SINGLECOLORMODE_REMESH",
            "singleColorMode": True,
            "col_fActive": True,
            "col_wPondsActive": True,
        },
    )
    _print_stats("hexagon / singlecolormode_remesh / real forest+water (3BergeTour)", stats)

    assert stats["object_count"] >= 2
    assert stats["total_vertices"] > 0
    dims = [stats["bbox_max"][i] - stats["bbox_min"][i] for i in range(3)]
    assert dims[2] > 0, "Should still have vertical relief after chained booleans"


def test_long_route_exaggerated_singlecolor_forest_water():
    """100KmTour road ride, exaggerated elevation scale + single-color trail
    mode + real forest/water — stresses the curve-vs-terrain boolean cut on
    a much larger, longer real route."""
    stats = _run_generation_scenario(
        "long_route_exaggerated_singlecolor_forest_water",
        "100KmTour.gpx",
        {
            "scaleElevation": 3.0,
            "singleColorMode": True,
            "col_fActive": True,
            "col_wPondsActive": True,
        },
    )
    _print_stats("hexagon / paint / scaleElevation=3 / singleColorMode / real forest+water (100KmTour)", stats)

    assert stats["object_count"] >= 2
    assert stats["total_vertices"] > 0
    dims = [stats["bbox_max"][i] - stats["bbox_min"][i] for i in range(3)]
    assert dims[2] > 0, "Exaggerated elevation should still produce vertical relief"


def test_separate_forest_water_long_route():
    """100KmTour road ride, SEPARATE element mode, real forest + water — the
    boolean-intersect/split-loose path on a much larger real map."""
    stats = _run_generation_scenario(
        "separate_forest_water_long_route",
        "100KmTour.gpx",
        {"elementMode": "SEPARATE", "col_fActive": True, "col_wPondsActive": True},
    )
    _print_stats("hexagon / separate / real forest+water (100KmTour)", stats)

    assert stats["object_count"] >= 4, \
        f"Expected map+trail+forest+water objects, got {stats['object_count']}"
    assert stats["faces_by_color"].get("FOREST", 0) > 0
    assert stats["faces_by_color"].get("WATER", 0) > 0


# ---------------------------------------------------------------------------
# Non-shape parameter variations — shape/shapeTextStyle/handleStyle coverage
# itself now lives in test_model_shape_matrix.py, so these scenarios stay on
# the default HEXAGON and instead vary the other generation parameters, each
# as a paired comparison against a baseline/alternate value so the assertion
# checks a real, derivable effect rather than just "didn't crash".
# ---------------------------------------------------------------------------

def test_terrain_offset():
    """3BergeTour hike, xTerrainOffset/yTerrainOffset — paired against a
    zero-offset baseline. transform_MapObject() (generation.py,
    _rg_create_map_object) moves the base map shape to center+offset, so its
    own bounding-box center should shift by very close to the requested
    offset."""
    baseline = _run_generation_scenario("terrain_offset_baseline", "3BergeTour.gpx", {})
    shifted = _run_generation_scenario(
        "terrain_offset_shifted", "3BergeTour.gpx",
        {"xTerrainOffset": 20.0, "yTerrainOffset": -15.0},
    )
    _print_stats("terrain offset baseline (3BergeTour)", baseline)
    _print_stats("terrain offset xOff=20 yOff=-15 (3BergeTour)", shifted)

    base_map = next(o for o in baseline["objects"] if o["name"] == "terrain_offset_baseline")
    shifted_map = next(o for o in shifted["objects"] if o["name"] == "terrain_offset_shifted")

    base_center_x = (base_map["bbox_min"][0] + base_map["bbox_max"][0]) / 2
    base_center_y = (base_map["bbox_min"][1] + base_map["bbox_max"][1]) / 2
    shifted_center_x = (shifted_map["bbox_min"][0] + shifted_map["bbox_max"][0]) / 2
    shifted_center_y = (shifted_map["bbox_min"][1] + shifted_map["bbox_max"][1]) / 2

    assert abs((shifted_center_x - base_center_x) - 20.0) < 1.0, \
        f"Expected the base map to shift ~20mm in X, moved {shifted_center_x - base_center_x:.2f}mm"
    assert abs((shifted_center_y - base_center_y) - (-15.0)) < 1.0, \
        f"Expected the base map to shift ~-15mm in Y, moved {shifted_center_y - base_center_y:.2f}mm"


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  TrailPrint3D generation-pipeline integration tests (real data)")
    print("=" * 60 + "\n")

    os.makedirs(_BUNDLE_DIR, exist_ok=True)
    for _f in os.listdir(_BUNDLE_DIR):
        _fp = os.path.join(_BUNDLE_DIR, _f)
        if os.path.isfile(_fp):
            os.remove(_fp)

    _run("hexagon/paint + real forest+water (3BergeTour)",         test_hexagon_paint_forest_water)
    _run("separate + real forest+water+city (3BergeTour)",         test_separate_mode_forest_water_city)
    _run("singlecolormode_remesh + real forest+water (3BergeTour)", test_singlecolormode_remesh_forest_water)
    _run("long route, exaggerated elevation + singlecolor + real elements", test_long_route_exaggerated_singlecolor_forest_water)
    _run("separate + real forest+water (100KmTour)",               test_separate_forest_water_long_route)
    _run("xTerrainOffset/yTerrainOffset (3BergeTour)",              test_terrain_offset)

    _assert_all_passed()
