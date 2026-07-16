"""Run all test files and print a combined pass/fail summary.

Run with:
  & "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" --background --factory-startup --python-exit-code 1 -P tests/run_all_tests.py

Each test file is executed in its own namespace so module-level state
does not leak between files. The per-file _passed/_failed counters are
collected after each run and aggregated into a final total.
"""

import sys
import os

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))

_TEST_FILES = [
    "test_geo_elevation.py",
    "test_geojson_import.py",
    "test_gpx.py",
    "test_osm_pipeline.py",
    "test_updater.py",
]

total_passed = 0
total_failed = 0
results = []

for filename in _TEST_FILES:
    filepath = os.path.join(_TESTS_DIR, filename)
    ns = {"__name__": "__main__", "__file__": filepath}

    print(f"\n{'#' * 60}")
    print(f"#  {filename}")
    print(f"{'#' * 60}\n")

    try:
        with open(filepath) as fh:
            exec(compile(fh.read(), filepath, "exec"), ns)
    except SystemExit:
        pass  # _assert_all_passed() raises this — counts are already in ns

    p = ns.get("_passed", 0)
    f = ns.get("_failed", 0)
    total_passed += p
    total_failed += f
    results.append((filename, p, f))

# Combined summary
print(f"\n{'=' * 60}")
print(f"  COMBINED RESULTS")
print(f"{'=' * 60}")
for filename, p, f in results:
    status = "OK  " if f == 0 else "FAIL"
    print(f"  [{status}]  {filename:30s}  {p} passed, {f} failed")
print(f"{'=' * 60}")
print(f"  TOTAL: {total_passed} passed, {total_failed} failed")
print(f"{'=' * 60}\n")

if total_failed:
    raise SystemExit(1)
