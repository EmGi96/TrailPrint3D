import math
import time
from collections import Counter

import bmesh  # type: ignore
import bpy  # type: ignore
import numpy as np  # type: ignore
from mathutils import Vector  # type: ignore
from mathutils.bvhtree import BVHTree  # type: ignore

from ... import constants as const
from ... import progress as _progress
from .. import geometry2d as g2d
from ..dataclasses import GenerationContext
from ..mesh_ops import recalculateNormals
from ..scene import remove_objects
from .fetch_solo import fetch_osm_data

# Set after each buildings-enabled generation; read by the puzzle flow to clip
# per-building footprints against each jigsaw piece (buildings are otherwise
# a single standalone object never cut along the puzzle's own seams).
_puzzle_buildings_data: tuple | None = None


def _build_terrain_height_sampler(
    bvh, x_min, x_max, y_min, y_max, z_cast, z_floor, resolution=160
):
    """Sample terrain height for many (x, y) points fast, via a numpy grid.

    Raycasting a BVHTree is one ray at a time (no batch API), so draping ~200k
    footprint/road vertices one-by-one costs ~30s. Instead this raycasts a
    fixed resolution x resolution grid ONCE (e.g. 160 = 25.6k rays, a few
    seconds, independent of how many buildings/roads there are) and returns a
    vectorized bilinear sampler. Every subsequent height lookup is pure numpy.

    Returns sample(px, py) -> np.ndarray of z, accepting array-likes of equal
    length. Grid cells that miss the terrain fall back to z_floor.

    The grid is a smoothed approximation of the surface (cell spacing ~ map
    width / resolution). For the small, relatively flat maps roads/buildings
    appear on this is visually indistinguishable from per-vertex raycasting;
    raise *resolution* if roads/buildings cut into or float over steep terrain.
    """

    ray_down = Vector((0, 0, -1))
    nx = ny = max(2, int(resolution))
    gxs = np.linspace(x_min, x_max, nx)
    gys = np.linspace(y_min, y_max, ny)
    grid = np.empty((ny, nx), dtype=np.float64)
    for j in range(ny):
        gy = float(gys[j])
        for i in range(nx):
            hit, _, _, _ = bvh.ray_cast(Vector((float(gxs[i]), gy, z_cast)), ray_down)
            grid[j, i] = hit.z if hit is not None else z_floor
    dx = (x_max - x_min) / (nx - 1) if nx > 1 else 1.0
    dy = (y_max - y_min) / (ny - 1) if ny > 1 else 1.0

    def sample(px, py):
        px = np.asarray(px, dtype=np.float64)
        py = np.asarray(py, dtype=np.float64)
        fx = np.clip((px - x_min) / dx, 0.0, nx - 1)
        fy = np.clip((py - y_min) / dy, 0.0, ny - 1)
        ix = np.floor(fx).astype(np.intp)
        iy = np.floor(fy).astype(np.intp)
        ix1 = np.minimum(ix + 1, nx - 1)
        iy1 = np.minimum(iy + 1, ny - 1)
        tx = fx - ix
        ty = fy - iy
        z0 = grid[iy, ix] * (1.0 - tx) + grid[iy, ix1] * tx
        z1 = grid[iy1, ix] * (1.0 - tx) + grid[iy1, ix1] * tx
        return z0 * (1.0 - ty) + z1 * ty

    return sample


# ---------------------------------------------------------------------------
# Small shared helpers used by every roof-shape builder below.
# ---------------------------------------------------------------------------


def _poly_rings(poly):
    """Return (ext, holes) as plain coordinate-tuple lists, closing points
    stripped. Returns (None, None) if the exterior is degenerate."""
    ext = list(poly.exterior.coords)
    if len(ext) > 1 and ext[0] == ext[-1]:
        ext = ext[:-1]
    if len(ext) < 3:
        return None, None
    holes = []
    for interior in poly.interiors:
        ring = list(interior.coords)
        if len(ring) > 1 and ring[0] == ring[-1]:
            ring = ring[:-1]
        if len(ring) >= 3:
            holes.append(ring)
    return ext, holes


def _ring_indices(ring_coords, verts2d):
    """Return verts2d indices for each coord in ring_coords, using rounded lookup."""
    vert_map = {(round(x, 8), round(y, 8)): i for i, (x, y) in enumerate(verts2d)}
    return [vert_map[(round(x, 8), round(y, 8))] for x, y in ring_coords]


def _extrude_prism(ext, holes, verts2d, cap_tris, z_bottom, z_top, b_verts, b_faces):
    """Shared plain prism: flat floor cap at z_bottom, flat roof cap at
    z_top, walls around the exterior ring and each hole. This is the flat-roof
    building body, and is reused per-tier for the stadium terrace treatment.
    """
    n2 = len(verts2d)
    base = len(b_verts)
    for vx, vy in verts2d:
        b_verts.append((vx, vy, z_bottom))
    for vx, vy in verts2d:
        b_verts.append((vx, vy, z_top))
    for ia, ib, ic in cap_tris:
        b_faces.append([base + ic, base + ib, base + ia])  # floor (down)
        b_faces.append([base + n2 + ia, base + n2 + ib, base + n2 + ic])  # roof (up)
    for ring in [ext] + holes:
        ring_idxs = _ring_indices(ring, verts2d)
        rn = len(ring_idxs)
        for i in range(rn):
            a = base + ring_idxs[i]
            b = base + ring_idxs[(i + 1) % rn]
            c = base + n2 + ring_idxs[(i + 1) % rn]
            d = base + n2 + ring_idxs[i]
            b_faces.append([a, b, c, d])


def _add_height_field_roof(
    ext, holes, verts2d, cap_tris, z_floor, heights, b_verts, b_faces
):
    """Shared by skillion/gabled/hipped: identical topology to a flat prism,
    but each roof vertex gets its own z from *heights* (parallel to verts2d)
    instead of one constant z_top. The earcut cap triangles simply become
    non-planar, which renders fine as a faceted sloped/ridged roof."""
    n2 = len(verts2d)
    base = len(b_verts)
    for vx, vy in verts2d:
        b_verts.append((vx, vy, z_floor))
    for (vx, vy), z in zip(verts2d, heights):
        b_verts.append((vx, vy, z))
    for ia, ib, ic in cap_tris:
        b_faces.append([base + ic, base + ib, base + ia])
        b_faces.append([base + n2 + ia, base + n2 + ib, base + n2 + ic])
    for ring in [ext] + holes:
        ring_idxs = _ring_indices(ring, verts2d)
        rn = len(ring_idxs)
        for i in range(rn):
            a = base + ring_idxs[i]
            b = base + ring_idxs[(i + 1) % rn]
            c = base + n2 + ring_idxs[(i + 1) % rn]
            d = base + n2 + ring_idxs[i]
            b_faces.append([a, b, c, d])


def _skillion_heights(verts2d, z_eave, roof_height, roof_angle_deg, direction_deg):
    """Single sloped plane. direction_deg is a compass bearing (0=north,
    matching this codebase's +y-is-north convention) for the downslope
    direction; roof_height wins if given, else it's derived from roof_angle
    (default 20 deg) times the footprint's span along that direction."""
    ang = math.radians(direction_deg if direction_deg is not None else 0.0)
    dx, dy = math.sin(ang), math.cos(ang)
    proj = [vx * dx + vy * dy for vx, vy in verts2d]
    p_min, p_max = min(proj), max(proj)
    span = max(p_max - p_min, 1e-6)
    if roof_height is None:
        pitch = math.radians(roof_angle_deg if roof_angle_deg is not None else 20.0)
        roof_height = span * math.tan(pitch)
    # `direction_deg` is the compass bearing of the downhill slope, so height
    # must DECREASE moving that way (p_max = the low/eave side), not increase.
    return [z_eave + roof_height * (p_max - p) / span for p in proj]


def _fitted_rect_axes(poly):
    """Fit the footprint's minimum-rotated-rectangle and return its centroid,
    long-axis unit vector, and half-extents -- the local frame a gabled/hipped
    roof is built in. Returns None if the footprint is too irregular (coverage
    of the fitted rectangle too low) for a ridge roof to look right; the
    caller should fall back to a flat cap in that case."""
    try:
        rect = poly.minimum_rotated_rectangle
    except AttributeError:
        from shapely import oriented_envelope

        rect = oriented_envelope(poly)
    if rect is None or rect.is_empty or rect.area <= 0:
        return None
    coverage = poly.area / rect.area
    coords = list(rect.exterior.coords)[:4]
    if len(coords) < 4:
        return None
    cx = sum(c[0] for c in coords) / 4.0
    cy = sum(c[1] for c in coords) / 4.0
    e0 = Vector((coords[1][0] - coords[0][0], coords[1][1] - coords[0][1], 0))
    e1 = Vector((coords[2][0] - coords[1][0], coords[2][1] - coords[1][1], 0))
    along, cross = (e0, e1) if e0.length >= e1.length else (e1, e0)
    if along.length < 1e-6:
        return None
    along_n = along.normalized()
    return {
        "cx": cx,
        "cy": cy,
        "along": (along_n.x, along_n.y),
        "half_long": along.length / 2.0,
        "half_short": max(cross.length / 2.0, 1e-6),
        "coverage": coverage,
    }


def _ridge_heights(verts2d, axes, z_eave, roof_height, roof_angle_deg, hip):
    """Analytic height field for a ridge roof over a rectangle-ish footprint:
    'gabled' depends only on distance to the long axis (constant along the
    ridge, so the short ends naturally come out as vertical gable triangles);
    'hipped' takes the min of both axis tents, sloping on all four sides."""
    cx, cy = axes["cx"], axes["cy"]
    ax, ay = axes["along"]
    cxv, cyv = -ay, ax
    half_long, half_short = axes["half_long"], axes["half_short"]
    if roof_height is None:
        pitch = math.radians(roof_angle_deg if roof_angle_deg is not None else 30.0)
        roof_height = half_short * math.tan(pitch)
    heights = []
    for vx, vy in verts2d:
        dx, dy = vx - cx, vy - cy
        along_d = dx * ax + dy * ay
        cross_d = dx * cxv + dy * cyv
        t = max(0.0, 1.0 - abs(cross_d) / half_short)
        if hip:
            t_long = max(0.0, 1.0 - abs(along_d) / half_long)
            t = min(t, t_long)
        heights.append(z_eave + roof_height * t)
    return heights


def _add_dome_roof(
    ext, holes, verts2d, cap_tris, z_floor, z_eave, roof_height, style, b_verts, b_faces
):
    """Vertical walls up to z_eave, then a curved cap revolved around the
    footprint centroid instead of a flat roof: 'dome' tapers smoothly
    (hemisphere-like), 'onion' bulges outward before narrowing to a point.
    Vertices are scaled toward the centroid ring-by-ring, which holds up well
    for roughly convex footprints and degrades gracefully (slight overlap) on
    very concave ones -- acceptable for a roof silhouette."""
    n2 = len(verts2d)
    base = len(b_verts)
    for vx, vy in verts2d:
        b_verts.append((vx, vy, z_floor))
    for ia, ib, ic in cap_tris:
        b_faces.append([base + ic, base + ib, base + ia])  # floor
    eave_base = len(b_verts)
    for vx, vy in verts2d:
        b_verts.append((vx, vy, z_eave))
    start = 0
    for ring in [ext] + holes:
        rn = len(ring)
        for i in range(rn):
            a = base + start + i
            b = base + start + (i + 1) % rn
            c = eave_base + start + (i + 1) % rn
            d = eave_base + start + i
            b_faces.append([a, b, c, d])
        start += rn

    cx = sum(v[0] for v in verts2d) / n2
    cy = sum(v[1] for v in verts2d) / n2

    if style == "onion":
        # Skip t=0 -- that ring is identical to the eave ring already added above.
        profile = [(0.28, 1.18), (0.5, 1.0), (0.72, 0.45), (0.88, 0.16)]
    else:  # dome / hemisphere-like
        profile = [(t, math.cos(t * math.pi / 2.0)) for t in (0.25, 0.5, 0.75)]

    prev_base = eave_base
    for t, r in profile:
        z = z_eave + roof_height * t
        ring_base = len(b_verts)
        for vx, vy in verts2d:
            b_verts.append((cx + (vx - cx) * r, cy + (vy - cy) * r, z))
        start = 0
        for ring in [ext] + holes:
            rn = len(ring)
            for i in range(rn):
                a = prev_base + start + i
                b = prev_base + start + (i + 1) % rn
                c = ring_base + start + (i + 1) % rn
                d = ring_base + start + i
                b_faces.append([a, b, c, d])
            start += rn
        prev_base = ring_base

    apex_idx = len(b_verts)
    b_verts.append((cx, cy, z_eave + roof_height))
    start = 0
    for ring in [ext] + holes:
        rn = len(ring)
        for i in range(rn):
            a = prev_base + start + i
            b = prev_base + start + (i + 1) % rn
            b_faces.append([a, b, apex_idx])
        start += rn


def _add_stadium_bowl(
    poly, z_floor, z_eave, roof_height, roof_shape, b_verts, b_faces, n_tiers=4
):
    """Bowl-shaped stadium: the outermost tier is at full eave height and each
    inward tier steps down, giving a recessed-seating silhouette rather than a
    flat-topped block. Returns False if the footprint is too small/thin to inset
    at all (caller falls back to a plain prism)."""
    total_rise = max(z_eave - z_floor, 0.1)
    minx, miny, maxx, maxy = poly.bounds
    span = max(maxx - minx, maxy - miny)
    if span <= 0:
        return False
    step_inset = span * 0.12

    # Pre-build all inset rings so we know how many tiers actually fit.
    rings = [poly]
    current = poly
    for _ in range(n_tiers):
        candidate = g2d.validate(current.buffer(-step_inset))
        if (
            candidate is None
            or candidate.is_empty
            or candidate.geom_type != "Polygon"
            or candidate.area < current.area * 0.05
        ):
            break
        rings.append(candidate)
        current = candidate

    if len(rings) < 2:
        return False

    n_actual = len(rings) - 1
    actual_tier_rise = total_rise / n_actual

    # Each annulus: outer = rings[i], inner = rings[i+1].
    # Top height DECREASES inward — bowl, not ziggurat.
    for i in range(n_actual):
        z_top = z_eave - i * actual_tier_rise
        ext_o, holes_o = _poly_rings(rings[i])
        ext_i, _ = _poly_rings(rings[i + 1])
        if ext_o is None or ext_i is None:
            return i > 0
        annulus_holes = (holes_o or []) + [ext_i]
        _annulus_poly = g2d.Polygon(ext_o, annulus_holes)
        ec = g2d._cdt_triangulate(_annulus_poly, ext_o, annulus_holes)
        if ec is None:
            return i > 0
        verts2d_a, cap_tris_a, _ = ec
        _extrude_prism(
            ext_o, annulus_holes, verts2d_a, cap_tris_a, z_floor, z_top, b_verts, b_faces
        )

    # Innermost area (the open playing field): thin floor slab.
    ext_c, holes_c = _poly_rings(rings[-1])
    if ext_c is not None:
        ec = g2d._cdt_triangulate(rings[-1], ext_c, holes_c)
        if ec is not None:
            verts2d_c, cap_tris_c, _ = ec
            _extrude_prism(
                ext_c, holes_c, verts2d_c, cap_tris_c,
                z_floor, z_floor + actual_tier_rise * 0.1,
                b_verts, b_faces,
            )

    return True


def _append_building(
    poly,
    z_offset,
    sample_z,
    b_verts,
    b_faces,
    roof_shape=None,
    roof_height=None,
    roof_angle=None,
    roof_direction=None,
    z_min_offset=0.0,
    building_type=None,
):
    """Append one manifold building volume to b_verts/b_faces.

    poly: shapely Polygon (single, possibly with holes) footprint.
    z_offset: eave height above the local terrain max, already scaled to
        print units (see create_buildings).
    z_min_offset: OSM min_height, already scaled to print units -- absolute
        height above the real terrain where this part's own volume starts
        (0 for a normal ground-level building; >0 for e.g. a rooftop
        cupola or an antenna/spire mast whose `height` tag is its own
        absolute top, not a segment length relative to min_height). A solid
        pedestal is extruded from the real terrain up to that start height
        using the same footprint (see below) so the part is never left
        floating with no manifold path to the ground, without inflating a
        thin mast into a full-height spike by pretending it starts at grade.
    roof_shape / roof_height / roof_angle / roof_direction: OSM roof:* tag
        values, already unit-converted by the caller where relevant.
    building_type: OSM building=* value, used only to detect stadiums/
        grandstands for the tiered-terrace treatment.
    """
    ext, holes = _poly_rings(poly)
    if ext is None:
        return
    ec = g2d._cdt_triangulate(poly, ext, holes or [])
    if ec is None:
        return
    verts2d, cap_tris, _ = ec
    _vx = [v[0] for v in verts2d]
    _vy = [v[1] for v in verts2d]
    zs = sample_z(_vx, _vy)
    z_ground = float(zs.min())
    z_floor = z_ground + z_min_offset
    z_eave = float(zs.max()) + z_offset

    if z_min_offset > 1e-4:
        _extrude_prism(ext, holes, verts2d, cap_tris, z_ground, z_floor, b_verts, b_faces)

    rs = (roof_shape or "flat").strip().lower()
    bt = (building_type or "").strip().lower()
    rh_default = (
        roof_height if roof_height is not None else max(z_eave - z_floor, 0.1) * 0.5
    )
    # OSM `height` is the absolute peak including the roof; `roof:height` is the
    # roof's own vertical span, subtracted from it to get the eave -- NOT added
    # on top of `height` (that previously turned e.g. One WTC's skillion facets,
    # height=417 roof:height=362, into a spike to 417+362 instead of tapering
    # from 417-362=55 up to the correct peak at 417).
    wall_top = max(z_floor, z_eave - rh_default)

    if bt in ("stadium", "grandstand") and _add_stadium_bowl(poly, z_floor, z_eave, rh_default, rs, b_verts, b_faces):
        return
    # _add_stadium_bowl returned False (footprint too small/thin to inset) -- fall through to normal building.

    if rs in ("dome", "onion"):
        _add_dome_roof(
            ext,
            holes,
            verts2d,
            cap_tris,
            z_floor,
            wall_top,
            rh_default,
            rs,
            b_verts,
            b_faces,
        )
        return

    if rs == "tomb_pyramid":
        # Ground-to-apex with no vertical walls — tomb=pyramid only.
        n2 = len(verts2d)
        base = len(b_verts)
        cx = poly.centroid.x
        cy = poly.centroid.y
        apex_idx = base + n2
        for vx, vy in verts2d:
            b_verts.append((vx, vy, z_floor))
        b_verts.append((cx, cy, z_eave))
        for ia, ib, ic in cap_tris:
            b_faces.append([base + ic, base + ib, base + ia])  # floor
        start = 0
        for ring in [ext] + holes:
            rn = len(ring)
            for i in range(rn):
                a = base + start + i
                b = base + start + (i + 1) % rn
                b_faces.append([a, b, apex_idx])
            start += rn
        return

    if rs == "pyramidal":
        # Vertical walls up to eave, then a pyramidal cap — a tower with a pointed roof.
        n2 = len(verts2d)
        base = len(b_verts)
        cx = poly.centroid.x
        cy = poly.centroid.y
        for vx, vy in verts2d:
            b_verts.append((vx, vy, z_floor))
        eave_base = len(b_verts)
        for vx, vy in verts2d:
            b_verts.append((vx, vy, wall_top))
        for ia, ib, ic in cap_tris:
            b_faces.append([base + ic, base + ib, base + ia])  # floor
        start = 0
        for ring in [ext] + holes:
            rn = len(ring)
            for i in range(rn):
                a = base + start + i
                b = base + start + (i + 1) % rn
                c = eave_base + start + (i + 1) % rn
                d = eave_base + start + i
                b_faces.append([a, b, c, d])  # vertical wall
            start += rn
        apex_idx = len(b_verts)
        b_verts.append((cx, cy, z_eave))  # peak == the tag height, already incl. roof
        start = 0
        for ring in [ext] + holes:
            rn = len(ring)
            for i in range(rn):
                a = eave_base + start + i
                b = eave_base + start + (i + 1) % rn
                b_faces.append([a, b, apex_idx])  # pyramidal cap
            start += rn
        return

    if rs == "skillion":
        heights = _skillion_heights(
            verts2d, wall_top, roof_height, roof_angle, roof_direction
        )
        _add_height_field_roof(
            ext, holes, verts2d, cap_tris, z_floor, heights, b_verts, b_faces
        )
        return

    if rs in ("gabled", "hipped", "hip", "half-hipped", "gambrel"):
        axes = _fitted_rect_axes(poly)
        if axes is not None and axes["coverage"] >= 0.75:
            hip = rs in ("hipped", "hip", "half-hipped")
            heights = _ridge_heights(
                verts2d, axes, wall_top, roof_height, roof_angle, hip
            )
            _add_height_field_roof(
                ext, holes, verts2d, cap_tris, z_floor, heights, b_verts, b_faces
            )
            return
        # footprint isn't rectangular enough for a clean ridge -- fall through
        # to a flat cap below rather than producing a distorted roof.

    # Flat roof: also the fallback for any roof shape not modeled above.
    _extrude_prism(ext, holes, verts2d, cap_tris, z_floor, z_eave, b_verts, b_faces)


def buildings_geometry_for_polygon(piece_polygon, buildings_data):
    """Return (verts, faces) for every building footprint clipped to *piece_polygon*.

    Mirrors roads_geometry_for_polygon but for buildings: `buildings_data` is
    the (footprints, sample_z) tuple cached in `_puzzle_buildings_data` by
    create_buildings during the puzzle blank's own generation, since buildings
    are otherwise a single standalone object never cut along the jigsaw seams.
    """
    footprints, sample_z = buildings_data
    b_verts, b_faces = [], []
    for fp in footprints:
        clipped = g2d.validate(fp["poly"].intersection(piece_polygon))
        if clipped is None or clipped.is_empty:
            continue
        for part in g2d.iter_polygons(clipped):
            _append_building(
                part,
                fp["z_offset"],
                sample_z,
                b_verts,
                b_faces,
                roof_shape=fp["roof_shape"],
                roof_height=fp["roof_height"],
                roof_angle=fp["roof_angle"],
                roof_direction=fp["roof_direction"],
                z_min_offset=fp["z_min_offset"],
                building_type=fp["building_type"],
            )
    if not b_verts:
        return None, None
    return b_verts, b_faces


def create_buildings(gen: GenerationContext, default_height=10, scaleHor=1.0, prefetched_tiles=None):

    # Mercator scale used by convert_to_blender_coordinates (it reads sScaleHor
    # from the scene). Read once so the vectorized node conversion matches.
    _sScaleHor = bpy.context.scene.tp3d.sScaleHor
    _t_setup = time.time()

    # Copy map and extrude vertical faces outward
    map = gen.runtime.mapObject
    wall_obj = map.copy()
    wall_obj.data = map.data.copy()
    bpy.context.collection.objects.link(wall_obj)
    bpy.ops.object.select_all(action="DESELECT")
    wall_obj.select_set(True)
    bpy.context.view_layer.objects.active = wall_obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_mode(type="FACE")
    bpy.ops.mesh.select_all(action="DESELECT")
    bm = bmesh.from_edit_mesh(wall_obj.data)
    for f in bm.faces:
        f.select = abs(f.normal.normalized().z) < 0.1  # near-vertical faces
    bmesh.update_edit_mesh(wall_obj.data)
    bpy.ops.mesh.extrude_region_shrink_fatten(
        TRANSFORM_OT_shrink_fatten={"value": 20.0}
    )
    bpy.ops.object.mode_set(mode="OBJECT")

    # Build BVH once from wall_obj, then bake a terrain heightmap grid so the
    # per-vertex drape is a vectorized numpy lookup instead of ~200k one-by-one
    # BVH raycasts (which were the ~30s cost).
    depsgraph = bpy.context.evaluated_depsgraph_get()
    eval_terrain = map.evaluated_get(depsgraph)
    bm_terrain = bmesh.new()
    bm_terrain.from_mesh(eval_terrain.to_mesh())
    bm_terrain.transform(map.matrix_world)
    terrain_bvh = BVHTree.FromBMesh(bm_terrain)
    bm_terrain.free()

    _ov = _progress.ProgressOverlay.get()
    if _ov.active:
        _ov.set_fetch_progress("buildings", 0.0)

    minThickness = bpy.context.scene.tp3d.minThickness

    _mc = [map.matrix_world @ Vector(c) for c in map.bound_box]
    _x_min = min(v.x for v in _mc)
    _x_max = max(v.x for v in _mc)
    _y_min = min(v.y for v in _mc)
    _y_max = max(v.y for v in _mc)
    _z_cast = max(v.z for v in _mc) + 10.0
    _sample_z = _build_terrain_height_sampler(
        terrain_bvh, _x_min, _x_max, _y_min, _y_max, _z_cast, minThickness
    )

    minLat = gen.runtime.tbMinLat
    minLon = gen.runtime.tbMinLon
    maxLat = gen.runtime.tbMaxLat
    maxLon = gen.runtime.tbMaxLon
    # Printed so two separate runs (e.g. "normal" vs "puzzle") can be diffed
    # for an EXACT bbox match after the fact, instead of trying to hand-match
    # UI inputs beforehand -- this is what fetch_osm_data's cache key is
    # actually built from (see make_cache_key's 7-decimal rounding).
    print(
        f"[TP3D buildings] tile bounds: minLat={minLat:.7f} minLon={minLon:.7f} "
        f"maxLat={maxLat:.7f} maxLon={maxLon:.7f}"
    )

    # Geometry for ALL buildings across every tile is accumulated here and built
    # into one mesh at the very end.
    b_verts = []
    b_faces = []
    # Every footprint entry that fed the mesh above, cached into
    # _puzzle_buildings_data at the end of this function -- the puzzle flow
    # re-clips these per piece since the mesh itself is never cut along the
    # jigsaw seams. Each entry is a dict; see the fields appended below.
    _puzzle_footprints = []
    b_height_mult = bpy.context.scene.tp3d.el_bHeightMultiplier

    # Diagnostics only -- lets two generation runs (e.g. "normal" vs "puzzle")
    # over the same area be compared from the console instead of guessing why
    # one looks more detailed: how many buildings actually carry a roof:shape/
    # roof:height/building:part tag from Overpass, and how many footprints
    # survive the el_bMinPrintMM cull below.
    _roof_shape_counts = Counter()
    _has_roof_height_tag = 0
    _is_part_count = 0
    _entries_seen = 0
    _parts_kept = 0

    # Clip footprints to the map outline in 2D so buildings never spill past the
    # map edge -- robust, unlike a 3D boolean against a non-manifold building mesh.
    map_fp = g2d.map_footprint_polygon(map)
    print(
        f"[TP3D buildings] setup (wall extrude + BVH + map outline) took {time.time() - _t_setup:.1f}s"
    )
    if _ov.active:
        _ov.set_fetch_progress("buildings", 0.15)

    # Stage timers accumulated across all tiles.
    _t_fetch = 0.0
    _t_convert = 0.0
    _t_geom = 0.0

    # Cull buildings whose PRINTED footprint is too small to print cleanly. The
    # footprint coords are already in print units (same space as the map; 1 unit
    # ≈ 1 mm on the model), so the threshold is a direct printed-size cutoff. This
    # is inherently scale-aware: the same mm cutoff culls a larger real-world
    # building on a larger-km map, where everything prints smaller.
    min_area = bpy.context.scene.tp3d.el_bMinPrintMM**2

    lat_step = 2
    lon_step = 2

    lat_step = min(lat_step, maxLat - minLat)
    lon_step = min(lon_step, maxLon - minLon)

    lats = math.ceil((maxLat - minLat) / lat_step)
    lons = math.ceil((maxLon - minLon) / lon_step)

    if lats * lons < 20:
        for k in range(lats):
            for l in range(lons):
                _cntr = (k) * lons + l + 1
                _maxcntr = lats * lons
                print(f"Buildings loop: {_cntr}/{_maxcntr}")
                _ov = _progress.ProgressOverlay.get()
                if _ov.active:
                    _ov.update(
                        message=f"Buildings: tile {_cntr}/{_maxcntr} — processing…"
                    )
                south = minLat + k * lat_step
                north = south + lat_step
                west = minLon + l * lon_step
                east = west + lon_step

                bbox = (south, west, north, east)
                data = []

                _t0 = time.time()
                if prefetched_tiles is not None:
                    # Already fetched (and disk-cached) by the combined
                    # background prefetch -- avoid re-querying Overpass.
                    tile_result = prefetched_tiles.get(bbox)
                    data = tile_result[0] if tile_result else None
                else:
                    data = fetch_osm_data(bbox, "BUILDINGS")
                _t_fetch += time.time() - _t0

                if not data or "elements" not in data:
                    print("No Building data returned")
                    continue

                assert isinstance(data, dict)
                n_buildings = len([e for e in data["elements"] if e["type"] == "way"])
                if _ov.active:
                    _ov.update(
                        message=f"Buildings: tile {_cntr}/{_maxcntr} — calculating {n_buildings} buildings…"
                    )
                # Cache node id -> (lat, lon) and node id -> (x, y, z_base) to avoid repeated conversions
                raw_nodes = {
                    n["id"]: (n["lat"], n["lon"])
                    for n in data["elements"]
                    if n["type"] == "node"
                }

                # Compute 2D coordinates for every node in one vectorized numpy
                # pass instead of a per-node convert_to_blender_coordinates call
                # (each of which re-reads scene properties).
                _t0 = time.time()
                node_xy = {}
                if raw_nodes:
                    nid_list = list(raw_nodes.keys())
                    arr = np.array(
                        [raw_nodes[nid] for nid in nid_list], dtype=np.float64
                    )  # (N, 2) lat, lon
                    xs = const.R * np.radians(arr[:, 1]) * _sScaleHor
                    ys = (
                        const.R
                        * np.log(np.tan(np.pi / 4.0 + np.radians(arr[:, 0]) / 2.0))
                        * _sScaleHor
                    )
                    for nid, x, y, (nlat, nlon) in zip(
                        nid_list, xs.tolist(), ys.tolist(), arr.tolist()
                    ):
                        node_xy[nid] = (x, y, nlat, nlon)
                _t_convert += time.time() - _t0

                def safe_float_height(h):
                    # supports strings like "10", "10.0", "10 m"
                    if h is None:
                        return float(default_height)
                    if isinstance(h, (int, float)):
                        return float(h)
                    try:
                        s = str(h).strip().lower()
                        # strip units like "m"
                        if s.endswith("m"):
                            s = s[:-1].strip()
                        return float(s)
                    except (ValueError, TypeError):
                        return float(default_height)

                # Build a lookup for ways by id, so relations can reference them
                ways_by_id = {
                    e["id"]: e for e in data["elements"] if e["type"] == "way"
                }

                _t0 = time.time()
                _tile_total = max(1, len(data["elements"]))

                # First pass: parse every element in this tile into a footprint
                # entry (poly + tags) without building geometry yet. Elements
                # tagged building:part=* are rendered individually instead of
                # the building outline they sit inside (see the containment
                # pass below). This depends on fetch_osm_data's Overpass query
                # actually requesting building:part ways -- if it doesn't, no
                # entry will ever have is_part=True and behavior is identical
                # to before: every element renders as a stand-alone building.
                tile_entries = []
                for i, element in enumerate(data["elements"]):
                    if _ov.active and i % max(1, _tile_total // 20) == 0:
                        _elem_frac = ((_cntr - 1) + i / _tile_total) / _maxcntr
                        _ov.set_fetch_progress("buildings", 0.15 + 0.60 * _elem_frac)
                    if element["type"] == "relation":
                        # Find the outer member way and use its nodes as the footprint
                        outer_way = None
                        for member in element.get("members", []):
                            if (
                                member.get("type") == "way"
                                and member.get("role") == "outer"
                            ):
                                outer_way = ways_by_id.get(member["ref"])
                                if outer_way:
                                    break
                        if outer_way is None:
                            continue
                        # Treat the relation like the outer way but use relation tags if present
                        node_ids = outer_way.get("nodes", [])
                        tags = element.get("tags") or outer_way.get("tags", {})
                    elif element["type"] == "way":
                        node_ids = element.get("nodes", [])
                        tags = element.get("tags", {})
                    else:
                        continue

                    is_part = "building:part" in tags
                    if not is_part and "building" not in tags:
                        continue

                    # build 2D footprint coords from cached node_xy
                    footprint = []
                    for nid in node_ids:
                        if nid in node_xy:
                            x, y, nlat, nlon = node_xy[nid]
                            footprint.append((x, y))
                    if len(footprint) < 3:
                        continue

                    # An explicit height tag is real surveyed/modeled data and always
                    # wins; building:levels * 2.7m is only a fallback guess for
                    # buildings with no height tag at all. Previously this was
                    # backwards (levels always overrode height when present), which
                    # under-measured buildings like 28 Liberty (height=248 but
                    # building:levels=60 -> a wrong 162m) whenever a sibling
                    # building:part lacked a levels tag and kept its own correct
                    # height -- producing a mismatched, apparently "too tall" part.
                    if tags.get("height") is not None:
                        height = safe_float_height(tags.get("height"))
                    else:
                        levels = safe_float_height(tags.get("building:levels", 0))
                        height = levels * 2.7 if levels != 0 else float(default_height)
                    min_height = safe_float_height(
                        tags.get("min_height") or tags.get("building:min_height") or 0
                    )

                    roof_shape = (
                        "tomb_pyramid"
                        if tags.get("tomb") == "pyramid"
                        else tags.get("roof:shape")
                    )
                    _entries_seen += 1
                    _roof_shape_counts[roof_shape or "flat"] += 1
                    if is_part:
                        _is_part_count += 1
                    if tags.get("roof:height") is not None:
                        _has_roof_height_tag += 1
                    roof_height_tag = tags.get("roof:height")
                    if roof_height_tag is not None:
                        roof_height = (
                            safe_float_height(roof_height_tag)
                            * 0.002
                            * scaleHor
                            * b_height_mult
                        )
                    else:
                        # Clamp at 25 m so a skyscraper tagged pyramidal doesn't
                        # get a cap hundreds of metres tall.
                        roof_height = (
                            min(height * 0.3, 25.0) * 0.002 * scaleHor * b_height_mult
                        )
                    roof_angle = None
                    if tags.get("roof:angle") is not None:
                        try:
                            roof_angle = float(tags["roof:angle"])
                        except (ValueError, TypeError):
                            roof_angle = None
                    roof_direction = None
                    if tags.get("roof:direction") is not None:
                        try:
                            roof_direction = float(tags["roof:direction"])
                        except (ValueError, TypeError):
                            roof_direction = None

                    z_offset = height * 0.002 * scaleHor * b_height_mult
                    z_min_offset = min_height * 0.002 * scaleHor * b_height_mult

                    # Validate the footprint and clip it to the map shape in 2D.
                    # validate() repairs self-touching OSM outlines; the clip keeps
                    # buildings from spilling past the map edge.
                    poly = g2d.xy_ring_to_polygon(footprint)
                    if poly is None:
                        continue
                    if map_fp is not None:
                        poly = g2d.validate(poly.intersection(map_fp))
                    if poly is None or poly.is_empty:
                        continue

                    tile_entries.append(
                        {
                            "poly": poly,
                            "is_part": is_part,
                            "z_offset": z_offset,
                            "z_min_offset": z_min_offset,
                            "roof_shape": roof_shape,
                            "roof_height": roof_height,
                            "roof_angle": roof_angle,
                            "roof_direction": roof_direction,
                            "building_type": tags.get("building"),
                        }
                    )

                # Second pass: a base building outline that a building:part
                # sits inside is skipped in favor of rendering its parts
                # individually (otherwise you'd get a solid block AND the
                # detailed parts overlapping it). A building with no parts
                # (the common case today) renders exactly as before.
                # Uses an STRtree bbox query per base instead of an O(bases *
                # parts) brute-force scan -- with tens of thousands of
                # buildings in one tile (e.g. a dense city-center marathon
                # route) the brute-force version could mean billions of
                # shapely .contains() calls and looked like a hang.
                parts = [e for e in tile_entries if e["is_part"]]
                bases = [e for e in tile_entries if not e["is_part"]]
                if parts:
                    from shapely.strtree import STRtree

                    part_polys = [p["poly"] for p in parts]
                    part_reps = [p.representative_point() for p in part_polys]
                    tree = STRtree(part_reps)
                    for b in bases:
                        # predicate kwarg is broken in Blender's Shapely build --
                        # bbox-only query then filter manually (see terrain.py).
                        b["has_parts"] = any(
                            b["poly"].contains(part_reps[int(idx)])
                            for idx in tree.query(b["poly"])
                        )
                else:
                    for b in bases:
                        b["has_parts"] = False
                render_entries = parts + [b for b in bases if not b["has_parts"]]

                if _ov.active:
                    _ov.update(
                        message=f"Buildings: tile {_cntr}/{_maxcntr} — creating {n_buildings} buildings…"
                    )

                # Each (clipped) polygon part becomes its own manifold volume.
                # This triangulation/extrusion loop is the actual heavy cost for
                # large tiles, so it gets its own progress slice (0.75-0.98)
                # instead of silently running after the 0.75 parsing checkpoint.
                _n_render = max(1, len(render_entries))
                for _ri, entry in enumerate(render_entries):
                    if _ov.active and _ri % max(1, _n_render // 20) == 0:
                        _render_frac = ((_cntr - 1) + _ri / _n_render) / _maxcntr
                        _ov.set_fetch_progress("buildings", 0.75 + 0.23 * _render_frac)
                    for part in g2d.iter_polygons(entry["poly"], min_area=min_area):
                        _parts_kept += 1
                        _puzzle_footprints.append(
                            {
                                "poly": part,
                                "z_offset": entry["z_offset"],
                                "z_min_offset": entry["z_min_offset"],
                                "roof_shape": entry["roof_shape"],
                                "roof_height": entry["roof_height"],
                                "roof_angle": entry["roof_angle"],
                                "roof_direction": entry["roof_direction"],
                                "building_type": entry["building_type"],
                            }
                        )
                        _append_building(
                            part,
                            entry["z_offset"],
                            _sample_z,
                            b_verts,
                            b_faces,
                            roof_shape=entry["roof_shape"],
                            roof_height=entry["roof_height"],
                            roof_angle=entry["roof_angle"],
                            roof_direction=entry["roof_direction"],
                            z_min_offset=entry["z_min_offset"],
                            building_type=entry["building_type"],
                        )

                _t_geom += time.time() - _t0

    print(
        f"[TP3D buildings] fetch={_t_fetch:.1f}s  convert={_t_convert:.1f}s  "
        f"geometry(clip+earcut+raycast)={_t_geom:.1f}s"
    )
    print(
        f"[TP3D buildings] DIAGNOSTIC: {_entries_seen} tagged elements parsed, "
        f"{_is_part_count} building:part, {_has_roof_height_tag} with roof:height tag, "
        f"{_parts_kept} footprints survived el_bMinPrintMM={bpy.context.scene.tp3d.el_bMinPrintMM} cull. "
        f"roof:shape distribution: {dict(_roof_shape_counts)}"
    )
    if _ov.active:
        _ov.set_fetch_progress("buildings", 0.985)
        _ov.update(message="Buildings: building mesh…")
    _t0 = time.time()
    remove_objects(wall_obj)

    global _puzzle_buildings_data
    _puzzle_buildings_data = (_puzzle_footprints, _sample_z)

    if not b_verts:
        return None

    # Build one mesh containing every building across all tiles.
    mesh = bpy.data.meshes.new("building_mesh")
    mesh.from_pydata(b_verts, [], b_faces)
    mesh.update(calc_edges=True)

    obj = bpy.data.objects.new("Buildings", mesh)
    bpy.context.collection.objects.link(obj)

    mesh.validate(verbose=False)
    mesh.update(calc_edges=True)
    bpy.context.view_layer.update()

    if _ov.active:
        _ov.set_fetch_progress("buildings", 0.99)

    for poly in mesh.polygons:
        poly.use_smooth = False  # flat shading for buildings

    recalculateNormals(obj)
    if _ov.active:
        _ov.set_fetch_progress("buildings", 1.0)

    mat = bpy.data.materials.get("BUILDINGS")
    obj.data.materials.clear()
    obj.data.materials.append(mat)

    print(
        f"[TP3D buildings] final mesh build ({len(b_verts)} verts) took {time.time() - _t0:.1f}s"
    )
    return obj
