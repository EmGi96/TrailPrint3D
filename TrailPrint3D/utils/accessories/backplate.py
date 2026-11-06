#  Copyright (C) 2026  EmGi
"""Backplate -- a standalone Shape Extras "Solid Plate" (no map): the plate
with its map inset pocket, optional text (Outer Edge / Front Face / Curved
layouts) and optional dovetail recesses in its back face.

Built through the same code the map generation's Shape Extras use
(plate.create_generic_plate, text_layouts.apply_text_layout,
terrain.plateInsert), around a stand-in "map" that only carries the map
outline (map_polygon_wkt) -- so a backplate is exactly the plate a map of
that shape/size would get. Those builders read their settings from the
scene (tp3d), so each build swaps the page's values in for its duration
(_scene_overrides) and puts the user's own back afterwards.

Shared by the Backplate Generator's live 3D preview (write_preview_glb) and
its final "Send to Blender" result. Lies flat with its front face up at
z = plateThickness, display "up" = +Y.
"""
import math
import os
from contextlib import contextmanager

import bpy  # type: ignore
from bpy.app.translations import pgettext_rpt as _rpt

from ... import temp
from ..primitives import (
    circle_polygon,
    hexagon_polygon,
    octagon_polygon,
    rectangle_polygon,
)
from ..shape_capabilities import valid_layouts
from . import medal_holder

SHAPES = ('HEXAGON', 'OCTAGON', 'CIRCLE', 'SQUARE')
# Text layouts a backplate can take -- ON_MAP needs a real map surface.
LAYOUTS = ('NONE', 'OUTER_EDGE', 'FRONT_FACE', 'CURVED')
HANDLE_STYLES = ('NONE', 'ROUND', 'FLAT')

DEFAULTS = {
    'shape': 'HEXAGON',
    'objSize': 100,
    'plateThickness': 5.0,
    'outerBorderSize': 20,
    'plateInsertValue': 2.0,
    'plateBevel': 0.0,
    'tolerance': 0.2,
    'textLayout': 'OUTER_EDGE',
    'textSize': 5,
    'textSizeTitle': 0,
    'textPlacement': False,
    'handleStyle': 'NONE',
}

# Every text field the layouts know (text_layouts._FIELD_TO_PROP values), with
# its icon prop and the icons that prop accepts (props.py's enum items).
_TITLE_ICONS = ('cycling', 'hiking', 'running', 'swimming', 'skiing', 'snowboarding', 'kajak', 'no')
_TEXT_ICONS = ('distance', 'elevation', 'time', 'speed', 'date', 'no')
TEXT_FIELDS = {
    'titlefield': ('titleIcon', _TITLE_ICONS),
    'textfield1': ('iconText1', _TEXT_ICONS),
    'textfield2': ('iconText2', _TEXT_ICONS),
    'textfield3': ('iconText3', _TEXT_ICONS),
    'textfield4': ('iconText4', _TEXT_ICONS),
    'textfield5': ('iconText5', _TEXT_ICONS),
    'textfield6': ('iconText6', _TEXT_ICONS),
    'textfield7': ('iconText7', _TEXT_ICONS),
}
_TEXT_MAX_CHARS = 60

_PREVIEW_PLATE_RGBA = (0.05, 0.05, 0.055, 1.0)
_PREVIEW_TEXT_RGBA = (0.9, 0.9, 0.9, 1.0)


def sanitize_params(raw: dict | None, scene_font: str = '') -> dict:
    """Clamp page-supplied values into buildable ranges (the page is a
    browser form -- never trust it to have done so). *scene_font* (the
    scene's own textFont) is accepted as a font besides the listed ones."""
    raw = raw or {}
    p = dict(DEFAULTS)

    def _num(key, lo, hi, integer=False):
        try:
            val = float(raw.get(key, p[key]))
        except (TypeError, ValueError):
            val = p[key]
        if not math.isfinite(val):
            val = p[key]
        val = max(lo, min(hi, val))
        p[key] = int(round(val)) if integer else val

    shape = str(raw.get('shape', p['shape'])).upper()
    p['shape'] = shape if shape in SHAPES else DEFAULTS['shape']
    _num('objSize', 10, 1000, integer=True)
    _num('plateThickness', 0.6, 50.0)
    _num('outerBorderSize', 1, 200, integer=True)
    # Leaves at least 0.4 mm of floor under the map.
    _num('plateInsertValue', 0.0, max(0.0, p['plateThickness'] - 0.4))
    _num('plateBevel', 0.0, min(50.0, p['plateThickness'] / 2))
    _num('tolerance', 0.0, 2.0)
    _num('textSize', 1, 100, integer=True)
    _num('textSizeTitle', 0, 100, integer=True)
    p['textPlacement'] = raw.get('textPlacement') is True

    layout = str(raw.get('textLayout', p['textLayout'])).upper()
    allowed = [lay for lay in valid_layouts(p['shape'], 'SOLID_PLATE') if lay in LAYOUTS]
    p['textLayout'] = layout if layout in allowed else 'NONE'

    from .. import font_list
    font = str(raw.get('textFont') or '')
    if font and font != scene_font and font not in {f['path'] for f in font_list.list_fonts()}:
        font = ''
    # A font file that's gone (e.g. the scene's, picked on another machine)
    # would make text_objects.create_text fail -- use the default instead.
    if font and not os.path.isfile(bpy.path.abspath(font)):
        font = ''
    p['textFont'] = font

    texts = raw.get('texts') if isinstance(raw.get('texts'), dict) else {}
    p['texts'] = {prop: _sanitize_text(prop, texts.get(prop)) for prop in TEXT_FIELDS}

    n_spots = len(medal_holder._dovetail_angles(_dovetail_shape(p)))
    dovetails = raw.get('dovetails')
    p['dovetails'] = sorted({
        int(i) for i in (dovetails if isinstance(dovetails, list) else [])
        if isinstance(i, (int, float)) and 0 <= int(i) < n_spots
    })

    handle = str(raw.get('handleStyle') or 'NONE').upper()
    p['handleStyle'] = handle if handle in HANDLE_STYLES and temp.PREMIUMVERSION else 'NONE'
    return p


def _sanitize_text(prop, raw):
    """{'text', 'icon'} or None (no text in that field)."""
    if not isinstance(raw, dict):
        return None
    text = ' '.join(str(raw.get('text') or '').split())[:_TEXT_MAX_CHARS]
    if not text:
        return None
    icon = str(raw.get('icon') or 'no')
    # Text icons are Premium (text_layouts' textIcon is a no-op without it).
    if icon not in TEXT_FIELDS[prop][1] or not temp.PREMIUMVERSION:
        icon = 'no'
    return {'text': text, 'icon': icon}


# ── Outlines ───────────────────────────────────────────────────────────────


def map_outline(p):
    """The map's outline -- same construction the map generator uses for
    each shape (operators.py's map generator, utils/osm/prefetch.py)."""
    half = p['objSize'] / 2
    if p['shape'] == 'CIRCLE':
        return circle_polygon(half, 64)
    if p['shape'] == 'HEXAGON':
        return hexagon_polygon(half)
    if p['shape'] == 'OCTAGON':
        return octagon_polygon(half)
    return rectangle_polygon(p['objSize'], p['objSize'])


def plate_outline(p):
    """The plate's outline, grown from the map's exactly like
    create_generic_plate does."""
    from ..plate import _expand_outline
    return _expand_outline(map_outline(p), p['objSize'] * p['outerBorderSize'] / 200.0, shape=p['shape'])


def _ring_radii(p):
    """(map, plate) inradius -- what text_layouts._ring_radii measures from
    the built objects, here from the outlines directly."""
    from ..plate import measured_inradius
    return (measured_inradius(map_outline(p), p['shape']),
            measured_inradius(plate_outline(p), p['shape']))


# ── Text ───────────────────────────────────────────────────────────────────
_FIELD_LABELS = {
    'titlefield': 'Title', 'textfield1': 'Text 1', 'textfield2': 'Text 2', 'textfield3': 'Text 3',
    'textfield4': 'Text 4', 'textfield5': 'Text 5', 'textfield6': 'Text 6', 'textfield7': 'Text 7',
}


def text_spots(p):
    """Where every text field of the current layout sits, for the page's
    clickable markers: [{'field', 'label', 'x', 'y', 'z', 'angle', 'face'}].
    face 'top' = flat on the front ring (Outer Edge, Curved), 'side' = on
    the side wall (Front Face). Same angles/radii as text_layouts places
    the text itself (text_angle_preset is always 0 here)."""
    from .. import text_layouts as tl

    layout = p['textLayout']
    table = {
        'OUTER_EDGE': tl.OUTER_EDGE_FIELDS,
        'FRONT_FACE': tl.FRONT_FACE_FIELDS,
        'CURVED': tl.CURVED_RING_FIELDS,
    }.get(layout, {})
    rows = table.get(p['shape'], [])
    if not rows:
        return []
    inner, outer = _ring_radii(p)
    t = p['plateThickness']
    spots = []
    for row in rows:
        field = tl._FIELD_TO_PROP[row[0]]
        a = math.radians(row[1])
        if layout == 'FRONT_FACE':
            r, z, face = tl._polygon_radius_at_angle(outer, p['shape'], a), t / 2, 'side'
        elif layout == 'CURVED':
            r, z, face = (inner + outer) / 2, t, 'top'
        else:
            r, z, face = tl._polygon_radius_at_angle((inner + outer) / 2, p['shape'], a), t, 'top'
        spots.append({'field': field, 'label': _FIELD_LABELS[field],
                      'x': math.cos(a) * r, 'y': math.sin(a) * r, 'z': z, 'angle': a, 'face': face})
    return spots


def text_room(p):
    """How tall text can get (mm): the border ring's width for the flat
    layouts, the plate's height for Front Face."""
    if p['textLayout'] == 'FRONT_FACE':
        return p['plateThickness']
    inner, outer = _ring_radii(p)
    return max(0.0, outer - inner)


# ── Dovetails ──────────────────────────────────────────────────────────────
# Same recesses as the Medal Holder's (medal_holder._dovetail_cutter_geometry,
# itself matching operators.dovetail_cutout), on the plate's edges.


def _dovetail_shape(p):
    return 'ROUND' if p['shape'] == 'CIRCLE' else p['shape']


def _dovetail_params(p):
    """medal_holder's own params for a plate the size of this backplate:
    its 'objSize' is chosen so medal_holder._inradius lands on the plate's
    real edge."""
    _inner, outer = _ring_radii(p)
    size = 2 * outer / 0.866 if p['shape'] == 'HEXAGON' else 2 * outer
    return {'shape': _dovetail_shape(p), 'objSize': size,
            'thickness': p['plateThickness'], 'dovetails': p['dovetails']}


def _cut_dovetails(p, plate):
    dp = _dovetail_params(p)
    if not dp['dovetails'] or medal_holder.dovetail_height(dp) <= 0:
        return
    verts, faces = medal_holder._dovetail_cutter_geometry(dp)
    mesh = bpy.data.meshes.new(f"{plate.name}_Dovetails")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    cutter = bpy.data.objects.new(mesh.name, mesh)
    bpy.context.collection.objects.link(cutter)
    medal_holder._subtract_object(plate, cutter, "DovetailCutout")


# ── Building ───────────────────────────────────────────────────────────────


@contextmanager
def _scene_overrides(p, keep_selection):
    """The scene settings the Shape Extras builders read, swapped for *p*'s
    (and the map centered at the origin with the 3D cursor on it) for the
    duration, then restored. *keep_selection* also restores the user's
    selection/active object -- the builders select things as they go."""
    tp3d = bpy.context.scene.tp3d
    values = {
        'objSize': p['objSize'],
        'tolerance': p['tolerance'],
        'plateInsertValue': p['plateInsertValue'],
        'textFont': p['textFont'],
        'textSize': p['textSize'],
        'textSizeTitle': p['textSizeTitle'],
        'shapeRotation': 0,
        'text_angle_preset': 0,
        'o_centerx': 0.0,
        'o_centery': 0.0,
        'xTerrainOffset': 0.0,
        'yTerrainOffset': 0.0,
    }
    if temp.PREMIUMVERSION:
        values['handleStyle'] = p['handleStyle']
    for prop, (icon_prop, _icons) in TEXT_FIELDS.items():
        spec = p['texts'][prop]
        values[prop] = spec['text'] if spec else ''
        values[icon_prop] = spec['icon'] if spec else 'no'

    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    saved = {k: getattr(tp3d, k) for k in values}
    saved_cursor = scene.cursor.location.copy()
    saved_selection = [o for o in view_layer.objects if o.select_get()] if keep_selection else []
    saved_active = view_layer.objects.active
    try:
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for k, v in values.items():
            setattr(tp3d, k, v)
        scene.cursor.location = (0.0, 0.0, 0.0)
        yield
    finally:
        for k, v in saved.items():
            try:
                setattr(tp3d, k, v)
            except (TypeError, ValueError) as exc:
                print(f"[TrailPrint3D] backplate: could not restore {k}: {exc!r}")
        scene.cursor.location = saved_cursor
        if keep_selection:
            # Objects removed during the build can linger as None entries
            # until the view layer updates -- skipped, never allowed to
            # replace an exception the build itself raised.
            for o in list(view_layer.objects):
                if o is not None:
                    o.select_set(False)
            for o in saved_selection:
                try:
                    o.select_set(True)
                except (ReferenceError, RuntimeError):
                    pass
            try:
                view_layer.objects.active = saved_active
            except (ReferenceError, RuntimeError):
                pass


def build_objects(p, name='Backplate', keep_selection=False):
    """Create the plate (and its text, if any) in the active collection.
    Returns (plate, text_obj or None)."""
    from ..plate import create_generic_plate
    from ..terrain import plateInsert
    from ..text_layouts import apply_text_layout

    t = p['plateThickness']
    existing = {o.as_pointer() for o in bpy.data.objects}
    with _scene_overrides(p, keep_selection):
        # Stand-in map: only its outline is ever read (map_polygon_wkt).
        map_stub = bpy.data.objects.new(f"{name}_MapOutline", None)
        map_stub["map_polygon_wkt"] = map_outline(p).wkt
        plate = text = None
        try:
            plate = create_generic_plate(
                map_stub, 'SOLID_PLATE', shape=p['shape'],
                outer_border_pct=p['outerBorderSize'], thickness=t,
                bevel=p['plateBevel'], name=name,
            )
            if plate is None:
                raise ValueError(_rpt("Could not build the plate"))
            # Sit it on the ground: local [-t, 0] -> world [0, t].
            plate.location.z += t

            fields = {prop for prop, spec in p['texts'].items() if spec}
            if p['textLayout'] != 'NONE' and fields:
                inset = p['textPlacement']
                text = apply_text_layout(
                    map_stub, plate, p['textLayout'], p['shape'], inset=inset,
                    inset_depth=p['plateInsertValue'] or 0.8, only_fields=fields,
                )
                # Same lift elements._rg_create_text_and_overlays gives them.
                if text is not None and not inset and p['textLayout'] in {'OUTER_EDGE', 'CURVED'}:
                    text.location.z += t + 0.002

            plateInsert(plate, map_stub)
            _cut_dovetails(p, plate)
            # The z lifts above only reach matrix_world (read by write_glb)
            # on the next update -- plateInsert skips its own at depth 0.
            bpy.context.view_layer.update()
        except Exception:
            # Everything the build created so far -- the plate, but also
            # half-finished text/icon objects from inside the layout code.
            for obj in [o for o in bpy.data.objects if o.as_pointer() not in existing]:
                if obj is not map_stub:
                    remove_object(obj)
            raise
        finally:
            bpy.data.objects.remove(map_stub, do_unlink=True)

    plate.name = plate.data.name = name
    if text is not None:
        text.name = text.data.name = f"{name}_Text"
    return plate, text


def remove_object(obj):
    try:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
    except ReferenceError:
        return
    # Meshes, or the text curves of a build that failed mid-way.
    if isinstance(data, bpy.types.Mesh) and data.users == 0:
        bpy.data.meshes.remove(data)
    elif isinstance(data, bpy.types.Curve) and data.users == 0:
        bpy.data.curves.remove(data)


def preview_info(p):
    """What the page needs alongside each preview GLB: where its clickable
    dovetail/text markers go -- computed here so the page never duplicates
    the placement rules."""
    dp = _dovetail_params(p)
    return {
        'dovetailSpots': medal_holder.dovetail_spots(dp),
        'dovetailSize': medal_holder.dovetail_size(dp['objSize']),
        'dovetailsPossible': medal_holder.dovetail_height(dp) > 0,
        'dovetails': p['dovetails'],
        'textSpots': text_spots(p),
        'textRoom': text_room(p),
        'plateSize': _plate_width(p),
    }


def _plate_width(p):
    minx, _miny, maxx, _maxy = plate_outline(p).bounds
    return maxx - minx


def write_preview_glb(p, path):
    """Build the model, dump it to *path* as GLB, then remove it again --
    the scene (settings, selection) is left exactly as it was."""
    plate, text = build_objects(p, name="TP3D_BackplatePreview", keep_selection=True)
    try:
        parts = [(plate, _PREVIEW_PLATE_RGBA, {'part': 'plate'})]
        if text is not None:
            parts.append((text, _PREVIEW_TEXT_RGBA, {'part': 'text'}))
        medal_holder.write_glb(parts, path)
    finally:
        for obj in (plate, text):
            if obj is not None:
                remove_object(obj)


def place_at(objs, location):
    """Move the built objects (built around the origin) to *location*."""
    for obj in objs:
        obj.location += location
