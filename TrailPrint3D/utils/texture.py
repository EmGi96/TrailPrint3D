"""
OSM element texture rasterization, now controlled by a boolean in paint mode instead of being a whole generation type.

Rasterizes Shapely polygons (OSM element areas) into a Blender Image,
sets up planar UV coordinates on the terrain mesh, creates a single
material referencing the image, and assigns the three mesh custom
properties expected by the 3MF addon's paint-segmentation export pipeline.

Coordinate conventions
----------------------
- Shapely polygon coordinates are in *world space* (Web Mercator, scaled by
  R * scaleHor).  The terrain object's vertex coords are in *local space*
  (world coords minus cursor location).
- UV (u, v) maps local X → u, local Y → v, both in [0, 1].
- The Blender image stores pixels row-0 = bottom (v = 0), matching Blender's
  UV convention and the layout expected by the 3MF addon's segmentation reader.
"""

from copy import deepcopy
import zlib

import bpy
import numpy as np

from ..constants import (
    _HEIGHT_BAKE_UNDO,
    _KIND_MATERIAL_NAME,
    _MAX_HEIGHT_BAKE_UNDO_MEMORY,
    _RASTER_ORDER,
    _UV_LAYER_NAME,
)
from .color import material_to_srgb
from .color_styles import (
    ROLE_NAMES,
    material_role_colors,
    read_mesh_color_roles,
    write_mesh_color_roles,
)
from .dataclasses import GenerationContext

# ── Helpers ───────────────────────────────────────────────────────────────────

_HEIGHT_BAKE_PALETTE_KEY_PROP = "3mf_height_bake_palette_key"


def _srgb_to_hex(r8, g8, b8):
    return f"#{int(r8):02X}{int(g8):02X}{int(b8):02X}"


def _srgb_to_rgba(srgb):
    """Convert an sRGB uint8 (R, G, B) tuple to an opaque float (R, G, B, A) pixel."""
    return (srgb[0] / 255.0, srgb[1] / 255.0, srgb[2] / 255.0, 1.0)


def _named_material_srgb(name, fallback=(0, 0, 0)):
    return material_to_srgb(bpy.data.materials.get(name), fallback)


def _height_palette_keys(mesh):
    """Return the palette slots this feature created, as a plain int list.

    Stored as a list because "Color by Height" stacks: every colour the user
    layers on gets its own extruder slot, and all of them have to survive the
    next bake.
    """
    stored = mesh.get(_HEIGHT_BAKE_PALETTE_KEY_PROP)
    if isinstance(stored, int):
        return [stored]
    if stored is None:
        return []
    return [int(key) for key in stored]


def _present_hex_colors(arr):
    """Collect every distinct #RRGGBB colour actually present in a pixel array."""
    u8 = np.clip(np.rint(arr[..., :3] * 255.0), 0, 255).astype(np.uint8)
    packed = (
        (u8[..., 0].astype(np.uint32) << 16)
        | (u8[..., 1].astype(np.uint32) << 8)
        | u8[..., 2].astype(np.uint32)
    )
    return {f"#{int(value):06X}" for value in np.unique(packed)}


def _sync_height_bake_palette(mesh, palette, arr, mountain_hex=None):
    """Keep one owned palette slot per height colour still visible in the image.

    Adding a slot is only done for colours this feature introduced, and owned
    slots are dropped again once a later bake has painted over them, so
    stacking green -> grey -> white keeps three slots while simply recolouring
    a single layer keeps one.
    """
    owned = [key for key in _height_palette_keys(mesh) if key in palette]

    # Prune first so a colour that was just painted over frees its slot for
    # the new one; what survives is exactly the set of stacked layers.
    present = _present_hex_colors(arr)
    for key in list(owned):
        if palette.get(key) not in present:
            palette.pop(key, None)
            owned.remove(key)

    if mountain_hex is not None and mountain_hex not in palette.values():
        next_idx = (max(palette.keys()) + 1) if palette else 1
        palette[next_idx] = mountain_hex
        owned.append(next_idx)

    owned = sorted(set(owned))
    if owned:
        mesh[_HEIGHT_BAKE_PALETTE_KEY_PROP] = owned
    elif _HEIGHT_BAKE_PALETTE_KEY_PROP in mesh:
        del mesh[_HEIGHT_BAKE_PALETTE_KEY_PROP]
    return palette


def _build_palette(present_kinds):
    """Return (palette_dict, kind_to_index) for the kinds actually present.

    palette_dict: {0: "#RRGGBB", ...} where index 0 is the terrain background.
    kind_to_index: {KIND_STR_UPPER: int_palette_index}

    Kinds that share an identical colour (e.g. OCEAN/WATER) reuse the same
    palette key regardless of which one is encountered first in
    _RASTER_ORDER -- a kind-name special case here previously only worked
    when WATER was processed before OCEAN, silently wasting a filament slot
    (and shifting every later index by one) whenever OCEAN came first.
    """
    palette = {0: _srgb_to_hex(*_named_material_srgb("BASE"))}
    kind_to_index = {}
    idx = 1
    for kind in _RASTER_ORDER:
        if kind not in present_kinds:
            continue
        hexcol = _srgb_to_hex(*_named_material_srgb(_KIND_MATERIAL_NAME[kind]))
        existing_idx = next((k for k, v in palette.items() if v == hexcol), None)
        if existing_idx is not None:
            kind_to_index[kind] = existing_idx
            continue
        palette[idx] = hexcol
        kind_to_index[kind] = idx
        idx += 1
    return palette, kind_to_index


def _compute_local_bbox(terrain_obj):
    """Return (min_x, min_y, width, height) in the object's local space."""
    verts = terrain_obj.data.vertices
    co = np.empty(len(verts) * 3, dtype=np.float32)
    verts.foreach_get("co", co)
    co = co.reshape(-1, 3)
    min_x = float(co[:, 0].min())
    max_x = float(co[:, 0].max())
    min_y = float(co[:, 1].min())
    max_y = float(co[:, 1].max())
    return min_x, min_y, max_x - min_x, max_y - min_y


def _new_paint_image(name, width, height, pixels):
    """Create (or replace) a packed sRGB Blender Image from a flat RGBA float buffer."""
    if name in bpy.data.images:
        bpy.data.images.remove(bpy.data.images[name])
    image = bpy.data.images.new(name, width=width, height=height, alpha=True)
    image.colorspace_settings.name = 'sRGB'
    image.pixels.foreach_set(pixels) # type: ignore - Blender api accepts the numpy array
    image.pack()
    return image


def _assign_texture_material(mesh, name, image):
    """Create (or replace) an Image Texture -> Principled BSDF material named
    *name* and make it the mesh's only material."""
    if name in bpy.data.materials:
        bpy.data.materials.remove(bpy.data.materials[name])
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    tex_node = nodes.new(type="ShaderNodeTexImage")
    tex_node.image = image
    tex_node.interpolation = "Closest"
    tex_node.location = (-300, 0)

    bsdf = nodes.new(type="ShaderNodeBsdfPrincipled")
    bsdf.location = (0, 0)

    out_node = nodes.new(type="ShaderNodeOutputMaterial")
    out_node.location = (300, 0)

    links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
    links.new(bsdf.outputs["BSDF"], out_node.inputs["Surface"])

    mesh.materials.clear()
    mesh.materials.append(mat)
    return mat


def _rasterize_polygon_even_odd(rings_px, arr, color_float, resolution):
    """Paint a polygon using the even-odd fill rule across all rings combined.

    rings_px : list of (xs, ys) float32 arrays — exterior first, then interiors.
    Pixels inside an odd number of ring boundaries are painted; hole pixels
    (inside an even number) are left untouched, preserving earlier layers.
    """
    all_y = np.concatenate([py for _, py in rings_px])
    row_min = max(0, int(np.floor(all_y.min())))
    row_max = min(resolution - 1, int(np.ceil(all_y.max())))

    edges = []
    for xs, ys in rings_px:
        if len(xs) < 3:
            continue
        edges.append((xs[:-1], ys[:-1], xs[1:], ys[1:]))

    if not edges:
        return

    for row in range(row_min, row_max + 1):
        y = row + 0.5
        xi_parts = []
        for x0, y0, x1, y1 in edges:
            cross = ((y0 < y) & (y <= y1)) | ((y1 < y) & (y <= y0))
            if not cross.any():
                continue
            dy = y1[cross] - y0[cross]
            t = (y - y0[cross]) / dy
            xi_parts.append(x0[cross] + t * (x1[cross] - x0[cross]))
        if not xi_parts:
            continue
        xi_sorted = np.sort(np.concatenate(xi_parts))
        for k in range(0, len(xi_sorted) - 1, 2):
            col_s = max(0, int(np.ceil(xi_sorted[k])))
            col_e = min(resolution, int(np.floor(xi_sorted[k + 1])) + 1)
            if col_s < col_e:
                arr[row, col_s:col_e] = color_float


def _rasterize_geometry(
    geom,
    arr,
    color_float,
    bg_float,
    cursor_x,
    cursor_y,
    min_x,
    min_y,
    width,
    height,
    resolution,
):
    """Rasterize a Shapely Polygon or MultiPolygon into arr."""
    if geom is None or geom.is_empty:
        return

    try:
        from shapely.geometry import GeometryCollection, MultiPolygon
    except ImportError:
        print("[TP3D texture] Shapely not available — skipping rasterization")
        return

    if isinstance(geom, (MultiPolygon, GeometryCollection)):
        polys = list(geom.geoms)
    else:
        polys = [geom]

    def _to_px(ring):
        """Convert a Shapely ring's world-space coords to pixel floats."""
        coords = np.array(list(ring.coords), dtype=np.float64)
        px = (coords[:, 0] - cursor_x - min_x) / width * resolution
        py = (coords[:, 1] - cursor_y - min_y) / height * resolution
        return px.astype(np.float32), py.astype(np.float32)

    for poly in polys:
        if not hasattr(poly, "exterior") or poly.is_empty:
            continue
        # Combine exterior + all interior rings so the even-odd rule
        # naturally skips holes without overwriting earlier-painted layers.
        rings_px = [_to_px(poly.exterior)]
        for interior in poly.interiors:
            rings_px.append(_to_px(interior))
        _rasterize_polygon_even_odd(rings_px, arr, color_float, resolution)


# ── Public entry point ────────────────────────────────────────────────────────


def setup_paint_texture(gen: GenerationContext):
    """Rasterize OSM polygons into a texture and configure terrain_obj for 3MF paint export.

    Parameters
    ----------
    terrain_obj      : bpy.types.Object — the terrain mesh object
    polygons_by_kind : dict[str, shapely_geometry] — {KIND_UPPER: Shapely polygon}
                       Coordinates are in world space (Web Mercator, same system
                       used by convert_to_blender_coordinates).
    resolution       : int — image width and height in pixels (default 2048)

    Side effects
    ------------
    - Creates / replaces UV layer "MMU_Paint" on terrain_obj.data
    - Creates / replaces Blender Image "{mesh.name}_MMU_Paint"
    - Creates / replaces material "{mesh.name}_MMU_Paint" with a TEX_IMAGE node
    - Sets mesh custom properties: 3mf_is_paint_texture, 3mf_paint_default_extruder,
      3mf_paint_extruder_colors — triggering the 3MF addon's paint-segmentation
      export when use_orca_format="AUTO" or "PAINT".
    """
    resolution = gen.texture.texResolution
    terrain_obj = gen.runtime.mapObject
    mesh = terrain_obj.data
    cursor = bpy.context.scene.cursor.location
    cursor_x = float(cursor.x)
    cursor_y = float(cursor.y)

    min_x, min_y, width, height = _compute_local_bbox(terrain_obj)
    if width <= 0 or height <= 0:
        print("[TP3D texture] degenerate terrain bbox — skipping texture setup")
        return
    polygons_by_kind = gen.runtime.elements.get("_osm_polygons", {})
    present_kinds = {k.upper() for k, v in polygons_by_kind.items() if v is not None}
    palette, _kind_to_index = _build_palette(present_kinds)

    # ── WorldCover land-cover base fill ─────────────────────────────────────
    # elementSource == "WORLDCOVER" colors terrain from the ESA WorldCover
    # reference plane rather than individual OSM element toggles (see
    # satellite.py's paint_terrain_from_landcover(), the non-texture-mode
    # equivalent this mirrors). That function assigns per-face materials,
    # which texture mode ignores entirely -- without this, WORLDCOVER +
    # useTexture silently produced a plain BASE-colour terrain. Sampled once
    # here (vectorized over the whole pixel grid); OSM-derived kinds (roads,
    # buildings, trail) still rasterize on top per _RASTER_ORDER below.
    landcover_classes = None
    landcover_fill = {}
    if gen.settings.elementSource == "WORLDCOVER":
        from .satellite import landcover_effective_material, sample_landcover_classes

        landcover_classes = sample_landcover_classes(
            resolution,
            cursor_x,
            cursor_y,
            min_x,
            min_y,
            width,
            height,
            gen.runtime.tbMinLat,
            gen.runtime.tbMaxLat,
            gen.runtime.tbMinLon,
            gen.runtime.tbMaxLon,
        )
        if landcover_classes is not None:
            tp3d = bpy.context.scene.tp3d
            for _class_id in (int(c) for c in np.unique(landcover_classes) if c >= 0):
                _mat_name = landcover_effective_material(_class_id, tp3d)
                if _mat_name is None:
                    continue
                _lc_srgb = _named_material_srgb(_mat_name)
                landcover_fill[_class_id] = (
                    _srgb_to_hex(*_lc_srgb),
                    (
                        _lc_srgb[0] / 255.0,
                        _lc_srgb[1] / 255.0,
                        _lc_srgb[2] / 255.0,
                        1.0,
                    ),
                )
    for _lc_hex, _ in landcover_fill.values():
        if _lc_hex not in palette.values():
            palette[max(palette.keys()) + 1] = _lc_hex

    # Always add WHITE, BLACK and TRAIL so companion text/plate/trail objects
    # have exact palette matches regardless of which OSM element kinds are present.
    for _cmat_name in ("WHITE", "BLACK", "TRAIL"):
        _chex = _srgb_to_hex(*_named_material_srgb(_cmat_name))
        if _chex not in palette.values():
            palette[max(palette.keys()) + 1] = _chex

    # ── UV layer ──────────────────────────────────────────────────────────────
    if _UV_LAYER_NAME in mesh.uv_layers:
        mesh.uv_layers.remove(mesh.uv_layers[_UV_LAYER_NAME])
    uv_layer = mesh.uv_layers.new(name=_UV_LAYER_NAME)
    mesh.uv_layers.active = uv_layer

    # Vectorised planar projection: local X → U, local Y → V
    co_flat = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co_flat)
    co = co_flat.reshape(-1, 3)

    v_idx = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", v_idx)

    uv_u = np.clip((co[v_idx, 0] - min_x) / width, 0.0, 1.0)
    uv_v = np.clip((co[v_idx, 1] - min_y) / height, 0.0, 1.0)

    # Pin side/bottom face loops to the base-colour anchor pixel so they
    # never accidentally pick up an element colour from their XY position.
    _ANCHOR_U = 2.0 / resolution
    _ANCHOR_V = 2.0 / resolution
    n_polys = len(mesh.polygons)
    loop_totals = np.empty(n_polys, dtype=np.int32)
    mesh.polygons.foreach_get("loop_total", loop_totals)
    loop_face = np.repeat(np.arange(n_polys, dtype=np.int32), loop_totals)
    face_normals_flat = np.empty(n_polys * 3, dtype=np.float32)
    mesh.polygons.foreach_get("normal", face_normals_flat)
    face_nz = face_normals_flat.reshape(-1, 3)[:, 2]
    # Structural side faces (nz≈0) and bottom (nz=-1) only — steep terrain
    # faces have nz>0 even at extreme angles, so 0.1 safely excludes them.
    non_top = face_nz[loop_face] < 0.1
    uv_u[non_top] = _ANCHOR_U
    uv_v[non_top] = _ANCHOR_V

    uv_flat = np.empty(len(mesh.loops) * 2, dtype=np.float32)
    uv_flat[0::2] = uv_u
    uv_flat[1::2] = uv_v
    uv_layer.data.foreach_set("uv", uv_flat)
    mesh.update()

    # ── Rasterize ─────────────────────────────────────────────────────────────
    base_f = _srgb_to_rgba(_named_material_srgb("BASE"))
    arr = np.full((resolution, resolution, 4), base_f, dtype=np.float32)

    if landcover_classes is not None:
        for _class_id, (_, _c_f) in landcover_fill.items():
            arr[landcover_classes == _class_id] = _c_f

    for kind in _RASTER_ORDER:
        geom = polygons_by_kind.get(kind) or polygons_by_kind.get(kind.lower())
        if geom is None:
            continue
        srgb = _named_material_srgb(_KIND_MATERIAL_NAME[kind])
        c_f = (srgb[0] / 255.0, srgb[1] / 255.0, srgb[2] / 255.0, 1.0)
        _rasterize_geometry(
            geom,
            arr,
            c_f,
            None,
            cursor_x,
            cursor_y,
            min_x,
            min_y,
            width,
            height,
            resolution,
        )

    # Re-paint anchor block after all element rasterization so no polygon
    # that happens to touch the corner can overwrite it with an element colour.
    arr[0:4, 0:4] = base_f

    # ── Blender Image ─────────────────────────────────────────────────────────
    img_name = f"{mesh.name}_MMU_Paint"
    if img_name in bpy.data.images:
        bpy.data.images.remove(bpy.data.images[img_name])
    # A fresh generation invalidates any height-bake history from a prior
    # run that happened to reuse this same mesh/image name -- otherwise the
    # undo backup could silently apply pixels from a completely different,
    # already-deleted image onto this new one.
    _HEIGHT_BAKE_UNDO.pop(img_name, None)
    image = bpy.data.images.new(
        img_name, width=resolution, height=resolution, alpha=True
    )
    image.colorspace_settings.name = "sRGB"
    image.pixels.foreach_set(arr.ravel())  # type: ignore - Blender api accepts the numpy array
    image.pack()

    # ── Material ──────────────────────────────────────────────────────────────
    _assign_texture_material(mesh, img_name, image)

    # ── 3MF paint metadata ────────────────────────────────────────────────────
    mesh["3mf_is_paint_texture"] = True
    mesh["3mf_paint_default_extruder"] = 1
    mesh["3mf_paint_extruder_colors"] = str(palette)
    write_mesh_color_roles(mesh, material_role_colors())

    print(
        f"[TP3D texture] {resolution}x{resolution}px | {len(palette)} filaments | "
        f"kinds: {sorted(present_kinds)}"
    )
    return palette


def _ensure_palette_color(mesh, srgb):
    """Declare *srgb* as an extruder colour in the mesh's 3MF paint palette
    if it isn't one already, so the exporter maps it to its own filament."""
    import ast

    try:
        palette = ast.literal_eval(mesh.get("3mf_paint_extruder_colors", "{}"))
    except (ValueError, SyntaxError):
        palette = {}
    hexcol = _srgb_to_hex(*srgb)
    if hexcol not in palette.values():
        palette[(max(palette.keys()) + 1) if palette else 1] = hexcol
        mesh["3mf_paint_extruder_colors"] = str(palette)


def bake_trail_into_texture(terrain_obj, trail_polygon, material=None):
    """Rasterize a single trail ribbon polygon onto terrain_obj's EXISTING
    MMU_Paint texture in place, without disturbing whatever other elements
    (water/forest/roads/etc) are already baked into it.

    material : the trail curve's own Blender material (e.g. TRAIL or YELLOW),
    so a trail baked into the texture keeps its actual colour instead of
    always turning red. Falls back to the TRAIL material if not given.

    setup_paint_texture() always rebuilds the image from scratch from a full
    polygons_by_kind dict, so it can't be reused here -- callers like
    generateJustTrail() add a trail to an already-generated CREATE_TEXTURE
    map, long after the original polygons_by_kind used to build it is gone.

    Returns False (no-op) if terrain_obj has no existing paint texture to
    composite onto -- the caller should fall back to keeping the trail as
    3D geometry in that case.
    """
    srgb = (
        material_to_srgb(material)
        if material is not None
        else _named_material_srgb("TRAIL")
    )
    return bake_polygon_into_texture(terrain_obj, trail_polygon, srgb)


def bake_polygon_into_texture(terrain_obj, polygon, srgb, undo=False):
    """Rasterize a world-space Shapely (Multi)Polygon onto terrain_obj's
    EXISTING MMU_Paint texture in colour *srgb* (uint8 R, G, B), in place.

    Used for anything painted onto an already-textured map after generation
    (trails, "Add SVG" / "Add Text" in Paint on Surface mode): once a mesh is
    flagged 3mf_is_paint_texture the 3MF exporter colours it from this
    texture only, so per-face materials would be lost on export.

    undo : snapshot the texture first into the same history Color by Height
    uses (see save_height_bake_state), so "Undo Texture Bake" steps back
    through every post-generation paint in order. One shared history per
    image keeps that correct -- separate ones would restore a snapshot that
    predates the other feature's paint and silently erase it.

    Returns False (no-op) if terrain_obj has no paint texture.
    """
    if polygon is None or polygon.is_empty:
        return False

    mesh = terrain_obj.data
    img_name = f"{mesh.name}_MMU_Paint"
    image = bpy.data.images.get(img_name)
    if image is None:
        return False

    resolution = image.size[0]
    min_x, min_y, width, height = _compute_local_bbox(terrain_obj)
    if width <= 0 or height <= 0:
        return False

    # _rasterize_geometry's world->local conversion assumes the "cursor"
    # value it's given is the object's own world origin -- true at the
    # original bake, where setup_paint_texture ran right after
    # createTerrainFromSelected pointed scene.cursor.location at this exact
    # tile (see its per-tile loop). Reading the *current* scene cursor here
    # instead would silently misalign the trail onto the wrong pixels the
    # moment it's moved (e.g. by a later tile) between then and now, so use
    # the tile's own world location directly rather than depending on
    # whatever the live cursor currently is.
    cursor_x, cursor_y = float(terrain_obj.location.x), float(terrain_obj.location.y)

    if undo:
        save_height_bake_state(image, mesh)

    arr = np.empty(resolution * resolution * 4, dtype=np.float32)
    image.pixels.foreach_get(arr)
    arr = arr.reshape((resolution, resolution, 4))

    _rasterize_geometry(
        polygon,
        arr,
        _srgb_to_rgba(srgb),
        None,
        cursor_x,
        cursor_y,
        min_x,
        min_y,
        width,
        height,
        resolution,
    )

    image.pixels.foreach_set(arr.ravel())
    image.pack()

    # Register the colour in the exported extruder palette if this map's
    # original bake predates it (e.g. no trail existed yet at bake time).
    _ensure_palette_color(mesh, srgb)

    return True


def paint_part_before_join(target_obj, part_obj, srgb):
    """Map every loop of part_obj onto one texel of target_obj's MMU_Paint
    texture that carries colour *srgb*, so the part keeps that colour in 3MF
    paint export once it's joined into target_obj.

    Once a mesh is flagged 3mf_is_paint_texture, the exporter colours every
    triangle from the texture via its UVs; per-face materials are only a
    fallback, and that fallback converts the material colour with a gamma
    curve that never matches this addon's plain x255 palette hex -- so a
    joined part otherwise ends up on the default filament.

    The exporter samples texel (round(u*(W-1)), round(v*(H-1))), so a UV of
    exactly (x/(W-1), y/(H-1)) hits texel (x, y) with no rounding risk. The
    texel is picked without disturbing the top surface where possible:
      1. any texel already that colour (e.g. a baked trail) -- image unchanged;
      2. else an image corner no top-face triangle can sample (always true
         for cropped puzzle pieces, whose crop has a border, and for
         non-rectangular shapes);
      3. else the top-right corner texel is overwritten -- a single texel,
         far below nozzle size.

    Returns False (no-op) when target_obj has no paint texture.
    """
    mesh = target_obj.data
    if not mesh.get("3mf_is_paint_texture"):
        return False

    image = None
    for slot in target_obj.material_slots:
        mat = slot.material
        if mat and mat.use_nodes:
            image = next((n.image for n in mat.node_tree.nodes
                          if n.type == "TEX_IMAGE" and n.image), None)
            if image:
                break
    if image is None:
        return False

    W, H = image.size
    if W < 2 or H < 2:
        return False

    arr = np.empty(W * H * 4, dtype=np.float32)
    image.pixels.foreach_get(arr)
    arr = arr.reshape((H, W, 4))
    target = np.array(srgb, dtype=np.int16)

    texel = None
    rgb = np.rint(arr[:, :, :3] * 255).astype(np.int16)
    match = np.argwhere(np.all(np.abs(rgb - target) <= 1, axis=2))
    if len(match):
        ty, tx = match[0]
        texel = (int(tx), int(ty))

    if texel is None:
        uv_layer = mesh.uv_layers.get(_UV_LAYER_NAME)
        tri_u = tri_v = None
        if uv_layer is not None:
            mesh.calc_loop_triangles()
            n_tris = len(mesh.loop_triangles)
            tri_loops = np.empty(n_tris * 3, dtype=np.int32)
            mesh.loop_triangles.foreach_get("loops", tri_loops)
            tri_poly = np.empty(n_tris, dtype=np.int32)
            mesh.loop_triangles.foreach_get("polygon_index", tri_poly)
            normals = np.empty(len(mesh.polygons) * 3, dtype=np.float32)
            mesh.polygons.foreach_get("normal", normals)
            top = normals.reshape(-1, 3)[tri_poly, 2] >= 0.1
            uv_flat = np.empty(len(mesh.loops) * 2, dtype=np.float32)
            uv_layer.data.foreach_get("uv", uv_flat)
            uvs = uv_flat.reshape(-1, 2)[tri_loops.reshape(-1, 3)[top]]
            tri_u = uvs[:, :, 0] * (W - 1)
            tri_v = uvs[:, :, 1] * (H - 1)

        # (0, 0) is left alone -- it's the BASE anchor sides/bottoms use.
        for cx, cy in ((W - 1, H - 1), (W - 1, 0), (0, H - 1)):
            if tri_u is None:
                texel = (cx, cy)
                break
            # Conservative: any top triangle whose pixel bbox reaches this
            # texel's rounding cell counts as covering it.
            covered = np.any(
                (tri_u.min(axis=1) <= cx + 0.5) & (tri_u.max(axis=1) >= cx - 0.5)
                & (tri_v.min(axis=1) <= cy + 0.5) & (tri_v.max(axis=1) >= cy - 0.5)
            )
            if not covered:
                texel = (cx, cy)
                break
        if texel is None:
            texel = (W - 1, H - 1)

        tx, ty = texel
        arr[ty, tx] = _srgb_to_rgba(srgb)
        image.pixels.foreach_set(arr.ravel())
        image.pack()

    # Make sure the colour is a declared filament, same as bake_polygon_into_texture.
    _ensure_palette_color(mesh, srgb)

    part_mesh = part_obj.data
    while part_mesh.uv_layers:
        part_mesh.uv_layers.remove(part_mesh.uv_layers[0])
    part_uv = part_mesh.uv_layers.new(name=_UV_LAYER_NAME)
    tx, ty = texel
    uv_flat = np.empty(len(part_mesh.loops) * 2, dtype=np.float32)
    uv_flat[0::2] = tx / (W - 1)
    uv_flat[1::2] = ty / (H - 1)
    part_uv.data.foreach_set("uv", uv_flat)
    return True


def _value_noise_field(xs, ys, scale, seed=0):
    """Cheap fully-vectorised smooth noise: bilinear-interpolated hash
    lattice, numpy-only (no scipy, no mathutils.noise -- that isn't
    vectorised, so calling it per-pixel across a 2048x2048 grid is a
    non-starter). xs/ys are arrays of local-space coordinates of any shape;
    scale is the lattice frequency (smaller = broader, smoother features).
    Returns values in roughly [-1, 1], same shape as xs/ys.
    """
    gx = xs.astype(np.float64) * scale
    gy = ys.astype(np.float64) * scale
    x0 = np.floor(gx).astype(np.int64)
    y0 = np.floor(gy).astype(np.int64)
    tx = gx - x0
    ty = gy - y0

    def _hash(ix, iy):
        h = (ix * 374761393 + iy * 668265263 + seed * 2147483647) & 0xFFFFFFFF
        h = (h ^ (h >> 13)) * 1274126177 & 0xFFFFFFFF
        h = h ^ (h >> 16)
        return (h & 0xFFFF).astype(np.float64) / 65535.0 * 2.0 - 1.0

    def _smooth(t):
        return t * t * (3.0 - 2.0 * t)

    sx = _smooth(tx)
    sy = _smooth(ty)

    n00 = _hash(x0, y0)
    n10 = _hash(x0 + 1, y0)
    n01 = _hash(x0, y0 + 1)
    n11 = _hash(x0 + 1, y0 + 1)

    nx0 = n00 * (1.0 - sx) + n10 * sx
    nx1 = n01 * (1.0 - sx) + n11 * sx
    return nx0 * (1.0 - sy) + nx1 * sy


def _rasterize_height_field(mesh, min_x, min_y, width, height, resolution):
    """Rasterize the mesh's own top-facing triangles into a per-pixel
    elevation grid via barycentric interpolation of vertex Z.

    Deliberately reads whatever the mesh looks like *right now* rather than
    any generation-time elevation data (gen.runtime.tileVerts) -- this
    operator runs standalone, well after generation, with no GenerationContext
    available, and build_mesh_from_polygon's lattice-clipped verts are never
    a clean row-major grid for any shape anyway, so there's no cheap reshape
    to fall back on. Working from the live mesh also means a single-color-mode
    trail's boolean cutout is handled for free: pixels with no covering
    top-facing triangle are simply left as NaN and never painted.

    A full pixels-x-triangles broadcast isn't an option (a 2048x2048 canvas
    against tens of thousands of terrain triangles doesn't fit in memory),
    and looping per-triangle at full resolution is slow. So the loop runs over
    whichever of (top-facing triangle count, resolution) is smaller, fully
    vectorised over the other axis: few triangles -> loop triangles, as
    before, each one vectorised over its own pixel footprint; many
    triangles (the usual case) -> loop image rows instead, and for each row
    vectorise the barycentric test across every triangle whose Y-span
    covers that row in a single broadcast, so the loop trip count is
    bounded by min(n_top, resolution) rather than always paying for n_top.

    Returns a (resolution, resolution) float32 array of local-space Z,
    NaN where no top-facing triangle covers that pixel.
    """
    mesh.calc_loop_triangles()
    tris = mesh.loop_triangles
    n_tris = len(tris)
    elev = np.full((resolution, resolution), np.nan, dtype=np.float32)
    if n_tris == 0:
        return elev

    co = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)

    tri_verts = np.empty(n_tris * 3, dtype=np.int64)
    tris.foreach_get("vertices", tri_verts)
    tri_verts = tri_verts.reshape(-1, 3)

    tri_normals = np.empty(n_tris * 3, dtype=np.float32)
    tris.foreach_get("normal", tri_normals)
    tri_normals = tri_normals.reshape(-1, 3)

    # Same nz > 0.1 convention already used elsewhere in this file to pick
    # top-facing geometry out from structural side/bottom faces.
    top_mask = tri_normals[:, 2] > 0.1
    tri_verts = tri_verts[top_mask]
    n_top = len(tri_verts)
    if n_top == 0:
        return elev

    p = co[tri_verts]  # (n_top, 3, 3) -- 3 verts x (x, y, z) per triangle
    px = (p[:, :, 0] - min_x) / width * resolution
    py = (p[:, :, 1] - min_y) / height * resolution
    pz = p[:, :, 2]

    denom = (py[:, 1] - py[:, 2]) * (px[:, 0] - px[:, 2]) + (px[:, 2] - px[:, 1]) * (
        py[:, 0] - py[:, 2]
    )
    valid = np.abs(denom) > 1e-9

    # Few triangles
    if n_top <= resolution:
        for ti in range(n_top):
            if not valid[ti]:
                continue
            tx, ty, tz, d = px[ti], py[ti], pz[ti], denom[ti]

            col_min = max(0, int(np.floor(tx.min())))
            col_max = min(resolution, int(np.ceil(tx.max())) + 1)
            row_min = max(0, int(np.floor(ty.min())))
            row_max = min(resolution, int(np.ceil(ty.max())) + 1)
            if col_min >= col_max or row_min >= row_max:
                continue

            xs, ys = np.meshgrid(
                np.arange(col_min, col_max) + 0.5,
                np.arange(row_min, row_max) + 0.5,
            )
            w0 = ((ty[1] - ty[2]) * (xs - tx[2]) + (tx[2] - tx[1]) * (ys - ty[2])) / d
            w1 = ((ty[2] - ty[0]) * (xs - tx[2]) + (tx[0] - tx[2]) * (ys - ty[2])) / d
            w2 = 1.0 - w0 - w1

            inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
            if not inside.any():
                continue

            z = w0 * tz[0] + w1 * tz[1] + w2 * tz[2]
            sub_rows, sub_cols = np.nonzero(inside)
            elev[row_min + sub_rows, col_min + sub_cols] = z[sub_rows, sub_cols]

        return elev

    # Many triangles
    ex0 = np.stack([px[:, 0], px[:, 1], px[:, 2]], axis=1)
    ex1 = np.stack([px[:, 1], px[:, 2], px[:, 0]], axis=1)
    ey0 = np.stack([py[:, 0], py[:, 1], py[:, 2]], axis=1)
    ey1 = np.stack([py[:, 1], py[:, 2], py[:, 0]], axis=1)
    ez0 = np.stack([pz[:, 0], pz[:, 1], pz[:, 2]], axis=1)
    ez1 = np.stack([pz[:, 1], pz[:, 2], pz[:, 0]], axis=1)

    tri_ymin = py.min(axis=1)
    tri_ymax = py.max(axis=1)

    for row in range(resolution):
        y = row + 0.5
        cand = np.nonzero((tri_ymin <= y) & (tri_ymax >= y))[0]
        if cand.size == 0:
            continue

        cy0, cy1 = ey0[cand], ey1[cand]
        cx0, cx1 = ex0[cand], ex1[cand]
        cz0, cz1 = ez0[cand], ez1[cand]

        cross = ((cy0 < y) & (y <= cy1)) | ((cy1 < y) & (y <= cy0))
        dy = cy1 - cy0
        safe_dy = np.where(
            dy == 0, 1.0, dy
        )  # dodge /0 on non-crossing edges; masked out next
        t = (y - cy0) / safe_dy
        x_cross = np.where(cross, cx0 + t * (cx1 - cx0), np.nan)
        z_cross = np.where(cross, cz0 + t * (cz1 - cz0), np.nan)

        order = np.argsort(x_cross, axis=1)
        x_sorted = np.take_along_axis(x_cross, order, axis=1)
        z_sorted = np.take_along_axis(z_cross, order, axis=1)
        xL, xR = x_sorted[:, 0], x_sorted[:, 1]
        zL, zR = z_sorted[:, 0], z_sorted[:, 1]

        ok = np.isfinite(xL) & np.isfinite(xR) & (xR > xL)
        if not ok.any():
            continue
        xL, xR, zL, zR = xL[ok], xR[ok], zL[ok], zR[ok]

        col_start = np.clip(np.ceil(xL).astype(np.int64), 0, resolution)
        col_end = np.clip(np.floor(xR).astype(np.int64) + 1, 0, resolution)
        span = col_end - col_start
        keep = span > 0
        if not keep.any():
            continue
        col_start, span = col_start[keep], span[keep]
        xL, xR, zL, zR = xL[keep], xR[keep], zL[keep], zR[keep]

        # Flatten all of this row's (triangle, column) pairs into one pass
        # with a repeat/cumsum trick
        total = int(span.sum())
        seg_idx = np.repeat(np.arange(span.size), span)
        offsets = np.arange(total) - np.repeat(np.cumsum(span) - span, span)
        cols = col_start[seg_idx] + offsets
        span_width = np.maximum(xR[seg_idx] - xL[seg_idx], 1e-9)
        frac = (cols + 0.5 - xL[seg_idx]) / span_width
        z_vals = zL[seg_idx] + frac * (zR[seg_idx] - zL[seg_idx])

        elev[row, cols] = z_vals

    return elev


def bake_height_layer_into_texture(
    terrain_obj, z_threshold, material=None, noise_amplitude=2.0, noise_scale=0.04
):
    """Paint a noised colour-by-height layer onto terrain_obj's EXISTING
    MMU_Paint texture in place, on top of whatever element/land-cover
    rasterization is already baked into it -- mirrors bake_trail_into_texture's
    read/modify/write-back pattern, since this is also called long after
    setup_paint_texture's original polygons_by_kind is gone.

    z_threshold is in the same local-space Z units already used by the
    vertex/material-index "Color by Height" path (see operators.py) --
    noise_amplitude/noise_scale perturb that flat threshold per-pixel via
    _value_noise_field so the boundary isn't a razor-flat line.

    material : the MOUNTAIN material by default. The shared material reader
    preserves legacy colors and decodes materials written with the current
    scene-linear convention.

    Returns False (no-op) if terrain_obj has no existing paint texture.
    """
    import ast

    mesh = terrain_obj.data
    img_name = f"{mesh.name}_MMU_Paint"
    image = bpy.data.images.get(img_name)
    if image is None:
        return False

    resolution = image.size[0]
    min_x, min_y, width, height = _compute_local_bbox(terrain_obj)
    if width <= 0 or height <= 0:
        return False

    elev = _rasterize_height_field(mesh, min_x, min_y, width, height, resolution)

    ys, xs = np.mgrid[0:resolution, 0:resolution]
    wx = min_x + (xs + 0.5) / resolution * width
    wy = min_y + (ys + 0.5) / resolution * height
    noise_field = _value_noise_field(wx, wy, noise_scale)
    local_thresh = z_threshold + noise_field * noise_amplitude

    mountain_mask = ~np.isnan(elev) & (elev > local_thresh)

    current = np.empty(resolution * resolution * 4, dtype=np.float32)
    image.pixels.foreach_get(current)
    current = current.reshape((resolution, resolution, 4))

    # Snapshot pre-edit state for undo functionality
    save_height_bake_state(image, mesh)

    # Paint on top of whatever is already baked rather than resetting to a
    # pristine baseline -- that is what lets successive bakes stack, e.g.
    # green from 40%, grey from 60%, white from 80%.
    arr = current.copy()
    srgb = (
        material_to_srgb(material)
        if material is not None
        else _named_material_srgb("MOUNTAIN")
    )
    if mountain_mask.any():
        color_f = (srgb[0] / 255.0, srgb[1] / 255.0, srgb[2] / 255.0, 1.0)
        arr[mountain_mask] = color_f
        # Re-paint the base-colour anchor block (same as setup_paint_texture):
        # every side/bottom face's UVs are pinned to it, so a mountain pixel
        # landing on the map corner must not turn those faces mountain-coloured.
        arr[0:4, 0:4] = _srgb_to_rgba(_named_material_srgb("BASE"))

    image.pixels.foreach_set(arr.ravel())
    image.pack()

    try:
        palette = ast.literal_eval(mesh.get("3mf_paint_extruder_colors", "{}"))
    except (ValueError, SyntaxError):
        print(
            f"[TP3D] Invalid 3mf_paint_extruder_colors on {terrain_obj.name}; "
            "rebuilding the height-bake palette entry."
        )
        palette = {}
    if not isinstance(palette, dict):
        print(
            f"[TP3D] Invalid 3mf_paint_extruder_colors type on {terrain_obj.name}; "
            "rebuilding the height-bake palette entry."
        )
        palette = {}
    mountain_hex = _srgb_to_hex(*srgb) if mountain_mask.any() else None
    _sync_height_bake_palette(mesh, palette, arr, mountain_hex)
    mesh["3mf_paint_extruder_colors"] = str(palette)

    return True


def _pack_rgba_u32(u8_rgba):
    """Pack an (..., 4) uint8 RGBA array into one uint32 key per pixel."""
    u32 = u8_rgba.astype(np.uint32)
    return (u32[..., 0] << 24) | (u32[..., 1] << 16) | (u32[..., 2] << 8) | u32[..., 3]


def _unpack_rgba_u32(keys_u32):
    """Inverse of _pack_rgba_u32 -- returns (K, 4) uint8."""
    r = (keys_u32 >> 24) & 0xFF
    g = (keys_u32 >> 16) & 0xFF
    b = (keys_u32 >> 8) & 0xFF
    a = keys_u32 & 0xFF
    return np.stack([r, g, b, a], axis=-1).astype(np.uint8)


def _encode_indexed(arr):
    """Compress an (H, W, 4) float32 [0,1] pixel array into a small colour
    palette + zlib-compressed uint8 index array.

    MMU_Paint textures only ever contain at most 11 (as of writing) colors.
    Materials are never blended, making a raw float32 copy (H*W*4*4 bytes) 
    extremely wasteful.

    np.unique on the raw (H*W, 4) float rows is a genuinely slow row-wise
    sort (measured ~6s at 2048x2048) -- quantising to uint8 (exact for
    these colours) and packing each pixel's RGBA into a single uint32 key
    first turns that into a plain scalar sort instead, ~15-20x faster.

    Returns (palette_u8 (K,4) uint8, compressed_index bytes, (H, W) shape),
    or None if more than 255 distinct colours were found (would overflow a
    uint8 index -- not expected given this addon's palette sizes, but
    falling back to a raw copy beats silently losing colours).
    """
    h, w = arr.shape[:2]
    u8 = np.clip(np.round(arr * 255.0), 0, 255).astype(np.uint8)
    keys = _pack_rgba_u32(u8).ravel()

    uniq_keys, inv = np.unique(keys, return_inverse=True)
    if len(uniq_keys) > 255:
        return None

    palette_u8 = _unpack_rgba_u32(uniq_keys)
    index_u8 = inv.astype(np.uint8)
    compressed = zlib.compress(index_u8.tobytes(), level=6)
    return palette_u8, compressed, (h, w)


def _decode_indexed(palette_u8, compressed_index, shape):
    h, w = shape
    index_u8 = np.frombuffer(zlib.decompress(compressed_index), dtype=np.uint8)
    palette_f = palette_u8.astype(np.float32) / 255.0
    return palette_f[index_u8].reshape(h, w, 4)


def save_height_bake_state(image, mesh):
    """Save the current MMU_Paint state before applying a height bake.

    Stored as a palette-indexed, zlib-compressed representation rather than
    a raw pixel copy -- see _encode_indexed -- which cuts each snapshot to
    roughly 1/16th its raw size before compression even helps, and often
    several hundred times smaller after (large flat regions, which is most
    of a typical map, compress extremely well). That means the same
    _MAX_HEIGHT_BAKE_UNDO_MEMORY budget holds far more undo steps than raw
    copies ever could. Falls back to a raw copy only if _encode_indexed
    bails (more than 255 distinct colours -- not expected in practice).

    Keeps a bounded history based on total memory so repeated mountain
    bakes can be undone individually without allowing the image snapshots
    to consume unbounded memory.
    """
    history = _HEIGHT_BAKE_UNDO[image.name]

    pixels = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(pixels)
    resolution = image.size[0]
    arr = pixels.reshape((resolution, image.size[1], 4))

    encoded = _encode_indexed(arr)
    if encoded is not None:
        palette_u8, compressed_index, shape = encoded
        state = {
            "kind": "indexed",
            "palette": palette_u8,
            "index": compressed_index,
            "shape": shape,
            "meta_palette": deepcopy(mesh.get("3mf_paint_extruder_colors")),
            "height_palette_key": _height_palette_keys(mesh),
        }
    else:
        state = {
            "kind": "raw",
            "pixels": pixels,
            "meta_palette": deepcopy(mesh.get("3mf_paint_extruder_colors")),
            "height_palette_key": _height_palette_keys(mesh),
        }

    history.append(state)

    def _state_bytes(s):
        if s["kind"] == "indexed":
            return s["palette"].nbytes + len(s["index"])
        return s["pixels"].nbytes

    raw_bytes = pixels.nbytes
    if state["kind"] == "indexed":
        h, w = state["shape"]
        pre_compression_bytes = state["palette"].nbytes + (h * w)
        post_compression_bytes = _state_bytes(state)
        print(
            f"[TP3D] Saved height-bake undo snapshot: kind=indexed, "
            f"raw={raw_bytes} bytes ({raw_bytes / (1024 * 1024):.2f} MiB), "
            f"pre-compression={pre_compression_bytes} bytes ({pre_compression_bytes / (1024 * 1024):.2f} MiB), "
            f"post-compression={post_compression_bytes} bytes ({post_compression_bytes / (1024 * 1024):.2f} MiB)"
        )
    else:
        print(
            f"[TP3D] Saved height-bake undo snapshot: kind=raw, "
            f"size={raw_bytes} bytes ({raw_bytes / (1024 * 1024):.2f} MiB)"
        )

    total_memory = sum(_state_bytes(s) for s in history)
    while total_memory > _MAX_HEIGHT_BAKE_UNDO_MEMORY and len(history) > 1:
        removed = history.pop(0)
        total_memory -= _state_bytes(removed)


def restore_last_height_bake(terrain_obj):
    """Restore the MMU_Paint texture to its state immediately before the most recent height bake.
    Returns False if there is no previous state or the texture no longer exists.
    """
    mesh = terrain_obj.data
    img_name = f"{mesh.name}_MMU_Paint"
    image = bpy.data.images.get(img_name)
    if image is None:
        return False
    history = _HEIGHT_BAKE_UNDO.get(image.name)
    if not history:
        return False
    backup = history[-1]

    if backup["kind"] == "indexed":
        restored = _decode_indexed(backup["palette"], backup["index"], backup["shape"])
        image.pixels.foreach_set(restored.ravel())
    else:
        image.pixels.foreach_set(backup["pixels"].ravel())
    image.pack()

    if backup["meta_palette"] is not None:
        mesh["3mf_paint_extruder_colors"] = backup["meta_palette"]
    elif "3mf_paint_extruder_colors" in mesh:
        del mesh["3mf_paint_extruder_colors"]
    if backup["height_palette_key"]:
        mesh[_HEIGHT_BAKE_PALETTE_KEY_PROP] = backup["height_palette_key"]
    elif _HEIGHT_BAKE_PALETTE_KEY_PROP in mesh:
        del mesh[_HEIGHT_BAKE_PALETTE_KEY_PROP]
    history.pop()
    if not history:
        del _HEIGHT_BAKE_UNDO[image.name]
    return True


def has_height_bake_undo(terrain_obj):
    """Return whether the object has a saved Color by Height texture state."""
    # check if an object is even selected
    if terrain_obj is None or not hasattr(terrain_obj, "type") or terrain_obj.type != "MESH":
        return False
    mesh = terrain_obj.data
    img_name = f"{mesh.name}_MMU_Paint"
    history = _HEIGHT_BAKE_UNDO.get(img_name)
    return isinstance(history, list) and bool(history)


PAINT_MESH_METADATA_KEYS = (
    "3mf_is_paint_texture",
    "3mf_paint_default_extruder",
    "3mf_paint_extruder_colors",
    "tp3d_color_roles",
)


def copy_paint_metadata(source_mesh, target_mesh):
    """Copy mesh-level 3MF paint metadata that Blender boolean results drop."""
    if source_mesh is None or target_mesh is None:
        return
    for key in PAINT_MESH_METADATA_KEYS:
        if key in source_mesh:
            target_mesh[key] = source_mesh[key]


def tag_solid_color_for_paint_export(obj, srgb, palette):
    """Give a companion mesh a 1×1 solid-colour paint texture.

    Without this the Orca exporter sees no paint data on the object and the
    slicer defaults it to extruder 1 regardless of material colour.
    srgb must be a colour already present in palette for an exact extruder match --
    the exporter's segmentation encoder always renders a palette key K as
    filament (K + 1) regardless of what's declared here (see
    Blender3mfFormat's segmentation.py _build_state_map: ext_num = ext_idx + 1),
    so the stored default_extruder must be palette_key + 1, not the raw key,
    or every colour renders as whatever occupies the *next* palette slot.
    """
    if obj is None or not hasattr(obj, "type") or obj.type != "MESH":
        return
    mesh = obj.data

    target_hex = _srgb_to_hex(*srgb)
    palette_key = next(
        (idx for idx, hexcol in palette.items() if hexcol == target_hex), 0
    )
    default_extruder = palette_key + 1

    img_name = str(mesh.name) + "_MMU_Solid"
    if img_name in bpy.data.images:
        bpy.data.images.remove(bpy.data.images[img_name])
    image = bpy.data.images.new(img_name, width=1, height=1, alpha=True)
    image.colorspace_settings.name = "sRGB"
    image.pixels.foreach_set([srgb[0] / 255.0, srgb[1] / 255.0, srgb[2] / 255.0, 1.0])
    image.pack()

    if _UV_LAYER_NAME in mesh.uv_layers:
        mesh.uv_layers.remove(mesh.uv_layers[_UV_LAYER_NAME])
    uv_layer = mesh.uv_layers.new(name=_UV_LAYER_NAME)
    mesh.uv_layers.active = uv_layer
    uv_flat = np.full(len(mesh.loops) * 2, 0.5, dtype=np.float32)
    uv_layer.data.foreach_set("uv", uv_flat)

    _assign_texture_material(mesh, img_name, image)

    mesh["3mf_is_paint_texture"] = True
    mesh["3mf_paint_default_extruder"] = default_extruder
    mesh["3mf_paint_extruder_colors"] = str(palette)


def crop_paint_texture_to_piece(piece_obj, source_image):
    """Crop the shared terrain paint image down to this piece's UV footprint.

    For a 6×6 puzzle this turns 36 full 2K textures into 36 ~340px crops,
    cutting per-piece segmentation work by ~36×.
    """
    mesh = piece_obj.data
    uv_layer = mesh.uv_layers.get(_UV_LAYER_NAME)
    if uv_layer is None:
        return

    W, H = source_image.size

    # Identify top-face loops (face normal z ≥ 0.5).
    n_polys = len(mesh.polygons)
    loop_totals = np.empty(n_polys, dtype=np.int32)
    mesh.polygons.foreach_get("loop_total", loop_totals)
    normals_flat = np.empty(n_polys * 3, dtype=np.float32)
    mesh.polygons.foreach_get("normal", normals_flat)
    face_nz = normals_flat.reshape(-1, 3)[:, 2]
    loop_face_idx = np.repeat(np.arange(n_polys, dtype=np.int32), loop_totals)
    top_loop_mask = face_nz[loop_face_idx] >= 0.1

    uv_flat = np.empty(len(mesh.loops) * 2, dtype=np.float32)
    uv_layer.data.foreach_get("uv", uv_flat)
    uv_u = uv_flat[0::2]
    uv_v = uv_flat[1::2]

    if not top_loop_mask.any():
        return

    u_min = float(uv_u[top_loop_mask].min())
    u_max = float(uv_u[top_loop_mask].max())
    v_min = float(uv_v[top_loop_mask].min())
    v_max = float(uv_v[top_loop_mask].max())

    # 2-pixel border so edge triangles don't land on exact pixel boundaries.
    u_min = max(0.0, u_min - 2.0 / W)
    u_max = min(1.0, u_max + 2.0 / W)
    v_min = max(0.0, v_min - 2.0 / H)
    v_max = min(1.0, v_max + 2.0 / H)

    px_x0 = int(u_min * W)
    px_x1 = min(W, int(u_max * W) + 1)
    py_y0 = int(v_min * H)
    py_y1 = min(H, int(v_max * H) + 1)
    crop_w = max(4, px_x1 - px_x0)
    crop_h = max(4, py_y1 - py_y0)

    src_px = np.empty(W * H * 4, dtype=np.float32)
    source_image.pixels.foreach_get(src_px)
    crop_arr = np.ascontiguousarray(
        src_px.reshape(H, W, 4)[py_y0 : py_y0 + crop_h, px_x0 : px_x0 + crop_w]
    )

    # Re-paint the base-colour anchor block at (0,0)–(4,4) in the crop.
    crop_arr[0:4, 0:4] = _srgb_to_rgba(_named_material_srgb("BASE"))

    img_name = str(mesh.name) + "_MMU_Paint"
    if img_name in bpy.data.images:
        bpy.data.images.remove(bpy.data.images[img_name])
    new_img = bpy.data.images.new(img_name, width=crop_w, height=crop_h, alpha=True)
    new_img.colorspace_settings.name = "sRGB"
    new_img.pixels.foreach_set(crop_arr.ravel())  # type: ignore - Blender api accepts the numpy array
    new_img.pack()

    # Remap top-face UVs into the new [0, 1] crop space.
    u_range = (u_max - u_min) or 1.0
    v_range = (v_max - v_min) or 1.0
    new_u = uv_u.copy()
    new_v = uv_v.copy()
    new_u[top_loop_mask] = (uv_u[top_loop_mask] - u_min) / u_range
    new_v[top_loop_mask] = (uv_v[top_loop_mask] - v_min) / v_range

    # Pin non-top (side/bottom) loops to the anchor pixel in the cropped space.
    new_u[~top_loop_mask] = 2.0 / crop_w
    new_v[~top_loop_mask] = 2.0 / crop_h

    new_uv_flat = np.empty(len(mesh.loops) * 2, dtype=np.float32)
    new_uv_flat[0::2] = new_u
    new_uv_flat[1::2] = new_v
    uv_layer.data.foreach_set("uv", new_uv_flat)
    mesh.update()

    mat_name = img_name
    if mat_name in bpy.data.materials:
        bpy.data.materials.remove(bpy.data.materials[mat_name])
    mat = bpy.data.materials.new(name=mat_name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    tex_node = nodes.new(type="ShaderNodeTexImage")
    tex_node.image = new_img
    tex_node.location = (-300, 0)
    bsdf = nodes.new(type="ShaderNodeBsdfPrincipled")
    bsdf.location = (0, 0)
    out_node = nodes.new(type="ShaderNodeOutputMaterial")
    out_node.location = (300, 0)
    links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
    links.new(bsdf.outputs["BSDF"], out_node.inputs["Surface"])
    mesh.materials.clear()
    mesh.materials.append(mat)


# ── Recolor existing paint textures ──────────────────────────────────────────

_RECOLOR_TOLERANCE = 3


def _hex_to_u8(value):
    value = value.lstrip("#")
    return np.array([int(value[i : i + 2], 16) for i in (0, 2, 4)], dtype=np.int16)


def color_role_mapping(old_roles, new_roles):
    """Return ({old_hex: new_hex}, colliding_old_hexes).

    Roles that previously shared one colour can't be told apart in the image;
    the first role in ROLE_NAMES order wins and the colour is reported.
    """
    mapping = {}
    collisions = set()
    for role in ROLE_NAMES:
        old_hex = _srgb_to_hex(*old_roles[role])
        new_hex = _srgb_to_hex(*new_roles[role])
        if old_hex in mapping:
            if mapping[old_hex] != new_hex:
                collisions.add(old_hex)
            continue
        mapping[old_hex] = new_hex
    return {old: new for old, new in mapping.items() if old != new}, collisions


def _remap_rgb_u8(rgb, mapping):
    """Return (remapped, changed_mask); masks all come from the original colours."""
    rgb = rgb.astype(np.int16)
    out = rgb.copy()
    changed = np.zeros(rgb.shape[:-1], dtype=bool)
    for old_hex, new_hex in mapping.items():
        mask = np.all(np.abs(rgb - _hex_to_u8(old_hex)) <= _RECOLOR_TOLERANCE, axis=-1)
        out[mask] = _hex_to_u8(new_hex)
        changed |= mask
    return out, changed


def _remap_hex(hexcol, mapping):
    out, _changed = _remap_rgb_u8(_hex_to_u8(hexcol)[None, :], mapping)
    return _srgb_to_hex(*out[0])


def _remap_palette(palette, mapping, keep_hexes):
    remapped = {}
    for key in sorted(palette):
        hexcol = _remap_hex(palette[key], mapping)
        if hexcol in remapped.values() and key != 0:
            continue
        remapped[key] = hexcol
    for hexcol in keep_hexes:
        if hexcol not in remapped.values():
            remapped[(max(remapped) + 1) if remapped else 0] = hexcol
    return remapped


def _remap_float_pixels(pixels, mapping):
    arr = pixels.reshape(-1, 4)
    u8 = np.clip(np.rint(arr[:, :3] * 255.0), 0, 255)
    out, changed = _remap_rgb_u8(u8, mapping)
    arr[changed, :3] = out[changed].astype(np.float32) / 255.0
    return int(np.count_nonzero(changed))


def _remap_palette_prop(mesh, mapping, keep_hexes):
    import ast

    raw = mesh.get("3mf_paint_extruder_colors")
    if not raw:
        return
    try:
        palette = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        print(f"[TP3D colors] Invalid 3mf_paint_extruder_colors on {mesh.name}; left unchanged")
        return
    if not isinstance(palette, dict):
        return
    palette = _remap_palette(palette, mapping, keep_hexes)
    mesh["3mf_paint_extruder_colors"] = str(palette)
    owned = [key for key in _height_palette_keys(mesh) if key in palette]
    if owned:
        mesh[_HEIGHT_BAKE_PALETTE_KEY_PROP] = owned
    elif _HEIGHT_BAKE_PALETTE_KEY_PROP in mesh:
        del mesh[_HEIGHT_BAKE_PALETTE_KEY_PROP]


def _remap_height_bake_history(image_name, mapping, keep_hexes):
    import ast

    for state in _HEIGHT_BAKE_UNDO.get(image_name, []):
        if state["kind"] == "indexed":
            palette_u8 = state["palette"]
            rgb, _changed = _remap_rgb_u8(palette_u8[:, :3], mapping)
            state["palette"] = np.concatenate(
                (rgb.astype(np.uint8), palette_u8[:, 3:]), axis=1
            )
        else:
            _remap_float_pixels(state["pixels"], mapping)
        if state["meta_palette"]:
            try:
                palette = ast.literal_eval(state["meta_palette"])
            except (ValueError, SyntaxError):
                continue
            if isinstance(palette, dict):
                state["meta_palette"] = str(_remap_palette(palette, mapping, keep_hexes))


def recolor_paint_textures(new_roles, fallback_roles, objects):
    """Recolor TrailPrint3D paint textures used by objects to new_roles.

    Uses each mesh's tp3d_color_roles snapshot as the source colours, falling
    back to fallback_roles (the materials before the update) for older maps.
    An image shared with objects outside *objects* (e.g. split tiles using
    their source terrain's image) is recolored for those too.
    Returns {"meshes", "pixels", "collisions", "missing"}.
    """
    keep_hexes = [
        _srgb_to_hex(*_named_material_srgb("WHITE", (255, 255, 255))),
        _srgb_to_hex(*_named_material_srgb("BLACK")),
        _srgb_to_hex(*new_roles["TRAIL"]),
    ]
    stats = {"meshes": 0, "pixels": 0, "collisions": set(), "missing": []}
    processed_images = set()
    meshes = {obj.data for obj in objects if obj.type == "MESH"}
    for mesh in meshes:
        if not mesh.get("3mf_is_paint_texture"):
            continue
        # Split tiles and terrain-capped frames can still use the source
        # terrain's image, whose name no longer matches their mesh names.
        image = next(
            (
                node.image
                for material in mesh.materials
                if material and material.use_nodes and material.node_tree
                for node in material.node_tree.nodes
                if node.type == "TEX_IMAGE" and node.image
            ),
            None,
        )
        if image is None:
            if f"{mesh.name}_MMU_Solid" in bpy.data.images:
                continue
            image = bpy.data.images.get(f"{mesh.name}_MMU_Paint")
        if image is not None and image.name.endswith("_MMU_Solid"):
            continue
        if image is None:
            stats["missing"].append(mesh.name)
            continue

        old_roles = read_mesh_color_roles(mesh, fallback_roles)
        mapping, collisions = color_role_mapping(old_roles, new_roles)
        stats["collisions"] |= collisions
        if mapping:
            if image.name not in processed_images:
                pixels = np.empty(len(image.pixels), dtype=np.float32)
                image.pixels.foreach_get(pixels)
                changed = _remap_float_pixels(pixels, mapping)
                if changed:
                    image.pixels.foreach_set(pixels)  # type: ignore - Blender api accepts the numpy array
                    image.update()
                    if image.packed_file is not None:
                        image.pack()
                stats["pixels"] += changed
                _remap_height_bake_history(image.name, mapping, keep_hexes)
                processed_images.add(image.name)
            _remap_palette_prop(mesh, mapping, keep_hexes)
        write_mesh_color_roles(mesh, new_roles)
        stats["meshes"] += 1
    return stats
