import math

import bpy  # type: ignore
import numpy as np  # type: ignore
from bpy.app.translations import pgettext_iface as _

from .. import constants as const


def calculate_scale(mapSize, coordinates, gen_type, diagonal=False):

    pathScale = bpy.context.scene.tp3d.pathScale

    if bpy.app.debug:
        print(f"Gen_type: {gen_type}")

    min_lat = min(point[0] for point in coordinates)
    max_lat = max(point[0] for point in coordinates)
    min_lon = min(point[1] for point in coordinates)
    max_lon = max(point[1] for point in coordinates)

    x1, y1, _e = convert_to_neutral_coordinates(min_lat, min_lon, 0,0)
    x2, y2, _e = convert_to_neutral_coordinates(max_lat, max_lon, 0,0)

    width = abs(x2 - x1)
    height = abs(y2 - y1)
    distance = math.sqrt((x2 - x1)**2 + (y2 - y1)**2)

    maxer = max(width, height, distance) if diagonal else max(width, height)
    # Area-based generation types (2/3) fill the whole map; trail-based ones
    # leave a pathScale margin around the trail.
    if gen_type == 2 or gen_type == 3:
        scale = mapSize / maxer
    else:
        scale = (mapSize * pathScale) / maxer

    return scale

def map_scale_ratio():
    """The current map's real-world scale as a "1:X" string, the same value as the
    generator pages' Scale readout (rect_editor.js's tp3dScaleDenominator and
    tp3dSetSizeReadout): sScaleHor is mm per Mercator km, and Mercator is only
    true-scale along one parallel, so this is the scale at the map's center
    latitude, to 3 significant figures."""
    tp3d = bpy.context.scene.tp3d
    latitude, _lon = convert_to_geo(tp3d.o_centerx, tp3d.o_centery)
    denominator = 1_000_000 * math.cos(math.radians(latitude)) / tp3d.sScaleHor
    magnitude = 10 ** max(0, math.floor(math.log10(denominator)) - 2)
    return f"1:{round(denominator / magnitude) * magnitude}"

def convert_to_blender_coordinates(lat, lon, elevation,timestamp):

    scaleHor = bpy.context.scene.tp3d.sScaleHor
    autoScale = bpy.context.scene.tp3d.sAutoScale
    scaleElevation = bpy.context.scene.tp3d.scaleElevation

    R = const.R
    x = R * math.radians(lon) * scaleHor
    y = R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * scaleHor
    z = elevation / 1000 * scaleElevation * autoScale


    return (x, y, z)

def convert_to_blender_coordinates_batch(coords) -> list[tuple[float, float, float]]:
    """Vectorized batch version of convert_to_blender_coordinates.

    coords: iterable of (lat, lon, elevation, timestamp)
    Returns a list of (x, y, z) tuples — same interface as repeated scalar calls,
    but reads scene properties once and does all math in numpy.
    """
    scaleHor = bpy.context.scene.tp3d.sScaleHor
    autoScale = bpy.context.scene.tp3d.sAutoScale
    scaleElevation = bpy.context.scene.tp3d.scaleElevation
    R = const.R

    arr = np.array([(c[0], c[1], c[2]) for c in coords], dtype=np.float64)
    if len(arr) == 0:
        return []

    lats = arr[:, 0]
    lons = arr[:, 1]
    elevs = arr[:, 2]

    x = R * np.radians(lons) * scaleHor
    y = R * np.log(np.tan(np.pi / 4.0 + np.radians(lats) / 2.0)) * scaleHor
    z = elevs / 1000.0 * scaleElevation * autoScale

    return list(zip(x.tolist(), y.tolist(), z.tolist()))


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

def parallel_distance(lat, lon1, lon2):
    """East-west distance in km along the parallel at *lat*. Unlike
    haversine() it never wraps: a 360-degree span (e.g. -180..180) is a full
    circumference, not 0, so a map's width can't collapse to nothing."""
    return const.R * math.radians(abs(lon2 - lon1)) * math.cos(math.radians(lat))


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


def calculate_total_length(points) -> float:
    #Calculates the total path length in kilometers.
    if len(points) < 2:
        return 0.0
    # Vectorised haversine — same column order as the scalar loop above
    arr = np.array([(p[0], p[1]) for p in points], dtype=np.float64)
    phi1 = np.radians(arr[:-1, 0])
    phi2 = np.radians(arr[1:,  0])
    dphi = np.radians(arr[1:,  0] - arr[:-1, 0])
    dlam = np.radians(arr[1:,  1] - arr[:-1, 1])
    a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlam / 2) ** 2
    c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1.0 - a))
    return float(np.sum(const.R * c))

def calculate_total_elevation(points) -> float:
    #Calculates the total elevation gain in meters.
    if len(points) < 2:
        return 0.0
    elevs = np.array([p[2] for p in points], dtype=np.float64)
    diffs = np.diff(elevs)
    return float(np.sum(diffs[diffs > 0]))

def calculate_total_time(points) -> float:
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

def calculate_date(points) -> str:
    #Calculates the total time taken between the first and last points.
    if len(points) < 2:
        return ""
    st = points[0][3]

    if st:
        dt = str(st.date())
    else:
        dt = ""

    return dt

def separate_duplicate_xy(coordinates, offset=0.05, threshold=0.01):
    accepted = []

    for i, point in enumerate(coordinates):
        if isinstance(point, tuple):
            point = list(point)
            coordinates[i] = point

        x, y, z = point[0], point[1], point[2]

        # Keep bumping by another offset and re-checking against every
        # accepted point until clear -- a single fixed-offset nudge sends
        # every duplicate of the same earlier point to the identical new
        # spot, just re-collapsing them onto each other instead of
        # separating them.
        bump = 0
        while any(
            math.sqrt((x - prev[0])**2 + (y - prev[1])**2 + (z - prev[2])**2) < threshold
            for prev in accepted
        ):
            bump += 1
            y = point[1] + bump * offset
            z = point[2] + bump * offset

        point[1], point[2] = y, z
        accepted.append((point[0], point[1], point[2]))

    return coordinates

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
        raise ValueError(_("Direction must be 'n', 's', 'e', or 'w'"))

    # Convert radians back to degrees
    new_lat = math.degrees(lat_rad)
    new_lon = math.degrees(lon_rad)

    return new_lat, new_lon
