import bpy  # type: ignore
import bmesh  # type: ignore
import math
import os
import json
import time
import requests
import hashlib
from collections import deque
from mathutils import Vector  # type: ignore
from .. import constants as const
from .. import progress as _progress


def fetch_osm_data(bbox, kind="WATER", max_cache_age_hours=720, return_cache_status=False):
    #print("FETCH OSM:", kind)

    disableCache = bpy.context.scene.tp3d.disableCache
    apiRetries = bpy.context.scene.tp3d.apiRetries
    mapsize = bpy.context.scene.tp3d.sMapInKm
    road_big   = bool(bpy.context.scene.tp3d.el_sBigActive)
    road_med   = bool(bpy.context.scene.tp3d.el_sMedActive)
    road_small = bool(bpy.context.scene.tp3d.el_sSmallActive)
    water_ponds       = bool(bpy.context.scene.tp3d.col_wPondsActive)
    water_small_rivers = bool(bpy.context.scene.tp3d.col_wSmallRiversActive)
    water_big_rivers  = bool(bpy.context.scene.tp3d.col_wBigRiversActive)

    def get_cache_dir():
        path = const.overpass_cache_dir
        os.makedirs(path, exist_ok=True)
        return path


    def make_cache_key(bbox, kind):
        south, west, north, east = bbox
        payload = {
            "bbox": [round(south, 7), round(west, 7), round(north, 7), round(east, 7)],
            "kind": kind
        }
        data = json.dumps(payload, sort_keys=True).encode("utf-8")
        return hashlib.sha256(data).hexdigest()

    cache_dir = get_cache_dir()
    cache_key = make_cache_key(bbox, kind)
    if kind == "STREETS":
        cache_key = make_cache_key(bbox, kind + str(road_big) + str(road_med) + str(road_small))
    if kind == "WATER":
        cache_key = make_cache_key(bbox, kind + str(water_ponds) + str(water_small_rivers) + str(water_big_rivers))
    cache_path = os.path.join(cache_dir, f"{cache_key}.json")

    # --------------------------------------------------
    # Use cache if fresh
    # --------------------------------------------------
    if os.path.exists(cache_path) and disableCache == 0:
        age_hours = (time.time() - os.path.getmtime(cache_path)) / 3600
        if age_hours < max_cache_age_hours:
            print("Cached Data found")
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return (data, True) if return_cache_status else data


    south, west, north, east = bbox
    # Clamp to valid geographic ranges — guards against antimeridian padding overflow
    west = max(-180.0, min(180.0, west))
    east = max(-180.0, min(180.0, east))
    south = max(-90.0, min(90.0, south))
    north = max(-90.0, min(90.0, north))
    overpass_url = "https://overpass-api.de/api/interpreter"

    # --------------------------------------------------
    # Build query
    # Each entry is a callable (south, west, north, east, **ctx) -> query string.
    # ctx carries extra context (e.g. mapsize) for kinds that need dynamic filters.
    # To add a new OSM kind, add one entry to this dict.
    # --------------------------------------------------
    def _bbox_header(s, w, n, e):
        return f"[out:json][timeout:60][bbox:{s},{w},{n},{e}]"

    def _simple_query(s, w, n, e, filters):
        """Build a standard area query from a list of tag-filter strings."""
        lines = "\n".join(f"        {f};" for f in filters)
        return f"""
        {_bbox_header(s, w, n, e)};
        (
{lines}
        );
        out body;
        >;
        out skel qt;
        """

    OSM_QUERY_BUILDERS = {
        "WATER": lambda s, w, n, e, ponds=True, small_rivers=True, big_rivers=True, **_: _build_water_query(s, w, n, e, ponds, small_rivers, big_rivers),
        "FOREST": lambda s, w, n, e, **_: _simple_query(s, w, n, e, [
            'way["natural"="wood"]',
            'relation["natural"="wood"]',
            'way["landuse"="forest"]',
            'relation["landuse"="forest"]',
        ]),
        "SCREE": lambda s, w, n, e, **_: _simple_query(s, w, n, e, [
            'nwr["natural"="scree"]',
            'nwr["natural"="stone"]',
            'nwr["natural"="boulder"]',
            'nwr["natural"="rock"]',
            'nwr["natural"="bare_rock"]',
        ]),
        "CITY": lambda s, w, n, e, **_: _simple_query(s, w, n, e, [
            'way["landuse"~"residential|urban|commercial|industrial"]',
            'relation["landuse"~"residential|urban|commercial|industrial"]',
        ]),
        "GREENSPACE": lambda s, w, n, e, **_: _simple_query(s, w, n, e, [
            'way["leisure"="park"]',
            'relation["leisure"="park"]',
            'way["leisure"="garden"]',
            'relation["leisure"="garden"]',
            'way["leisure"="recreation_ground"]',
            'relation["leisure"="recreation_ground"]',
            'way["landuse"="grass"]',
            'way["natural"="grass"]',
            'way["landuse"="village_green"]',
            'relation["landuse"="village_green"]',
        ]),
        "FARMLAND": lambda s, w, n, e, **_: _simple_query(s, w, n, e, [
            'way["landuse"="farmland"]',
            'way["landuse"="farmyard"]',
            'relation["landuse"="farmland"]',
            'relation["landuse"="farmyard"]',
        ]),
        "GLACIER": lambda s, w, n, e, **_: _simple_query(s, w, n, e, [
            'way["natural"="glacier"]',
            'relation["natural"="glacier"]',
        ]),
        "COASTLINE": lambda s, w, n, e, **_: _simple_query(s, w, n, e, [
            'way["natural"="coastline"]',
        ]),
        "BUILDINGS": lambda s, w, n, e, **_: _simple_query(s, w, n, e, [
            'nwr["building"]',
        ]),
        "STREETS": lambda s, w, n, e, mapsize=0, big=True, med=True, small=False, **_: _build_streets_query(s, w, n, e, mapsize, big, med, small),
    }

    def _build_water_query(s, w, n, e, ponds, small_rivers, big_rivers):
        filters = []
        if ponds:
            filters += [
                'way["natural"="water"]',
                'relation["natural"="water"]',
                'way["water"~"river|lake|stream|canal"]',
                'relation["water"~"river|lake|stream|canal"]',
            ]
        if small_rivers:
            # No wikidata filter — includes all minor waterways
            filters.append('way["waterway"~"stream|river|canal|ditch|drain"]')
        elif big_rivers:
            # Only major named rivers (wikidata-tagged)
            filters.append('way["waterway"~"stream|river|canal|ditch|drain"]["wikidata"]')
        if big_rivers and small_rivers:
            # small_rivers already covers big ones; wikidata filter would be redundant
            pass
        if not filters:
            # Fallback: return an empty result query
            return f"{_bbox_header(s, w, n, e)};\n(  );\nout body;\n>;\nout skel qt;"
        return _simple_query(s, w, n, e, filters)

    def _build_streets_query(s, w, n, e, mapsize, big, med, small):
        all_big   = {'primary', 'motorway', 'primary_link', 'motorway_link'}
        all_med   = {'secondary', 'tertiary', 'secondary_link', 'tertiary_link', 'unclassified', 'trunk', 'trunk_link'}
        all_small = {'residential', 'living_street', 'service', 'footway'}

        # Build user-requested set
        requested = set()
        if big:   requested |= all_big
        if med:   requested |= all_med
        if small: requested |= all_small

        # Apply mapsize performance limits (larger maps = fewer road types allowed)
        allowed = all_big | all_med | all_small
        if mapsize > const.ROADS_MAXSIZE:
            allowed = all_big
        elif mapsize > const.STREETS_PRIMARY_THRESHOLD:
            allowed = all_big | all_med
        elif mapsize > const.STREETS_MAJOR_ONLY_THRESHOLD:
            allowed = all_big | all_med | all_small

        highway_types = sorted(requested & allowed)
        if not highway_types:
            highway_types = ['motorway', 'primary']
        pattern = '|'.join(highway_types)
        filter_str = f'["highway"~"^({pattern})$"]'
        print(f"Filter str: {filter_str}")
        return _simple_query(s, w, n, e, [f"way{filter_str}"])

    builder = OSM_QUERY_BUILDERS.get(kind)
    if builder is None:
        raise ValueError(f"Unknown OSM kind: {kind}")
    query = builder(south, west, north, east, mapsize=mapsize, big=road_big, med=road_med, small=road_small,
                    ponds=water_ponds, small_rivers=water_small_rivers, big_rivers=water_big_rivers)

    # --------------------------------------------------
    # Request with retries
    # --------------------------------------------------
    for attempt in range(apiRetries):
        try:
            response = requests.post(
                overpass_url,
                data={"data": query},
                headers={'User-Agent': 'TrailPrint3D_3.00', 'Accept': '*/*'},
                timeout=60
            )
            print(query)

            if response.status_code == 200:
                data = response.json()

                # --------------------------------------------------
                # Write cache
                # --------------------------------------------------
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(data, f)

                return (data, False) if return_cache_status else data

            elif response.status_code != 200:
                retry_num = attempt + 2
                print(f"Status ({response.status_code}), retrying... {retry_num}/{apiRetries}")
                _ov = _progress.ProgressOverlay.get()
                if _ov.active:
                    _ov.update(message=f"Overpass error — retrying {retry_num}/{apiRetries}")
                time.sleep(5 + attempt)

        except requests.exceptions.Timeout:
            retry_num = attempt + 2
            print(f"Request timed out (attempt {attempt + 1}/{apiRetries})")
            _ov = _progress.ProgressOverlay.get()
            if _ov.active:
                _ov.update(message=f"Timed out — retrying {retry_num}/{apiRetries}")
            time.sleep(5)
        except Exception as e:
            print("Request failed:", e)
            time.sleep(5)

    print("Overpass request failed after retries")
    _progress.WarningsOverlay.add_warning(f"failed to fetch {kind} elements from Overpass API", "error")
    return None


def extract_multipolygon_bodies(elements, nodes):
    # Helper to get coordinates of a way by its node ids
    def way_coords(way):
        return [ (nodes[nid]['lat'], nodes[nid]['lon'], nodes[nid].get('elevation', 0)) for nid in way['nodes'] if nid in nodes ]

    # Store all multipolygon lakes as lists of outer rings (each ring = list of coords)
    multipolygon_lakes = []
    multipolycon_negatives = []

    # Index ways by their id for quick lookup
    way_dict = {el['id']: el for el in elements if el['type'] == 'way'}

    for el in elements:
        if el['type'] == 'relation':
            # Collect outer and inner member ways
            outer_ways = []
            inner_ways = []

            for member in el.get('members', []):
                if member['type'] != 'way':
                    continue
                way = way_dict.get(member['ref'])
                if not way:
                    continue

                role = member.get('role', '')
                if role == 'outer':
                    outer_ways.append(way)
                elif role == 'inner':
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
                    print(f"Skipping OSM outer ring with {len(loop)} nodes (limit {OSM_MAX_POLYGON_VERTS})")
                    _progress.WarningsOverlay.add_warning("once Very large instance polygon was removed due to its complex shape", "warn")
                    continue
                multipolygon_lakes.append(loop)
            for loop in inner_loops:
                if len(loop) > OSM_MAX_POLYGON_VERTS:
                    _progress.WarningsOverlay.add_warning("once Very large instance polygon was removed due to its complex shape", "warn")
                    print(f"Skipping OSM inner ring with {len(loop)} nodes (limit {OSM_MAX_POLYGON_VERTS})")
                    continue
                multipolycon_negatives.append(loop)
    return multipolygon_lakes, multipolycon_negatives


def calculate_polygon_area_2d(coords):
    area = 0.0

    if len(coords) >= 3:

        n = len(coords)
        for i in range(n):
            x0, y0, z0 = coords[i]
            x1, y1, z1 = coords[(i + 1) % n]  # Wrap around to the first point
            area += (x0 * y1) - (x1 * y0)

    return abs(area) * 0.5

def build_osm_nodes(data):
    nodes = {}
    for element in data['elements']:
        if element['type'] == 'node':
            nodes[element['id']] = element
    return nodes

def get_building_data(bbox, max_retries=5, timeout=90):

    apiRetries = bpy.context.scene.tp3d.apiRetries


    south, west, north, east = bbox

    overpass_url = "https://overpass-api.de/api/interpreter"
    query = f"""
    [out:json][timeout:{timeout}];
    (
    way["building"]({south},{west},{north},{east});
    );
    out body;
    >;
    out skel qt;
    """

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(overpass_url, params={'data': query}, headers={'User-Agent': 'TrailPrint3D_3.00', 'Accept': '*/*'}, timeout=timeout)


            if response.status_code != 200:

                print(f"Status ({response.status_code}), retrying... {attempt + 1}/{apiRetries}")
            else:
                # Try to parse JSON
                try:
                    data = response.json()
                    return data  # Success
                except ValueError:
                    print(f"Attempt {attempt}: Invalid JSON response")

        except requests.RequestException as e:
            print(f"Attempt {attempt}: Request error: {e}")

        # If not successful, wait before retrying
        if attempt < max_retries:
            wait_time = 2 + 1 * attempt  # exponential-ish backoff
            print(f"Retrying in {wait_time} seconds...")
            time.sleep(wait_time)

    # If we reach here, all retries failed
    print("Failed to fetch data from Overpass API after multiple attempts.")
    return None

def create_buildings(map, default_height=10, scaleHor=1.0):
    from .geo import convert_to_blender_coordinates  # deferred to avoid circular import at load time
    from .mesh_ops import recalculateNormals, RaycastPointToMeshZ  # deferred to avoid circular import at load time
    from .scene import remove_objects  # deferred to avoid circular import at load time

    # Copy map and extrude vertical faces outward
    wall_obj = map.copy()
    wall_obj.data = map.data.copy()
    bpy.context.collection.objects.link(wall_obj)
    bpy.ops.object.select_all(action='DESELECT')
    wall_obj.select_set(True)
    bpy.context.view_layer.objects.active = wall_obj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='FACE')
    bpy.ops.mesh.select_all(action='DESELECT')
    bm = bmesh.from_edit_mesh(wall_obj.data)
    for f in bm.faces:
        f.select = abs(f.normal.normalized().z) < 0.1  # near-vertical faces
    bmesh.update_edit_mesh(wall_obj.data)
    bpy.ops.mesh.extrude_region_shrink_fatten(TRANSFORM_OT_shrink_fatten={"value": 20.0})
    bpy.ops.object.mode_set(mode='OBJECT')


    minThickness = bpy.context.scene.tp3d.minThickness
    minLat = bpy.context.scene.tp3d.minLat
    minLon = bpy.context.scene.tp3d.minLon
    maxLat = bpy.context.scene.tp3d.maxLat
    maxLon = bpy.context.scene.tp3d.maxLon


    lat_step = 2
    lon_step = 2


    if maxLat - minLat < lat_step:
        lat_step = maxLat - minLat
    if maxLon - minLon < lon_step:
        lon_step = maxLon - minLon

    lats = math.ceil((maxLat - minLat) / lat_step)
    lons = math.ceil((maxLon - minLon) / lon_step)


    if lats * lons < 20:
        for k in range(lats):
            for l in range(lons):
                _cntr = (k) * lons + l + 1
                _maxcntr = lats * lons
                print(f"Buildings loop: {_cntr}/{_maxcntr}")
                _ov = _progress.ProgressOverlay.get()
                if _ov.active:
                    _ov.update(message=f"Buildings: tile {_cntr}/{_maxcntr} — fetching…")
                    _ov.set_fetch_progress('buildings', _cntr / _maxcntr)
                south = minLat + k * lat_step
                north = south + lat_step
                west = minLon + l * lon_step
                east = west + lon_step

                bbox = (south, west, north, east)
                data = []

                data = fetch_osm_data(bbox,"BUILDINGS")

                if not data or "elements" not in data:
                    print("No Building data returned")
                    return None

                n_buildings = len([e for e in data['elements'] if e['type'] == 'way'])
                if _ov.active:
                    _ov.update(message=f"Buildings: tile {_cntr}/{_maxcntr} — calculating {n_buildings} buildings…")
                # Cache node id -> (lat, lon) and node id -> (x, y, z_base) to avoid repeated conversions
                raw_nodes = {n['id']: (n['lat'], n['lon']) for n in data['elements'] if n['type'] == 'node'}

                # compute 2D coordinates once per node (z=0 for now)
                node_xy = {}
                for nid, (nlat, nlon) in raw_nodes.items():
                    x, y, z = convert_to_blender_coordinates(nlat, nlon, 0, scaleHor)
                    node_xy[nid] = (x, y, nlat, nlon)  # keep lat/lon if you need to re-evaluate elevation later

                verts = []   # list of (x, y, z)
                faces = []   # list of index lists (quads/ngons)
                vert_count = 0

                def safe_float_height(h):
                    # supports strings like "10", "10.0", "10 m"
                    if h is None:
                        return float(default_height)
                    if isinstance(h, (int, float)):
                        return float(h)
                    try:
                        s = str(h).strip().lower()
                        # strip units like "m"
                        if s.endswith('m'):
                            s = s[:-1].strip()
                        return float(s)
                    except Exception:
                        return float(default_height)


                # Build a lookup for ways by id, so relations can reference them
                ways_by_id = {e['id']: e for e in data['elements'] if e['type'] == 'way'}

                relation_elements = [e for e in data['elements'] if e['type'] == 'relation']

                for i,element in enumerate(data['elements']):
                    if element['type'] == 'relation':
                        # Find the outer member way and use its nodes as the footprint
                        outer_way = None
                        for member in element.get('members', []):
                            if member.get('type') == 'way' and member.get('role') == 'outer':
                                outer_way = ways_by_id.get(member['ref'])
                                if outer_way:
                                    break
                        if outer_way is None:
                            continue
                        # Treat the relation like the outer way but use relation tags if present
                        node_ids = outer_way.get('nodes', [])
                        tags = element.get('tags') or outer_way.get('tags', {})
                    elif element['type'] == 'way':
                        node_ids = element.get('nodes', [])
                        tags = element.get('tags', {})
                    else:
                        continue

                    # build 2D footprint coords from cached node_xy
                    footprint = []
                    for nid in node_ids:
                        if nid in node_xy:
                            x, y, nlat, nlon = node_xy[nid]
                            footprint.append((x, y, nlat, nlon))
                    if len(footprint) < 3:
                        continue

                    height = safe_float_height(tags.get('height', default_height))
                    levels = safe_float_height(tags.get("building:levels", 0))
                    if levels != 0:
                        height = levels * 2.7
                    base_elevation = 0


                    zTerrainOffset = RaycastPointToMeshZ((x,y,100),wall_obj)
                    if zTerrainOffset == None:
                        zTerrainOffset = minThickness

                    # convert base_elevation to Blender z once per building (avoid reconverting lat/lon for each node)
                    z_offset = height * 0.002 * scaleHor * bpy.context.scene.tp3d.el_bHeightMultiplier

                    n = len(footprint)
                    # Add bottom verts
                    for (x, y, nlat, nlon) in footprint:
                        # Convert node to Blender coords once (with base elevation)
                        xb, yb, zb = convert_to_blender_coordinates(nlat, nlon, 0, scaleHor)
                        verts.append((xb, yb, base_elevation + zTerrainOffset))
                    # Add top verts (same XY, Z + height)
                    for (x, y, nlat, nlon) in footprint:

                        xb, yb, zb = convert_to_blender_coordinates(nlat, nlon, 0, scaleHor)
                        verts.append((xb, yb, base_elevation + z_offset + zTerrainOffset))

                    # Indices for bottom and top
                    base = vert_count
                    bottom_idx = [base + i for i in range(n)]
                    top_idx    = [base + n + i for i in range(n)]

                    # Add faces:
                    # bottom (note: Blender's face winding is important; reverse for bottom)
                    faces.append(bottom_idx[::-1])  # bottom polygon (reverse winding so normal faces down)
                    # top
                    faces.append(top_idx)          # top polygon

                    # sides: build quads (a, b, c, d) as bottom_i, bottom_i+1, top_i+1, top_i
                    for i in range(n):
                        i_next = (i + 1) % n
                        a = base + i
                        b = base + i_next
                        c = base + n + i_next
                        d = base + n + i
                        faces.append([a, b, c, d])

                    vert_count += n * 2

                obj = None

                if _ov.active:
                    _ov.update(message=f"Buildings: tile {_cntr}/{_maxcntr} — creating {n_buildings} buildings…")

                if vert_count > 0:
                    # Create mesh and object
                    mesh = bpy.data.meshes.new("building_mesh")
                    mesh.from_pydata(verts, [], faces)
                    mesh.update(calc_edges=True)

                    # Create object
                    obj = bpy.data.objects.new("Buildings", mesh)
                    bpy.context.collection.objects.link(obj)

                    # Recalculate normals once after the mesh is built
                    mesh.validate(verbose=False)
                    mesh.update(calc_edges=True)
                    bpy.context.view_layer.update()

                    for poly in mesh.polygons:
                        poly.use_smooth = False  # flat shading for buildings; change if desired

                    # Finally, ensure object normals are correct
                    recalculateNormals(obj)

                    mat = bpy.data.materials.get("BUILDINGS")
                    obj.data.materials.clear()
                    obj.data.materials.append(mat)


    remove_objects(wall_obj)

    return obj

def highway_default_width(highway):
    mapping = {
        "motorway": 6.0, "trunk": 6.0, "primary": 6.0, "secondary": 6.0, "footway": 6,
        "tertiary": 6.0, "residential": 6.0, "service": 6.0, "track": 6.0, "path": 6
    }
    return mapping.get(highway, 6.0)


def create_roads(map, default_height=10, scaleHor=1.0, mapsize = 1):
    from .geo import convert_to_blender_coordinates  # deferred to avoid circular import at load time
    from .mesh_ops import extrude_plane, selectBottomFacesByZ, remeshClearing, boolean_operation  # deferred to avoid circular import at load time
    from .scene import set_origin_to_3d_cursor  # deferred to avoid circular import at load time

    minLat = bpy.context.scene.tp3d.minLat
    minLon = bpy.context.scene.tp3d.minLon
    maxLat = bpy.context.scene.tp3d.maxLat
    maxLon = bpy.context.scene.tp3d.maxLon

    streetwidthMultiplier = bpy.context.scene.tp3d.el_sMultiplier


    lat_step = 2
    lon_step = 2


    if maxLat - minLat < lat_step:
        lat_step = maxLat - minLat
    if maxLon - minLon < lon_step:
        lon_step = maxLon - minLon

    lats = math.ceil((maxLat - minLat) / lat_step)
    lons = math.ceil((maxLon - minLon) / lon_step)


    if lats * lons < 20:
        for k in range(lats):
            for l in range(lons):
                _cntr = (k) * lons + l + 1
                _maxcntr = lats * lons
                print(f"Roads loop: {_cntr}/{_maxcntr}")
                _ov = _progress.ProgressOverlay.get()
                if _ov.active:
                    _ov.update(message=f"Roads: tile {_cntr}/{_maxcntr} — fetching…")
                    _ov.set_fetch_progress('roads', _cntr / _maxcntr)

                south = minLat + k * lat_step
                north = south + lat_step
                west = minLon + l * lon_step
                east = west + lon_step

                bbox = (south, west, north, east)

                data = []

                data = fetch_osm_data(bbox, "STREETS")

                if not data or "elements" not in data:
                    print("No Road data returned")
                    return None

                # Build node dict (id -> (lat, lon))
                nodes = {el['id']:(el['lat'], el['lon']) for el in data['elements'] if el['type'] == "node"}
                print(f"Road nodes: {len(nodes)}")
                n_roads = len([e for e in data['elements'] if e['type'] == 'way'])
                if _ov.active:
                    _ov.update(message=f"Roads: tile {_cntr}/{_maxcntr} — calculating {n_roads} road segments…")

                # Cache converted coordinates per node id (as Vector)
                coord_cache = {}
                for nid, (lat, lon) in nodes.items():
                    x, y, z = convert_to_blender_coordinates(lat, lon, 0, scaleHor)
                    coord_cache[nid] = Vector((x, y, 0))

                wm = bpy.context.window_manager
                wm.progress_begin(0, max(1, len(data['elements'])))

                # Group geometry by width key (rounded width to avoid float equality issues)
                groups = {}
                element_count = len(data['elements'])
                any_adjusted = False

                for idx, el in enumerate(data['elements']):
                    wm.progress_update(int(idx * 100 / element_count))
                    if el['type'] != "way":
                        continue
                    node_ids = el.get("nodes", [])
                    pts = [coord_cache[nid] for nid in node_ids if nid in coord_cache]
                    if len(pts) < 2:
                        continue

                    tags = el.get("tags", {}) or {}

                    # Determine width_m (prefer explicit width, then lanes, then highway fallback)
                    width_m = None
                    if "width" in tags:
                        try:
                            s = str(tags["width"]).strip().lower()
                            if s.endswith("m"):
                                s = s[:-1]
                            width_m = float(s)
                        except Exception:
                            width_m = None
                    if width_m is None and "lanes" in tags:
                        try:
                            width_m = float(tags["lanes"]) * 3.0
                        except Exception:
                            width_m = None
                    if width_m is None:
                        width_m = highway_default_width(tags.get("highway"))
                    width_m = highway_default_width(tags.get("highway"))

                    # Grouping key -- round to millimeters to avoid tiny float diffs
                    key = round(width_m, 3)

                    if key not in groups:
                        groups[key] = {
                            'width_m': width_m,
                            'verts': [],
                            'faces': [],
                            'vert_count': 0
                        }
                    group = groups[key]

                    # Keep same streetWidth logic as before
                    streetWidth = (width_m * 0.5) * 0.2 * scaleHor * 0.02 * streetwidthMultiplier

                    print(f"Road width: {streetWidth}")

                    if streetWidth <= 0.1:
                        streetWidth *= 0.1/streetWidth
                        any_adjusted = True
                        print(f"Adjusted road width: {streetWidth}")


                    # Compute segment directions and per-node perpendiculars (2D perp)
                    seg_dirs = []
                    for a, b in zip(pts[:-1], pts[1:]):
                        d = (b - a)
                        if d.length == 0:
                            seg_dirs.append(Vector((0.0, 0.0, 0.0)))
                        else:
                            seg_dirs.append(d.normalized())

                    perp_at = []
                    npts = len(pts)
                    for i_pt in range(npts):
                        if i_pt == 0:
                            dir_vec = seg_dirs[0]
                        elif i_pt == npts - 1:
                            dir_vec = seg_dirs[-1]
                        else:
                            s = seg_dirs[i_pt - 1] + seg_dirs[i_pt]
                            if s.length == 0:
                                dir_vec = seg_dirs[i_pt - 1] if seg_dirs[i_pt - 1].length != 0 else seg_dirs[i_pt]
                            else:
                                dir_vec = s.normalized()
                        perp = Vector((-dir_vec.y, dir_vec.x, 0.0))
                        if perp.length != 0:
                            perp = perp.normalized()
                        perp_at.append(perp)

                    # Create left/right vertices for each node, add to group's vert list
                    idx_pairs = []
                    for p, perp in zip(pts, perp_at):
                        terrainOffsetPoint = Vector((0,0,0))
                        left = p + perp * streetWidth + terrainOffsetPoint
                        right = p - perp * streetWidth + terrainOffsetPoint
                        group['verts'].append((left.x, left.y, left.z))
                        group['verts'].append((right.x, right.y, right.z))
                        a_idx = group['vert_count']
                        b_idx = group['vert_count'] + 1
                        idx_pairs.append((a_idx, b_idx))
                        group['vert_count'] += 2

                    # Create quads between consecutive node pairs (top faces)
                    for j in range(len(idx_pairs) - 1):
                        a_left, a_right = idx_pairs[j]
                        b_left, b_right = idx_pairs[j + 1]
                        group['faces'].append((a_left, b_left, b_right, a_right))

                wm.progress_end()

                if _ov.active:
                    _ov.update(message=f"Roads: tile {_cntr}/{_maxcntr} — creating {n_roads} road mesh…")

                # Create mesh objects for each group
                created_objects = []
                for key in sorted(groups.keys()):
                    group = groups[key]
                    if not group['verts'] or not group['faces']:
                        continue

                    width_m = group['width_m']
                    streetWidth = (width_m * 0.5) * 0.2 * scaleHor * 0.02 * streetwidthMultiplier

                    mesh_name = f"Road_w{str(key).replace('.', '_')}"
                    mesh = bpy.data.meshes.new(mesh_name)
                    mesh.from_pydata(group['verts'], [], group['faces'])
                    mesh.update(calc_edges=True)

                    obj_name = f"Road_{str(key)}"
                    obj = bpy.data.objects.new(obj_name, mesh)
                    bpy.context.collection.objects.link(obj)

                    extrude_plane(obj,30)

                    # store width metadata
                    obj["width"] = width_m

                    created_objects.append(obj)


                # If nothing was created, return None
                if not created_objects:
                    return None


                # Apply modifiers on each object (only Solidify in this case)
                # To safely apply modifiers we need each object to be active in the view layer
                original_active = bpy.context.view_layer.objects.active
                original_selection = list(bpy.context.selected_objects)

                # Deselect all first
                bpy.ops.object.select_all(action='DESELECT')



        for obj in created_objects:
            try:
                obj.select_set(True)
                bpy.context.view_layer.objects.active = obj
                # apply all modifiers present (we expect Solidify only)
                # copy list because applying modifies the collection
                mods = [m.name for m in obj.modifiers]
                for mname in mods:
                    try:
                        bpy.ops.object.modifier_apply(modifier=mname)
                    except RuntimeError as e:
                        # in some contexts modifier_apply may fail; print and continue
                        print(f"Failed to apply modifier {mname} on {obj.name}: {e}")
                obj.select_set(False)
            except Exception as e:
                print(f"Error applying modifiers on {obj.name}: {e}")

        if _ov.active:
            _ov.update(message=f"Roads: Merge road segments into single object")


        # Merge (join) all created objects into a single object
        # Re-select all created_objects, set active to the first one and join
        bpy.ops.object.select_all(action='DESELECT')
        for o in created_objects:
            o.select_set(True)
        bpy.context.view_layer.objects.active = created_objects[0]

        try:
            bpy.ops.object.join()
        except Exception as e:
            print(f"Join failed: {e}")


        # The joined object is now the active object
        merged_obj = bpy.context.view_layer.objects.active

        # Shade flat and assign material
        try:
            bpy.ops.object.shade_flat()
        except Exception:
            pass

        # restore selection / active if needed (optional)
        bpy.ops.object.select_all(action='DESELECT')
        if merged_obj:
            merged_obj.select_set(True)
            bpy.context.view_layer.objects.active = merged_obj
        else:
            # restore previous state
            for o in original_selection:
                o.select_set(True)
            bpy.context.view_layer.objects.active = original_active

        roads = merged_obj

        set_origin_to_3d_cursor(roads)

        selectBottomFacesByZ(roads)
        bpy.ops.mesh.select_all(action='INVERT')
        bpy.ops.mesh.delete(type='VERT')
        bpy.ops.object.mode_set(mode='OBJECT')

        if _ov.active:
            _ov.update(message=f"Roads: Remeshing roads for clean geometry")

        remeshClearing(roads, 0.2, 0)


        boolean_operation(roads,map,"INTERSECT")

        selectBottomFacesByZ(roads)
        bpy.ops.mesh.delete(type='VERT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.extrude_region_move(TRANSFORM_OT_translate={"value": (0, 0, default_height)})
        bpy.ops.object.mode_set(mode='OBJECT')


        if any_adjusted:
            _progress.WarningsOverlay.add_warning("Some roads were too thin and made thicker", "warn")

        return roads


def is_bbox_overlapping(obj1, obj2):
    # Get world-space corners of bounding boxes
    bbox1 = [obj1.matrix_world @ Vector(corner) for corner in obj1.bound_box]
    bbox2 = [obj2.matrix_world @ Vector(corner) for corner in obj2.bound_box]

    # Calculate Min/Max for each axis
    def get_min_max(bbox):
        return [min(c[i] for c in bbox) for i in range(3)], [max(c[i] for c in bbox) for i in range(3)]

    min1, max1 = get_min_max(bbox1)
    min2, max2 = get_min_max(bbox2)

    # Standard AABB overlap test
    return all(max1[i] >= min2[i] and max2[i] >= min1[i] for i in range(3))


def create_element(bbox, elementHeight=1.0, scaleHor=1.0, kind = "WATER", baseHeight = 1):
    from .geo import convert_to_blender_coordinates  # deferred to avoid circular import at load time
    from .primitives import col_create_face_mesh, col_create_line_curve  # deferred to avoid circular import at load time
    from .mesh_ops import merge_objects, extrude_plane  # deferred to avoid circular import at load time

    col_Area = 5

    waypart = None

    data = []
    resp = fetch_osm_data(bbox, kind)

    created_objects = []
    elementDeleted = 0
    elementCreated = 0

    if resp == None:
        return None

    data = resp

    nodes = build_osm_nodes(data)
    bodies = extract_multipolygon_bodies(data['elements'], nodes)

    for i, coords in enumerate(bodies):
        blender_coords = [convert_to_blender_coordinates(lat, lon, ele, scaleHor) for lat, lon, ele in coords]
        blender_coords = [(x,y,0) for (x, y, z) in blender_coords]
        calcArea = calculate_polygon_area_2d(blender_coords)
        if calcArea > col_Area:
            tobj = col_create_face_mesh(f"Relation_{i}", blender_coords)
            created_objects.append(tobj)
            elementCreated += 1
        else:
            elementDeleted += 1

    wm = bpy.context.window_manager
    wm.progress_begin(0, 100)

    for i, element in enumerate(data['elements']):
        wm.progress_update(i*100/len(nodes))

        coords = []
        for node_id in element.get('nodes', []):
            if node_id in nodes:
                node = nodes[node_id]
                x,y,_ = convert_to_blender_coordinates(node['lat'], node['lon'], 0, scaleHor)
                coord = (x,y,0)
                coords.append(coord)
        tArea = calculate_polygon_area_2d(coords)
        if len(coords) < 2 or (tArea < col_Area and element['type'] != 'way'):
            elementDeleted += 1
            continue

        tags = element.get("tags", {})
        if coords[0] == coords[-1] and kind != "COASTLINE":
            tobj = col_create_face_mesh(f"coloredObject_{i}", coords)
            created_objects.append(tobj)
            elementCreated += 1
        elif kind == "COASTLINE":
            tobj = col_create_line_curve(f"coloredObject_{i}", coords, close=False)
            created_objects.append(tobj)
            tobj.select_set(False)
            elementCreated += 1

            waypart = tobj

    wm.progress_end()

    time.sleep(1)  # Pause to prevent request throttling


    if elementCreated > 0:
        print(f"created: {elementCreated} of {kind}")
        element = merge_objects(created_objects)

        element.name = kind

        bpy.ops.object.shade_flat()

        if kind == "WATER":
            mat = bpy.data.materials.get("BLUE")
        elif kind == "GREEN":
            mat = bpy.data.materials.get("GREEN")
        else:
            mat = bpy.data.materials.get("BLACK")
        element.data.materials.clear()
        element.data.materials.append(mat)
        extrude_plane(element,elementHeight)
        return element
    else:
        return None
