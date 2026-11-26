"""Post-processing page backend tests (TrailPrint3D/post_process.py).

Run with:
  blender --background --factory-startup --python-exit-code 1 -P tests/test_post_process.py
"""

import json
import math
import os
import struct
import sys
import tempfile
import traceback

import bmesh
import bpy

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

if "TrailPrint3D" not in bpy.context.preferences.addons:
    bpy.ops.preferences.addon_enable(module="TrailPrint3D")

from TrailPrint3D import picker_server, post_process, temp  # noqa: E402

_SAMPLES = os.path.join(_REPO_ROOT, "TrailPrint3D", "sample_files")
_TMP = tempfile.TemporaryDirectory()
_passed = 0
_failed = 0


def _run(name, fn):
    global _passed, _failed
    _clear_scene()
    try:
        fn()
        print(f"  PASS  {name}")
        _passed += 1
    except Exception:
        print(f"  FAIL  {name}")
        traceback.print_exc()
        _failed += 1


def _clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    bpy.context.scene.tp3d.currentMap = None


def _material(name, rgb):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    return mat


def _terrain_map(name="PPMap", size=100.0, offset=(0.0, 0.0), shape="SQUARE"):
    """A manifold block: a 24x24 grid with a hill on top, extruded down to a
    flat bottom at z=0 -- enough like a generated map for every tool."""
    half = size / 2
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=24, y_segments=24, size=half)
    for v in bm.verts:
        r2 = (v.co.x ** 2 + v.co.y ** 2) / (size * 0.3) ** 2
        v.co.z = 3.0 + 12.0 * math.exp(-r2)
    ret = bmesh.ops.extrude_face_region(bm, geom=list(bm.faces))
    for v in [e for e in ret["geom"] if isinstance(e, bmesh.types.BMVert)]:
        v.co.z = 0.0
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.materials.append(_material("BASE", (0.1, 0.5, 0.1)))
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = (offset[0], offset[1], 0.0)
    obj["Object type"] = "MAP"
    obj["objType"] = "MAP"
    obj["Shape"] = shape
    obj["shapeRotation"] = 0
    obj["objSize"] = size
    obj["minThickness"] = 3.0
    obj["elementMode"] = "PAINT"
    obj["BottomMark"] = False
    obj["outline_wkt"] = (
        f"POLYGON (({-half} {-half}, {half} {-half}, {half} {half}, {-half} {half}, {-half} {-half}))"
    )
    bpy.context.view_layer.update()
    return obj


def _element(name, map_obj):
    bpy.ops.mesh.primitive_cube_add(size=4, location=(map_obj.location.x + 5, map_obj.location.y, 16))
    obj = bpy.context.object
    obj.name = name
    obj["Object type"] = "TRAIL"
    obj.data.materials.append(_material("TRAIL", (0.8, 0.1, 0.1)))
    return obj


def _select(objs):
    for o in bpy.context.selected_objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0] if objs else None


def _session(maps):
    return post_process.PostProcessSession([m.name for m in maps])


def _messages_text(result):
    return " | ".join(m["text"] for m in result.get("messages", []))


# ── Targets ──────────────────────────────────────────────────────────────


def test_find_page_maps_selection_and_fallback():
    a = _terrain_map("TileA")
    b = _terrain_map("TileB", offset=(120, 0))
    piece = _terrain_map("Piece", offset=(400, 0))
    piece["PuzzleRow"] = 0
    piece["PuzzleCol"] = 0
    cube = bpy.data.objects.new("Plain", bpy.data.meshes.new("Plain"))
    bpy.context.scene.collection.objects.link(cube)

    _select([a, b, piece])
    assert [o.name for o in post_process.find_page_maps(bpy.context)] == ["TileA", "TileB"]

    _select([piece])  # puzzle pieces aren't maps...
    assert post_process.find_page_maps(bpy.context) == []
    # ...but they open the page in puzzle mode (Text and SVG only) -- the
    # puzzle generators' hand-off goes through this poll.
    assert bpy.ops.tp3d.post_process_generator.poll()

    bpy.context.scene.tp3d.currentMap = a  # ...but the last generated map does
    assert [o.name for o in post_process.find_page_maps(bpy.context)] == ["TileA"]
    assert bpy.ops.tp3d.post_process_generator.poll()

    bpy.context.scene.tp3d.currentMap = piece  # a puzzle as last generation
    assert post_process.find_page_maps(bpy.context) == []


def test_find_page_maps_when_last_map_pointer_is_empty():
    from TrailPrint3D.props import LAST_MAP_STAMP_PROP

    tp3d = bpy.context.scene.tp3d
    a = _terrain_map("TileA")
    _select([])
    tp3d.currentMap = None
    # The only map in the scene is the target.
    assert [o.name for o in post_process.find_page_maps(bpy.context)] == ["TileA"]
    assert bpy.ops.tp3d.post_process_generator.poll()

    b = _terrain_map("TileB", offset=(120, 0))
    tp3d.currentMap = a
    tp3d.currentMap = b  # generated last
    assert LAST_MAP_STAMP_PROP in a and LAST_MAP_STAMP_PROP in b
    tp3d.currentMap = None
    assert [o.name for o in post_process.find_page_maps(bpy.context)] == ["TileB"]

    # Last map deleted: the newest one left takes over.
    bpy.data.objects.remove(b, do_unlink=True)
    assert [o.name for o in post_process.find_page_maps(bpy.context)] == ["TileA"]

    # Several unstamped maps (older files): no guessing.
    _terrain_map("TileC", offset=(240, 0))
    del a[LAST_MAP_STAMP_PROP]
    assert post_process.find_page_maps(bpy.context) == []


def test_companions_by_footprint():
    a = _terrain_map("TileA")
    trail = _element("TrailA", a)
    other = _terrain_map("TileB", offset=(300, 0))
    _element("TrailB", other)
    plain = bpy.data.objects.new("NotOurs", bpy.data.meshes.new("NotOurs"))
    bpy.context.scene.collection.objects.link(plain)
    names = {o.name for o in post_process.find_companions(bpy.context, [a])}
    assert names == {trail.name}, names


def test_info_and_glb():
    a = _terrain_map("TileA")
    _element("TrailA", a)
    _select([a])
    session = _session([a])
    info = session.info(bpy.context)
    assert len(info["maps"]) == 1
    m = info["maps"][0]
    assert m["shape"] == "SQUARE" and m["dovetailReason"] is None
    assert len(m["dovetailSpots"]) == 4, m["dovetailSpots"]
    # Spots sit on the square's edges (|x| or |y| == 50).
    for s in m["dovetailSpots"]:
        assert abs(max(abs(s["x"]), abs(s["y"])) - 50) < 1e-3, s
    assert m["colorRange"][1] > m["colorRange"][0]
    assert m["bottomMark"] is False
    assert info["premium"] == bool(temp.PREMIUMVERSION)
    json.dumps(info)  # must be JSON-serialisable for the status endpoint

    path = os.path.join(_TMP.name, "pp.glb")
    tris = session.write_glb(bpy.context, path)
    assert tris > 0
    with open(path, "rb") as f:
        magic, version, total = struct.unpack("<III", f.read(12))
        jlen, jtype = struct.unpack("<II", f.read(8))
        gltf = json.loads(f.read(jlen))
    assert magic == 0x46546C67 and version == 2 and total == os.path.getsize(path)
    roles = sorted(n["extras"]["role"] for n in gltf["nodes"])
    assert roles == ["map", "trail"], roles
    prim = gltf["meshes"][0]["primitives"][0]
    assert set(prim["attributes"]) == {"POSITION", "COLOR_0"}
    pos = gltf["accessors"][prim["attributes"]["POSITION"]]
    assert pos["count"] % 3 == 0 and pos["max"][2] > 14


def test_glb_samples_paint_texture():
    a = _terrain_map("TileA")
    mesh = a.data
    uv = mesh.uv_layers.new(name="UVMap")
    for loop in uv.data:
        loop.uv = (0.75, 0.25)  # right half of a 2x1 image
    image = bpy.data.images.new(f"{mesh.name}_MMU_Paint", width=2, height=1)
    image.pixels.foreach_set([1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0])  # red | blue
    mat = bpy.data.materials.new("PaintTex")
    mat.use_nodes = True
    tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = image
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    mesh.materials.clear()
    mesh.materials.append(mat)
    path = os.path.join(_TMP.name, "tex.glb")
    post_process.write_scene_glb(path, [(a, {"role": "map"})], bpy.context.evaluated_depsgraph_get())
    with open(path, "rb") as f:
        f.read(12)
        jlen, _t = struct.unpack("<II", f.read(8))
        gltf = json.loads(f.read(jlen))
        f.read(8)
        binary = f.read()
    acc = gltf["accessors"][1]
    view = gltf["bufferViews"][acc["bufferView"]]
    first = struct.unpack_from("<4H", binary, view["byteOffset"])
    assert first == (0, 0, 65535, 65535), first  # blue, from the texture


def test_operator_proxy_collects_reports():
    from TrailPrint3D import operators

    a = _terrain_map("TileA")
    a["MagnetHoles"] = True
    _select([a])
    proxy = post_process.OperatorProxy(operators.TP3D_OT_magnet_holes)
    result = proxy.execute(bpy.context)
    assert result == {"CANCELLED"}
    assert any("already has magnet holes" in r["text"] for r in proxy.reports), proxy.reports


# ── Apply + undo ─────────────────────────────────────────────────────────


def test_magnets_apply_and_undo():
    a = _terrain_map("TileA")
    trail_name = _element("TrailA", a).name
    bpy.context.scene.tp3d.currentMap = a
    _select([a])
    n_verts = len(a.data.vertices)
    session = _session([a])

    result = session.apply(bpy.context, "magnets", {"diameter": 6, "depth": 2, "margin": 3, "count": 3})
    assert result["rebuild"] and result["messages"][0]["level"] == "INFO", _messages_text(result)
    a = bpy.data.objects["TileA"]
    assert a.get("MagnetHoles") and len(a.data.vertices) > n_verts
    assert bpy.context.scene.tp3d.magnetDiameter == 6
    assert session.info(bpy.context)["undo"] == ["Magnet Holes"]

    # Something the user adds in Blender meanwhile must survive the undo.
    extra = bpy.data.objects.new("UserAdded", bpy.data.meshes.new("UserAdded"))
    bpy.context.scene.collection.objects.link(extra)

    result = session.undo(bpy.context)
    assert result["rebuild"], _messages_text(result)
    a = bpy.data.objects["TileA"]
    assert not a.get("MagnetHoles")
    assert len(a.data.vertices) == n_verts
    assert a.name in bpy.context.scene.collection.objects
    assert bpy.context.scene.tp3d.currentMap == a  # followed the swap
    assert a.data.name == "TileA"
    assert bpy.data.objects.get("UserAdded") is not None
    # Page companions are swapped back too (same name, restored copy).
    assert bpy.data.objects.get(trail_name) in list(bpy.context.scene.objects)
    assert session.info(bpy.context)["undo"] == []
    session.dispose()


def test_contour_lines_apply_and_undo():
    a = _terrain_map("TileA")
    _select([a])
    session = _session([a])
    n_verts = len(a.data.vertices)
    result = session.apply(bpy.context, "contour_lines",
                           {"realMeters": False, "distance": 3, "offset": 1, "thickness": 0.5})
    assert result["messages"][0]["level"] == "INFO", _messages_text(result)
    lines = [o for o in bpy.context.scene.objects if o.get("Object type") == "LINES"]
    assert len(lines) == 1 and lines[0]["PARENT"] == bpy.data.objects["TileA"]
    # The lines are part of the page now (shown in the viewer, part of undo).
    assert lines[0] in post_process.find_companions(bpy.context, [bpy.data.objects["TileA"]])
    assert session.info(bpy.context)["maps"][0]["contourLines"]  # page badge "Applied"

    session.undo(bpy.context)
    assert not [o for o in bpy.context.scene.objects if o.get("Object type") == "LINES"]
    assert not session.info(bpy.context)["maps"][0]["contourLines"]
    assert len(bpy.data.objects["TileA"].data.vertices) == n_verts

    bad = session.apply(bpy.context, "contour_lines",
                        {"realMeters": False, "distance": 0.2, "offset": 0, "thickness": 0.5})
    assert bad["messages"][0]["level"] == "WARNING", _messages_text(bad)
    assert session.info(bpy.context)["undo"] == []
    session.dispose()


def test_color_height_paint_and_undo():
    a = _terrain_map("TileA")
    _select([a])
    session = _session([a])
    result = session.apply(bpy.context, "color_height",
                           {"threshold": 50, "color": [0.9, 0.9, 0.9], "mode": "paint"})
    assert result["messages"][0]["level"] == "INFO", _messages_text(result)
    a = bpy.data.objects["TileA"]
    assert len(a.data.materials) == 2
    used = {p.material_index for p in a.data.polygons}
    assert used == {0, 1}, used
    assert session.info(bpy.context)["maps"][0]["colorHeight"]  # page badge "Applied"
    session.undo(bpy.context)
    a = bpy.data.objects["TileA"]
    assert len(a.data.materials) == 1
    assert not session.info(bpy.context)["maps"][0]["colorHeight"]
    session.dispose()


def test_color_height_multiple_bands():
    a = _terrain_map("TileA")
    _select([a])
    session = _session([a])
    # Given highest first: the backend still applies lowest first, so the
    # higher band isn't painted over.
    result = session.apply(bpy.context, "color_height", {"mode": "paint", "bands": [
        {"threshold": 70, "color": [0.95, 0.95, 0.95]},
        {"threshold": 30, "color": [0.9, 0.1, 0.1]},
    ]})
    assert result["messages"][0]["level"] == "INFO", _messages_text(result)
    a = bpy.data.objects["TileA"]
    assert len(a.data.materials) == 3, [m.name for m in a.data.materials]
    used = {p.material_index for p in a.data.polygons}
    assert used == {0, 1, 2}, used
    assert session.info(bpy.context)["undo"] == ["Color by Height"]
    session.undo(bpy.context)
    assert len(bpy.data.objects["TileA"].data.materials) == 1
    session.dispose()


def test_color_height_bands_on_textured_map_use_page_colors():
    import numpy as np

    a = _terrain_map("TileA")
    mesh = a.data
    uv = mesh.uv_layers.new(name="UVMap")
    for poly in mesh.polygons:
        for li in poly.loop_indices:
            v = mesh.vertices[mesh.loops[li].vertex_index].co
            uv.data[li].uv = ((v.x + 50) / 100, (v.y + 50) / 100)
    res = 64
    stale = bpy.data.images.get(f"{mesh.name}_MMU_Paint")  # left by an earlier test
    if stale is not None:
        bpy.data.images.remove(stale)
    image = bpy.data.images.new(f"{mesh.name}_MMU_Paint", width=res, height=res)
    image.pixels.foreach_set([0.1, 0.5, 0.1, 1.0] * (res * res))
    mesh["3mf_is_paint_texture"] = True
    mesh["3mf_paint_extruder_colors"] = "{}"
    # The sidebar holds other values -- the page's bands must win.
    tp3d = bpy.context.scene.tp3d
    tp3d.mountain_treshold = 69
    tp3d.mountain_color = (0.8, 0.4, 0.4)
    _select([a])
    session = _session([a])
    result = session.apply(bpy.context, "color_height", {"mode": "paint", "bands": [
        {"threshold": 20, "color": [0.0, 0.0, 1.0]},
        {"threshold": 70, "color": [1.0, 1.0, 1.0]},
    ]})
    assert result["messages"][0]["level"] == "INFO", _messages_text(result)
    px = np.empty(res * res * 4, dtype=np.float32)
    bpy.data.images[f"{mesh.name}_MMU_Paint"].pixels.foreach_get(px)
    colors = {tuple(np.round(c[:3], 2)) for c in px.reshape(-1, 4)}
    assert (0.0, 0.0, 1.0) in colors and (1.0, 1.0, 1.0) in colors, colors
    assert (0.8, 0.4, 0.4) not in colors, colors
    session.dispose()


def test_dovetails_and_bottom_mark():
    a = _terrain_map("TileA")
    _select([a])
    session = _session([a])
    n_verts = len(a.data.vertices)
    result = session.apply(bpy.context, "dovetails", {"sides": {"TileA": [0, 2]}})
    assert result["messages"][0]["level"] == "INFO", _messages_text(result)
    a = bpy.data.objects["TileA"]
    assert a.get("Dovetail") and len(a.data.vertices) != n_verts
    info = session.info(bpy.context)["maps"][0]
    assert info["dovetail"] and info["dovetailSpots"] == []

    nothing = session.apply(bpy.context, "dovetails", {"sides": {"TileA": [1]}})
    assert nothing["messages"][-1]["level"] == "WARNING"

    result = session.apply(bpy.context, "bottom_mark", {"cutout": True})
    assert result["messages"][0]["level"] == "INFO", _messages_text(result)
    assert bpy.data.objects["TileA"]["BottomMark"]
    assert session.info(bpy.context)["undo"] == ["Dovetails", "Bottom Mark"]
    session.undo(bpy.context)
    assert not bpy.data.objects["TileA"]["BottomMark"]
    assert bpy.data.objects["TileA"].get("Dovetail")
    session.dispose()


def test_undo_stack_is_bounded():
    a = _terrain_map("TileA")
    _select([a])
    session = _session([a])
    for _i in range(post_process.MAX_UNDO_STEPS + 2):
        result = session.apply(bpy.context, "color_height",
                               {"threshold": 20 + _i * 5, "color": [0.9, 0.9, 0.9], "mode": "paint"})
        assert result["messages"][0]["level"] == "INFO", _messages_text(result)
    assert len(session.info(bpy.context)["undo"]) == post_process.MAX_UNDO_STEPS
    session.dispose()
    # Disposing frees every snapshot copy -- nothing named TileA.00x is left.
    assert [o.name for o in bpy.data.objects if o.name.startswith("TileA")] == ["TileA"]


def test_text_preview_and_apply():
    a = _terrain_map("TileA")
    _select([a])
    session = _session([a])
    before = set(bpy.data.objects.keys())
    preview = session.preview(bpy.context, "text", {"text": "Hi", "size": 10})
    tris = preview["triangles"]
    assert len(tris) > 12 and len(tris) % 6 == 0
    xs = tris[0::2]
    assert -15 < min(xs) < 0 < max(xs) < 15  # centred
    assert set(bpy.data.objects.keys()) == before  # preview leaves nothing behind

    result = session.apply(bpy.context, "text", {
        "map": "TileA", "text": "Hi", "size": 10, "x": 20, "y": -20, "rotation": 30,
        "bottom": False, "operation": "negative",
    })
    assert result["messages"][0]["level"] == "INFO", _messages_text(result)
    result = session.apply(bpy.context, "text", {
        "map": "TileA", "text": "Lo", "size": 8, "x": 0, "y": 0, "rotation": 0, "bottom": True,
    })
    assert result["messages"][0]["level"] == "INFO", _messages_text(result)
    session.undo(bpy.context)
    session.undo(bpy.context)
    assert set(bpy.data.objects.keys()) == before
    session.dispose()


def test_svg_and_heightmap_previews():
    a = _terrain_map("TileA")
    _select([a])
    session = _session([a])
    before = set(bpy.data.objects.keys())
    preview = session.preview(bpy.context, "svg", {"svgPath": os.path.join(_SAMPLES, "star.svg")})
    assert len(preview["triangles"]) > 6, preview
    assert set(bpy.data.objects.keys()) == before
    assert bpy.context.view_layer.objects.active == a  # selection put back

    hm = session.preview(bpy.context, "heightmap", {
        "gpxPath": os.path.join(_SAMPLES, "BetweenLakes.gpx"), "length": 60, "height": 12})
    xs = [p[0] for p in hm["outline"]]
    ys = [p[1] for p in hm["outline"]]
    assert abs(min(xs) + 30) < 1e-6 and abs(max(xs) - 30) < 1e-6
    assert min(ys) == 0 and abs(max(ys) - 12) < 1e-6
    missing = session.preview(bpy.context, "heightmap", {"gpxPath": os.path.join(_TMP.name, "nope.gpx")})
    assert missing["outline"] == [] and missing["error"]

    mags = session.preview(bpy.context, "magnets", {"diameter": 6, "count": 3, "margin": 3})["magnets"]
    assert mags[0]["placed"] == 3 and len(mags[0]["centers"]) == 3
    session.dispose()


def test_svg_apply():
    a = _terrain_map("TileA")
    _select([a])
    session = _session([a])
    result = session.apply(bpy.context, "svg", {
        "map": "TileA", "svgPath": os.path.join(_SAMPLES, "star.svg"), "scale": 0.5,
        "x": 10, "y": 10, "rotation": 0, "bottom": False, "operation": "negative",
    })
    assert result["messages"][0]["level"] == "INFO", _messages_text(result)
    session.undo(bpy.context)
    session.dispose()


def test_text_and_svg_across_tiles():
    """A shape over the seam of two tiles goes onto both, not just the one
    its center is on -- and one well inside a tile leaves the other alone."""
    a = _terrain_map("TileA")
    b = _terrain_map("TileB", offset=(101.0, 0.0))
    session = _session([a, b])

    def counts():
        return len(a.data.polygons), len(b.data.polygons)

    start = counts()
    result = session.apply(bpy.context, "text", {
        "map": "TileA", "text": "HELLO", "size": 12, "x": 48, "y": 0, "rotation": 0,
        "bottom": False, "operation": "negative",
    })
    assert result["messages"][0]["level"] == "INFO", _messages_text(result)
    after_text = counts()
    assert after_text[0] != start[0] and after_text[1] != start[1], (start, after_text)

    result = session.apply(bpy.context, "svg", {
        # star.svg is ~15 mm across at scale 1 -- this one spans x 30..60.
        "map": "TileA", "svgPath": os.path.join(_SAMPLES, "star.svg"), "scale": 2.0,
        "x": 45, "y": 25, "rotation": 0, "bottom": True,
    })
    assert result["messages"][0]["level"] == "INFO", _messages_text(result)
    after_svg = counts()
    assert after_svg[0] != after_text[0] and after_svg[1] != after_text[1], (after_text, after_svg)

    result = session.apply(bpy.context, "text", {
        "map": "TileA", "text": "A", "size": 8, "x": -20, "y": 0, "rotation": 0,
        "bottom": False, "operation": "negative",
    })
    assert result["messages"][0]["level"] == "INFO", _messages_text(result)
    assert counts()[1] == after_svg[1]
    session.dispose()


def test_switched_off_tiles_are_left_alone():
    """Tiles switched off on the page (request "disabled") get nothing from
    Apply or the magnet preview -- a shape over the seam only goes onto the
    tile that's still on, and one only on a switched-off tile changes nothing."""
    a = _terrain_map("TileA")
    b = _terrain_map("TileB", offset=(101.0, 0.0))
    session = _session([a, b])

    def counts():
        # By name: a refused Apply puts the scene back with fresh objects.
        objs = bpy.data.objects
        return len(objs["TileA"].data.polygons), len(objs["TileB"].data.polygons)

    start = counts()
    result = session.handle(bpy.context, {
        "kind": "apply", "action": "text", "disabled": ["TileB"],
        "params": {"map": "TileA", "text": "HELLO", "size": 12, "x": 48, "y": 0, "rotation": 0,
                   "bottom": False, "operation": "negative"},
    })
    assert result["messages"][0]["level"] == "INFO", _messages_text(result)
    after = counts()
    assert after[0] != start[0] and after[1] == start[1], (start, after)

    # Placed on the switched-off tile, reaching nothing else.
    result = session.handle(bpy.context, {
        "kind": "apply", "action": "text", "disabled": ["TileB"],
        "params": {"map": "TileB", "text": "A", "size": 8, "x": 121, "y": 0, "rotation": 0,
                   "bottom": False, "operation": "negative"},
    })
    assert result["messages"][0]["level"] == "WARNING", _messages_text(result)
    assert counts() == after
    assert len(session._undo_stack) == 1

    result = session.handle(bpy.context, {
        "kind": "apply", "action": "text", "disabled": ["TileA", "TileB"],
        "params": {"map": "TileA", "text": "A", "size": 8, "x": 0, "y": 0, "rotation": 0},
    })
    assert result["messages"][0]["level"] == "WARNING", _messages_text(result)
    assert counts() == after

    preview = session.handle(bpy.context, {
        "kind": "preview", "action": "magnets", "disabled": ["TileB"],
        "params": {"diameter": 6, "count": 3, "margin": 3},
    })["preview"]
    assert [e["map"] for e in preview["magnets"]] == ["TileA"], preview
    session.dispose()




def test_map_connectors_beside_the_tiles_are_part_of_the_page():
    """The Multi Tile Generator's map connectors lie in a block beside the
    tiles, not on one -- they still belong to the page (viewer, export) by
    the batch's MultitileIndex; another batch's don't."""
    a = _terrain_map("TileA")
    b = _terrain_map("TileB", offset=(101.0, 0.0))
    a["MultitileIndex"] = 5
    b["MultitileIndex"] = 6

    def connector(name, index):
        bpy.ops.mesh.primitive_cube_add(size=4, location=(170.0, 0.0, 2.0))
        obj = bpy.context.object
        obj.name = name
        obj["Object type"] = "CONNECTOR"
        obj["ExportGroup"] = 0
        obj["MultitileIndex"] = index
        return obj

    ours = connector("MapConnector2mm", 5)
    other = connector("MapConnector2mm.other", 9)
    session = _session([a, b])
    _maps, companions = session.page_objects(bpy.context)
    assert ours in companions and other not in companions, [o.name for o in companions]
    session.dispose()


def test_puzzle_mode_paints_text_and_marks_every_piece():
    """The puzzle generators hand off to the page in puzzle mode: it works
    on the selected puzzle pieces (never on them otherwise). Text over a
    seam goes onto both pieces, always painted on top -- even when asked
    to cut in from below, which would change the pieces' shape. Bottom
    Mark marks every piece; every other tool is refused."""
    a = _terrain_map("PieceA")
    b = _terrain_map("PieceB", offset=(101.0, 0.0))
    for piece, col in ((a, 0), (b, 1)):
        piece["PuzzleRow"] = 0
        piece["PuzzleCol"] = col
    _select([a, b])
    assert post_process.find_page_maps(bpy.context) == []
    assert [o.name for o in post_process.find_page_maps(bpy.context, puzzle=True)] == ["PieceA", "PieceB"]

    session = post_process.PostProcessSession(["PieceA", "PieceB"], puzzle=True)
    assert session.info(bpy.context)["puzzle"] is True
    result = session.apply(bpy.context, "magnets", {"count": 2})
    assert result["messages"][0]["level"] == "ERROR", _messages_text(result)

    def shapes():
        objs = bpy.data.objects
        return [tuple(round(v, 3) for c in objs[n].bound_box for v in c) for n in ("PieceA", "PieceB")]

    start = shapes()
    result = session.apply(bpy.context, "text", {
        "map": "PieceA", "text": "HELLO", "size": 12, "x": 48, "y": 0, "rotation": 0,
        "bottom": True, "operation": "negative", "color": [1.0, 0.0, 0.0],
    })
    assert result["messages"][0]["level"] == "INFO", _messages_text(result)
    assert shapes() == start
    for name in ("PieceA", "PieceB"):
        mats = [m.name for m in bpy.data.objects[name].data.materials if m]
        assert any(m.startswith("PAINT_") for m in mats), (name, mats)

    result = session.apply(bpy.context, "bottom_mark", {"cutout": False})
    assert result["messages"][0]["level"] == "INFO", _messages_text(result)
    assert all(bpy.data.objects[n]["BottomMark"] for n in ("PieceA", "PieceB"))
    session.dispose()


def test_painted_text_on_puzzle_pieces_goes_into_their_shared_texture():
    """Puzzle pieces keep the paint texture of the map they were cut from
    (one image, each piece's UVs on its own part). Painted text is baked
    into it at the right spot -- not left as per-face materials, which
    broke pieces -- and the page's Undo takes it back out."""
    import numpy as np

    res = 128
    stale = bpy.data.images.get("Whole_MMU_Paint")
    if stale is not None:
        bpy.data.images.remove(stale)
    image = bpy.data.images.new("Whole_MMU_Paint", width=res, height=res)
    image.pixels.foreach_set([0.1, 0.5, 0.1, 1.0] * (res * res))
    mat = bpy.data.materials.new("Whole_MMU_Paint")
    mat.use_nodes = True
    node = mat.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = image
    # The whole puzzle spans world x -50..151, y -50..50.
    pieces = []
    for name, offset, col in (("PieceA", 0.0, 0), ("PieceB", 101.0, 1)):
        piece = _terrain_map(name, offset=(offset, 0.0))
        piece["PuzzleRow"] = 0
        piece["PuzzleCol"] = col
        mesh = piece.data
        mesh.materials.clear()
        mesh.materials.append(mat)
        uv = mesh.uv_layers.new(name="UVMap")
        for li, loop in enumerate(mesh.loops):
            v = mesh.vertices[loop.vertex_index].co
            uv.data[li].uv = ((v.x + offset + 50) / 201, (v.y + 50) / 100)
        mesh["3mf_is_paint_texture"] = True
        mesh["3mf_paint_extruder_colors"] = "{}"
        pieces.append(piece)
    _select(pieces)
    session = post_process.PostProcessSession(["PieceA", "PieceB"], puzzle=True)
    result = session.apply(bpy.context, "text", {
        "map": "PieceA", "text": "HELLO", "size": 12, "x": 50, "y": 0, "rotation": 0,
        "bottom": False, "operation": "paint", "color": [1.0, 0.0, 0.0],
    })
    assert result["messages"][0]["level"] == "INFO", _messages_text(result)
    for name in ("PieceA", "PieceB"):
        mats = [m.name for m in bpy.data.objects[name].data.materials if m]
        assert mats == ["Whole_MMU_Paint"], (name, mats)

    def red_columns():
        px = np.empty(res * res * 4, dtype=np.float32)
        bpy.data.images["Whole_MMU_Paint"].pixels.foreach_get(px)
        px = px.reshape(res, res, 4)
        red = (px[:, :, 0] > 0.9) & (px[:, :, 1] < 0.1)
        return np.nonzero(red.any(axis=0))[0]

    cols = red_columns()
    # Text centered at world x 50 (u ~0.5), a few cm wide, on both pieces.
    assert len(cols) and cols.min() < res * 0.5 < cols.max(), cols
    assert cols.min() > res * 0.25 and cols.max() < res * 0.75, cols

    result = session.undo(bpy.context)
    assert result["messages"][0]["level"] == "INFO", _messages_text(result)
    assert not len(red_columns())
    session.dispose()


def test_glb_parts_name_their_tile():
    """Each part in the viewer's GLB names the tile it sits on, so the page
    can grey a switched-off tile's parts with it."""
    a = _terrain_map("TileA")
    b = _terrain_map("TileB", offset=(101.0, 0.0))
    trail = _element("TrailB", b)
    session = _session([a, b])
    maps, companions = session.page_objects(bpy.context)
    assert trail in companions
    assert post_process.tile_of(trail, maps) == "TileB"
    assert post_process.tile_of(a, maps) == "TileA"
    session.dispose()


def test_svg_cut_in_keeps_a_floor():
    """"Cut in" engraves the SVG -- it must never cut through to the base."""
    from mathutils import Vector

    a = _terrain_map("TileA")
    _select([a])
    session = _session([a])
    n_verts = len(a.data.vertices)
    result = session.apply(bpy.context, "svg", {
        "map": "TileA", "svgPath": os.path.join(_SAMPLES, "star.svg"), "scale": 3.0,
        "x": 20, "y": 0, "rotation": 0, "bottom": False, "operation": "negative",
    })
    assert result["messages"][0]["level"] == "INFO", _messages_text(result)
    a = bpy.data.objects["TileA"]
    assert len(a.data.vertices) != n_verts  # something was cut
    up = Vector((0, 0, 1))
    floors = []
    for dx in range(-4, 5, 2):
        for dy in range(-4, 5, 2):
            hit, loc, _n, _i = a.ray_cast(Vector((20 + dx, dy, -5)), up)
            assert hit and abs(loc.z) < 1e-3, ("bottom missing -- cut through", dx, dy, hit, loc)
            hit, loc, _n, _i = a.ray_cast(loc + up * 1e-3, up)
            assert hit, ("nothing above the base", dx, dy)
            floors.append(loc.z)
    assert min(floors) > 1.0, floors
    session.dispose()


def test_text_cut_in_over_bottom_recess_keeps_a_floor():
    """"Cut in" text over a recess in the map's underside (bottom text here;
    side walls, magnet holes and dovetails alike) must still stop 1 mm below
    the terrain -- not at the recess, which cut straight through."""
    from mathutils import Vector

    a = _terrain_map("TileA")
    _select([a])
    session = _session([a])
    result = session.apply(bpy.context, "text", {
        "map": "TileA", "text": "MMMM", "size": 14, "x": 20, "y": 0, "rotation": 0, "bottom": True,
    })
    assert result["messages"][0]["level"] == "INFO", _messages_text(result)
    result = session.apply(bpy.context, "text", {
        "map": "TileA", "text": "MMMM", "size": 14, "x": 20, "y": 0, "rotation": 0,
        "bottom": False, "operation": "negative",
    })
    assert result["messages"][0]["level"] == "INFO", _messages_text(result)
    a = bpy.data.objects["TileA"]
    up = Vector((0, 0, 1))
    floors = []
    for dx in range(-16, 17, 2):
        for dy in range(-5, 6, 2):
            hit, loc, _n, _i = a.ray_cast(Vector((20 + dx, dy, -5)), up)
            assert hit and loc.z < 1.0, ("bottom missing -- cut through", dx, dy, hit, loc)
            hit, loc, _n, _i = a.ray_cast(loc + up * 1e-3, up)
            assert hit, ("nothing above the underside", dx, dy)
            floors.append(loc.z)
    # The terrain is at least 3 mm high, so the cut stops at 2 mm or above.
    assert min(floors) > 1.5, sorted(floors)[:5]
    session.dispose()


def test_text_cut_in_at_map_edge_keeps_a_wall():
    """"Cut in" text running off the map's edge leaves a 0.5 mm wall
    standing there instead of opening up the side."""
    from mathutils import Vector

    a = _terrain_map("TileA")
    _select([a])
    session = _session([a])
    result = session.apply(bpy.context, "text", {
        "map": "TileA", "text": "MMMM", "size": 14, "x": 45, "y": 0, "rotation": 0,
        "bottom": False, "operation": "negative",
    })
    assert result["messages"][0]["level"] == "INFO", _messages_text(result)
    a = bpy.data.objects["TileA"]
    inward = Vector((-1, 0, 0))
    walls = []
    cut_found = False
    for dy in range(-4, 5, 2):
        # Between the cut's floor (~2.75 mm) and the terrain (~3.75 mm at
        # the edge): the outer wall, then the cut.
        hit, loc, _n, _i = a.ray_cast(Vector((60, dy, 3.3)), inward)
        assert hit and abs(loc.x - 50) < 1e-3, ("outer wall missing", dy, hit, loc)
        hit, loc2, _n, _i = a.ray_cast(loc + inward * 1e-3, inward)
        if hit and loc2.x > 45:
            cut_found = True
            walls.append(50 - loc2.x)
    assert cut_found, "text did not cut near the edge"
    # Rows through gaps between letters see a thicker wall; the thinnest is
    # where a letter runs off the edge.
    assert 0.45 < min(walls) < 0.55, walls
    session.dispose()


def test_svg_import_has_no_sliver_faces_and_bottom_keeps_every_part():
    """Straight runs drawn as curves (a "q" corner with almost collinear
    points, an all-"c" rectangle) used to fill into zero-area triangles,
    which made the bottom-side clip (EXACT boolean) drop whole parts."""
    import bmesh

    from TrailPrint3D.utils.scene import importSVGtoMerge

    path = os.path.join(_TMP.name, "slivers.svg")
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 650">'
            '<path d="M230,650h-6.5c-72.8,0-145.6,0-218.5,0q-6.6,0-6.6-6.4c0-64,0-128-.1-192,0-4,1-6,5-7.6L230,356Z"/>'
            '<path d="M270,10c50,0,100,0,150,.01,0,50,0,100,.01,150-50,0-100,0-150-.01Z"/>'
            '<path d="M0,0H230V300L0,400Z"/></svg>'
        )
    a = _terrain_map("TileA")
    _select([a])
    bpy.context.scene.cursor.location = (0, 0, 0)
    svg = importSVGtoMerge(a, path)
    bm = bmesh.new()
    bm.from_mesh(svg.data)
    slivers = sum(1 for face in bm.faces if face.calc_area() < 1e-6)
    open_edges = sum(1 for e in bm.edges if not e.is_manifold)
    bm.free()
    assert slivers == 0 and open_edges == 0, (slivers, open_edges)
    bpy.data.objects.remove(svg, do_unlink=True)

    session = _session([a])
    result = session.apply(bpy.context, "svg", {
        "map": "TileA", "svgPath": path, "scale": 2.0, "x": 0, "y": 0, "rotation": 0, "bottom": True,
    })
    assert result["messages"][0]["level"] == "INFO", _messages_text(result)
    inlays = [o for o in bpy.context.scene.objects if o.name != "TileA"]
    assert len(inlays) == 1
    bm = bmesh.new()
    bm.from_mesh(inlays[0].data)
    bm.transform(inlays[0].matrix_world)
    seen, part_points = set(), []
    for face in bm.faces:
        if face.index in seen:
            continue
        stack, comp = [face], []
        seen.add(face.index)
        while stack:
            cur = stack.pop()
            comp.append(cur)
            for e in cur.edges:
                for nb in e.link_faces:
                    if nb.index not in seen:
                        seen.add(nb.index)
                        stack.append(nb)
        down = max(comp, key=lambda f: f.calc_area() if f.normal.z < -0.5 else 0.0)
        part_points.append(down.calc_center_median().copy())
    bm.free()
    assert len(part_points) == 3, len(part_points)
    # ...and every part sits in a real recess: the clipped SVG isn't
    # watertight, and a MANIFOLD-only boolean used to cut nothing at all.
    from mathutils import Vector

    a = bpy.data.objects["TileA"]
    for p in part_points:
        hit, loc, _n, _i = a.ray_cast(Vector((p.x, p.y, -5.0)), Vector((0, 0, 1)))
        assert hit and loc.z > 0.3, ("no recess under part", tuple(p), loc if hit else None)
    session.dispose()


def test_heightmap_apply():
    if not temp.PREMIUMVERSION:
        print("    (free build -- checking the premium gate only)")
        a = _terrain_map("TileA")
        result = _session([a]).apply(bpy.context, "heightmap", {"map": "TileA"})
        assert result["messages"][0]["level"] == "ERROR"
        return
    a = _terrain_map("TileA")
    _select([a])
    session = _session([a])
    n_verts = len(a.data.vertices)
    result = session.apply(bpy.context, "heightmap", {
        "map": "TileA", "gpxPath": os.path.join(_SAMPLES, "BetweenLakes.gpx"),
        "length": 50, "height": 10, "x": 0, "y": 0, "rotation": 0,
    })
    assert result["messages"][0]["level"] == "INFO", _messages_text(result)
    assert len(bpy.data.objects["TileA"].data.vertices) != n_verts
    session.undo(bpy.context)
    assert len(bpy.data.objects["TileA"].data.vertices) == n_verts
    session.dispose()


def test_failed_action_restores_scene():
    a = _terrain_map("TileA")
    _select([a])
    session = _session([a])
    before = set(bpy.data.objects.keys())

    def boom(context, maps, companions, params, messages):
        bpy.data.objects.new("HalfDone", bpy.data.meshes.new("HalfDone"))
        context.scene.collection.objects.link(bpy.data.objects["HalfDone"])
        maps[0]["Broken"] = True
        raise RuntimeError("boom")

    session._apply_magnets = boom
    result = session.apply(bpy.context, "magnets", {})
    assert result["messages"][-1]["level"] == "ERROR" and "boom" in result["messages"][-1]["text"]
    assert set(bpy.data.objects.keys()) == before
    assert "Broken" not in bpy.data.objects["TileA"]
    assert session.info(bpy.context)["undo"] == []


def test_export_colored_flag():
    a = _terrain_map("TileA")
    session = _session([a])
    assert session.info(bpy.context)["colored"] is False
    _element("TileA_Trail", a)
    assert session.info(bpy.context)["colored"] is True
    session.dispose()


def test_export_stl_and_obj():
    a = _terrain_map("TileA")
    trail = _element("TileA_Trail", a)
    tp3d = bpy.context.scene.tp3d
    folder = os.path.join(_TMP.name, "export_test", "")
    os.makedirs(folder, exist_ok=True)
    previous = tp3d.export_path
    tp3d.export_path = folder
    try:
        session = _session([a])
        tp3d.modelname = ""
        assert session.info(bpy.context)["exportName"] == "TileA"
        for fmt in ("stl", "obj"):
            # Default name: the map's own; the page's name replaces it.
            for export_name, expected in (("", ("TileA", "TileA_TileA_Trail")),
                                          ("My Trip", ("My Trip", "My Trip_TileA_Trail"))):
                result = session.export(bpy.context, fmt, export_name)
                assert result["messages"][0]["level"] == "INFO", _messages_text(result)
                for name in expected:
                    assert os.path.isfile(os.path.join(folder, f"{name}.{fmt}")), (fmt, name)
        # The page's maps stay selected afterwards, the parts don't.
        assert a.select_get() and not trail.select_get()
        assert session.export(bpy.context, "fbx")["messages"][0]["level"] == "ERROR"
        if not temp.has3mf:
            assert session.export(bpy.context, "3mf")["messages"][0]["level"] == "ERROR"
        tp3d.export_path = os.path.join(_TMP.name, "missing", "")
        assert session.export(bpy.context, "stl")["messages"][0]["level"] == "ERROR"
        session.dispose()
    finally:
        tp3d.export_path = previous


def test_server_queue_keeps_every_request():
    picker_server._pending_post_process.put({"kind": "apply", "seq": 1})
    picker_server._pending_post_process.put({"kind": "apply", "seq": 2})
    assert picker_server.drain_post_process_request()["seq"] == 1
    assert picker_server.drain_post_process_request()["seq"] == 2
    assert picker_server.drain_post_process_request() is None


def main():
    print(f"Post-processing tests (premium={bool(temp.PREMIUMVERSION)})")
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            _run(name, fn)
    print(f"\n{_passed} passed, {_failed} failed")
    if _failed:
        raise SystemExit(1)


main()
