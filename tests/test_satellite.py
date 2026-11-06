"""WorldCover crop request and failure handling regression tests.

Run inside Blender with --background --factory-startup --python-exit-code 1
-P tests/test_satellite.py. Add -- --live for a real two-chunk Grand Canyon fetch.
"""

import os
import struct
import sys
import tempfile
import traceback
from types import SimpleNamespace
from unittest.mock import Mock, patch

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from TrailPrint3D.utils import satellite

_passed = 0
_failed = 0
_ITEM = {"id": "ESA_WorldCover_10m_2021_v200_N33W114", "collection": "esa-worldcover"}


def _response(status, content=b"", text=""):
    return Mock(status_code=status, content=content, text=text)


def _crop(path):
    return satellite._render_crop(
        "esa-worldcover", _ITEM, "map", 64, 32,
        35.9, 35.99, -112.2, -112.1, path,
        colormap=satellite._LANDCOVER_PALETTE,
    )


def test_crop_route_payload_and_cache_bytes():
    png = b"\x89PNG\r\n\x1a\n" + b"test payload"
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "crop.png")
        with patch.object(satellite.requests, "post", return_value=_response(200, png)) as post:
            assert _crop(path)
        args, kwargs = post.call_args
        assert args[0].endswith("/item/crop/64x32.png"), args
        assert kwargs["params"]["item"] == _ITEM["id"]
        assert kwargs["params"]["assets"] == "map"
        assert kwargs["params"]["colormap"]
        assert kwargs["json"]["geometry"]["coordinates"][0] == [
            [-112.2, 35.9], [-112.1, 35.9], [-112.1, 35.99],
            [-112.2, 35.99], [-112.2, 35.9],
        ]
        with open(path, "rb") as file:
            assert file.read() == png


def test_permanent_failures_are_not_retried_or_cached():
    for status in (400, 404, 501, 200):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "crop.png")
            response = _response(status, b"not a PNG", "This operation is not supported.")
            with patch.object(satellite.requests, "post", return_value=response) as post, \
                    patch.object(satellite.time, "sleep") as sleep:
                try:
                    _crop(path)
                except satellite.SatelliteFetchError as error:
                    assert str(status) in str(error) if status != 200 else "invalid PNG" in str(error)
                    assert "not supported" in str(error)
                else:
                    raise AssertionError("Failed crop must raise SatelliteFetchError")
                assert post.call_count == 1
                sleep.assert_not_called()
            assert not os.path.exists(path)


def test_transient_failure_retries_then_succeeds():
    png = b"\x89PNG\r\n\x1a\n" + b"test payload"
    with tempfile.TemporaryDirectory() as directory:
        responses = [_response(503, text="temporarily unavailable"), _response(200, png)]
        with patch.object(satellite.requests, "post", side_effect=responses) as post, \
                patch.object(satellite.time, "sleep") as sleep:
            assert _crop(os.path.join(directory, "crop.png"))
            assert post.call_count == 2
            sleep.assert_called_once_with(3)


def test_exhausted_retries_raise_without_final_sleep():
    with tempfile.TemporaryDirectory() as directory:
        with patch.object(satellite.requests, "post", return_value=_response(503, text="outage")) as post, \
                patch.object(satellite.time, "sleep") as sleep:
            try:
                _crop(os.path.join(directory, "crop.png"))
            except satellite.SatelliteFetchError:
                pass
            else:
                raise AssertionError("Exhausted retries must raise")
            assert post.call_count == 3
            assert sleep.call_count == 2


def test_stac_absent_coverage_differs_from_service_failure():
    response = _response(200)
    response.json.return_value = {"features": []}
    with patch.object(satellite.requests, "post", return_value=response):
        assert satellite._stac_search("esa-worldcover", 35.9, 35.99, -112.2, -112.1) is None
    with patch.object(satellite.requests, "post", return_value=_response(501, text="unsupported")) as post:
        try:
            satellite._stac_search("esa-worldcover", 35.9, 35.99, -112.2, -112.1)
        except satellite.SatelliteFetchError:
            pass
        else:
            raise AssertionError("Failed search must not mean absent coverage")
        assert post.call_count == 1


def test_partial_fetch_failure_cannot_be_filled_as_water():
    def fetch(*args, **kwargs):
        if args[3] < 36:
            return "successful-chunk.png"
        raise satellite.SatelliteFetchError("outage")

    with patch.object(satellite, "_get_cached_crop", side_effect=fetch):
        try:
            satellite.get_cached_landcover_image(35.9, 36.1, -112.2, -112.1)
        except satellite.SatelliteFetchError:
            pass
        else:
            raise AssertionError("Failed chunk must not be returned as missing coverage")


def test_prefetch_failure_warns_and_removes_stale_landcover():
    from TrailPrint3D import progress
    from TrailPrint3D.utils.generation import terrain_gen

    gen = SimpleNamespace(
        settings=SimpleNamespace(elementSource="WORLDCOVER"),
        runtime=SimpleNamespace(tbMinLat=35.9, tbMaxLat=36.1, tbMinLon=-112.2, tbMaxLon=-112.1),
        fetch=SimpleNamespace(satelliteThread=None, satelliteResult=None),
    )
    fake_bpy = Mock()
    fake_bpy.context.scene.tp3d.disableCache = True
    fake_bpy.app.debug = False
    with patch.object(terrain_gen, "bpy", fake_bpy), \
            patch.object(terrain_gen, "any_landcover_active", return_value=True), \
            patch.object(progress.ProgressOverlay, "get", return_value=Mock()) as overlay, \
            patch.object(progress.WarningsOverlay, "add_warning") as warn, \
            patch.object(satellite, "remove_stale_satellite_debug") as remove, \
            patch.object(satellite, "get_cached_landcover_image", side_effect=satellite.SatelliteFetchError("HTTP 501")):
        terrain_gen._rg_start_satellite_prefetch(gen)
        gen.fetch.satelliteThread.join(timeout=5)
        assert not gen.fetch.satelliteThread.is_alive()
        assert gen.fetch.satelliteResult["error"] == "HTTP 501"
        terrain_gen._rg_create_satellite_plane(gen)
        warn.assert_called_once()
        assert "HTTP 501" in warn.call_args.args[0]
        remove.assert_called_once()
        overlay.return_value.set_fetch_done.assert_called_once_with("landcover", success=False)


def test_debug_photo_failure_preserves_successful_landcover():
    from TrailPrint3D import progress
    from TrailPrint3D.utils.generation import terrain_gen

    gen = SimpleNamespace(
        settings=SimpleNamespace(elementSource="WORLDCOVER"),
        runtime=SimpleNamespace(tbMinLat=35.9, tbMaxLat=36.1, tbMinLon=-112.2, tbMaxLon=-112.1),
        fetch=SimpleNamespace(satelliteThread=None, satelliteResult=None),
    )
    fake_bpy = Mock()
    fake_bpy.context.scene.tp3d.disableCache = True
    fake_bpy.app.debug = True
    landcover = object()
    with patch.object(terrain_gen, "bpy", fake_bpy), \
            patch.object(terrain_gen, "any_landcover_active", return_value=True), \
            patch.object(progress.ProgressOverlay, "get", return_value=Mock()), \
            patch.object(satellite, "get_cached_landcover_image", return_value=landcover), \
            patch.object(satellite, "get_cached_photo_image", side_effect=satellite.SatelliteFetchError("photo outage")):
        terrain_gen._rg_start_satellite_prefetch(gen)
        gen.fetch.satelliteThread.join(timeout=5)
        assert not gen.fetch.satelliteThread.is_alive()
        assert gen.fetch.satelliteResult["landcover"] is landcover
        assert "error" not in gen.fetch.satelliteResult


def test_live_grand_canyon_crop_and_classification():
    import bpy
    import numpy as np

    with tempfile.TemporaryDirectory() as directory, \
            patch.object(satellite.const, "satellite_cache_dir", directory):
        progress = []
        tiled = satellite.get_cached_landcover_image(
            35.9, 36.1, -112.2, -112.1, disable_cache=True,
            progress_cb=progress.append,
        )
        assert tiled is not None and len(tiled.tiles) == 2
        assert progress[-1] == 1.0
        for tile in tiled.tiles:
            with open(tile["path"], "rb") as file:
                header = file.read(24)
            assert header[:8] == b"\x89PNG\r\n\x1a\n"
            assert struct.unpack(">II", header[16:24]) == (tile["width"], tile["height"])
        image = satellite._load_stitched_image("TP3D_TestLandCover", tiled)
        try:
            pixels = np.empty(tiled.total_width * tiled.total_height * 4, dtype=np.float32)
            image.pixels.foreach_get(pixels)
            rgb = pixels.reshape(tiled.total_height, tiled.total_width, 4)[..., :3] * 255
            classes = satellite._classify_pixels_to_ids(rgb)
            known_fraction = float((classes >= 0).mean())
            assert known_fraction > 0.99, known_fraction
            assert 60 in np.unique(classes), "Expected classified canyon bare/sparse vegetation"
            print(f"  Grand Canyon: {tiled.total_width}x{tiled.total_height}, "
                  f"{known_fraction:.2%} classified, classes={np.unique(classes).tolist()}")
        finally:
            bpy.data.images.remove(image)


_tests = [
    test_crop_route_payload_and_cache_bytes,
    test_permanent_failures_are_not_retried_or_cached,
    test_transient_failure_retries_then_succeeds,
    test_exhausted_retries_raise_without_final_sleep,
    test_stac_absent_coverage_differs_from_service_failure,
    test_partial_fetch_failure_cannot_be_filled_as_water,
    test_prefetch_failure_warns_and_removes_stale_landcover,
    test_debug_photo_failure_preserves_successful_landcover,
]
if "--live" in sys.argv:
    _tests.append(test_live_grand_canyon_crop_and_classification)

for test in _tests:
    try:
        test()
        print(f"  PASS  {test.__name__}")
        _passed += 1
    except Exception:
        traceback.print_exc()
        print(f"  FAIL  {test.__name__}")
        _failed += 1

print(f"\n{_passed} passed, {_failed} failed")
if _failed:
    raise SystemExit(1)
