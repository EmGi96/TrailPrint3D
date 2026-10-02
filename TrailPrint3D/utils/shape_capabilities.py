"""Compatibility matrix for shape x plate-mode x text-layout combinations.

Single source of truth: the UI panel, the enum item callbacks, the update
callbacks, and the generation dispatch all read from here. Adding a new
shape or layout is a change in one place only.
"""

from bpy.app.translations import pgettext_iface as _

from . import temp

# Per base shape: which text layouts are defined. "NONE" is always first so
# it becomes the EnumProperty default when the items list is rebuilt.
TEXT_LAYOUTS_BY_SHAPE = {
    "HEXAGON": ("NONE", "ON_MAP", "OUTER_EDGE", "FRONT_FACE"),
    "OCTAGON": ("NONE", "OUTER_EDGE", "FRONT_FACE"),
    "CIRCLE": ("NONE", "CURVED"),
    "SQUARE": ("NONE", "OUTER_EDGE", "FRONT_FACE"),
    "ELLIPSE": ("NONE",),
    "HEART": ("NONE",),
    "GEOJSON": ("NONE",),
    "SVG": ("NONE",),
}

# Layouts that require a physical plate to sit on. Selecting one of these
# auto-upgrades plateMode from NONE to SOLID_PLATE.
LAYOUTS_REQUIRING_PLATE = frozenset({"OUTER_EDGE", "FRONT_FACE", "CURVED"})

# Layouts each plate mode can actually host.
_LAYOUTS_BY_PLATE_MODE = {
    "NONE": frozenset({"NONE", "ON_MAP"}),
    "SOLID_PLATE": frozenset({"NONE", "ON_MAP", "OUTER_EDGE", "FRONT_FACE", "CURVED"}),
    "SHELL": frozenset({"NONE", "ON_MAP", "FRONT_FACE"}),
}
# Per base shape: which plate/shell modes are offered.
PLATE_MODES_BY_SHAPE = {
    "HEXAGON": ("NONE", "SOLID_PLATE", "SHELL"),
    "OCTAGON": ("NONE", "SOLID_PLATE", "SHELL"),
    "CIRCLE": ("NONE", "SOLID_PLATE", "SHELL"),
    "SQUARE": ("NONE", "SOLID_PLATE", "SHELL"),
    "ELLIPSE": ("NONE", "SOLID_PLATE", "SHELL"),
    "HEART": ("NONE", "SOLID_PLATE", "SHELL"),
    "GEOJSON": ("NONE", "SOLID_PLATE"),
    "SVG": ("NONE", "SOLID_PLATE"),
}

_LAYOUT_LABELS = {
    "NONE": (_("None"), _("No text overlay")),
    "ON_MAP": (_("On Map"), _("Text carved into the map surface itself")),
    "OUTER_EDGE": (_("Outer Edge"), _("Text lies flat on the plate's top ring")),
    "FRONT_FACE": (_("Front Face"), _("Text on the plate's vertical side wall")),
    "CURVED": (_("Curved"), _("Text arced around the plate's ring")),
}

_PLATE_LABELS = {
    "NONE": (_("None"), _("No plate or shell")),
    "SOLID_PLATE": (_("Solid Plate"), _("Solid backplate under the map")),
    "SHELL": (_("Shell"), _("Protective shell around the map")),
}


def valid_layouts(shape, plate_mode):
    shape_layouts = TEXT_LAYOUTS_BY_SHAPE.get(shape, ("NONE",))
    allowed = _LAYOUTS_BY_PLATE_MODE.get(plate_mode, _LAYOUTS_BY_PLATE_MODE["NONE"])
    return tuple(l for l in shape_layouts if l in allowed)


def text_layout_items(self, context):
    shape = self.shape or "HEXAGON"
    plate_mode = getattr(self, "plateMode", "NONE") or "NONE"
    return [
        (ident, _LAYOUT_LABELS[ident][0], _LAYOUT_LABELS[ident][1])
        for ident in valid_layouts(shape, plate_mode)
    ]


def plate_mode_items(self, context):
    """EnumProperty items callback — depends on self.shape and Premium state."""
    shape = self.shape or "HEXAGON"
    valid = PLATE_MODES_BY_SHAPE.get(shape, ("NONE",))
    items = []
    for i, ident in enumerate(valid):
        label, desc = _PLATE_LABELS[ident]
        if ident == "SHELL" and not temp.PREMIUMVERSION:
            items.append((ident, _("%s (Premium)") % label, desc, "LOCKED", i))
        else:
            items.append((ident, label, desc, "NONE", i))
    return items
