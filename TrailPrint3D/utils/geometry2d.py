"""Pure-2D Shapely geometry helpers for the TrailPrint3D OSM pipeline.

All functions operate in the (x, y) plane in Blender-Mercator space.
Shapely ignores Z throughout; callers are responsible for stripping and
re-adding Z coordinates.

Import guard
------------
_HAS_SHAPELY is True once the bundled wheel loads cleanly.  If Shapely is
missing (old Blender build, corrupted wheel, etc.) every public function
raises ImportError with a message that tells the user to reinstall from
the latest .zip.
"""

import math
import time
from typing import Any

import bmesh  # type: ignore
import bpy  # type: ignore
import numpy as np  # type: ignore

from .dataclasses import GenerationContext

# These values are overwritten by _load_shapely() on success.
_HAS_SHAPELY: bool = False
_SHAPELY_MAJOR: int = 0
_SHAPELY_IMPORT_ERROR: Exception | None = None
_shapely: Any = None  # the shapely module, stored for set_precision etc.
Polygon: Any = None
MultiPolygon: Any = None
LineString: Any = None
MultiLineString: Any = None
GeometryCollection: Any = None
Point: Any = None
box: Any = None
orient: Any = None
prep: Any = None
_make_valid_compat: Any = None
_make_valid_v2: Any = None
union_all: Any = None
polygonize: Any = None


def _load_shapely():
    """Attempt to import Shapely and populate module-level globals.

    Returns True on success, False on ImportError (which is stored in
    ``_SHAPELY_IMPORT_ERROR`` for later reporting).
    """
    global _HAS_SHAPELY, _SHAPELY_MAJOR, _SHAPELY_IMPORT_ERROR, _shapely
    global Polygon, MultiPolygon, LineString, MultiLineString, GeometryCollection
    global Point, box, orient, prep
    global _make_valid_compat, _make_valid_v2, union_all, polygonize
    try:
        import shapely as _shapely_mod
        from shapely import make_valid as _mv2
        from shapely import union_all as _uu
        from shapely.geometry import (
            GeometryCollection as _GC,
        )
        from shapely.geometry import (
            LineString as _LS,
        )
        from shapely.geometry import (
            MultiLineString as _MLS,
        )
        from shapely.geometry import (
            MultiPolygon as _MP,
        )
        from shapely.geometry import (
            Point as _Pt,
        )
        from shapely.geometry import (
            Polygon as _P,
        )
        from shapely.geometry import (
            box as _box,
        )
        from shapely.geometry.polygon import orient as _orient
        from shapely.ops import polygonize as _pg
        from shapely.prepared import prep as _prep
        from shapely.validation import make_valid as _mvc

        Polygon = _P
        MultiPolygon = _MP
        LineString = _LS
        MultiLineString = _MLS
        GeometryCollection = _GC
        Point = _Pt
        box = _box
        orient = _orient
        prep = _prep
        _make_valid_compat = _mvc
        _make_valid_v2 = _mv2
        union_all = _uu
        polygonize = _pg
        _shapely = _shapely_mod
        _HAS_SHAPELY = True
        _SHAPELY_MAJOR = int(_shapely_mod.__version__.split(".")[0])
        _SHAPELY_IMPORT_ERROR = None
        return True
    except ImportError as _e:
        _HAS_SHAPELY = False
        _SHAPELY_MAJOR = 0
        _SHAPELY_IMPORT_ERROR = _e
        return False


_load_shapely()
if not _HAS_SHAPELY:
    print(f"[TrailPrint3D] Shapely import failed: {_SHAPELY_IMPORT_ERROR!r}")

_SHAPELY_ERR = (
    "TrailPrint3D requires Shapely 2.x. "
    "Reinstall the addon from the latest .zip to get the bundled wheel."
)
if _HAS_SHAPELY and _SHAPELY_MAJOR < 2:
    print(
        f"[TrailPrint3D] WARNING: Shapely {_shapely.__version__} loaded "
        "(expected 2.x from bundled wheel). "
        "Ocean/OSM geometry may be degraded. "
        "Check that the addon zip was installed correctly."
    )


def _require_shapely():
    """Ensure Shapely is available, retrying a live import if the static flag
    is False.

    The static ``_HAS_SHAPELY`` flag is set once at module-import time.  On
    first install the wheel may not yet be importable at that moment.
    Rather than forcing the user to restart Blender, this function retries via
    ``_load_shapely()`` each time it is called while the flag is still False
    and promotes all module-level globals on success.
    """
    if _HAS_SHAPELY:
        return

    # Static check failed — attempt a live re-import now that the wheel may
    # have been extracted / released by AV since this module was first loaded.
    if _load_shapely():
        print(
            "[TrailPrint3D] Shapely loaded on retry (was unavailable at "
            "module-import time)."
        )
        return

    if _SHAPELY_IMPORT_ERROR is not None:
        raise ImportError(
            f"{_SHAPELY_ERR}\n(Underlying error: {_SHAPELY_IMPORT_ERROR})"
        ) from _SHAPELY_IMPORT_ERROR
    raise ImportError(_SHAPELY_ERR)


# ---------------------------------------------------------------------------
# Core geometry helpers
# ---------------------------------------------------------------------------


def validate(geom, method="structure", keep_collapsed=False, force=False):
    """Repair a Shapely geometry using make_valid(method='structure').

    'structure' treats outer rings as area and inner rings as holes, merges
    overlapping shells and subtracts holes — the correct behaviour for OSM
    polygons.  Returns the repaired geometry (Polygon / MultiPolygon /
    GeometryCollection).  Empty or None geometries pass through unchanged.

    force=True bypasses the is_valid check — required for figure-8 self-touching
    rings that Shapely considers valid but earcut triangulates incorrectly.
    """
    _require_shapely()
    if geom is None or geom.is_empty:
        return geom
    if geom.is_valid and not force:
        return geom
    if _SHAPELY_MAJOR >= 2:
        return _make_valid_v2(geom, method=method, keep_collapsed=keep_collapsed)
    return _make_valid_compat(geom)


def iter_polygons(geom, min_area=0.0):
    """Yield every non-empty Polygon from *geom*, skipping sub-min-area parts.

    Handles Polygon, MultiPolygon, and GeometryCollection transparently.
    """
    _require_shapely()
    if geom is None or geom.is_empty:
        return
    if isinstance(geom, Polygon):
        if not geom.is_empty and geom.area >= min_area:
            yield geom
    elif isinstance(geom, (MultiPolygon, GeometryCollection)):
        for part in geom.geoms:
            yield from iter_polygons(part, min_area)


def drop_small_holes(geom, min_area):
    """Remove interior holes (islands) below *min_area* from a Polygon or
    MultiPolygon, folding those small land pockets back into the surrounding
    area instead of leaving them punched out.

    This is the polygon-with-holes equivalent of the min_area island-skip
    logic in terrain.py::_polygonize_ocean_faces (which works from
    face-classification instead, since it builds the ocean polygon up from
    raw coastline chains rather than receiving a finished polygon) -- used
    for ocean sources that hand back a ready-made water polygon with real
    holes for islands, e.g. the global water-polygon dataset.
    """
    _require_shapely()
    if geom is None or geom.is_empty or min_area <= 0:
        return geom

    def _fix_poly(p):
        if not p.interiors:
            return p
        kept = [r for r in p.interiors if Polygon(r).area >= min_area]
        if len(kept) == len(p.interiors):
            return p
        return Polygon(p.exterior, kept)

    if isinstance(geom, Polygon):
        return _fix_poly(geom)
    if isinstance(geom, (MultiPolygon, GeometryCollection)):
        return MultiPolygon(
            [_fix_poly(p) for p in geom.geoms if isinstance(p, Polygon)]
        )
    return geom


def union(geoms):
    """Return the unary union of *geoms* (list / iterable of Shapely geometries).

    Filters out None and empty entries first.  Returns None if the input is
    empty or every geometry is None / empty.
    """
    _require_shapely()
    valid = [g for g in geoms if g is not None and not g.is_empty]
    if not valid:
        return None
    result = union_all(valid)
    return result if not result.is_empty else None


def subtract(geom, neg_geom):
    """Return geom.difference(neg_geom).

    Returns *geom* unchanged if *neg_geom* is None / empty.
    """
    _require_shapely()
    if neg_geom is None or neg_geom.is_empty:
        return geom
    if geom is None or geom.is_empty:
        return geom
    return geom.difference(neg_geom)


def _smooth_polygon_taubin_pinned(geom, is_pinned, **taubin_kwargs):
    """Core Taubin smoothing pass shared by smooth_polygon_taubin() and
    smooth_polygon_taubin_bbox_pinned() -- smooths a Shapely Polygon or
    MultiPolygon, restoring any vertex for which is_pinned(x, y) is True back
    to its exact original coordinate after smoothing.

    is_pinned -- callable(x, y) -> bool, so callers can pin against either a
    shared outline boundary or a tile bbox edge.
    taubin_kwargs -- passed straight through to shapelysmooth.taubin_smooth
    (factor, mu, steps).
    """
    from shapelysmooth import taubin_smooth

    _require_shapely()

    def _smooth_ring(coords):
        # Keep the ring CLOSED (first == last) when handing it to
        # taubin_smooth -- that's how it distinguishes a closed ring from
        # an open polyline. Stripping the closing point here would make it
        # treat the seam as two endpoints instead of interior nodes.
        pts = list(coords)
        if len(pts) < 4:  # 3 real points + closing duplicate
            return pts

        pinned_mask = [is_pinned(px, py) for px, py in pts]
        smoothed = taubin_smooth(pts, **taubin_kwargs)

        result = [pts[i] if pinned_mask[i] else smoothed[i] for i in range(len(pts))]
        result[-1] = result[0]  # guard against float drift breaking closure
        return result

    def _smooth_polygon(poly):
        ext = _smooth_ring(list(poly.exterior.coords))
        holes = [_smooth_ring(list(ir.coords)) for ir in poly.interiors]
        try:
            result = Polygon(ext, holes)
            return validate(result) if not result.is_valid else result
        except Exception as _exc:  # noqa: BLE001
            print(
                f"[TrailPrint3D] geometry2d: smoothing produced an invalid polygon, keeping original: {_exc!r}"
            )
            return poly

    if geom is None or geom.is_empty:
        return geom
    if geom.geom_type == "Polygon":
        return _smooth_polygon(geom)
    if geom.geom_type in ("MultiPolygon", "GeometryCollection"):
        flat = []
        for part in iter_polygons(geom):
            flat.extend(iter_polygons(_smooth_polygon(part)))
        if not flat:
            return geom
        return flat[0] if len(flat) == 1 else MultiPolygon(flat)
    return geom


def smooth_polygon_taubin(
    gen: GenerationContext, geom, pin_tolerance=1e-3, **taubin_kwargs
):
    """Smooth a Shapely Polygon or MultiPolygon using Taubin smoothing
    (shapelysmooth), preserving vertex count/order so outline-touching
    vertices can be pinned back to their exact original position afterward.

    taubin_kwargs -- passed straight through to shapelysmooth.taubin_smooth
    (factor, mu, steps). Omitted here to use the library's own defaults;
    tune once you've seen real output.

    Pins any vertex lying on gen.runtime.mapOutline's boundary (within
    pin_tolerance) so touching elements stay stitched together at that edge.
    """
    _require_shapely()
    outline = gen.runtime.mapOutline
    # mapOutline is stored in the map object's LOCAL space (pre-transform); geom
    # is in absolute Mercator space, so translate to match before pin-checking.
    if outline is not None and gen.runtime.mapObject is not None:
        from shapely.affinity import translate as _shp_translate

        outline = _shp_translate(
            outline,
            xoff=gen.runtime.mapObject.location.x,
            yoff=gen.runtime.mapObject.location.y,
        )
    pin_geom = (
        outline.boundary
        if outline is not None and hasattr(outline, "boundary")
        else outline
    )

    def _is_pinned(px, py):
        if pin_geom is None:
            return False
        return pin_geom.distance(Point(px, py)) <= pin_tolerance

    return _smooth_polygon_taubin_pinned(geom, _is_pinned, **taubin_kwargs)


def smooth_polygon_taubin_bbox_pinned(geom, bbox, pin_tolerance=1e-3, **taubin_kwargs):
    """Smooth a Shapely Polygon or MultiPolygon using Taubin smoothing,
    pinning any vertex lying on the edges of *bbox* (min_x, min_y, max_x,
    max_y) back to its exact original coordinate.

    Intended for per-tile geometry (e.g. the ocean mesh) whose boundary must
    stay exactly on the tile's bbox so adjacent tiles keep stitching
    together seamlessly -- only interior vertices actually move.
    """
    _require_shapely()
    min_x, min_y, max_x, max_y = bbox

    def _is_pinned(px, py):
        return (
            abs(px - min_x) <= pin_tolerance
            or abs(px - max_x) <= pin_tolerance
            or abs(py - min_y) <= pin_tolerance
            or abs(py - max_y) <= pin_tolerance
        )

    return _smooth_polygon_taubin_pinned(geom, _is_pinned, **taubin_kwargs)


def line_to_ribbon(coords_xy, half_width, cap_style="round", join_style="round"):
    """Buffer a polyline into a flat ribbon polygon.

    coords_xy  -- iterable of (x, y) tuples in Blender-Mercator space
    half_width -- half the desired ribbon width in Blender units

    Returns a validated Shapely Polygon / MultiPolygon, or None if the
    line is degenerate (fewer than 2 points or zero-length).
    """
    _require_shapely()
    pts = list(coords_xy)
    if len(pts) < 2:
        return None
    line = LineString(pts)
    if line.is_empty:
        return None
    buf = line.buffer(half_width, cap_style=cap_style, join_style=join_style)
    if buf.is_empty:
        return None
    return validate(buf)


def polylines_to_ribbon(
    coords_list,
    half_width,
    cap_style="round",
    join_style="round",
    quad_segs=2,
    simplify_tol=None,
    precision=None,
):
    """Buffer many polylines into one merged ribbon polygon.

    coords_list -- iterable of polylines, each an iterable of (x, y) tuples
    half_width  -- half the desired ribbon width in Blender units

    The lines are collected into a single MultiLineString and buffered ONCE.
    Buffering a MultiLineString already merges overlapping road areas into a
    single clean polygon, so there is no need to node/union the centrelines
    first -- a buffer is a Minkowski dilation of the underlying point set, and
    `union_all(lines).buffer(w)` yields the identical region as
    `MultiLineString(lines).buffer(w)`.  Skipping that union avoids noding the
    entire network (computing every intersection), which for a dense city of
    ~200k nodes is by far the most expensive step.

    simplify_tol (recommended) runs Douglas-Peucker on the lines BEFORE the
    buffer, cutting the vertex count fed into the buffer and every downstream
    stage.

    precision (strongly recommended for dense networks) snaps every coordinate
    to a grid of this size via GEOS set_precision BEFORE buffering, and snaps
    the result AFTER. Snapping collapses the thousands of near-coincident
    vertices a city street grid produces, which is what makes the buffer's
    internal cascaded union blow up (113s for ~65k segments unsnapped). It also
    yields a simpler output polygon (faster earcut / boolean downstream) and
    welds coincident points so the prisms are more likely watertight-manifold.

    Returns a validated Polygon / MultiPolygon, or None on degenerate input.
    """
    _require_shapely()
    lines = []
    for coords in coords_list:
        pts = list(coords)
        if len(pts) < 2:
            continue
        ln = LineString(pts)
        if not ln.is_empty and ln.length > 0:
            lines.append(ln)
    if not lines:
        return None
    merged = lines[0] if len(lines) == 1 else MultiLineString(lines)
    if simplify_tol:
        merged = merged.simplify(simplify_tol)
    if precision:
        # Snap input vertices to a grid; this is the single biggest buffer
        # speed-up for dense networks (collapses near-coincident street nodes).
        merged = _shapely.set_precision(merged, precision)
    buf = merged.buffer(
        half_width, quad_segs=quad_segs, cap_style=cap_style, join_style=join_style
    )
    if buf.is_empty:
        return None
    if precision:
        buf = _shapely.set_precision(buf, precision)
        if buf.is_empty:
            return None
    return validate(buf)


def map_footprint_polygon(obj):
    """Return the 2D (x, y) outline of a mesh object as a Shapely Polygon.

    Collects the mesh's boundary edges (edges with a single linked face),
    polygonizes them, and unions every large-enough resulting polygon into
    the map outline (small artifact loops -- e.g. a magnet-hole cutout --
    are filtered out via an area threshold relative to the biggest piece
    found).  Works for any map shape (hexagon, circle, square, ...), AND for
    a mesh made of several disjoint islands -- e.g. a pre-cut multi-tile
    puzzle blank, where each tile is its own separate boundary loop and ALL
    of them are real map area, not just the single largest one.  Used to
    clip OSM elements (buildings / roads) to the map shape in 2D, which is
    far more robust than a 3D boolean against non-manifold element meshes.

    Returns a validated Polygon/MultiPolygon, or None if no closed boundary
    can be built.
    """
    _require_shapely()
    if obj is None or obj.type != "MESH":
        return None
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.normal_update()
    mw = obj.matrix_world

    def _seg(e):
        v0 = mw @ e.verts[0].co
        v1 = mw @ e.verts[1].co
        if (v0.x, v0.y) != (v1.x, v1.y):
            return LineString([(v0.x, v0.y), (v1.x, v1.y)])
        return None

    # First try genuine boundary edges -- correct for a flat / open map surface.
    segs = []
    for e in bm.edges:
        if len(e.link_faces) == 1:
            s = _seg(e)
            if s is not None:
                segs.append(s)

    # A closed solid map (base + side walls + top) has NO single-face edges, so
    # the above finds nothing. Fall back to the top-surface silhouette: an edge
    # is on the outline when exactly one of its linked faces is a perimeter
    # wall (its other neighbour is real terrain). This recovers the map
    # outline for a watertight terrain block.
    #
    # A face's normal alone can't tell a perimeter wall from steep terrain --
    # both can be near-vertical. What's unique to the extruded perimeter wall
    # is that it's the only geometry spanning all the way down to the flat
    # base plate (the solidify step always drops the base *below* the lowest
    # terrain point, by minThickness); no terrain face, however steep, ever
    # reaches that low. So classify by touching the base, not by normal --
    # normal-based classification mistook steep cliffs/ridges for walls and
    # carved holes out of the map outline there, silently dropping buildings
    # and roads on steep terrain.
    if not segs:
        world_zs = [(mw @ v.co).z for v in bm.verts]
        z_min_mesh = min(world_zs) if world_zs else 0.0
        z_eps = max(1e-4, 1e-4 * (max(world_zs) - z_min_mesh)) if world_zs else 1e-4

        def _is_wall_face(f):
            if abs(f.normal.normalized().z) >= 0.1:
                return False  # not near-vertical -> definitely terrain
            return min((mw @ v.co).z for v in f.verts) <= z_min_mesh + z_eps

        for e in bm.edges:
            wall_count = sum(1 for f in e.link_faces if _is_wall_face(f))
            if wall_count == 1:
                s = _seg(e)
                if s is not None:
                    segs.append(s)

    bm.free()
    if segs:
        merged = union_all(segs)
        polys = list(polygonize(merged))
        if polys:
            # Union every polygon big enough to be real map area, not just the
            # single biggest one -- a mesh with several disjoint islands (e.g. a
            # pre-cut multi-tile puzzle blank) polygonizes into one boundary loop
            # per island, and every one of them is genuine map area that OSM
            # elements (roads/buildings) must still be clipped to. Small artifact
            # loops (magnet-hole cutouts, etc.) are filtered relative to the
            # largest piece found.
            max_area = max(p.area for p in polys)
            keep = [p for p in polys if p.area >= max_area * 0.01]
            footprint = validate(union_all(keep))
            if footprint is not None and not footprint.is_empty:
                return footprint

    # Boundary-edge tracing found nothing (or an open, non-polygonizable
    # chain): a sharp/thin outline feature (e.g. the heart shape's cusp) can
    # produce sliver wall faces whose near-degenerate normal fails the
    # near-vertical wall test, leaving a gap in the traced ring. Fall back to
    # projecting the solid's downward-facing faces (the base plate), which
    # covers the full footprint regardless of how the walls triangulated.
    footprint = footprint_with_holes(obj, down_only=True)
    if footprint is not None and not footprint.is_empty:
        return footprint
    # A flat, single-layer map (no base/walls) has no downward faces at all --
    # project every face instead as a last resort.
    return footprint_with_holes(obj, down_only=False)


def footprint_with_holes(
    obj, simplify_tol=None, down_only=False, method="structure", keep_collapsed=False
):
    """Return the true 2D footprint of a mesh as a Shapely Polygon/MultiPolygon.

    Projects faces to the (x, y) plane and unions them.  Because the union is
    built from the faces that actually exist, any region the mesh does not
    cover -- e.g. the land island inside a river loop -- remains an interior
    ring (hole).  This is robust to bumpy / terrain-intersected bottoms where a
    boundary-edge polygonize would miss or jaggedly break the hole rings.

    down_only -- when True only downward-facing faces (normal.z < -0.3) are
    projected.  A closed solid's bottom shell alone already describes the full
    footprint, so this roughly halves the face count fed to the union.

    Returns a validated Polygon / MultiPolygon (holes preserved), or None.
    """
    _require_shapely()
    if obj is None or obj.type != "MESH":
        return None
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    if down_only:
        bm.normal_update()
    mw = obj.matrix_world
    polys = []
    for f in bm.faces:
        if down_only and f.normal.z >= -0.3:
            continue
        ring = [(mw @ v.co) for v in f.verts]
        ring = [(c.x, c.y) for c in ring]
        if len(ring) < 3:
            continue
        try:
            p = Polygon(ring)
        except Exception as _exc:  # noqa: BLE001
            print(f"[TrailPrint3D] geometry2d: skipping degenerate face ring: {_exc!r}")
            continue
        if not p.is_valid:
            p = validate(p, method=method, keep_collapsed=keep_collapsed)
        if p is not None and not p.is_empty and p.area > 0:
            polys.append(p)
    bm.free()
    if not polys:
        return None
    merged = union_all(polys)
    if merged.is_empty:
        return None
    if simplify_tol:
        merged = merged.simplify(simplify_tol)
        if merged.is_empty:
            return None
    return validate(merged)


def xy_ring_to_polygon(coords_xy):
    """Build a validated Shapely Polygon from a ring of (x, y) tuples.

    Returns a validated Polygon / MultiPolygon, or None if the ring is
    degenerate (fewer than 3 points, or results in an empty geometry).
    """
    _require_shapely()
    pts = list(coords_xy)
    if len(pts) < 3:
        return None
    try:
        poly = Polygon(pts)
    except Exception as _exc:  # noqa: BLE001
        print(f"[TrailPrint3D] geometry2d: failed to build Polygon from ring: {_exc!r}")
        return None
    if poly.is_empty:
        return None
    return validate(poly)


# ---------------------------------------------------------------------------
# Blender mesh creation
# ---------------------------------------------------------------------------


def _ring_coords_3d(ring):
    """Convert a Shapely LinearRing to a list of (x, y, 0.0) Blender coords.

    Shapely closes rings (last == first); the closing duplicate is dropped.
    """
    coords = list(ring.coords)
    if len(coords) > 1 and coords[0] == coords[-1]:
        coords = coords[:-1]
    return [(x, y, 0.0) for x, y in coords]


def _cdt_triangulate(polygon, exterior_xy, holes_xy):
    """Triangulate using shapely.constrained_delaunay_triangles (GEOS 3.11+).

    Ring boundary vertices are pre-registered in the shared vertex array in
    their original order so _extrude_flat_polygon's wall quads keep working.
    CDT Steiner points (if any) are appended after the ring vertices.

    Returns (verts2d, tris, ring_idx_lists), or None.
    """
    _require_shapely()
    try:
        tris_geom = _shapely.constrained_delaunay_triangles(polygon)
    except Exception:  # noqa: BLE001 — AttributeError on old GEOS, others on degenerate input
        return None
    if tris_geom is None or tris_geom.is_empty:
        return None

    verts2d = []
    vert_map = {}

    def _get(x, y):
        k = (round(x, 8), round(y, 8))
        if k not in vert_map:
            vert_map[k] = len(verts2d)
            verts2d.append((x, y))
        return vert_map[k]

    # Register ring vertices first, in order, and record the actual indices
    # (dedup may collapse coincident coords, so we can't assume linear offsets).
    ring_idx_lists = []
    ext_idxs = [_get(x, y) for x, y in exterior_xy]
    ring_idx_lists.append(ext_idxs)
    for hole in holes_xy:
        ring_idx_lists.append([_get(x, y) for x, y in hole])

    tris = []
    for tri in tris_geom.geoms:
        if not isinstance(tri, Polygon):
            continue
        coords = list(tri.exterior.coords)[:-1]
        if len(coords) != 3:
            continue
        ia = _get(coords[0][0], coords[0][1])
        ib = _get(coords[1][0], coords[1][1])
        ic = _get(coords[2][0], coords[2][1])
        if ia == ib or ib == ic or ia == ic:
            continue
        tris.append((ic, ib, ia))  # reverse CW→CCW to match earcut's convention

    return (verts2d, tris, ring_idx_lists) if tris else None


def polygon_to_mesh(name, polygon):
    """Convert a Shapely Polygon to a flat Blender mesh object at z=0.

    Returns the new bpy.types.Object linked into the active collection, or
    None if the polygon is empty / degenerate.
    """
    _require_shapely()

    if polygon is None or polygon.is_empty or not isinstance(polygon, Polygon):
        return None

    outer = _ring_coords_3d(polygon.exterior)
    if len(outer) < 3:
        return None

    holes = [_ring_coords_3d(ir) for ir in polygon.interiors]
    holes = [h for h in holes if len(h) >= 3]

    if holes:
        ext_xy = [(x, y) for x, y, _ in outer]
        holes_xy = [[(x, y) for x, y, _ in h] for h in holes]
        ec = _cdt_triangulate(polygon, ext_xy, holes_xy)
        if ec is None:
            return None
        verts2d, tris, _ring_idx_lists = ec
        coords = [(x, y, 0.0) for x, y in verts2d]
        mesh = bpy.data.meshes.new(name)
        tobj = bpy.data.objects.new(name, mesh)
        bpy.context.collection.objects.link(tobj)
        mesh.from_pydata(coords, [], tris)
        mesh.update()
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.dissolve_degenerate(bm, dist=1e-6, edges=bm.edges[:])
        bm.to_mesh(mesh)
        bm.free()
        mesh.update()
    else:
        ext_xy = [(x, y) for x, y, _ in outer]
        ec = _cdt_triangulate(polygon, ext_xy, [])
        mesh = bpy.data.meshes.new(name)
        tobj = bpy.data.objects.new(name, mesh)
        bpy.context.collection.objects.link(tobj)
        if ec is None:
            return None
        verts2d, tris, _ = ec
        coords = [(x, y, 0.0) for x, y in verts2d]
        mesh.from_pydata(coords, [], tris)
        mesh.update()
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.dissolve_degenerate(bm, dist=1e-6, edges=bm.edges[:])
        bm.to_mesh(mesh)
        bm.free()
        mesh.update()

    return tobj


# ---------------------------------------------------------------------------
# Debug visualization
# ---------------------------------------------------------------------------


def _iter_all_rings(geom):
    """Yield every ring as a list of (x, y) tuples from any Shapely geometry.

    For polygons yields the exterior first, then each interior (hole).  For
    lines yields the coordinate sequence.  Recurses into Multi* and
    GeometryCollection.  This is the *exact* coordinate data Shapely holds —
    no cleanup, no Z — so the wireframe reveals self-intersections, gaps and
    sliver rings that a filled mesh would hide.
    """
    if geom is None or geom.is_empty:
        return
    if isinstance(geom, Polygon):
        yield list(geom.exterior.coords)
        for interior in geom.interiors:
            yield list(interior.coords)
    elif isinstance(geom, (MultiPolygon, GeometryCollection)):
        for part in geom.geoms:
            yield from _iter_all_rings(part)
    elif isinstance(geom, LineString):
        yield list(geom.coords)


def debug_collection(name):
    """Get or create a named collection under the scene root (debug only)."""
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(coll)
    return coll


def debug_dump(name, geom_or_list, collection_name="TP3D_Debug", z=0.0):
    """DEBUG: build a wireframe Blender object from the exact rings Shapely holds.

    *geom_or_list* may be a single Shapely geometry or a list/tuple of them
    (e.g. the raw pos_geoms list before any union).  Every exterior and hole
    ring is emitted as a closed edge loop at height *z* — no faces — so the raw
    topology is fully visible.  Returns the object, or None if there is nothing
    to draw or debug mode is off.

    Use distinct *z* per pipeline stage to stack stages vertically for easy
    visual separation in the viewport.
    """
    if not bpy.app.debug:
        return None
    _require_shapely()
    geoms = geom_or_list if isinstance(geom_or_list, (list, tuple)) else [geom_or_list]

    verts = []
    edges = []
    for geom in geoms:
        for ring in _iter_all_rings(geom):
            if len(ring) < 2:
                continue
            start = len(verts)
            verts.extend((float(x), float(y), float(z)) for x, y in ring)
            for i in range(len(ring) - 1):
                edges.append((start + i, start + i + 1))

    if not verts:
        return None

    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    mesh.from_pydata(verts, edges, [])
    mesh.update()
    debug_collection(collection_name).objects.link(obj)
    return obj


def debug_dump_polygon_arrays(
    name, poly_arrays, collection_name="TP3D_Debug", z=0.0, color=(1.0, 0.0, 0.0, 1.0)
):
    """DEBUG: Dump polygon arrays (numpy arrays or list of (x,y) tuples) as wireframes.

    This is specifically for the format returned by _buffer_tiers_to_polygons().
    poly_arrays: list of numpy arrays or list of (x,y) coordinate lists
    z: height to place the debug geometry
    color: RGBA color for the wireframe (default red)
    """
    if not bpy.app.debug:
        return None
    _require_shapely()

    verts = []
    edges = []

    for poly_arr in poly_arrays:
        # Handle both numpy arrays and list/tuple formats
        if hasattr(poly_arr, "shape"):  # numpy array
            coords = [
                (float(poly_arr[i][0]), float(poly_arr[i][1]))
                for i in range(len(poly_arr))
            ]
        else:  # list/tuple
            coords = [(float(x), float(y)) for x, y in poly_arr]

        if len(coords) < 3:
            continue

        start = len(verts)
        verts.extend((x, y, z) for x, y in coords)
        # Close the polygon (connect last to first)
        for i in range(len(coords)):
            edges.append((start + i, start + ((i + 1) % len(coords))))

    if not verts:
        return None

    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    mesh.from_pydata(verts, edges, [])
    mesh.update()

    # Add a material with the specified color
    mat = bpy.data.materials.new(name=f"{name}_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    # Clear default nodes
    for node in nodes:
        nodes.remove(node)
    # Add emission node for visibility
    emission = nodes.new(type="ShaderNodeEmission")
    emission.inputs["Color"].default_value = color
    emission.inputs["Strength"].default_value = 1.0
    output = nodes.new(type="ShaderNodeOutputMaterial")
    mat.node_tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    obj.data.materials.append(mat)

    debug_collection(collection_name).objects.link(obj)
    return obj


def debug_dump_polylines(
    name, polylines, collection_name="TP3D_Debug", z=0.0, color=(0.0, 1.0, 0.0, 1.0)
):
    """DEBUG: Dump polylines as wireframe lines.

    polylines: list of polyline lists, each polyline is a list of (x,y) coordinates
    z: height to place the debug geometry
    color: RGBA color for the wireframe (default green)
    """
    if not bpy.app.debug:
        return None
    _require_shapely()

    verts = []
    edges = []

    for polyline in polylines:
        if len(polyline) < 2:
            continue

        start = len(verts)
        # Convert to (x, y, z)
        verts.extend((float(x), float(y), z) for x, y in polyline)
        # Connect consecutive points
        for i in range(len(polyline) - 1):
            edges.append((start + i, start + i + 1))

    if not verts:
        return None

    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    mesh.from_pydata(verts, edges, [])
    mesh.update()

    # Add a material with the specified color
    mat = bpy.data.materials.new(name=f"{name}_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    for node in nodes:
        nodes.remove(node)
    emission = nodes.new(type="ShaderNodeEmission")
    emission.inputs["Color"].default_value = color
    emission.inputs["Strength"].default_value = 1.0
    output = nodes.new(type="ShaderNodeOutputMaterial")
    mat.node_tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    obj.data.materials.append(mat)

    debug_collection(collection_name).objects.link(obj)
    return obj


def debug_dump_mesh_footprint(
    name, obj, collection_name="TP3D_Debug", z=0.0, color=(0.0, 0.0, 1.0, 1.0)
):
    """DEBUG: Dump a Blender mesh's footprint as a wireframe polygon.

    obj: Blender mesh object
    z: height to place the debug geometry
    color: RGBA color for the wireframe (default blue)
    """
    if not bpy.app.debug or obj is None:
        return None
    _require_shapely()

    # Get the footprint as a Shapely polygon
    footprint = footprint_with_holes(obj, simplify_tol=0.1)
    if footprint is None or footprint.is_empty:
        return None

    # Convert to wireframe
    verts = []
    edges = []

    def add_ring(ring_coords):
        coords = list(ring_coords)
        if len(coords) < 3:
            return
        start = len(verts)
        verts.extend((float(x), float(y), z) for x, y in coords)
        for i in range(len(coords)):
            edges.append((start + i, start + ((i + 1) % len(coords))))

    if isinstance(footprint, Polygon):
        add_ring(footprint.exterior.coords)
        for interior in footprint.interiors:
            add_ring(interior.coords)
    elif isinstance(footprint, (MultiPolygon, GeometryCollection)):
        for part in footprint.geoms:
            if isinstance(part, Polygon):
                add_ring(part.exterior.coords)
                for interior in part.interiors:
                    add_ring(interior.coords)

    if not verts:
        return None

    mesh = bpy.data.meshes.new(name)
    debug_obj = bpy.data.objects.new(name, mesh)
    mesh.from_pydata(verts, edges, [])
    mesh.update()

    # Add a material with the specified color
    mat = bpy.data.materials.new(name=f"{name}_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    for node in nodes:
        nodes.remove(node)
    emission = nodes.new(type="ShaderNodeEmission")
    emission.inputs["Color"].default_value = color
    emission.inputs["Strength"].default_value = 1.0
    output = nodes.new(type="ShaderNodeOutputMaterial")
    mat.node_tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    debug_obj.data.materials.append(mat)

    debug_collection(collection_name).objects.link(debug_obj)
    return debug_obj


# ---------------------------------------------------------------------------
# Grid generation + grid/polygon clipping
#
# Shared by roads (clipping the terrain's own triangulated grid to a road
# footprint) and by primitives.build_mesh_from_polygon (clipping a flat
# lattice to any shape/GeoJSON/combined polygon) -- one clip implementation
# instead of one per caller.
# ---------------------------------------------------------------------------

from shapely import (
    area as _sh_area,
)
from shapely import (
    contains as _sh_contains,
)
from shapely import (
    get_num_coordinates as _sh_get_num_coordinates,
)
from shapely import (
    get_num_geometries as _sh_get_num_geometries,
)
from shapely import (
    intersects as _sh_intersects,
)
from shapely import (
    is_valid as _sh_is_valid,
)
from shapely import (
    polygons as _sh_polygons,
)
from shapely import (
    prepare as _sh_prepare,
)


def build_triangular_lattice(
    bounds: tuple[float, float, float, float],
    cell_size: float,
    max_cells: int = 10_000_000,
):
    """Build a regular triangular lattice covering *bounds* (minx, miny, maxx,
    maxy), each grid cell split into 2 triangles, flat at z=0.

    Returns an (T, 3, 3) ndarray -- T triangles, 3 verts each, 3 coords each
    -- the same per-triangle-independent layout _triangulated_terrain_faces'
    plain list produces, just as an array instead of nested tuples, so it
    plugs directly into clip_triangles_to_polygon with no adapter needed
    (its first step is np.asarray(triangles, ...), a free no-op on an array
    already in this shape/dtype).

    A tiny bit of overscan (half a cell past each edge) keeps boundary
    triangles fully covering the polygon edge so the clip's slow/CDT path
    isn't left with a sliver gap at the boundary.

    Built with numpy (not a Python nested loop) -- at high num_subdivisions
    callers can request millions of cells, and a pure-Python loop building
    that many tuples is itself minutes slow before clip_triangles_to_polygon
    even starts. max_cells is a hard backstop on top of that: cell_size gets
    scaled up (coarser) just enough to land at or under the budget, since
    callers (see primitives.create_*) derive cell_size from num_subdivisions
    with exponential growth, and that field allows values well above its
    slider (soft_max=10, max=50) that would otherwise generate an unbounded
    lattice with no warning.
    """
    minx, miny, maxx, maxy = bounds
    if cell_size <= 0 or maxx <= minx or maxy <= miny:
        return []

    pad = cell_size * 0.5
    minx, miny = minx - pad, miny - pad
    maxx, maxy = maxx + pad, maxy + pad

    nx = max(1, math.ceil((maxx - minx) / cell_size))
    ny = max(1, math.ceil((maxy - miny) / cell_size))

    if nx * ny > max_cells:
        scale = math.sqrt((nx * ny) / max_cells)
        cell_size *= scale
        nx = max(1, math.ceil((maxx - minx) / cell_size))
        ny = max(1, math.ceil((maxy - miny) / cell_size))
        print(
            f"[TP3D lattice] requested lattice exceeded {max_cells} cells -- "
            f"clamped cell_size to {cell_size:.4f} ({nx}x{ny} cells) to keep "
            "generation tractable"
        )

    xs = minx + np.arange(nx + 1) * cell_size  # (nx+1,)
    ys = miny + np.arange(ny + 1) * cell_size  # (ny+1,)

    # Corners of every cell as 4 same-shaped (ny, nx) arrays -- a uniform
    # lattice, so no per-vertex elevation lookup needed (unlike terrain).
    x0 = np.tile(xs[:-1], (ny, 1))
    x1 = np.tile(xs[1:], (ny, 1))
    y0 = np.tile(ys[:-1].reshape(-1, 1), (1, nx))
    y1 = np.tile(ys[1:].reshape(-1, 1), (1, nx))

    z = np.zeros_like(x0)
    p00 = np.stack([x0, y0, z], axis=-1)
    p10 = np.stack([x1, y0, z], axis=-1)
    p01 = np.stack([x0, y1, z], axis=-1)
    p11 = np.stack([x1, y1, z], axis=-1)

    tri_a = np.stack([p00, p10, p11], axis=-2)  # (ny, nx, 3, 3)
    tri_b = np.stack([p00, p11, p01], axis=-2)  # (ny, nx, 3, 3)
    # Returned as an (T, 3, 3) ndarray, NOT converted to nested Python
    # tuples -- clip_triangles_to_polygon's first step is np.asarray(...)
    # anyway, which is a free no-op passthrough on an array already in this
    # shape/dtype, whereas building nested tuples here only to immediately
    # convert them straight back was pure overhead (~3s at 500k triangles,
    # for zero benefit -- every other consumer only ever indexes tri[i][j],
    # which an ndarray supports identically to a tuple).
    return np.concatenate([tri_a.reshape(-1, 3, 3), tri_b.reshape(-1, 3, 3)], axis=0)


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


def clip_triangles_to_polygon(
    triangles: list,
    polygon,
    z_offset: float = 0.0,
) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]]]:
    """Clip a flat triangle list (see build_triangular_lattice /
    osm.roads._triangulated_terrain_faces) to a 2-D polygon (holes included),
    returning a deduped indexed mesh: (verts, tris).

    Relocated from osm.roads._clip_terrain_grid_to_polygon -- unchanged in
    behavior. Was terrain-specific in name only; the triangle input already
    carries its own Z per vertex (interpolated via barycentric coords for
    boundary-straddling triangles), which for a flat z=0 lattice just always
    resolves to 0 -- z_offset works identically as a uniform height offset
    for both use cases.
    """
    import shapely

    if polygon is None or polygon.is_empty:
        return [], []

    _sh_prepare(polygon)  # cached prepared geometry, used automatically below

    tri_arr = np.asarray(triangles, dtype=np.float64)  # (T, 3, 3)
    if tri_arr.size == 0:
        return [], []

    px0, py0, px1, py1 = polygon.bounds
    xs, ys = tri_arr[:, :, 0], tri_arr[:, :, 1]
    bbox_mask = (
        (xs.max(axis=1) >= px0)
        & (xs.min(axis=1) <= px1)
        & (ys.max(axis=1) >= py0)
        & (ys.min(axis=1) <= py1)
    )
    cand_idx = np.nonzero(bbox_mask)[0]
    if cand_idx.size == 0:
        return [], []

    ring = tri_arr[cand_idx][:, :, :2]  # (C, 3, 2)
    ring_closed = np.concatenate([ring, ring[:, :1, :]], axis=1)  # (C, 4, 2)
    tri_polys_arr = _sh_polygons(ring_closed)

    valid_mask = _sh_is_valid(tri_polys_arr) & (_sh_area(tri_polys_arr) > 1e-12)
    cand_idx = cand_idx[valid_mask]
    tri_polys_arr = tri_polys_arr[valid_mask]
    if cand_idx.size == 0:
        return [], []

    contains_mask = _sh_contains(polygon, tri_polys_arr)
    intersects_mask = _sh_intersects(polygon, tri_polys_arr) & ~contains_mask
    n_poly_coords = _sh_get_num_coordinates(polygon)
    n_poly_parts = _sh_get_num_geometries(polygon)
    print(
        f"[TP3D clip] clip diag: candidates={len(cand_idx)} contains={int(contains_mask.sum())} "
        f"intersects_only={int(intersects_mask.sum())} polygon_coords={n_poly_coords} polygon_parts={n_poly_parts}"
    )
    _fast_t0 = time.time()
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

    # Fast path: triangles fully inside -- no per-triangle Shapely calls needed
    fast_idx = cand_idx[contains_mask]
    if fast_idx.size:
        fast_tris = tri_arr[fast_idx].copy()  # (F, 3, 3)
        fast_tris[:, :, 2] += z_offset
        rounded = np.round(fast_tris.reshape(-1, 3), 5)  # (F*3, 3)
        uniq, inverse = np.unique(rounded, axis=0, return_inverse=True)
        uniq_list = [tuple(v) for v in uniq.tolist()]  # native python floats
        out_verts.extend(uniq_list)
        vert_cache.update((v, i) for i, v in enumerate(uniq_list))
        out_tris.extend(tuple(row) for row in inverse.reshape(-1, 3).tolist())
    print(f"[TP3D clip] clip diag: fast-path loop took {time.time() - _fast_t0:.2f}s")

    # Slow path: vectorized intersection across all boundary triangles at once
    slow_cand = cand_idx[intersects_mask]
    slow_polys = tri_polys_arr[intersects_mask]

    if slow_cand.size > 0:
        intersections = shapely.intersection(slow_polys, polygon)

        for i, inter in zip(slow_cand, intersections):
            if inter.is_empty:
                continue
            tri = triangles[i]
            for part in iter_polygons(inter):
                part = orient(part, sign=1.0)
                ext = list(part.exterior.coords)[:-1]
                if len(ext) < 3:
                    continue
                holes = [
                    list(r.coords)[:-1] for r in part.interiors if len(r.coords) >= 4
                ]
                ec = _cdt_triangulate(part, ext, holes)
                if ec is None:
                    continue
                verts2d_part, tris_part, _ = ec
                local_idx = []
                for vx, vy in verts2d_part:
                    vz = _bary_z(tri, vx, vy) + z_offset
                    local_idx.append(_get_vert(vx, vy, vz))
                for a, b, c in tris_part:
                    out_tris.append((local_idx[a], local_idx[b], local_idx[c]))

    print(
        f"[TP3D clip] clip diag: cdt-fallback loop took {time.time() - _fast_t0:.2f}s"
    )
    return out_verts, out_tris


def group_boundary_loops(edges):
    """Sort unsorted boundary edges into ordered vertex loops."""
    adj = {}
    for e in edges:
        for v in e.verts:
            adj.setdefault(v, []).append(e)

    visited_edges = set()
    loops = []

    for start_edge in edges:
        if start_edge in visited_edges:
            continue

        loop = []
        curr_edge = start_edge
        curr_v = curr_edge.verts[0]

        while curr_edge not in visited_edges:
            visited_edges.add(curr_edge)
            loop.append(curr_v)
            next_v = curr_edge.other_vert(curr_v)

            next_edge = None
            for e in adj.get(next_v, []):
                if e not in visited_edges:
                    next_edge = e
                    break

            curr_v = next_v
            if next_edge is None:
                break
            curr_edge = next_edge

        if len(loop) >= 3:
            loops.append(loop)

    return loops


def get_map_polygon(obj) -> Polygon | MultiPolygon | None:
    """Retrieve the original 2D Shapely polygon stored on a map object."""
    from shapely import wkt

    if obj and "map_polygon_wkt" in obj:
        return wkt.loads(obj["map_polygon_wkt"])
    return None
