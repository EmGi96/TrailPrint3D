"""Unit and integration tests for the TrailPrint3D OSM data pipeline.

Covers:
  - Overpass HTTP request logic (retry, error handling, GET vs POST)
  - Per-tile parallel fetcher (_fetch_tiles_parallel)
  - Multi-kind parallel fetcher (_fetch_all_kinds_parallel)
  - Union query and element classifier (fetch_osm_combined)
  - coloring_main API surface
  - bmesh flood-fill loose-part splitting
  - bmesh ribbon merge
  - Live Overpass API integration (requires network, ~5-20 s each)

Run with:
  blender --background --factory-startup --python-exit-code 1 -P tests/test_osm_pipeline.py

--python-exit-code 1 exits Blender with code 1 on any unhandled exception
(including AssertionError), making failures visible to CI.
"""

import sys
import os
import traceback

import bpy         # type: ignore  — provided by Blender's Python
import bmesh       # type: ignore
from mathutils import Vector  # type: ignore

# ---------------------------------------------------------------------------
# Path setup — makes TrailPrint3D importable as a package from source
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
        raise SystemExit(1)   # non-zero → Blender exits with code 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code, body=None):
    """Return a mock requests.Response with the given status and JSON body."""
    from unittest.mock import MagicMock
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = body if body is not None else {"elements": []}
    return r


# ---------------------------------------------------------------------------
# Overpass HTTP request — retry and error handling
# ---------------------------------------------------------------------------

def test_overpass_request_success_first_try():
    from unittest.mock import patch
    from TrailPrint3D.utils.osm import _overpass_request

    data = {"elements": [{"type": "node", "id": 1}]}
    resp = _make_response(200, data)
    with patch("TrailPrint3D.utils.osm.requests.post", return_value=resp) as mp, \
         patch("TrailPrint3D.utils.osm.time.sleep"):
        result = _overpass_request("query", "https://example.com", max_retries=3)

    assert result == data, f"Expected data dict, got {result!r}"
    assert mp.call_count == 1, f"Expected 1 POST, got {mp.call_count}"


def test_overpass_request_retries_then_succeeds():
    from unittest.mock import patch
    from TrailPrint3D.utils.osm import _overpass_request

    data = {"elements": []}
    fail = _make_response(429)
    ok   = _make_response(200, data)
    with patch("TrailPrint3D.utils.osm.requests.post", side_effect=[fail, fail, ok]), \
         patch("TrailPrint3D.utils.osm.time.sleep"):
        result = _overpass_request("query", "https://example.com", max_retries=5)

    assert result == data, f"Expected data after retry, got {result!r}"


def test_overpass_request_exhausted_returns_none():
    from unittest.mock import patch
    from TrailPrint3D.utils.osm import _overpass_request

    fail = _make_response(500)
    with patch("TrailPrint3D.utils.osm.requests.post", return_value=fail), \
         patch("TrailPrint3D.utils.osm.time.sleep"):
        result = _overpass_request("query", "https://example.com", max_retries=3)

    assert result is None, f"Expected None after exhausted retries, got {result!r}"


def test_overpass_request_timeout_triggers_retry():
    import requests as _requests
    from unittest.mock import patch
    from TrailPrint3D.utils.osm import _overpass_request

    ok = _make_response(200, {"elements": []})
    with patch("TrailPrint3D.utils.osm.requests.post",
               side_effect=[_requests.exceptions.Timeout, ok]), \
         patch("TrailPrint3D.utils.osm.time.sleep"):
        result = _overpass_request("query", "https://example.com", max_retries=3)

    assert result is not None, "Expected success after one Timeout, got None"


def test_overpass_request_log_callback_called_on_error():
    from unittest.mock import patch
    from TrailPrint3D.utils.osm import _overpass_request

    messages = []
    fail = _make_response(503)
    ok   = _make_response(200, {"elements": []})
    with patch("TrailPrint3D.utils.osm.requests.post", side_effect=[fail, ok]), \
         patch("TrailPrint3D.utils.osm.time.sleep"):
        _overpass_request("query", "https://example.com",
                          max_retries=3, log_callback=messages.append)

    assert messages, "log_callback should have been called on HTTP error"


def test_overpass_request_get_method():
    """method='GET' uses requests.get, not requests.post."""
    from unittest.mock import patch
    from TrailPrint3D.utils.osm import _overpass_request

    data = {"elements": []}
    resp = _make_response(200, data)
    with patch("TrailPrint3D.utils.osm.requests.get", return_value=resp) as mg, \
         patch("TrailPrint3D.utils.osm.requests.post") as mp, \
         patch("TrailPrint3D.utils.osm.time.sleep"):
        result = _overpass_request("query", "https://example.com",
                                   method="GET", max_retries=1)

    assert result == data
    assert mg.call_count == 1, "GET method should use requests.get"
    assert mp.call_count == 0, "GET method should not use requests.post"


# ---------------------------------------------------------------------------
# Per-tile parallel fetcher — _fetch_tiles_parallel
# ---------------------------------------------------------------------------

def test_fetch_tiles_parallel_all_tiles_fetched():
    import threading
    from unittest.mock import patch
    from TrailPrint3D.utils.terrain import _fetch_tiles_parallel

    tasks = [(0.0, 0.0, 2.0, 2.0), (2.0, 0.0, 4.0, 2.0), (4.0, 0.0, 6.0, 2.0)]
    captured = []

    def _mock_fetch(bbox, kind, return_cache_status=False, settings=None):
        captured.append(bbox)
        return ({"elements": []}, True)

    with patch("TrailPrint3D.utils.osm.fetch_osm_data", _mock_fetch):
        sem = threading.Semaphore(2)
        result = _fetch_tiles_parallel(tasks, "WATER", sem)

    assert set(captured) == set(tasks), \
        f"Expected {set(tasks)}, got {set(captured)}"
    assert len(result) == 3, f"Expected 3 results, got {len(result)}"


def test_fetch_tiles_parallel_failed_tile_excluded():
    import threading
    from unittest.mock import patch
    from TrailPrint3D.utils.terrain import _fetch_tiles_parallel

    good = (0.0, 0.0, 2.0, 2.0)
    bad  = (2.0, 0.0, 4.0, 2.0)

    def _mock_fetch(bbox, kind, return_cache_status=False, settings=None):
        if bbox == good:
            return ({"elements": []}, False)
        return None  # simulate failure

    with patch("TrailPrint3D.utils.osm.fetch_osm_data", _mock_fetch):
        sem = threading.Semaphore(2)
        result = _fetch_tiles_parallel([good, bad], "WATER", sem)

    assert good in result, "Successful tile should be in result"
    assert bad not in result, "Failed tile should not be in result"


def test_fetch_tiles_parallel_result_carries_cache_flag():
    import threading
    from unittest.mock import patch
    from TrailPrint3D.utils.terrain import _fetch_tiles_parallel

    bbox = (0.0, 0.0, 2.0, 2.0)

    def _mock_fetch(b, kind, return_cache_status=False, settings=None):
        return ({"elements": []}, True)   # from_cache = True

    with patch("TrailPrint3D.utils.osm.fetch_osm_data", _mock_fetch):
        result = _fetch_tiles_parallel([bbox], "WATER", __import__("threading").Semaphore(1))

    data, from_cache = result[bbox]
    assert from_cache is True


def test_fetch_tiles_parallel_respects_semaphore():
    """Semaphore(1) must limit concurrency — verify no deadlock and correct output."""
    import threading
    from unittest.mock import patch
    from TrailPrint3D.utils.terrain import _fetch_tiles_parallel

    tasks = [(float(i), 0.0, float(i + 2), 2.0) for i in range(6)]

    def _mock_fetch(bbox, kind, return_cache_status=False, settings=None):
        return ({"elements": []}, False)

    with patch("TrailPrint3D.utils.osm.fetch_osm_data", _mock_fetch):
        sem = threading.Semaphore(1)   # strictest rate limit
        result = _fetch_tiles_parallel(tasks, "FOREST", sem)

    assert len(result) == len(tasks), \
        f"Expected {len(tasks)} results with Semaphore(1), got {len(result)}"


def test_fetch_tiles_parallel_actually_concurrent():
    """Prove that multiple fetches genuinely run at the same time.

    Strategy: use a threading.Barrier(3) inside the mock fetch.  The barrier
    only releases when exactly 3 threads call barrier.wait() simultaneously.
    If the pool runs fetches one-at-a-time (sequential), the third thread never
    arrives and barrier.wait() raises BrokenBarrierError — test fails.
    If the pool truly runs at least 3 threads concurrently, all three reach the
    barrier and it releases cleanly — test passes.
    """
    import threading
    from unittest.mock import patch
    from TrailPrint3D.utils.terrain import _fetch_tiles_parallel

    CONCURRENCY_TARGET = 3
    barrier = threading.Barrier(CONCURRENCY_TARGET, timeout=5)
    tasks = [(float(i), 0.0, float(i + 2), 2.0) for i in range(6)]

    def _mock_fetch(bbox, kind, return_cache_status=False, settings=None):
        barrier.wait()   # blocks until CONCURRENCY_TARGET threads are all here
        return ({"elements": []}, False)

    with patch("TrailPrint3D.utils.osm.fetch_osm_data", _mock_fetch):
        sem = threading.Semaphore(CONCURRENCY_TARGET + 1)  # not the bottleneck
        result = _fetch_tiles_parallel(tasks, "WATER", sem, max_workers=CONCURRENCY_TARGET + 1)

    assert len(result) == len(tasks)


def test_fetch_tiles_parallel_semaphore_caps_concurrency():
    """Prove that the semaphore actually limits how many fetches run at once.

    Strategy: track peak in-flight count with an atomic counter.  Each mock
    fetch increments on entry, records the peak, then decrements on exit.
    The semaphore is set to 2, so peak must never exceed 2.
    """
    import threading
    from unittest.mock import patch
    from TrailPrint3D.utils.terrain import _fetch_tiles_parallel

    SEM_LIMIT = 2
    active = [0]          # mutable int (list to allow nonlocal-style mutation)
    peak   = [0]
    lock   = threading.Lock()
    # Barrier forces all threads that get through the semaphore to overlap,
    # so we get a reliable peak reading rather than a lucky sequential one.
    gate   = threading.Barrier(SEM_LIMIT, timeout=5)

    tasks = [(float(i), 0.0, float(i + 2), 2.0) for i in range(6)]

    def _mock_fetch(bbox, kind, return_cache_status=False, settings=None):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        gate.wait()   # wait until SEM_LIMIT threads are in here simultaneously
        with lock:
            active[0] -= 1
        return ({"elements": []}, False)

    with patch("TrailPrint3D.utils.osm.fetch_osm_data", _mock_fetch):
        sem = threading.Semaphore(SEM_LIMIT)
        result = _fetch_tiles_parallel(tasks, "WATER", sem, max_workers=SEM_LIMIT + 2)

    assert peak[0] <= SEM_LIMIT, \
        f"Semaphore({SEM_LIMIT}) should cap concurrency, but peak was {peak[0]}"
    assert len(result) == len(tasks)


# ---------------------------------------------------------------------------
# coloring_main API surface
# ---------------------------------------------------------------------------

def test_coloring_main_has_prefetched_tiles_param():
    import inspect
    from TrailPrint3D.utils.terrain import coloring_main

    sig = inspect.signature(coloring_main)
    assert "prefetched_tiles" in sig.parameters, \
        "coloring_main is missing the prefetched_tiles parameter"
    assert sig.parameters["prefetched_tiles"].default is None, \
        "prefetched_tiles default should be None"


# ---------------------------------------------------------------------------
# bmesh flood-fill — loose-part splitting
# (mirrors the _split_loose algorithm used in _process_coloring_object)
# ---------------------------------------------------------------------------

def _flood_fill_components(bm_src):
    """Return a list of vertex sets, one per connected component.
    Uses Python object identity as the set key, which avoids the .index
    invalidation that occurs when a bmesh is built directly (without a
    to_mesh / from_mesh round-trip).
    """
    visited = set()   # contains BMVert Python objects (by identity)
    components = []
    for start in bm_src.verts:
        if start in visited:
            continue
        comp = set()
        stack = [start]
        while stack:
            v = stack.pop()
            if v in visited:
                continue
            visited.add(v)
            comp.add(v)
            for edge in v.link_edges:
                other = edge.other_vert(v)
                if other not in visited:
                    stack.append(other)
        components.append(comp)
    return components


def test_split_loose_two_disconnected_triangles():
    bm = bmesh.new()
    # Triangle A
    v0 = bm.verts.new((0.0, 0.0, 0.0))
    v1 = bm.verts.new((1.0, 0.0, 0.0))
    v2 = bm.verts.new((0.0, 1.0, 0.0))
    bm.faces.new([v0, v1, v2])
    # Triangle B — completely disconnected (different position, no shared verts)
    v3 = bm.verts.new((10.0, 0.0, 0.0))
    v4 = bm.verts.new((11.0, 0.0, 0.0))
    v5 = bm.verts.new((10.0, 1.0, 0.0))
    bm.faces.new([v3, v4, v5])

    components = _flood_fill_components(bm)
    bm.free()

    assert len(components) == 2, f"Expected 2 components, got {len(components)}"
    sizes = sorted(len(c) for c in components)
    assert sizes == [3, 3], f"Expected component sizes [3,3], got {sizes}"


def test_split_loose_single_connected_mesh():
    bm = bmesh.new()
    v0 = bm.verts.new((0.0, 0.0, 0.0))
    v1 = bm.verts.new((1.0, 0.0, 0.0))
    v2 = bm.verts.new((1.0, 1.0, 0.0))
    v3 = bm.verts.new((0.0, 1.0, 0.0))
    bm.faces.new([v0, v1, v2, v3])

    components = _flood_fill_components(bm)
    bm.free()

    assert len(components) == 1, f"Expected 1 component, got {len(components)}"


def test_split_loose_three_islands():
    bm = bmesh.new()
    for offset in [0.0, 5.0, 10.0]:
        va = bm.verts.new((offset,       0.0, 0.0))
        vb = bm.verts.new((offset + 1.0, 0.0, 0.0))
        vc = bm.verts.new((offset,       1.0, 0.0))
        bm.faces.new([va, vb, vc])

    components = _flood_fill_components(bm)
    bm.free()

    assert len(components) == 3, f"Expected 3 components, got {len(components)}"


def test_split_loose_produces_correct_objects_in_scene():
    """End-to-end: build mesh object → run _split_loose logic → verify N objects."""
    # Build a mesh with two disconnected quads and link it to the scene
    mesh = bpy.data.meshes.new("_test_split_src")
    bm = bmesh.new()
    for x_off in [0.0, 20.0]:   # 20 units apart = definitely disconnected
        va = bm.verts.new((x_off,       0.0, 0.0))
        vb = bm.verts.new((x_off + 1.0, 0.0, 0.0))
        vc = bm.verts.new((x_off + 1.0, 1.0, 0.0))
        vd = bm.verts.new((x_off,       1.0, 0.0))
        bm.faces.new([va, vb, vc, vd])
    bm.to_mesh(mesh)
    bm.free()

    obj = bpy.data.objects.new("_test_split_obj", mesh)
    col = bpy.context.scene.collection
    col.objects.link(obj)

    # --- replicate _split_loose from terrain.py (adapted for object-identity sets) ---
    bm_src = bmesh.new()
    bm_src.from_mesh(obj.data)
    components = _flood_fill_components(bm_src)
    world_matrix = obj.matrix_world.copy()
    parts = []
    for comp_verts in components:
        comp_faces = [f for f in bm_src.faces
                      if all(v in comp_verts for v in f.verts)]
        bm_new = bmesh.new()
        vert_map = {}
        for v in comp_verts:
            nv = bm_new.verts.new(v.co.copy())
            vert_map[v] = nv
        bm_new.verts.ensure_lookup_table()
        for f in comp_faces:
            try:
                bm_new.faces.new([vert_map[v] for v in f.verts])
            except ValueError:
                pass
        new_mesh = bpy.data.meshes.new(obj.name)
        bm_new.to_mesh(new_mesh)
        bm_new.free()
        part = bpy.data.objects.new(obj.name, new_mesh)
        part.matrix_world = world_matrix
        col.objects.link(part)
        parts.append(part)
    bm_src.free()
    bpy.data.objects.remove(obj, do_unlink=True)

    assert len(parts) == 2, f"Expected 2 part objects, got {len(parts)}"
    for p in parts:
        assert len(p.data.vertices) == 4, \
            f"Each part should have 4 verts, got {len(p.data.vertices)}"
        bpy.data.objects.remove(p, do_unlink=True)


# ---------------------------------------------------------------------------
# bmesh ribbon merge
# ---------------------------------------------------------------------------

def _make_quad_object(name, x_offset):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    x = x_offset
    v0 = bm.verts.new((x,       0.0, 0.0))
    v1 = bm.verts.new((x + 1.0, 0.0, 0.0))
    v2 = bm.verts.new((x + 1.0, 1.0, 0.0))
    v3 = bm.verts.new((x,       1.0, 0.0))
    bm.faces.new([v0, v1, v2, v3])
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def test_ribbon_merge_vertex_count():
    """Two 4-vert ribbons merged → 8 verts in the result mesh."""
    r0 = _make_quad_object("_test_ribbon0", 0.0)
    r1 = _make_quad_object("_test_ribbon1", 5.0)

    # --- replicate A2 merge logic from terrain.py ---
    bm_merged = bmesh.new()
    target_world_inv = r0.matrix_world.inverted()
    bm_merged.from_mesh(r0.data)

    bm_part = bmesh.new()
    bm_part.from_mesh(r1.data)
    xform = target_world_inv @ r1.matrix_world
    bmesh.ops.transform(bm_part, verts=bm_part.verts[:], matrix=xform)
    tmp = bpy.data.meshes.new("_test_ribbon_tmp")
    bm_part.to_mesh(tmp)
    bm_part.free()
    bm_merged.from_mesh(tmp)
    bpy.data.meshes.remove(tmp)
    bpy.data.objects.remove(r1, do_unlink=True)

    bm_merged.to_mesh(r0.data)
    bm_merged.free()
    r0.name = "OpenObject_merged"

    assert len(r0.data.vertices) == 8, \
        f"Expected 8 verts after merge, got {len(r0.data.vertices)}"
    assert len(r0.data.polygons) == 2, \
        f"Expected 2 faces after merge, got {len(r0.data.polygons)}"

    bpy.data.objects.remove(r0, do_unlink=True)


def test_ribbon_merge_single_ribbon_is_unchanged():
    """Single ribbon: no merge needed, object renamed and returned as-is."""
    r0 = _make_quad_object("_test_ribbon_single", 0.0)
    original_vert_count = len(r0.data.vertices)

    # Simulate the len == 1 fast path from terrain.py
    valid_ribbons = [r0]
    if len(valid_ribbons) == 1:
        valid_ribbons[0].name = "OpenObject_merged"

    assert r0.name == "OpenObject_merged"
    assert len(r0.data.vertices) == original_vert_count, \
        "Single ribbon should be unchanged"

    bpy.data.objects.remove(r0, do_unlink=True)


# ---------------------------------------------------------------------------
# Live Overpass API integration — real network, no mocks
# ---------------------------------------------------------------------------
# Hits overpass-api.de directly using a small bbox from the München Marathon
# GPX route (English Garden area, ~2×2 km).  Requires internet access.
# Expected duration: 5–20 s each.
#
# Bbox: 48.140–48.160 N, 11.550–11.580 E
# ---------------------------------------------------------------------------

_MUNICH_BBOX = (48.140, 11.550, 48.160, 11.580)  # (south, west, north, east)


def _munich_settings(**overrides):
    """Return an OsmFetchSettings for the Munich integration bbox."""
    from TrailPrint3D.utils.osm import OsmFetchSettings
    defaults = dict(
        disable_cache=True,  # always go to the network; no stale results
        api_retries=2,
        mapsize=5.0,
        road_big=True,
        road_med=True,
        road_small=False,
        water_ponds=True,
        water_small_rivers=True,
        water_big_rivers=True,
    )
    defaults.update(overrides)
    return OsmFetchSettings(**defaults)


def test_real_overpass_union_query():
    """The union QL query must be accepted by the Overpass server and return
    a non-empty elements list for a well-populated urban area (Munich)."""
    import threading
    from TrailPrint3D.utils.osm import fetch_osm_combined

    result = fetch_osm_combined(
        _MUNICH_BBOX,
        ["STREETS", "WATER", "FOREST"],
        settings=_munich_settings(),
        semaphore=threading.Semaphore(1),
    )

    assert result, "Overpass returned no data at all — check network/query syntax"
    print()
    total_elements = 0
    for kind, (data, from_cache) in result.items():
        elems = data.get("elements", [])
        ways = [e for e in elems if e.get("type") != "node"]
        print(f"    {kind:12s}: {len(elems):4d} elements  ({len(ways)} ways/relations)")
        total_elements += len(elems)
    assert total_elements > 0, "All kinds returned empty element lists"


def test_real_overpass_classifier():
    """Every returned way/relation must be binned into the correct kind.
    Central Munich must yield at least one element in each of the three
    requested kinds — STREETS, WATER, and FOREST."""
    import threading
    from TrailPrint3D.utils.osm import fetch_osm_combined, _classify_element

    settings = _munich_settings()
    result = fetch_osm_combined(
        _MUNICH_BBOX,
        ["STREETS", "WATER", "FOREST"],
        settings=settings,
        semaphore=threading.Semaphore(1),
    )

    print()
    for kind in ["STREETS", "WATER", "FOREST"]:
        assert kind in result, (
            f"Kind {kind!r} missing from result — "
            f"got: {list(result.keys())}"
        )
        data, _ = result[kind]
        ways = [e for e in data.get("elements", []) if e.get("type") != "node"]
        assert ways, (
            f"Kind {kind!r} has no ways or relations — classifier may have "
            f"dropped everything.  Check tag filters in _classify_element."
        )
        # Spot-check: every classified way should round-trip through the
        # classifier back to the same kind (no misclassification)
        wrong = [
            e for e in ways
            if _classify_element(e, [kind], settings) != kind
        ]
        assert not wrong, (
            f"{len(wrong)} element(s) in the {kind!r} bucket failed "
            f"round-trip classification.  First offender tags: "
            f"{wrong[0].get('tags', {})}"
        )
        print(f"    {kind:12s}: {len(ways)} ways/relations — all correctly classified")


# ---------------------------------------------------------------------------
# Multi-kind parallel fetcher — _fetch_all_kinds_parallel
# ---------------------------------------------------------------------------

def test_fetch_all_kinds_fetches_every_kind():
    """Every requested kind must appear in the result dict."""
    import threading
    from unittest.mock import patch
    from TrailPrint3D.utils.terrain import _fetch_all_kinds_parallel

    bbox = (0.0, 0.0, 2.0, 2.0)
    kinds = ["WATER", "FOREST", "SCREE"]
    kind_task_pairs = [(k, [bbox]) for k in kinds]

    def _mock(b, ks, settings=None, semaphore=None):
        return {k: ({"elements": []}, True) for k in ks}

    with patch("TrailPrint3D.utils.osm.fetch_osm_combined", _mock):
        result = _fetch_all_kinds_parallel(kind_task_pairs, threading.Semaphore(4))

    for k in kinds:
        assert k in result, f"Kind {k} missing from result"
        assert bbox in result[k], f"Tile missing for kind {k}"


def test_fetch_all_kinds_failed_kind_excluded():
    """A kind whose fetch returns nothing must have an empty dict in the result."""
    import threading
    from unittest.mock import patch
    from TrailPrint3D.utils.terrain import _fetch_all_kinds_parallel

    bbox = (0.0, 0.0, 2.0, 2.0)

    def _mock(b, ks, settings=None, semaphore=None):
        # Return WATER but silently drop FOREST (simulate no matching elements)
        return {k: ({"elements": []}, False) for k in ks if k == "WATER"}

    with patch("TrailPrint3D.utils.osm.fetch_osm_combined", _mock):
        result = _fetch_all_kinds_parallel(
            [("WATER", [bbox]), ("FOREST", [bbox])],
            threading.Semaphore(4),
        )

    assert bbox in result["WATER"], "Successful kind should have its tile"
    assert result["FOREST"] == {}, "Failed kind should be an empty dict"


def test_fetch_all_kinds_actually_concurrent():
    """All tile tasks must be in-flight simultaneously (barrier proof)."""
    import threading
    from unittest.mock import patch
    from TrailPrint3D.utils.terrain import _fetch_all_kinds_parallel

    N_TILES = 4
    barrier = threading.Barrier(N_TILES, timeout=5)
    # Use distinct bboxes so each becomes a separate worker task
    tiles = [(float(i), float(i), float(i) + 1.0, float(i) + 1.0) for i in range(N_TILES)]
    kind_task_pairs = [("FOREST", tiles)]

    def _mock(bbox, kinds, settings=None, semaphore=None):
        barrier.wait()   # all N_TILES threads must arrive here simultaneously
        return {k: ({"elements": []}, False) for k in kinds}

    with patch("TrailPrint3D.utils.osm.fetch_osm_combined", _mock):
        result = _fetch_all_kinds_parallel(
            kind_task_pairs,
            threading.Semaphore(N_TILES),   # semaphore not the bottleneck
            max_workers=N_TILES,
        )

    assert "FOREST" in result
    assert len(result["FOREST"]) == N_TILES


def test_fetch_all_kinds_semaphore_caps_concurrency():
    """Shared semaphore must limit peak concurrent Overpass requests."""
    import threading
    from unittest.mock import patch
    from TrailPrint3D.utils.terrain import _fetch_all_kinds_parallel

    SEM_LIMIT = 2
    active = [0]
    peak   = [0]
    lock   = threading.Lock()
    gate   = threading.Barrier(SEM_LIMIT, timeout=5)

    # 6 distinct tiles × 1 kind = 6 tile tasks; semaphore(2) caps to ≤ 2 concurrent
    tiles = [(float(i), float(i), float(i) + 1.0, float(i) + 1.0) for i in range(6)]
    kind_task_pairs = [("FOREST", tiles)]

    def _mock(bbox, kinds, settings=None, semaphore=None):
        # _fetch_tile holds the semaphore while calling us, so active count
        # directly reflects semaphore occupancy.
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        gate.wait()
        with lock:
            active[0] -= 1
        return {k: ({"elements": []}, False) for k in kinds}

    with patch("TrailPrint3D.utils.osm.fetch_osm_combined", _mock):
        result = _fetch_all_kinds_parallel(
            kind_task_pairs,
            threading.Semaphore(SEM_LIMIT),
            max_workers=SEM_LIMIT + 4,
        )

    assert peak[0] <= SEM_LIMIT, \
        f"Semaphore({SEM_LIMIT}) exceeded: peak was {peak[0]}"
    assert len(result["FOREST"]) == 6


# ---------------------------------------------------------------------------
# Coastline pipeline — _stitch_coastline_chains, _close_chain_with_bbox,
# _build_ocean_mesh, fetch_coastline_ways
# ---------------------------------------------------------------------------

# Bbox for "Coastline Check" GPX (Gävle coast, Sweden)
_COASTLINE_BBOX = (60.6419, 17.1906, 60.7008, 17.3296)  # (south, west, north, east)


def test_stitch_empty_input():
    from TrailPrint3D.utils.terrain import _stitch_coastline_chains
    open_chains, closed_loops = _stitch_coastline_chains([])
    assert open_chains == [] and closed_loops == [], "Empty input must return two empty lists"


def test_stitch_already_closed_single_way():
    """A single way whose first ≈ last point is classified as a closed loop."""
    from TrailPrint3D.utils.terrain import _stitch_coastline_chains
    ring = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]
    open_chains, closed_loops = _stitch_coastline_chains([ring])
    assert len(closed_loops) == 1, f"Expected 1 closed loop, got {len(closed_loops)}"
    assert len(open_chains) == 0, f"Expected 0 open chains, got {len(open_chains)}"


def test_stitch_two_fragments_join():
    """Two abutting fragments must be merged into one open chain."""
    from TrailPrint3D.utils.terrain import _stitch_coastline_chains
    a = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)]   # ends at (2,0)
    b = [(2.0, 0.0), (3.0, 0.0), (4.0, 0.0)]   # starts at (2,0)
    open_chains, closed_loops = _stitch_coastline_chains([a, b])
    assert len(open_chains) == 1, f"Expected 1 merged chain, got {len(open_chains)}"
    assert len(closed_loops) == 0
    merged = open_chains[0]
    assert len(merged) == 5, f"Merged chain should have 5 pts, got {len(merged)}"
    assert merged[0] == (0.0, 0.0) and merged[-1] == (4.0, 0.0)


def test_stitch_reversed_fragment_joins():
    """Fragment B reversed (end meets A's end) must still merge."""
    from TrailPrint3D.utils.terrain import _stitch_coastline_chains
    a = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)]
    b = [(4.0, 0.0), (3.0, 0.0), (2.0, 0.0)]   # reversed: end=(2,0) matches a's end
    open_chains, closed_loops = _stitch_coastline_chains([a, b])
    assert len(open_chains) == 1, f"Expected 1 merged chain, got {len(open_chains)}"
    merged = open_chains[0]
    assert merged[0] == (0.0, 0.0) and merged[-1] == (4.0, 0.0), \
        f"Unexpected endpoints: {merged[0]} … {merged[-1]}"


def test_stitch_three_fragments_chain():
    """Three sequential fragments must reduce to one open chain."""
    from TrailPrint3D.utils.terrain import _stitch_coastline_chains
    a = [(0.0, 0.0), (1.0, 0.0)]
    b = [(1.0, 0.0), (2.0, 0.0)]
    c = [(2.0, 0.0), (3.0, 0.0)]
    open_chains, closed_loops = _stitch_coastline_chains([a, b, c])
    assert len(open_chains) == 1, f"Expected 1 chain, got {len(open_chains)}"
    assert len(open_chains[0]) == 4, \
        f"Expected 4 pts after stitching 3 fragments, got {len(open_chains[0])}"


def test_stitch_disjoint_chains_stay_separate():
    """Two chains with no shared endpoints must remain two open chains."""
    from TrailPrint3D.utils.terrain import _stitch_coastline_chains
    a = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)]
    b = [(10.0, 0.0), (11.0, 0.0), (12.0, 0.0)]
    open_chains, closed_loops = _stitch_coastline_chains([a, b])
    assert len(open_chains) == 2, f"Expected 2 separate chains, got {len(open_chains)}"


def test_stitch_fragments_form_closed_ring():
    """Two fragments that together form a closed ring are classified as a loop."""
    from TrailPrint3D.utils.terrain import _stitch_coastline_chains
    a = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
    b = [(1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]
    open_chains, closed_loops = _stitch_coastline_chains([a, b])
    assert len(closed_loops) == 1, f"Expected 1 closed loop, got {len(closed_loops)}"
    assert len(open_chains) == 0


def test_close_chain_with_bbox_returns_polygon():
    """An open chain crossing two sides of the bbox must produce a closed polygon."""
    from TrailPrint3D.utils.terrain import _close_chain_with_bbox
    # Chain runs W→E across the bottom of a 10×10 bbox
    chain = [(-5.0, -5.0), (0.0, -5.0), (5.0, -5.0)]
    bbox  = (-5.0, -5.0, 5.0, 5.0)
    poly  = _close_chain_with_bbox(chain, bbox)
    assert poly is not None, "_close_chain_with_bbox returned None"
    assert len(poly) >= 3, f"Polygon needs at least 3 pts, got {len(poly)}"


def test_build_ocean_mesh_no_chains_returns_bbox_rect():
    """No open chains → tile is fully ocean → returns the full bbox rectangle."""
    from TrailPrint3D.utils.terrain import _build_ocean_mesh

    # Build a tiny dummy tile object for location reference
    mesh = bpy.data.meshes.new("_test_ocean_tile")
    tile = bpy.data.objects.new("_test_ocean_tile", mesh)
    bpy.context.scene.collection.objects.link(tile)
    tile.location = (0, 0, 0)

    bbox_bl = (-5.0, -5.0, 5.0, 5.0)
    ocean = _build_ocean_mesh([], [], bbox_bl, tile)

    bpy.data.objects.remove(tile, do_unlink=True)

    assert ocean is not None, "_build_ocean_mesh should return a rect when no chains given"
    assert len(ocean.data.vertices) == 4, \
        f"Full-bbox rect should have 4 verts, got {len(ocean.data.vertices)}"
    assert len(ocean.data.polygons) == 1, \
        f"Full-bbox rect should have 1 face, got {len(ocean.data.polygons)}"
    bpy.data.objects.remove(ocean, do_unlink=True)


def test_build_ocean_mesh_open_chain_produces_polygon():
    """One open chain → polygon built and linked as a mesh object."""
    from TrailPrint3D.utils.terrain import _build_ocean_mesh

    mesh = bpy.data.meshes.new("_test_ocean_tile2")
    tile = bpy.data.objects.new("_test_ocean_tile2", mesh)
    bpy.context.scene.collection.objects.link(tile)
    tile.location = (0, 0, 0)

    bbox_bl = (-5.0, -5.0, 5.0, 5.0)
    # Chain runs along bottom edge W→E (land-is-left means ocean is below/south)
    chain = [(-5.0, -5.0), (0.0, -5.0), (5.0, -5.0)]
    ocean = _build_ocean_mesh([chain], [], bbox_bl, tile)

    bpy.data.objects.remove(tile, do_unlink=True)

    assert ocean is not None, "_build_ocean_mesh with one chain should return an object"
    assert len(ocean.data.vertices) >= 3, \
        f"Ocean polygon needs ≥3 verts, got {len(ocean.data.vertices)}"
    bpy.data.objects.remove(ocean, do_unlink=True)


def test_fetch_coastline_ways_empty_prefetch():
    """Empty prefetch dict returns empty chain list without error."""
    from TrailPrint3D.utils.osm import fetch_coastline_ways
    result = fetch_coastline_ways({}, scaleHor=1.0)
    assert result == [], f"Expected [], got {result!r}"


def test_fetch_coastline_ways_extracts_chains():
    """fetch_coastline_ways returns one chain per way with correct point count."""
    from TrailPrint3D.utils.osm import fetch_coastline_ways

    # Minimal synthetic Overpass response: 1 coastline way with 3 nodes
    data = {
        "elements": [
            {"type": "node", "id": 1, "lat": 60.64, "lon": 17.20},
            {"type": "node", "id": 2, "lat": 60.65, "lon": 17.22},
            {"type": "node", "id": 3, "lat": 60.66, "lon": 17.24},
            {
                "type": "way",
                "id": 101,
                "nodes": [1, 2, 3],
                "tags": {"natural": "coastline"},
            },
        ]
    }
    prefetched = {(60.64, 17.20, 60.66, 17.24): (data, False)}
    chains = fetch_coastline_ways(prefetched, scaleHor=1.0)

    assert len(chains) == 1, f"Expected 1 chain, got {len(chains)}"
    assert len(chains[0]) == 3, f"Expected 3 pts, got {len(chains[0])}"


def test_fetch_coastline_ways_ignores_non_coastline_tags():
    """Ways with tags other than natural=coastline are silently ignored."""
    from TrailPrint3D.utils.osm import fetch_coastline_ways

    data = {
        "elements": [
            {"type": "node", "id": 1, "lat": 60.64, "lon": 17.20},
            {"type": "node", "id": 2, "lat": 60.65, "lon": 17.22},
            {
                "type": "way",
                "id": 200,
                "nodes": [1, 2],
                "tags": {"natural": "water"},   # NOT coastline
            },
        ]
    }
    prefetched = {(60.64, 17.20, 60.65, 17.22): (data, False)}
    chains = fetch_coastline_ways(prefetched, scaleHor=1.0)
    assert chains == [], f"Non-coastline way should be ignored, got {chains!r}"


def test_fetch_coastline_ways_deduplicates_across_tiles():
    """The same way_id appearing in two overlapping tiles is only returned once."""
    from TrailPrint3D.utils.osm import fetch_coastline_ways

    nodes = [
        {"type": "node", "id": 1, "lat": 60.64, "lon": 17.20},
        {"type": "node", "id": 2, "lat": 60.65, "lon": 17.22},
    ]
    way = {
        "type": "way",
        "id": 999,
        "nodes": [1, 2],
        "tags": {"natural": "coastline"},
    }
    tile_a = {(60.64, 17.20, 60.65, 17.22): ({"elements": nodes + [way]}, False)}
    tile_b = {(60.64, 17.22, 60.65, 17.24): ({"elements": nodes + [way]}, True)}
    prefetched = {**tile_a, **tile_b}

    chains = fetch_coastline_ways(prefetched, scaleHor=1.0)
    assert len(chains) == 1, \
        f"Duplicate way_id should only produce 1 chain, got {len(chains)}"


# ---------------------------------------------------------------------------
# Live coastline integration — real network, Gävle coast (Sweden)
# ---------------------------------------------------------------------------

def test_real_coastline_fetch_returns_ways():
    """Overpass must return at least one natural=coastline way for the Gävle bbox."""
    import threading
    from TrailPrint3D.utils.osm import fetch_osm_combined, OsmFetchSettings

    settings = OsmFetchSettings(
        disable_cache=True, api_retries=2, mapsize=10.0,
        road_big=False, road_med=False, road_small=False,
        water_ponds=False, water_small_rivers=False, water_big_rivers=False,
    )
    result = fetch_osm_combined(
        _COASTLINE_BBOX, ["COASTLINE"],
        settings=settings,
        semaphore=threading.Semaphore(1),
    )

    assert "COASTLINE" in result, "COASTLINE kind missing from result"
    data, _ = result["COASTLINE"]
    ways = [e for e in data.get("elements", []) if e.get("type") == "way"]
    print(f"\n    coastline ways returned: {len(ways)}")
    assert ways, "No coastline ways returned — check Overpass query or bbox"


def test_real_coastline_stitch_and_polygon():
    """End-to-end: fetch → stitch → close with bbox → polygon has ≥3 vertices."""
    import math
    import threading
    from TrailPrint3D.utils.osm import fetch_osm_combined, fetch_coastline_ways, OsmFetchSettings
    from TrailPrint3D.utils.terrain import _stitch_coastline_chains, _close_chain_with_bbox
    from TrailPrint3D import constants as const

    settings = OsmFetchSettings(
        disable_cache=True, api_retries=2, mapsize=10.0,
        road_big=False, road_med=False, road_small=False,
        water_ponds=False, water_small_rivers=False, water_big_rivers=False,
    )
    result = fetch_osm_combined(
        _COASTLINE_BBOX, ["COASTLINE"],
        settings=settings,
        semaphore=threading.Semaphore(1),
    )

    scaleHor = 1.0

    def _ll_to_bl(lat, lon):
        x = const.R * math.radians(lon) * scaleHor
        y = const.R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * scaleHor
        return (x, y)

    prefetched = {_COASTLINE_BBOX: result.get("COASTLINE", ({}, False))}
    raw_chains = fetch_coastline_ways(prefetched, scaleHor=scaleHor)
    print(f"\n    raw ways: {len(raw_chains)}")
    assert raw_chains, "No raw chains from fetch_coastline_ways"

    open_chains, closed_loops = _stitch_coastline_chains(raw_chains)
    print(f"    open chains: {len(open_chains)}  closed loops: {len(closed_loops)}")

    # Compute the tile bbox using the same inline Mercator formula as createOcean
    s, w, n, e = _COASTLINE_BBOX
    sw = _ll_to_bl(s, w)
    ne = _ll_to_bl(n, e)
    bbox_bl = (min(sw[0], ne[0]), min(sw[1], ne[1]), max(sw[0], ne[0]), max(sw[1], ne[1]))
    print(f"    bbox_bl: x=[{bbox_bl[0]:.3f}, {bbox_bl[2]:.3f}]  y=[{bbox_bl[1]:.3f}, {bbox_bl[3]:.3f}]")

    # Every open chain must produce a valid closed polygon when combined with bbox
    polys_built = 0
    for i, chain in enumerate(open_chains):
        poly = _close_chain_with_bbox(chain, bbox_bl)
        assert poly is not None, f"open_chain[{i}] produced None polygon"
        assert len(poly) >= 3, \
            f"open_chain[{i}] polygon has only {len(poly)} pts (need ≥3)"
        polys_built += 1
        print(f"    open_chain[{i}]: {len(chain)} pts → polygon {len(poly)} pts")

    # At minimum the stitch must have produced something processable
    total = len(open_chains) + len(closed_loops)
    assert total > 0, "Stitch produced no chains at all"
    print(f"    polygons built: {polys_built}")


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  TrailPrint3D OSM pipeline tests")
    print("=" * 60 + "\n")

    # Overpass HTTP request
    _run("overpass request: success on first try",        test_overpass_request_success_first_try)
    _run("overpass request: retry on 4xx then succeed",   test_overpass_request_retries_then_succeeds)
    _run("overpass request: exhausted retries → None",    test_overpass_request_exhausted_returns_none)
    _run("overpass request: Timeout triggers retry",      test_overpass_request_timeout_triggers_retry)
    _run("overpass request: log_callback fired on error", test_overpass_request_log_callback_called_on_error)
    _run("overpass request: GET method uses requests.get",test_overpass_request_get_method)

    # Per-tile parallel fetcher
    _run("tile fetcher: all tiles fetched",               test_fetch_tiles_parallel_all_tiles_fetched)
    _run("tile fetcher: failed tile excluded from result",test_fetch_tiles_parallel_failed_tile_excluded)
    _run("tile fetcher: result carries from_cache flag",  test_fetch_tiles_parallel_result_carries_cache_flag)
    _run("tile fetcher: Semaphore(1) no deadlock",        test_fetch_tiles_parallel_respects_semaphore)
    _run("tile fetcher: threads genuinely concurrent",    test_fetch_tiles_parallel_actually_concurrent)
    _run("tile fetcher: semaphore caps peak concurrency", test_fetch_tiles_parallel_semaphore_caps_concurrency)

    # Multi-kind parallel fetcher
    _run("kind fetcher: all kinds fetched",               test_fetch_all_kinds_fetches_every_kind)
    _run("kind fetcher: failed kind → empty dict",        test_fetch_all_kinds_failed_kind_excluded)
    _run("kind fetcher: kinds genuinely concurrent",      test_fetch_all_kinds_actually_concurrent)
    _run("kind fetcher: semaphore caps concurrency",      test_fetch_all_kinds_semaphore_caps_concurrency)

    # coloring_main API
    _run("coloring_main: prefetched_tiles param exists",  test_coloring_main_has_prefetched_tiles_param)

    # bmesh flood-fill
    _run("split loose: two disconnected triangles → 2",   test_split_loose_two_disconnected_triangles)
    _run("split loose: single connected mesh → 1",        test_split_loose_single_connected_mesh)
    _run("split loose: three islands → 3",                test_split_loose_three_islands)
    _run("split loose: end-to-end object creation",       test_split_loose_produces_correct_objects_in_scene)

    # bmesh ribbon merge
    _run("ribbon merge: two ribbons merged → 8 verts",    test_ribbon_merge_vertex_count)
    _run("ribbon merge: single ribbon fast path",         test_ribbon_merge_single_ribbon_is_unchanged)

    # Live Overpass integration (network required)
    _run("live overpass: union query accepted by server", test_real_overpass_union_query)
    _run("live overpass: classifier bins Munich elements",test_real_overpass_classifier)

    # Coastline pipeline — unit tests (no network, no bpy objects)
    _run("coastline stitch: empty input",                         test_stitch_empty_input)
    _run("coastline stitch: already-closed single way",           test_stitch_already_closed_single_way)
    _run("coastline stitch: two abutting fragments merge",        test_stitch_two_fragments_join)
    _run("coastline stitch: reversed fragment merges",            test_stitch_reversed_fragment_joins)
    _run("coastline stitch: three sequential fragments → one",    test_stitch_three_fragments_chain)
    _run("coastline stitch: disjoint chains stay separate",       test_stitch_disjoint_chains_stay_separate)
    _run("coastline stitch: two halves form closed ring",         test_stitch_fragments_form_closed_ring)
    _run("coastline bbox close: returns valid polygon",           test_close_chain_with_bbox_returns_polygon)
    _run("coastline mesh: no chains → full bbox rect",            test_build_ocean_mesh_no_chains_returns_bbox_rect)
    _run("coastline mesh: open chain → polygon object",           test_build_ocean_mesh_open_chain_produces_polygon)
    _run("fetch_coastline_ways: empty prefetch → []",             test_fetch_coastline_ways_empty_prefetch)
    _run("fetch_coastline_ways: extracts chains correctly",       test_fetch_coastline_ways_extracts_chains)
    _run("fetch_coastline_ways: ignores non-coastline tags",      test_fetch_coastline_ways_ignores_non_coastline_tags)
    _run("fetch_coastline_ways: deduplicates across tiles",       test_fetch_coastline_ways_deduplicates_across_tiles)

    # Live coastline integration (network required, Gävle coast)
    _run("live coastline: overpass returns ways",                 test_real_coastline_fetch_returns_ways)
    _run("live coastline: stitch + polygon end-to-end",           test_real_coastline_stitch_and_polygon)

    _assert_all_passed()
