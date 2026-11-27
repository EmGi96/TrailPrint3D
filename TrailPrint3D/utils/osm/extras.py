"""OSM "Extras" -- the less-used map elements grouped under one composite
category (the picker's single "Extras" chip, the N-panel's "Extras" box).

Every extra is an ordinary coloring kind: fetched in the same combined
Overpass request as forest/water/..., built by terrain.coloring_main and
colored by its own material / color role (named after the kind). The only
difference to the classic area kinds is that some of them are LINES: open
ways become fixed-width ribbons (Line Width, printed mm) instead of the
water-stream width, and a closed line way only counts as an area when it is
explicitly tagged area=yes.

This table is the single source of truth -- props.py, ui_state.py,
elements.py, fetch_group.py/fetch_solo.py, prefetch.py and panels.py all
loop over it, so adding another extra means adding one row here (plus its
color role in color_styles.py and its props flag).

No bpy import: fetch_group / fetch_solo call into this from worker threads.
"""

from dataclasses import dataclass
from typing import Callable

from . import custom_osm as _custom

# The composite's N-panel master checkbox (same idea as show_water/show_roads).
MASTER_FLAG = "show_extras"
# Element-status chip / fetch-progress key shared by every extra kind.
CHIP_KEY = "extras"

_LIFT_TYPES = {
    "cable_car",
    "gondola",
    "mixed_lift",
    "chair_lift",
    "drag_lift",
    "t-bar",
    "j-bar",
    "platter",
    "rope_tow",
    "magic_carpet",
}
_RAILWAY_TYPES = {"rail", "narrow_gauge", "light_rail", "funicular", "monorail"}
_RAILWAY_SKIP_SERVICE = {"yard", "siding", "spur", "crossover"}
_RAILWAY_SKIP_TUNNEL = {"yes", "building_passage", "culvert"}
_SPORTS_TYPES = {"pitch", "track", "stadium"}

# Runways without a width=* tag -- a typical airliner runway is 45 m.
_DEFAULT_RUNWAY_WIDTH_M = 45.0


def _area_or_multipolygon(element):
    """Ways, and relations only when they are real multipolygons (piste
    route relations would otherwise be stitched into nonsense rings)."""
    return element.get("type") != "relation" or (
        (element.get("tags") or {}).get("type") == "multipolygon"
    )


@dataclass(frozen=True)
class ExtraKind:
    kind: str  # OSM kind / material / color role, e.g. "SKISLOPE"
    flag: str  # sub-flag on the scene property group
    label: str  # progress / log label
    line: bool  # open ways -> Line Width ribbons; closed ways need area=yes
    filters: tuple  # Overpass QL filter statements (no trailing ';')
    match: Callable  # (tags, element) -> bool
    # Composite whose master checkbox gates this kind: the Extras group, or
    # Roads for railways (a Roads sub-category with its own color + width).
    master: str = "show_extras"
    width_attr: str = "el_xLineWidth"  # Line Width (mm) setting for its ribbons
    custom_slot: int = -1  # >= 0: a user-defined slot (filters/match/line live in _custom_defs)


EXTRA_KINDS = (
    ExtraKind(
        "SKIAREA",
        "el_xSkiAreaActive",
        "Ski Area",
        False,
        ('way["landuse"="winter_sports"]', 'relation["landuse"="winter_sports"]'),
        lambda t, e: t.get("landuse") == "winter_sports",
    ),
    ExtraKind(
        "BEACH",
        "el_xBeachActive",
        "Beach / Sand",
        False,
        ('way["natural"~"^(beach|sand)$"]', 'relation["natural"~"^(beach|sand)$"]'),
        lambda t, e: t.get("natural") in {"beach", "sand"},
    ),
    ExtraKind(
        "GOLF",
        "el_xGolfActive",
        "Golf Course",
        False,
        ('way["leisure"="golf_course"]', 'relation["leisure"="golf_course"]'),
        lambda t, e: t.get("leisure") == "golf_course",
    ),
    ExtraKind(
        "SPORTS",
        "el_xSportsActive",
        "Sports Pitches",
        False,
        (
            'way["leisure"~"^(pitch|track|stadium)$"]',
            'relation["leisure"~"^(pitch|track|stadium)$"]',
        ),
        lambda t, e: t.get("leisure") in _SPORTS_TYPES,
    ),
    ExtraKind(
        "RUNWAY",
        "el_xRunwayActive",
        "Runways",
        True,
        ('way["aeroway"="runway"]',),
        lambda t, e: t.get("aeroway") == "runway",
    ),
    ExtraKind(
        "RAILWAY",
        "el_sRailwayActive",
        "Railways",
        True,
        (
            'way["railway"~"^(rail|narrow_gauge|light_rail|funicular|monorail)$"]'
            '["tunnel"!~"^(yes|building_passage|culvert)$"]'
            '["service"!~"^(yard|siding|spur|crossover)$"]',
        ),
        lambda t, e: (
            t.get("railway") in _RAILWAY_TYPES
            and t.get("tunnel") not in _RAILWAY_SKIP_TUNNEL
            and t.get("service") not in _RAILWAY_SKIP_SERVICE
        ),
        master="show_roads",
        width_attr="el_sRailwayWidth",
    ),
    # The three ski kinds share one sub-flag ("Ski Slopes & Lifts") but are
    # separate kinds so each gets its own color.
    ExtraKind(
        "SKISLOPE",
        "el_xSkiActive",
        "Ski Slopes",
        True,
        (
            'way["piste:type"="downhill"]',
            'relation["piste:type"="downhill"]["type"="multipolygon"]',
        ),
        lambda t, e: t.get("piste:type") == "downhill" and _area_or_multipolygon(e),
    ),
    ExtraKind(
        "SKINORDIC",
        "el_xSkiActive",
        "Cross-Country Trails",
        True,
        (
            'way["piste:type"="nordic"]',
            'relation["piste:type"="nordic"]["type"="multipolygon"]',
        ),
        lambda t, e: t.get("piste:type") == "nordic" and _area_or_multipolygon(e),
    ),
    ExtraKind(
        "SKILIFT",
        "el_xSkiActive",
        "Ski Lifts",
        True,
        (
            'way["aerialway"~"^(cable_car|gondola|mixed_lift|chair_lift|drag_lift'
            '|t-bar|j-bar|platter|rope_tow|magic_carpet)$"]',
        ),
        lambda t, e: t.get("aerialway") in _LIFT_TYPES,
    ),
    # The user's own tag rules ("Custom" sub-flag), one kind per slot. Their
    # filters, matcher and Line/Area type are only known at runtime -- see
    # _custom_defs / _sync_custom below.
    *(
        ExtraKind(
            kind,
            "el_xCustomActive",
            f"Custom {i + 1}",
            False,
            (),
            lambda t, e: False,
            custom_slot=i,
        )
        for i, kind in enumerate(_custom.KINDS)
    ),
)

EXTRAS_BY_KIND = {x.kind: x for x in EXTRA_KINDS}
EXTRA_KIND_NAMES = frozenset(EXTRAS_BY_KIND)
EXTRA_KEYS = frozenset(k.lower() for k in EXTRAS_BY_KIND)
LINE_KINDS = frozenset(x.kind for x in EXTRA_KINDS if x.line)  # built-in lines only
CUSTOM_KINDS = frozenset(_custom.KINDS)
# The Extras composite's distinct sub-flags in display order (the three ski
# kinds share one); mirrors COMPOSITE_ELEMENTS.extras in settings_modal.js.
# Railways are not among them -- they sit in the Roads composite instead
# (ui_state._road_subflags, RAILWAY_ROAD_ID).
SUB_FLAGS = (
    "el_xSkiActive",
    "el_xSkiAreaActive",
    "el_xBeachActive",
    "el_xGolfActive",
    "el_xRunwayActive",
    "el_xSportsActive",
    "el_xCustomActive",
)
assert set(SUB_FLAGS) == {x.flag for x in EXTRA_KINDS if x.master == MASTER_FLAG}
# Pseudo road-tier id of the railway checkbox inside the Roads composite.
RAILWAY_ROAD_ID = "railway"
RAILWAY_FLAG = EXTRAS_BY_KIND["RAILWAY"].flag
# Sub-flag ticked when the composite is switched on with nothing ticked.
BOOTSTRAP_FLAGS = ("el_xSkiActive",)

# Overlap priority for Single/Multi Extruder mode, highest first (a kind
# subtracts every kind before it): lines sit on top of every area, small
# areas on top of the big ones; Ski Area -- which contains the forests,
# scree and glaciers of a whole resort -- lies under all of them.
PRIORITY_HIGH = ("SKILIFT", "SKISLOPE", "SKINORDIC", "RAILWAY", "RUNWAY")
PRIORITY_MID = ("SPORTS", "GOLF", "BEACH")
PRIORITY_LOW = ("SKIAREA",)


@dataclass(frozen=True)
class CustomDef:
    """One enabled, valid custom slot, as last read from the scene."""

    name: str
    geometry: str  # "LINE" or "AREA"
    rules: tuple
    width: float
    area: float
    signature: str


# kind -> CustomDef for every custom slot currently in use. Written on the
# main thread by _sync_custom (every kind_active check of a custom kind --
# generation, prefetch and progress planning all go through it before any
# worker starts), read by the Overpass worker threads. Replaced, never
# mutated, so a worker always sees a consistent dict.
_custom_defs: dict = {}


def custom_slot(tp3d, index):
    """The scene's custom slot *index*, or None if it isn't in use."""
    slots = getattr(tp3d, "custom_osm", None)
    if slots is None or index >= min(len(slots), getattr(tp3d, "custom_osm_count", 0)):
        return None
    return slots[index]


def _sync_custom(tp3d, kind):
    global _custom_defs
    extra = EXTRAS_BY_KIND[kind]
    slot = custom_slot(tp3d, extra.custom_slot)
    definition = None
    if slot is not None and slot.enabled:
        parsed = _custom.parse_rules(slot.tags)
        if parsed.ok:
            definition = CustomDef(
                name=slot.name.strip() or extra.label,
                geometry=slot.geometry,
                rules=parsed.rules,
                width=slot.width,
                area=slot.area,
                signature=_custom.signature(parsed.rules, slot.geometry),
            )
    if _custom_defs.get(kind) != definition:
        defs = dict(_custom_defs)
        if definition is None:
            defs.pop(kind, None)
        else:
            defs[kind] = definition
        _custom_defs = defs
    return definition is not None


def kind_active(tp3d, kind):
    """Its composite's master checkbox + the kind's own sub-flag (map size
    not checked). A custom slot must also be in use, enabled and have valid
    tags."""
    extra = EXTRAS_BY_KIND[kind]
    on = bool(getattr(tp3d, extra.master, False) and getattr(tp3d, extra.flag, False))
    if extra.custom_slot >= 0:
        return _sync_custom(tp3d, kind) and on
    return on


def custom_def(kind):
    return _custom_defs.get(kind)


def is_line_kind(kind):
    """Built-in line extras, and custom slots set to Line."""
    if kind in LINE_KINDS:
        return True
    definition = _custom_defs.get(kind)
    return definition is not None and definition.geometry == "LINE"


def open_ways_are_lines(kind):
    """Whether open ways of *kind* become ribbons at all. A custom Area slot
    ignores them (an open way has no area); every other kind keeps the usual
    open-way-is-a-line rule."""
    definition = _custom_defs.get(kind)
    return definition is None or definition.geometry == "LINE"


def area_threshold(tp3d, kind):
    """Area threshold (mm2) of an extra kind: 0 for lines."""
    if is_line_kind(kind):
        return 0.0
    definition = _custom_defs.get(kind)
    if definition is not None:
        return definition.area
    return tp3d.el_xArea


def cache_signature(kind):
    """Extra cache-key part for a custom slot's current rules ('' otherwise)."""
    definition = _custom_defs.get(kind)
    return definition.signature if definition is not None else ""


def display_label(kind):
    """A custom slot's own name, else the kind's label."""
    definition = _custom_defs.get(kind)
    if definition is not None:
        return definition.name
    return EXTRAS_BY_KIND[kind].label


def any_active(tp3d, master=MASTER_FLAG):
    """Any kind of the Extras group (or of *master*'s composite) is on."""
    return any(kind_active(tp3d, x.kind) for x in EXTRA_KINDS if x.master == master)


def chip_key(kind):
    """Fetch-progress chip a kind reports to: Roads for railways, else Extras."""
    return "roads" if EXTRAS_BY_KIND[kind].master == "show_roads" else CHIP_KEY


def line_width(tp3d, kind):
    """The printed width (mm) setting a kind's ribbons use."""
    definition = _custom_defs.get(kind)
    if definition is not None:
        return definition.width
    return getattr(tp3d, EXTRAS_BY_KIND[kind].width_attr)


def coloring_entries(max_size):
    """(result_key, active_flag, max_size, phase_label, fetch_message) rows,
    the same layout as ui_state.coloring_elements()."""
    return [
        (
            x.kind.lower(),
            (lambda t, _k=x.kind: kind_active(t, _k)),
            max_size,
            x.label,
            f"Fetching {x.label.lower()} data…",
        )
        for x in EXTRA_KINDS
    ]


def overpass_filters(kinds):
    """Filter statements for every extra kind in *kinds*."""
    out = []
    for kind in kinds:
        extra = EXTRAS_BY_KIND.get(kind)
        if extra is None:
            continue
        definition = _custom_defs.get(kind)
        if definition is not None:
            out.extend(_custom.overpass_filters(definition.rules, definition.geometry))
        else:
            out.extend(extra.filters)
    return out


def _custom_matches(kind, tags, element):
    definition = _custom_defs.get(kind)
    if definition is None or not _custom.matches(definition.rules, tags):
        return False
    if element.get("type") == "relation":
        return definition.geometry == "AREA" and tags.get("type") == "multipolygon"
    return True


def classify(element, active_kinds):
    """Every extra kind in *active_kinds* that *element* belongs to.

    Separate from fetch_group._classify_element (which picks exactly one
    kind): a ski slope is very often the same way as a highway=track, a
    railway can be a bridge over a river -- an extra must not steal the way
    from roads/water, nor be lost to them.
    """
    if element.get("type") == "node":
        return []
    tags = element.get("tags") or {}
    if not tags:
        return []
    out = []
    for kind in active_kinds:
        extra = EXTRAS_BY_KIND.get(kind)
        if extra is None:
            continue
        if extra.custom_slot >= 0:
            if _custom_matches(kind, tags, element):
                out.append(kind)
        elif extra.match(tags, element):
            out.append(kind)
    return out


def closed_way_is_area(kind, tags):
    """A closed way of a line kind (a loop piste, a circular railway) is
    still a line unless tagged area=yes; every other kind keeps the usual
    closed-way-is-a-polygon rule."""
    if is_line_kind(kind):
        return (tags or {}).get("area") == "yes"
    return True


def is_line_way(kind, tags, closed):
    """Whether a way of *kind* is drawn as a line (prefetch preview)."""
    if kind not in EXTRAS_BY_KIND:
        return False
    if is_line_kind(kind):
        return not (closed and closed_way_is_area(kind, tags))
    return not closed and open_ways_are_lines(kind)


def _parse_width_m(value):
    if not value:
        return None
    try:
        width = float(str(value).split()[0].replace(",", "."))
    except (ValueError, IndexError):
        return None
    return width if width > 0 else None


def ribbon_half_width(kind, tags, scale_hor, line_width_mm):
    """Half width (Blender units = printed mm) of the ribbon an open way of
    *kind* becomes. Runways use their real width (width=*, default 45 m),
    never thinner than Line Width; everything else is exactly Line Width."""
    half = max(0.01, float(line_width_mm)) / 2.0
    if kind == "RUNWAY":
        width_m = _parse_width_m((tags or {}).get("width")) or _DEFAULT_RUNWAY_WIDTH_M
        # scale_hor is mm per km (geo.convert_to_blender_coordinates).
        half = max(half, width_m / 1000.0 * float(scale_hor) / 2.0)
    return half
