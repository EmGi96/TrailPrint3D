import math

import bmesh  # type: ignore
import bpy  # type: ignore
from mathutils import Vector  # type: ignore
from shapely import wkt

from . import geometry2d as g2d  # deferred-safe: pure-Python, no bpy-time side effects
from .dataclasses import GenerationContext


def _setup_material(name, color):
    if name not in bpy.data.materials:
        mat = bpy.data.materials.new(name=name)
    else:
        mat = bpy.data.materials[name]

    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if not bsdf:
        bsdf = nodes.new(type="ShaderNodeBsdfPrincipled")
        bsdf.location = (0, 0)

    output = next((n for n in nodes if n.type == "OUTPUT_MATERIAL"), None)
    if not output:
        output = nodes.new(type="ShaderNodeOutputMaterial")
        output.location = (300, 0)

    if not bsdf.outputs["BSDF"].is_linked:
        links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    bsdf.inputs["Base Color"].default_value = color


def setupColors():
    _setup_material("BASE", (0.05, 0.7, 0.05, 1.0))
    _setup_material("FOREST", (0.05, 0.25, 0.05, 1.0))
    _setup_material("MOUNTAIN", (0.5, 0.5, 0.5, 1.0))
    _setup_material("WATER", (0.0, 0.0, 0.8, 1.0))
    _setup_material("TRAIL", (1.0, 0.0, 0.0, 1.0))
    _setup_material("YELLOW", (1.0, 1.0, 0.0, 1.0))
    _setup_material("CITY", (0.7, 0.7, 0.1, 1.0))
    _setup_material("GREENSPACE", (0.16, 1.0, 0.16, 1.0))
    _setup_material("GLACIER", (0.8, 0.9, 0.8, 1.0))
    _setup_material("BLACK", (0.0, 0.0, 0.0, 1.0))
    _setup_material("WHITE", (1.0, 1.0, 1.0, 1.0))
    _setup_material("BUILDINGS", (0.4, 0.4, 0.4, 1.0))
    _setup_material("FARMLAND", (0.3, 0.5, 0.1, 1.0))


def create_curve_from_coordinates(gen: GenerationContext, coordinates):
    """
    Create a curve in Blender based on a list of (x, y, z) coordinates.
    """

    pathThickness = bpy.context.scene.tp3d.pathThickness
    name = bpy.context.scene.tp3d.modelname

    # Create a new curve object
    curve_data = bpy.data.curves.new("GPX_Curve", type="CURVE")
    curve_data.dimensions = "3D"
    polyline = curve_data.splines.new("POLY")
    polyline.points.add(count=len(coordinates) - 1)

    # Populate the curve with points
    for i, coord in enumerate(coordinates):
        polyline.points[i].co = (coord[0], coord[1], coord[2], 1)  # (x, y, z, w)

    # Create an object with this curve
    curve_object = bpy.data.objects.new("GPX_Curve_Object", curve_data)
    bpy.context.collection.objects.link(curve_object)
    curve_object.data.bevel_depth = pathThickness / 2  # Set the thickness of the curve
    curve_object.data.bevel_resolution = 4  # Set the resolution for smoothness

    mod = curve_object.modifiers.new(name="Remesh", type="REMESH")
    mod.mode = "VOXEL"
    mod.voxel_size = 0.05 * pathThickness * 10 / 2
    mod.adaptivity = 0.0
    curve_object.data.use_fill_caps = True

    print("CREATED CURVES")
    curve_object.data.name = name + "_Trail"
    curve_object.name = name + "_Trail"

    curve_object.select_set(True)

    bpy.context.view_layer.objects.active = curve_object

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.curve.select_all(action="SELECT")
    bpy.ops.object.mode_set(mode="OBJECT")

    return curve_object


def simplify_curve(points_with_extra, min_distance=0.1000):
    """
    Removes points that are too close to any previously accepted point.
    Keeps the full (x, y, z, time) format.
    """

    if not points_with_extra:
        return []

    simplified = [points_with_extra[0]]
    last_xyz = Vector(points_with_extra[0][:3])
    skipped = 0

    for pt in points_with_extra[1:]:
        current_xyz = Vector(pt[:3])
        if (current_xyz - last_xyz).length >= min_distance:
            simplified.append(pt)
            last_xyz = current_xyz
        else:
            skipped += 1

    print(f"Smooth curve: Removed {skipped} vertices")
    return simplified


# ---------------------------------------------------------------------------
# Polygon outlines + shared grid-clip builder
#
# Every named shape below is just a Shapely Polygon in local map units,
# clipped against a regular triangular lattice by build_mesh_from_polygon.
# One meshing implementation for all of them (plus GeoJSON boundaries, or
# any combination of the two via ordinary Shapely boolean ops) instead of a
# bespoke bmesh builder per shape.
# ---------------------------------------------------------------------------


def hexagon_polygon(size: float):
    """Same 6 vertices create_hexagon used to build directly."""
    pts = []
    for i in range(6):
        angle = math.radians(60 * i)
        pts.append((size * math.cos(angle), size * math.sin(angle)))
    return g2d.Polygon(pts)


def octagon_polygon(size: float):
    """Same regular-octagon construction create_octagon used to build
    directly (bevelled square, not 8 angle-equal points -- see that
    function's docstring for why)."""
    t = size * (math.sqrt(2) - 1)
    pts = [
        (size, t),
        (t, size),
        (-t, size),
        (-size, t),
        (-size, -t),
        (-t, -size),
        (t, -size),
        (size, -t),
    ]
    return g2d.Polygon(pts)


def circle_polygon(radius: float, num_segments: int = 64):
    pts = []
    for i in range(num_segments):
        angle = math.radians(360 * i / num_segments)
        pts.append((radius * math.cos(angle), radius * math.sin(angle)))
    return g2d.Polygon(pts)


def ellipse_polygon(radius: float, aspect_ratio: float = 0.75, num_segments: int = 64):
    pts = []
    for i in range(num_segments):
        angle = math.radians(360 * i / num_segments)
        pts.append((radius * math.cos(angle), radius * math.sin(angle) * aspect_ratio))
    return g2d.Polygon(pts)


def heart_polygon(size: float, steps: int = 200):
    """Same parametric heart curve create_heart used, now kept as an exact
    boundary instead of being extruded + voxel-remeshed and rounded off."""
    pts = []
    for i in range(steps):
        t = i / steps * (2 * math.pi)
        x = size * (16 * math.sin(t) ** 3) / 16
        y = (
            size
            * (
                13 * math.cos(t)
                - 5 * math.cos(2 * t)
                - 2 * math.cos(3 * t)
                - math.cos(4 * t)
            )
            / 16
        )
        pts.append((x, y))
    return g2d.Polygon(pts)


def rectangle_polygon(width: float, height: float):
    return g2d.Polygon(
        [
            (-width / 2, -height / 2),
            (width / 2, -height / 2),
            (width / 2, height / 2),
            (-width / 2, height / 2),
        ]
    )


# ---------------------------------------------------------------------------
# Shape Builders + Helpers
# ---------------------------------------------------------------------------


def build_mesh_from_polygon(polygon, cell_size: float, name: str = "Shape"):
    """Build a Blender mesh object by clipping a regular triangular lattice
    to *polygon* (any Shapely Polygon/MultiPolygon, in whatever local units
    the caller is working in -- a named shape's own outline, a projected
    GeoJSON boundary, or any combination of the two via ordinary Shapely
    boolean ops before it ever gets here).

    *cell_size* is the resolution knob -- smaller means more/smaller
    triangles. Callers translate their own num_subdivisions into a cell_size
    (see each create_* wrapper below for the convention this codebase uses).

    Returns the new linked, active object (verts at z=0, un-elevated --
    elevation gets applied later by vertex index, same as every other shape
    already works). Returns None on a degenerate/empty polygon.
    """
    if polygon is None or polygon.is_empty:
        return None

    lattice = g2d.build_triangular_lattice(polygon.bounds, cell_size)
    verts, tris = g2d.clip_triangles_to_polygon(lattice, polygon, 0.0)
    if not verts or not tris:
        return None

    # Drop degenerate triangles (two+ corners collapsed onto the same vertex
    # index by clip_triangles_to_polygon's 5-decimal rounding dedup, which
    # can happen at tiny cell_size/near-zero polygon extents). from_pydata
    # and bm.from_mesh accept these silently, but the bmesh.ops.delete call
    # below can hard-crash Blender's native bmesh code on the resulting
    # invalid geometry rather than raising a catchable Python exception.
    tris = [t for t in tris if len(set(t)) == 3]
    if not tris:
        return None

    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    mesh.from_pydata(verts, [], tris)
    mesh.validate(verbose=False)
    mesh.update()

    bpy.context.view_layer.objects.active = obj
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)

    degenerate_faces = [f for f in bm.faces if f.calc_area() < 1e-7]
    if degenerate_faces:
        bmesh.ops.delete(bm, geom=degenerate_faces, context="FACES")

    bm.normal_update()
    if bm.faces and sum(f.normal.z for f in bm.faces) / len(bm.faces) < 0:
        for f in bm.faces:
            f.normal_flip()
        bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()

    obj.name = name
    obj.data.name = name

    # --- ATTACH CANONICAL SHAPELY OUTLINE ---
    obj["map_polygon_wkt"] = polygon.wkt

    return obj


def clean_and_union_geometry(
    geometries, target_size: float = None, min_area_ratio: float = 0.001
):
    from shapely import Polygon, make_valid, normalize, set_precision
    from shapely.affinity import scale as af_scale
    from shapely.affinity import translate as af_trans

    if not isinstance(geometries, (list, tuple)):
        geometries = [geometries]

    # 1. UNPACK all inputs into discrete individual Polygons
    raw_polys = []
    for g in geometries:
        if g is None or g.is_empty:
            continue
        v = make_valid(g)
        if v.is_empty:
            continue

        if isinstance(v, Polygon):
            raw_polys.append(v)
        elif hasattr(v, "geoms"):
            for sub_g in v.geoms:
                if sub_g.geom_type == "Polygon" and not sub_g.is_empty:
                    raw_polys.append(sub_g)

    if not raw_polys:
        return None

    # 2. SCALE raw modules together first
    minx = min(p.bounds[0] for p in raw_polys)
    miny = min(p.bounds[1] for p in raw_polys)
    maxx = max(p.bounds[2] for p in raw_polys)
    maxy = max(p.bounds[3] for p in raw_polys)

    width, height = maxx - minx, maxy - miny
    max_dim = max(width, height)

    scaled_polys = []
    if target_size is not None and max_dim > 0:
        scale_factor = target_size / max_dim
        cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0

        for p in raw_polys:
            p_trans = af_trans(p, xoff=-cx, yoff=-cy)
            p_scaled = af_scale(
                p_trans, xfact=scale_factor, yfact=scale_factor, origin=(0, 0)
            )
            scaled_polys.append(p_scaled)
    else:
        scaled_polys = raw_polys

    # 3. PRE-UNION BUFFER
    target_dim = target_size if target_size is not None else max_dim
    micro_offset = target_dim * 0.0005  # 0.05% weld thickness

    welded_polys = [p.buffer(micro_offset, join_style="bevel") for p in scaled_polys]

    # 4. UNION into a single geometry
    unioned = g2d.union(welded_polys)
    unioned = make_valid(unioned)

    # Snap micro-precision jitter
    unioned = set_precision(unioned, grid_size=1e-5)
    unioned = make_valid(unioned)

    # Extract purely polygonal components
    polys = []
    if isinstance(unioned, Polygon):
        polys = [unioned]
    elif hasattr(unioned, "geoms"):
        polys = [
            p for p in unioned.geoms if p.geom_type == "Polygon" and not p.is_empty
        ]

    if not polys:
        return None

    # Filter out tiny noise islands
    max_area = max(p.area for p in polys)
    filtered_polys = [p for p in polys if p.area >= (max_area * min_area_ratio)]

    if filtered_polys:
        unioned = g2d.union(filtered_polys)
        unioned = make_valid(unioned)

    # 5. SHAVE OFF MITRE SPIKES
    # Tolerance set slightly higher than micro_offset collapses near-collinear nubs on flat edges
    if unioned and not unioned.is_empty:
        clean_tol = micro_offset * 1.5
        unioned = unioned.simplify(clean_tol, preserve_topology=True)
        unioned = make_valid(unioned)

    # Enforce CCW exterior / CW holes winding
    if unioned and not unioned.is_empty:
        unioned = g2d.orient(unioned, sign=1.0)
        unioned = normalize(unioned)

    return unioned


# ── GeoJSON Importer ──────────────────────────────────────────────────────────


def polygon_from_geojson(filepath: str, target_size: float = None):
    """Loads a GeoJSON file using Shapely's C-accelerated parser, projects it
    from lon/lat degrees into local Mercator distance units (same projection
    convert_to_blender_coordinates uses for every other geo-sourced shape in
    this addon -- raw degrees would otherwise stretch the outline east-west,
    since a degree of longitude covers less real ground distance than a
    degree of latitude away from the equator), cleans it, and returns a
    normalized Shapely geometry.
    """
    import numpy as np
    from shapely import GeometryCollection, MultiPolygon, Polygon
    from shapely.io import from_geojson
    from shapely.ops import transform

    from .. import constants as const

    with open(filepath, "r", encoding="utf-8") as f:
        # Parses Geometries, Features, and FeatureCollections directly
        geom = from_geojson(f.read())

    def _project(lon, lat):
        lon = np.asarray(lon, dtype=np.float64)
        lat = np.asarray(lat, dtype=np.float64)
        x = const.R * np.radians(lon)
        y = const.R * np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))
        return x, y

    geom = transform(_project, geom)

    # Extract only polygonal geometry if the GeoJSON contained mixed types (e.g. points/lines)
    if isinstance(geom, GeometryCollection):
        polys = [g for g in geom.geoms if isinstance(g, (Polygon, MultiPolygon))]
        geom = polys if polys else None

    return clean_and_union_geometry(geom, target_size=target_size)


# ── SVG Importer (via Blender SVG curve import) ───────────────────────────────


def polygon_from_svg(filepath: str, target_size: float = None):
    """Imports an SVG, evaluates smooth Bezier geometry via Blender's 2D engine,
    polygonizes raw stroke networks, and returns a clean normalized geometry.
    """
    from shapely import LineString, Polygon, polygonize

    prior_objs = set(bpy.context.scene.objects)

    # 1. Native SVG import
    bpy.ops.import_curve.svg(filepath=filepath)
    imported_objs = [
        o
        for o in bpy.context.scene.objects
        if o not in prior_objs and o.type == "CURVE"
    ]

    if not imported_objs:
        return None

    depsgraph = bpy.context.evaluated_depsgraph_get()
    extracted_polys = []
    edge_lines = []

    for obj in imported_objs:
        # Force 2D dimension so Blender's curve engine handles filled regions & holes
        obj.data.dimensions = "2D"
        obj.data.resolution_u = 4

        # Evaluate curve into mesh to sample true Bezier resolution
        eval_obj = obj.evaluated_get(depsgraph)
        mesh = eval_obj.to_mesh()
        matrix = obj.matrix_world

        verts_2d = [((matrix @ v.co).x, (matrix @ v.co).y) for v in mesh.vertices]

        # Path A: Filled shapes (Blender already solved the 2D face tessellation & holes)
        if len(mesh.polygons) > 0:
            for poly in mesh.polygons:
                face_coords = [verts_2d[idx] for idx in poly.vertices]
                if len(face_coords) >= 3:
                    p = Polygon(face_coords)
                    if p.is_valid and p.area > 1e-12:
                        extracted_polys.append(p)

        # Path B: Unfilled strokes / open paths (gather raw edge network)
        else:
            for edge in mesh.edges:
                p1, p2 = verts_2d[edge.vertices[0]], verts_2d[edge.vertices[1]]
                if p1 != p2:
                    edge_lines.append(LineString([p1, p2]))

        # Clean up temp curve & evaluated mesh data
        eval_obj.to_mesh_clear()
        bpy.data.objects.remove(obj, do_unlink=True)

    # Path B handling: Node arbitrary line soups and stitch them into closed loops
    if edge_lines:
        noded_lines = g2d.union(edge_lines)
        stroke_polys = list(polygonize(noded_lines))
        extracted_polys.extend(stroke_polys)

    if not extracted_polys:
        return None

    # Merge, fix self-intersections, and center/scale
    return clean_and_union_geometry(extracted_polys, target_size=target_size)


def create_hexagon(size, num_subdivisions=1, name="Hexagon"):
    """Creates a hexagon centered at (0,0,0)."""
    cell_size = size / (2**num_subdivisions)
    return build_mesh_from_polygon(hexagon_polygon(size), cell_size, name)


def create_rectangle(width, height, num_subdivisions=1, name="Rectangle"):
    """Creates a rectangle centered at (0,0,0)."""
    cell_size = max(width, height) / (2 ** (num_subdivisions + 1))
    return build_mesh_from_polygon(rectangle_polygon(width, height), cell_size, name)


def create_heart(size, num_subdivisions=1, name="Heart"):
    """Creates a heart-shaped mesh via the exact parametric outline, clipped
    from a regular lattice -- no more extrude+voxel-remesh+flatten pass, and
    no more rounding-off of the heart's sharp cusp/point that the old
    REMESH-based construction produced."""
    cell_size = max(size / (2**num_subdivisions), 0.12)
    return build_mesh_from_polygon(heart_polygon(size), cell_size, name)


def create_circle(radius, num_subdivisions=1, name="Circle", num_segments=64):
    """Creates a circle centered at (0,0,0). num_segments controls boundary
    smoothness independently of num_subdivisions (interior density) -- same
    two-knob split as before, just no more center-fan wedge triangles that
    got tiny near the middle and huge near the rim."""
    cell_size = radius / (2**num_subdivisions)
    return build_mesh_from_polygon(
        circle_polygon(radius, num_segments), cell_size, name
    )


def create_ellipse(
    radius, num_subdivisions=1, name="Ellipse", aspect_ratio=0.75, num_segments=64
):
    """Creates an ellipse centered at (0,0,0)."""
    cell_size = radius / (2**num_subdivisions)
    return build_mesh_from_polygon(
        ellipse_polygon(radius, aspect_ratio, num_segments), cell_size, name
    )


def create_octagon(size, num_subdivisions=1, name="Octagon"):
    """Creates a regular octagon centered at (0,0,0). See octagon_polygon's
    docstring for why it's a bevelled square, not 8 angle-equal points."""
    cell_size = size / (2**num_subdivisions)
    return build_mesh_from_polygon(octagon_polygon(size), cell_size, name)


def create_custom_geojson(filepath, size, num_subdivisions=1, name="GeoJSON_Map"):
    poly = polygon_from_geojson(filepath, target_size=size)
    cell_size = size / (2**num_subdivisions)
    return build_mesh_from_polygon(poly, cell_size, name)


def create_custom_svg(filepath, size, num_subdivisions=1, name="SVG_Map"):
    poly = polygon_from_svg(filepath, target_size=size)
    cell_size = size / (2**num_subdivisions)
    return build_mesh_from_polygon(poly, cell_size, name)


def col_create_line_mesh(name, coords):
    mesh = bpy.data.meshes.new(name)
    tobj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(tobj)

    bm = bmesh.new()
    verts = [bm.verts.new(c) for c in coords]
    for i in range(len(verts) - 1):
        bm.edges.new((verts[i], verts[i + 1]))
    bm.to_mesh(mesh)
    bm.free()
    return tobj


def col_create_face_mesh(name, coords):

    if len(coords) < 3:
        return  # Need at least 3 points for a face

    mesh = bpy.data.meshes.new(name)
    tobj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(tobj)

    bm = bmesh.new()
    verts = [bm.verts.new(c) for c in coords]
    try:
        bm.faces.new(verts)
    except ValueError as e:
        print(e)  # face might already exist or be invalid
    bm.to_mesh(mesh)
    bm.free()
    return tobj


def col_create_line_curve(name, coords, close=False, collection=None, bevel_depth=0.0):
    """
    Create a Curve object with a POLY spline from coords.
    coords: iterable of (x,y) or (x,y,z)
    close: make spline cyclic
    collection: bpy.types.Collection (defaults to context.collection)
    bevel_depth: >0 will give the curve thickness
    """
    if not coords:
        raise ValueError("coords is empty")

    # normalize coords to 3-tuples
    pts = []
    for c in coords:
        if len(c) == 2:
            pts.append((c[0], c[1], 0.0))
        else:
            pts.append((c[0], c[1], c[2]))

    curve_data = bpy.data.curves.new(name + "_curve", type="CURVE")
    curve_data.dimensions = "3D"
    curve_data.resolution_u = 1

    spline = curve_data.splines.new(type="POLY")
    spline.points.add(len(pts) - 1)  # one point exists by default
    for i, (x, y, z) in enumerate(pts):
        spline.points[i].co = (x, y, z, 1.0)

    spline.use_cyclic_u = bool(close)

    if bevel_depth and bevel_depth > 0.0:
        curve_data.bevel_depth = float(bevel_depth)
        curve_data.fill_mode = "FULL"

    obj = bpy.data.objects.new(name, curve_data)
    target_col = collection or bpy.context.collection
    target_col.objects.link(obj)

    return obj


def curve_to_mesh_object(curve_obj, name=None, apply_modifiers=True):
    """
    Create and return a new Mesh object built from `curve_obj` evaluation.
    - curve_obj: bpy.types.Object of type 'CURVE'
    - name: optional name for new object (mesh)
    - apply_modifiers: if True, evaluate modifiers and use new_from_object (recommended)
    """
    if curve_obj.type != "CURVE":
        raise ValueError("curve_obj must be a Curve object")

    mesh_name = name if name else curve_obj.name + "_mesh"
    coll = bpy.context.collection
    depsgraph = bpy.context.evaluated_depsgraph_get()

    if apply_modifiers:
        # Create a real Mesh datablock from the evaluated object (safe to use with objects.new)
        eval_obj = curve_obj.evaluated_get(depsgraph)
        mesh = bpy.data.meshes.new_from_object(
            eval_obj, preserve_all_data_layers=True, depsgraph=depsgraph
        )
        new_obj = bpy.data.objects.new(mesh_name, mesh)
        new_obj.matrix_world = curve_obj.matrix_world.copy()
        coll.objects.link(new_obj)
        return new_obj

    else:
        # Create a temporary evaluated mesh, copy it to a real datablock, then clear the temp
        eval_obj = curve_obj.evaluated_get(depsgraph)
        temp_mesh = eval_obj.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
        # copy to real datablock
        real_mesh = temp_mesh.copy()
        real_mesh.name = mesh_name
        new_obj = bpy.data.objects.new(mesh_name, real_mesh)
        new_obj.matrix_world = curve_obj.matrix_world.copy()
        coll.objects.link(new_obj)
        # free the temporary evaluated mesh
        eval_obj.to_mesh_clear()
        return new_obj
