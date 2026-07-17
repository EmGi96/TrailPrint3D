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
writing to tests/output/<scenario-name>/ (gitignored) so the actual
generated files can be inspected after a run. PAINT-mode scenarios export as
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
"""

import sys
import os
import math
import shutil
import traceback
from collections import Counter

import bpy         # type: ignore  — provided by Blender's Python
from mathutils import Vector  # type: ignore

# ---------------------------------------------------------------------------
# Path setup — makes TrailPrint3D importable as a package from source
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_RESOURCES_DIR = os.path.join(os.path.dirname(__file__), "Resources")
_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

if "TrailPrint3D" not in bpy.context.preferences.addons:
    bpy.ops.preferences.addon_enable(module="TrailPrint3D")

from TrailPrint3D.utils.generation import runGeneration  # noqa: E402


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
    except Exception:
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
            for corner in obj.bound_box:
                world_co = obj.matrix_world @ Vector(corner)
                for i in range(3):
                    stats["bbox_min"][i] = min(stats["bbox_min"][i], world_co[i])
                    stats["bbox_max"][i] = max(stats["bbox_max"][i], world_co[i])
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
        print(f"  exported files ({os.path.join('tests', 'output')}/...):")
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
    persistent tests/output/<name>/ folder so the actual generated files can
    be inspected afterwards.
    """
    _reset_scene_defaults()
    tp3d = bpy.context.scene.tp3d

    gpx_path = os.path.join(_RESOURCES_DIR, gpx_filename)
    tp3d.file_path = gpx_path

    out_dir = os.path.join(_OUTPUT_DIR, name)
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)
    tp3d.export_path = out_dir + os.sep  # export_to_STL concatenates path + filename directly

    for key, value in overrides.items():
        setattr(tp3d, key, value)

    before = set(bpy.data.objects)
    runGeneration(0)
    after = set(bpy.data.objects)
    new_objects = list(after - before)

    stats = _collect_stats(new_objects)
    stats["exported_files"] = sorted(os.listdir(out_dir)) if os.path.isdir(out_dir) else []
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


def test_circle_paint_forest_water():
    """3BergeTour hike, CIRCLE shape, PAINT mode, real forest + water."""
    stats = _run_generation_scenario(
        "circle_paint_forest_water",
        "3BergeTour.gpx",
        {"shape": "CIRCLE", "col_fActive": True, "col_wPondsActive": True},
    )
    _print_stats("circle / paint / real forest+water (3BergeTour)", stats)

    assert stats["object_count"] >= 2
    assert stats["faces_by_color"].get("BASE", 0) > 0
    assert stats["faces_by_color"].get("FOREST", 0) > 0
    assert stats["faces_by_color"].get("WATER", 0) > 0


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
    base_map = next(o for o in stats["objects"] if o["type"] == 'MESH' and o["name"] == "3BergeTour")
    assert base_map["faces"] > 0
    # Real OSM shapes are complex, multi-part polygons — a simple 4-8 vertex
    # box could never come from real forest/water data at this scale.
    forest_obj = next(o for o in stats["objects"] if o["name"] == "3BergeTour_FOREST")
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


def test_octagon_forest_water_long_route():
    """100KmTour road ride, OCTAGON shape, PAINT mode, real forest + water."""
    stats = _run_generation_scenario(
        "octagon_forest_water_long_route",
        "100KmTour.gpx",
        {"shape": "OCTAGON", "col_fActive": True, "col_wPondsActive": True},
    )
    _print_stats("octagon / paint / real forest+water (100KmTour)", stats)

    assert stats["object_count"] >= 2
    assert stats["faces_by_color"].get("FOREST", 0) > 0
    assert stats["faces_by_color"].get("WATER", 0) > 0


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


def test_hexagon_outer_text_paint_forest_water():
    """3BergeTour hike, HEXAGON OUTER TEXT shape, PAINT mode, resolution 8
    (num_subdivisions), real forest + water.

    This shape variant adds a separate text object (trail name/stats, WHITE
    material) and a separate backing plate object (BLACK material) on top of
    the usual map + trail, so it exercises the text/plate object-creation and
    material-assignment path together with real painted terrain elements.
    """
    stats = _run_generation_scenario(
        "hexagon_outer_text_paint_forest_water",
        "3BergeTour.gpx",
        {
            "shape": "HEXAGON",
            "shapeTextStyle": "OUTER TEXT",
            "num_subdivisions": 8,
            "col_fActive": True,
            "col_wPondsActive": True,
        },
    )
    _print_stats("hexagon outer text / paint / resolution=8 / real forest+water (3BergeTour)", stats)

    # Base map + trail + text + plate = 4 distinct objects.
    assert stats["object_count"] >= 4, \
        f"Expected map+trail+text+plate objects, got {stats['object_count']}"
    assert stats["faces_by_color"].get("BASE", 0) > 0
    assert stats["faces_by_color"].get("FOREST", 0) > 0, "Expected real FOREST-painted faces"
    assert stats["faces_by_color"].get("WATER", 0) > 0, "Expected real WATER-painted faces"
    assert stats["faces_by_color"].get("WHITE", 0) > 0, "Expected the WHITE text object"
    assert stats["faces_by_color"].get("BLACK", 0) > 0, "Expected the BLACK backing plate"
    assert any(f.endswith(".obj") for f in stats["exported_files"]), \
        f"PAINT mode should export .obj, got {stats['exported_files']}"


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  TrailPrint3D generation-pipeline integration tests (real data)")
    print("=" * 60 + "\n")

    _run("hexagon/paint + real forest+water (3BergeTour)",         test_hexagon_paint_forest_water)
    _run("circle/paint + real forest+water (3BergeTour)",          test_circle_paint_forest_water)
    _run("separate + real forest+water+city (3BergeTour)",         test_separate_mode_forest_water_city)
    _run("singlecolormode_remesh + real forest+water (3BergeTour)", test_singlecolormode_remesh_forest_water)
    _run("long route, exaggerated elevation + singlecolor + real elements", test_long_route_exaggerated_singlecolor_forest_water)
    _run("octagon + real forest+water (100KmTour)",                test_octagon_forest_water_long_route)
    _run("separate + real forest+water (100KmTour)",               test_separate_forest_water_long_route)
    _run("hexagon outer text + resolution 8 + real forest+water",  test_hexagon_outer_text_paint_forest_water)

    _assert_all_passed()
