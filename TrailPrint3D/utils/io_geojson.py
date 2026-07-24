"""GeoJSON boundary import: parse -> simplify -> triangulate into a flat tile.

Mirrors the structure of utils/io_gpx.py: a plain reader that raises on bad
input (the caller decides how to surface it, same contract as read_gpx()),
plus the geometry-building step that hands off a tagged "MAP" tile to the
same createTerrainFromSelected() pipeline every other mapmode already uses.
"""

import json
import math

import bmesh  # type: ignore
import bpy  # type: ignore

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


def count_boundary_points(polygon):
    """Total exterior-ring point count across every part of a Polygon or
    MultiPolygon (mainland + islands), for UI display -- mirrors how
    build_tile_from_polygon iterates every part via geometry2d.iter_polygons.
    """
    return sum(len(part.exterior.coords) - 1 for part in g2d.iter_polygons(polygon))


def _geometry_to_polygons(geometry):
    geom_type = geometry.get("type")
    if geom_type == "Polygon":
        return [_polygon_from_coords(geometry["coordinates"])]
    if geom_type == "MultiPolygon":
        return [_polygon_from_coords(part) for part in geometry["coordinates"]]
    return []


def _polygon_or_multipolygon(parts):
    """Normalize a list of Shapely Polygons back into a single Polygon (if
    only one part) or a MultiPolygon (if several) -- the canonical shape
    every function in this module expects to receive/return, so mainland +
    island parts all survive as one geometry rather than picking a winner.
    """
    if len(parts) == 1:
        return parts[0]
    return g2d.MultiPolygon(parts)


def _extract_polygons_from_geojson(data):
    """Parse one already-loaded GeoJSON dict into a flat list of raw Shapely
    Polygons (no union/validate yet) -- accepts a bare Polygon/MultiPolygon
    geometry, a Feature, or a FeatureCollection.
    """
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
    return polygons


def _finalize_polygons(polygons, source_desc="file"):
    """Union, validate, and normalize a flat list of raw polygon parts into
    the canonical Polygon/MultiPolygon shape every function in this module
    expects. Shared tail end of read_geojson_file/read_geojson_files.
    """
    if not polygons:
        raise ValueError(f"No Polygon/MultiPolygon geometry found in {source_desc}")

    merged = g2d.union(polygons) if len(polygons) > 1 else polygons[0]
    merged = g2d.validate(merged)

    parts = list(g2d.iter_polygons(merged))
    if not parts:
        raise ValueError(f"{source_desc} contains no usable polygon area")

    return _polygon_or_multipolygon(parts)


def read_geojson_file(filepath):
    """Parse a .geojson/.json file into a validated Shapely Polygon or
    MultiPolygon.

    Accepts a bare Polygon/MultiPolygon geometry, a Feature, or a
    FeatureCollection (coordinates are lon/lat degrees, per the GeoJSON
    spec). Raises on malformed input (OSError, json.JSONDecodeError,
    KeyError, ValueError) -- callers wrap this in try/except and surface the
    error, same contract as io_gpx.read_gpx().

    Every polygon part found (a MultiPolygon, or a FeatureCollection with
    several polygon features -- e.g. a coastal departement's mainland plus
    its islands) is kept; downstream code (build_tile_from_polygon) builds
    one tile whose mesh has a separate disconnected piece per part, correctly
    positioned relative to each other.
    """
    g2d._require_shapely()
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    return _finalize_polygons(_extract_polygons_from_geojson(data), source_desc="file")


def read_geojson_files(filepaths):
    """Parse and merge multiple .geojson/.json files into one boundary.

    Combines every polygon part from every file (e.g. two neighbouring
    departements) through the same union+validate step read_geojson_file
    uses for multiple parts within one file. Boundaries that share an exact
    edge fuse into a single seamless Polygon; boundaries whose source data
    doesn't align perfectly at the seam fall back to a MultiPolygon with
    every part kept, same as the islands/exclaves case rather than failing.
    """
    g2d._require_shapely()
    if not filepaths:
        raise ValueError("No GeoJSON files given")

    all_polygons = []
    for filepath in filepaths:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        all_polygons.extend(_extract_polygons_from_geojson(data))

    return _finalize_polygons(all_polygons, source_desc=f"{len(filepaths)} file(s)")


def simplify_boundary(polygon, tolerance):
    """Simplify *polygon* (Douglas-Peucker) by *tolerance*, in whatever units
    the polygon's own coordinates are in. Works on a Polygon or MultiPolygon
    alike, keeping every part (mainland + islands).

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
    return _polygon_or_multipolygon(parts)


def build_tile_from_polygon(polygon_lonlat, obj_size, num_subdivisions, name="GeoJSON",
                             simplify_tolerance=0.1, scale_hor=None, set_auto_scale=True):
    """Build a flat MAP tile mesh shaped like *polygon_lonlat* (lon/lat degrees).

    Derives scene.tp3d.sScaleHor from the polygon's own bounding box and
    *obj_size* (mm) -- the same COORDINATES-mode formula every other mapmode
    uses (utils.geo.calculate_scale) -- then projects every vertex to Blender
    space and simplifies (map-unit tolerance, same convention as
    el_oRdpEpsilon).

    *scale_hor*, if given, is used verbatim instead of deriving one from this
    polygon's own bounding box -- for a batch of several boundaries meant to
    keep their true relative geographic position (multitile_configurator's
    GeoJSON batch, premium/operators_pe.py's TP3D_OT_map_picker), the caller
    computes ONE shared scale from the combined bbox of every boundary in the
    batch and passes it to each build_tile_from_polygon() call, rather than
    each tile picking its own scale from just its own footprint.

    *set_auto_scale*, when False, skips writing scene.tp3d.sAutoScale/
    sAdditionalExtrusion (and the elevation-preview fetch that would compute
    them) -- both are scene-global, so a caller building several tiles in one
    batch needs to compute and set a single shared pair itself beforehand
    (mirroring TP3D_OT_map_picker's own combined-bbox preview-elevation pass
    for its grid-segment batches) rather than have each tile's own call
    silently overwrite the previous tile's values, leaving every tile but the
    last with the wrong auto-scale/extrusion once createTerrainFromSelected()
    processes them all together.

    The terrain mesh itself is a regular grid (primitives.create_rectangle,
    the same well-shaped, evenly-subdivided primitive create_hexagon/
    create_circle use), clipped down to the polygon's exact outline via a
    boolean INTERSECT against a solid prism cut from the polygon. Earcut-
    triangulating the real boundary directly (the first approach tried here)
    produces long sliver triangles on a complex, concave real-world border --
    up to 45:1 aspect ratio on a real French departement boundary -- which
    fan out into visible spikes once each vertex gets its own independent
    elevation sample. Clipping a regular grid instead keeps that well-shaped
    topology through the whole interior, with only the boundary ring
    affected by the cut -- the same technique already proven for jigsaw
    pieces in mesh_ops.cut_into_puzzle_pieces().

    Returns the new tagged "MAP" tile object (selected + active), or None on
    a degenerate/empty polygon.
    """
    from .geo import convert_to_neutral_coordinates, convert_to_blender_coordinates, convert_to_geo  # deferred to avoid circular import at load time
    from .elevation import compute_and_store_tile_bounds, get_tile_elevation  # deferred to avoid circular import at load time
    from .primitives import create_rectangle  # deferred to avoid circular import at load time
    from . import mesh_ops  # deferred to avoid circular import at load time

    tp3d = bpy.context.scene.tp3d

    west, south, east, north = polygon_lonlat.bounds
    x1, y1, _ = convert_to_neutral_coordinates(south, west, 0, 0)
    x2, y2, _ = convert_to_neutral_coordinates(north, east, 0, 0)
    maxer = max(abs(x2 - x1), abs(y2 - y1))
    if maxer <= 0:
        return None
    if scale_hor is None:
        scale_hor = obj_size / maxer
    tp3d["sScaleHor"] = scale_hor

    def _project_ring(ring):
        pts = []
        for lon, lat in ring:
            x, y, _z = convert_to_blender_coordinates(lat, lon, 0, 0)
            pts.append((x, y))
        return pts

    def _project_part(part):
        ext_xy = _project_ring(part.exterior.coords)
        holes_xy = [_project_ring(interior.coords) for interior in part.interiors]
        return g2d.Polygon(ext_xy, holes_xy)

    # A MultiPolygon (mainland + islands) projects to one Blender-space part
    # per input part -- iter_polygons/_extrude_flat_polygon further down
    # already iterate over every part of a MultiPolygon transparently, so no
    # other step needs to know how many separate landmasses there are.
    parts_lonlat = list(polygon_lonlat.geoms) if isinstance(polygon_lonlat, g2d.MultiPolygon) else [polygon_lonlat]
    projected_parts = [_project_part(part) for part in parts_lonlat]
    projected = projected_parts[0] if len(projected_parts) == 1 else g2d.MultiPolygon(projected_parts)
    projected = g2d.validate(projected)
    projected = simplify_boundary(projected, simplify_tolerance)
    if projected is None or projected.is_empty:
        return None

    px1, py1, px2, py2 = projected.bounds
    grid_w, grid_h = px2 - px1, py2 - py1
    if grid_w <= 0 or grid_h <= 0:
        return None

    tile = create_rectangle(grid_w, grid_h, num_subdivisions, name)
    tile.location = ((px1 + px2) / 2, (py1 + py2) / 2, 0)
    bpy.context.view_layer.objects.active = tile
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

    # writeMetadata() (utils/metadata.py) computes the tile's lat/long from
    # these two scene properties -- the only other writer is runGeneration's
    # trail pipeline (utils/generation.py, Phase 6). This mapmode bypasses
    # that pipeline entirely, so without setting them here they're left at
    # whatever a previous (differently-scaled) generation left behind; paired
    # with this tile's own fresh sScaleHor, convert_to_geo's Mercator formula
    # can overflow on the mismatch.
    tp3d["o_centerx"] = (px1 + px2) / 2
    tp3d["o_centery"] = (py1 + py2) / 2

    # Solid cutter prism from the (simplified) polygon -- generously tall so
    # it fully spans the still-flat (z=0) grid regardless of scale.
    cutter_verts, cutter_faces = [], []
    for part in g2d.iter_polygons(projected):
        mesh_ops._extrude_flat_polygon(g2d, part, -50.0, 50.0, cutter_verts, cutter_faces)
    if not cutter_verts:
        bpy.data.objects.remove(tile, do_unlink=True)
        return None

    cutter_mesh = bpy.data.meshes.new(f"{name}_cutter")
    cutter_mesh.from_pydata(cutter_verts, [], cutter_faces)
    cutter_mesh.update()
    mesh_ops._clean_solid_mesh(cutter_mesh)
    cutter_obj = bpy.data.objects.new(cutter_mesh.name, cutter_mesh)
    bpy.context.collection.objects.link(cutter_obj)

    # EXACT (not MANIFOLD): the cutter, extruded from a real-world border,
    # can still be non-manifold even after _clean_solid_mesh -- MANIFOLD
    # silently no-ops on non-manifold input (same failure mode documented in
    # mesh_ops.intersectWithTile), leaving the grid uncropped.
    mesh_ops.boolean_operation(tile, cutter_obj, 'INTERSECT', solver='EXACT')
    bpy.data.objects.remove(cutter_obj, do_unlink=True)

    if len(tile.data.vertices) == 0:
        bpy.data.objects.remove(tile, do_unlink=True)
        return None


    # The boolean can leave normals inconsistent -- flip if the average
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
    tile["Shape"] = "CUSTOM"
    tile["objSize"] = maxer * scale_hor

    # Reference outline for the multi-tile configurator's existing-maps layer
    # (operators._collect_existing_maps / premium/multitile_configurator.html)
    # -- a CUSTOM tile has no regular hexagon/rectangle shape to reconstruct
    # from just its bounding box, so store the actual boundary (reprojected
    # back to lat/lon) it can draw instead. Each part is wrapped in its own
    # single-ring array ([[ring]] per part, i.e. a 3-level list overall) --
    # Leaflet's L.polygon reads a 2-level [ring, ring] list as one polygon
    # with the second ring as a HOLE, so mainland+island parts must each get
    # their own ring-array to render as separate shapes instead.
    tile["BoundaryPolygon"] = json.dumps([
        [[convert_to_geo(x, y) for x, y in part.exterior.coords]]
        for part in g2d.iter_polygons(projected)
    ])

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
    #
    # Skipped entirely when set_auto_scale is False -- a batch caller already
    # computed one shared auto_scale/additional_extrusion (from the combined
    # bbox of every tile in the batch) and set it itself; doing the same work
    # here per-tile would both waste an extra elevation-preview fetch and
    # clobber that shared value with one derived from just this one polygon.
    if set_auto_scale:
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
