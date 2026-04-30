import bpy  # type: ignore
import math
from .. import constants as const


def calculate_scale(mapSize, coordinates, gen_type):

    scalemode = bpy.context.scene.tp3d.scalemode
    pathScale = bpy.context.scene.tp3d.pathScale

    print(f"Scalemode: {scalemode}")
    print(f"Gen_type: {gen_type}")

    min_lat = min(point[0] for point in coordinates)
    max_lat = max(point[0] for point in coordinates)
    min_lon = min(point[1] for point in coordinates)
    max_lon = max(point[1] for point in coordinates)

    R = const.R

    x1, y1, e = convert_to_neutral_coordinates(min_lat, min_lon, 0,0)
    x2, y2, e = convert_to_neutral_coordinates(max_lat, max_lon, 0,0)

    if scalemode == "FACTOR" and gen_type != 2:
        width = abs(x2 - x1)
        height = abs(y2 - y1)
        distance = math.sqrt((x2 - x1)**2 + (y2 - y1)**2)

    else:
        width = haversine(min_lat, min_lon, min_lat, max_lon) * 1
        height = haversine(min_lat, min_lon, max_lat,min_lon) * 1
        distance = haversine(min_lat,min_lon,max_lat,max_lon)*1


    if scalemode == "SCALE":
        mx1 = x1 = R * math.radians(min_lon) * math.cos(math.radians(min_lat))
        mx2 = x2 = R * math.radians(max_lon) * math.cos(math.radians(max_lat))
        mwidth = abs(mx1 - mx2)
        mf = 1/width * mwidth
        mf = 1

    if scalemode == "COORDINATES" or scalemode == "SCALE":
        distance = 0


    maxer = max(width,height, distance)

    scale = 1
    if scalemode == "COORDINATES" or gen_type == 2 or gen_type == 3:
        print("scalemode1")
        scale = mapSize / maxer
    elif scalemode == "FACTOR":
        print("scalemode2")
        scale = (mapSize * pathScale) / maxer
    elif scalemode == "SCALE":
        print("scalemode3")
        scale = pathScale * mf

    print(f"Scale: {scale}")

    return scale

def convert_to_blender_coordinates(lat, lon, elevation,timestamp):

    scaleHor = bpy.context.scene.tp3d.sScaleHor
    autoScale = bpy.context.scene.tp3d.sAutoScale
    scaleElevation = bpy.context.scene.tp3d.scaleElevation

    R = const.R
    x = R * math.radians(lon) * scaleHor
    y = R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * scaleHor
    z = elevation / 1000 * scaleElevation * autoScale


    return (x, y, z)

def convert_to_neutral_coordinates(lat, lon, elevation,timestamp):

    autoScale = bpy.context.scene.tp3d.sAutoScale
    scaleElevation = bpy.context.scene.tp3d.scaleElevation

    R = const.R
    x = R * math.radians(lon)
    y = R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    z = elevation / 1000 * scaleElevation * autoScale

    return (x, y, z)


def convert_to_geo(x,y):
    """Converts Blender x/y offsets to latitude/longitude."""

    scaleHor = bpy.context.scene.tp3d.sScaleHor

    R = const.R
    longitude = math.degrees((x) / (R * scaleHor) )
    latitude = math.degrees(2 * math.atan(math.exp((y) / (R * scaleHor) )) - math.pi / 2)
    return latitude, longitude

def haversine(lat1, lon1, lat2, lon2):
    """Calculates the great-circle distance between two points using the Haversine formula."""

    R = const.R
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    distance = R * c  # distance in kilometers
    return distance


def calculate_total_length(points):
    #Calculates the total path length in kilometers.
    total_distance = 0.0
    for i in range(1, len(points)):
        lon1, lat1, _, _ = points[i - 1]
        lon2, lat2, _, _ = points[i]
        total_distance += haversine(lon1, lat1, lon2, lat2)
    return total_distance

def calculate_total_elevation(points):
    #Calculates the total elevation gain in meters.
    total_elevation = 0.0
    for i in range(1, len(points)):
        _, _, elev1, _ = points[i - 1]
        _, _, elev2, _ = points[i]
        if elev2 > elev1:
            total_elevation += elev2 - elev1
    return total_elevation

def calculate_total_time(points):
    hrs = 0
    #Calculates the total time taken between the first and last points.
    if len(points) < 2:
        return 0.0
    st = points[0][3]
    et = points[-1][3]
    if st != None and et != None:
        time_diff = et - st
        hrs = time_diff.total_seconds() / 3600

    return hrs

def calculate_date(points):
    hrs = 0
    #Calculates the total time taken between the first and last points.
    if len(points) < 2:
        return ""
    st = points[0][3]
    print(st)
    print(type(st))

    if st:
        dt = str(st.date())
    else:
        dt = ""

    return dt

def separate_duplicate_xy(coordinates, offset=0.05):
    seen_xy = set()

    for i, point in enumerate(coordinates):
        # Convert tuple to list if needed
        if isinstance(point, tuple):
            point = list(point)
            coordinates[i] = point  # Update the original array with the list version

        x, y, z = point[0], point[1], point[2]
        xy_key = (x, y,z)

        if xy_key in seen_xy:
            point[2] += offset
            point[1] += offset
        else:
            seen_xy.add(xy_key)

    return(coordinates)

def midpoint_spherical(lat1, lon1, lat2, lon2):
    # Convert degrees to radians
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)

    # Convert to Cartesian coordinates
    x1 = math.cos(lat1_rad) * math.cos(lon1_rad)
    y1 = math.cos(lat1_rad) * math.sin(lon1_rad)
    z1 = math.sin(lat1_rad)

    x2 = math.cos(lat2_rad) * math.cos(lon2_rad)
    y2 = math.cos(lat2_rad) * math.sin(lon2_rad)
    z2 = math.sin(lat2_rad)

    # Average the vectors
    x = (x1 + x2) / 2
    y = (y1 + y2) / 2
    z = (z1 + z2) / 2

    # Convert back to spherical coordinates
    lon_mid = math.atan2(y, x)
    hyp = math.sqrt(x * x + y * y)
    lat_mid = math.atan2(z, hyp)

    # Convert radians back to degrees
    return math.degrees(lat_mid), math.degrees(lon_mid)

def move_coordinates(lat, lon, distance_km, direction):
    """
    Move a point a given distance (in km) in a cardinal direction (N, S, E, W).
    """
    R = const.R
    direction = direction.lower()

    # Convert latitude and longitude from degrees to radians
    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)

    if direction == "n":
        lat_rad += distance_km / R
    elif direction == "s":
        lat_rad -= distance_km / R
    elif direction == "e":
        lon_rad += distance_km / (R * math.cos(lat_rad))
    elif direction == "w":
        lon_rad -= distance_km / (R * math.cos(lat_rad))
    else:
        raise ValueError("Direction must be 'n', 's', 'e', or 'w'")

    # Convert radians back to degrees
    new_lat = math.degrees(lat_rad)
    new_lon = math.degrees(lon_rad)

    return new_lat, new_lon
