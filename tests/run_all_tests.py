"""Run all test files and print a combined pass/fail summary.

Run with:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --factory-startup --python-exit-code 1 -P tests/run_all_tests.py

Pass --overpass (after a `--` separator, as Blender requires for script
args) to also run the live Overpass/coastline network tests inside
test_osm_pipeline.py, e.g.:
  blender --background --factory-startup --python-exit-code 1 -P tests/run_all_tests.py -- --overpass

Those live tests hit the real, shared Overpass API and can fail for reasons
outside this addon's control (rate limiting, timeouts, upstream outages), so
they're skipped by default here. They always run when test_osm_pipeline.py
is invoked directly on its own.

Pass --integration to also run the full end-to-end generation pipeline
tests (test_generation_pipeline.py). These hit real MapTerhorn + Overpass
network, generate real geometry with booleans, and are noticeably slower
than the fast suite, so they're excluded by default:
  blender --background --factory-startup --python-exit-code 1 -P tests/run_all_tests.py -- --integration

Each test file is executed in its own namespace so module-level state
does not leak between files. The per-file _passed/_failed counters are
collected after each run and aggregated into a final total.
"""

import os
import platform
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))


def _wire_bundled_wheels():
    """Extract the addon's bundled wheels (shapely/shapelysmooth) and add them to sys.path.

    Running tests via `-P script.py` bypasses the Blender Extensions install
    step that normally unpacks `[wheels]` from blender_manifest.toml, so
    Blender's bundled Python can't see shapely. Shapely ships a compiled
    `shapely.lib` extension module, which zipimport cannot load straight out
    of a .whl zip, so the wheel must be extracted to a real directory on
    disk first (cached under tests/.wheel_cache/ so this only happens once).
    """
    import zipfile

    wheels_dir = os.path.join(os.path.dirname(_TESTS_DIR), "TrailPrint3D", "wheels")
    if not os.path.isdir(wheels_dir):
        return

    machine = platform.machine().lower()
    is_arm = machine in ("arm64", "aarch64")
    if sys.platform.startswith("win"):
        shapely_wheel = "shapely-2.1.2-cp313-cp313-win_arm64.whl" if is_arm else "shapely-2.1.2-cp313-cp313-win_amd64.whl"
    elif sys.platform == "darwin":
        shapely_wheel = "shapely-2.1.2-cp313-cp313-macosx_11_0_arm64.whl" if is_arm else "shapely-2.1.2-cp313-cp313-macosx_10_13_x86_64.whl"
    else:
        shapely_wheel = "shapely-2.1.2-cp313-cp313-manylinux_2_17_x86_64.whl"

    cache_dir = os.path.join(_TESTS_DIR, ".wheel_cache")
    for wheel_name in (shapely_wheel, "shapelysmooth-0.2.1-py3-none-any.whl"):
        wheel_path = os.path.join(wheels_dir, wheel_name)
        if not os.path.isfile(wheel_path):
            continue
        extract_dir = os.path.join(cache_dir, os.path.splitext(wheel_name)[0])
        if not os.path.isdir(extract_dir):
            with zipfile.ZipFile(wheel_path) as zf:
                zf.extractall(extract_dir)
        if extract_dir not in sys.path:
            sys.path.insert(0, extract_dir)


_wire_bundled_wheels()

_script_args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
_RUN_OVERPASS = "--overpass" in _script_args
_RUN_INTEGRATION = "--integration" in _script_args

_TEST_FILES = [
    "test_geo_elevation.py",
    "test_geojson_import.py",
    "test_geometry2d.py",
    "test_gpx.py",
    "test_osm_pipeline.py",
    "test_prefetch.py",
    "test_updater.py",
]

if _RUN_INTEGRATION:
    _TEST_FILES.append("test_generation_pipeline.py")
    _TEST_FILES.append("test_model_shape_matrix.py")

total_passed = 0
total_failed = 0
results = []

print(f"live Overpass/coastline network tests: {'ENABLED (--overpass)' if _RUN_OVERPASS else 'skipped (pass -- --overpass to enable)'}")
print(f"generation pipeline integration tests: {'ENABLED (--integration)' if _RUN_INTEGRATION else 'skipped (pass -- --integration to enable)'}")

for filename in _TEST_FILES:
    filepath = os.path.join(_TESTS_DIR, filename)
    ns = {"__name__": "__main__", "__file__": filepath, "_TP3D_RUN_OVERPASS": _RUN_OVERPASS}

    print(f"\n{'#' * 60}")
    print(f"#  {filename}")
    print(f"{'#' * 60}\n")

    try:
        with open(filepath) as fh:
            exec(compile(fh.read(), filepath, "exec"), ns)  # noqa: S102
    except SystemExit:
        pass  # _assert_all_passed() raises this — counts are already in ns

    p = ns.get("_passed", 0)
    f = ns.get("_failed", 0)
    total_passed += p
    total_failed += f
    results.append((filename, p, f))

# Combined summary
print(f"\n{'=' * 60}")
print("  COMBINED RESULTS")
print(f"{'=' * 60}")
for filename, p, f in results:
    status = "OK  " if f == 0 else "FAIL"
    print(f"  [{status}]  {filename:30s}  {p} passed, {f} failed")
print(f"{'=' * 60}")
print(f"  TOTAL: {total_passed} passed, {total_failed} failed")
print(f"{'=' * 60}\n")

if total_failed:
    raise SystemExit(1)
