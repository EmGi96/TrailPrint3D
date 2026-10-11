"""Coverage matrix: every shape / Shape-Extras / Medal-Handle combination.

Generates the same 3BergeTour hike -- resolution=3 (num_subdivisions), no
terrain elements (every col_*/el_* flag off) -- once for every shape +
"Shape Extras" (shapeTextStyle) combination the UI actually exposes (see
props.SHAPE_TEXT_STYLES), and additionally for every Medal Handle
(handleStyle) style on the four shape/extra combos that expose that control
(HEXAGON OUTER TEXT, HEXAGON FRONT TEXT, OCTAGON OUTER TEXT, CIRCLE OUTER
TEXT -- see panels.py's `effective_shape in {...}` gate around
"handleStyle"). handleStyle has zero effect anywhere else (add_medal_handle
is only ever called from those four shape functions), so cross-multiplying
it against every combo would just re-generate identical geometry under a
different name -- it's only varied where it actually changes the output.

Each scenario gets a unique trailName (-> modelname -> export filename) and
real export is left on (disable_auto_export=False, disable_3mf_export=
False), so every scenario is exported as .3mf via the real 3MF Blender
extension if it's installed in the running Blender -- falling back to the
addon's own STL/OBJ export path otherwise (mirrors _rg_export's own
fallback in generation.py, so this test behaves the same on a machine
without the 3MF extension installed).

Unlike test_generation_pipeline.py, this matrix is only about the plate/
shape/handle geometry, not terrain-element or elevation-data correctness --
so elevation.get_tile_elevation() is monkeypatched to a synthetic heightfield
(a smooth single hill, no network) for the duration of this file's run
instead of hitting the real MapTerhorn service. geographic-bounds bookkeeping
(compute_and_store_tile_bounds) still runs for real -- it's pure coordinate
math, no network -- so scale/GPX handling is exercised normally. The stub is
restored after the run so it can't leak into any test file exec'd after this
one by run_all_tests.py.

Run with:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --factory-startup --python-exit-code 1 -P tests/test_model_shape_matrix.py
"""

import math
import os
import sys
import traceback

import bpy  # type: ignore  — provided by Blender's Python

# ---------------------------------------------------------------------------
# Path setup — makes TrailPrint3D importable as a package from source
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_RESOURCES_DIR = os.path.join(os.path.dirname(__file__), "Resources")
_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output", "shape_matrix")
_GPX_FILE = "3BergeTour.gpx"

if "TrailPrint3D" not in bpy.context.preferences.addons:
    bpy.ops.preferences.addon_enable(module="TrailPrint3D")

# --factory-startup ignores userpref.blend, so an installed-but-not-yet-
# session-enabled 3MF extension needs enabling here too (mirrors the
# TrailPrint3D enable above) — otherwise every run would silently exercise
# only the STL/OBJ fallback path even on a machine that has it installed.
import addon_utils as _addon_utils  # type: ignore
for _mod in _addon_utils.modules():
    if _mod.__name__.endswith("ThreeMF_io") or _mod.__name__ == "io_mesh_3mf":
        _is_loaded, _is_enabled = _addon_utils.check(_mod.__name__)
        if not _is_enabled:
            bpy.ops.preferences.addon_enable(module=_mod.__name__)
        break

from TrailPrint3D.export import is_3mf_extension_installed
from TrailPrint3D.utils import elevation as _elevation_module
from TrailPrint3D.utils.generation import runGeneration

# ---------------------------------------------------------------------------
# Elevation stub — synthetic single-hill heightfield, no network.
# ---------------------------------------------------------------------------
_original_get_tile_elevation = _elevation_module.get_tile_elevation


def _fake_get_tile_elevation(obj, progress_cb=None):
    """Drop-in replacement for elevation.get_tile_elevation() that fabricates
    a smooth single-hill heightfield instead of calling MapTerhorn. Keeps
    real geographic-bounds bookkeeping (compute_and_store_tile_bounds is
    pure coordinate math) so scale/GPX handling still runs normally; only
    the network round-trip is skipped. Matches the real function's
    (elevations, diff) return contract and the scene-property side effects
    downstream code (metadata, overlays) reads."""
    world_verts, _num_subdivisions, _disable_cache, _minLat, _maxLat, _minLon, _maxLon = (
        _elevation_module.compute_and_store_tile_bounds(obj)
    )

    elevations = [
        500.0 + 200.0 * math.exp(-(v.x * v.x + v.y * v.y) / 5000.0)
        for v in world_verts
    ]
    lowestElevation = min(elevations)
    highestElevation = max(elevations)
    diff = highestElevation - lowestElevation

    tp3d = bpy.context.scene.tp3d
    tp3d["o_verticesMap"] = str(len(obj.data.vertices))
    tp3d.lowestElevation = lowestElevation
    tp3d.highestElevation = highestElevation
    tp3d.sAdditionalExtrusion = lowestElevation

    return elevations, diff


def _patch_elevation():
    _elevation_module.get_tile_elevation = _fake_get_tile_elevation


def _unpatch_elevation():
    _elevation_module.get_tile_elevation = _original_get_tile_elevation

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
    except Exception:  # noqa: BLE001 - wide exception needed to keep test runner going
        print(f"  FAIL  {name}")
        traceback.print_exc()
        _failed += 1


def _assert_all_passed():
    print(f"\n{'='*60}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"{'='*60}\n")
    if _failed:
        raise SystemExit(1)  # non-zero → Blender exits with code 1


# ---------------------------------------------------------------------------
# Scene setup / teardown helpers
# ---------------------------------------------------------------------------

def _reset_scene_defaults():
    tp3d = bpy.context.scene.tp3d
    tp3d.shape = "HEXAGON"
    tp3d.shapeTextStyle = "NONE"
    tp3d.handleStyle = "NONE"
    tp3d.objSize = 100
    tp3d.num_subdivisions = 3  # resolution = 3
    tp3d.scaleElevation = 1.0
    tp3d.fixedElevationScale = False
    tp3d.singleColorMode = False
    tp3d.elementMode = "PAINT"
    tp3d.disableCache = False  # reuse the addon's real cache across runs
    tp3d.disable_auto_export = False
    tp3d.disable_3mf_export = False
    tp3d.trailName = ""
    tp3d.api = "MAPTERHORN"
    # No terrain elements for any scenario in this matrix.
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
# The matrix
# ---------------------------------------------------------------------------

# Every shape / "Shape Extras" (shapeTextStyle) combination the UI exposes —
# mirrors props.SHAPE_TEXT_STYLES. HEART has no extras at all.
_SHAPE_EXTRAS = [
    ("HEXAGON", "NONE"),
    ("HEXAGON", "INNER TEXT"),
    ("HEXAGON", "OUTER TEXT"),
    ("HEXAGON", "FRONT TEXT"),
    ("HEXAGON", "SHELL"),
    ("OCTAGON", "NONE"),
    ("OCTAGON", "OUTER TEXT"),
    ("OCTAGON", "SHELL"),
    ("CIRCLE", "NONE"),
    ("CIRCLE", "OUTER TEXT"),
    ("CIRCLE", "SHELL"),
    ("SQUARE", "NONE"),
    ("SQUARE", "SHELL"),
    ("ELLIPSE", "NONE"),
    ("ELLIPSE", "SHELL"),
    ("HEART", "NONE"),
]

# Combos that actually show the "Medal Handle" control (panels.py gates
# handleStyle to `effective_shape in {...}` these four) — handleStyle is a
# no-op everywhere else (add_medal_handle is only called from these four
# shape functions in text_objects.py), so it's only varied here.
_HANDLE_CAPABLE = {
    ("HEXAGON", "OUTER TEXT"),
    ("HEXAGON", "FRONT TEXT"),
    ("OCTAGON", "OUTER TEXT"),
    ("CIRCLE", "OUTER TEXT"),
}
_HANDLE_STYLES = ["NONE", "ROUND", "FLAT"]

# Expected minimum object count per shapeTextStyle: map + trail, plus
# whatever extra objects that style creates (matches the per-shape
# expectations in test_generation_pipeline.py). A medal handle is joined
# into the existing plate object, not a new object, so it doesn't change
# these numbers.
_MIN_OBJECTS = {
    "NONE": 2,
    "INNER TEXT": 3,
    "OUTER TEXT": 4,
    "FRONT TEXT": 4,
    "SHELL": 3,
}


def _scenario_name(shape, extra, handle):
    parts = [shape.lower(), extra.lower().replace(" ", "_")]
    if handle != "NONE":
        parts.append(f"handle_{handle.lower()}")
    return "matrix_" + "_".join(parts)


def _build_scenarios():
    scenarios = []
    for shape, extra in _SHAPE_EXTRAS:
        handles = _HANDLE_STYLES if (shape, extra) in _HANDLE_CAPABLE else ["NONE"]
        for handle in handles:
            scenarios.append((shape, extra, handle))
    return scenarios


def _run_matrix_scenario(shape, extra, handle):
    name = _scenario_name(shape, extra, handle)

    _reset_scene_defaults()
    tp3d = bpy.context.scene.tp3d

    tp3d.file_path = os.path.join(_RESOURCES_DIR, _GPX_FILE)
    tp3d.shape = shape
    tp3d.shapeTextStyle = extra
    tp3d.handleStyle = handle
    tp3d.trailName = name  # -> modelname -> export filename, unique per scenario

    # All 24 scenarios share one flat output folder — trailName already makes
    # every filename unique, so there's no need for a per-scenario subfolder.
    tp3d.export_path = _OUTPUT_DIR + os.sep  # export_to_STL / export_selected_to_3mf concatenate path + filename directly

    before = set(bpy.data.objects)
    runGeneration(0)
    after = set(bpy.data.objects)
    new_objects = list(after - before)

    object_count = len(new_objects)
    total_vertices = sum(len(o.data.vertices) for o in new_objects if o.type == 'MESH')

    # Exact-path checks, not a directory-listing prefix match — several
    # scenario names are literal prefixes of others (e.g. "..._outer_text"
    # vs "..._outer_text_handle_round"), which a startswith() filter against
    # the shared flat output folder would cross-match.
    three_mf_path = os.path.join(_OUTPUT_DIR, name + ".3mf")
    obj_path = os.path.join(_OUTPUT_DIR, name + ".obj")
    stl_path = os.path.join(_OUTPUT_DIR, name + ".stl")

    _cleanup_objects(new_objects)

    print(f"    shape={shape:10s} extra={extra:12s} handle={handle:6s} "
          f"objects={object_count} verts={total_vertices}")

    min_objects = _MIN_OBJECTS[extra]
    assert object_count >= min_objects, (
        f"{name}: expected >= {min_objects} objects for {shape}/{extra}, got {object_count}"
    )
    assert total_vertices > 0, f"{name}: expected non-empty geometry"
    if is_3mf_extension_installed():
        assert os.path.isfile(three_mf_path), (
            f"{name}: 3MF extension is installed, expected {three_mf_path} to exist"
        )
    else:
        assert os.path.isfile(obj_path) or os.path.isfile(stl_path), (
            f"{name}: 3MF extension not installed, expected {obj_path} or {stl_path} to exist"
        )


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  TrailPrint3D shape / shape-extras / medal-handle matrix (synthetic elevation, 3MF export)")
    print("=" * 60 + "\n")

    if is_3mf_extension_installed():
        print("  3MF Blender extension detected — exporting real .3mf files.\n")
    else:
        print("  3MF Blender extension NOT installed — exports will fall back to STL/OBJ.\n")

    os.makedirs(_OUTPUT_DIR, exist_ok=True)
    for _f in os.listdir(_OUTPUT_DIR):
        _fp = os.path.join(_OUTPUT_DIR, _f)
        if os.path.isfile(_fp):
            os.remove(_fp)

    _patch_elevation()
    try:
        for shape, extra, handle in _build_scenarios():
            scenario_name = _scenario_name(shape, extra, handle)
            _run(
                scenario_name,
                lambda shape=shape, extra=extra, handle=handle: _run_matrix_scenario(shape, extra, handle),
            )
    finally:
        _unpatch_elevation()

    _assert_all_passed()
