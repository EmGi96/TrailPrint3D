"""Unit tests for pure-math functions in geo.py and elevation.py.

No bpy scene properties are read by any function under test — all tested
functions take plain Python arguments and return plain values.

Run with:
  & "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" --background --factory-startup --python-exit-code 1 -P tests/test_geo_elevation.py
"""

import sys
import os
import math
import traceback
from datetime import datetime, timedelta

import bpy  # type: ignore  — provided by Blender's Python

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

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


_APPROX = 1e-6   # relative tolerance for floating-point comparisons
_KM_TOL = 0.001  # absolute tolerance in km for distance results

def _approx_equal(a, b, tol=_KM_TOL):
    return abs(a - b) <= tol

def _approx_rel(a, b, rel=_APPROX):
    return abs(a - b) <= rel * max(abs(a), abs(b), 1.0)


# ===========================================================================
# geo.py
# ===========================================================================

# ---------------------------------------------------------------------------
# haversine
# ---------------------------------------------------------------------------

def test_haversine_same_point():
    from TrailPrint3D.utils.geo import haversine
    assert haversine(0, 0, 0, 0) == 0.0
    assert haversine(48.14, 11.55, 48.14, 11.55) == 0.0


def test_haversine_one_degree_longitude_at_equator():
    from TrailPrint3D.utils.geo import haversine
    # 1° of longitude at the equator ≈ 2π*R/360 ≈ 111.195 km
    expected = 2 * math.pi * 6371.0 / 360
    result = haversine(0, 0, 0, 1)
    assert _approx_equal(result, expected, tol=0.001), \
        f"Expected ≈{expected:.3f} km, got {result:.3f} km"


def test_haversine_one_degree_latitude():
    from TrailPrint3D.utils.geo import haversine
    # 1° of latitude is the same arc length regardless of longitude
    expected = 2 * math.pi * 6371.0 / 360
    result = haversine(0, 0, 1, 0)
    assert _approx_equal(result, expected, tol=0.001), \
        f"Expected ≈{expected:.3f} km, got {result:.3f} km"


def test_haversine_symmetry():
    from TrailPrint3D.utils.geo import haversine
    a = haversine(48.14, 11.55, 52.52, 13.40)
    b = haversine(52.52, 13.40, 48.14, 11.55)
    assert _approx_rel(a, b), f"haversine not symmetric: {a} vs {b}"


def test_haversine_antipodal():
    from TrailPrint3D.utils.geo import haversine
    # Points 180° apart on the equator — half the Earth's circumference
    expected = math.pi * 6371.0
    result = haversine(0, 0, 0, 180)
    assert _approx_equal(result, expected, tol=0.01), \
        f"Expected ≈{expected:.3f} km (half circumference), got {result:.3f} km"


def test_haversine_known_city_pair():
    from TrailPrint3D.utils.geo import haversine
    # Munich → Berlin: roughly 504 km; accept ±2 km
    result = haversine(48.14, 11.58, 52.52, 13.40)
    assert 500 < result < 510, \
        f"Munich-Berlin distance out of expected range: {result:.1f} km"


# ---------------------------------------------------------------------------
# calculate_total_length
# ---------------------------------------------------------------------------

def test_total_length_empty():
    from TrailPrint3D.utils.geo import calculate_total_length
    assert calculate_total_length([]) == 0.0


def test_total_length_single_point():
    from TrailPrint3D.utils.geo import calculate_total_length
    assert calculate_total_length([(48.0, 11.0, 500, None)]) == 0.0


def test_total_length_two_points_matches_haversine():
    from TrailPrint3D.utils.geo import calculate_total_length, haversine
    p1 = (48.14, 11.55, 500, None)
    p2 = (52.52, 13.40, 100, None)
    expected = haversine(p1[0], p1[1], p2[0], p2[1])
    result = calculate_total_length([p1, p2])
    assert _approx_equal(result, expected, tol=0.001), \
        f"Expected {expected:.4f} km, got {result:.4f} km"


def test_total_length_three_points_sums_segments():
    from TrailPrint3D.utils.geo import calculate_total_length, haversine
    p1 = (0.0, 0.0,   0, None)
    p2 = (0.0, 1.0,   0, None)
    p3 = (0.0, 2.0,   0, None)
    seg1 = haversine(0, 0, 0, 1)
    seg2 = haversine(0, 1, 0, 2)
    result = calculate_total_length([p1, p2, p3])
    assert _approx_equal(result, seg1 + seg2, tol=0.001), \
        f"Expected {seg1+seg2:.4f} km, got {result:.4f} km"


# ---------------------------------------------------------------------------
# calculate_total_elevation
# ---------------------------------------------------------------------------

def test_total_elevation_empty():
    from TrailPrint3D.utils.geo import calculate_total_elevation
    assert calculate_total_elevation([]) == 0.0


def test_total_elevation_single_point():
    from TrailPrint3D.utils.geo import calculate_total_elevation
    assert calculate_total_elevation([(0, 0, 500, None)]) == 0.0


def test_total_elevation_monotone_ascending():
    from TrailPrint3D.utils.geo import calculate_total_elevation
    pts = [(0, 0, e, None) for e in [100, 200, 300, 400]]
    assert calculate_total_elevation(pts) == 300.0


def test_total_elevation_only_gains_counted():
    from TrailPrint3D.utils.geo import calculate_total_elevation
    # 100→50 is a descent (ignored), 50→200 is +150 gain
    pts = [(0, 0, e, None) for e in [100, 50, 200]]
    assert calculate_total_elevation(pts) == 150.0


def test_total_elevation_monotone_descending():
    from TrailPrint3D.utils.geo import calculate_total_elevation
    pts = [(0, 0, e, None) for e in [500, 400, 300, 100]]
    assert calculate_total_elevation(pts) == 0.0


def test_total_elevation_flat():
    from TrailPrint3D.utils.geo import calculate_total_elevation
    pts = [(0, 0, 250, None)] * 5
    assert calculate_total_elevation(pts) == 0.0


# ---------------------------------------------------------------------------
# calculate_total_time
# ---------------------------------------------------------------------------

def test_total_time_empty():
    from TrailPrint3D.utils.geo import calculate_total_time
    assert calculate_total_time([]) == 0.0


def test_total_time_none_timestamps():
    from TrailPrint3D.utils.geo import calculate_total_time
    pts = [(0, 0, 0, None), (1, 1, 0, None)]
    assert calculate_total_time(pts) == 0.0


def test_total_time_one_hour():
    from TrailPrint3D.utils.geo import calculate_total_time
    t0 = datetime(2024, 6, 1, 9, 0, 0)
    t1 = datetime(2024, 6, 1, 10, 0, 0)
    pts = [(0, 0, 0, t0), (0, 0, 0, None), (0, 0, 0, t1)]
    result = calculate_total_time(pts)
    assert _approx_rel(result, 1.0), f"Expected 1.0 h, got {result}"


def test_total_time_ninety_minutes():
    from TrailPrint3D.utils.geo import calculate_total_time
    t0 = datetime(2024, 6, 1, 8, 0, 0)
    t1 = datetime(2024, 6, 1, 9, 30, 0)
    pts = [(0, 0, 0, t0), (0, 0, 0, t1)]
    result = calculate_total_time(pts)
    assert _approx_rel(result, 1.5), f"Expected 1.5 h, got {result}"


# ---------------------------------------------------------------------------
# calculate_date
# ---------------------------------------------------------------------------

def test_calculate_date_empty():
    from TrailPrint3D.utils.geo import calculate_date
    assert calculate_date([]) == ""


def test_calculate_date_none_timestamp():
    from TrailPrint3D.utils.geo import calculate_date
    pts = [(0, 0, 0, None), (1, 1, 0, None)]
    assert calculate_date(pts) == ""


def test_calculate_date_known_date():
    from TrailPrint3D.utils.geo import calculate_date
    t = datetime(2024, 6, 15, 10, 0, 0)
    pts = [(0, 0, 0, t), (1, 1, 0, None)]
    assert calculate_date(pts) == "2024-06-15"


# ---------------------------------------------------------------------------
# separate_duplicate_xy
# ---------------------------------------------------------------------------

def test_separate_duplicate_xy_unique_points_unchanged():
    from TrailPrint3D.utils.geo import separate_duplicate_xy
    pts = [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0], [2.0, 2.0, 2.0]]
    result = separate_duplicate_xy(pts)
    assert result[0][0] == 0.0 and result[0][1] == 0.0
    assert result[1][0] == 1.0 and result[1][1] == 1.0
    assert result[2][0] == 2.0 and result[2][1] == 2.0


def test_separate_duplicate_xy_duplicates_are_moved():
    from TrailPrint3D.utils.geo import separate_duplicate_xy
    pts = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
    result = separate_duplicate_xy(pts)
    x0, y0, z0 = result[0][0], result[0][1], result[0][2]
    x1, y1, z1 = result[1][0], result[1][1], result[1][2]
    dist = math.sqrt((x1 - x0)**2 + (y1 - y0)**2 + (z1 - z0)**2)
    assert dist >= 0.01, f"Duplicate points were not separated (dist={dist})"


def test_separate_duplicate_xy_preserves_count():
    from TrailPrint3D.utils.geo import separate_duplicate_xy
    pts = [[float(i), float(i), float(i)] for i in range(5)]
    pts.append([0.0, 0.0, 0.0])  # duplicate of first
    result = separate_duplicate_xy(pts)
    assert len(result) == 6


def test_separate_duplicate_xy_three_identical():
    from TrailPrint3D.utils.geo import separate_duplicate_xy
    threshold = 0.01
    pts = [[5.0, 5.0, 5.0], [5.0, 5.0, 5.0], [5.0, 5.0, 5.0]]
    result = separate_duplicate_xy(pts)
    for i in range(len(result)):
        for j in range(i + 1, len(result)):
            xi, yi, zi = result[i][0], result[i][1], result[i][2]
            xj, yj, zj = result[j][0], result[j][1], result[j][2]
            dist = math.sqrt((xi-xj)**2 + (yi-yj)**2 + (zi-zj)**2)
            assert dist >= threshold, \
                f"Points {i} and {j} still too close after separation (dist={dist:.4f})"


# ---------------------------------------------------------------------------
# midpoint_spherical
# ---------------------------------------------------------------------------

def test_midpoint_spherical_same_point():
    from TrailPrint3D.utils.geo import midpoint_spherical
    lat, lon = midpoint_spherical(45.0, 10.0, 45.0, 10.0)
    assert _approx_equal(lat, 45.0, tol=1e-9)
    assert _approx_equal(lon, 10.0, tol=1e-9)


def test_midpoint_spherical_equatorial_average_longitude():
    from TrailPrint3D.utils.geo import midpoint_spherical
    # Two points on the equator at lon=0 and lon=90 → midpoint should be at lon=45
    lat, lon = midpoint_spherical(0.0, 0.0, 0.0, 90.0)
    assert _approx_equal(lat, 0.0, tol=1e-9), f"Latitude should be 0, got {lat}"
    assert _approx_equal(lon, 45.0, tol=1e-9), f"Longitude should be 45, got {lon}"


def test_midpoint_spherical_symmetric():
    from TrailPrint3D.utils.geo import midpoint_spherical
    lat1, lon1 = midpoint_spherical(48.0, 11.0, 52.0, 13.0)
    lat2, lon2 = midpoint_spherical(52.0, 13.0, 48.0, 11.0)
    assert _approx_equal(lat1, lat2, tol=1e-9)
    assert _approx_equal(lon1, lon2, tol=1e-9)


def test_midpoint_spherical_equidistant_from_endpoints():
    from TrailPrint3D.utils.geo import midpoint_spherical, haversine
    lat1, lon1 = 40.0, -10.0
    lat2, lon2 = 50.0,  20.0
    mid_lat, mid_lon = midpoint_spherical(lat1, lon1, lat2, lon2)
    d1 = haversine(lat1, lon1, mid_lat, mid_lon)
    d2 = haversine(lat2, lon2, mid_lat, mid_lon)
    assert _approx_equal(d1, d2, tol=0.01), \
        f"Midpoint not equidistant: d1={d1:.4f} km, d2={d2:.4f} km"


# ---------------------------------------------------------------------------
# move_coordinates
# ---------------------------------------------------------------------------

def test_move_coordinates_north_increases_latitude():
    from TrailPrint3D.utils.geo import move_coordinates
    lat2, lon2 = move_coordinates(0.0, 0.0, 100.0, 'n')
    assert lat2 > 0.0
    assert _approx_equal(lon2, 0.0, tol=1e-9)


def test_move_coordinates_south_decreases_latitude():
    from TrailPrint3D.utils.geo import move_coordinates
    lat2, lon2 = move_coordinates(10.0, 5.0, 100.0, 's')
    assert lat2 < 10.0
    assert _approx_equal(lon2, 5.0, tol=1e-9)


def test_move_coordinates_east_increases_longitude():
    from TrailPrint3D.utils.geo import move_coordinates
    lat2, lon2 = move_coordinates(0.0, 0.0, 100.0, 'e')
    assert lon2 > 0.0
    assert _approx_equal(lat2, 0.0, tol=1e-9)


def test_move_coordinates_west_decreases_longitude():
    from TrailPrint3D.utils.geo import move_coordinates
    lat2, lon2 = move_coordinates(0.0, 10.0, 100.0, 'w')
    assert lon2 < 10.0
    assert _approx_equal(lat2, 0.0, tol=1e-9)


def test_move_coordinates_distance_roundtrip():
    from TrailPrint3D.utils.geo import move_coordinates, haversine
    for direction in ['n', 's', 'e', 'w']:
        lat2, lon2 = move_coordinates(0.0, 0.0, 100.0, direction)
        dist = haversine(0.0, 0.0, lat2, lon2)
        assert _approx_equal(dist, 100.0, tol=0.01), \
            f"Direction {direction!r}: moved 100 km but haversine gives {dist:.4f} km"


def test_move_coordinates_invalid_direction():
    from TrailPrint3D.utils.geo import move_coordinates
    try:
        move_coordinates(0.0, 0.0, 10.0, 'x')
        assert False, "Expected ValueError for invalid direction"
    except ValueError:
        pass


# ===========================================================================
# elevation.py
# ===========================================================================

# ---------------------------------------------------------------------------
# terrarium_pixel_to_elevation
# ---------------------------------------------------------------------------

def test_terrarium_sea_level():
    from TrailPrint3D.utils.elevation import terrarium_pixel_to_elevation
    # (128, 0, 0): 128*256 + 0 + 0 - 32768 = 32768 - 32768 = 0
    result = terrarium_pixel_to_elevation(128, 0, 0)
    assert result == 0.0, f"Expected 0.0, got {result}"


def test_terrarium_minimum():
    from TrailPrint3D.utils.elevation import terrarium_pixel_to_elevation
    # (0, 0, 0): 0 - 32768 = -32768
    result = terrarium_pixel_to_elevation(0, 0, 0)
    assert result == -32768.0, f"Expected -32768.0, got {result}"


def test_terrarium_one_unit_above_sea_level():
    from TrailPrint3D.utils.elevation import terrarium_pixel_to_elevation
    # (128, 1, 0): 32768 + 1 + 0 - 32768 = 1
    result = terrarium_pixel_to_elevation(128, 1, 0)
    assert result == 1.0, f"Expected 1.0, got {result}"


def test_terrarium_256_meters():
    from TrailPrint3D.utils.elevation import terrarium_pixel_to_elevation
    # (129, 0, 0): 129*256 - 32768 = 33024 - 32768 = 256
    result = terrarium_pixel_to_elevation(129, 0, 0)
    assert result == 256.0, f"Expected 256.0, got {result}"


def test_terrarium_maximum():
    from TrailPrint3D.utils.elevation import terrarium_pixel_to_elevation
    # (255, 255, 255): 255*256 + 255 + 255/256 - 32768
    expected = 255 * 256 + 255 + 255 / 256 - 32768
    result = terrarium_pixel_to_elevation(255, 255, 255)
    assert _approx_rel(result, expected), f"Expected {expected}, got {result}"


def test_terrarium_blue_channel_is_fractional():
    from TrailPrint3D.utils.elevation import terrarium_pixel_to_elevation
    # b=256 is invalid, but b=128 adds 0.5 m
    base = terrarium_pixel_to_elevation(128, 0, 0)
    with_blue = terrarium_pixel_to_elevation(128, 0, 128)
    assert _approx_rel(with_blue - base, 128 / 256), \
        f"Blue channel should add 128/256 m, got {with_blue - base}"


# ---------------------------------------------------------------------------
# lonlat_to_tilexy
# ---------------------------------------------------------------------------

def test_tilexy_zoom0_whole_world():
    from TrailPrint3D.utils.elevation import lonlat_to_tilexy
    # At zoom 0, everything maps to tile (0, 0)
    assert lonlat_to_tilexy(0, 0, 0) == (0, 0)


def test_tilexy_zoom1_center():
    from TrailPrint3D.utils.elevation import lonlat_to_tilexy
    # At zoom 1, (lon=0, lat=0) maps to tile (1, 1)
    assert lonlat_to_tilexy(0, 0, 1) == (1, 1)


def test_tilexy_result_in_valid_range():
    from TrailPrint3D.utils.elevation import lonlat_to_tilexy
    for zoom in [0, 1, 5, 10]:
        max_tile = 2 ** zoom - 1
        xtile, ytile = lonlat_to_tilexy(11.55, 48.14, zoom)
        assert 0 <= xtile <= max_tile, f"xtile {xtile} out of range at zoom {zoom}"
        assert 0 <= ytile <= max_tile, f"ytile {ytile} out of range at zoom {zoom}"


def test_tilexy_east_of_west():
    from TrailPrint3D.utils.elevation import lonlat_to_tilexy
    # A point further east should map to the same or higher x tile
    zoom = 5
    x_west, _ = lonlat_to_tilexy(0, 0, zoom)
    x_east, _ = lonlat_to_tilexy(90, 0, zoom)
    assert x_east > x_west, "Eastern longitude should yield a higher x tile"


def test_tilexy_north_above_south():
    from TrailPrint3D.utils.elevation import lonlat_to_tilexy
    # In web mercator, y tiles count from top (north), so north → lower y index
    zoom = 5
    _, y_north = lonlat_to_tilexy(0, 60, zoom)
    _, y_south = lonlat_to_tilexy(0, -60, zoom)
    assert y_north < y_south, "Northern latitude should yield a lower y tile index"


# ---------------------------------------------------------------------------
# lonlat_to_pixelxy
# ---------------------------------------------------------------------------

def test_pixelxy_result_in_byte_range():
    from TrailPrint3D.utils.elevation import lonlat_to_pixelxy
    for lon, lat in [(0, 0), (11.55, 48.14), (-73.9, 40.7), (139.7, 35.7)]:
        px, py = lonlat_to_pixelxy(lon, lat, zoom=10)
        assert 0 <= px <= 255, f"px={px} out of [0,255] for ({lon},{lat})"
        assert 0 <= py <= 255, f"py={py} out of [0,255] for ({lon},{lat})"


def test_pixelxy_center_of_world_tile():
    from TrailPrint3D.utils.elevation import lonlat_to_pixelxy
    # At zoom 0, lon=0/lat=0 is the center of the single tile → pixel (128, 128)
    px, py = lonlat_to_pixelxy(0, 0, 0)
    assert px == 128, f"Expected px=128, got {px}"
    assert py == 128, f"Expected py=128, got {py}"


# ---------------------------------------------------------------------------
# paeth_predictor
# ---------------------------------------------------------------------------

def test_paeth_all_equal():
    from TrailPrint3D.utils.elevation import paeth_predictor
    # p = a+b-c = 1; pa=pb=pc=0 → returns a
    assert paeth_predictor(1, 1, 1) == 1


def test_paeth_returns_a():
    from TrailPrint3D.utils.elevation import paeth_predictor
    # p=5+3-2=6; pa=|6-5|=1, pb=|6-3|=3, pc=|6-2|=4 → pa smallest → return a=5
    assert paeth_predictor(5, 3, 2) == 5


def test_paeth_returns_b():
    from TrailPrint3D.utils.elevation import paeth_predictor
    # p=10+20-5=25; pa=|25-10|=15, pb=|25-20|=5, pc=|25-5|=20 → pb smallest → return b=20
    assert paeth_predictor(10, 20, 5) == 20


def test_paeth_returns_c():
    from TrailPrint3D.utils.elevation import paeth_predictor
    # p=0+0-10=-10; pa=|-10-0|=10, pb=|-10-0|=10, pc=|-10-10|=20
    # pa<=pb (tie) and pa<=pc → returns a=0
    # Actually let me think again: pa=10, pb=10, pc=20
    # pa<=pb (10<=10) and pa<=pc (10<=20) → return a=0
    # To force c, need pc to be smallest:
    # a=200, b=200, c=199: p=200+200-199=201; pa=1, pb=1, pc=2 → tie on pa,pb → returns a
    # Let me think of a case where c wins:
    # a=5, b=5, c=6: p=4; pa=|4-5|=1, pb=|4-5|=1, pc=|4-6|=2 → tie → returns a
    # Actually getting c to win requires pb > pc: p=a+b-c, need |p-b| > |p-c|
    # If a=10, b=20, c=15: p=15; pa=5, pb=5, pc=0 → pc smallest → return c=15
    assert paeth_predictor(10, 20, 15) == 15


def test_paeth_zero_inputs():
    from TrailPrint3D.utils.elevation import paeth_predictor
    assert paeth_predictor(0, 0, 0) == 0


# ---------------------------------------------------------------------------
# fix_invalid_elevations
# ---------------------------------------------------------------------------

def test_fix_invalid_empty():
    from TrailPrint3D.utils.elevation import fix_invalid_elevations
    result, count = fix_invalid_elevations([])
    assert result == [] and count == 0


def test_fix_invalid_all_valid_unchanged():
    from TrailPrint3D.utils.elevation import fix_invalid_elevations
    elevs = [100.0, 200.0, 150.0, 300.0]
    result, count = fix_invalid_elevations(elevs)
    assert count == 0
    assert result == elevs


def test_fix_invalid_out_of_range_interpolated():
    from TrailPrint3D.utils.elevation import fix_invalid_elevations
    # 99999 is above 9000 m limit → should be replaced with avg of neighbors
    elevs = [100.0, 99999.0, 300.0]
    result, count = fix_invalid_elevations(elevs)
    assert count == 1
    assert result[1] == (100.0 + 300.0) / 2, \
        f"Expected 200.0, got {result[1]}"


def test_fix_invalid_at_start_uses_right_neighbor():
    from TrailPrint3D.utils.elevation import fix_invalid_elevations
    elevs = [-9999.0, 500.0, 600.0]
    result, count = fix_invalid_elevations(elevs)
    assert count == 1
    assert result[0] == 500.0, \
        f"Start invalid should be filled from right neighbor, got {result[0]}"


def test_fix_invalid_at_end_uses_left_neighbor():
    from TrailPrint3D.utils.elevation import fix_invalid_elevations
    elevs = [400.0, 500.0, -9999.0]
    result, count = fix_invalid_elevations(elevs)
    assert count == 1
    assert result[2] == 500.0, \
        f"End invalid should be filled from left neighbor, got {result[2]}"


def test_fix_invalid_multiple_bad_values():
    from TrailPrint3D.utils.elevation import fix_invalid_elevations
    elevs = [100.0, 99999.0, 99999.0, 400.0]
    result, count = fix_invalid_elevations(elevs)
    assert count == 2
    assert result[0] == 100.0
    assert result[3] == 400.0
    # Both interior invalids replaced with average of 100 and 400
    assert result[1] == (100.0 + 400.0) / 2
    assert result[2] == (100.0 + 400.0) / 2


def test_fix_invalid_all_invalid_returns_unchanged():
    from TrailPrint3D.utils.elevation import fix_invalid_elevations
    # No valid values → early return, list untouched
    elevs = [-9999.0, -9999.0]
    result, count = fix_invalid_elevations(elevs)
    assert count == 0
    assert result == elevs


# ===========================================================================
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  TrailPrint3D geo + elevation math tests")
    print("=" * 60 + "\n")

    # haversine
    _run("haversine: same point → 0",                    test_haversine_same_point)
    _run("haversine: 1° lon at equator ≈ 111.195 km",    test_haversine_one_degree_longitude_at_equator)
    _run("haversine: 1° lat ≈ 111.195 km",               test_haversine_one_degree_latitude)
    _run("haversine: symmetric",                          test_haversine_symmetry)
    _run("haversine: antipodal ≈ π·R km",                test_haversine_antipodal)
    _run("haversine: Munich-Berlin ≈ 504 km",            test_haversine_known_city_pair)

    # calculate_total_length
    _run("total length: empty → 0",                      test_total_length_empty)
    _run("total length: single point → 0",               test_total_length_single_point)
    _run("total length: two points matches haversine",   test_total_length_two_points_matches_haversine)
    _run("total length: three points sums segments",     test_total_length_three_points_sums_segments)

    # calculate_total_elevation
    _run("total elevation: empty → 0",                   test_total_elevation_empty)
    _run("total elevation: single point → 0",            test_total_elevation_single_point)
    _run("total elevation: monotone ascending",          test_total_elevation_monotone_ascending)
    _run("total elevation: only gains counted",          test_total_elevation_only_gains_counted)
    _run("total elevation: monotone descending → 0",     test_total_elevation_monotone_descending)
    _run("total elevation: flat → 0",                    test_total_elevation_flat)

    # calculate_total_time
    _run("total time: empty → 0",                        test_total_time_empty)
    _run("total time: None timestamps → 0",              test_total_time_none_timestamps)
    _run("total time: 1 hour",                           test_total_time_one_hour)
    _run("total time: 90 minutes",                       test_total_time_ninety_minutes)

    # calculate_date
    _run("calculate date: empty → ''",                   test_calculate_date_empty)
    _run("calculate date: None timestamp → ''",          test_calculate_date_none_timestamp)
    _run("calculate date: known date",                   test_calculate_date_known_date)

    # separate_duplicate_xy
    _run("separate_xy: unique points unchanged",         test_separate_duplicate_xy_unique_points_unchanged)
    _run("separate_xy: duplicates are moved",            test_separate_duplicate_xy_duplicates_are_moved)
    _run("separate_xy: preserves point count",           test_separate_duplicate_xy_preserves_count)
    _run("separate_xy: three identical all separated",   test_separate_duplicate_xy_three_identical)

    # midpoint_spherical
    _run("midpoint: same point → same coords",           test_midpoint_spherical_same_point)
    _run("midpoint: equatorial average longitude",       test_midpoint_spherical_equatorial_average_longitude)
    _run("midpoint: symmetric",                          test_midpoint_spherical_symmetric)
    _run("midpoint: equidistant from endpoints",         test_midpoint_spherical_equidistant_from_endpoints)

    # move_coordinates
    _run("move: north increases latitude",               test_move_coordinates_north_increases_latitude)
    _run("move: south decreases latitude",               test_move_coordinates_south_decreases_latitude)
    _run("move: east increases longitude",               test_move_coordinates_east_increases_longitude)
    _run("move: west decreases longitude",               test_move_coordinates_west_decreases_longitude)
    _run("move: distance roundtrip via haversine",       test_move_coordinates_distance_roundtrip)
    _run("move: invalid direction raises ValueError",    test_move_coordinates_invalid_direction)

    # terrarium_pixel_to_elevation
    _run("terrarium: sea level (128,0,0) → 0",           test_terrarium_sea_level)
    _run("terrarium: minimum (0,0,0) → -32768",          test_terrarium_minimum)
    _run("terrarium: 1 m above sea level",               test_terrarium_one_unit_above_sea_level)
    _run("terrarium: 256 m (129,0,0)",                   test_terrarium_256_meters)
    _run("terrarium: maximum (255,255,255)",             test_terrarium_maximum)
    _run("terrarium: blue channel is fractional",        test_terrarium_blue_channel_is_fractional)

    # lonlat_to_tilexy
    _run("tilexy: zoom 0 whole world → (0,0)",           test_tilexy_zoom0_whole_world)
    _run("tilexy: zoom 1 center → (1,1)",                test_tilexy_zoom1_center)
    _run("tilexy: result within valid range",            test_tilexy_result_in_valid_range)
    _run("tilexy: east → higher x tile",                 test_tilexy_east_of_west)
    _run("tilexy: north → lower y tile",                 test_tilexy_north_above_south)

    # lonlat_to_pixelxy
    _run("pixelxy: result in [0,255]",                   test_pixelxy_result_in_byte_range)
    _run("pixelxy: center of world tile → (128,128)",    test_pixelxy_center_of_world_tile)

    # paeth_predictor
    _run("paeth: all equal → returns a",                 test_paeth_all_equal)
    _run("paeth: returns a when pa smallest",            test_paeth_returns_a)
    _run("paeth: returns b when pb smallest",            test_paeth_returns_b)
    _run("paeth: returns c when pc smallest",            test_paeth_returns_c)
    _run("paeth: zero inputs",                           test_paeth_zero_inputs)

    # fix_invalid_elevations
    _run("fix_invalid: empty list",                      test_fix_invalid_empty)
    _run("fix_invalid: all valid unchanged",             test_fix_invalid_all_valid_unchanged)
    _run("fix_invalid: out-of-range → interpolated",    test_fix_invalid_out_of_range_interpolated)
    _run("fix_invalid: invalid at start → right nbr",   test_fix_invalid_at_start_uses_right_neighbor)
    _run("fix_invalid: invalid at end → left nbr",      test_fix_invalid_at_end_uses_left_neighbor)
    _run("fix_invalid: multiple bad values",             test_fix_invalid_multiple_bad_values)
    _run("fix_invalid: all invalid → unchanged",         test_fix_invalid_all_invalid_returns_unchanged)

    _assert_all_passed()
