"""GeoJSON boundary import: parse -> simplify -> triangulate into a flat tile.

Mirrors the structure of utils/io_gpx.py: a plain reader that raises on bad
input (the caller decides how to surface it, same contract as read_gpx()),
plus the geometry-building step that hands off a tagged "MAP" tile to the
same createTerrainFromSelected() pipeline every other mapmode already uses.
"""

import json
import math

import bpy       # type: ignore
import bmesh     # type: ignore

from . import geometry2d as g2d

# Above this real-world size, a full Overpass fetch (roads/water/forest/...)
# over the whole area gets slow -- not a hard limit (the per-element
# ..._MAXSIZE constants in constants.py already gate what gets fetched), just
# a heads-up so the user isn't surprised by a long-running generation.
_LARGE_AREA_WARN_KM = 150


def _ring_2d(ring):
    """Strip an optional z/altitude component from a GeoJSON coordinate ring."""
    return [(pt[0], pt[1]) for pt in ring]


def _polygon_from_coords(coords):
    """Build a Shapely Polygon from a GeoJSON Polygon 'coordinates' array."""
    exterior = _ring_2d(coords[0])
    holes = [_ring_2d(ring) for ring in coords[1:]]
    return g2d.Polygon(exterior, holes)


def _geometry_to_polygons(geometry):
    geom_type = geometry.get("type")
    if geom_type == "Polygon":
        return [_polygon_from_coords(geometry["coordinates"])]
    if geom_type == "MultiPolygon":
        return [_polygon_from_coords(part) for part in geometry["coordinates"]]
    return []


def read_geojson_file(filepath):
    """Parse a .geojson/.json file into a single validated Shapely Polygon.

    Accepts a bare Polygon/MultiPolygon geometry, a Feature, or a
    FeatureCollection (coordinates are lon/lat degrees, per the GeoJSON
    spec). Raises on malformed input (OSError, json.JSONDecodeError,
    KeyError, ValueError) -- callers wrap this in try/except and surface the
    error, same contract as io_gpx.read_gpx().

    If the file contains more than one polygon part (a MultiPolygon, or a
    FeatureCollection with several polygon features), the largest by area is
    kept and a warning is raised through WarningsOverlay about the rest --
    multi-part boundaries (islands, exclaves) aren't supported as multiple
    tiles yet.
    """
    g2d._require_shapely()

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    polygons = []

    def _collect(node):
        node_type = node.get("type")
        if node_type == "FeatureCollection":
            for feature in node.get("features", []):
                _collect(feature)
        elif node_type == "Feature":
            geometry = node.get("geometry")
            if geometry is not None:
                _collect(geometry)
        elif node_type == "GeometryCollection":
            for geometry in node.get("geometries", []):
                _collect(geometry)
        elif node_type in ("Polygon", "MultiPolygon"):
            polygons.extend(_geometry_to_polygons(node))
        # Other geometry types (Point, LineString, ...) are silently skipped
        # -- a boundary import only cares about area geometry.

    _collect(data)

    if not polygons:
        raise ValueError("No Polygon/MultiPolygon geometry found in file")

    merged = g2d.union(polygons) if len(polygons) > 1 else polygons[0]
    merged = g2d.validate(merged)

    parts = list(g2d.iter_polygons(merged))
    if not parts:
        raise ValueError("GeoJSON contains no usable polygon area")

    if len(parts) > 1:
        from .. import progress as _progress  # deferred to avoid circular import at load time
        dropped = len(parts) - 1
        _progress.WarningsOverlay.add_warning(
            f"GeoJSON had {len(parts)} separate polygon parts — using the largest "
            f"and dropping {dropped} smaller part(s) (islands/exclaves aren't supported yet).",
            "warn",
        )

    return max(parts, key=lambda p: p.area)


def simplify_boundary(polygon, tolerance):
    """Simplify *polygon* (Douglas-Peucker) by *tolerance*, in whatever units
    the polygon's own coordinates are in.

    Falls back to the unsimplified polygon if simplification breaks validity
    -- the same self-intersection risk already flagged in the el_oRdpEpsilon
    coastline-tolerance docstring (props.py). tolerance <= 0 disables
    simplification entirely.
    """
    if tolerance <= 0:
        return polygon
    simplified = polygon.simplify(tolerance, preserve_topology=True)
    simplified = g2d.validate(simplified)
    parts = list(g2d.iter_polygons(simplified))
    if not parts:
        return polygon
    return max(parts, key=lambda p: p.area)


def build_tile_from_polygon(polygon_lonlat, obj_size, num_subdivisions, name="GeoJSON", simplify_tolerance=0.1):
    """Build a flat MAP tile mesh shaped like *polygon_lonlat* (lon/lat degrees).

    Derives scene.tp3d.sScaleHor from the polygon's own bounding box and
    *obj_size* (mm) -- the same COORDINATES-mode formula every other mapmode
    uses (utils.geo.calculate_scale) -- then projects every vertex to Blender
    space, simplifies (map-unit tolerance, same convention as
    el_oRdpEpsilon), and earcut-triangulates the boundary via
    geometry2d.polygon_to_mesh(). The result's interior is then subdivided
    (mirroring create_circle/create_hexagon's own post-fill subdivide step)
    so elevation draping has more than just the boundary ring to sample.

    Returns the new tagged "MAP" tile object (selected + active), or None on
    a degenerate/empty polygon.
    """
    from .geo import convert_to_neutral_coordinates, convert_to_blender_coordinates, convert_to_geo  # deferred to avoid circular import at load time
    from .elevation import compute_and_store_tile_bounds, get_tile_elevation  # deferred to avoid circular import at load time

    tp3d = bpy.context.scene.tp3d

    west, south, east, north = polygon_lonlat.bounds
    x1, y1, _ = convert_to_neutral_coordinates(south, west, 0, 0)
    x2, y2, _ = convert_to_neutral_coordinates(north, east, 0, 0)
    maxer = max(abs(x2 - x1), abs(y2 - y1))
    if maxer <= 0:
        return None
    scale_hor = obj_size / maxer
    tp3d["sScaleHor"] = scale_hor

    def _project_ring(ring):
        pts = []
        for lon, lat in ring:
            x, y, _z = convert_to_blender_coordinates(lat, lon, 0, 0)
            pts.append((x, y))
        return pts

    ext_xy = _project_ring(polygon_lonlat.exterior.coords)
    holes_xy = [_project_ring(interior.coords) for interior in polygon_lonlat.interiors]

    projected = g2d.Polygon(ext_xy, holes_xy)
    projected = g2d.validate(projected)
    projected = simplify_boundary(projected, simplify_tolerance)

    tile = g2d.polygon_to_mesh(name, projected)
    if tile is None:
        return None

    # Add interior vertices so elevation draping isn't limited to the
    # boundary ring -- mirrors create_circle/create_hexagon's own
    # post-fill subdivide step (primitives.py).
    _sub_iters = num_subdivisions - 3
    if _sub_iters > 0:
        cuts = 2 ** _sub_iters - 1
        bm = bmesh.new()
        bm.from_mesh(tile.data)
        bmesh.ops.subdivide_edges(bm, edges=list(bm.edges), cuts=cuts, use_grid_fill=True)
        bm.to_mesh(tile.data)
        bm.free()
        tile.data.update()

    # earcut's winding isn't guaranteed to face up -- flip if the average
    # normal points down (same check create_circle runs after fill_grid).
    bm = bmesh.new()
    bm.from_mesh(tile.data)
    bm.normal_update()
    if bm.faces and sum(f.normal.z for f in bm.faces) / len(bm.faces) < 0:
        for f in bm.faces:
            f.normal_flip()
        bm.normal_update()
    bm.to_mesh(tile.data)
    bm.free()
    tile.data.update()

    tile.name = name
    tile["objType"] = "MAP"
    tile["Shape"] = "GEOJSON"
    tile["objSize"] = maxer * scale_hor

    bpy.ops.object.select_all(action='DESELECT')
    tile.select_set(True)
    bpy.context.view_layer.objects.active = tile

    compute_and_store_tile_bounds(tile)

    map_km = bpy.context.scene.tp3d.get("sMapInKm", 0)
    if map_km > _LARGE_AREA_WARN_KM:
        from .. import progress as _progress  # deferred to avoid circular import at load time
        _progress.WarningsOverlay.add_warning(
            f"This boundary spans ~{map_km:.0f} km — fetching roads/water/forest over "
            "an area this large can take a while (or time out on the Overpass API).",
            "warn",
        )

    # Seed autoScale/additionalExtrusion before createTerrainFromSelected()
    # runs -- it reads scene.tp3d.sAutoScale directly with no fallback
    # computation of its own (utils/generation.py:_ctfs_load_props). The
    # default (fixedElevationScale off) needs no preview fetch at all; only
    # the fixed-scale mode needs a real elevation range, mirroring
    # runGeneration's own fixedElevationScale branch.
    auto_scale = scale_hor
    additional_extrusion = 0.0
    if tp3d.get('fixedElevationScale', False):
        preview_elevations, preview_diff = get_tile_elevation(tile)
        auto_scale = 10 / (preview_diff / 1000) if preview_diff > 0 else 10
        lowest_z = 1000.0
        obj_matrix = tile.matrix_world
        for i, vert in enumerate(tile.data.vertices):
            world_co = obj_matrix @ vert.co
            vert_lat, _lon = convert_to_geo(world_co.x, world_co.y)
            merc = 1 / math.cos(math.radians(vert_lat))
            val = preview_elevations[i] / 1000 * tp3d.scaleElevation * auto_scale * merc
            lowest_z = min(lowest_z, val)
        additional_extrusion = lowest_z

    tp3d.sAutoScale = auto_scale
    tp3d.sAdditionalExtrusion = additional_extrusion

    return tile
