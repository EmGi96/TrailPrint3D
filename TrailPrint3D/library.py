#  Copyright (C) 2026  EmGi
"""Map library ("My Maps" in the generator pages).

Every generation's collection (utils.scene.generation_collection) is saved
on its own into the library folder (addon_preferences.get_library_folder)
-- no save dialog, nothing to remember, and the open .blend is never
touched:

    <id>.blend  the collection with its objects, meshes, materials and
                packed paint textures (bpy.data.libraries.write)
    <id>.json   name, kind, dates, counts -- what the gallery lists
    <id>.png    a top-down thumbnail

The collection keeps the id (LIBRARY_ID_PROP), so later Post-processing
changes overwrite the same entry, and opening it again finds the copy that
is already in the scene.

list_maps / thumbnail_path / delete_map are plain file work (safe from the
picker server's request threads); the rest needs Blender's main thread.
"""

import json
import os
import re
import time
import uuid

import bpy  # type: ignore
import numpy as np

LIBRARY_ID_PROP = "tp3d_library_id"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
THUMB_SIZE = 256


def library_dir() -> str:
    from .addon_preferences import get_library_folder

    return get_library_folder()


def valid_id(map_id) -> bool:
    return isinstance(map_id, str) and bool(_ID_RE.match(map_id))


def _paths(folder, map_id):
    base = os.path.join(folder, map_id)
    return base + ".blend", base + ".json", base + ".png"


def _new_id(name) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")[:40] or "map"
    return f"{slug}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


# ── Saving (main thread) ─────────────────────────────────────────────────


def _pack_images(collection):
    """Pack the images the collection's materials use that only live in
    memory (paint textures) -- otherwise the saved file loses them."""
    for obj in collection.all_objects:
        for mat in getattr(obj.data, "materials", ()) if obj.data is not None else ():
            if mat is None or not mat.use_nodes or mat.node_tree is None:
                continue
            for node in mat.node_tree.nodes:
                image = getattr(node, "image", None) if node.type == "TEX_IMAGE" else None
                if image is None or image.packed_file is not None or not image.has_data:
                    continue
                try:
                    image.pack()
                except RuntimeError as e:
                    print(f"[TP3D library] could not pack {image.name}: {e}")


def save_collection(collection, thumbnail=True):
    """Save *collection* into the library (new entry, or over its own one).
    *thumbnail* False keeps the entry's picture (quicker -- for a last save
    of something that's already been saved). Returns the entry id, or None
    if it couldn't be written."""
    from .post_process import collection_targets
    from .utils.scene import GENERATION_COLLECTION_PROP

    folder = library_dir()
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as e:
        print(f"[TP3D library] no library folder {folder}: {e}")
        return None
    map_id = collection.get(LIBRARY_ID_PROP)
    if not valid_id(map_id):
        map_id = _new_id(collection.name)
        collection[LIBRARY_ID_PROP] = map_id
    blend_path, meta_path, thumb_path = _paths(folder, map_id)

    _pack_images(collection)
    tmp = blend_path + ".tmp"
    try:
        bpy.data.libraries.write(tmp, {collection}, path_remap="ABSOLUTE", fake_user=False, compress=True)
        os.replace(tmp, blend_path)
    except (OSError, RuntimeError, ValueError) as e:
        print(f"[TP3D library] could not save {collection.name}: {e}")
        try:
            os.remove(tmp)
        except OSError:
            pass
        return None

    previous = _read_meta(meta_path) or {}
    names, puzzle = collection_targets(collection)
    now = time.time()
    meta = {
        "id": map_id,
        "name": collection.name,
        "collection": collection.name,
        "kind": str(collection.get(GENERATION_COLLECTION_PROP) or ""),
        "created": previous.get("created", now),
        "modified": now,
        "maps": len(names),
        "puzzle": bool(puzzle),
        "objects": len(collection.all_objects),
    }
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=1)
    except OSError as e:
        print(f"[TP3D library] could not write {meta_path}: {e}")
    try:
        if thumbnail or not os.path.isfile(thumb_path):
            write_thumbnail(collection, thumb_path)
    except (OSError, RuntimeError, ValueError) as e:
        print(f"[TP3D library] no thumbnail for {collection.name}: {e}")
    return map_id


def _srgb(linear):
    linear = np.clip(linear, 0.0, 1.0)
    return np.where(linear <= 0.0031308, linear * 12.92, 1.055 * np.power(linear, 1 / 2.4) - 0.055)


# Triangles a thumbnail reads at most -- plenty for 256 px (a terrain mesh
# has far more than one per pixel), and keeps a save after each change quick.
_THUMB_MAX_TRIANGLES = 250_000


def _thumbnail_triangles(collection):
    """(triangles (M, 3, 3) world mm, colors (M, 3) linear) of the
    collection's visible parts: an even spread of at most
    _THUMB_MAX_TRIANGLES triangles, colored at their centers (the page's
    viewer colors, post_process._material_info)."""
    from .post_process import _FALLBACK_SRGB, _material_info, _srgb_to_linear

    depsgraph = bpy.context.evaluated_depsgraph_get()
    objects = [o for o in collection.all_objects if o.type in {"MESH", "CURVE", "FONT"} and not o.hide_render]
    meshes = []
    total = 0
    for obj in objects:
        evaluated = obj.evaluated_get(depsgraph)
        try:
            mesh = evaluated.to_mesh()
        except RuntimeError:
            continue
        if mesh is None:
            continue
        mesh.calc_loop_triangles()
        meshes.append((obj, evaluated, mesh))
        total += len(mesh.loop_triangles)
    stride = max(1, int(np.ceil(total / _THUMB_MAX_TRIANGLES)))
    cache = {}
    tris, colors = [], []
    try:
        for obj, evaluated, mesh in meshes:
            n = len(mesh.loop_triangles)
            if not n:
                continue
            # Small parts (connectors, text...) keep every triangle -- with
            # few, large ones, skipping some would leave gaps.
            pick = np.arange(0, n, stride if n > 20_000 else 1)
            tri_verts = np.empty(n * 3, dtype=np.int32)
            mesh.loop_triangles.foreach_get("vertices", tri_verts)
            tri_loops = np.empty(n * 3, dtype=np.int32)
            mesh.loop_triangles.foreach_get("loops", tri_loops)
            tri_mats = np.empty(n, dtype=np.int32)
            mesh.loop_triangles.foreach_get("material_index", tri_mats)
            tri_verts = tri_verts.reshape(-1, 3)[pick]
            tri_loops = tri_loops.reshape(-1, 3)[pick]
            tri_mats = tri_mats[pick]
            co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
            mesh.vertices.foreach_get("co", co)
            mw = np.array(obj.matrix_world, dtype=np.float64)
            world = co.reshape(-1, 3).astype(np.float64) @ mw[:3, :3].T + mw[:3, 3]
            tris.append(world[tri_verts])

            col = np.empty((len(pick), 3), dtype=np.float32)
            slots = [slot.material for slot in evaluated.material_slots] or [None]
            uv = None
            for index in np.unique(tri_mats):
                material = slots[index] if 0 <= index < len(slots) else None
                kind, value = _material_info(material, cache)
                mask = tri_mats == index
                if kind == "texture":
                    if uv is None:
                        layer = mesh.uv_layers.active
                        if layer is None:
                            col[mask] = _srgb_to_linear(np.array(_FALLBACK_SRGB) / 255.0)
                            continue
                        uv = np.empty(len(mesh.loops) * 2, dtype=np.float32)
                        layer.data.foreach_get("uv", uv)
                        uv = uv.reshape(-1, 2)
                    h, w = value.shape[:2]
                    center = uv[tri_loops[mask]].mean(axis=1)
                    px = np.clip((center[:, 0] * w).astype(np.int64), 0, w - 1)
                    py = np.clip((center[:, 1] * h).astype(np.int64), 0, h - 1)
                    col[mask] = _srgb_to_linear(value[py, px, :3])
                else:
                    col[mask] = value
            colors.append(col)
    finally:
        for _obj, evaluated, _mesh in meshes:
            evaluated.to_mesh_clear()
    if not tris:
        return None, None
    return np.concatenate(tris), np.concatenate(colors).astype(np.float64)


def write_thumbnail(collection, path, size=THUMB_SIZE):
    """A top-down picture of *collection* (shaded by slope), as PNG at *path*."""
    tri, col = _thumbnail_triangles(collection)
    if tri is None:
        return
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    length = np.linalg.norm(normal, axis=1)
    keep = (length > 1e-12) & (normal[:, 2] > 0)
    tri, col, normal, length = tri[keep], col[keep], normal[keep], length[keep]
    if not len(tri):
        return
    normal /= length[:, None]
    light = np.array([-0.45, 0.55, 1.0])
    light /= np.linalg.norm(light)
    col = col * np.clip(normal @ light, 0.35, 1.0)[:, None]

    xy = tri[:, :, :2]
    lo, hi = xy.reshape(-1, 2).min(axis=0), xy.reshape(-1, 2).max(axis=0)
    extent = max(float((hi - lo).max()), 1e-6)
    margin = 0.06
    scale = size * (1 - 2 * margin) / extent
    offset = (size - (hi - lo) * scale) / 2

    # Points spread over each triangle (more on big ones), the highest one
    # per pixel wins.
    area_px = 0.5 * np.abs(np.cross(xy[:, 1] - xy[:, 0], xy[:, 2] - xy[:, 0])) * scale ** 2
    count = np.clip(np.ceil(area_px), 1, 2000).astype(np.int64)
    budget = 600_000
    if count.sum() > budget:
        count = np.maximum(1, (count * budget // count.sum()))
    idx = np.repeat(np.arange(len(tri)), count)
    rng = np.random.default_rng(0)
    u = rng.random(len(idx))
    v = rng.random(len(idx))
    flip = u + v > 1
    u[flip], v[flip] = 1 - u[flip], 1 - v[flip]
    t = tri[idx]
    pts = t[:, 0] + u[:, None] * (t[:, 1] - t[:, 0]) + v[:, None] * (t[:, 2] - t[:, 0])
    px = np.clip(((pts[:, 0] - lo[0]) * scale + offset[0]).astype(np.int64), 0, size - 1)
    py = np.clip(((pts[:, 1] - lo[1]) * scale + offset[1]).astype(np.int64), 0, size - 1)
    order = np.argsort(pts[:, 2], kind="stable")
    img = np.zeros((size, size, 4), dtype=np.float32)
    img[py[order], px[order], :3] = _srgb(col[idx][order])
    img[py[order], px[order], 3] = 1.0

    # Close the odd pixel no point landed on.
    holes = img[:, :, 3] == 0
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        shifted = np.roll(np.roll(img, dy, axis=0), dx, axis=1)
        fill = holes & (shifted[:, :, 3] > 0)
        img[fill] = shifted[fill]
        holes &= ~fill

    image = bpy.data.images.new("tp3d_library_thumb", width=size, height=size, alpha=True)
    try:
        image.pixels.foreach_set(img.ravel())
        image.filepath_raw = path
        image.file_format = "PNG"
        image.save()
    finally:
        bpy.data.images.remove(image)


# ── Loading (main thread) ────────────────────────────────────────────────


def find_in_scene(scene, map_id):
    for collection in scene.collection.children_recursive:
        if collection.get(LIBRARY_ID_PROP) == map_id:
            return collection
    return None


def load_map(scene, map_id):
    """The library entry *map_id* as a collection in *scene* -- the copy
    already there, or loaded from its file. None if there's no such entry."""
    if not valid_id(map_id):
        return None
    existing = find_in_scene(scene, map_id)
    if existing is not None:
        return existing
    folder = library_dir()
    blend_path, meta_path, _thumb = _paths(folder, map_id)
    if not os.path.isfile(blend_path):
        return None
    meta = _read_meta(meta_path) or {}
    with bpy.data.libraries.load(blend_path, link=False) as (data_from, data_to):
        names = list(data_from.collections)
        wanted = meta.get("collection")
        data_to.collections = [wanted] if wanted in names else names[:1]
    loaded = [c for c in data_to.collections if c is not None]
    if not loaded:
        return None
    collection = loaded[0]
    scene.collection.children.link(collection)
    collection[LIBRARY_ID_PROP] = map_id
    # Renamed in "My Maps" while it wasn't in the scene.
    if meta.get("name") and collection.name != meta["name"]:
        collection.name = meta["name"]
    return collection


# ── Plain file work (any thread) ─────────────────────────────────────────


def _read_meta(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def list_maps(folder):
    """Every saved map in *folder*, newest change first."""
    result = []
    try:
        names = os.listdir(folder)
    except OSError:
        return result
    for name in names:
        if not name.endswith(".json"):
            continue
        map_id = name[:-5]
        if not valid_id(map_id):
            continue
        blend_path, meta_path, thumb_path = _paths(folder, map_id)
        meta = _read_meta(meta_path)
        if meta is None or not os.path.isfile(blend_path):
            continue
        result.append({
            "id": map_id,
            "name": str(meta.get("name") or map_id),
            "kind": str(meta.get("kind") or ""),
            "modified": float(meta.get("modified") or os.path.getmtime(blend_path)),
            "maps": int(meta.get("maps") or 0),
            "puzzle": bool(meta.get("puzzle")),
            "size": os.path.getsize(blend_path),
            "thumb": os.path.isfile(thumb_path),
        })
    result.sort(key=lambda m: m["modified"], reverse=True)
    return result


def has_map(folder, map_id) -> bool:
    """Whether *folder* holds library entry *map_id* (its .blend)."""
    return valid_id(map_id) and os.path.isfile(_paths(folder, map_id)[0])


def thumbnail_path(folder, map_id):
    if not valid_id(map_id):
        return None
    path = _paths(folder, map_id)[2]
    return path if os.path.isfile(path) else None


def delete_map(folder, map_id) -> bool:
    """Remove the entry's files. True if there was one."""
    if not valid_id(map_id):
        return False
    found = False
    for path in _paths(folder, map_id):
        try:
            os.remove(path)
            found = True
        except FileNotFoundError:
            pass
    return found


# ── Default names (main thread) ──────────────────────────────────────────
# What a generation is called in "My Maps" (and its collection): a name the
# user gave (typed, or the GPX track's), else the place the map shows, else
# its coordinates and the date -- "Terrain" for every map isn't telling.

_PLACEHOLDER_NAMES = {"", "terrain"}
_KIND_PREFIX = {
    "multitile": "Multi-tile",
    "puzzle": "Jigsaw",
    "sliding_puzzle": "Sliding",
    "medal_holder": "Medal Holder",
    "backplate": "Backplate",
}
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
_USER_AGENT = "TrailPrint3D_3.00"
_place_cache = {}


def _collection_center(collection):
    """(lat, lon) the collection's maps were generated around, or None."""
    lats, lons = [], []
    for obj in collection.all_objects:
        if obj.get("Object type") != "MAP":
            continue
        try:
            lat, lon = float(obj["latitude"]), float(obj["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        lats.append(lat)
        lons.append(lon)
    if not lats:
        return None
    return sum(lats) / len(lats), sum(lons) / len(lons)


def place_name(lat, lon, timeout=4.0):
    """The town / village / area at (*lat*, *lon*) from OpenStreetMap's
    Nominatim, or None -- offline (Blender's Allow Online Access off), no
    answer in *timeout* seconds, or nothing named there. Only the
    coordinates are sent."""
    import urllib.error
    import urllib.parse
    import urllib.request

    if not getattr(bpy.app, "online_access", True):
        return None
    key = (round(lat, 3), round(lon, 3))
    if key in _place_cache:
        return _place_cache[key]
    query = urllib.parse.urlencode({"format": "jsonv2", "lat": f"{lat:.5f}", "lon": f"{lon:.5f}", "zoom": 12})
    request = urllib.request.Request(f"{_NOMINATIM_URL}?{query}", headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"[TP3D library] no place name for {lat:.4f}, {lon:.4f}: {e}")
        return None
    if not isinstance(data, dict):
        data = {}
    address = data.get("address") or {}
    name = None
    for field in ("village", "town", "city", "hamlet", "municipality", "suburb",
                  "natural", "peak", "county", "state", "region", "country"):
        value = address.get(field)
        if value:
            name = str(value).strip()
            break
    if not name and data.get("name"):
        name = str(data["name"]).strip()
    _place_cache[key] = name or None
    return _place_cache[key]


def _coordinate_name(lat, lon):
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{abs(lat):.2f}° {ns} {abs(lon):.2f}° {ew}"


def _taken_names(scene):
    names = {c.name for c in bpy.data.collections}
    try:
        names.update(m["name"] for m in list_maps(library_dir()))
    except OSError:
        pass
    return names


def default_name(scene, collection, kind):
    """The name a new generation gets (see the section comment)."""
    given = str(getattr(scene.tp3d, "modelname", "") or "").strip()
    if given.lower() not in _PLACEHOLDER_NAMES:
        base = given
    else:
        center = _collection_center(collection)
        base = None
        if center is not None:
            base = place_name(*center) or f"{_coordinate_name(*center)} · {time.strftime('%d %b')}"
        if not base:
            base = time.strftime("%d %b %Y %H:%M")
    prefix = _KIND_PREFIX.get(kind)
    name = f"{prefix} · {base}" if prefix else base
    return unique_name(scene, collection, name)


def unique_name(scene, collection, name):
    """*name* (cut to 60 characters) for *collection*, with " 2", " 3"...
    added if another collection or a saved map already has it."""
    name = " ".join(str(name or "").split())[:60].strip()
    taken = _taken_names(scene) - {collection.name}
    unique, n = name, 2
    while unique in taken:
        unique = f"{name} {n}"
        n += 1
    return unique


def rename_map(scene, map_id, name):
    """Rename library entry *map_id* (and its collection, if it's in the
    scene). Returns the name it got, or None."""
    name = " ".join(str(name or "").split())[:60]
    if not valid_id(map_id) or not name:
        return None
    meta_path = _paths(library_dir(), map_id)[1]
    meta = _read_meta(meta_path)
    if meta is None:
        return None
    collection = find_in_scene(scene, map_id)
    if collection is not None:
        collection.name = name
    meta["name"] = name
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    return name
