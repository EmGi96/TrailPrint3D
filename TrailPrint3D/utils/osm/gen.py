from collections import deque

from mathutils import Vector

from ... import progress as _progress


def extract_multipolygon_bodies(elements, nodes):
    # Helper to get coordinates of a way by its node ids
    def way_coords(way):
        return [
            (nodes[nid]["lat"], nodes[nid]["lon"], nodes[nid].get("elevation", 0))
            for nid in way["nodes"]
            if nid in nodes
        ]

    # Store all multipolygon lakes as lists of outer rings (each ring = list of coords)
    multipolygon_lakes = []
    multipolycon_negatives = []

    # Index ways by their id for quick lookup
    way_dict = {el["id"]: el for el in elements if el["type"] == "way"}

    for el in elements:
        if el["type"] == "relation":
            # Collect outer and inner member ways
            outer_ways = []
            inner_ways = []

            for member in el.get("members", []):
                if member["type"] != "way":
                    continue
                way = way_dict.get(member["ref"])
                if not way:
                    continue

                role = member.get("role", "")
                if role == "outer":
                    outer_ways.append(way)
                elif role == "inner":
                    inner_ways.append(way)

            # Stitch ways to closed loops for outer and inner rings
            def stitch_ways(ways):
                loops = []
                # Convert ways to deque of coord lists for O(1) popleft
                ways_dq = deque(way_coords(w) for w in ways)

                while ways_dq:
                    current = ways_dq.popleft()
                    changed = True
                    while changed:
                        changed = False
                        remaining = deque()
                        while ways_dq:
                            w = ways_dq.popleft()
                            if not w:
                                continue
                            # Check if current end connects to w start or end
                            if current[-1] == w[0]:
                                current.extend(w[1:])
                                changed = True
                            elif current[-1] == w[-1]:
                                current.extend(reversed(w[:-1]))
                                changed = True
                            # Also check if current start connects to w end or start
                            elif current[0] == w[-1]:
                                current = w[:-1] + current
                                changed = True
                            elif current[0] == w[0]:
                                current = list(reversed(w[1:])) + current
                                changed = True
                            else:
                                remaining.append(w)
                        ways_dq = remaining
                    loops.append(current)

                return loops

            outer_loops = stitch_ways(outer_ways)
            inner_loops = stitch_ways(inner_ways)

            OSM_MAX_POLYGON_VERTS = 300000
            for loop in outer_loops:
                if len(loop) > OSM_MAX_POLYGON_VERTS:
                    print(
                        f"Skipping OSM outer ring with {len(loop)} nodes (limit {OSM_MAX_POLYGON_VERTS})"
                    )
                    _progress.WarningsOverlay.add_warning(
                        "once Very large instance polygon was removed due to its complex shape",
                        "warn",
                    )
                    continue
                multipolygon_lakes.append(loop)
            for loop in inner_loops:
                if len(loop) > OSM_MAX_POLYGON_VERTS:
                    _progress.WarningsOverlay.add_warning(
                        "once Very large instance polygon was removed due to its complex shape",
                        "warn",
                    )
                    print(
                        f"Skipping OSM inner ring with {len(loop)} nodes (limit {OSM_MAX_POLYGON_VERTS})"
                    )
                    continue
                multipolycon_negatives.append(loop)
    return multipolygon_lakes, multipolycon_negatives


def build_osm_nodes(data):
    nodes = {}
    for element in data["elements"]:
        if element["type"] == "node":
            nodes[element["id"]] = element
    return nodes


def is_bbox_overlapping(obj1, obj2):
    # Get world-space corners of bounding boxes
    bbox1 = [obj1.matrix_world @ Vector(corner) for corner in obj1.bound_box]
    bbox2 = [obj2.matrix_world @ Vector(corner) for corner in obj2.bound_box]

    # Calculate Min/Max for each axis
    def get_min_max(bbox):
        return [min(c[i] for c in bbox) for i in range(3)], [
            max(c[i] for c in bbox) for i in range(3)
        ]

    min1, max1 = get_min_max(bbox1)
    min2, max2 = get_min_max(bbox2)

    # Standard AABB overlap test
    return all(max1[i] >= min2[i] and max2[i] >= min1[i] for i in range(3))


def fetch_coastline_ways(prefetched_tiles, scaleHor):
    """Extract raw directed coastline way sequences from pre-fetched Overpass data.

    Returns a list of coordinate chains: each chain is a list of (x, y) tuples
    in Blender space, in OSM way direction (land-is-left convention).
    Closed ways (first node == last node) are returned as closed chains.
    No Blender objects are created.  No bpy.context reads.

    Parameters
    ----------
    prefetched_tiles : dict  {bbox -> (data_dict, from_cache_bool)}
                       The COASTLINE entry from the prefetch result dict.
    scaleHor         : float  horizontal scale factor
    """
    import math as _math

    from ... import constants as _const  # type: ignore

    def _ll_to_bl(lat, lon):
        """Inline Mercator → Blender XY, elevation fixed at 0."""
        x = _const.R * _math.radians(lon) * scaleHor
        y = (
            _const.R
            * _math.log(_math.tan(_math.pi / 4 + _math.radians(lat) / 2))
            * scaleHor
        )
        return (x, y)

    chains = []
    seen_way_ids = set()

    for bbox, (data, _from_cache) in prefetched_tiles.items():  # noqa: PERF102 (data, _from_cache) requires key-value pairs.
        if not data or "elements" not in data:
            continue

        nodes = {el["id"]: el for el in data["elements"] if el["type"] == "node"}

        for el in data["elements"]:
            if el["type"] != "way":
                continue
            if el["id"] in seen_way_ids:
                continue
            if el.get("tags", {}).get("natural") != "coastline":
                continue
            seen_way_ids.add(el["id"])

            node_ids = el.get("nodes", [])
            pts = []
            for nid in node_ids:
                if nid not in nodes:
                    continue
                nd = nodes[nid]
                pts.append(_ll_to_bl(nd["lat"], nd["lon"]))

            if len(pts) >= 2:
                chains.append(pts)

    return chains
