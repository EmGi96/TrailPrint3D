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
# Generations save into the map library -- keep that out of the user's own
# Documents/TrailPrint3D/Maps.
_LIBRARY = os.path.join(_TMP.name, "library")
bpy.context.preferences.addons["TrailPrint3D"].preferences.library_folder = _LIBRARY
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


def test_glb_paint_texture_has_one_color_per_triangle():
    # A triangle across two paint colors must not carry both (the viewer
    # would blend them into shades the Color by Height preview skips).
    a = _terrain_map("TileA")
    mesh = a.data
    uv = mesh.uv_layers.new(name="UVMap")
    for i, loop in enumerate(uv.data):
        loop.uv = (0.25 if i % 2 else 0.75, 0.5)  # corners on both halves
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
    _pos, colors = post_process._object_triangles(a, bpy.context.evaluated_depsgraph_get(), {})
    per_tri = colors.reshape(-1, 3, 3)
    assert (per_tri == per_tri[:, :1]).all()
    palette = {tuple(round(float(v), 4) for v in c) for c in colors}
    assert palette <= {(1.0, 0.0, 0.0), (0.0, 0.0, 1.0)}, palette


def test_extruder_modes_greyed_out_on_textured_map_with_colors():
    from TrailPrint3D import operators

    a = _terrain_map("TileA")
    mesh = a.data
    # _clear_scene keeps images: drop an earlier test's TileA texture.
    old = bpy.data.images.get(f"{mesh.name}_MMU_Paint")
    if old is not None:
        bpy.data.images.remove(old)
    image = bpy.data.images.new(f"{mesh.name}_MMU_Paint", width=4, height=4)
    image.pixels.foreach_set([0.2, 0.6, 0.1, 1.0] * 16)  # base only
    mat = bpy.data.materials.new("PaintTex")
    mat.use_nodes = True
    tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = image
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    mesh.materials.clear()
    mesh.materials.append(mat)
    mesh["3mf_is_paint_texture"] = True

    assert not operators._paint_texture_has_colors(mesh)
    assert operators._mountain_split_ineligible_reason(
        a, bpy.context.scene.tp3d, check_paint=False) is None
    info = post_process.map_info(bpy.context, a, [], 0)
    assert info["splitReasonSEM"] is None and info["splitReasonMEM"] is None, info
    assert info["hasColors"] is False

    px = [0.2, 0.6, 0.1, 1.0] * 16
    px[-4:] = [0.1, 0.3, 0.1, 1.0]  # a forest pixel
    image.pixels.foreach_set(px)
    info = post_process.map_info(bpy.context, a, [], 0)
    assert info["splitReasonSEM"] and info["splitReasonMEM"], info
    assert info["hasColors"] is True
    # The operator refuses it too (its check reads the image as well).
    reason = operators._mountain_split_ineligible_reason(
        a, bpy.context.scene.tp3d, check_topology=True, mode=operators.SPLIT_MULTI_EXTRUDER)
    assert reason, reason


def test_map_info_reports_base_color_not_the_most_common_one():
    # Forest covering most of the map must not become the Color by Height
    # preview's "base" -- the page recolors info's baseColor only.
    a = _terrain_map("TileA")
    mesh = a.data
    forest = _material("FOREST", (0.02, 0.13, 0.02))
    mesh.materials.clear()
    mesh.materials.append(forest)
    mesh.materials.append(_material("BASE", (0.1, 0.5, 0.1)))
    for poly in mesh.polygons:
        poly.material_index = 0 if poly.index % 4 else 1
    expected = post_process.map_base_color(a)
    from TrailPrint3D.utils.color import material_to_srgb
    from TrailPrint3D.utils.color_styles import srgb_to_hex

    assert expected == srgb_to_hex(material_to_srgb(bpy.data.materials["BASE"])), expected
    info = post_process.map_info(bpy.context, a, [], 0)
    assert info["baseColor"] == expected, info["baseColor"]

    # A textured map: the BASE role recorded on its mesh.
    mesh["3mf_is_paint_texture"] = True
    bpy.data.images.new(f"{mesh.name}_MMU_Paint", width=2, height=2)
    mesh["tp3d_color_roles"] = json.dumps({"BASE": "#55B222", "FOREST": "#266623"})
    assert post_process.map_base_color(a) == "#55B222"
    del mesh["tp3d_color_roles"]
    assert post_process.map_base_color(a) is None  # page falls back to its guess


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


def test_color_height_bands_each_use_their_own_noise():
    a = _terrain_map("TileA")
    _select([a])
    session = _session([a])
    runs = []
    original = post_process.OperatorProxy

    class Recording(original):
        def __init__(self, op_class, **props):
            super().__init__(op_class, **props)
            runs.append(props)

    post_process.OperatorProxy = Recording
    try:
        result = session.apply(bpy.context, "color_height", {
            "mode": "paint",
            # Request-wide values: only the band without its own uses them.
            "noise": True, "noiseAmplitude": 9.0, "noiseScale": 1.5,
            "bands": [
                {"threshold": 70, "color": [0.9, 0.9, 0.9],
                 "noise": True, "noiseAmplitude": 2.0, "noiseScale": 0.2},
                {"threshold": 30, "color": [0.5, 0.3, 0.1],
                 "noise": False, "noiseAmplitude": 0.5, "noiseScale": 0.8},
                {"threshold": 50, "color": [0.4, 0.4, 0.4]},
            ],
        })
    finally:
        post_process.OperatorProxy = original
    assert result["messages"][0]["level"] == "INFO", _messages_text(result)
    got = [(r["mountain_treshold"], r["mountain_noise"], r["mountain_noise_amplitude"],
            r["mountain_noise_scale"]) for r in runs]
    assert got == [(30, False, 0.5, 0.8), (50, True, 9.0, 1.5), (70, True, 2.0, 0.2)], got
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


def test_generation_goes_into_its_own_collection():
    """Everything a generator run adds to the scene lands in one new
    collection, tagged with the generator's kind; a failed run moves
    nothing, and debug output keeps its own collection."""
    from TrailPrint3D.utils import scene as scene_utils

    keep = _terrain_map("Existing")
    debug = bpy.data.collections.get("TP3D_Debug") or bpy.data.collections.new("TP3D_Debug")
    if debug.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(debug)
    with scene_utils.generation_collection(bpy.context, "map", "Map") as gen:
        new_map = _terrain_map("NewMap")
        trail = _element("NewTrail", new_map)
        dbg = bpy.data.objects.new("DebugThing", None)
        debug.objects.link(dbg)
    col = gen.collection
    assert col is not None and col.get(scene_utils.GENERATION_COLLECTION_PROP) == "map", col
    assert {o.name for o in col.objects} == {"NewMap", "NewTrail"}, [o.name for o in col.objects]
    assert [c.name for c in new_map.users_collection] == [col.name]
    assert col not in keep.users_collection and col not in dbg.users_collection

    try:
        with scene_utils.generation_collection(bpy.context, "map", "Broken") as failed:
            _terrain_map("HalfDone")
            raise RuntimeError("generation failed")
    except RuntimeError:
        pass
    assert failed.collection is None and bpy.data.collections.get("Broken") is None


def test_save_puts_the_map_into_my_maps():
    """Nothing goes into My Maps on its own: the page's Save does it (file,
    thumbnail, gallery entry), and saves later changes over the same entry
    -- the page knows whether there's anything to save. Once it's gone from
    the scene, opening it brings the map back, paint texture included;
    deleting removes its files."""
    import time as _time

    import numpy as np

    from TrailPrint3D import library
    from TrailPrint3D.utils import scene as scene_utils

    # A library of its own: earlier tests' generations are saved too.
    folder = os.path.join(_TMP.name, "library_test")
    prefs = bpy.context.preferences.addons["TrailPrint3D"].preferences
    prefs.library_folder = folder
    try:
        _check_library(library, scene_utils, folder, np, _time)
    finally:
        prefs.library_folder = _LIBRARY


def test_save_uses_the_file_name_as_the_map_name():
    from TrailPrint3D import library
    from TrailPrint3D.utils import scene as scene_utils

    folder = os.path.join(_TMP.name, "library_names")
    prefs = bpy.context.preferences.addons["TrailPrint3D"].preferences
    prefs.library_folder = folder
    try:
        with scene_utils.generation_collection(bpy.context, "map", "Map") as gen:
            m = _terrain_map("NamedMap")
        collection = gen.collection
        session = _session([m])
        # The File name starts as the map's own name (one name for export
        # and My Maps).
        own = collection.name
        assert session.info(bpy.context)["exportName"] == own
        # No name typed: the map keeps its own.
        result = session.handle(bpy.context, {"kind": "save", "params": {"name": ""}})
        assert "Saved" in _messages_text(result), _messages_text(result)
        assert collection.name == own
        # A typed File name becomes the map's name in My Maps.
        session.handle(bpy.context, {"kind": "save", "params": {"name": "  Zugspitze  "}})
        assert collection.name == "Zugspitze"
        entry = [e for e in library.list_maps(folder) if e["id"] == collection[library.LIBRARY_ID_PROP]]
        assert entry and entry[0]["name"] == "Zugspitze", entry
        assert session.info(bpy.context)["mapName"] == "Zugspitze"
        assert session.info(bpy.context)["exportName"] == "Zugspitze"
        assert len(library.list_maps(folder)) == 1, "renaming saves over the same entry"
        # A name another saved map has gets a number.
        with scene_utils.generation_collection(bpy.context, "map", "Map") as gen2:
            other = _terrain_map("OtherMap", offset=(300, 0))
        other_session = _session([other])
        other_session.handle(bpy.context, {"kind": "save", "params": {"name": "Zugspitze"}})
        assert gen2.collection.name == "Zugspitze 2", gen2.collection.name
    finally:
        prefs.library_folder = _LIBRARY


def test_save_again_after_its_my_maps_entry_was_deleted():
    from TrailPrint3D import library
    from TrailPrint3D.utils import scene as scene_utils

    folder = os.path.join(_TMP.name, "library_resave")
    prefs = bpy.context.preferences.addons["TrailPrint3D"].preferences
    prefs.library_folder = folder
    try:
        with scene_utils.generation_collection(bpy.context, "map", "Map") as gen:
            m = _terrain_map("ResaveMap")
        session = _session([m])
        session.handle(bpy.context, {"kind": "save", "params": {}})
        map_id = gen.collection[library.LIBRARY_ID_PROP]
        assert session.info(bpy.context)["saved"] is True
        assert library.delete_map(folder, map_id)
        # The page's refresh (after a delete in My Maps) hears it's gone.
        result = session.handle(bpy.context, {"kind": "refresh"})
        assert result == {"refresh_info": True}
        info = session.info(bpy.context)
        assert info["saved"] is False and info["changed"] is True, info
        session.handle(bpy.context, {"kind": "save", "params": {}})
        assert [e["id"] for e in library.list_maps(folder)] == [gen.collection[library.LIBRARY_ID_PROP]]
        assert session.info(bpy.context)["saved"] is True
    finally:
        prefs.library_folder = _LIBRARY


def _check_library(library, scene_utils, _LIBRARY, np, _time):
    with scene_utils.generation_collection(bpy.context, "map", "Map") as gen:
        m = _terrain_map("LibMap")
        image = bpy.data.images.new("LibMap_MMU_Paint", width=8, height=8)  # in memory, not packed
        image.pixels.foreach_set([0.2, 0.6, 0.2, 1.0] * 64)
        mat = bpy.data.materials.new("LibMap_MMU_Paint")
        mat.use_nodes = True
        mat.node_tree.nodes.new("ShaderNodeTexImage").image = image
        m.data.materials.append(mat)
    col = gen.collection
    assert library.list_maps(_LIBRARY) == []          # not saved by itself
    session = _session([m])
    info = session.info(bpy.context)
    assert info["saved"] is False and info["changed"] is True and info["mapName"] == col.name

    result = session.handle(bpy.context, {"kind": "save"})
    assert result["messages"][0]["level"] == "INFO" and result.get("refresh_info"), _messages_text(result)
    map_id = col.get(library.LIBRARY_ID_PROP)
    entries = library.list_maps(_LIBRARY)
    assert [e["id"] for e in entries] == [map_id], entries
    assert entries[0]["thumb"] and entries[0]["maps"] == 1 and entries[0]["kind"] == "map", entries[0]
    info = session.info(bpy.context)
    assert info["saved"] is True and info["changed"] is False and info["mapId"] == map_id
    first_saved = entries[0]["modified"]

    # A change makes it "changed" until saved again -- over the same entry.
    result = session.apply(bpy.context, "bottom_mark", {"cutout": False})
    assert result["messages"][0]["level"] == "INFO", _messages_text(result)
    assert session.info(bpy.context)["changed"] is True
    _time.sleep(0.05)
    session.handle(bpy.context, {"kind": "save"})
    entries = library.list_maps(_LIBRARY)
    assert len(entries) == 1 and entries[0]["modified"] > first_saved
    assert session.info(bpy.context)["changed"] is False

    session.dispose()

    # Gone from the scene (a fresh Blender session) -- still in the library.
    for obj in list(col.all_objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.collections.remove(col)
    bpy.data.images.remove(bpy.data.images["LibMap_MMU_Paint"])

    loaded = library.load_map(bpy.context.scene, map_id)
    assert loaded is not None and "LibMap" in [o.name for o in loaded.objects]
    assert bpy.data.objects["LibMap"]["BottomMark"]
    img = bpy.data.images.get("LibMap_MMU_Paint")
    assert img is not None and img.packed_file is not None  # self-contained file
    px = np.empty(8 * 8 * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    assert abs(px[1] - 0.6) < 0.01, px[:4]
    # Opening it again finds the copy that's already there.
    assert library.load_map(bpy.context.scene, map_id) is loaded

    assert library.delete_map(_LIBRARY, map_id)
    assert library.list_maps(_LIBRARY) == []
    assert library.load_map(bpy.context.scene, "no-such-map") is None
    assert not library.valid_id("../escape")


def test_new_generation_clears_the_previous_ones():
    """A new generation takes the earlier ones out of the scene -- the saved
    ones come back from My Maps, unsaved ones are gone. Maps that aren't a
    generation's (older files) stay, and a Multi Tile Extend run
    (clear_previous=False) keeps what it builds on."""
    from TrailPrint3D import library
    from TrailPrint3D.utils import scene as scene_utils

    legacy = _terrain_map("OldMap", offset=(500.0, 0.0))
    with scene_utils.generation_collection(bpy.context, "map", "Map") as first:
        _terrain_map("FirstMap")
    first_id = library.save_collection(first.collection)
    with scene_utils.generation_collection(bpy.context, "map", "Map") as unsaved:
        _terrain_map("UnsavedMap")
    assert "FirstMap" not in bpy.data.objects
    with scene_utils.generation_collection(bpy.context, "map", "Map"):
        _terrain_map("SecondMap")
    assert "UnsavedMap" not in bpy.data.objects
    assert "OldMap" in bpy.data.objects and legacy.name == "OldMap"
    assert "SecondMap" in bpy.data.objects

    with scene_utils.generation_collection(bpy.context, "multitile", "Multi-tile", clear_previous=False):
        _terrain_map("ExtendTile", offset=(101.0, 0.0))
    assert "SecondMap" in bpy.data.objects and "ExtendTile" in bpy.data.objects

    assert library.load_map(bpy.context.scene, first_id) is not None
    assert "FirstMap" in bpy.data.objects


def test_generations_get_telling_names_and_can_be_renamed():
    """A new generation is named after what the user gave (typed or the GPX
    track's name), else the place it shows, else its coordinates; puzzles
    and the like get their kind in front, repeats a number. Renaming in My
    Maps renames the entry and its collection -- also one that's reopened
    later."""
    from unittest.mock import patch

    from TrailPrint3D import library
    from TrailPrint3D.utils import scene as scene_utils

    folder = os.path.join(_TMP.name, "library_names")
    prefs = bpy.context.preferences.addons["TrailPrint3D"].preferences
    prefs.library_folder = folder
    tp3d = bpy.context.scene.tp3d

    def generate(kind, label, name="M", lat=47.42, lon=10.98):
        with scene_utils.generation_collection(bpy.context, kind, label, clear_previous=False) as gen:
            m = _terrain_map(name + str(len(bpy.data.objects)))
            m["latitude"], m["longitude"] = lat, lon
        return gen.collection

    try:
        with patch.object(library, "place_name", lambda lat, lon, timeout=4.0: "Garmisch-Partenkirchen"):
            tp3d.modelname = "Zugspitze via Höllental"   # typed / from the GPX
            assert generate("map", "Map").name == "Zugspitze via Höllental"
            tp3d.modelname = "Terrain"                   # the old placeholder
            assert generate("map", "Map").name == "Garmisch-Partenkirchen"
            assert generate("map", "Map").name == "Garmisch-Partenkirchen 2"
            puzzle = generate("puzzle", "Jigsaw Puzzle")
            assert puzzle.name == "Jigsaw · Garmisch-Partenkirchen", puzzle.name
            library.save_collection(puzzle)
        with patch.object(library, "place_name", lambda lat, lon, timeout=4.0: None):  # offline
            name = generate("map", "Map").name
            assert name.startswith("47.42° N 10.98° E · "), name

        map_id = puzzle[library.LIBRARY_ID_PROP]
        assert library.rename_map(bpy.context.scene, map_id, "  My   Puzzle ") == "My Puzzle"
        assert puzzle.name == "My Puzzle"
        assert "My Puzzle" in [e["name"] for e in library.list_maps(folder)]
        assert library.rename_map(bpy.context.scene, map_id, "   ") is None

        # Renamed while out of the scene: reopened under the new name.
        for obj in list(puzzle.all_objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(puzzle)
        assert library.rename_map(bpy.context.scene, map_id, "Holiday Puzzle") == "Holiday Puzzle"
        assert library.load_map(bpy.context.scene, map_id).name == "Holiday Puzzle"
    finally:
        tp3d.modelname = ""
        prefs.library_folder = _LIBRARY


def test_generation_remembers_its_settings_for_going_back():
    """A generation keeps the settings it was made with (its page's history
    entry) and its generator on its collection -- also through My Maps. Going
    back hands them to that generator's page once: the fields through
    /get_state, the rest through /history_restore; never to another page."""
    import json
    import pathlib
    from unittest.mock import patch

    from TrailPrint3D import library, operators
    from TrailPrint3D.utils import scene as scene_utils

    history = pathlib.Path(_TMP.name) / "map_generator_history.json"
    entry = {"id": "h123", "settings": {"coords": [1, 2, 3, 4], "resolution": 9},
             "settingsState": {"scaleElevation": 2.5}, "thumbnail": "data:image/png;base64,AAAA"}
    history.write_text(json.dumps([{"id": "other"}, entry]), encoding="utf-8")
    page = pathlib.Path("generators/map_generator.html")
    with patch.object(picker_server._Handler, "history_path", history, create=True), \
         patch.object(picker_server._Handler, "html_path", page, create=True):
        with scene_utils.generation_collection(bpy.context, "map", "Map", history_id="h123") as gen:
            m = _terrain_map("BackMap")
    col = gen.collection
    assert col[scene_utils.GENERATOR_PROP] == "map_generator"
    stored = json.loads(col[scene_utils.GENERATOR_STATE_PROP])
    assert stored["settings"]["resolution"] == 9 and stored["page"] == "map_generator"
    assert "thumbnail" not in stored  # images stay in the history file

    # Kept in the saved map: reopened later, it still knows its settings.
    map_id = library.save_collection(col)
    for obj in list(col.all_objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.collections.remove(col)
    col = library.load_map(bpy.context.scene, map_id)
    op_id, origin = operators.TP3D_OT_post_process_generator._map_origin([bpy.data.objects["BackMap"]])
    assert op_id == "map_generator" and origin["id"] == "h123", (op_id, origin)

    picker_server.set_pending_restore(origin)
    assert picker_server._take_pending_restore("puzzleGenerator", "state") is None   # another page
    assert picker_server._take_pending_restore("map_generator", "state")["settings"]["resolution"] == 9
    assert picker_server._take_pending_restore("map_generator", "state") is None     # only once
    assert picker_server._take_pending_restore("map_generator", "settings")["settingsState"] == {"scaleElevation": 2.5}
    assert picker_server._pending_restore is None

    picker_server.set_pending_restore(origin)
    picker_server._pending_restore["at"] -= picker_server._PENDING_RESTORE_SECONDS + 1  # stale
    assert picker_server._take_pending_restore("map_generator", "state") is None


def test_opening_a_map_takes_the_others_out_of_the_scene():
    """Opening a map from My Maps leaves just that one in Blender, so
    switching back and forth doesn't pile up. What wasn't saved stays
    unsaved (the page offers to save first). A map that's gone clears
    nothing."""
    import threading
    from unittest.mock import patch

    from TrailPrint3D import library
    from TrailPrint3D.utils import scene as scene_utils

    with scene_utils.generation_collection(bpy.context, "map", "Map", clear_previous=False) as a:
        _terrain_map("SwitchA")
    with scene_utils.generation_collection(bpy.context, "map", "Map", clear_previous=False) as b:
        _terrain_map("SwitchB", offset=(150.0, 0.0))
    a_id, b_id = library.save_collection(a.collection), library.save_collection(b.collection)
    bpy.data.objects["SwitchB"]["EditedAfterSave"] = True   # never saved

    def open_map(map_id):
        request = {"action": "open", "id": map_id, "done": threading.Event()}
        picker_server._pending_library.put(request)
        with patch.object(picker_server, "_open_in_window", lambda *a, **k: {"ok": True, "next_url": "x"}):
            picker_server.process_library_requests(object())
        return request["result"]

    assert open_map("no-such-map")["ok"] is False
    assert "SwitchA" in bpy.data.objects and "SwitchB" in bpy.data.objects

    assert open_map(a_id)["ok"]
    assert "SwitchA" in bpy.data.objects and "SwitchB" not in bpy.data.objects

    assert open_map(b_id)["ok"]
    assert "SwitchB" in bpy.data.objects and "SwitchA" not in bpy.data.objects
    assert not bpy.data.objects["SwitchB"].get("EditedAfterSave")  # the saved version


def test_configuration_model_switch_finds_the_generators_map():
    """The header's Configuration / Model switch: a generator page is told
    whether its map is in Blender, and "Model" opens Post-processing on
    exactly that map."""
    import threading
    from unittest.mock import patch

    from TrailPrint3D.utils import scene as scene_utils

    with scene_utils.generation_collection(bpy.context, "map", "Map", clear_previous=False):
        _terrain_map("ModelMap")
    other = _terrain_map("Unrelated", offset=(300.0, 0.0))
    _select([other])

    assert picker_server.build_generator_menu("generators/map_generator.html")["hasModel"] is True
    assert picker_server.build_generator_menu("generators/puzzleGenerator.html")["hasModel"] is False
    assert picker_server.build_generator_menu("generators/postProcessGenerator.html")["hasModel"] is False

    request = {"id": "post_process_generator", "from": "map_generator", "done": threading.Event()}
    picker_server._pending_generator_switch.put(request)
    with patch.object(picker_server, "_open_in_window", lambda *a, **k: {"ok": True, "next_url": "x"}):
        assert picker_server.process_generator_switch(object())
    assert [o.name for o in bpy.context.selected_objects] == ["ModelMap"]


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


# ── Trees ────────────────────────────────────────────────────────────────


def _tree_map(name="TreeMap", scale_mm_per_km=1000.0):
    """_terrain_map at 1 mm per ground meter (1:1,000) by default, with the
    top faces of its left half (x < 0) in the FOREST material."""
    obj = _terrain_map(name)
    obj["Horizontal Scale"] = scale_mm_per_km
    obj["latitude"] = 0.0
    mesh = obj.data
    mesh.materials.append(_material("FOREST", (0.05, 0.3, 0.05)))
    for poly in mesh.polygons:
        if poly.normal.z > 0.5 and poly.center.x < 0:
            poly.material_index = 1
    return obj


def _trees_of(map_obj):
    return [o for o in bpy.context.scene.objects
            if o.get("Object type") == "TREES" and o.get("PARENT") == map_obj]


def _all_manifold(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    try:
        return all(e.is_manifold for e in bm.edges)
    finally:
        bm.free()


def test_trees_on_forest_faces_apply_and_undo():
    m = _tree_map()
    trail = _element("Trail", m)
    trail.location = (-20, 0, 16)
    bpy.context.view_layer.update()
    session = _session([m])
    info = session.info(bpy.context)["maps"][0]
    assert info["treesReason"] is None, info["treesReason"]
    assert abs(info["groundMmPerMeter"] - 1.0) < 1e-6

    result = session.handle(bpy.context, {"kind": "apply", "action": "trees",
                                          "params": {"size": 8, "spacing": 12, "shape": "mixed"}})
    assert "Trees applied" in _messages_text(result), _messages_text(result)
    trees = _trees_of(m)
    assert len(trees) == 1, trees
    tree = trees[0]
    assert tree.data.materials[0].name == "FOREST"
    assert _all_manifold(tree), "tree mesh is not closed"
    xs = [tree.matrix_world @ v.co for v in tree.data.vertices]
    # Only on the forest half, and never past the map's edge.
    assert max(v.x for v in xs) < 1.0, max(v.x for v in xs)
    assert min(v.x for v in xs) >= -50.5 and min(v.y for v in xs) >= -50.5
    # Real size: 8 m crowns at 1 mm/m are ~6.4-9.6 mm wide.
    assert len(tree.data.vertices) > 50
    # The forest ground took the base color.
    assert all(p.material_index == 0 for p in m.data.polygons)
    assert m.get("Trees") is True
    assert session.info(bpy.context)["maps"][0]["treesReason"] == "Already has trees"

    # Nothing grows through the trail.
    depsgraph = bpy.context.evaluated_depsgraph_get()
    forest = {"kind": "faces", "slots": {1}, "material": None, "base_slot": 0}
    probe = post_process._SurfaceProbe(m, [trail], forest, depsgraph)
    assert probe.probe(-20, 0) is None

    session.undo(bpy.context)
    assert not _trees_of(m)
    m = bpy.context.scene.objects["TreeMap"]
    assert any(p.material_index == 1 for p in m.data.polygons)
    assert not m.get("Trees")


def test_trees_preview_changes_nothing_and_matches_apply():
    m = _tree_map()
    session = _session([m])
    params = {"size": 8, "spacing": 12, "shape": "mixed"}
    preview = session.handle(bpy.context, {"kind": "preview", "action": "trees", "params": params})["preview"]
    entry = preview["trees"][0]
    stride = preview["stride"]
    assert entry["count"] > 0 and len(entry["trees"]) == entry["count"] * stride, entry["count"]
    assert entry["color"] and entry["color"].startswith("#")
    # Nothing was planted or recolored.
    assert not _trees_of(m)
    assert any(p.material_index == 1 for p in m.data.polygons)
    assert not m.get("Trees")
    # A second preview reuses the probe; other settings give other trees.
    bigger = session.handle(bpy.context, {"kind": "preview", "action": "trees",
                                          "params": dict(params, spacing=24)})["preview"]["trees"][0]
    assert 0 < bigger["count"] < entry["count"], (bigger["count"], entry["count"])
    # Another seed moves the trees; the same seed gives the same ones.
    reseeded = session.handle(bpy.context, {"kind": "preview", "action": "trees",
                                            "params": dict(params, seed=7)})["preview"]["trees"][0]
    assert reseeded["trees"][:2] != entry["trees"][:2]
    again = session.handle(bpy.context, {"kind": "preview", "action": "trees",
                                         "params": params})["preview"]["trees"][0]
    assert again["trees"] == entry["trees"]

    # Trees clicked off in the preview stay out: the first two.
    flat = entry["trees"]
    skipped = [0, 1]
    skip = [f"{flat[i * stride + 7]},{flat[i * stride + 8]}" for i in skipped]
    session.handle(bpy.context, {"kind": "apply", "action": "trees",
                                 "params": dict(params, skip={m.name: skip})})
    tree = _trees_of(m)[0]
    kept = [i for i in range(entry["count"]) if i not in skipped]
    pines = sum(1 for i in kept if flat[i * stride + 5] == 1)
    rounds = len(kept) - pines
    assert len(tree.data.vertices) == pines * 9 + rounds * 41, (len(tree.data.vertices), pines, rounds)
    # No tree stands where a skipped one was.
    xy = [(tree.matrix_world @ v.co).xy for v in tree.data.vertices]
    for i in skipped:
        x, y, r = flat[i * stride], flat[i * stride + 1], flat[i * stride + 4]
        assert not any(abs(p.x - x) < 1e-3 and abs(p.y - y) < 1e-3 for p in xy), (x, y)
    # Planted: the preview has nothing left to show.
    after = session.handle(bpy.context, {"kind": "preview", "action": "trees", "params": params})["preview"]["trees"][0]
    assert after["count"] == 0 and "Already has trees" in after["problem"]


def test_trees_refused_where_they_would_be_too_small():
    m = _tree_map(scale_mm_per_km=20.0)  # 1:50,000
    session = _session([m])
    reason = session.info(bpy.context)["maps"][0]["treesReason"]
    assert reason and "too small" in reason, reason
    result = session.handle(bpy.context, {"kind": "apply", "action": "trees", "params": {}})
    assert not _trees_of(m)
    assert "Trees applied" not in _messages_text(result)


def test_trees_refused_without_forest():
    m = _terrain_map("NoForest")
    m["Horizontal Scale"] = 1000.0
    m["latitude"] = 0.0
    reason = _session([m]).info(bpy.context)["maps"][0]["treesReason"]
    assert reason == "No forest on this map", reason


def test_trees_on_textured_forest_replace_its_pixels():
    from TrailPrint3D.utils.color_styles import COLOR_ROLES_PROP

    m = _terrain_map("TexTrees")
    m["Horizontal Scale"] = 1000.0
    m["latitude"] = 0.0
    mesh = m.data
    uv = mesh.uv_layers.new(name="UVMap")
    for loop in mesh.loops:
        co = mesh.vertices[loop.vertex_index].co
        uv.data[loop.index].uv = ((co.x + 50) / 100, (co.y + 50) / 100)
    forest, base = (30, 90, 30), (200, 200, 200)
    image = bpy.data.images.new(f"{mesh.name}_MMU_Paint", width=2, height=1)
    image.pixels.foreach_set([*(c / 255 for c in forest), 1.0, *(c / 255 for c in base), 1.0])
    mat = bpy.data.materials.new("PaintTex")
    mat.use_nodes = True
    tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = image
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    mesh.materials.clear()
    mesh.materials.append(mat)
    mesh["3mf_is_paint_texture"] = True
    mesh[COLOR_ROLES_PROP] = json.dumps({"FOREST": "#1e5a1e", "BASE": "#c8c8c8"})

    session = _session([m])
    result = session.handle(bpy.context, {"kind": "apply", "action": "trees",
                                          "params": {"size": 8, "spacing": 12, "shape": "pine"}})
    assert "Trees applied" in _messages_text(result), _messages_text(result)
    tree = _trees_of(m)[0]
    xs = [(tree.matrix_world @ v.co).x for v in tree.data.vertices]
    assert max(xs) < 1.0, max(xs)
    from TrailPrint3D.utils.color import material_to_srgb
    assert tuple(material_to_srgb(tree.data.materials[0])) == forest
    px = list(image.pixels)
    assert [round(c * 255) for c in px[:3]] == list(base), px[:3]

    session.undo(bpy.context)
    assert [round(c * 255) for c in list(image.pixels)[:3]] == list(forest)


def test_trees_on_forest_element_objects():
    m = _terrain_map("ObjTrees")
    m["Horizontal Scale"] = 1000.0
    m["latitude"] = 0.0
    bpy.ops.mesh.primitive_cube_add(size=1, location=(-25, 0, 20))
    slab = bpy.context.object
    slab.scale = (50, 100, 1)
    bpy.ops.object.transform_apply(scale=True)
    slab.name = "ObjTrees_FOREST"
    slab["Object type"] = "FOREST"
    slab.data.materials.append(_material("FOREST", (0.05, 0.3, 0.05)))
    bpy.context.view_layer.update()

    session = _session([m])
    assert session.info(bpy.context)["maps"][0]["treesReason"] is None
    result = session.handle(bpy.context, {"kind": "apply", "action": "trees",
                                          "params": {"size": 8, "spacing": 12, "shape": "round"}})
    assert "Trees applied" in _messages_text(result), _messages_text(result)
    tree = _trees_of(m)[0]
    zs = [(tree.matrix_world @ v.co).z for v in tree.data.vertices]
    assert min(zs) > 20.0, min(zs)  # standing on the slab, sunk into it a little
    assert slab.data.materials[0].name == "BASE"


# ── Procedural Snow ──────────────────────────────────────────────────────


_SNOW = {"coverage": 40, "snowline": 50, "softness": 20, "maxSlope": 45, "slopeSoftness": 10,
         "smoothing": 2, "hollows": 0.3, "amplitude": 0.3, "scale": 0.3, "seed": 0,
         "minPatch": 1.5}
# What the page sends.
_SNOW_PAGE = {"coverage": 40, "snowline": 50, "maxSlope": 45, "patchiness": 0.3, "patchSize": 3.5, "seed": 0}


def _snow_surface(obj):
    from TrailPrint3D import operators
    from TrailPrint3D.utils.snow import SnowSurface

    low, high = operators._color_height_range(obj)
    z = obj.matrix_world.translation.z
    return SnowSurface(obj, bpy.context.evaluated_depsgraph_get(), (low + z, high + z))


def _flat_map(name="FlatSnow"):
    obj = _terrain_map(name)
    for v in obj.data.vertices:
        if v.co.z > 0.5:
            v.co.z = 5.0
    obj.data.update()
    bpy.context.view_layer.update()
    return obj


def _snow_components(surface, covered):
    """Area of every connected snow patch."""
    adjacency = surface.adjacency()
    seen = set()
    sizes = []
    for start in [int(f) for f in covered.nonzero()[0]]:
        if start in seen:
            continue
        stack, area = [start], 0.0
        seen.add(start)
        while stack:
            f = stack.pop()
            area += surface.areas[f]
            for nb in adjacency[f]:
                if covered[nb] and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        sizes.append(area)
    return sizes


def _snow_faces(obj):
    names = [m.name if m else "" for m in obj.data.materials]
    return [p for p in obj.data.polygons if names[p.material_index] == "HEIGHT_FFFFFF"]


def test_snow_paints_faces_preview_apply_and_undo():
    m = _terrain_map("SnowMap")
    session = _session([m])
    preview = session.handle(bpy.context, {"kind": "preview", "action": "snow", "params": _SNOW_PAGE})["preview"]
    entry = preview["snow"][0]
    assert entry["faces"] > 0 and entry["percent"] > 0, entry
    assert len(entry["triangles"]) == entry["faces"] * 9
    assert not _snow_faces(m), "a preview must not change the scene"

    result = session.handle(bpy.context, {"kind": "apply", "action": "snow", "params": _SNOW_PAGE})
    assert "Snow applied" in _messages_text(result), _messages_text(result)
    snowed = _snow_faces(m)
    assert snowed, "no face got the snow material"
    # Painted, nothing added.
    assert not [o for o in bpy.context.scene.objects if o.get("Object type") == "SNOW"]
    # On the upper part of the hill (snow threshold 50 % -- 3..15 mm --
    # with its 15 % soft band).
    mw = m.matrix_world
    assert min((mw @ p.center).z for p in snowed) > 3.0 + 12 * 0.42, min((mw @ p.center).z for p in snowed)
    assert "HEIGHT_FFFFFF" in list(m.get("heightColorMaterials", []))
    assert m.get("Snow") is True
    again = session.handle(bpy.context, {"kind": "apply", "action": "snow", "params": _SNOW_PAGE})
    assert "already has snow" in _messages_text(again), _messages_text(again)

    session.undo(bpy.context)
    m = bpy.context.scene.objects["SnowMap"]
    assert not _snow_faces(m) and not m.get("Snow")


def test_snow_leaves_water_faces_alone():
    m = _terrain_map("SnowWater")
    m.data.materials.append(_material("WATER", (0.1, 0.3, 0.8)))
    water = [p.index for p in m.data.polygons if p.normal.z > 0.5 and abs(p.center.x) < 6 and abs(p.center.y) < 6]
    for i in water:
        m.data.polygons[i].material_index = 1
    session = _session([m])
    session.handle(bpy.context, {"kind": "apply", "action": "snow",
                                 "params": dict(_SNOW_PAGE, coverage=100, snowline=0)})
    assert _snow_faces(m)
    assert all(m.data.polygons[i].material_index == 1 for i in water), "snow painted over water"


def _textured_hill(name="TexSnow", res=128):
    """_terrain_map painted on a res x res texture (planar UVs over the
    top), all base color except a water strip through the middle."""
    from TrailPrint3D.utils.color_styles import COLOR_ROLES_PROP

    m = _terrain_map(name)
    mesh = m.data
    uv = mesh.uv_layers.new(name="UVMap")
    for loop in mesh.loops:
        co = mesh.vertices[loop.vertex_index].co
        uv.data[loop.index].uv = ((co.x + 50) / 100, (co.y + 50) / 100)
    base, water = (200, 200, 200), (30, 80, 200)
    px = [[c / 255 for c in base] + [1.0] for _ in range(res * res)]
    for r in range(res):
        for c in range(res // 2 - 3, res // 2 + 3):
            px[r * res + c] = [v / 255 for v in water] + [1.0]
    image = bpy.data.images.new(f"{mesh.name}_MMU_Paint", width=res, height=res)
    image.pixels.foreach_set([v for p in px for v in p])
    mat = bpy.data.materials.new(f"{name}_Paint")
    mat.use_nodes = True
    tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = image
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    mesh.materials.clear()
    mesh.materials.append(mat)
    mesh["3mf_is_paint_texture"] = True
    mesh[COLOR_ROLES_PROP] = json.dumps({"BASE": "#C8C8C8", "WATER": "#1E50C8"})
    mesh["3mf_paint_extruder_colors"] = str({0: "#C8C8C8", 1: "#1E50C8"})
    return m, image, base, water, res


def test_snow_paints_the_texture_per_pixel():
    import numpy as _np

    m, image, base, water, res = _textured_hill()
    before = _np.array(image.pixels[:]).reshape(res, res, 4)
    session = _session([m])
    result = session.handle(bpy.context, {"kind": "apply", "action": "snow",
                                          "params": dict(_SNOW_PAGE, coverage=60)})
    assert "Snow applied" in _messages_text(result), _messages_text(result)
    after = _np.array(image.pixels[:]).reshape(res, res, 4)
    rgb = _np.rint(after[..., :3] * 255)
    white = (rgb == 255).all(axis=-1)
    assert white.sum() > 50, white.sum()
    # The water strip is untouched; snow sits around the hill's top
    # (the middle of the texture), not out at the flat edges.
    strip = slice(res // 2 - 3, res // 2 + 3)
    assert _np.allclose(after[:, strip], before[:, strip])
    rows, cols = _np.nonzero(white)
    dist = _np.hypot(rows - res / 2, cols - res / 2)
    assert dist.mean() < res * 0.3, dist.mean()
    # The base-colour anchor block stays, and white is in the 3MF palette.
    assert (_np.rint(after[0:4, 0:4, :3] * 255) == base).all()
    assert "#FFFFFF" in m.data["3mf_paint_extruder_colors"]
    assert not _snow_faces(m), "a textured map is painted in its texture, not by faces"
    # What's painted is what the preview showed: every covered face is
    # white at its centre (unless it's water), every other face isn't.
    p = post_process.PostProcessSession._snow_params(dict(_SNOW_PAGE, coverage=60))
    surface = _snow_surface(m)
    covered = surface.cover(p)
    on_terrain = surface.terrain
    col = _np.clip(((surface.centroids[:, 0] + 50) / 100 * res).astype(int), 0, res - 1)
    row = _np.clip(((surface.centroids[:, 1] + 50) / 100 * res).astype(int), 0, res - 1)
    is_water = (rgb[row, col] == water).all(axis=-1)
    assert white[row, col][covered & on_terrain & ~is_water].all()
    assert not white[row, col][~covered & on_terrain].any()

    session.undo(bpy.context)
    assert _np.allclose(_np.array(image.pixels[:]).reshape(res, res, 4), before)


def test_snow_page_settings_and_fixed_rest():
    from TrailPrint3D.utils import snow

    p = post_process.PostProcessSession._snow_params(
        {"coverage": 30, "snowline": 60, "maxSlope": 40, "patchiness": 0.5, "patchSize": 4, "seed": 3,
         # Gone from the page: ignored if an old page still sends them.
         "softness": 99, "hollows": 1, "smoothing": 40, "minPatch": 20, "thickness": 5})
    assert (p["coverage"], p["snowline"], p["maxSlope"], p["amplitude"], p["seed"]) == (30, 60, 40, 0.5, 3)
    assert abs(p["scale"] - 0.25) < 1e-12  # 4 mm patches
    assert (p["softness"], p["slopeSoftness"], p["hollows"], p["minPatch"]) == (
        snow.THRESHOLD_SOFTNESS, snow.SLOPE_SOFTNESS_DEG, snow.HOLLOWS, snow.MIN_PATCH_MM)
    assert p["smoothing"] is None and "thickness" not in p
    # Automatic smoothing: 4 of the map's triangles wide (100 mm / 24 grid
    # = 4.2 mm each), capped at 10 mm.
    surface = _snow_surface(_terrain_map("AutoSmooth"))
    assert abs(surface.auto_smoothing - min(4 * 100 / 24, 10.0)) < 1e-6, surface.auto_smoothing
    assert surface.cover(p).any()


def test_snow_on_a_flat_map():
    surface = _snow_surface(_flat_map())
    covered = surface.cover(dict(_SNOW, snowline=0))
    share = surface.areas[covered].sum() / surface.areas[surface.terrain].sum()
    assert 0.25 < share < 0.55, share  # about the 40 % coverage, after the min-patch cleanup


def test_snow_coverage_snowline_and_slope():
    import math as _m

    surface = _snow_surface(_terrain_map("SnowRules"))

    def share(**kw):
        covered = surface.cover(dict(_SNOW, **kw))
        return covered, surface.areas[covered].sum() / surface.areas[surface.terrain].sum()

    _c, low = share(coverage=20)
    _c, high = share(coverage=60)
    assert 0 < low < high, (low, high)
    # Above the top of the hill: nothing.
    nothing, _s = share(snowline=100, softness=0)
    assert not nothing.any()
    # A hard snow threshold: nothing below it.
    hard, _s = share(snowline=70, softness=0, coverage=100, minPatch=0)
    assert hard.any() and (surface.centroids[hard, 2] >= 3.0 + 12 * 0.7 - 1e-6).all()
    # A low max slope: only gentle ground.
    gentle, _s = share(maxSlope=8, slopeSoftness=0, coverage=100, minPatch=0, snowline=0, smoothing=0)
    normals = surface.normals[gentle]
    assert gentle.any() and (normals[:, 2] >= _m.cos(_m.radians(8)) - 1e-9).all()


def test_snow_min_patch_cleans_up_specks():
    surface = _snow_surface(_flat_map("SpeckSnow"))
    p = dict(_SNOW, snowline=0, hollows=0, amplitude=1.0, scale=1.5, coverage=50)
    raw = surface.cover(dict(p, minPatch=0))
    clean = surface.cover(dict(p, minPatch=4))
    raw_sizes, clean_sizes = _snow_components(surface, raw), _snow_components(surface, clean)
    assert len(clean_sizes) < len(raw_sizes), (len(raw_sizes), len(clean_sizes))
    assert min(clean_sizes) >= 16.0 - 1e-6, min(clean_sizes)


def test_snow_smoothing_reads_terraces_as_the_slope_they_form():
    """A 25° hillside of flat steps and steep risers: judged face by face
    the steps are flat and get snow under a 20° max slope; smoothed, the
    hillside is 25° steep and gets none."""
    half = 50.0
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=200, y_segments=20, size=half)
    for v in bm.verts:
        ramp = (half - v.co.x) * math.tan(math.radians(25))
        v.co.z = 3.0 + math.floor(ramp / 1.9) * 1.9
    ret = bmesh.ops.extrude_face_region(bm, geom=list(bm.faces))
    for v in [e for e in ret["geom"] if isinstance(e, bmesh.types.BMVert)]:
        v.co.z = 0.0
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    mesh = bpy.data.meshes.new("Terraces")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Terraces", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj["Object type"] = "MAP"
    bpy.context.view_layer.update()

    surface = _snow_surface(obj)
    p = dict(_SNOW, snowline=0, coverage=100, maxSlope=20, slopeSoftness=0, minPatch=0)
    assert surface.cover(dict(p, smoothing=0)).any()
    assert not surface.cover(dict(p, smoothing=4)).any()
    # Never on the map's side walls.
    assert not surface.wall[surface.cover(dict(p, smoothing=0))].any()


# ── Color palette ────────────────────────────────────────────────────────


def _role_hex(obj, role):
    from TrailPrint3D.utils.color import material_to_srgb
    from TrailPrint3D.utils.color_styles import material_role, srgb_to_hex

    for slot in obj.material_slots:
        if material_role(slot.material) == role:
            return srgb_to_hex(material_to_srgb(slot.material))
    return None


def test_palette_recolors_the_map_and_undo_puts_it_back():
    from TrailPrint3D.utils import color_styles

    m = _terrain_map("PaletteMap")
    tp3d = bpy.context.scene.tp3d
    scene_before = color_styles.role_hex_map(color_styles.scene_colors(tp3d))
    style_before = tp3d.color_style
    map_before = _role_hex(m, "BASE")
    session = _session([m])
    info = session.info(bpy.context)
    assert info["palettes"]["builtIn"] and info["mapColors"]["BASE"] == map_before

    result = session.handle(bpy.context, {"kind": "apply", "action": "colors",
                                          "params": {"colors": {"BASE": "#112233"}, "style": ""}})
    assert "Colors applied" in _messages_text(result), _messages_text(result)
    assert _role_hex(m, "BASE") == "#112233"
    assert session.info(bpy.context)["mapColors"]["BASE"] == "#112233"
    # It's the scene's palette now too (what the next generation uses).
    assert color_styles.role_hex_map(color_styles.scene_colors(tp3d))["BASE"] == "#112233"
    # A built-in palette by id.
    style = next(iter(color_styles.BUILT_IN_STYLES))
    colors = color_styles.role_hex_map(color_styles.style_colors(style))
    session.handle(bpy.context, {"kind": "apply", "action": "colors", "params": {"colors": colors, "style": style}})
    assert _role_hex(m, "BASE") == colors["BASE"] and tp3d.color_style == style

    session.undo(bpy.context)
    session.undo(bpy.context)
    m = bpy.context.scene.objects["PaletteMap"]
    assert _role_hex(m, "BASE") == map_before, (_role_hex(m, "BASE"), map_before)
    assert color_styles.role_hex_map(color_styles.scene_colors(tp3d)) == scene_before
    assert tp3d.color_style == style_before


def test_palette_recolors_a_painted_texture():
    import numpy as _np

    m, image, base, water, res = _textured_hill("PaletteTex")
    session = _session([m])
    before = _np.array(image.pixels[:]).reshape(res, res, 4)
    session.handle(bpy.context, {"kind": "apply", "action": "colors",
                                 "params": {"colors": {"BASE": "#336699"}, "style": ""}})
    rgb = _np.rint(_np.array(image.pixels[:]).reshape(res, res, 4)[..., :3] * 255)
    assert (rgb == (0x33, 0x66, 0x99)).all(axis=-1).sum() > res * res // 2
    assert (rgb == water).all(axis=-1).any(), "water should keep its color"
    session.undo(bpy.context)
    assert _np.allclose(_np.array(image.pixels[:]).reshape(res, res, 4), before)


def test_palette_save_and_delete():
    from TrailPrint3D import constants
    from TrailPrint3D.utils import color_styles

    real_dir = constants.color_style_dir
    constants.color_style_dir = os.path.join(_TMP.name, "color_styles")  # never the user's own
    try:
        m = _terrain_map("PaletteSave")
        session = _session([m])
        undo_before = len(session.info(bpy.context)["undo"])
        result = session.handle(bpy.context, {"kind": "palette", "params": {
            "op": "save", "name": "Winter Test", "colors": {"BASE": "#E0E8F0", "FOREST": "#2F5530"}}})
        assert "Saved palette" in _messages_text(result), _messages_text(result)
        info = session.info(bpy.context)
        saved = [s for s in info["palettes"]["user"] if s["name"] == "Winter Test"]
        assert saved and saved[0]["colors"]["BASE"] == "#E0E8F0" and saved[0]["colors"]["FOREST"] == "#2F5530"
        assert bpy.context.scene.tp3d.color_style == "USER:Winter Test"
        assert len(info["undo"]) == undo_before, "saving a palette changes nothing on the map"

        result = session.handle(bpy.context, {"kind": "palette", "params": {"op": "delete", "style": "USER:Winter Test"}})
        assert "Deleted palette" in _messages_text(result), _messages_text(result)
        assert not [s for s in session.info(bpy.context)["palettes"]["user"] if s["name"] == "Winter Test"]
        # Built-in palettes can't be deleted.
        builtin = next(iter(color_styles.BUILT_IN_STYLES))
        refused = session.handle(bpy.context, {"kind": "palette", "params": {"op": "delete", "style": builtin}})
        assert refused["messages"][0]["level"] == "ERROR"
    finally:
        constants.color_style_dir = real_dir


def main():
    print(f"Post-processing tests (premium={bool(temp.PREMIUMVERSION)})")
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            _run(name, fn)
    print(f"\n{_passed} passed, {_failed} failed")
    if _failed:
        raise SystemExit(1)


main()
