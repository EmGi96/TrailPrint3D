import math
import time

import bmesh  # type: ignore
import bpy  # type: ignore
import numpy as np  # type: ignore
from mathutils import Vector  # type: ignore
from mathutils.bvhtree import BVHTree  # type: ignore

from ... import constants as const
from ... import progress as _progress
from .. import geometry2d as g2d
from ..geometry2d import _earcut_triangulate
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


def _append_building(poly, z_offset, sample_z, b_verts, b_faces):
    # poly: shapely Polygon (single, possibly with holes). Builds a manifold
    # prism with a flat floor at the lowest terrain point under the footprint
    # and a flat roof at the highest terrain point + height.
    ext = list(poly.exterior.coords)
    if len(ext) > 1 and ext[0] == ext[-1]:
        ext = ext[:-1]
    if len(ext) < 3:
        return
    holes = []
    for interior in poly.interiors:
        ring = list(interior.coords)
        if len(ring) > 1 and ring[0] == ring[-1]:
            ring = ring[:-1]
        if len(ring) >= 3:
            holes.append(ring)
    ec = _earcut_triangulate(ext, holes)
    if ec is None:
        return
    verts2d, cap_tris = ec
    # Vectorized terrain-height lookup for every footprint vertex at once.
    _vx = [v[0] for v in verts2d]
    _vy = [v[1] for v in verts2d]
    zs = sample_z(_vx, _vy)
    z_min = float(zs.min())
    z_top = float(zs.max()) + z_offset
    n2 = len(verts2d)
    base = len(b_verts)
    for vx, vy in verts2d:
        b_verts.append((vx, vy, z_min))
    for vx, vy in verts2d:
        b_verts.append((vx, vy, z_top))
    for ia, ib, ic in cap_tris:
        b_faces.append([base + ic, base + ib, base + ia])  # floor (down)
        b_faces.append(
            [base + n2 + ia, base + n2 + ib, base + n2 + ic]
        )  # roof (up)
    # Walls around the exterior ring and each hole. Ring ranges follow
    # earcut's vertex order (exterior first, then holes).
    start = 0
    for ring in [ext] + holes:
        rn = len(ring)
        for i in range(rn):
            a = base + start + i
            b = base + start + (i + 1) % rn
            c = base + n2 + start + (i + 1) % rn
            d = base + n2 + start + i
            b_faces.append([a, b, c, d])
        start += rn


def buildings_geometry_for_polygon(piece_polygon, buildings_data):
    """Return (verts, faces) for every building footprint clipped to *piece_polygon*.

    Mirrors roads_geometry_for_polygon but for buildings: `buildings_data` is
    the (footprints, sample_z) tuple cached in `_puzzle_buildings_data` by
    create_buildings during the puzzle blank's own generation, since buildings
    are otherwise a single standalone object never cut along the jigsaw seams.
    """
    footprints, sample_z = buildings_data
    b_verts, b_faces = [], []
    for poly, z_offset in footprints:
        clipped = g2d.validate(poly.intersection(piece_polygon))
        if clipped is None or clipped.is_empty:
            continue
        for part in g2d.iter_polygons(clipped):
            _append_building(part, z_offset, sample_z, b_verts, b_faces)
    if not b_verts:
        return None, None
    return b_verts, b_faces


def create_buildings(map, default_height=10, scaleHor=1.0):

    # Mercator scale used by convert_to_blender_coordinates (it reads sScaleHor
    # from the scene). Read once so the vectorized node conversion matches.
    _sScaleHor = bpy.context.scene.tp3d.sScaleHor
    _t_setup = time.time()

    # Copy map and extrude vertical faces outward
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
    eval_wall = wall_obj.evaluated_get(depsgraph)
    bm_bvh = bmesh.new()
    bm_bvh.from_mesh(eval_wall.to_mesh())
    bm_bvh.transform(wall_obj.matrix_world)
    terrain_bvh = BVHTree.FromBMesh(bm_bvh)
    bm_bvh.free()

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

    minLat = bpy.context.scene.tp3d.minLat
    minLon = bpy.context.scene.tp3d.minLon
    maxLat = bpy.context.scene.tp3d.maxLat
    maxLon = bpy.context.scene.tp3d.maxLon

    # Geometry for ALL buildings across every tile is accumulated here and built
    # into one mesh at the very end.
    b_verts = []
    b_faces = []
    # Every (footprint, z_offset) pair that fed the mesh above, cached into
    # _puzzle_buildings_data at the end of this function -- the puzzle flow
    # re-clips these per piece since the mesh itself is never cut along the
    # jigsaw seams.
    _puzzle_footprints = []
    b_height_mult = bpy.context.scene.tp3d.el_bHeightMultiplier

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

                    # build 2D footprint coords from cached node_xy
                    footprint = []
                    for nid in node_ids:
                        if nid in node_xy:
                            x, y, nlat, nlon = node_xy[nid]
                            footprint.append((x, y))
                    if len(footprint) < 3:
                        continue

                    height = safe_float_height(tags.get("height", default_height))
                    levels = safe_float_height(tags.get("building:levels", 0))
                    if levels != 0:
                        height = levels * 2.7

                    z_offset = height * 0.002 * scaleHor * b_height_mult

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

                    # Each (clipped) polygon part becomes its own manifold prism.
                    for part in g2d.iter_polygons(poly, min_area=min_area):
                        _puzzle_footprints.append((part, z_offset))
                        _append_building(part, z_offset, _sample_z, b_verts, b_faces)

                _t_geom += time.time() - _t0

                if _ov.active:
                    _ov.update(
                        message=f"Buildings: tile {_cntr}/{_maxcntr} — creating {n_buildings} buildings…"
                    )

    print(
        f"[TP3D buildings] fetch={_t_fetch:.1f}s  convert={_t_convert:.1f}s  "
        f"geometry(clip+earcut+raycast)={_t_geom:.1f}s"
    )
    if _ov.active:
        _ov.set_fetch_progress("buildings", 0.75)
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
        _ov.set_fetch_progress("buildings", 0.90)

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
