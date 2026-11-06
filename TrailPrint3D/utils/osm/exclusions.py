"""User-disabled OSM elements (picked in the map generator's prefetch preview).

The picker page lists every prefetched feature by its OSM key ("way/123",
"relation/45"); the ones the user switched off are sent back with the
/confirm payload, stored here for the duration of one generation, and dropped
from every fetched tile by filter_excluded() -- right where the tile data
enters the pipeline, so coloring_main/create_buildings/create_roads never see
them. The on-disk Overpass cache itself is never modified.
"""

_excluded: frozenset = frozenset()


def set_excluded(keys) -> None:
    global _excluded
    _excluded = frozenset(str(k) for k in (keys or ()))


def clear_excluded() -> None:
    set_excluded(())


def get_excluded() -> frozenset:
    return _excluded


def filter_excluded(data, excluded=None):
    """Return *data* (an Overpass response dict) without the excluded elements.

    Never mutates *data* -- returns a shallow copy with a new "elements" list
    when anything was dropped, otherwise *data* itself.

    Dropping a relation also drops its untagged member ways (the skeleton
    ways `>;` pulls in): with the relation gone nothing consumes them as
    ring parts any more, so the pipeline would otherwise treat each one as a
    stand-alone polygon. A member way is kept if a surviving relation still
    references it, or if it is a tagged feature in its own right.
    """
    if excluded is None:
        excluded = _excluded
    if not excluded or not data:
        return data
    elements = data.get("elements")
    if not elements:
        return data

    dropped_relation_members: set = set()
    kept_relation_members: set = set()
    drop_keys: set = set()
    for el in elements:
        el_type = el.get("type")
        if el_type == "node":
            continue
        key = f"{el_type}/{el.get('id')}"
        is_excluded = key in excluded
        if is_excluded:
            drop_keys.add(key)
        if el_type == "relation":
            target = dropped_relation_members if is_excluded else kept_relation_members
            for member in el.get("members", ()):
                if member.get("type") == "way":
                    target.add(member.get("ref"))
    if not drop_keys:
        return data

    orphaned = dropped_relation_members - kept_relation_members
    kept = []
    for el in elements:
        el_type = el.get("type")
        if el_type != "node":
            if f"{el_type}/{el.get('id')}" in drop_keys:
                continue
            if el_type == "way" and not el.get("tags") and el.get("id") in orphaned:
                continue
        kept.append(el)
    filtered = dict(data)
    filtered["elements"] = kept
    return filtered
