import json
import os
import re
import zlib

import bpy
from bpy.app.translations import pgettext_iface as _
from bpy.app.translations import pgettext_rpt as _rpt
from bpy.app.translations import pgettext_tip as _tip

from .. import constants as const
from .color import material_to_srgb, set_material_srgb

COLOR_ROLES = (
    ("BASE", "element_color_base", "Base Terrain"),
    ("WATER", "element_color_water", "Water"),
    ("FOREST", "element_color_forest", "Forest"),
    ("MOUNTAIN", "element_color_mountain", "Mountain / Scree"),
    ("CITY", "element_color_city", "City"),
    ("GREENSPACE", "element_color_greenspace", "Greenspace"),
    ("FARMLAND", "element_color_farmland", "Farmland"),
    ("GLACIER", "element_color_glacier", "Glacier"),
    ("BUILDINGS", "element_color_buildings", "Buildings"),
    ("ROADS", "element_color_roads", "Roads"),
    ("TRAIL", "element_color_trail", "Trail"),
    ("YELLOW", "element_color_secondary_trail", "Secondary Trail"),
)
ROLE_NAMES = tuple(role for role, _attr, _label in COLOR_ROLES)
ROLE_ATTRS = {role: attr for role, attr, _label in COLOR_ROLES}
COLOR_ROLES_PROP = "tp3d_color_roles"
MATERIAL_ROLE_PROP = "tp3d_color_role"
USER_STYLE_PREFIX = "USER:"
STYLE_VERSION = 1

BUILT_IN_STYLES = {
    "FOREST": (
        _("Forest"),
        _tip(
            "A bold, natural green palette with vivid greens, deep forest tones, and classic blue water."
        ),
        {
            "BASE": "#55B222",
            "WATER": "#2338CC",
            "FOREST": "#266623",
            "MOUNTAIN": "#808080",
            "CITY": "#B2B21A",
            "GREENSPACE": "#29FF29",
            "FARMLAND": "#4D801A",
            "GLACIER": "#CCE5CC",
            "BUILDINGS": "#666666",
            "ROADS": "#000000",
            "TRAIL": "#FF0000",
            "YELLOW": "#FFFF00",
        },
    ),
    "URBAN": (
        _("Urban"),
        _tip(
            "A city-model palette of warm taupe terrain, white buildings, black roads, and vivid blue water."
        ),
        {
            "BASE": "#363636",
            "WATER": "#1E3CD2",
            "FOREST": "#1F7A35",
            "MOUNTAIN": "#8E8178",
            "CITY": "#776A5E",
            "GREENSPACE": "#3A9A48",
            "FARMLAND": "#98A87F",
            "GLACIER": "#EDEDED",
            "BUILDINGS": "#F2F2F2",
            "ROADS": "#1C1C1C",
            "TRAIL": "#E02424",
            "YELLOW": "#F2C230",
        },
    ),
    "DESERT": (
        _("Desert"),
        _tip(
            "A warm, earthy palette of sandstone, clay, and olive tones, accented by blue water and red trails."
        ),
        {
            "BASE": "#D2B078",
            "WATER": "#318BA3",
            "FOREST": "#667744",
            "MOUNTAIN": "#A66F49",
            "CITY": "#E1CDAA",
            "GREENSPACE": "#A4B477",
            "FARMLAND": "#B88E50",
            "GLACIER": "#F4E9D5",
            "BUILDINGS": "#80664F",
            "ROADS": "#514033",
            "TRAIL": "#C83F35",
            "YELLOW": "#F0C653",
        },
    ),
    "SNOW": (
        _("Snow"),
        _tip(
            "A crisp winter palette of snowy whites, icy blues, cool grays, and evergreen forests, with orange trail accents."
        ),
        {
            "BASE": "#E8EEF2",
            "WATER": "#68A9D0",
            "FOREST": "#315746",
            "MOUNTAIN": "#A3ADB8",
            "CITY": "#C8D0D8",
            "GREENSPACE": "#A8C7B0",
            "FARMLAND": "#D4DCCB",
            "GLACIER": "#FFFFFF",
            "BUILDINGS": "#697785",
            "ROADS": "#485563",
            "TRAIL": "#E87532",
            "YELLOW": "#F2C75C",
        },
    ),
}

# The first built-in doubles as the default: it is the first enum item (so the
# initial selection) and the source of default_colors().
DEFAULT_STYLE = next(iter(BUILT_IN_STYLES))

_STYLE_ITEMS_CACHE = []

# Blender stores a callback EnumProperty as the integer from the item tuple, not the
# identifier. User styles therefore need a value that depends only on the style's
# name; a positional index shifts whenever another style is saved or deleted, and
# a saved .blend then reopens on the wrong style. Built-ins keep their small
# indices; user styles hash into a range that cannot overlap them.
_USER_STYLE_VALUE_BASE = 1 << 16
_USER_STYLE_VALUE_SPAN = 1 << 24

# Redraws call list_user_styles/style_is_modified, so avoid file reads there. The
# listing is keyed on the style folder's mtime and each parsed style on its own
# file's (mtime, size); both are a single stat() to validate.
_user_styles_cache = {"signature": None, "names": []}
_user_style_colors_cache = {}


def hex_to_srgb(value):
    if not isinstance(value, str) or not re.fullmatch(r"#?[0-9A-Fa-f]{6}", value):
        raise ValueError(_rpt("Invalid color: {value!r}").format(value=value))
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def srgb_to_hex(rgb):
    return "#{:02X}{:02X}{:02X}".format(*rgb[:3])


def color_to_srgb(color):
    return tuple(round(max(0.0, min(1.0, channel)) * 255.0) for channel in color[:3])


def srgb_to_color(rgb):
    return tuple(channel / 255.0 for channel in rgb[:3])


def default_colors():
    return normalize_colors(BUILT_IN_STYLES[DEFAULT_STYLE][2])


def normalize_colors(colors):
    normalized = {
        role: hex_to_srgb(value)
        for role, value in BUILT_IN_STYLES[DEFAULT_STYLE][2].items()
    }
    if not isinstance(colors, dict):
        return normalized
    for role in ROLE_NAMES:
        if role in colors:
            normalized[role] = hex_to_srgb(colors[role])
    return normalized


def scene_colors(tp3d=None):
    if tp3d is None:
        scene = getattr(bpy.context, "scene", None)
        tp3d = getattr(scene, "tp3d", None)
    colors = default_colors()
    if tp3d is None:
        return colors
    for role, attr in ROLE_ATTRS.items():
        value = getattr(tp3d, attr, None)
        if value is not None:
            colors[role] = color_to_srgb(value)
    return colors


def set_scene_colors(tp3d, colors):
    for role in ROLE_NAMES:
        if role in colors:
            setattr(tp3d, ROLE_ATTRS[role], srgb_to_color(colors[role]))


def _safe_style_name(name):
    name = " ".join(str(name).split())
    if not name or len(name) > 64 or re.search(r'[<>:"/\\|?*]', name):
        raise ValueError(_('Use 1-64 characters without < > : " / \\ | ? *.'))
    if name.rstrip(" .") != name or name.upper() in {"CON", "PRN", "AUX", "NUL"}:
        raise ValueError(_("Use a regular file-safe style name."))
    return name


def _style_path(name):
    return os.path.join(const.color_style_dir, _safe_style_name(name) + ".json")


def list_user_styles():
    try:
        signature = (const.color_style_dir, os.stat(const.color_style_dir).st_mtime_ns)
    except OSError:
        _user_styles_cache.update(signature=None, names=[])
        return []
    if _user_styles_cache["signature"] == signature:
        return list(_user_styles_cache["names"])
    styles = []
    for filename in sorted(os.listdir(const.color_style_dir), key=str.lower):
        name, ext = os.path.splitext(filename)
        if ext.lower() != ".json":
            continue
        try:
            styles.append(_safe_style_name(name))
        except ValueError:
            continue
    _user_styles_cache.update(signature=signature, names=styles)
    return list(styles)


def _user_style_enum_values(names):
    """Map each user style name to a stable enum value (see _USER_STYLE_VALUE_BASE)."""
    values = {}
    used = set()
    for name in names:
        value = (
            _USER_STYLE_VALUE_BASE
            + zlib.crc32(name.casefold().encode("utf-8")) % _USER_STYLE_VALUE_SPAN
        )
        while value in used:  # astronomically rare; stays deterministic for a given set
            value += 1
        used.add(value)
        values[name] = value
    return values


def load_user_style(name):
    path = _style_path(name)
    stat = os.stat(path)  # raises OSError when missing, like open() would
    signature = (stat.st_mtime_ns, stat.st_size)
    cached = _user_style_colors_cache.get(path)
    if cached is not None and cached[0] == signature:
        return dict(cached[1])
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or data.get("version") != STYLE_VERSION:
        raise ValueError(_rpt("Unsupported color style version"))
    colors = normalize_colors(data.get("colors"))
    _user_style_colors_cache[path] = (signature, colors)
    return dict(colors)


def save_user_style(name, colors):
    name = _safe_style_name(name)
    if name.upper() in BUILT_IN_STYLES or name.lower() in {
        label.lower() for label, _description, _colors in BUILT_IN_STYLES.values()
    }:
        raise ValueError(_("Built-in style names are reserved."))
    os.makedirs(const.color_style_dir, exist_ok=True)
    data = {
        "version": STYLE_VERSION,
        "name": name,
        "colors": role_hex_map(normalize_colors(role_hex_map(colors))),
    }
    path = _style_path(name)
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    os.replace(temp_path, path)
    return USER_STYLE_PREFIX + name


def delete_user_style(identifier):
    if not identifier.startswith(USER_STYLE_PREFIX):
        raise ValueError(_("Only saved styles can be deleted."))
    path = _style_path(identifier[len(USER_STYLE_PREFIX) :])
    os.remove(path)
    _user_style_colors_cache.pop(path, None)


def style_colors(identifier):
    if identifier in BUILT_IN_STYLES:
        return normalize_colors(BUILT_IN_STYLES[identifier][2])
    if identifier.startswith(USER_STYLE_PREFIX):
        return load_user_style(identifier[len(USER_STYLE_PREFIX) :])
    raise ValueError(
        _rpt("Unknown color style: {identifier}").format(identifier=identifier)
    )  # noqa: UP032


def color_style_items(_self, _context):
    # Labels and descriptions are already translated at definition time in
    # BUILT_IN_STYLES (via _() and _tip()), so do not re-wrap them here.
    items = [
        (key, label, description, index)
        for index, (key, (label, description, _colors)) in enumerate(
            BUILT_IN_STYLES.items()
        )
    ]
    names = list_user_styles()
    for name, value in _user_style_enum_values(names).items():
        items.append((USER_STYLE_PREFIX + name, name, _("Saved color style"), value))
    _STYLE_ITEMS_CACHE[:] = items
    return _STYLE_ITEMS_CACHE


def style_is_modified(tp3d):
    try:
        return scene_colors(tp3d) != style_colors(tp3d.color_style)
    except (OSError, ValueError, json.JSONDecodeError):
        return True


def material_role_colors():
    colors = default_colors()
    for role in ROLE_NAMES:
        material = bpy.data.materials.get(role)
        if material is not None:
            colors[role] = material_to_srgb(material, colors[role])
    return colors


def material_role(material):
    """Color role a TrailPrint3D element material stands for, or None.

    The shared materials are named after their role ("BASE", "WATER", ...);
    per-map copies made by recolor_object_materials carry MATERIAL_ROLE_PROP.
    """
    if material is None:
        return None
    role = material.get(MATERIAL_ROLE_PROP)
    if role in ROLE_NAMES:
        return role
    return material.name if material.name in ROLE_NAMES else None


def recolor_object_materials(objects, colors):
    """Recolor only *objects*' element materials to *colors*.

    The shared role materials stay untouched (every other map and every
    by-name lookup keeps using them); each one used by *objects* is swapped
    for a tagged copy, shared between the objects of this call. A copy that
    no object outside *objects* uses is recolored in place instead of copied
    again. Returns the number of material slots recolored.
    """
    objects = set(objects)
    users = {}
    for obj in bpy.data.objects:
        for slot in obj.material_slots:
            if slot.material is not None:
                users.setdefault(slot.material.name, set()).add(obj)

    copies = {}
    changed = 0
    for obj in objects:
        for slot in obj.material_slots:
            material = slot.material
            role = material_role(material)
            if role is None:
                continue
            if (
                material.get(MATERIAL_ROLE_PROP)
                and users.get(material.name, set()) <= objects
            ):
                target = material
            else:
                target = copies.get(material.name)
                if target is None:
                    target = material.copy()
                    target[MATERIAL_ROLE_PROP] = role
                    copies[material.name] = target
                slot.material = target
            set_material_srgb(target, colors[role])
            changed += 1
    return changed


def detach_role_material_users(colors):
    """Keep already generated maps' colors before the shared role materials
    take on *colors* (primitives.setupColors, at the start of a generation).

    Every object still using a shared role material whose color is about to
    change moves onto a tagged copy that keeps the old color -- one copy per
    material, shared by all its current users -- so switching palettes only
    affects maps generated from then on. Returns the number of copies made.
    """
    copied = 0
    for role, rgb in colors.items():
        material = bpy.data.materials.get(role)
        if material is None or material.get(MATERIAL_ROLE_PROP):
            continue
        if srgb_to_hex(material_to_srgb(material, rgb)) == srgb_to_hex(rgb):
            continue
        slots = [
            slot
            for obj in bpy.data.objects
            for slot in obj.material_slots
            if slot.material == material
        ]
        if not slots:
            continue
        frozen = material.copy()
        frozen[MATERIAL_ROLE_PROP] = role
        for slot in slots:
            slot.material = frozen
        copied += 1
    return copied


def role_hex_map(colors):
    return {role: srgb_to_hex(rgb) for role, rgb in colors.items()}


def write_mesh_color_roles(mesh, colors=None):
    mesh[COLOR_ROLES_PROP] = json.dumps(
        role_hex_map(colors or scene_colors()), sort_keys=True
    )


def read_mesh_color_roles(mesh, fallback):
    raw = mesh.get(COLOR_ROLES_PROP)
    if not raw:
        return dict(fallback)
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise TypeError(_rpt("Color roles must be an object"))
        # Roles missing from an older snapshot take the caller's fallback (the
        # current material color), not DEFAULT, which would recolor the wrong pixels.
        roles = dict(fallback)
        for role in ROLE_NAMES:
            if role in parsed:
                roles[role] = hex_to_srgb(parsed[role])
        return roles
    except (TypeError, ValueError, json.JSONDecodeError):
        print(
            _rpt(
                "[TP3D colors] Invalid {COLOR_ROLES_PROP} on {mesh_name}; using current materials"
            ).format(COLOR_ROLES_PROP=COLOR_ROLES_PROP, mesh_name=mesh.name)
        )
        return dict(fallback)
