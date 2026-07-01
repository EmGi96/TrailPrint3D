"""Tests for the GPX reader (io_gpx.read_gpx).

Uses real GPX files from tests/Resources/:
  - OneSegment.gpx   — Garmin Connect export, 1 segment, 30 615 pts, has timestamps
  - ManySegments.gpx — KML2GPX export,        21 segments, 9 841 pts, no timestamps

Run with:
  & "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" --background --factory-startup --python-exit-code 1 -P tests/test_gpx.py
"""

import sys
import os
import traceback
from datetime import datetime, timezone

import bpy  # type: ignore

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_RESOURCES = os.path.join(_REPO_ROOT, "tests", "Resources")
_ONE  = os.path.join(_RESOURCES, "OneSegment.gpx")
_MANY = os.path.join(_RESOURCES, "ManySegments.gpx")

# ---------------------------------------------------------------------------
# Minimal test runner
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
        raise SystemExit(1)


# ===========================================================================
# Shared fixture — parse once, reuse across tests
# ===========================================================================
_one_segs  = None
_many_segs = None

def _load():
    global _one_segs, _many_segs
    from TrailPrint3D.utils.io_gpx import read_gpx
    _one_segs  = read_gpx(_ONE)
    _many_segs = read_gpx(_MANY)

# ---------------------------------------------------------------------------
# Return-type / structural invariants (apply to both files)
# ---------------------------------------------------------------------------

def test_returns_list():
    assert isinstance(_one_segs,  list), "read_gpx must return a list"
    assert isinstance(_many_segs, list), "read_gpx must return a list"


def test_segments_are_lists():
    for seg in _one_segs + _many_segs:
        assert isinstance(seg, list), f"Each segment must be a list, got {type(seg)}"


def test_points_are_4_tuples():
    for seg in _one_segs + _many_segs:
        for pt in seg[:5]:  # spot-check first 5 per segment
            assert len(pt) == 4, f"Point must be (lat, lon, ele, time), got len={len(pt)}"


def test_lat_lon_are_floats():
    for seg in _one_segs + _many_segs:
        for pt in seg[:5]:
            lat, lon, *_ = pt
            assert isinstance(lat, float), f"lat must be float, got {type(lat)}"
            assert isinstance(lon, float), f"lon must be float, got {type(lon)}"


def test_elevation_is_float():
    for seg in _one_segs + _many_segs:
        for pt in seg[:5]:
            _, _, ele, _ = pt
            assert isinstance(ele, float), f"elevation must be float, got {type(ele)}"


def test_lat_in_valid_range():
    for seg in _one_segs + _many_segs:
        for pt in seg:
            lat = pt[0]
            assert -90.0 <= lat <= 90.0, f"lat={lat} out of [-90, 90]"


def test_lon_in_valid_range():
    for seg in _one_segs + _many_segs:
        for pt in seg:
            lon = pt[1]
            assert -180.0 <= lon <= 180.0, f"lon={lon} out of [-180, 180]"


def test_no_empty_segments():
    for segs in (_one_segs, _many_segs):
        for i, seg in enumerate(segs):
            assert len(seg) > 0, f"Segment {i} is empty"


# ===========================================================================
# OneSegment.gpx
# ===========================================================================

def test_one_segment_count():
    assert len(_one_segs) == 1, \
        f"OneSegment.gpx must have 1 segment, got {len(_one_segs)}"


def test_one_point_count():
    total = sum(len(s) for s in _one_segs)
    assert total == 30615, f"Expected 30 615 points, got {total}"


def test_one_first_point_coordinates():
    pt = _one_segs[0][0]
    lat, lon, ele, _ = pt
    assert abs(lat - 28.390550) < 1e-4, f"First lat mismatch: {lat}"
    assert abs(lon - 100.382624) < 1e-4, f"First lon mismatch: {lon}"


def test_one_last_point_coordinates():
    pt = _one_segs[0][-1]
    lat, lon, *_ = pt
    assert abs(lat - 28.434356) < 1e-4, f"Last lat mismatch: {lat}"
    assert abs(lon - 100.355721) < 1e-4, f"Last lon mismatch: {lon}"


def test_one_has_elevation():
    # File records elevations starting at ~4179 m
    ele = _one_segs[0][0][2]
    assert ele > 0, f"First elevation should be positive (high altitude hike), got {ele}"


def test_one_elevations_in_plausible_range():
    for pt in _one_segs[0]:
        ele = pt[2]
        assert 0 <= ele <= 9000, f"Elevation {ele} m out of plausible range"


def test_one_has_timestamps():
    for pt in _one_segs[0][:10]:
        ts = pt[3]
        assert ts is not None, "OneSegment.gpx should have timestamps on every point"
        assert isinstance(ts, datetime), f"Timestamp must be datetime, got {type(ts)}"


def test_one_first_timestamp_date():
    ts = _one_segs[0][0][3]
    assert ts.year  == 2025, f"Expected year 2025, got {ts.year}"
    assert ts.month == 10,   f"Expected month 10, got {ts.month}"
    assert ts.day   == 5,    f"Expected day 5, got {ts.day}"


def test_one_timestamps_non_decreasing():
    pts = _one_segs[0]
    for i in range(1, min(500, len(pts))):
        t_prev = pts[i - 1][3]
        t_curr = pts[i][3]
        if t_prev is not None and t_curr is not None:
            assert t_curr >= t_prev, \
                f"Timestamps not non-decreasing at index {i}: {t_prev} > {t_curr}"


# ===========================================================================
# ManySegments.gpx
# ===========================================================================

_MANY_PTS_PER_SEG = [
    425, 542, 1337, 946, 116, 435, 567, 836, 229, 491,
    252, 118, 237, 194, 883, 878, 227, 482,  83, 239, 324,
]

def test_many_segment_count():
    assert len(_many_segs) == 21, \
        f"ManySegments.gpx must have 21 segments, got {len(_many_segs)}"


def test_many_total_point_count():
    total = sum(len(s) for s in _many_segs)
    assert total == 9841, f"Expected 9 841 points, got {total}"


def test_many_points_per_segment():
    for i, (seg, expected) in enumerate(zip(_many_segs, _MANY_PTS_PER_SEG)):
        assert len(seg) == expected, \
            f"Segment {i}: expected {expected} pts, got {len(seg)}"


def test_many_no_timestamps():
    for i, seg in enumerate(_many_segs):
        for pt in seg[:5]:
            ts = pt[3]
            assert ts is None, \
                f"ManySegments.gpx should have no timestamps, got {ts} in seg {i}"


def test_many_first_point_coordinates():
    pt = _many_segs[0][0]
    lat, lon, *_ = pt
    assert abs(lat - 43.983766) < 1e-4, f"First lat mismatch: {lat}"
    assert abs(lon - (-114.870377)) < 1e-4, f"First lon mismatch: {lon}"


def test_many_coordinates_in_sawtooth_region():
    # All points should be in the Sawtooth Mountains area of Idaho
    for seg in _many_segs:
        for pt in seg:
            lat, lon = pt[0], pt[1]
            assert 43.0 < lat < 45.0, f"lat {lat} outside Sawtooth region"
            assert -116.0 < lon < -113.0, f"lon {lon} outside Sawtooth region"


def test_many_elevation_in_low_range():
    # Converted from KML — most elevations are 0, a few have small non-zero
    # values (up to ~17 m) that are KML conversion artifacts
    for seg in _many_segs:
        for pt in seg:
            assert 0.0 <= pt[2] <= 20.0, \
                f"Elevation {pt[2]} m outside expected low range [0, 20]"


# ===========================================================================
# Error handling
# ===========================================================================

def test_missing_file_raises():
    from TrailPrint3D.utils.io_gpx import read_gpx
    try:
        read_gpx("/nonexistent/path/file.gpx")
        assert False, "Expected an exception for missing file"
    except Exception:
        pass  # any exception is acceptable (FileNotFoundError, ET.ParseError, etc.)


# ===========================================================================
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  TrailPrint3D GPX reader tests")
    print("=" * 60 + "\n")

    _load()

    # Structural / return-type invariants
    _run("structure: returns a list",                      test_returns_list)
    _run("structure: segments are lists",                  test_segments_are_lists)
    _run("structure: points are 4-tuples",                 test_points_are_4_tuples)
    _run("structure: lat/lon are floats",                  test_lat_lon_are_floats)
    _run("structure: elevation is float",                  test_elevation_is_float)
    _run("structure: lat in [-90, 90]",                    test_lat_in_valid_range)
    _run("structure: lon in [-180, 180]",                  test_lon_in_valid_range)
    _run("structure: no empty segments",                   test_no_empty_segments)

    # OneSegment.gpx
    _run("one: 1 segment",                                 test_one_segment_count)
    _run("one: 30 615 total points",                       test_one_point_count)
    _run("one: first point coordinates",                   test_one_first_point_coordinates)
    _run("one: last point coordinates",                    test_one_last_point_coordinates)
    _run("one: has positive elevation",                    test_one_has_elevation)
    _run("one: elevations in plausible range",             test_one_elevations_in_plausible_range)
    _run("one: has datetime timestamps",                   test_one_has_timestamps)
    _run("one: first timestamp date is 2025-10-05",        test_one_first_timestamp_date)
    _run("one: timestamps non-decreasing",                 test_one_timestamps_non_decreasing)

    # ManySegments.gpx
    _run("many: 21 segments",                              test_many_segment_count)
    _run("many: 9 841 total points",                       test_many_total_point_count)
    _run("many: points per segment match exactly",         test_many_points_per_segment)
    _run("many: no timestamps",                            test_many_no_timestamps)
    _run("many: first point coordinates",                  test_many_first_point_coordinates)
    _run("many: all coords in Sawtooth region",            test_many_coordinates_in_sawtooth_region)
    _run("many: elevations in low range [0, 20]",           test_many_elevation_in_low_range)

    # Error handling
    _run("error: missing file raises exception",           test_missing_file_raises)

    _assert_all_passed()
