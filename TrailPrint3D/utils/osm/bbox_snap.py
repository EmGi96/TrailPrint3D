"""Snap tile bboxes to ones a picker prefetch already fetched.

The Overpass disk cache is keyed on the tile bbox rounded to 7 decimals
(~1 cm). The bbox the picker computes for a prefetch and the one the generator
later derives from its real mesh describe the same area, but Blender stores
vertices as float32, so they routinely differ by ~1e-6 degrees -- enough to
land on a different cache file. Snapping the *cache key's* bbox to the
prefetched one (within ~2 m) makes the generator reuse what was prefetched.
The Overpass query itself still uses the bbox it was given.

Session-only state: after a Blender restart the cache files are still there,
they just aren't aliased any more (worst case: one refetch).
"""

SNAP_TOLERANCE_DEG = 2e-5
_MAX_TARGETS = 64

_targets: list = []


def register(bboxes) -> None:
    """Remember prefetched tile bboxes as snap targets."""
    for bbox in bboxes:
        bbox = tuple(bbox)
        if bbox not in _targets:
            _targets.append(bbox)
    del _targets[:-_MAX_TARGETS]


def clear() -> None:
    _targets.clear()


def snap(bbox):
    """The registered bbox within SNAP_TOLERANCE_DEG of *bbox* on every side,
    else *bbox* unchanged."""
    for target in _targets:
        if all(abs(a - b) <= SNAP_TOLERANCE_DEG for a, b in zip(bbox, target)):
            return target
    return bbox
