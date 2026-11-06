"""Pure-Shapely magnet-hole placement (no bpy), shared by the Magnet Holes
operator and the Medal Holder generator.

All coordinates are in the target object's local XY frame (see outline.py).
"""

import math

from shapely.geometry import Point
from shapely.ops import unary_union
from shapely.prepared import prep

# Shapes whose local origin is their own center, so a magnet can be pushed
# outward along a fixed direction from (0, 0).
_RADIAL_SHAPES = {"SQUARE", "HEXAGON", "OCTAGON", "CIRCLE", "ELLIPSE"}

# First corner direction (degrees, before shapeRotation) -- clear of where
# TP3D_OT_dovetail puts its cutouts (square: 0/90/..., everything else:
# 30/90/150/...).
_CORNER_START = {
    "SQUARE": 45.0,
    "HEXAGON": 0.0,
    "OCTAGON": 22.5,
    "CIRCLE": 0.0,
    "ELLIPSE": 0.0,
}

_RAY_STEP = 0.25
_DEFAULT_GAP = 1.0


def place_on_rays(fits, angles, reach, step=0.5, center=(0.0, 0.0)):
    """For each angle, march from *reach* toward *center* and keep the first
    (x, y) where fits(x, y) is true. Angles where nothing fits are skipped."""
    cx, cy = center
    placed = []
    for a in angles:
        d = reach
        while d > 0:
            x, y = cx + math.cos(a) * d, cy + math.sin(a) * d
            if fits(x, y):
                placed.append((x, y))
                break
            d -= step
    return placed


def preferred_angles(shape, count, rotation_deg=0.0):
    """Evenly spread directions (radians) starting at the shape's first
    corner, offset by shapeRotation. None for shapes without a center."""
    if shape not in _RADIAL_SHAPES or count <= 0:
        return None
    start = _CORNER_START[shape] + rotation_deg
    return [math.radians(start + 360.0 * k / count) for k in range(count)]


def dovetail_footprints(obj_size, shape, rotation_deg=0.0):
    """Discs covering the cutouts TP3D_OT_dovetail makes, in the same frame
    (relative to the object's origin). Mirrors that operator's own sizing."""
    size = 15
    if obj_size <= 50:
        size = 5
    elif obj_size <= 75:
        size = 10
    if shape == "SQUARE":
        radius = obj_size / 2 - size / 2
        steps, start = 4, rotation_deg
    elif shape == "OCTAGON":
        radius = obj_size / 2 - size / 2
        steps, start = 8, rotation_deg
    else:
        radius = obj_size / 2 * 0.866 - size / 2
        steps, start = 6, rotation_deg + 30.0
    discs = []
    for i in range(steps):
        a = math.radians(start + i * 360.0 / steps)
        # 1.05: the operator scales the triangles' top faces up by that much.
        discs.append(Point(math.cos(a) * radius, math.sin(a) * radius).buffer(size * 1.05, quad_segs=8))
    return unary_union(discs)


def _farthest_point_fill(region, count, placed, min_dist, anchor):
    """Greedy farthest-point sampling over a grid inside *region*: each new
    point maximizes its distance to the points already placed (or to
    *anchor* for the very first one), never closer than *min_dist*."""
    minx, miny, maxx, maxy = region.bounds
    step = max(min_dist / 4.0, max(maxx - minx, maxy - miny) / 80.0, 0.1)
    region_p = prep(region)
    candidates = []
    y = miny
    while y <= maxy:
        x = minx
        while x <= maxx:
            if region_p.contains(Point(x, y)):
                candidates.append((x, y))
            x += step
        y += step
    # Vertices of the region itself catch corners the grid steps over.
    for part in getattr(region, "geoms", [region]):
        candidates.extend(part.exterior.coords[:-1])

    out = list(placed)
    while len(out) < count and candidates:
        best, best_d = None, -1.0
        for c in candidates:
            if out:
                d = min(math.hypot(c[0] - p[0], c[1] - p[1]) for p in out)
            else:
                d = math.hypot(c[0] - anchor[0], c[1] - anchor[1])
            if d > best_d:
                best, best_d = c, d
        if best is None or (out and best_d < min_dist):
            break
        out.append(best)
    return out[len(placed):]


def magnet_layout(
    outline,
    count,
    diameter,
    margin,
    blocked=None,
    shape=None,
    rotation_deg=0.0,
    gap=_DEFAULT_GAP,
):
    """Hole centers [(x, y), ...] for up to *count* magnets of *diameter*,
    each at least *margin* from the outline's edge, clear of *blocked*, and
    at least *gap* apart. May return fewer than asked (even none) when they
    don't fit.

    Regular shapes (*shape* in _RADIAL_SHAPES) push each magnet as far out as
    it fits along evenly spread corner directions; anything else (and any
    direction that found no room) falls back to farthest-point sampling of
    the free area.
    """
    if outline is None or outline.is_empty or count <= 0 or diameter <= 0:
        return []
    r = diameter / 2.0
    region = outline.buffer(-(margin + r))
    if blocked is not None and not blocked.is_empty:
        region = region.difference(blocked.buffer(r))
    if region.is_empty:
        return []
    region_p = prep(region)
    min_dist = diameter + gap

    placed = []

    def fits(x, y):
        if not region_p.contains(Point(x, y)):
            return False
        return all(math.hypot(x - px, y - py) >= min_dist for px, py in placed)

    angles = preferred_angles(shape, count, rotation_deg)
    if angles:
        reach = max(_polygon_reach(outline), _RAY_STEP)
        for a in angles:
            placed.extend(place_on_rays(fits, [a], reach, step=_RAY_STEP))

    if len(placed) < count:
        c = outline.centroid
        placed.extend(_farthest_point_fill(region, count, placed, min_dist, (c.x, c.y)))
    return placed[:count]


def _polygon_reach(poly):
    best = 0.0
    for part in getattr(poly, "geoms", [poly]):
        for x, y in part.exterior.coords:
            best = max(best, math.hypot(x, y))
    return best
