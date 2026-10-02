"""Generic WKT-driven plate/shell builder.

Replaces the per-shape inline bmesh construction in HexagonOuterText /
OctagonOuterText / MedalText with a single function that reads the map's own
stored outline (`map_obj["map_polygon_wkt"]`) and buffers/extrudes it. A
hexagon, circle, GeoJSON import, or multi-island SVG all go through the same
code path with no shape-specific branches.
"""

import math

import bmesh  # type: ignore
import bpy  # type: ignore
from bpy.app.translations import pgettext_iface as _  # type: ignore
from mathutils import Vector
from shapely import wkt
from shapely.affinity import rotate as shp_rotate
from shapely.affinity import scale as shp_scale

from .. import temp
from . import geometry2d as g2d
from .mesh_ops import recalculateNormals

try:
    from ..premium import utils_pe  # type: ignore
    add_medal_handle = utils_pe.add_medal_handle
except ImportError:
    def add_medal_handle(*_a, **_k):
        return None


# Radially symmetric: a single uniform scale from the centroid offsets every
# edge by the same distance, because inradius and circumradius scale together.
_RADIAL_SHAPES = {"HEXAGON", "OCTAGON", "CIRCLE"}

# Axis-aligned bounding-box shapes. Scaling by (bounds + 2*grow) / bounds
# gives a uniform border on all four sides even when W != H.
_BOX_SHAPES = {"SQUARE", "ELLIPSE"}

# Interior angle at each vertex of a regular N-gon, used to convert the
# desired edge-offset into a circumradius scale factor.
_INRADIUS_COS = {
    "HEXAGON": math.cos(math.pi / 6),  # 30°
    "OCTAGON": math.cos(math.pi / 8),  # 45°
    "CIRCLE": 1.0,  # inradius == radius
}


def measured_inradius(poly, shape=None):
    """Real distance from centre to *this polygon's own* nearest edge,
    measured from its actual bounds rather than a nominal size/outerBorderSize
    setting — same math _expand_outline already uses to grow a plate, just
    read back instead of applied. For a radial shape (HEXAGON/OCTAGON/CIRCLE)
    that's the bounding-box circumradius corrected by the shape's own apothem
    (so e.g. a hexagon's flat-edge distance, not its vertex distance); for
    anything else the circumradius itself is already the edge distance along
    an axis-aligned direction, which is all SQUARE/ELLIPSE's field angles
    (0/90/180/270) ever need.

    This is what lets text-layout anchoring (text_layouts.py) place text
    correctly on *either* a Solid Plate or a Shell: both store their real
    outline as WKT (`plate_wkt` / `shell_outer_wkt`), and this reads whichever
    is actually there instead of assuming the Solid-Plate-only
    outerBorderSize formula applies.
    """
    if poly is None or poly.is_empty:
        return 0.0
    minx, miny, maxx, maxy = poly.bounds
    cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0
    R = max(maxx - cx, maxy - cy)
    if shape in _INRADIUS_COS:
        return R * _INRADIUS_COS[shape]
    return R


def _expand_outline(poly, grow, shape=None):
    """Offset a polygon outward by `grow` while preserving its corner angles.

    Uses centroid scaling for shapes whose every edge is equidistant from
    the centre (so scaling gives a uniform border), per-axis scaling for
    rectangles/ellipses, and mitre-buffer only for arbitrary imports.
    """
    if grow <= 0:
        return poly

    minx, miny, maxx, maxy = poly.bounds
    cx = (minx + maxx) / 2.0
    cy = (miny + maxy) / 2.0

    if shape in _RADIAL_SHAPES:
        # Circumradius == distance from centre to a vertex. For HEXAGON the
        # bounding box is 2R wide and R*sqrt(3) tall, so max() picks R.
        R = max(maxx - cx, maxy - cy)
        if R <= 0:
            return poly
        inradius = R * _INRADIUS_COS[shape]
        if inradius <= 0:
            return poly
        # Scale so the new inradius is old + grow — same edge offset the
        # user asked for, but the corners are never touched.
        k = (inradius + grow) / inradius
        return g2d.validate(shp_scale(poly, xfact=k, yfact=k, origin="centroid"))

    if shape in _BOX_SHAPES:
        w = maxx - minx
        h = maxy - miny
        if w <= 0 or h <= 0:
            return poly
        return g2d.validate(
            shp_scale(
                poly,
                xfact=(w + 2 * grow) / w,
                yfact=(h + 2 * grow) / h,
                origin=(cx, cy),
            )
        )

    # Irregular imports (SVG, GeoJSON, heart).
    return g2d.validate(poly.buffer(grow, join_style="round"))


def _assign_black_material(obj):
    mat = bpy.data.materials.get("BLACK")
    if mat is None:
        # Material library hasn't been initialized yet — call the
        # canonical setup once, then re-query.
        from .primitives import setupColors

        setupColors()
        mat = bpy.data.materials.get("BLACK")
    if mat is None:
        return
    obj.data.materials.clear()
    obj.data.materials.append(mat)


def _shapely_outline(map_obj, shape_rotation=0.0):
    """Return the map's 2D outline (local space) as a Shapely geometry."""
    if map_obj is None or "map_polygon_wkt" not in map_obj:
        return None
    poly = wkt.loads(map_obj["map_polygon_wkt"])
    if poly is None or poly.is_empty:
        return None
    if shape_rotation:
        poly = shp_rotate(poly, shape_rotation, origin=(0, 0))
    return g2d.validate(poly)


def _build_cap(bm, poly, z, flip=False):
    """Tessellate *poly* at height z into bm.

    Returns (verts, ring_idx_lists) or None. Ring index lists come straight
    from _cdt_triangulate, so they already account for any coordinate dedup.
    """
    ext = [(x, y) for x, y in list(poly.exterior.coords)[:-1]]
    holes = [[(x, y) for x, y in list(r.coords)[:-1]] for r in poly.interiors]
    if len(ext) < 3:
        return None
    ec = g2d._cdt_triangulate(poly, ext, holes)
    if ec is None:
        return None
    verts2d, tris, ring_idx_lists = ec
    vs = [bm.verts.new((x, y, z)) for x, y in verts2d]
    bm.verts.ensure_lookup_table()
    for a, b, c in tris:
        tri = (vs[c], vs[b], vs[a]) if flip else (vs[a], vs[b], vs[c])
        try:
            bm.faces.new(tri)
        except ValueError:
            pass
    return vs, ring_idx_lists


def _build_prism(polygon, top_z, bottom_z, name):
    """Extrude a Shapely Polygon *or* MultiPolygon into a closed prism.

    MultiPolygon parts (each possibly with its own holes) each get a
    separate cap pair and side-wall loop, all written into one shared
    bmesh — so a map whose WKT has separate islands produces one plate
    object containing all islands, not N objects.
    """
    bm = bmesh.new()

    if hasattr(polygon, "geoms"):
        parts = [p for p in polygon.geoms if isinstance(p, g2d.Polygon)]
    else:
        parts = [polygon]

    top_data = []
    bot_data = []
    for part in parts:
        if part is None or part.is_empty:
            continue
        tr = _build_cap(bm, part, top_z, flip=False)
        br = _build_cap(bm, part, bottom_z, flip=True)
        if tr is None or br is None:
            bm.free()
            return None
        top_data.append(tr)
        bot_data.append(br)

    if not top_data:
        bm.free()
        return None

    # Same polygon fed to both caps means identical verts2d layout per
    # part, so the top ring indices are valid against the bottom verts.
    for (top_verts, top_rings), (bot_verts, _bot_rings) in zip(top_data, bot_data):
        for ring in top_rings:
            n = len(ring)
            for i in range(n):
                a = ring[i]
                b = ring[(i + 1) % n]
                try:
                    bm.faces.new(
                        (top_verts[a], top_verts[b], bot_verts[b], bot_verts[a])
                    )
                except ValueError:
                    pass

    # Merge coplanar cap triangles into clean N-gons per ring.
    bm.edges.ensure_lookup_table()
    cap_edges = []
    for e in bm.edges:
        zs = [v.co.z for v in e.verts]
        if all(abs(z - top_z) < 1e-5 for z in zs) or all(
            abs(z - bottom_z) < 1e-5 for z in zs
        ):
            cap_edges.append(e)
    if cap_edges:
        try:
            bmesh.ops.dissolve_limit(
                bm,
                angle_limit=0.01,
                verts=bm.verts[:],
                edges=cap_edges,
            )
        except Exception as exc:
            print(f"[TrailPrint3D] plate cap dissolve_limit skipped: {exc!r}")

    bm.normal_update()
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return obj

def _limited_dissolve(obj, angle_limit=0.01):
    """Dissolve edges in the bmesh that are below the angle limit."""
    try:
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.edges.ensure_lookup_table()
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        bm.normal_update()
        bmesh.ops.dissolve_limit(
            bm,
            angle_limit=angle_limit,
            verts=bm.verts[:],
            edges=[e for e in bm.edges if e.is_valid],
        )
        bm.to_mesh(obj.data)
        bm.free()
    except Exception as exc:
        print(f"[TrailPrint3D] limited dissolve skipped: {exc!r}")


def _apply_perimeter_bevel(obj, bevel_amount, top_z, bottom_z):
    """Bevel ONLY the outer top and bottom perimeter of a plate.

    An edge is eligible if it sits at a cap height AND is the boundary
    between a cap face and a wall whose outward normal points away from
    the plate's XY centroid. That last test is what excludes hole rims
    and the shell cavity's inner rim — those walls face inward, so their
    edges don't qualify.

    clamp_overlap=True is essential. Without it, bmesh.ops.bevel pushes
    the offset through adjacent geometry at sharp corners; on a square,
    two beveled edges meeting at 90° compound their offsets diagonally
    and fire the corner outward past the original wall — the "flared
    brim with inset side" look.

    Callers MUST ensure the mesh has consistent face winding before
    calling this (recalculateNormals beforehand) — the outward test
    depends on face normals actually pointing outward.
    """
    if bevel_amount <= 0:
        return


    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.edges.ensure_lookup_table()
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.normal_update()

    if not bm.verts:
        bm.free()
        return

    cx = sum(v.co.x for v in bm.verts) / len(bm.verts)
    cy = sum(v.co.y for v in bm.verts) / len(bm.verts)

    z_tol = 1e-4

    def _at_cap_height(e, z):
        return all(abs(v.co.z - z) < z_tol for v in e.verts)

    def _wall_face(e):
        """The linked face that spans the plate thickness (not a cap)."""
        for f in e.link_faces:
            zs = [v.co.z for v in f.verts]
            if (max(zs) - min(zs)) > abs(top_z - bottom_z) * 0.5:
                return f
        return None

    def _faces_outward(e, wall):
        mid = (e.verts[0].co + e.verts[1].co) / 2
        # 3D radial vector with z=0 so it matches wall.normal's dimensions.
        # mathutils.Vector.dot requires both operands to be the same size.
        radial = Vector((mid.x - cx, mid.y - cy, 0.0))
        if radial.length < 1e-9:
            return True   # degenerate midpoint; be permissive
        return wall.normal.dot(radial.normalized()) > 0.0

    bevel_edges = []
    for e in bm.edges:
        if not (_at_cap_height(e, top_z) or _at_cap_height(e, bottom_z)):
            continue
        if len(e.link_faces) != 2:
            continue
        wall = _wall_face(e)
        if wall is None:
            continue
        if not _faces_outward(e, wall):
            continue
        bevel_edges.append(e)

    print(
        f"[TrailPrint3D] plate bevel: {len(bevel_edges)} outer-perimeter "
        f"edge(s) selected (amount={bevel_amount})"
    )

    if bevel_edges:
        try:
            bmesh.ops.bevel(
                bm,
                geom=bevel_edges,
                offset=bevel_amount,
                offset_type="OFFSET",
                segments=1,
                profile=0.5,
                affect="EDGES",
                clamp_overlap=True,
            )
            bm.to_mesh(obj.data)
            obj.data.update()
        except Exception as exc:
            print(f"[TrailPrint3D] bevel failed, skipping: {exc!r}")

    bm.free()


def _boolean_difference(target, cutter):
    bpy.ops.object.select_all(action="DESELECT")
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    mod = target.modifiers.new(name="Boolean", type="BOOLEAN")
    mod.operation = "DIFFERENCE"
    mod.solver = "MANIFOLD"
    mod.object = cutter
    bpy.ops.object.modifier_apply(modifier=mod.name)


def _store_wkt_metadata(obj, plate_wkt, insert_wkt, mode):
    obj["plate_wkt"] = plate_wkt
    obj["insert_wkt"] = insert_wkt
    obj["plateMode"] = mode
    obj.data["plate_wkt"] = plate_wkt
    obj.data["insert_wkt"] = insert_wkt


def create_generic_plate(
    map_obj,
    mode,
    *,
    shape=None,
    outer_border_pct=20.0,
    thickness=5.0,
    bevel=0.0,
    shape_rotation=0.0,
    tolerance=0.1,
    wall=2.0,
    bottom_wall=1.0,
    name=None,
) -> bpy.types.Object | None:
    """Build a plate or shell for *any* map shape from its stored WKT.

    mode = "SOLID_PLATE" : buffered slab, top at z=0, bottom at -thickness.
    mode = "SHELL"       : hollow tray — outer wall buffered by tolerance+wall,
                           inner wall buffered by tolerance, floor at
                           -thickness + bottom_wall.

    Returns the new object at the local origin (caller positions it), or
    None on degenerate input.
    """
    poly = _shapely_outline(map_obj, shape_rotation)
    if poly is None:
        return None

    size = bpy.context.scene.tp3d.objSize
    base_name = name or (map_obj.name + "_Plate")

    if mode == "SOLID_PLATE":
        grow = size * outer_border_pct / 200.0
        outer_poly = _expand_outline(poly, grow, shape=shape)
        if outer_poly is None or outer_poly.is_empty:
            return None

        plate_obj = _build_prism(outer_poly, 0.0, -thickness, base_name)
        if plate_obj is None:
            return None
        recalculateNormals(plate_obj)
        _limited_dissolve(plate_obj, angle_limit=0.01)
        _apply_perimeter_bevel(plate_obj, bevel, 0.0, -thickness)
        recalculateNormals(plate_obj)
        handle_style = bpy.context.scene.tp3d.handleStyle
        if handle_style != "NONE" and temp.PREMIUMVERSION:
            add_medal_handle(
                plate_obj,
                thickness,
                handle_style,
                bevel,
            )

        _store_wkt_metadata(plate_obj, outer_poly.wkt, poly.wkt, mode)
        plate_obj.name = base_name
        plate_obj.data.name = base_name
        _assign_black_material(plate_obj)
        plate_obj["Object type"] = "PLATE"
        plate_obj["ExportGroup"] = 2
        return plate_obj

    if mode == "SHELL":
        outer_poly = _expand_outline(poly, tolerance + wall, shape=shape)
        inner_poly = _expand_outline(poly, tolerance, shape=shape)
        if outer_poly is None or outer_poly.is_empty:
            return None
        if inner_poly is None or inner_poly.is_empty:
            return None

        outer_obj = _build_prism(outer_poly, 0.0, -thickness, base_name)
        if outer_obj is None:
            return None

        # Cutter extends above the top so the difference fully opens the
        # cavity, and stops short of the bottom to leave a floor.
        cutter_obj = _build_prism(
            inner_poly,
            thickness,
            -thickness + bottom_wall,
            base_name + "_Cutter",
        )
        if cutter_obj is None:
            bpy.data.objects.remove(outer_obj, do_unlink=True)
            return None

        _boolean_difference(outer_obj, cutter_obj)
        bpy.data.objects.remove(cutter_obj, do_unlink=True)

        recalculateNormals(outer_obj)
        _apply_perimeter_bevel(outer_obj, bevel, 0.0, -thickness)
        recalculateNormals(outer_obj)

        _store_wkt_metadata(outer_obj, outer_poly.wkt, inner_poly.wkt, mode)
        outer_obj.name = base_name
        outer_obj.data.name = base_name
        _assign_black_material(outer_obj)
        outer_obj["Object type"] = "SHELL"
        outer_obj["ExportGroup"] = 2
        return outer_obj

    return None
