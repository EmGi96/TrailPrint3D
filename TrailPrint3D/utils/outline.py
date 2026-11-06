"""Bottom-outline lookup shared by every operator that needs an object's 2D
footprint (magnet holes today).

WKT contract -- every key below is a Shapely WKT string in the object's own
LOCAL XY frame (relative to its origin, ignoring Z), unless noted:

  outline_wkt       Generic footprint, highest priority. Stamped on puzzle
                    pieces (cut_into_puzzle_pieces / premium sliding pieces),
                    which also inherit the whole map's map_polygon_wkt.
  plate_wkt         Solid plate outer outline (create_generic_plate), or the
                    shell's outer outline (alias of shell_outer_wkt).
  insert_wkt        Map footprint the plate/shell holds.
  shell_outer_wkt   Shell outer outline (outer wall face).
  shell_inner_wkt   Shell inner outline (cavity wall face).
  map_polygon_wkt   Map outline, PRE-rotation: shapeRotation is baked into the
                    mesh afterwards (around the origin) but this key is never
                    updated -- use map_outline_local() to get the real one.

All post-rotation keys are rotated around the local origin (0, 0), the same
pivot transform_apply uses when it bakes shapeRotation into the mesh.

No bpy import: everything here works on plain object attributes, so it stays
importable from headless tests.
"""

from shapely import wkt as _wkt
from shapely.affinity import rotate as _rotate
from shapely.affinity import translate as _translate
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

_DIRECT_KEYS = ("outline_wkt", "plate_wkt", "shell_outer_wkt")
# Every local-frame key, i.e. everything that must move when the origin does.
_LOCAL_KEYS = (
    "outline_wkt",
    "plate_wkt",
    "insert_wkt",
    "shell_outer_wkt",
    "shell_inner_wkt",
    "shell_map_wkt",
)


def stamp_shell_outline(obj, outer, inner, map_poly=None):
    """Stamp a shell's outlines (local frame) under its own keys plus the
    plate_wkt/insert_wkt aliases, so plates and shells read the same way."""
    values = {
        "shell_outer_wkt": outer.wkt,
        "shell_inner_wkt": inner.wkt,
        "plate_wkt": outer.wkt,
        "insert_wkt": inner.wkt,
    }
    obj["shell_mode"] = "SHELL"
    if map_poly is not None:
        obj["shell_map_wkt"] = map_poly.wkt
    for key, value in values.items():
        obj[key] = value
        if obj.data is not None:
            obj.data[key] = value


def shift_outline_keys(obj, dx, dy):
    """Translate every stored local-frame outline by (dx, dy) -- call with
    (old_origin - new_origin) after anything moves the object's origin."""
    if not dx and not dy:
        return
    holders = [obj] + ([obj.data] if getattr(obj, "data", None) is not None else [])
    for holder in holders:
        for key in _LOCAL_KEYS:
            if key not in holder:
                continue
            try:
                geom = _wkt.loads(holder[key])
            except Exception as exc:  # shapely raises its own WKTReadingError/GEOSException
                print(f"[TrailPrint3D] could not shift {key}: {exc!r}")
                continue
            holder[key] = _translate(geom, xoff=dx, yoff=dy).wkt


def _load(wkt_str):
    try:
        geom = _wkt.loads(wkt_str)
    except Exception as exc:  # shapely raises its own WKTReadingError/GEOSException
        print(f"[TrailPrint3D] outline WKT unreadable: {exc!r}")
        return None
    return _polygonal(geom)


def _polygonal(geom):
    """Polygon/MultiPolygon part of *geom*, made valid; None if nothing is left."""
    if geom is None or geom.is_empty:
        return None
    if not geom.is_valid:
        geom = geom.buffer(0)
    if isinstance(geom, (Polygon, MultiPolygon)):
        return None if geom.is_empty else geom
    parts = [g for g in getattr(geom, "geoms", []) if isinstance(g, (Polygon, MultiPolygon))]
    if not parts:
        return None
    merged = unary_union(parts)
    return None if merged.is_empty else merged


def _bounds_error(a, b):
    return sum(abs(x - y) for x, y in zip(a, b))


def map_outline_local(obj):
    """The map's real outline in its local frame, or None.

    map_polygon_wkt is pre-rotation, and whether the rotation was baked into
    the mesh (normal generation) or still sits in the object transform
    (premium Extend Tile) isn't recorded anywhere -- so pick whichever of the
    two candidates matches the mesh's own local XY bounds.
    """
    if "map_polygon_wkt" not in obj:
        return None
    poly = _load(obj["map_polygon_wkt"])
    if poly is None:
        return None
    rotation = float(obj.get("shapeRotation", 0.0) or 0.0)
    if not rotation:
        return poly
    rotated = _rotate(poly, rotation, origin=(0, 0))
    mesh_bounds = _local_xy_bounds(obj)
    if mesh_bounds is None:
        return rotated
    if _bounds_error(poly.bounds, mesh_bounds) < _bounds_error(rotated.bounds, mesh_bounds):
        return poly
    return rotated


def _local_xy_bounds(obj):
    bb = getattr(obj, "bound_box", None)
    if not bb:
        return None
    xs = [c[0] for c in bb]
    ys = [c[1] for c in bb]
    return (min(xs), min(ys), max(xs), max(ys))


def _outline_from_bottom_faces(obj):
    """Union of the downward-facing faces' XY footprints, for objects with
    no WKT at all (legacy files, imported meshes)."""
    mesh = getattr(obj, "data", None)
    polys = getattr(mesh, "polygons", None)
    if not polys:
        return None
    verts = mesh.vertices
    z_min = min(c[2] for c in obj.bound_box)
    tris = []
    for f in polys:
        if f.normal.z > -0.99:
            continue
        idx = list(f.vertices)
        if any(abs(verts[i].co.z - z_min) > 1e-3 for i in idx):
            continue
        ring = [(verts[i].co.x, verts[i].co.y) for i in idx]
        if len(ring) >= 3:
            p = Polygon(ring)
            if not p.is_valid:
                p = p.buffer(0)
            if not p.is_empty:
                tris.append(p)
    if not tris:
        return None
    # Tiny grow/shrink welds the shared edges between neighboring faces.
    return _polygonal(unary_union(tris).buffer(1e-4).buffer(-1e-4))


def get_bottom_outline(obj):
    """Object's footprint as a Shapely (Multi)Polygon in its local XY frame,
    or None when nothing usable is found."""
    if obj is None:
        return None
    for key in _DIRECT_KEYS:
        if key in obj:
            poly = _load(obj[key])
            if poly is not None:
                return poly
    poly = map_outline_local(obj)
    if poly is not None:
        return poly
    return _outline_from_bottom_faces(obj)


def rotation_of(obj, default=0.0):
    """shapeRotation in degrees, for angle-based layouts."""
    value = obj.get("shapeRotation", default) if obj is not None else default
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)
