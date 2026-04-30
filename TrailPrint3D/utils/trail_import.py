import bpy  # type: ignore
import xml.etree.ElementTree as ET
import os
from datetime import datetime


def _parse_points(points, point_type):
    segcoords = []
    lowestElevation = float("inf")

    for pt in points:
        lat = float(pt.get("lat"))
        lon = float(pt.get("lon"))

        ele = None
        time = None

        for c in pt:
            tag = c.tag.split("}")[-1]
            if tag == "ele":
                ele = c
            elif tag == "time":
                time = c

        elevation = float(ele.text) if ele is not None else 0.0

        try:
            timestamp = (
                datetime.fromisoformat(time.text.replace("Z", "+00:00"))
                if time is not None else None
            )
        except Exception:
            timestamp = None

        segcoords.append((lat, lon, elevation, timestamp))
        lowestElevation = min(lowestElevation, elevation)

    bpy.context.scene.tp3d["o_verticesPath"] = f"{point_type} Path vertices: {len(segcoords)}"

    return segcoords


def read_gpx(filepath):
    """
    Universal GPX reader.
    Supports:
    - GPX 1.1 / 1.0
    - trk / trkseg / trkpt
    - rte / rtept
    - files without namespaces
    """

    tree = ET.parse(filepath)
    root = tree.getroot()

    segmentlist = []

    # --------------------------------------------------
    # Namespace handling (GPX 1.1 / 1.0 / none)
    # --------------------------------------------------
    def strip_ns(tag):
        return tag.split("}")[-1]

    def findall_any(elem, names):
        return [e for e in elem.iter() if strip_ns(e.tag) in names]

    def find_child(elem, names):
        for c in elem:
            if strip_ns(c.tag) in names:
                return c
        return None

    # --------------------------------------------------
    # Track segments
    # --------------------------------------------------
    trksegs = findall_any(root, ["trkseg"])

    if trksegs:
        for seg in trksegs:
            points = [p for p in seg if strip_ns(p.tag) == "trkpt"]
            if points:
                segmentlist.append(
                    _parse_points(points, "TRKPT")
                )

    # --------------------------------------------------
    # Routes (fallback or additional if no segments found)
    # --------------------------------------------------
    routes = findall_any(root, ["rte"])
    for rte in routes:
        points = [p for p in rte if strip_ns(p.tag) == "rtept"]
        if points:
            segmentlist.append(
                _parse_points(points, "RTEPT")
            )

    # --------------------------------------------------
    # Edge case: GPX with direct trkpt/rtept (rare but real)
    # --------------------------------------------------
    if not segmentlist:
        points = findall_any(root, ["trkpt", "rtept"])
        if points:
            segmentlist.append(
                _parse_points(points, "POINT")
            )

    return segmentlist


def read_igc(filepath):
    """Reads an IGC file and extracts the coordinates, elevation, and timestamps."""
    segmentlist = []
    coordinates = []
    lowestElevation = 10000

    with open(filepath, 'r') as file:
        for line in file:
            # IGC B records contain position data
            if line.startswith('B'):
                try:
                    # Extract time (HHMMSS)
                    time_str = line[1:7]
                    hours = int(time_str[0:2])
                    minutes = int(time_str[2:4])
                    seconds = int(time_str[4:6])

                    # Extract latitude (DDMMmmmN/S)
                    lat_str = line[7:15]
                    lat_deg = int(lat_str[0:2])
                    lat_min = int(lat_str[2:4])
                    lat_min_frac = int(lat_str[4:7]) / 1000.0
                    lat = lat_deg + (lat_min + lat_min_frac) / 60.0
                    if lat_str[7] == 'S':
                        lat = -lat

                    # Extract longitude (DDDMMmmmE/W)
                    lon_str = line[15:24]
                    lon_deg = int(lon_str[0:3])
                    lon_min = int(lon_str[3:5])
                    lon_min_frac = int(lon_str[5:8]) / 1000.0
                    lon = lon_deg + (lon_min + lon_min_frac) / 60.0
                    if lon_str[8] == 'W':
                        lon = -lon

                    # Extract pressure altitude (in meters)
                    pressure_alt = int(line[25:30])

                    # Extract GPS altitude (in meters)
                    gps_alt = int(line[30:35])

                    # Create timestamp (using current date since IGC files don't store date in B records)
                    now = datetime.now()
                    timestamp = datetime(now.year, now.month, now.day, hours, minutes, seconds)

                    # Use GPS altitude for elevation
                    elevation = gps_alt

                    coordinates.append((lat, lon, elevation, timestamp))

                    if elevation < lowestElevation:
                        lowestElevation = elevation

                except (ValueError, IndexError) as e:
                    print(f"Error parsing IGC line: {line.strip()}")
                    continue

    bpy.context.scene.tp3d["o_verticesPath"] = "Path vertices: " + str(len(coordinates))

    segmentlist.append(coordinates)
    return segmentlist


def read_gpx_directory(directory_path):
    """Reads all GPX files in a directory and extracts coordinates, elevation, and timestamps."""

    # Define GPX namespace
    ns = {'default': 'http://www.topografix.com/GPX/1/1'}

    # List to store all coordinates from all GPX files, grouped by file.
    # Structure: [[seg1, seg2, ...], [seg1, ...], ...] — one inner list per file,
    # each inner list contains that file's track segments.
    coordinatesByFile = []
    lowestElevation = 10000  # High initial value

    # Iterate over all files in the directory
    for filename in os.listdir(directory_path):
        if filename.lower().endswith(".gpx") or filename.lower().endswith(".igc"):
            filepath = os.path.join(directory_path, filename)

            file_extension = os.path.splitext(filepath)[1].lower()
            if file_extension == '.gpx':
                tree = ET.parse(filepath)
                root = tree.getroot()
                version = root.get("version")
                print(f"File Name: {filename}, File Version: {version}")
                co = read_gpx(filepath)
            elif file_extension == '.igc':
                co = read_igc(filepath)

            # Keep all segments from this file together as a group
            if co:
                coordinatesByFile.append(co)
                for coseg in co:
                    lowest = min(coseg, key=lambda x: x[2])
                    lowest_In_coords = lowest[2]
                    if lowest_In_coords < lowestElevation:
                        lowestElevation = lowest_In_coords
                        print(f"new Lowest Elevation: {lowestElevation}")

    # Flatten for vertex count reporting
    coordinatesSeparate = [seg for file_segs in coordinatesByFile for seg in file_segs]
    coordinates = [pt for seg in coordinatesSeparate for pt in seg]

    # Store the number of points in the Blender scene property
    bpy.context.scene.tp3d["o_verticesPath"] = f"Path vertices: {len(coordinates)}"

    print(f"Total GPX files processed: {len(coordinatesByFile)}")

    return coordinatesByFile


def read_gpx_file():

    gpx_file_path = bpy.context.scene.tp3d.get('file_path', None)

    coords = []
    file_extension = os.path.splitext(gpx_file_path)[1].lower()
    if file_extension == '.gpx':
        tree = ET.parse(gpx_file_path)
        root = tree.getroot()
        version = root.get("version")

        ns = {'default': root.tag.split('}')[0].strip('{')}
        GPXsections = len(root.findall(".//default:trkseg", ns))
        print(f"GPX Sections found in GPX File: {GPXsections}")
        coords = read_gpx(gpx_file_path)
    elif file_extension == '.igc':
        coords = read_igc(gpx_file_path)
    else:
        from . import show_message_box  # deferred to avoid circular import at load time
        show_message_box("Unsupported file format. Please use .gpx or .igc files.")
        return

    return coords


def read_gpx_and_create_heightmap(length=100.0, height=20.0):
    from .geo import haversine  # deferred to avoid circular import at load time

    gpx_file_path = bpy.context.scene.tp3d.get('file_path', None)
    if not gpx_file_path or not os.path.exists(gpx_file_path):
        print("Invalid or missing GPX file path.")
        return

    points = []
    total_distance = 0.0
    max_elevation = float('-inf')
    min_elevation = float('inf')
    prev_point = None

    separate_paths = read_gpx_file()
    temppoints = [item for sublist in separate_paths for item in sublist]

    for i, pnt in enumerate(temppoints):
        lat = pnt[0]
        lon = pnt[1]
        ele = pnt[2]
        elevation = ele if ele is not None else 0.0
        if prev_point:
            dist = haversine(prev_point[0], prev_point[1], lat, lon)
            total_distance += dist

        points.append((lat, lon, elevation, total_distance))
        max_elevation = max(max_elevation, elevation)
        min_elevation = min(min_elevation, elevation)
        prev_point = (lat, lon)

    if total_distance == 0 or not points:
        print("No valid points or zero-length route.")
        return

    curve_data = bpy.data.curves.new(name='RouteProfile', type='CURVE')
    curve_data.dimensions = '2D'
    curve_data.fill_mode = 'BOTH'
    spline = curve_data.splines.new('POLY')
    spline.use_cyclic_u = True  # Close the shape

    profile_points = []

    print(f"Min elevation: {min_elevation}")

    for lat, lon, elevation, distance in points:
        x = (distance / total_distance) * length - length/2
        y = ((elevation-min_elevation) / (max_elevation-min_elevation)) * (height-2) + 2
        profile_points.append((x, y))

    bottom_left = (profile_points[0][0], -0.0)
    bottom_right = (profile_points[-1][0], -0.0)

    full_points = profile_points + [bottom_right, bottom_left]

    spline.points.add(len(full_points) - 1)

    for i, (x, y) in enumerate(full_points):
        spline.points[i].co = (x, y, 0.0, 1.0)

    curve_obj = bpy.data.objects.new("RouteProfileSurface", curve_data)
    bpy.context.collection.objects.link(curve_obj)

    bpy.context.view_layer.objects.active = curve_obj
    curve_obj.select_set(True)
    bpy.ops.object.convert(target='MESH')

    mesh_obj = bpy.context.object
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.extrude_region_move(TRANSFORM_OT_translate={"value": (0, 0, 1)})
    bpy.ops.object.editmode_toggle()

    print(f"Route length: {total_distance:.2f} meters")
    print(f"Maximum elevation: {max_elevation:.2f} meters")

    curve_obj.location = bpy.context.scene.cursor.location

    return curve_obj
