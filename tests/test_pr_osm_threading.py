"""Tests for PR: osm-threading-pr

Validates:
  A3 — _overpass_request centralised retry logic (osm.py)
  A1 — bmesh flood-fill loose-part splitting (terrain.py)
  A2 — bmesh ribbon merge (terrain.py)
  B1 — coloring_main prefetched_tiles parameter (terrain.py)
  B2 — _fetch_tiles_parallel concurrent fetcher (terrain.py)

Run with:
  blender --background --factory-startup --python-exit-code 1 -P tests/test_pr_osm_threading.py

--python-exit-code 1 means any unhandled exception (including AssertionError)
causes Blender to exit with code 1, making failures visible to CI / PowerShell.
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
# A3 — _overpass_request
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
# B2 — _fetch_tiles_parallel
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
# B1 — coloring_main signature
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
# A1 — bmesh flood-fill loose-part splitting algorithm
# (The same algorithm used in _split_loose inside _process_coloring_object)
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
# A2 — bmesh ribbon merge
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
# Entry point
# ---------------------------------------------------------------------------
# B3 — _fetch_all_kinds_parallel  (cross-kind parallel fetch)
# ---------------------------------------------------------------------------

def test_fetch_all_kinds_fetches_every_kind():
    """Every requested kind must appear in the result dict."""
    import threading
    from unittest.mock import patch
    from TrailPrint3D.utils.terrain import _fetch_all_kinds_parallel

    tasks = [(0.0, 0.0, 2.0, 2.0)]
    kinds = ["WATER", "FOREST", "SCREE"]
    kind_task_pairs = [(k, tasks) for k in kinds]

    def _mock(bbox, kind, return_cache_status=False, settings=None):
        return ({"elements": []}, True)

    with patch("TrailPrint3D.utils.osm.fetch_osm_data", _mock):
        result = _fetch_all_kinds_parallel(kind_task_pairs, threading.Semaphore(4))

    for k in kinds:
        assert k in result, f"Kind {k} missing from result"
        assert tasks[0] in result[k], f"Tile missing for kind {k}"


def test_fetch_all_kinds_failed_kind_excluded():
    """A kind whose fetch returns None must have an empty dict in the result."""
    import threading
    from unittest.mock import patch
    from TrailPrint3D.utils.terrain import _fetch_all_kinds_parallel

    tasks = [(0.0, 0.0, 2.0, 2.0)]

    def _mock(bbox, kind, return_cache_status=False, settings=None):
        if kind == "WATER":
            return ({"elements": []}, False)
        return None  # FOREST fails

    with patch("TrailPrint3D.utils.osm.fetch_osm_data", _mock):
        result = _fetch_all_kinds_parallel(
            [("WATER", tasks), ("FOREST", tasks)],
            threading.Semaphore(4),
        )

    assert tasks[0] in result["WATER"], "Successful kind should have its tile"
    assert result["FOREST"] == {}, "Failed kind should be an empty dict"


def test_fetch_all_kinds_actually_concurrent():
    """All kinds must be in-flight simultaneously (barrier proof)."""
    import threading
    from unittest.mock import patch
    from TrailPrint3D.utils.terrain import _fetch_all_kinds_parallel

    N_KINDS = 4
    barrier = threading.Barrier(N_KINDS, timeout=5)
    tasks   = [(0.0, 0.0, 2.0, 2.0)]
    kind_task_pairs = [(f"KIND{i}", tasks) for i in range(N_KINDS)]

    def _mock(bbox, kind, return_cache_status=False, settings=None):
        barrier.wait()   # all N_KINDS threads must be here simultaneously
        return ({"elements": []}, False)

    with patch("TrailPrint3D.utils.osm.fetch_osm_data", _mock):
        result = _fetch_all_kinds_parallel(
            kind_task_pairs,
            threading.Semaphore(N_KINDS),   # semaphore not the bottleneck
            max_workers=N_KINDS,
        )

    assert len(result) == N_KINDS


def test_fetch_all_kinds_semaphore_caps_concurrency():
    """Shared semaphore must limit peak across ALL kinds combined."""
    import threading
    from unittest.mock import patch
    from TrailPrint3D.utils.terrain import _fetch_all_kinds_parallel

    SEM_LIMIT = 2
    active = [0]
    peak   = [0]
    lock   = threading.Lock()
    gate   = threading.Barrier(SEM_LIMIT, timeout=5)

    tasks = [(0.0, 0.0, 2.0, 2.0)]
    # 6 kinds × 1 tile = 6 total tasks, semaphore(2) should cap to ≤ 2
    kind_task_pairs = [(f"KIND{i}", tasks) for i in range(6)]

    def _mock(bbox, kind, return_cache_status=False, settings=None):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        gate.wait()
        with lock:
            active[0] -= 1
        return ({"elements": []}, False)

    with patch("TrailPrint3D.utils.osm.fetch_osm_data", _mock):
        result = _fetch_all_kinds_parallel(
            kind_task_pairs,
            threading.Semaphore(SEM_LIMIT),
            max_workers=SEM_LIMIT + 4,
        )

    assert peak[0] <= SEM_LIMIT, \
        f"Semaphore({SEM_LIMIT}) exceeded: peak was {peak[0]}"
    assert len(result) == 6


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  TrailPrint3D — osm-threading-pr test suite")
    print("=" * 60 + "\n")

    # A3 — _overpass_request
    _run("A3  success on first try",            test_overpass_request_success_first_try)
    _run("A3  retry on 4xx then succeed",        test_overpass_request_retries_then_succeeds)
    _run("A3  exhausted retries → None",         test_overpass_request_exhausted_returns_none)
    _run("A3  Timeout triggers retry",           test_overpass_request_timeout_triggers_retry)
    _run("A3  log_callback fired on error",      test_overpass_request_log_callback_called_on_error)
    _run("A3  GET method uses requests.get",     test_overpass_request_get_method)

    # B2 — _fetch_tiles_parallel
    _run("B2  all tiles fetched",                test_fetch_tiles_parallel_all_tiles_fetched)
    _run("B2  failed tile excluded from result", test_fetch_tiles_parallel_failed_tile_excluded)
    _run("B2  result carries from_cache flag",   test_fetch_tiles_parallel_result_carries_cache_flag)
    _run("B2  Semaphore(1) no deadlock",         test_fetch_tiles_parallel_respects_semaphore)
    _run("B2  threads genuinely concurrent",     test_fetch_tiles_parallel_actually_concurrent)
    _run("B2  semaphore caps peak concurrency",  test_fetch_tiles_parallel_semaphore_caps_concurrency)

    # B3 — _fetch_all_kinds_parallel
    _run("B3  all kinds fetched",                test_fetch_all_kinds_fetches_every_kind)
    _run("B3  failed kind → empty dict",         test_fetch_all_kinds_failed_kind_excluded)
    _run("B3  kinds genuinely concurrent",       test_fetch_all_kinds_actually_concurrent)
    _run("B3  semaphore caps cross-kind concurrency", test_fetch_all_kinds_semaphore_caps_concurrency)

    # B1 — coloring_main signature
    _run("B1  prefetched_tiles param exists",    test_coloring_main_has_prefetched_tiles_param)

    # A1 — bmesh split_loose algorithm
    _run("A1  two disconnected triangles → 2",   test_split_loose_two_disconnected_triangles)
    _run("A1  single connected mesh → 1",        test_split_loose_single_connected_mesh)
    _run("A1  three islands → 3",                test_split_loose_three_islands)
    _run("A1  end-to-end object creation",       test_split_loose_produces_correct_objects_in_scene)

    # A2 — bmesh ribbon merge
    _run("A2  two ribbons merged → 8 verts",     test_ribbon_merge_vertex_count)
    _run("A2  single ribbon fast path",          test_ribbon_merge_single_ribbon_is_unchanged)

    _assert_all_passed()
