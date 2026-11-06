"""Unit tests for the GeoTIFF DEM reader (utils/geotiff.py) on small synthetic files.

Run with:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --factory-startup --python-exit-code 1 -P tests/test_geotiff.py
"""

import math
import os
import struct
import sys
import tempfile
import traceback
from types import SimpleNamespace
from unittest.mock import patch

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
    except Exception:  # noqa: BLE001
        print(f"  FAIL  {name}")
        traceback.print_exc()
        _failed += 1


def _assert_all_passed():
    print(f"\n{'='*60}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"{'='*60}\n")
    if _failed:
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Synthetic GeoTIFF writer
# ---------------------------------------------------------------------------
_EPSG_LV95 = 2056
_EPSG_UTM32N = 32632
_EPSG_LV03 = 21781

def _write_tiff(path, origin, epsg, size=4, scale=1.0, fill=500.0, rgb=False, bigtiff=False, bands=1,
                compression=1, predictor=None, bits_tag=None):
    """Write a little-endian uncompressed one-strip GeoTIFF: a size*size float32
    raster of *fill*, or 8-bit RGB with rgb=True, with its top-left corner at *origin*.
    bands > 1 adds extra interleaved float32 bands of -1 after the *fill* band.
    compression/predictor/bits_tag only change the tags, to exercise the format check:
    the pixel data stays as above, so such a file is only good for header reads."""
    x0, y0 = origin
    if rgb:
        pixels = bytes([200, 150, 100]) * (size * size)
        bits, sample_format, samples, photometric = 8, 1, 3, 2
    else:
        pixel = [fill] + [-1.0] * (bands - 1)
        pixels = struct.pack(f"<{size * size * bands}f", *(pixel * (size * size)))
        bits, sample_format, samples, photometric = 32, 3, bands, 1

    multi_value = samples > 1
    geokeys = (1, 1, 0, 2, 1024, 0, 1, 1, 3072, 0, 1, epsg)
    bits_blob = struct.pack(f"<{samples}H", *([bits] * samples)) if multi_value else b""
    format_blob = struct.pack(f"<{samples}H", *([sample_format] * samples)) if multi_value else b""
    blobs = {
        258: bits_blob,
        339: format_blob,
        33550: struct.pack("<3d", scale, scale, 0.0),
        33922: struct.pack("<6d", 0.0, 0.0, 0.0, x0, y0, 0.0),
        34735: struct.pack(f"<{len(geokeys)}H", *geokeys),
    }

    # (tag, type, count, inline value) -- a None value lives in the out-of-line blobs
    entries = [
        (256, 3, 1, size),
        (257, 3, 1, size),
        (258, 3, samples, None) if multi_value else (258, 3, 1, bits_tag or bits),
        (259, 3, 1, compression),
        (262, 3, 1, photometric),
        (273, 16 if bigtiff else 4, 1, 0),  # strip offset, patched below
        (277, 3, 1, samples),
        (278, 3, 1, size),
        (279, 16 if bigtiff else 4, 1, len(pixels)),
        (339, 3, samples, None) if multi_value else (339, 3, 1, sample_format),
        (33550, 12, 3, None),
        (33922, 12, 6, None),
        (34735, 3, len(geokeys), None),
    ]
    if predictor:
        entries.insert(8, (317, 3, 1, predictor))  # keeps the tags in ascending order

    ifd_offset = 16 if bigtiff else 8
    if bigtiff:
        blob_offset = ifd_offset + 8 + len(entries) * 20 + 8
    else:
        blob_offset = ifd_offset + 2 + len(entries) * 12 + 4
    offsets = {}
    blob_bytes = b""
    for tag, blob in blobs.items():
        if blob:
            offsets[tag] = blob_offset + len(blob_bytes)
            blob_bytes += blob
    pixel_offset = blob_offset + len(blob_bytes)

    ifd = struct.pack("<Q" if bigtiff else "<H", len(entries))
    for tag, typ, count, value in entries:
        if tag == 273:
            value = pixel_offset
        field = offsets[tag] if value is None else value
        ifd += struct.pack("<HHQQ" if bigtiff else "<HHII", tag, typ, count, field)
    ifd += struct.pack("<Q" if bigtiff else "<I", 0)

    header = struct.pack("<HHHQ", 43, 8, 0, ifd_offset) if bigtiff else struct.pack("<HI", 42, ifd_offset)
    with open(path, "wb") as f:
        f.write(b"II" + header + ifd + blob_bytes + pixels)


# ===========================================================================
# LV95 projection
# ===========================================================================

def _dms(deg, minutes, seconds):
    return deg + minutes / 60 + seconds / 3600


def test_lv95_forward_matches_swisstopo_worked_example():
    from TrailPrint3D.utils.geotiff import _latlon_to_lv95

    # The worked example in swisstopo's approximate-formulas document
    easting, northing = _latlon_to_lv95(_dms(46, 2, 38.87), _dms(8, 43, 49.79))
    assert abs(easting - 2699999.76) < 0.5, easting
    assert abs(northing - 1099999.97) < 0.5, northing


def test_lv95_inverse_matches_swisstopo_worked_example():
    from TrailPrint3D.utils.geotiff import _lv95_to_latlon

    lat, lon = _lv95_to_latlon(2700000, 1100000)
    assert abs(lat - _dms(46, 2, 38.86)) < 1e-4, lat
    assert abs(lon - _dms(8, 43, 49.79)) < 1e-4, lon


def test_lv95_round_trip_error_stays_inside_the_tile_bounds_padding():
    from TrailPrint3D.utils.geotiff import _latlon_to_lv95, _lv95_to_latlon

    # The whole LV95 box, corners outside Switzerland included: this disagreement is
    # what geotiff._BOUNDS_PADDING_DEG (about 7.5 m of longitude here) has to absorb.
    for easting in range(2480000, 2840001, 20000):
        for northing in range(1070000, 1300001, 20000):
            back_e, back_n = _latlon_to_lv95(*_lv95_to_latlon(easting, northing))
            assert math.hypot(back_e - easting, back_n - northing) < 5.0, (easting, northing)


# ===========================================================================
# read_geotiff / footprint
# ===========================================================================

def test_lv95_footprint_lands_in_switzerland_and_keeps_corner_order():
    from TrailPrint3D.utils.geotiff import get_geotiff_footprint

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "tile.tif")
        _write_tiff(path, (2620000.0, 1200000.0), _EPSG_LV95, size=4, scale=1000.0)
        tl, tr, br, bl = get_geotiff_footprint(path)

    assert 46.0 < br[0] < tl[0] < 47.5, (tl, br)
    assert 6.0 < tl[1] < tr[1] < 10.5, (tl, tr)
    assert bl[0] < tl[0] and bl[1] < br[1], (bl, tl, br)


def test_lv95_tile_samples_at_a_query_point():
    from TrailPrint3D.utils.geotiff import (
        _lv95_to_latlon,
        dem_contains_point,
        read_geotiff,
        sample_geotiff,
    )

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "tile.tif")
        _write_tiff(path, (2620000.0, 1200000.0), _EPSG_LV95, size=4, scale=1000.0, fill=812.5)
        dem = read_geotiff(path)

    lat, lon = _lv95_to_latlon(2622000.0, 1198000.0)
    assert dem["projection"] == {"crs": "LV95"}
    assert dem_contains_point(dem, lat, lon)
    assert sample_geotiff(dem, lat, lon) == 812.5


def test_utm_tile_still_reads():
    from TrailPrint3D.utils.geotiff import get_geotiff_footprint, read_geotiff

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "tile.tif")
        _write_tiff(path, (500000.0, 5200000.0), _EPSG_UTM32N, size=4, scale=1000.0)
        dem = read_geotiff(path)
        tl = get_geotiff_footprint(path)[0]

    assert dem["projection"] == {"zone": 32, "hemisphere": "N", "ellipsoid": "WGS84"}
    assert abs(tl[1] - 9.0) < 1e-6, tl


def test_bigtiff_reads_like_classic_tiff():
    from TrailPrint3D.utils.geotiff import _lv95_to_latlon, read_geotiff, sample_geotiff

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "big.tif")
        _write_tiff(path, (2620000.0, 1200000.0), _EPSG_LV95, size=4, scale=1000.0, fill=812.5, bigtiff=True)
        header = read_geotiff(path, header_only=True)
        dem = read_geotiff(path)

    assert header["width"] == 4 and header["projection"] == {"crs": "LV95"}
    assert sample_geotiff(dem, *_lv95_to_latlon(2622000.0, 1198000.0)) == 812.5


def test_multiband_raster_reads_band_one():
    from TrailPrint3D.utils.geotiff import read_geotiff

    # swisstopo's swissBEDROCK ships 11 interleaved bands in a BigTIFF
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "bands.tif")
        _write_tiff(path, (2620000.0, 1200000.0), _EPSG_LV95, fill=812.5, bigtiff=True, bands=11)
        grid = read_geotiff(path)["grid"]

    assert grid.min() == grid.max() == 812.5


def test_unsupported_crs_is_rejected_naming_the_code():
    from TrailPrint3D.utils.geotiff import GeoTiffError, read_geotiff

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "tile.tif")
        _write_tiff(path, (620000.0, 200000.0), _EPSG_LV03)
        try:
            read_geotiff(path, header_only=True)
        except GeoTiffError as e:
            assert "EPSG:21781" in str(e)
        else:
            raise AssertionError("expected GeoTiffError")


def test_rgb_image_has_a_footprint_but_refuses_to_be_read_as_elevation():
    from TrailPrint3D.utils.geotiff import GeoTiffError, get_geotiff_footprint, read_geotiff

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "map.tif")
        _write_tiff(path, (2620000.0, 1200000.0), _EPSG_LV95, rgb=True)
        assert len(get_geotiff_footprint(path)) == 4
        try:
            read_geotiff(path)
        except GeoTiffError as e:
            assert "RGB" in str(e)
        else:
            raise AssertionError("expected GeoTiffError")


def test_header_only_on_an_empty_file_raises_geotiff_error():
    from TrailPrint3D.utils.geotiff import GeoTiffError, read_geotiff

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "empty.tif")
        open(path, "wb").close()
        try:
            read_geotiff(path, header_only=True)
        except GeoTiffError:
            return
    raise AssertionError("expected GeoTiffError")


def test_header_only_leaves_the_file_deletable():
    from TrailPrint3D.utils.geotiff import read_geotiff

    # The memory map must be closed by the time read_geotiff returns: Windows refuses
    # to delete a mapped file, so a leak raises from the cleanup here.
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "tile.tif")
        _write_tiff(path, (2620000.0, 1200000.0), _EPSG_LV95)
        header = read_geotiff(path, header_only=True)
        os.remove(path)
    assert header["width"] == 4


# ===========================================================================
# dem_coverage (map overlay + format check)
# ===========================================================================

_LV95_ORIGIN = (2620000.0, 1200000.0)

def test_coverage_of_a_good_file_has_a_footprint_and_no_problem():
    from TrailPrint3D.utils.geotiff import dem_coverage

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "dem.tif")
        _write_tiff(path, _LV95_ORIGIN, _EPSG_LV95)
        coverage = dem_coverage(path)

    assert coverage["path"] == path and coverage["name"] == "dem.tif"
    assert len(coverage["footprint"]) == 4
    assert "problem" not in coverage and "error" not in coverage


def test_coverage_of_a_color_map_has_a_footprint_and_a_problem():
    from TrailPrint3D.utils.geotiff import dem_coverage

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "map.tif")
        _write_tiff(path, _LV95_ORIGIN, _EPSG_LV95, rgb=True)
        coverage = dem_coverage(path)

    assert len(coverage["footprint"]) == 4
    assert "RGB" in coverage["problem"]


def test_coverage_names_unusable_pixel_formats():
    from TrailPrint3D.utils.geotiff import dem_coverage

    cases = [
        ({"compression": 50000}, "compression method 50000"),
        ({"compression": 7}, "compression method 7"),
        ({"predictor": 3}, "predictor 3"),
        ({"bits_tag": 64}, "64-bit"),
    ]
    with tempfile.TemporaryDirectory() as d:
        for i, (tags, expected) in enumerate(cases):
            path = os.path.join(d, f"case{i}.tif")
            _write_tiff(path, _LV95_ORIGIN, _EPSG_LV95, **tags)
            coverage = dem_coverage(path)
            assert expected in coverage.get("problem", ""), (tags, coverage)
            assert len(coverage["footprint"]) == 4, tags


def test_full_read_refuses_what_the_format_check_flags():
    from TrailPrint3D.utils.geotiff import GeoTiffError, read_geotiff

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "zstd.tif")
        _write_tiff(path, _LV95_ORIGIN, _EPSG_LV95, compression=50000)
        try:
            read_geotiff(path)
        except GeoTiffError as e:
            assert "50000" in str(e)
        else:
            raise AssertionError("expected GeoTiffError")


def test_coverage_of_an_unreadable_file_is_an_error_without_geometry():
    from TrailPrint3D.utils.geotiff import dem_coverage

    with tempfile.TemporaryDirectory() as d:
        unsupported = os.path.join(d, "lv03.tif")
        _write_tiff(unsupported, (620000.0, 200000.0), _EPSG_LV03)
        not_a_tiff = os.path.join(d, "notes.tif")
        with open(not_a_tiff, "wb") as f:
            f.write(b"this is not a tiff at all")

        for path, expected in ((unsupported, "EPSG:21781"), (not_a_tiff, "not a TIFF"),
                               (os.path.join(d, "gone.tif"), "")):
            coverage = dem_coverage(path)
            assert expected in coverage["error"], coverage
            assert "footprint" not in coverage and "tiles" not in coverage


def test_coverage_of_a_folder_lists_tiles_and_counts_unusable_ones():
    from TrailPrint3D.utils.geotiff import dem_coverage

    with tempfile.TemporaryDirectory() as d:
        _write_tiff(os.path.join(d, "a.tif"), _LV95_ORIGIN, _EPSG_LV95)
        _write_tiff(os.path.join(d, "b.tif"), (2620010.0, 1200000.0), _EPSG_LV95)
        clean = dem_coverage(d)
        _write_tiff(os.path.join(d, "c.tif"), (2620020.0, 1200000.0), _EPSG_LV95, rgb=True)
        mixed = dem_coverage(d)

    assert len(clean["tiles"]) == 2 and "problem" not in clean
    assert clean["name"].startswith("2 tiles in ")
    assert len(mixed["tiles"]) == 3
    assert mixed["problem"].startswith("1 of 3 tiles"), mixed["problem"]


def test_coverage_of_a_folder_without_tiles_is_an_error():
    from TrailPrint3D.utils.geotiff import dem_coverage

    with tempfile.TemporaryDirectory() as d:
        coverage = dem_coverage(d)
    assert "no readable" in coverage["error"] and "tiles" not in coverage


def test_picker_server_only_reports_coverage_for_the_local_dem_api():
    from TrailPrint3D import picker_server as mp

    saved = dict(mp._dem_path_job)
    try:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "dem.tif")
            _write_tiff(path, _LV95_ORIGIN, _EPSG_LV95)

            mp._dem_path_job.update(path=path, scene=path, api="LOCAL_DEM")
            assert mp._current_dem_coverage()["path"] == path

            mp._dem_path_job.update(api="MAPTERHORN")
            assert mp._current_dem_coverage() is None

            mp._dem_path_job.update(path="", api="LOCAL_DEM")
            assert mp._current_dem_coverage() is None
    finally:
        mp._dem_path_job.update(saved)


def test_picker_server_snapshot_keeps_the_api_current():
    from TrailPrint3D import picker_server as mp

    saved = dict(mp._dem_path_job)
    try:
        mp._dem_path_job.update(api="MAPTERHORN", scene="")
        mp.refresh_state_snapshots(settings_state={"api": "LOCAL_DEM", "demFilePath": ""})
        assert mp._dem_path_job["api"] == "LOCAL_DEM"

        # Pages without an API row (medal holder, backplate) leave it alone
        mp.refresh_state_snapshots(settings_state={"somethingElse": 1})
        assert mp._dem_path_job["api"] == "LOCAL_DEM"
    finally:
        mp._dem_path_job.update(saved)


# ===========================================================================
# Tile folders
# ===========================================================================

def test_tile_folder_has_no_gap_along_a_shared_lv95_edge():
    from TrailPrint3D.utils.geotiff import _lv95_to_latlon, build_tile_index, sample_tile_index

    # Shared north-south edge at E=2600000, the projection's central meridian, where
    # tiles barely rotate: nothing but the padding covers forward/inverse disagreement.
    edge = 2600000.0
    with tempfile.TemporaryDirectory() as d:
        _write_tiff(os.path.join(d, "west.tif"), (edge - 4000.0, 1204000.0), _EPSG_LV95, scale=1000.0, fill=100.0)
        _write_tiff(os.path.join(d, "east.tif"), (edge, 1204000.0), _EPSG_LV95, scale=1000.0, fill=200.0)
        index = build_tile_index(d)
        assert len(index) == 2

        cache = {}
        for north_step in range(40):
            northing = 1200100.0 + north_step * 100.0
            for east_offset in (-1.0, -0.25, 0.0, 0.25, 1.0):
                lat, lon = _lv95_to_latlon(edge + east_offset, northing)
                assert sample_tile_index(index, lat, lon, cache) in (100.0, 200.0), (east_offset, northing)


def _sample_local_dem(dem_path, coords):
    from TrailPrint3D.utils import elevation

    scene = SimpleNamespace(tp3d=SimpleNamespace(demFilePath=dem_path))
    fake_bpy = SimpleNamespace(context=SimpleNamespace(scene=scene))
    with patch.object(elevation, "bpy", fake_bpy), \
            patch.object(elevation._progress.WarningsOverlay, "add_warning") as add_warning:
        return elevation.get_elevation_localDem(coords), add_warning


def test_folder_of_rgb_maps_warns_instead_of_raising():
    from TrailPrint3D.utils.geotiff import _lv95_to_latlon

    coord = _lv95_to_latlon(2620002.0, 1199998.0)
    with tempfile.TemporaryDirectory() as d:
        _write_tiff(os.path.join(d, "map.tif"), (2620000.0, 1200000.0), _EPSG_LV95, rgb=True)
        elevations, add_warning = _sample_local_dem(d, [coord, coord])

    assert elevations == [0.0, 0.0]
    assert add_warning.call_args.args[1] == "error"


def test_single_rgb_map_warns_instead_of_raising():
    from TrailPrint3D.utils.geotiff import _lv95_to_latlon

    coord = _lv95_to_latlon(2620002.0, 1199998.0)
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "map.tif")
        _write_tiff(path, (2620000.0, 1200000.0), _EPSG_LV95, rgb=True)
        elevations, add_warning = _sample_local_dem(path, [coord])

    assert elevations == [0.0]
    assert add_warning.call_args.args[1] == "error"


def test_folder_of_lv95_dems_samples_real_heights():
    from TrailPrint3D.utils.geotiff import _lv95_to_latlon

    coord = _lv95_to_latlon(2620002.0, 1199998.0)
    with tempfile.TemporaryDirectory() as d:
        _write_tiff(os.path.join(d, "dem.tif"), (2620000.0, 1200000.0), _EPSG_LV95, fill=1234.0)
        elevations, add_warning = _sample_local_dem(d, [coord])

    assert elevations == [1234.0]
    add_warning.assert_not_called()


# ===========================================================================
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  TrailPrint3D GeoTIFF reader tests")
    print("=" * 60 + "\n")

    # LV95 projection
    _run("lv95: forward matches swisstopo example",      test_lv95_forward_matches_swisstopo_worked_example)
    _run("lv95: inverse matches swisstopo example",      test_lv95_inverse_matches_swisstopo_worked_example)
    _run("lv95: round trip inside bounds padding",       test_lv95_round_trip_error_stays_inside_the_tile_bounds_padding)

    # read_geotiff / footprint
    _run("footprint: LV95 lands in CH, corners ordered", test_lv95_footprint_lands_in_switzerland_and_keeps_corner_order)
    _run("read: LV95 tile samples at a query point",     test_lv95_tile_samples_at_a_query_point)
    _run("read: UTM tile still reads",                   test_utm_tile_still_reads)
    _run("read: BigTIFF reads like classic TIFF",        test_bigtiff_reads_like_classic_tiff)
    _run("read: multi-band raster reads band 1",         test_multiband_raster_reads_band_one)
    _run("read: unsupported CRS names its EPSG code",    test_unsupported_crs_is_rejected_naming_the_code)
    _run("read: RGB gives footprint, refuses pixels",    test_rgb_image_has_a_footprint_but_refuses_to_be_read_as_elevation)
    _run("read: header_only on empty file raises",       test_header_only_on_an_empty_file_raises_geotiff_error)
    _run("read: header_only releases the file",          test_header_only_leaves_the_file_deletable)

    # dem_coverage
    _run("coverage: good file",                          test_coverage_of_a_good_file_has_a_footprint_and_no_problem)
    _run("coverage: color map draws but has a problem",  test_coverage_of_a_color_map_has_a_footprint_and_a_problem)
    _run("coverage: names unusable pixel formats",       test_coverage_names_unusable_pixel_formats)
    _run("read: refuses what the format check flags",    test_full_read_refuses_what_the_format_check_flags)
    _run("coverage: unreadable file is an error",        test_coverage_of_an_unreadable_file_is_an_error_without_geometry)
    _run("coverage: folder counts unusable tiles",       test_coverage_of_a_folder_lists_tiles_and_counts_unusable_ones)
    _run("coverage: empty folder is an error",           test_coverage_of_a_folder_without_tiles_is_an_error)
    _run("server: coverage only for Local DEM API",      test_picker_server_only_reports_coverage_for_the_local_dem_api)
    _run("server: snapshot keeps the API current",       test_picker_server_snapshot_keeps_the_api_current)

    # Tile folders
    _run("folder: no gap along a shared LV95 edge",     test_tile_folder_has_no_gap_along_a_shared_lv95_edge)
    _run("local DEM: folder of RGB maps warns",          test_folder_of_rgb_maps_warns_instead_of_raising)
    _run("local DEM: single RGB map warns",              test_single_rgb_map_warns_instead_of_raising)
    _run("local DEM: folder of LV95 DEMs samples",       test_folder_of_lv95_dems_samples_real_heights)

    _assert_all_passed()
