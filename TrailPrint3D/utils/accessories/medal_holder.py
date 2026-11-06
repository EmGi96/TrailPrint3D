#  Copyright (C) 2026  EmGi
"""Medal holder plate -- a flat shape (round/hexagon/octagon/square) with
three ribbon slots cut through it, a round pocket in its back face, optional
dovetail recesses on chosen edges and optional raised text above/below the
medal. Built lying flat (front face up at z = thickness, display "up" = +Y),
shared by the Medal Holder Generator's live 3D preview (write_glb) and its
final "Send to Blender" result, so the preview is always the exact mesh that
ends up in the scene.
"""
import json
import math
import struct

import bpy  # type: ignore
from bpy.app.translations import pgettext_rpt as _rpt
from shapely.geometry import LineString, Point

from .. import geometry2d as g2d
from ..magnet_layout import place_on_rays
from ..mesh_ops import _extrude_flat_polygon, applyModifier
from ..primitives import (
    circle_polygon,
    hexagon_polygon,
    octagon_polygon,
    rectangle_polygon,
)

SHAPES = ('ROUND', 'HEXAGON', 'OCTAGON', 'SQUARE')

DEFAULTS = {
    'shape': 'HEXAGON',
    'objSize': 150.0,
    'thickness': 10.0,
    'cutoutWidth': 35.0,
    'slotHeight': 3.0,
    # Same defaults as the scene's Magnet Diameter/Height (props.py); the
    # page starts from the scene's current values.
    'magnetDiameter': 6.3,
    'magnetDepth': 2.5,
}

# Medal diameter as a fraction of the plate's size (objSize).
MEDAL_SIZE_RATIO = 2 / 3

# Slot centers as a fraction of the medal's radius: two near the top of the
# medal area (the ribbon goes in through one and back out the other), one a bit
# below the middle (the ribbon's lower loop holds the medal in place).
_SLOT_Y_FACTORS = (0.76, 0.49, -0.50)

# Back-face pocket: its diameter is medalSize minus this (mm), and it stops
# this far (mm) short of the front face.
_BACK_POCKET_INSET = 6.0
_BACK_POCKET_FLOOR = 1.0

# Preview-only colors: the plate's scene material is pure BLACK, which
# renders as a featureless silhouette in the preview.
_PREVIEW_PLATE_RGBA = (0.05, 0.05, 0.055, 1.0)


def sanitize_params(raw: dict | None) -> dict:
    """Clamp page-supplied values into buildable ranges (the page is a
    browser form -- never trust it to have done so)."""
    raw = raw or {}
    p = dict(DEFAULTS)

    def _num(key, lo, hi):
        try:
            val = float(raw.get(key, p[key]))
        except (TypeError, ValueError):
            val = p[key]
        if not math.isfinite(val):
            val = p[key]
        p[key] = max(lo, min(hi, val))

    shape = str(raw.get('shape', p['shape'])).upper()
    p['shape'] = shape if shape in SHAPES else DEFAULTS['shape']
    _num('objSize', 10.0, 500.0)
    _num('thickness', 0.6, 50.0)
    # Not a user setting: the medal area always scales with the plate.
    p['medalSize'] = p['objSize'] * MEDAL_SIZE_RATIO
    _num('cutoutWidth', 1.0, p['objSize'])
    _num('slotHeight', 0.4, 20.0)

    n_spots = len(_dovetail_angles(p['shape']))
    dovetails = raw.get('dovetails')
    p['dovetails'] = sorted({
        int(i) for i in (dovetails if isinstance(dovetails, list) else [])
        if isinstance(i, (int, float)) and 0 <= int(i) < n_spots
    })

    texts = raw.get('texts') if isinstance(raw.get('texts'), dict) else {}
    p['texts'] = {slot: _sanitize_text(texts.get(slot)) for slot in TEXT_SLOTS}

    p['magnets'] = raw.get('magnets') is True
    _num('magnetDiameter', 1.0, 30.0)
    _num('magnetDepth', 0.2, max(0.2, p['thickness'] - _BACK_POCKET_FLOOR))
    keyholes = str(raw.get('keyholes') or 'single').lower()
    # Magnets and keyholes are either/or (the page greys keyholes out while
    # magnets are on) -- enforced here too.
    p['keyholes'] = keyholes if keyholes in KEYHOLE_MODES and not p['magnets'] else 'none'
    return p


# ── Text ───────────────────────────────────────────────────────────────────
# Raised text on the front face, in the free band above ('top') or below
# ('bottom') the medal circle. A separate object so it can print in its own
# color. Shrunk as needed to fit its band; never grown past the asked size.
TEXT_SLOTS = ('top', 'bottom')
_TEXT_MARGIN = 2.0          # mm kept clear of the medal circle and plate edge
_TEXT_MAX_CHARS = 60
_PREVIEW_TEXT_RGBA = (0.9, 0.9, 0.9, 1.0)


def _sanitize_text(raw):
    """{'text', 'size', 'relief', 'font'} or None (no text in that slot)."""
    if not isinstance(raw, dict):
        return None
    text = ' '.join(str(raw.get('text') or '').split())[:_TEXT_MAX_CHARS]
    if not text:
        return None

    def _num(key, default, lo, hi):
        try:
            val = float(raw.get(key, default))
        except (TypeError, ValueError):
            val = default
        return max(lo, min(hi, val if math.isfinite(val) else default))

    # Only fonts Blender itself listed for the page -- the path comes from
    # the browser, so it's never trusted as an arbitrary file to open.
    from .. import font_list
    font = str(raw.get('font') or '')
    if font and font not in {f['path'] for f in font_list.list_fonts()}:
        font = ''
    return {
        'text': text,
        'size': _num('size', 8.0, 1.0, 100.0),
        'relief': _num('relief', 1.0, 0.2, 10.0),
        'font': font,
    }


def _half_height(p):
    """Distance from the center to the plate's top/bottom edge."""
    half = p['objSize'] / 2
    return half * math.sqrt(3) / 2 if p['shape'] == 'HEXAGON' else half


def text_band(p):
    """(inner, outer): distances from the center that bound both text bands
    -- clear of the medal circle and the plate's top/bottom edge."""
    return p['medalSize'] / 2 + _TEXT_MARGIN, _half_height(p) - _TEXT_MARGIN


def text_spots(p):
    """Where the page's text markers go, and how tall each band is."""
    inner, outer = text_band(p)
    room = max(0.0, outer - inner)
    return {
        slot: {'x': 0.0, 'y': (1 if slot == 'top' else -1) * (inner + outer) / 2, 'room': room}
        for slot in TEXT_SLOTS
    }


def _fit_text(p, slot, w, h, size):
    """(scale, y_center) placing a w x h text block (at scale 1) as large as
    possible up to *size* tall, fully inside the plate's outline (minus
    _TEXT_MARGIN) and within its band. None if it can't fit at all.

    Centered in the band when that fits; otherwise pulled in against the
    medal side, where hexagon/octagon/round plates are wider."""
    from shapely.geometry import box

    inner, outer = text_band(p)
    band_h = outer - inner
    if band_h <= 0.5:
        return None
    allowed = _outline(p).buffer(-_TEXT_MARGIN, join_style='mitre')
    sign = 1.0 if slot == 'top' else -1.0

    def _rect(scale, anchor_inner):
        tw, th = w * scale, h * scale
        lo = inner if anchor_inner else inner + (band_h - th) / 2
        y0, y1 = sign * lo, sign * (lo + th)
        return box(-tw / 2, min(y0, y1), tw / 2, max(y0, y1))

    def _fits(scale, anchor_inner):
        return allowed.contains(_rect(scale, anchor_inner))

    hi = min(size, band_h) / h
    best = None
    for anchor_inner in (False, True):
        if _fits(hi, anchor_inner):
            best = (hi, anchor_inner)
            break
    if best is None:
        # Largest scale that still fits against the medal side.
        lo_s, hi_s = 0.0, hi
        for _ in range(24):
            mid = (lo_s + hi_s) / 2
            if _fits(mid, True):
                lo_s = mid
            else:
                hi_s = mid
        if lo_s * h < 0.5:
            return None
        best = (lo_s, True)
    scale, anchor_inner = best
    r = _rect(scale, anchor_inner)
    return scale, (r.bounds[1] + r.bounds[3]) / 2


def _load_font(path):
    from ..text_objects import default_font_path
    for candidate in (path, default_font_path()):
        if candidate:
            try:
                return bpy.data.fonts.load(candidate, check_existing=True)
            except (RuntimeError, OSError):
                continue
    return bpy.data.fonts.load('<builtin>', check_existing=True)


def _text_object(p, slot, collection, name):
    """Mesh object of *slot*'s text sitting on the front face, or None if
    there is no text or no room for it."""
    from mathutils import Matrix  # type: ignore

    spec = p['texts'].get(slot)
    if spec is None:
        return None

    curve = bpy.data.curves.new(name, type='FONT')
    curve.body = spec['text']
    curve.font = _load_font(spec['font'])
    curve.align_x = 'CENTER'
    curve.align_y = 'CENTER'
    curve.size = 1.0
    curve.extrude = spec['relief'] / 2  # extrudes both ways from z=0
    curve_obj = bpy.data.objects.new(name, curve)
    collection.objects.link(curve_obj)
    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        mesh = bpy.data.meshes.new_from_object(curve_obj.evaluated_get(depsgraph))
    finally:
        bpy.data.objects.remove(curve_obj, do_unlink=True)
        bpy.data.curves.remove(curve)
    if not mesh.vertices:
        bpy.data.meshes.remove(mesh)
        return None

    xs = [v.co.x for v in mesh.vertices]
    ys = [v.co.y for v in mesh.vertices]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    fit = _fit_text(p, slot, w, h, spec['size']) if w > 0 and h > 0 else None
    if fit is None:
        bpy.data.meshes.remove(mesh)
        return None
    scale, y_center = fit
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    # Center the measured letters in the band (not the font's own baseline
    # box), scale only X/Y so the relief stays as asked, and stand the text
    # on the front face.
    mesh.transform(
        Matrix.Translation((0.0, y_center, p['thickness'] + spec['relief'] / 2))
        @ Matrix.Diagonal((scale, scale, 1.0, 1.0))
        @ Matrix.Translation((-cx, -cy, 0.0))
    )
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    return obj


# ── Dovetails ──────────────────────────────────────────────────────────────
# Same sizing/shape rules as operators.dovetail_cutout (the map tiles' own
# "Add Dovetail Cutouts"), so medal holders join with the same printed keys:
# a triangular recess cut up into the back face, one tip pointing out through
# the edge, widening inward, top scaled 1.05x for a slight taper.
_DOVETAIL_HEIGHT = 3.0
_DOVETAIL_TAPER = 1.05


def dovetail_size(obj_size):
    if obj_size <= 50:
        return 5.0
    if obj_size <= 75:
        return 10.0
    return 15.0


def _dovetail_angles(shape):
    """Outward normal angle (radians) of every edge a dovetail can sit on."""
    if shape == 'HEXAGON':
        return [math.radians(30 + 60 * i) for i in range(6)]
    if shape == 'SQUARE':
        return [math.radians(90 * i) for i in range(4)]
    # OCTAGON: one per flat edge. ROUND has no edges -- same 8 directions.
    return [math.radians(45 * i) for i in range(8)]


def _inradius(p):
    half = p['objSize'] / 2
    # 0.866 (not sqrt(3)/2) to match dovetail_cutout exactly.
    return half * 0.866 if p['shape'] == 'HEXAGON' else half


def dovetail_height(p):
    """dovetail_cutout's fixed 3 mm, capped so a thin plate keeps at least
    _BACK_POCKET_FLOOR of material in front of the recess. 0 = no room."""
    return max(0.0, min(_DOVETAIL_HEIGHT, p['thickness'] - _BACK_POCKET_FLOOR))


def dovetail_spots(p):
    """Every possible dovetail position, for the preview's clickable markers:
    [{'index', 'x', 'y', 'angle'}] with x/y on the edge's midpoint."""
    r = _inradius(p)
    return [
        {'index': i, 'x': math.cos(a) * r, 'y': math.sin(a) * r, 'angle': a}
        for i, a in enumerate(_dovetail_angles(p['shape']))
    ]


def _dovetail_cutter_geometry(p):
    """(verts, faces) of one tapered triangular prism per chosen dovetail."""
    size = dovetail_size(p['objSize'])
    height = dovetail_height(p)
    center_r = _inradius(p) - size / 2
    angles = _dovetail_angles(p['shape'])
    # Extended 1 mm below the back face (keeping the same taper slope) so the
    # boolean never meets a coplanar face; z=0..height matches dovetail_cutout.
    z_lo, z_hi = -1.0, height
    slope = (_DOVETAIL_TAPER - 1.0) / height

    verts, faces = [], []
    for idx in p['dovetails']:
        a = angles[idx]
        cx, cy = math.cos(a) * center_r, math.sin(a) * center_r
        base = len(verts)
        for z in (z_lo, z_hi):
            scale = 1.0 + slope * z
            for k in range(3):
                t = a + math.radians(120 * k)  # k=0: tip pointing outward
                verts.append((cx + math.cos(t) * size * scale, cy + math.sin(t) * size * scale, z))
        faces.append((base + 2, base + 1, base + 0))       # bottom
        faces.append((base + 3, base + 4, base + 5))       # top
        for k in range(3):
            j = (k + 1) % 3
            faces.append((base + k, base + j, base + 3 + j, base + 3 + k))
    return verts, faces


def _outline(p):
    half = p['objSize'] / 2
    shape = p['shape']
    if shape == 'ROUND':
        return circle_polygon(half, 128)
    if shape == 'HEXAGON':
        return hexagon_polygon(half)
    if shape == 'OCTAGON':
        return octagon_polygon(half)
    return rectangle_polygon(p['objSize'], p['objSize'])


def build_polygon(p):
    """The plate's Shapely outline (slots already cut) for sanitized params *p*."""
    return _outline(p).difference(_slots(p))


def _slots(p):
    """The three ribbon slots, as one Shapely geometry."""
    r = p['medalSize'] / 2
    slot_len = p['cutoutWidth']
    slot_h = p['slotHeight']
    # Stadium slots: the straight segment plus the round caps add up to the
    # requested cutout width.
    half_seg = max(0.0, (slot_len - slot_h) / 2)
    slots = None
    for fy in _SLOT_Y_FACTORS:
        y = fy * r
        if half_seg > 0:
            s = LineString([(-half_seg, y), (half_seg, y)]).buffer(slot_h / 2, quad_segs=12)
        else:
            s = Point(0, y).buffer(slot_h / 2, quad_segs=12)
        slots = s if slots is None else slots.union(s)
    return slots


# ── Wall mounting ──────────────────────────────────────────────────────────
# Magnet holes and keyholes, both cut into the back face. Neither has a fixed
# spot: each is placed automatically where it fits, clear of the back pocket,
# the slots, the chosen dovetails (and magnets clear of the keyholes), with
# _MOUNT_WALL of material around it.
KEYHOLE_MODES = ('none', 'single', 'double')
_MOUNT_WALL = 1.5
# Keyhole: the screw head goes in through the entry hole, then slides up by
# _KEYHOLE_TRAVEL behind a _KEYHOLE_LIP-thick lip whose slot only lets the
# shaft through. _BACK_POCKET_FLOOR of material stays in front of it.
_KEYHOLE_HEAD_D = 9.0
_KEYHOLE_SHAFT_W = 4.5
_KEYHOLE_TRAVEL = 8.0
_KEYHOLE_LIP = 1.5
_KEYHOLE_MIN_HEAD_ROOM = 1.5


def keyholes_possible(p):
    """Thick enough for lip + room for a screw head + front floor?"""
    return p['thickness'] - _BACK_POCKET_FLOOR - _KEYHOLE_LIP >= _KEYHOLE_MIN_HEAD_ROOM


def _magnet_angles(shape):
    """Directions the 4 magnets are pushed out along -- between the edges a
    dovetail can sit on (see _dovetail_angles), so they never compete for
    the same spot."""
    if shape == 'SQUARE':
        return [math.radians(a) for a in (45, 135, 225, 315)]      # corners
    if shape == 'HEXAGON':
        return [math.radians(a) for a in (60, 120, 240, 300)]      # corners
    return [math.radians(a) for a in (67.5, 112.5, 247.5, 292.5)]  # octagon/round


def _dovetail_footprint(p):
    """2D footprint of the chosen dovetails (at their widest, tapered top)."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    size = dovetail_size(p['objSize']) * _DOVETAIL_TAPER
    center_r = _inradius(p) - dovetail_size(p['objSize']) / 2
    angles = _dovetail_angles(p['shape'])
    polys = []
    for idx in p['dovetails']:
        a = angles[idx]
        cx, cy = math.cos(a) * center_r, math.sin(a) * center_r
        polys.append(Polygon([
            (cx + math.cos(a + math.radians(120 * k)) * size, cy + math.sin(a + math.radians(120 * k)) * size)
            for k in range(3)
        ]))
    return unary_union(polys) if polys else None


def _keyhole_shapes(x, y_entry):
    """(lip_layer, deep_layer) footprints of one keyhole whose entry hole is
    centered at (x, y_entry) and whose slot runs straight up from it."""
    r = _KEYHOLE_HEAD_D / 2
    y_rest = y_entry + _KEYHOLE_TRAVEL
    entry = Point(x, y_entry).buffer(r, quad_segs=16)
    # Lip layer: entry hole + a slot just wide enough for the screw's shaft.
    lip = entry.union(LineString([(x, y_entry), (x, y_rest)]).buffer(_KEYHOLE_SHAFT_W / 2, quad_segs=12))
    # Deep layer: the head's full width all the way up, so it can slide.
    deep = LineString([(x, y_entry), (x, y_rest)]).buffer(r, quad_segs=16)
    return lip, deep


_mount_cache = {'key': None, 'layout': None}


def mount_layout(p):
    """{'keyholes': [(x, y_entry)], 'magnets': [(x, y)]} -- where the wall
    mounting features actually fit. Fewer than asked for (even none) when
    the plate has no room left for them. The last result is cached: both
    the preview's info and its model need it for the same params."""
    key = json.dumps({k: p[k] for k in (
        'shape', 'objSize', 'thickness', 'medalSize', 'cutoutWidth', 'slotHeight',
        'dovetails', 'magnets', 'magnetDiameter', 'keyholes')}, sort_keys=True)
    if _mount_cache['key'] != key:
        _mount_cache['layout'] = _compute_mount_layout(p)
        _mount_cache['key'] = key
    return _mount_cache['layout']


def _compute_mount_layout(p):
    from shapely.ops import unary_union
    from shapely.prepared import prep

    layout = {'keyholes': [], 'magnets': []}
    wants_keyholes = {'none': 0, 'single': 1, 'double': 2}[p['keyholes']]
    if not wants_keyholes and not p['magnets']:
        return layout

    blocked = [_slots(p).buffer(_MOUNT_WALL)]
    dovetails = _dovetail_footprint(p)
    if dovetails is not None:
        blocked.append(dovetails.buffer(_MOUNT_WALL))
    pocket_r = (p['medalSize'] - _BACK_POCKET_INSET) / 2
    pocket = Point(0, 0).buffer(pocket_r, quad_segs=32) if pocket_r > 0 else None
    free = _outline(p).buffer(-_MOUNT_WALL, join_style='mitre')

    if wants_keyholes and keyholes_possible(p):
        # The pocket is open to the back just like a keyhole's entry hole,
        # so a keyhole may start inside it: the screw head goes in through
        # the pocket and slides up under the lip into solid material. Only
        # the part outside the pocket needs room, and the head's resting
        # spot must be fully backed by lip (clear of the pocket).
        solid = prep(free.difference(unary_union(blocked)))
        top = _half_height(p)
        rest_clear = _KEYHOLE_HEAD_D / 2 + 1.0

        def fits(x, y_entry):
            deep = _keyhole_shapes(x, y_entry)[1]
            if pocket is not None:
                if Point(x, y_entry + _KEYHOLE_TRAVEL).distance(pocket) < rest_clear:
                    return False
                deep = deep.difference(pocket)
            return solid.contains(deep)

        # Single: centered. Double: a mirrored pair, searched across widths.
        xs = [0.0] if wants_keyholes == 1 else [
            p['objSize'] * f / 100 for f in range(12, 43)]
        best = None
        for x in xs:
            # Highest spot first, down to the plate's middle -- a hanging
            # point below the center of mass would let the plate tip over.
            y = top - _KEYHOLE_TRAVEL
            while y >= 0:
                if fits(x, y) and (x == 0 or fits(-x, y)):
                    if best is None or y >= best[1]:
                        best = (x, y)
                    break
                y -= 0.5
        if best is not None:
            x, y = best
            layout['keyholes'] = [(0.0, y)] if x == 0 else [(-x, y), (x, y)]
            blocked += [_keyhole_shapes(kx, ky)[1].buffer(_MOUNT_WALL) for kx, ky in layout['keyholes']]

    if p['magnets']:
        if pocket is not None:
            blocked.append(pocket.buffer(_MOUNT_WALL))
        allowed = prep(free.difference(unary_union(blocked)))
        mr = p['magnetDiameter'] / 2
        # As far out as it fits along each direction, for a stable spread.
        layout['magnets'] = place_on_rays(
            lambda x, y: allowed.contains(Point(x, y).buffer(mr, quad_segs=16)),
            _magnet_angles(p['shape']),
            p['objSize'] / 2,
        )
    return layout


def _polygon_parts(geom):
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == 'Polygon':
        return [geom]
    return [g for g in getattr(geom, 'geoms', []) if g.geom_type == 'Polygon']


def _prism_object(name, geom, bottom_z, top_z, collection):
    verts, faces = [], []
    for part in _polygon_parts(geom):
        _extrude_flat_polygon(g2d, part, bottom_z, top_z, verts, faces)
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.validate(verbose=False)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    return obj


def _ensure_material(name):
    if name not in bpy.data.materials:
        # Scene with no map yet -- setupColors is the one place that defines
        # these, and creating BLACK and WHITE together means a Backplate
        # built later (which only checks for BLACK) still finds its WHITE.
        from ..primitives import setupColors
        setupColors()
    return bpy.data.materials.get(name)


def _subtract(target, cutter_geom, bottom_z, top_z, collection, label):
    """Boolean-subtract a flat prism of *cutter_geom* from *target*."""
    cutter = _prism_object(f"{target.name}_{label}", cutter_geom, bottom_z, top_z, collection)
    _subtract_object(target, cutter, label)


def _subtract_object(target, cutter, label):
    """Boolean-subtract (then delete) the mesh object *cutter* from *target*."""
    mod = target.modifiers.new(name=label, type='BOOLEAN')
    mod.object = cutter
    mod.operation = 'DIFFERENCE'
    mod.solver = 'MANIFOLD'
    applyModifier(target, mod)
    cutter_mesh = cutter.data
    bpy.data.objects.remove(cutter, do_unlink=True)
    bpy.data.meshes.remove(cutter_mesh)


def build_object(p, collection, name='MedalHolder'):
    """Create the plate as a mesh object in *collection* and return it."""
    t = p['thickness']
    plate = _prism_object(name, build_polygon(p), 0.0, t, collection)

    # Round pocket in the back face (the medal sits in it), leaving
    # _BACK_POCKET_FLOOR of material at the front. The cutter starts below
    # z=0 so the boolean never has to resolve coplanar faces.
    pocket_r = (p['medalSize'] - _BACK_POCKET_INSET) / 2
    pocket_top = t - _BACK_POCKET_FLOOR
    if pocket_r > 0 and pocket_top > 0:
        pocket = Point(0, 0).buffer(pocket_r, quad_segs=48)
        _subtract(plate, pocket, -1.0, pocket_top, collection, "BackPocket")

    if p['dovetails'] and dovetail_height(p) > 0:
        verts, faces = _dovetail_cutter_geometry(p)
        mesh = bpy.data.meshes.new(f"{name}_Dovetails")
        mesh.from_pydata(verts, [], faces)
        mesh.update()
        cutter = bpy.data.objects.new(mesh.name, mesh)
        collection.objects.link(cutter)
        _subtract_object(plate, cutter, "DovetailCutout")

    layout = mount_layout(p)
    if layout is None:
        raise ValueError(_rpt("Invalid mount layout"))
    if layout['magnets']:
        holes = [Point(x, y).buffer(p['magnetDiameter'] / 2, quad_segs=24) for x, y in layout['magnets']]
        _subtract(plate, _union(holes), -1.0, p['magnetDepth'], collection, "MagnetHoles")
    if layout['keyholes']:
        shapes = [_keyhole_shapes(x, y) for x, y in layout['keyholes']]
        # Lip layer runs a hair into the deep layer so the two cuts overlap
        # instead of meeting at a coplanar face.
        _subtract(plate, _union([s[0] for s in shapes]), -1.0, _KEYHOLE_LIP + 0.05, collection, "KeyholeLip")
        _subtract(plate, _union([s[1] for s in shapes]), _KEYHOLE_LIP,
                  t - _BACK_POCKET_FLOOR, collection, "KeyholeHead")
    return plate


def _union(geoms):
    from shapely.ops import unary_union
    return unary_union(geoms)


def build_objects(p, collection, name='MedalHolder'):
    """The plate plus one object per text slot that has text.
    Returns (plate, [(slot, text object)])."""
    plate = build_object(p, collection, name=name)
    texts = []
    for slot in TEXT_SLOTS:
        obj = _text_object(p, slot, collection, f"{name}_Text_{slot.capitalize()}")
        if obj is not None:
            texts.append((slot, obj))
    return plate, texts


def remove_object(obj):
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def assign_scene_materials(plate, texts):
    plate.data.materials.clear()
    plate.data.materials.append(_ensure_material("BLACK"))
    for _slot, obj in texts:
        obj.data.materials.clear()
        obj.data.materials.append(_ensure_material("WHITE"))


def write_glb(parts, path):
    """Write [(mesh_obj, rgba, extras), ...] as a minimal binary glTF: one
    flat-shaded triangle primitive per object (per-triangle normals, no index
    buffer). *extras* (e.g. {'part': 'text', 'slot': 'top'}) goes into the
    node's extras, which three.js's GLTFLoader exposes as userData -- the
    page styles and hit-tests each part by it. Coordinates are written as-is (Blender Z-up, mm) -- the
    preview page orients and frames the model itself.

    Hand-rolled rather than bpy.ops.export_scene.gltf so a preview never has
    to touch the user's selection/active object, and stays fast enough to
    rebuild on every field edit.
    """
    bin_chunks = []
    accessors, buffer_views, meshes, nodes, materials = [], [], [], [], []
    offset = 0

    def _add_view(data):
        nonlocal offset
        pad = (-len(data)) % 4
        bin_chunks.append(data + b'\x00' * pad)
        buffer_views.append({'buffer': 0, 'byteOffset': offset, 'byteLength': len(data), 'target': 34962})
        offset += len(data) + pad
        return len(buffer_views) - 1

    for obj, rgba, extras in parts:
        mesh = obj.data
        mesh.calc_loop_triangles()
        mw = obj.matrix_world
        pos, nrm = [], []
        lo = [math.inf] * 3
        hi = [-math.inf] * 3
        for tri in mesh.loop_triangles:
            co = [mw @ mesh.vertices[vi].co for vi in tri.vertices]
            n = (co[1] - co[0]).cross(co[2] - co[0])
            if n.length == 0:
                continue
            n.normalize()
            for c in co:
                pos.extend((c.x, c.y, c.z))
                nrm.extend((n.x, n.y, n.z))
                for i in range(3):
                    lo[i] = min(lo[i], c[i])
                    hi[i] = max(hi[i], c[i])
        count = len(pos) // 3
        if count == 0:
            continue
        pos_view = _add_view(struct.pack(f'<{len(pos)}f', *pos))
        nrm_view = _add_view(struct.pack(f'<{len(nrm)}f', *nrm))
        accessors.append({'bufferView': pos_view, 'componentType': 5126, 'count': count,
                          'type': 'VEC3', 'min': lo, 'max': hi})
        accessors.append({'bufferView': nrm_view, 'componentType': 5126, 'count': count, 'type': 'VEC3'})
        materials.append({'name': obj.name, 'pbrMetallicRoughness': {
            'baseColorFactor': list(rgba), 'metallicFactor': 0.0, 'roughnessFactor': 0.75}})
        meshes.append({'name': obj.name, 'primitives': [{
            'attributes': {'POSITION': len(accessors) - 2, 'NORMAL': len(accessors) - 1},
            'material': len(materials) - 1}]})
        nodes.append({'name': obj.name, 'mesh': len(meshes) - 1, 'extras': extras})

    binary = b''.join(bin_chunks)
    gltf = {
        'asset': {'version': '2.0', 'generator': 'TrailPrint3D'},
        'scene': 0,
        'scenes': [{'nodes': list(range(len(nodes)))}],
        'nodes': nodes,
        'meshes': meshes,
        'materials': materials,
        'accessors': accessors,
        'bufferViews': buffer_views,
        'buffers': [{'byteLength': len(binary)}],
    }
    json_bytes = json.dumps(gltf, separators=(',', ':')).encode('utf-8')
    json_bytes += b' ' * ((-len(json_bytes)) % 4)
    total = 12 + 8 + len(json_bytes) + 8 + len(binary)
    with open(path, 'wb') as f:
        f.write(struct.pack('<III', 0x46546C67, 2, total))
        f.write(struct.pack('<II', len(json_bytes), 0x4E4F534A))
        f.write(json_bytes)
        f.write(struct.pack('<II', len(binary), 0x004E4942))
        f.write(binary)


def preview_info(p):
    """What the page needs alongside each preview GLB: where its clickable
    dovetail/text markers go -- computed here so the page never duplicates
    the placement rules."""
    return {
        'medalSize': p['medalSize'],
        # Lower edge of the top ribbon slot -- the page hangs its
        # placeholder medal just below it.
        'hangSlotBottom': _SLOT_Y_FACTORS[0] * p['medalSize'] / 2 - p['slotHeight'] / 2,
        'dovetailSpots': dovetail_spots(p),
        'dovetailSize': dovetail_size(p['objSize']),
        'dovetailsPossible': dovetail_height(p) > 0,
        'dovetails': p['dovetails'],
        'textSpots': text_spots(p),
        'mounting': _mount_summary(p),
    }


def _mount_summary(p):
    """How many magnets/keyholes were asked for vs. actually fit."""
    layout = mount_layout(p)
    if layout is None:
        raise ValueError(_rpt("Invalid mount layout"))
    return {
        'magnetsWanted': len(_magnet_angles(p['shape'])) if p['magnets'] else 0,
        'magnets': len(layout['magnets']),
        'keyholesWanted': {'none': 0, 'single': 1, 'double': 2}[p['keyholes']],
        'keyholes': len(layout['keyholes']),
        'keyholesPossible': keyholes_possible(p),
    }


def write_preview_glb(p, path, collection):
    """Build the model into *collection*, dump it to *path* as GLB, then
    remove it again -- the scene is left exactly as it was."""
    plate, texts = build_objects(p, collection, name="TP3D_MedalPreview")
    try:
        write_glb([(plate, _PREVIEW_PLATE_RGBA, {'part': 'plate'})]
                  + [(obj, _PREVIEW_TEXT_RGBA, {'part': 'text', 'slot': slot}) for slot, obj in texts], path)
    finally:
        for obj in [plate] + [obj for _slot, obj in texts]:
            remove_object(obj)
