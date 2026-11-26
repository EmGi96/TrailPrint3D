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

    _select([piece])  # puzzle pieces never count...
    assert post_process.find_page_maps(bpy.context) == []
    assert not bpy.ops.tp3d.post_process_generator.poll()

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
