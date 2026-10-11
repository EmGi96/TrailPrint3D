import math
import time
from collections import defaultdict
from dataclasses import dataclass, field

import bmesh  # type: ignore
import bpy  # type: ignore
import numpy as np  # type: ignore
from mathutils import Vector  # type: ignore
from shapely import make_valid
from shapely.geometry.polygon import orient

from ... import progress as _progress
from .. import geometry2d as g2d
from .fetch_solo import fetch_tier_polylines

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

TIER_TAGS: dict[str, set[str]] = {
    "big": {"motorway", "primary", "motorway_link", "primary_link"},
    "medium": {
        "secondary",
        "tertiary",
        "secondary_link",
        "tertiary_link",
        "unclassified",
        "trunk",
        "trunk_link",
    },
    "small": {"residential", "living_street"},
    "service": {"service"},
    "footway": {"footway"},
}

ALLEY_SERVICE_TYPES: frozenset[str] = frozenset(
    {"alley", "driveway", "parking_aisle", "drive-through"}
)

_ROAD_SIMPLIFY_TOL = 0.5


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass
class RoadConfig:
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float
    street_width_multiplier: float
    exclude_alleys: bool
    tier_active: dict[str, bool] = field(
        default_factory=lambda: {t: True for t in TIER_TAGS}
    )

    @classmethod
    def from_scene(cls, tp3d, full_depth: bool = False) -> "RoadConfig":
        tier_active = {
            "big": bool(tp3d.el_sBigActive),
            "medium": bool(tp3d.el_sMedActive),
            "small": bool(tp3d.el_sSmallActive),
            "service": bool(tp3d.el_sServiceActive),
            "footway": bool(tp3d.el_sFootwaysActive),
        }
        if full_depth:
            if tier_active["service"] or tier_active["footway"]:
                print(
                    "[TP3D roads] full_depth mode: excluding service/footway tiers "
                    "(too dense to remesh cleanly as a standalone piece)"
                )
            tier_active["service"] = False
            tier_active["footway"] = False

        return cls(
            min_lat=tp3d.minLat,
            min_lon=tp3d.minLon,
            max_lat=tp3d.maxLat,
            max_lon=tp3d.maxLon,
            street_width_multiplier=tp3d.el_sMultiplier,
            exclude_alleys=bool(tp3d.el_sExcludeAlleys),
            tier_active=tier_active,
        )


# ---------------------------------------------------------------------------
# Width helpers
# ---------------------------------------------------------------------------


def highway_default_width(highway: str) -> float:
    mapping = {
        "motorway": 6.0,
        "trunk": 6.0,
        "primary": 6.0,
        "secondary": 6.0,
        "footway": 6.0,
        "tertiary": 6.0,
        "residential": 6.0,
        "service": 6.0,
        "track": 6.0,
        "path": 6.0,
    }
    return mapping.get(highway, 6.0)


def _compute_half_width(scale_hor: float, multiplier: float) -> tuple[float, bool]:
    """Return ``(half_width, was_clamped)``.  The caller owns the warning side-effect."""
    width_m = highway_default_width("residential")
    half_width = (width_m * 0.5) * 0.2 * scale_hor * 0.02 * multiplier
    if half_width < 0.2:
        return 0.2, True
    return half_width, False


# ---------------------------------------------------------------------------
# Geometry helper
# ---------------------------------------------------------------------------


def _debug_dump_union_rings(
    name: str,
    road_union,
    z: float = 0.0,
    color: tuple = (1.0, 0.0, 0.0, 1.0),
) -> int:
    """
    Dump all exterior AND interior rings of a Shapely geometry as polygon-array
    curves in Blender.  Interior rings (holes = city blocks) are included so you
    can verify the union preserved the road gaps correctly.  Returns ring count.
    """
    rings: list[np.ndarray] = []
    for part in g2d.iter_polygons(road_union):
        ext = list(part.exterior.coords)[:-1]
        if len(ext) >= 3:
            rings.append(np.array(ext, dtype=np.float32))
        for interior in part.interiors:
            coords = list(interior.coords)[:-1]
            if len(coords) >= 3:
                rings.append(np.array(coords, dtype=np.float32))

    if rings:
        g2d.debug_dump_polygon_arrays(
            name,
            rings,
            collection_name="TP3D_Debug_Roads",
            z=z,
            color=color,
        )
    return len(rings)


def _buffer_tiers_to_polygons(
    tier_polylines: dict[str, list],
    half_width: float,
    map_footprint=None,
) -> tuple[list[tuple[float, float]], list[tuple[int, int, int]], object]:
    """
    For each tier: buffer each road segment individually, then union the buffered
    areas within that tier.  Finally union across tiers.  Tessellate the result
    (including holes = city blocks) into triangles.

    Per-segment buffering → no self-intersecting prisms at intersections.
    Per-tier unary_union first → Shapely's spatial index works on smaller sets.
    Final cross-tier union → single clean 2-D road footprint.
    make_valid instead of buffer(0) → preserves geometry, handles GEOS quirks.

    Returns (flat_verts_2d, triangle_faces, road_union_polygon). The polygon is
    returned too so the caller can later clip the terrain's own grid to the
    exact same 2-D shape (see finalize_roads / _clip_terrain_grid_to_polygon).
    """
    from shapely.ops import unary_union

    tier_unions = []

    for tier_name, polylines in tier_polylines.items():
        # tier_buffered is INSIDE the outer loop — one list per tier
        tier_buffered = []

        for coords in polylines:
            pts = list(coords)
            if len(pts) < 2:
                continue
            ln = g2d.LineString(pts)
            if ln.is_empty or ln.length == 0:
                continue

            buf = ln.buffer(
                half_width, quad_segs=2, cap_style="round", join_style="round"
            )
            if buf.is_empty:
                continue
            if not buf.is_valid:
                # buf = make_valid(buf, method="structure")
                buf = unary_union(buf)
            if buf and not buf.is_empty:
                tier_buffered.append(buf)

        if not tier_buffered:
            continue

        tier_union = unary_union(tier_buffered)
        if tier_union.is_empty:
            continue
        if not tier_union.is_valid:
            tier_union = make_valid(tier_union, method="structure")
        if tier_union and not tier_union.is_empty:
            if bpy.app.debug:
                n_segs = len(tier_buffered)
                n_rings = sum(
                    1 + len(list(p.interiors)) for p in g2d.iter_polygons(tier_union)
                )
                print(
                    f"[DEBUG] Stage 2 ({tier_name}): {n_segs} segments → "
                    f"{n_rings} ring(s) after tier union"
                )
            tier_unions.append(tier_union)

    if not tier_unions:
        return [], [], None

    road_union = unary_union(tier_unions)
    if road_union.is_empty:
        return [], [], None
    if not road_union.is_valid:
        road_union = make_valid(road_union)
    if road_union is None or road_union.is_empty:
        return [], [], None

    # Clip to map boundary in 2D — avoids a 3D boolean against a non-manifold terrain
    if map_footprint is not None and not map_footprint.is_empty:
        road_union = road_union.intersection(map_footprint)
        if road_union is None or road_union.is_empty:
            return [], [], None
        if not road_union.is_valid:
            road_union = make_valid(road_union)

    # --- DEBUG: Stage 2 — 2-D road union before tessellation ---------------
    # Shows exterior rings (road outlines) AND interior rings (city block holes).
    # If holes are missing here, the extrusion will be a solid block — not roads.
    if bpy.app.debug:
        n_rings = _debug_dump_union_rings(
            "roads_stage2_2d_union",
            road_union,
            z=2.0,
            color=(1.0, 0.0, 0.0, 1.0),  # Red
        )
        n_polys = sum(1 for _ in g2d.iter_polygons(road_union))
        print(
            f"[DEBUG] Stage 2: Dumped {n_polys} polygon(s), {n_rings} total ring(s) "
            f"(exterior + holes) at z=2.0"
        )

    all_verts_2d: list[tuple[float, float]] = []
    all_tris: list[tuple[int, int, int]] = []
    n_skipped = 0

    for part in g2d.iter_polygons(road_union):
        part = orient(part, sign=1.0)  # exterior CCW, holes CW
        ext = list(part.exterior.coords)[:-1]
        if len(ext) < 3:
            continue

        holes = [
            list(ring.coords)[:-1] for ring in part.interiors if len(ring.coords) >= 4
        ]

        # earcut handles holed polygons correctly; tessellate_polygon inverts fill
        ec = g2d._earcut_triangulate(ext, holes)
        if ec is None:
            n_skipped += 1
            continue
        verts2d_part, tris_part = ec
        base = len(all_verts_2d)
        all_verts_2d.extend(verts2d_part)
        for i, j, k in tris_part:
            all_tris.append((base + i, base + j, base + k))

    if n_skipped:
        print(
            f"[TP3D roads] Warning: {n_skipped} polygon(s) skipped due to "
            "tessellation failure"
        )

    return all_verts_2d, all_tris, road_union


# ---------------------------------------------------------------------------
# Mesh construction
# ---------------------------------------------------------------------------


def _build_extruded_mesh(
    verts_2d: list[tuple[float, float]],
    tris: list[tuple[int, int, int]],
    bottom_z: float,
    top_z: float,
) -> bpy.types.Object:
    """Build a watertight extruded slab from a pre-triangulated 2D road mesh."""
    from collections import defaultdict

    n = len(verts_2d)

    # Bottom verts then top verts
    all_verts = [(x, y, bottom_z) for x, y in verts_2d]
    all_verts += [(x, y, top_z) for x, y in verts_2d]

    faces = []

    # Bottom cap (reversed winding for downward-facing normals)
    for i, j, k in tris:
        faces.append((k, j, i))

    # Top cap
    for i, j, k in tris:
        faces.append((i + n, j + n, k + n))

    # Side walls — only on boundary edges (edges shared by exactly one triangle)
    edge_count: dict[tuple[int, int], int] = defaultdict(int)
    for i, j, k in tris:
        for a, b in ((i, j), (j, k), (k, i)):
            edge_count[(min(a, b), max(a, b))] += 1

    # Collect directed boundary edges (preserving triangle winding)
    for i, j, k in tris:
        for a, b in ((i, j), (j, k), (k, i)):
            if edge_count[(min(a, b), max(a, b))] == 1:
                faces.append((a, b, b + n, a + n))

    mesh = bpy.data.meshes.new("road_mesh")
    mesh.from_pydata(all_verts, [], faces)
    mesh.update(calc_edges=True)
    mesh.validate(verbose=False)

    roads = bpy.data.objects.new("Roads", mesh)
    bpy.context.collection.objects.link(roads)
    return roads


# ---------------------------------------------------------------------------
# Terrain-grid draping
# ---------------------------------------------------------------------------


def _triangulated_terrain_faces(map_obj: bpy.types.Object) -> list:
    """Return only the upward-facing triangles of the terrain as world-space triples.

    Must be captured BEFORE any boolean cuts a road/element out of the terrain.
    Filtering to upward-facing faces (normal.z > 0.5) excludes side walls and
    the flat base, both of which would inject wrong-Z geometry into the road
    mesh if allowed to be clipped by the road polygon.
    """
    bm = bmesh.new()
    bm.from_mesh(map_obj.data)
    bmesh.ops.triangulate(bm, faces=bm.faces)
    bm.normal_update()
    mw = map_obj.matrix_world
    mw_rot = mw.to_3x3().normalized()
    tris = []
    for f in bm.faces:
        if len(f.verts) != 3:
            continue
        world_normal = mw_rot @ f.normal
        if world_normal.z < 0.5:  # skip walls and bottom — only top surface
            continue
        p0, p1, p2 = (mw @ v.co for v in f.verts)
        tris.append(((p0.x, p0.y, p0.z), (p1.x, p1.y, p1.z), (p2.x, p2.y, p2.z)))
    bm.free()
    return tris


def _bary_z(tri: tuple, x: float, y: float) -> float:
    """Interpolate Z at (x, y) inside a flat 3-D triangle via barycentric coords."""
    (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = tri
    d = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
    if abs(d) < 1e-12:
        return (z0 + z1 + z2) / 3.0
    w0 = ((y1 - y2) * (x - x2) + (x2 - x1) * (y - y2)) / d
    w1 = ((y2 - y0) * (x - x2) + (x0 - x2) * (y - y2)) / d
    w2 = 1.0 - w0 - w1
    return w0 * z0 + w1 * z1 + w2 * z2


def _clip_terrain_grid_to_polygon(
    terrain_tris: list,
    polygon,
    z_offset: float,
) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]]]:
    """Clip the terrain's own triangulated grid to a 2-D road polygon (holes included).

    Triangles fully inside the polygon are kept verbatim -- same vertices, same
    connectivity as the terrain mesh -- which is what gives the road top the
    identical resolution/pattern as the terrain and painted elements, instead
    of an independent (and much uglier) earcut triangulation. Triangles
    straddling the polygon boundary are Shapely-clipped and earcut-filled only
    for that sliver; height at any new vertex is barycentric-interpolated from
    the original flat terrain triangle, so it's exact, not approximated.
    Terrain outside the polygon (or inside a hole, e.g. a city block) is
    dropped, which is what carves the hole into the road mesh.
    """
    from shapely.geometry import Polygon as ShPolygon
    from shapely.prepared import prep

    if polygon is None or polygon.is_empty:
        return [], []

    # Cheap bounding-box pre-filter: only construct ShPolygon for triangles
    # whose AABB overlaps the road polygon bbox.  For a road covering ~5% of
    # the terrain this eliminates ~95% of ShPolygon constructions vs. the old
    # STRtree approach (which built ShPolygon for every tri unconditionally).
    pbounds = polygon.bounds          # (minx, miny, maxx, maxy)
    px0, py0, px1, py1 = pbounds
    prepared = prep(polygon)

    tri_polys = []
    tri_data = []
    for tri in terrain_tris:
        (x0, y0, _z0), (x1, y1, _z1), (x2, y2, _z2) = tri
        if max(x0, x1, x2) < px0 or min(x0, x1, x2) > px1:
            continue
        if max(y0, y1, y2) < py0 or min(y0, y1, y2) > py1:
            continue
        tp = ShPolygon(((x0, y0), (x1, y1), (x2, y2)))
        if tp.is_empty or not tp.is_valid or tp.area <= 1e-12:
            continue
        tri_polys.append(tp)
        tri_data.append(tri)

    if not tri_polys:
        return [], []

    out_verts: list[tuple[float, float, float]] = []
    out_tris: list[tuple[int, int, int]] = []
    vert_cache: dict[tuple[float, float, float], int] = {}

    def _get_vert(x: float, y: float, z: float) -> int:
        key = (round(x, 5), round(y, 5), round(z, 5))
        idx = vert_cache.get(key)
        if idx is None:
            idx = len(out_verts)
            out_verts.append((x, y, z))
            vert_cache[key] = idx
        return idx

    for idx in range(len(tri_polys)):
        tp = tri_polys[idx]
        tri = tri_data[idx]

        if prepared.contains(tp):
            i0 = _get_vert(tri[0][0], tri[0][1], tri[0][2] + z_offset)
            i1 = _get_vert(tri[1][0], tri[1][1], tri[1][2] + z_offset)
            i2 = _get_vert(tri[2][0], tri[2][1], tri[2][2] + z_offset)
            out_tris.append((i0, i1, i2))
            continue

        if not prepared.intersects(tp):
            continue

        inter = tp.intersection(polygon)
        if inter.is_empty:
            continue

        for part in g2d.iter_polygons(inter):
            part = orient(part, sign=1.0)  # exterior CCW, holes CW -- matches terrain winding
            ext = list(part.exterior.coords)[:-1]
            if len(ext) < 3:
                continue
            holes = [
                list(ring.coords)[:-1] for ring in part.interiors if len(ring.coords) >= 4
            ]
            ec = g2d._earcut_triangulate(ext, holes)
            if ec is None:
                continue
            verts2d_part, tris_part = ec
            local_idx = []
            for vx, vy in verts2d_part:
                vz = _bary_z(tri, vx, vy) + z_offset
                local_idx.append(_get_vert(vx, vy, vz))
            for a, b, c in tris_part:
                out_tris.append((local_idx[a], local_idx[b], local_idx[c]))

    return out_verts, out_tris


def _build_variable_extruded_mesh(
    top_verts: list[tuple[float, float, float]],
    top_tris: list[tuple[int, int, int]],
    bottom_zs: list[float],
) -> tuple[list[tuple[float, float, float]], list[tuple]]:
    """Mirror a clipped top surface straight down to build the full watertight slab.

    ``bottom_zs`` gives one Z per top vertex -- either a repeated flat value
    (full-depth printable base) or a terrain-following value (thin PAINT
    slab). Reuses the exact same triangle connectivity for both caps and for
    boundary-edge wall detection, so there's no separate bottom tessellation
    to fall out of sync with the top.
    """
    n = len(top_verts)
    all_verts = list(top_verts)
    all_verts += [(x, y, bottom_zs[i]) for i, (x, y, _z) in enumerate(top_verts)]

    faces: list[tuple] = []
    for i, j, k in top_tris:
        faces.append((i, j, k))  # top cap
    for i, j, k in top_tris:
        faces.append((k + n, j + n, i + n))  # bottom cap, reversed winding

    edge_count: dict[tuple[int, int], int] = defaultdict(int)
    for i, j, k in top_tris:
        for a, b in ((i, j), (j, k), (k, i)):
            edge_count[(min(a, b), max(a, b))] += 1
    for i, j, k in top_tris:
        for a, b in ((i, j), (j, k), (k, i)):
            if edge_count[(min(a, b), max(a, b))] == 1:
                faces.append((a, b, b + n, a + n))

    return all_verts, faces


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def finalize_roads(
    roads: bpy.types.Object,
    terrain_tris: list,
    road_polygon,
    el_sHeight: float,
    full_depth: bool,
    bottom_z: float,
) -> None:
    """Rebuild the road mesh's top surface from the terrain's own triangulated
    grid, clipped to the road footprint, so it shares the exact same
    resolution/pattern as the terrain and painted elements instead of an
    independent (uglier) earcut triangulation, and has no stray/spiked verts
    from ray-cast misses.

    ``terrain_tris`` must have been captured from the terrain BEFORE any
    boolean cut removed geometry under the road footprint (see
    _triangulated_terrain_faces, cached early in _rg_build_terrain_elements).
    Call this AFTER the road mesh has been used as a (coarse, cheap) boolean
    cutter against terrain/elements -- this rebuild is independent of that
    coarse mesh's own vertices, so it doesn't matter that they no longer
    exist by the time this runs.

    full_depth=True (SEPARATE/SINGLECOLORMODE*): bottom cap is flat at
    bottom_z, a normal printable base.
    full_depth=False (PAINT): bottom cap mirrors the top, offset down by
    2*el_sHeight, giving a thin slab that hugs the terrain surface on both
    faces instead of reaching down to the base.
    """
    if roads.data.get('tp3d_roads_finalized'):
        return
    if not terrain_tris or road_polygon is None or road_polygon.is_empty:
        return

    _t0 = time.time()
    top_verts, top_tris = _clip_terrain_grid_to_polygon(terrain_tris, road_polygon, el_sHeight)
    if not top_verts or not top_tris:
        print("[TP3D roads] finalize_roads: terrain-grid clip produced no geometry")
        return

    if full_depth:
        bottom_zs = [bottom_z] * len(top_verts)
    else:
        # top = terrain_z + el_sHeight; bottom = terrain_z (slab sits on surface, not inside it)
        bottom_zs = [z - el_sHeight for _x, _y, z in top_verts]

    all_verts, faces = _build_variable_extruded_mesh(top_verts, top_tris, bottom_zs)

    mw_inv = roads.matrix_world.inverted()
    local_verts = [mw_inv @ Vector(v) for v in all_verts]

    mesh = bpy.data.meshes.new("road_mesh")
    mesh.from_pydata(local_verts, [], faces)
    mesh.update(calc_edges=True)
    mesh.validate(verbose=False)

    old_mesh = roads.data
    for mat in old_mesh.materials:
        mesh.materials.append(mat)  # from_pydata() starts with no material slots
    roads.data = mesh
    bpy.data.meshes.remove(old_mesh)
    roads.data['tp3d_roads_finalized'] = 1  # type: ignore[index]

    from ..mesh_ops import recalculateNormals
    recalculateNormals(roads)

    print(
        f"[TP3D roads] finalize_roads: {len(all_verts)} verts, "
        f"{len(faces)} faces, took {time.time() - _t0:.1f}s"
    )


def roads_geometry_for_polygon(
    road_polygon,
    terrain_tris: list,
    el_sHeight: float,
) -> tuple[list | None, list | None]:
    """Return (verts, faces) for the road footprint clipped to *road_polygon*.

    Mirrors finalize_roads but for an arbitrary polygon (e.g. one puzzle piece).
    Returns (None, None) if the polygon yields no geometry.
    """
    top_verts, top_tris = _clip_terrain_grid_to_polygon(terrain_tris, road_polygon, el_sHeight)
    if not top_verts or not top_tris:
        return None, None
    bottom_zs = [z - el_sHeight for _x, _y, z in top_verts]
    all_verts, faces = _build_variable_extruded_mesh(top_verts, top_tris, bottom_zs)
    return all_verts, faces


def create_roads(map, default_height=10, scaleHor=1.0, mapsize=1, full_depth=False):
    _t_setup = time.time()
    _ov = _progress.ProgressOverlay.get()
    if _ov.active:
        _ov.set_fetch_progress("roads", 0.0)

    # --- Config ---------------------------------------------------------
    config = RoadConfig.from_scene(bpy.context.scene.tp3d, full_depth=full_depth)

    # --- Fetch ----------------------------------------------------------
    tier_polylines = fetch_tier_polylines(
        config.min_lat,
        config.min_lon,
        config.max_lat,
        config.max_lon,
        TIER_TAGS,
        config.tier_active,
        config.exclude_alleys,
        ALLEY_SERVICE_TYPES,
        progress_overlay=_ov,
    )
    if tier_polylines is None:
        return None

    # --- DEBUG: Stage 1 - Raw polylines from OSM -----------------------
    if bpy.app.debug:
        # Show the raw centerlines at z=0
        all_polylines = []
        for tier_name, polylines in tier_polylines.items():
            if polylines and tier_polylines.get(tier_name):
                all_polylines.extend(polylines)

        if all_polylines:
            g2d.debug_dump_polylines(
                "roads_stage1_raw_polylines",
                all_polylines,
                collection_name="TP3D_Debug_Roads",
                z=0.0,
                color=(0.0, 1.0, 0.0, 1.0),  # Green
            )
            print(
                f"[DEBUG] Stage 1: Dumped {len(all_polylines)} raw polylines at z=0.0"
            )

    if _ov.active:
        _ov.set_fetch_progress("roads", 0.30)
        _ov.update(message="Roads: buffering each tier…")

    # --- Width ----------------------------------------------------------
    half_width, width_was_adjusted = _compute_half_width(
        scaleHor, config.street_width_multiplier
    )

    # --- Z bounds from terrain ------------------------------------------
    mc = [map.matrix_world @ Vector(c) for c in map.bound_box]
    bottom_z = min(v.z for v in mc) - 1.0
    top_z = max(v.z for v in mc) + default_height

    # --- 2-D map clip ---------------------------------------------------
    # Clip road footprint to map boundary in Shapely rather than via 3D boolean.
    # Terrain meshes are non-manifold so boolean INTERSECT produces corrupt geometry.
    map_fp = g2d.map_footprint_polygon(map)

    # --- Buffer ---------------------------------------------------------
    verts_2d, tris, road_union = _buffer_tiers_to_polygons(tier_polylines, half_width, map_fp)
    if not verts_2d or not tris:
        print("No road data returned")
        return None

    # --- Mesh -----------------------------------------------------------
    roads = _build_extruded_mesh(verts_2d, tris, bottom_z, top_z)

    # This is a coarse cutter mesh only -- finalize_roads() rebuilds the top
    # surface from the terrain's own grid, clipped to road_union, AFTER this
    # mesh has been used as a (fast) boolean cutter against terrain/elements.

    # --- DEBUG: Stage 3 - The extruded mesh (before boolean) -----------
    if bpy.app.debug:
        # Make a copy of the mesh at a different location for debugging
        debug_roads = roads.copy()
        debug_roads.data = roads.data.copy()
        debug_roads.name = "roads_stage3_extruded"
        debug_roads.location = (0, 0, -30.0)  # Offset downward to see separately
        bpy.context.collection.objects.link(debug_roads)
        print(f"[DEBUG] Stage 3: Created extruded mesh copy at z=-30.0")

    if _ov.active:
        _ov.set_fetch_progress("roads", 0.90)

    # --- DEBUG: Stage 5 - After final extrusion ------------------------
    if bpy.app.debug:
        # Make a copy of the final mesh
        debug_roads_final = roads.copy()
        debug_roads_final.data = roads.data.copy()
        debug_roads_final.name = "roads_stage5_final"
        debug_roads_final.location = (0, 0, -60.0)  # Offset even lower
        bpy.context.collection.objects.link(debug_roads_final)
        print(f"[DEBUG] Stage 5: Created final mesh copy at z=-60.0")

    # --- Finalise -------------------------------------------------------
    bpy.ops.object.select_all(action="DESELECT")
    roads.select_set(True)
    bpy.context.view_layer.objects.active = roads

    if _ov.active:
        _ov.set_fetch_progress("roads", 1.0)

    if width_was_adjusted:
        _progress.WarningsOverlay.add_warning(
            "Some roads were too thin and made thicker", "warn"
        )

    print(
        f"[TP3D roads] final mesh ({len(roads.data.vertices)} verts) took "
        f"{time.time() - _t_setup:.1f}s total"
    )
    return roads, road_union
