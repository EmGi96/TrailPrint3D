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

import bpy      # type: ignore
import bmesh    # type: ignore
from mathutils import Vector  # type: ignore

from typing import Any

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
unary_union: Any = None
polygonize: Any = None

def _load_shapely():
    """Attempt to import Shapely and populate module-level globals.

    Returns True on success, False on ImportError (which is stored in
    ``_SHAPELY_IMPORT_ERROR`` for later reporting).
    """
    global _HAS_SHAPELY, _SHAPELY_MAJOR, _SHAPELY_IMPORT_ERROR, _shapely
    global Polygon, MultiPolygon, LineString, MultiLineString, GeometryCollection
    global Point, box, orient, prep
    global _make_valid_compat, _make_valid_v2, unary_union, polygonize
    try:
        import shapely as _shapely_mod
        from shapely.geometry import (
            Polygon as _P, MultiPolygon as _MP,
            LineString as _LS, MultiLineString as _MLS,
            GeometryCollection as _GC, Point as _Pt, box as _box,
        )
        from shapely.geometry.polygon import orient as _orient
        from shapely.prepared import prep as _prep
        from shapely.validation import make_valid as _mvc
        from shapely import make_valid as _mv2
        from shapely.ops import unary_union as _uu, polygonize as _pg

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
        unary_union = _uu
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

_np = None
_earcut = None
_HAS_EARCUT = False
try:
    import numpy as _np
    import mapbox_earcut as _earcut
    _HAS_EARCUT = True
except ImportError:
    pass

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

def validate(geom):
    """Repair a Shapely geometry using make_valid(method='structure').

    'structure' treats outer rings as area and inner rings as holes, merges
    overlapping shells and subtracts holes — the correct behaviour for OSM
    polygons.  Returns the repaired geometry (Polygon / MultiPolygon /
    GeometryCollection).  Empty or None geometries pass through unchanged.
    """
    _require_shapely()
    if geom is None or geom.is_empty:
        return geom
    if geom.is_valid:
        return geom
    if _SHAPELY_MAJOR >= 2:
        return _make_valid_v2(geom, method="structure", keep_collapsed=False)
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


def union(geoms):
    """Return the unary union of *geoms* (list / iterable of Shapely geometries).

    Filters out None and empty entries first.  Returns None if the input is
    empty or every geometry is None / empty.
    """
    _require_shapely()
    valid = [g for g in geoms if g is not None and not g.is_empty]
    if not valid:
        return None
    result = unary_union(valid)
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


def polylines_to_ribbon(coords_list, half_width, cap_style="round",
                        join_style="round", quad_segs=2, simplify_tol=None,
                        precision=None):
    """Buffer many polylines into one merged ribbon polygon.

    coords_list -- iterable of polylines, each an iterable of (x, y) tuples
    half_width  -- half the desired ribbon width in Blender units

    The lines are collected into a single MultiLineString and buffered ONCE.
    Buffering a MultiLineString already merges overlapping road areas into a
    single clean polygon, so there is no need to node/union the centrelines
    first -- a buffer is a Minkowski dilation of the underlying point set, and
    `unary_union(lines).buffer(w)` yields the identical region as
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
    buf = merged.buffer(half_width, quad_segs=quad_segs,
                        cap_style=cap_style, join_style=join_style)
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
    polygonizes them, and returns the largest resulting polygon -- the map
    outline.  Works for any map shape (hexagon, circle, square, ...).  Used to
    clip OSM elements (buildings / roads) to the map shape in 2D, which is far
    more robust than a 3D boolean against non-manifold element meshes.

    Returns a validated Polygon, or None if no closed boundary can be built.
    """
    _require_shapely()
    if obj is None or obj.type != 'MESH':
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
    # is on the outline when exactly one of its linked faces points upward (its
    # other neighbour is a vertical wall). This recovers the map outline for a
    # watertight terrain block.
    if not segs:
        for e in bm.edges:
            up = sum(1 for f in e.link_faces if f.normal.normalized().z > 0.5)
            if up == 1:
                s = _seg(e)
                if s is not None:
                    segs.append(s)

    bm.free()
    if not segs:
        return None
    merged = unary_union(segs)
    polys = list(polygonize(merged))
    if not polys:
        return None
    biggest = max(polys, key=lambda p: p.area)
    return validate(biggest)


def footprint_with_holes(obj, simplify_tol=None, down_only=False):
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
    if obj is None or obj.type != 'MESH':
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
        except Exception:
            continue
        if not p.is_valid:
            p = validate(p)
        if p is not None and not p.is_empty and p.area > 0:
            polys.append(p)
    bm.free()
    if not polys:
        return None
    merged = unary_union(polys)
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
    except Exception:
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


def _earcut_triangulate(exterior_xy, holes_xy):
    """Triangulate a polygon-with-holes using mapbox_earcut.

    exterior_xy -- list of (x, y) for the outer ring (no closing duplicate)
    holes_xy    -- list of lists of (x, y), one per hole (no closing dup)

    Returns (verts2d, tris) where:
      verts2d -- list of (x, y) tuples, the SHARED vertex array (outer ring
                 first, then each hole appended in order)
      tris    -- list of (i, j, k) index triples into verts2d

    The triangulation references every vertex by index with NO duplication, so
    the resulting 2D surface is manifold (interior edges shared by exactly two
    triangles, ring edges by one).  Returns None on failure / degenerate input.

    earcut is a modified ear-slicing algorithm that handles holes, concavity,
    twisted polygons and self-intersections robustly -- the same engine trimesh
    uses for Shapely -> mesh conversion.  Unlike mathutils.tessellate_polygon it
    does not emit overlapping triangles or duplicate vertices for complex rings.
    """
    if not _HAS_EARCUT:
        return None
    verts2d = list(exterior_xy)
    ring_ends = [len(verts2d)]
    for hole in holes_xy:
        verts2d.extend(hole)
        ring_ends.append(len(verts2d))
    if len(verts2d) < 3:
        return None
    arr = _np.array(verts2d, dtype=_np.float64).reshape(-1, 2)
    rings = _np.array(ring_ends, dtype=_np.uint32)
    try:
        idx = _earcut.triangulate_float64(arr, rings)
    except Exception:
        return None
    if idx is None or len(idx) < 3:
        return None
    tris = [tuple(int(i) for i in idx[t:t + 3]) for t in range(0, len(idx) - 2, 3)]
    if not tris:
        return None
    return verts2d, tris


def polygon_to_mesh(name, polygon):
    """Convert a Shapely Polygon to a flat Blender mesh object at z=0.

    For polygons with holes the cap is triangulated with mapbox_earcut, which
    shares every vertex by index and never emits overlapping triangles, so the
    resulting 2D surface is manifold.  Extruding that surface (done by the
    caller) yields a watertight manifold prism, which is required for the
    MANIFOLD boolean against the terrain to work.

    Falls back to mathutils.tessellate_polygon only if earcut is unavailable.

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
        ec = _earcut_triangulate(ext_xy, holes_xy)
        if ec is not None:
            verts2d, tris = ec
            coords = [(x, y, 0.0) for x, y in verts2d]
            mesh = bpy.data.meshes.new(name)
            tobj = bpy.data.objects.new(name, mesh)
            bpy.context.collection.objects.link(tobj)
            mesh.from_pydata(coords, [], tris)
            mesh.update()
            # earcut "doesn't guarantee correctness" for self-touching rings; it
            # can emit a few zero-area / overlapping sliver triangles that show up
            # as non-manifold edges. Dissolve them. This runs on THIS cap's own
            # fresh mesh only, so it never welds vertices across polygon parts
            # (which is what previously created pinch-point non-manifold verts).
            bm = bmesh.new()
            bm.from_mesh(mesh)
            bmesh.ops.dissolve_degenerate(bm, dist=1e-6, edges=bm.edges[:])
            bm.to_mesh(mesh)
            bm.free()
            mesh.update()
        else:
            # Fallback: mathutils tessellation (earcut missing). Less robust --
            # may produce non-manifold caps for complex holed polygons.
            from mathutils.geometry import tessellate_polygon  # type: ignore
            loops = [outer] + holes
            veclists = [[Vector(p) for p in loop] for loop in loops]
            all_coords = []
            for loop in loops:
                all_coords.extend(loop)
            mtris = tessellate_polygon(veclists)
            if not mtris:
                return None
            mesh = bpy.data.meshes.new(name)
            tobj = bpy.data.objects.new(name, mesh)
            bpy.context.collection.objects.link(tobj)
            mesh.from_pydata(all_coords, [], [tuple(t) for t in mtris])
            mesh.update()
            bm = bmesh.new()
            bm.from_mesh(mesh)
            bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-5)
            bmesh.ops.dissolve_degenerate(bm, dist=1e-5, edges=bm.edges[:])
            bm.to_mesh(mesh)
            bm.free()
            mesh.update()
    else:
        # No holes: still triangulate properly via earcut so the BVH gets
        # real triangles rather than a single NGON that gets fan-tessellated
        # incorrectly for concave river/ribbon polygons.
        ext_xy = [(x, y) for x, y, _ in outer]
        ec = _earcut_triangulate(ext_xy, [])
        mesh = bpy.data.meshes.new(name)
        tobj = bpy.data.objects.new(name, mesh)
        bpy.context.collection.objects.link(tobj)
        if ec is not None:
            verts2d, tris = ec
            coords = [(x, y, 0.0) for x, y in verts2d]
            mesh.from_pydata(coords, [], tris)
            mesh.update()
            bm = bmesh.new()
            bm.from_mesh(mesh)
            bmesh.ops.dissolve_degenerate(bm, dist=1e-6, edges=bm.edges[:])
            bm.to_mesh(mesh)
            bm.free()
            mesh.update()
        else:
            # earcut unavailable — fall back to single NGON (old behaviour)
            bm = bmesh.new()
            bm_verts = [bm.verts.new(c) for c in outer]
            try:
                bm.faces.new(bm_verts)
            except ValueError:
                bm.free()
                bpy.data.meshes.remove(mesh)
                bpy.data.objects.remove(tobj, do_unlink=True)
                return None
            bm.to_mesh(mesh)
            bm.free()

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

