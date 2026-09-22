"""Minimal, dependency-free (besides numpy, which Blender always bundles) reader for
single-band GeoTIFF DEM files.

Blender does not bundle GDAL/rasterio, so this module hand-parses just enough
of the TIFF 6.0 + GeoTIFF 1.0 spec to read the DEM rasters that providers like
OpenTopography, Copernicus and USGS 3DEP hand out: uncompressed or
Deflate/LZW-compressed, strip- or tile-based, 8/16/32-bit integer or 32-bit
float samples, with an optional horizontal predictor. BigTIFF is explicitly
rejected rather than silently mis-sampled. Geographic (lat/lon) rasters are
read as-is; projected rasters are only supported when they use a recognized
WGS84/ETRS89/NAD83 UTM zone, in which case query coordinates are converted to
that zone's easting/northing with a hand-rolled Transverse Mercator forward
projection — any other projected CRS is rejected with a reprojection hint.

The actual pixel decode (per-strip/tile decompression, undoing the horizontal
predictor, and assembling band 0 into the output grid) is vectorized with numpy --
the equivalent pure-Python per-row/per-pixel loops were the dominant cost when
reading many DEM tiles in one generation run (e.g. a multi-tile folder, see
build_tile_index/sample_tile_index).
"""

import math
import pathlib
import struct
import zlib

import numpy as np

_TAG_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}
_TAG_TYPE_FORMATS = {1: 'B', 3: 'H', 4: 'I', 6: 'b', 8: 'h', 9: 'i', 11: 'f', 12: 'd'}

TAG_IMAGE_WIDTH = 256
TAG_IMAGE_LENGTH = 257
TAG_BITS_PER_SAMPLE = 258
TAG_COMPRESSION = 259
TAG_SAMPLES_PER_PIXEL = 277
TAG_ROWS_PER_STRIP = 278
TAG_STRIP_OFFSETS = 273
TAG_STRIP_BYTE_COUNTS = 279
TAG_PREDICTOR = 317
TAG_TILE_WIDTH = 322
TAG_TILE_LENGTH = 323
TAG_TILE_OFFSETS = 324
TAG_TILE_BYTE_COUNTS = 325
TAG_SAMPLE_FORMAT = 339
TAG_MODEL_PIXEL_SCALE = 33550
TAG_MODEL_TIEPOINT = 33922
TAG_GEO_KEY_DIRECTORY = 34735
TAG_GDAL_NODATA = 42113

GEOKEY_MODEL_TYPE = 1024
GEOKEY_PROJECTED_CS_TYPE = 3072

# a, f (flattening) for the ellipsoids behind the UTM EPSG families we recognize.
_ELLIPSOIDS = {
    "WGS84": (6378137.0, 1 / 298.257223563),
    "GRS80": (6378137.0, 1 / 298.257222101),  # ETRS89 / NAD83
}


class GeoTiffError(Exception):
    """Raised when a GeoTIFF can't be read by this minimal parser."""


def _utm_zone_from_epsg(code):
    """Map a Projected CRS EPSG code to (zone, hemisphere, ellipsoid) if it's a UTM zone we support."""
    if 32601 <= code <= 32660:
        return code - 32600, "N", "WGS84"
    if 32701 <= code <= 32760:
        return code - 32700, "S", "WGS84"
    if 25828 <= code <= 25838:
        return code - 25800, "N", "GRS80"  # ETRS89 / UTM zone NN N
    if 26901 <= code <= 26923:
        return code - 26900, "N", "GRS80"  # NAD83 / UTM zone NN N
    return None


def _latlon_to_utm(lat, lon, zone, hemisphere, ellipsoid):
    """Forward Transverse Mercator projection (Snyder's series expansion) for one UTM zone."""
    a, f = _ELLIPSOIDS[ellipsoid]
    k0 = 0.9996
    e2 = f * (2 - f)
    e2_2 = e2 * e2
    e2_3 = e2_2 * e2
    ep2 = e2 / (1 - e2)

    lat_rad = math.radians(lat)
    central_lon_rad = math.radians(zone * 6 - 183)
    a_rad = math.cos(lat_rad) * (math.radians(lon) - central_lon_rad)

    sin_lat = math.sin(lat_rad)
    tan_lat = math.tan(lat_rad)
    tan2, tan4 = tan_lat * tan_lat, tan_lat ** 4
    n = a / math.sqrt(1 - e2 * sin_lat * sin_lat)
    c = ep2 * math.cos(lat_rad) ** 2
    a2, a3, a4, a5, a6 = a_rad ** 2, a_rad ** 3, a_rad ** 4, a_rad ** 5, a_rad ** 6

    m = a * (
        (1 - e2 / 4 - 3 * e2_2 / 64 - 5 * e2_3 / 256) * lat_rad
        - (3 * e2 / 8 + 3 * e2_2 / 32 + 45 * e2_3 / 1024) * math.sin(2 * lat_rad)
        + (15 * e2_2 / 256 + 45 * e2_3 / 1024) * math.sin(4 * lat_rad)
        - (35 * e2_3 / 3072) * math.sin(6 * lat_rad)
    )

    easting = k0 * n * (
        a_rad + a3 / 6 * (1 - tan2 + c) + a5 / 120 * (5 - 18 * tan2 + tan4 + 72 * c - 58 * ep2)
    ) + 500000.0

    northing = k0 * (
        m + n * tan_lat * (
            a2 / 2 + a4 / 24 * (5 - tan2 + 9 * c + 4 * c * c) + a6 / 720 * (61 - 58 * tan2 + tan4 + 600 * c - 330 * ep2)
        )
    )
    if hemisphere == "S":
        northing += 10000000.0

    return easting, northing


def _utm_to_latlon(easting, northing, zone, hemisphere, ellipsoid):
    """Inverse Transverse Mercator projection (Snyder's series expansion) for one UTM zone."""
    a, f = _ELLIPSOIDS[ellipsoid]
    k0 = 0.9996
    e2 = f * (2 - f)
    e2_2 = e2 * e2
    e2_3 = e2_2 * e2
    ep2 = e2 / (1 - e2)
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))

    x = easting - 500000.0
    y = northing - (10000000.0 if hemisphere == "S" else 0.0)

    m = y / k0
    mu = m / (a * (1 - e2 / 4 - 3 * e2_2 / 64 - 5 * e2_3 / 256))

    p_rad = (
        mu
        + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
        + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
        + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
        + (1097 * e1 ** 4 / 512) * math.sin(8 * mu)
    )

    sin_p, cos_p, tan_p = math.sin(p_rad), math.cos(p_rad), math.tan(p_rad)
    c1 = ep2 * cos_p * cos_p
    c1_2 = c1 * c1
    t1 = tan_p * tan_p
    t1_2 = t1 * t1
    n1 = a / math.sqrt(1 - e2 * sin_p * sin_p)
    r1 = a * (1 - e2) / (1 - e2 * sin_p * sin_p) ** 1.5
    d = x / (n1 * k0)
    d2, d3, d4, d5, d6 = d ** 2, d ** 3, d ** 4, d ** 5, d ** 6

    lat_rad = p_rad - (n1 * tan_p / r1) * (
        d2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1_2 - 9 * ep2) * d4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1_2 - 252 * ep2 - 3 * c1_2) * d6 / 720
    )

    central_lon_rad = math.radians(zone * 6 - 183)
    lon_rad = central_lon_rad + (
        d - (1 + 2 * t1 + c1) * d3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1_2 + 8 * ep2 + 24 * t1_2) * d5 / 120
    ) / cos_p

    return math.degrees(lat_rad), math.degrees(lon_rad)


def _decode_values(raw, typ, cnt, endian):
    if typ == 2:  # ASCII
        return raw.split(b'\x00', 1)[0].decode('ascii', errors='replace')
    if typ in (5, 10):  # RATIONAL / SRATIONAL: cnt pairs of 4-byte ints
        fmt = 'II' if typ == 5 else 'ii'
        vals = struct.unpack(endian + fmt * cnt, raw[:8 * cnt])
        return tuple(vals[i] / vals[i + 1] for i in range(0, len(vals), 2))
    fmt = _TAG_TYPE_FORMATS.get(typ)
    if fmt is None:
        return raw
    size = _TAG_TYPE_SIZES[typ] * cnt
    return struct.unpack(endian + fmt * cnt, raw[:size])


def _read_ifd(data, offset, endian):
    (count,) = struct.unpack_from(endian + 'H', data, offset)
    tags = {}
    p = offset + 2
    for _i in range(count):
        tag, typ, cnt = struct.unpack_from(endian + 'HHI', data, p)
        size = _TAG_TYPE_SIZES.get(typ, 1) * cnt
        value_field_offset = p + 8
        if size <= 4:
            raw = data[value_field_offset:value_field_offset + size]
        else:
            (off,) = struct.unpack_from(endian + 'I', data, value_field_offset)
            raw = data[off:off + size]
        try:
            tags[tag] = _decode_values(raw, typ, cnt, endian)
        except struct.error:
            pass  # unrecognised/truncated tag — ignore, it's likely not one we need
        p += 12
    (next_ifd,) = struct.unpack_from(endian + 'I', data, p)
    return tags, next_ifd


def _first(tags, tag, default=None):
    val = tags.get(tag)
    if val is None:
        return default
    return val[0] if isinstance(val, tuple) else val


def _decompress_chunk(raw, compression):
    if compression == 1:
        return raw
    if compression in (8, 32946):  # Deflate / "old style" Deflate
        return zlib.decompress(raw)
    if compression == 5:
        return _lzw_decode(raw)
    raise GeoTiffError(
        f"unsupported TIFF compression method {compression} — re-export as "
        "uncompressed, Deflate, or LZW GeoTIFF (e.g. with gdal_translate -co COMPRESS=DEFLATE)"
    )


def _lzw_decode(data):
    """Decode a TIFF-flavoured LZW stream (early code-width change, MSB-first bits).

    LZW's dictionary is inherently sequential (each code's meaning depends on every
    code before it), so unlike the rest of this module's pixel decode, this can't be
    vectorized with numpy -- but the ORIGINAL bit reader extracted codes one bit at a
    time (data_len*8 Python-level loop iterations, each with its own function call),
    which dominates decode time for real DEM tiles: LZW compresses noisy float
    elevation data poorly, so a typical tile decodes to millions of short codes. This
    version keeps a small (never more than ~19 bits) byte-refilled bit accumulator
    instead, so each code costs one shift+mask against a small int rather than
    `code_width` separate single-bit extractions -- an 8x-or-more reduction in loop
    iterations for the bit reader, which was the actual bottleneck.
    """
    CLEAR = 256
    EOI = 257
    data_len = len(data)
    out = []

    def reset_table():
        return [bytes([i]) for i in range(256)] + [None, None]  # 256=CLEAR, 257=EOI placeholders

    table = reset_table()
    table_len = len(table)  # tracked manually -- len(table) in the hot loop below,
                             # called twice per code, was ~15% of total decode time
    code_width = 9
    prev = None
    pos = 0
    bitbuf = 0
    bitcnt = 0
    while True:
        while bitcnt < code_width:
            if pos >= data_len:
                bitcnt = -1  # signal "ran out mid-code" -> treat as end of stream
                break
            bitbuf = (bitbuf << 8) | data[pos]
            pos += 1
            bitcnt += 8
        if bitcnt < code_width:
            break
        bitcnt -= code_width
        code = (bitbuf >> bitcnt) & ((1 << code_width) - 1)
        bitbuf &= (1 << bitcnt) - 1

        if code == EOI:
            break
        if code == CLEAR:
            table = reset_table()
            table_len = len(table)
            code_width = 9
            prev = None
            continue
        if code < table_len and table[code] is not None:
            entry = table[code]
        elif code == table_len and prev is not None:
            entry = prev + prev[:1]
        else:
            raise GeoTiffError("corrupt LZW stream in GeoTIFF")
        out.append(entry)
        if prev is not None:
            table.append(prev + entry[:1])
            table_len += 1
            if table_len == 511:
                code_width = 10
            elif table_len == 1023:
                code_width = 11
            elif table_len == 2047:
                code_width = 12
        prev = entry
    return b''.join(out)


def _numpy_dtype(bits_per_sample, sample_format, endian):
    """Map a TIFF (BitsPerSample, SampleFormat) pair to a numpy dtype, matching the
    same cases the old per-sample struct.unpack format-char table covered."""
    if bits_per_sample == 32 and sample_format == 3:
        code = 'f4'
    elif bits_per_sample == 8:
        code = 'i1' if sample_format == 2 else 'u1'
    elif bits_per_sample == 16:
        code = 'i2' if sample_format == 2 else 'u2'
    elif bits_per_sample == 32:
        code = 'i4' if sample_format == 2 else 'u4'
    else:
        raise GeoTiffError(f"unsupported sample depth {bits_per_sample}-bit")
    return np.dtype(endian + code)


def _decode_chunk(decompressed, num_rows, row_width, samples_per_pixel, dtype, predictor):
    """Decode one decompressed strip/tile's bytes into a (num_rows, row_width) float32
    array of band-0 values, undoing the horizontal predictor if present.

    Vectorized with numpy: reinterpreting the whole chunk at once (instead of
    struct.unpack-ing one row at a time) and using cumsum for the predictor (instead
    of a Python-level per-pixel accumulation loop) is what actually matters for
    speed -- decoding a multi-megabyte DEM tile this way is roughly one to two
    orders of magnitude faster than the equivalent pure-Python loops, which is what
    made reading a many-tile DEM folder (see sample_tile_index) slow in practice.
    """
    count = num_rows * row_width * samples_per_pixel
    try:
        arr = np.frombuffer(decompressed, dtype=dtype, count=count).reshape(num_rows, row_width, samples_per_pixel)
    except ValueError as e:
        raise GeoTiffError(f"truncated or corrupt pixel data ({e})") from e

    if predictor == 2:
        # cumsum along the column axis, per row and per band-channel independently --
        # equivalent to the TIFF horizontal-differencing decode (each sample += the
        # same-band sample samples_per_pixel positions to its left), including the
        # dtype's own wraparound arithmetic for integer samples, same as the encoder
        # that produced the differenced bytes in the first place.
        arr = np.cumsum(arr, axis=1, dtype=dtype)
    elif predictor != 1:
        raise GeoTiffError(f"unsupported TIFF predictor {predictor} (only horizontal differencing is supported)")

    return arr[:, :, 0].astype(np.float32)


def _parse_geo_reference(tags):
    scale = tags.get(TAG_MODEL_PIXEL_SCALE)
    tiepoint = tags.get(TAG_MODEL_TIEPOINT)
    if not scale or not tiepoint or len(scale) < 2 or len(tiepoint) < 6:
        raise GeoTiffError("missing georeferencing tags (ModelPixelScale/ModelTiepoint) — is this a valid GeoTIFF?")

    pixel_scale_x, pixel_scale_y = scale[0], scale[1]
    i0, j0, _k0, x0, y0, _z0 = tiepoint[0:6]
    origin_x = x0 - i0 * pixel_scale_x
    origin_y = y0 + j0 * pixel_scale_y

    model_type = None
    proj_cs_code = None
    geokeys = tags.get(TAG_GEO_KEY_DIRECTORY)
    if geokeys and len(geokeys) >= 4:
        num_keys = geokeys[3]
        for k in range(num_keys):
            base = 4 + k * 4
            if base + 4 > len(geokeys):
                break
            key_id, _loc, _count, value = geokeys[base:base + 4]
            if key_id == GEOKEY_MODEL_TYPE:
                model_type = value
            elif key_id == GEOKEY_PROJECTED_CS_TYPE:
                proj_cs_code = value

    projection = None
    if model_type == 1:  # GTModelTypeGeoKey == Projected
        utm = _utm_zone_from_epsg(proj_cs_code) if proj_cs_code else None
        if utm is None:
            code_desc = f"EPSG:{proj_cs_code}" if proj_cs_code else "an unspecified projected CRS"
            raise GeoTiffError(
                f"this GeoTIFF uses {code_desc}, which isn't a supported UTM zone "
                "(WGS84, ETRS89 or NAD83). Reproject it to EPSG:4326 first (e.g. gdalwarp -t_srs EPSG:4326)."
            )
        zone, hemisphere, ellipsoid = utm
        projection = {"zone": zone, "hemisphere": hemisphere, "ellipsoid": ellipsoid}

    return pixel_scale_x, pixel_scale_y, origin_x, origin_y, projection


def read_geotiff(filepath, header_only=False):
    """Read a single-band GeoTIFF DEM and return a dict describing its raster + geo-transform.

    Returned dict keys: width, height, grid ((height, width) float32 numpy array,
    band 0 only; None if header_only), origin_x, origin_y (of pixel [0,0]'s top-left
    corner, in degrees or, for a UTM raster, meters), pixel_scale_x, pixel_scale_y
    (units per pixel, matching origin_x/origin_y), projection (None for geographic/
    lat-lon rasters, else {"zone", "hemisphere", "ellipsoid"} for a recognized UTM
    zone), nodata (float or None).

    With header_only=True, the (potentially slow, decompression-heavy) pixel data is
    never decoded -- only the IFD tags needed for dimensions/georeferencing are read.
    Used to preview a DEM's coverage extent without paying the cost of a full read.
    """
    with open(filepath, 'rb') as f:
        data = f.read()

    if len(data) < 8:
        raise GeoTiffError("file too small to be a TIFF")

    byte_order = data[0:2]
    if byte_order == b'II':
        endian = '<'
    elif byte_order == b'MM':
        endian = '>'
    else:
        raise GeoTiffError("not a TIFF file (bad byte-order marker)")

    (magic,) = struct.unpack_from(endian + 'H', data, 2)
    if magic == 43:
        raise GeoTiffError("BigTIFF is not supported")
    if magic != 42:
        raise GeoTiffError("not a TIFF file (bad magic number)")

    (ifd_offset,) = struct.unpack_from(endian + 'I', data, 4)
    tags, _next_ifd = _read_ifd(data, ifd_offset, endian)

    width = _first(tags, TAG_IMAGE_WIDTH)
    height = _first(tags, TAG_IMAGE_LENGTH)
    if not width or not height:
        raise GeoTiffError("missing ImageWidth/ImageLength tag")

    if header_only:
        pixel_scale_x, pixel_scale_y, origin_x, origin_y, projection = _parse_geo_reference(tags)
        return {
            "width": width,
            "height": height,
            "grid": None,
            "origin_x": origin_x,
            "origin_y": origin_y,
            "pixel_scale_x": pixel_scale_x,
            "pixel_scale_y": pixel_scale_y,
            "projection": projection,
            "nodata": None,
        }

    bits_per_sample = _first(tags, TAG_BITS_PER_SAMPLE, 32)
    compression = _first(tags, TAG_COMPRESSION, 1)
    samples_per_pixel = _first(tags, TAG_SAMPLES_PER_PIXEL, 1)
    predictor = _first(tags, TAG_PREDICTOR, 1)
    sample_format = _first(tags, TAG_SAMPLE_FORMAT, 1)

    nodata = None
    nodata_str = tags.get(TAG_GDAL_NODATA)
    if isinstance(nodata_str, str):
        try:
            nodata = float(nodata_str.strip())
        except ValueError:
            nodata = None

    dtype = _numpy_dtype(bits_per_sample, sample_format, endian)
    grid = np.zeros((height, width), dtype=np.float32)

    if TAG_TILE_OFFSETS in tags:
        tile_width = _first(tags, TAG_TILE_WIDTH)
        tile_length = _first(tags, TAG_TILE_LENGTH)
        tile_offsets = tags[TAG_TILE_OFFSETS]
        tile_byte_counts = tags[TAG_TILE_BYTE_COUNTS]
        if not tile_width or not tile_length:
            raise GeoTiffError("tiled TIFF missing TileWidth/TileLength")
        tiles_across = (width + tile_width - 1) // tile_width
        tiles_down = (height + tile_length - 1) // tile_length
        for t in range(len(tile_offsets)):
            tx = t % tiles_across
            ty = t // tiles_across
            if ty >= tiles_down:
                break
            raw = data[tile_offsets[t]:tile_offsets[t] + tile_byte_counts[t]]
            decompressed = _decompress_chunk(raw, compression)
            chunk = _decode_chunk(decompressed, tile_length, tile_width, samples_per_pixel, dtype, predictor)
            row0, col0 = ty * tile_length, tx * tile_width
            row1, col1 = min(row0 + tile_length, height), min(col0 + tile_width, width)
            grid[row0:row1, col0:col1] = chunk[:row1 - row0, :col1 - col0]
    elif TAG_STRIP_OFFSETS in tags:
        rows_per_strip = _first(tags, TAG_ROWS_PER_STRIP, height)
        strip_offsets = tags[TAG_STRIP_OFFSETS]
        strip_byte_counts = tags[TAG_STRIP_BYTE_COUNTS]
        for s in range(len(strip_offsets)):
            raw = data[strip_offsets[s]:strip_offsets[s] + strip_byte_counts[s]]
            decompressed = _decompress_chunk(raw, compression)
            num_rows = min(rows_per_strip, height - s * rows_per_strip)
            chunk = _decode_chunk(decompressed, num_rows, width, samples_per_pixel, dtype, predictor)
            row0 = s * rows_per_strip
            grid[row0:row0 + num_rows, :] = chunk
    else:
        raise GeoTiffError("no StripOffsets or TileOffsets tag — unsupported TIFF layout")

    pixel_scale_x, pixel_scale_y, origin_x, origin_y, projection = _parse_geo_reference(tags)

    return {
        "width": width,
        "height": height,
        "grid": grid,
        "origin_x": origin_x,
        "origin_y": origin_y,
        "pixel_scale_x": pixel_scale_x,
        "pixel_scale_y": pixel_scale_y,
        "projection": projection,
        "nodata": nodata,
    }


def _project_query_point(dem, lat, lon):
    """Convert a query (lat, lon) into dem's native coordinate space -- the same units
    as its origin_x/origin_y/pixel_scale_x/pixel_scale_y: degrees for a plain
    geographic raster, or UTM easting/northing (via the tile's own zone/hemisphere/
    ellipsoid) for a projected one.
    """
    projection = dem["projection"]
    if projection is not None:
        return _latlon_to_utm(lat, lon, projection["zone"], projection["hemisphere"], projection["ellipsoid"])
    return lon, lat


def dem_contains_point(dem, lat, lon):
    """True if (lat, lon) falls within dem's actual raster extent, tested in its own
    native coordinate space rather than lat/lon.

    This matters for multi-tile lookups: a UTM tile's axis-aligned lat/lon bounding
    box (as used for the cheap first-pass filter in sample_tile_index) is a slight
    OVER-approximation of its true (very slightly rotated, see get_geotiff_footprint)
    footprint -- so neighboring tiles' bboxes overlap in a thin band along their shared
    edge. A point in that band would incorrectly get accepted by whichever tile happens
    to come first in the index, then get silently edge-clamped by sample_geotiff onto
    that tile's boundary row/column -- producing a visible duplicated-edge seam exactly
    along every tile border. Testing containment in the tile's own easting/northing
    (where it really is an axis-aligned rectangle, by construction) is exact, so only
    the one tile that genuinely contains the point passes.

    Also used by get_elevation_path_localDem for the single-file case, to flag points
    that fall outside the DEM's coverage instead of silently edge-clamping them via
    sample_geotiff.
    """
    x, y = _project_query_point(dem, lat, lon)
    x0, y0 = dem["origin_x"], dem["origin_y"]
    x1 = x0 + dem["width"] * dem["pixel_scale_x"]
    y1 = y0 - dem["height"] * dem["pixel_scale_y"]
    return x0 <= x <= x1 and y1 <= y <= y0


def sample_geotiff(dem, lat, lon):
    """Nearest-neighbor sample of a DEM dict (as returned by read_geotiff) at lat/lon.

    For a UTM-projected DEM, lat/lon is first converted to that zone's easting/northing.
    Points outside the raster are clamped to the nearest edge pixel rather than
    failing -- callers that care whether a point is actually covered (a multi-tile
    folder, or flagging out-of-coverage points for a single file) should verify
    containment with dem_contains_point first (see sample_tile_index) so a boundary
    point is never silently clamped onto the wrong neighboring tile's edge instead of
    read from the tile it's actually in.
    """
    x, y = _project_query_point(dem, lat, lon)
    col = round((x - dem["origin_x"]) / dem["pixel_scale_x"])
    row = round((dem["origin_y"] - y) / dem["pixel_scale_y"])
    col = max(0, min(col, dem["width"] - 1))
    row = max(0, min(row, dem["height"] - 1))
    value = dem["grid"][row, col]
    if dem["nodata"] is not None and value == dem["nodata"]:
        return 0.0
    return float(value)


def _footprint_from_header(dem):
    """Shared by get_geotiff_footprint and build_tile_index -- see get_geotiff_footprint
    for why a UTM tile's true corners generally aren't an axis-aligned lat/lon box.
    """
    x0, y0 = dem["origin_x"], dem["origin_y"]
    x1 = x0 + dem["width"] * dem["pixel_scale_x"]
    y1 = y0 - dem["height"] * dem["pixel_scale_y"]

    projection = dem["projection"]
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]  # TL, TR, BR, BL
    if projection is not None:
        return [
            list(_utm_to_latlon(x, y, projection["zone"], projection["hemisphere"], projection["ellipsoid"]))
            for x, y in corners
        ]
    return [[y, x] for x, y in corners]  # header stores (lon, lat) as (origin_x, origin_y)


def get_geotiff_footprint(filepath):
    """Return the true lat/lon footprint of a GeoTIFF DEM as an ordered list of four
    [lat, lon] corners (top-left, top-right, bottom-right, bottom-left).

    Only reads the header (see read_geotiff's header_only), not the pixel data. For a
    plain geographic raster this is an exact axis-aligned rectangle. For a UTM raster
    it generally ISN'T axis-aligned in lat/lon: UTM's "grid north" only coincides with
    true north exactly at the zone's central meridian, so a raster square in
    easting/northing is very slightly rotated relative to true north everywhere else
    (the rotation grows with distance from the central meridian and with latitude --
    a few degrees is normal well within a zone). Returning the actual corners (rather
    than their axis-aligned bounding box) matters for drawing: two UTM tiles that share
    an edge in real easting/northing space share that edge's lat/lon corners exactly,
    so drawing their true footprints lines them up edge-to-edge, while drawing each as
    its own axis-aligned box would "square off" that shared tilted edge independently
    for each tile and leave a visible brick-like stagger between neighbors.
    """
    return _footprint_from_header(read_geotiff(filepath, header_only=True))


def get_geotiff_bounds(filepath):
    """Return the lat/lon coverage extent of a GeoTIFF DEM as {"north","south","east","west"}
    -- the axis-aligned bounding box of get_geotiff_footprint()'s four true corners. Used
    for tile lookups (sample_tile_index), where a cheap "is this point roughly in this
    tile" test is enough; see get_geotiff_footprint's own docstring for why the overlay
    drawn on a picker map uses the actual (possibly slightly rotated) corners instead.
    """
    corners = get_geotiff_footprint(filepath)
    lats = [lat for lat, _lon in corners]
    lons = [lon for _lat, lon in corners]
    return {"north": max(lats), "south": min(lats), "east": max(lons), "west": min(lons)}


def build_tile_index(folder):
    """Scan *folder* for .tif/.tiff DEM tiles and return a list of {"path", "bounds",
    "footprint", "header"} dicts, one per tile whose header could be read -- used when
    a Local DEM File path points at a directory of many single-tile downloads (e.g. a
    national survey's per-km grid) instead of one file covering the whole area.
    "bounds" is the axis-aligned box (a cheap first-pass filter); "footprint" is the
    tile's actual four corners (see get_geotiff_footprint) for drawing an overlay that
    lines up edge-to-edge with its neighbors; "header" is the full header_only
    read_geotiff() result, kept so sample_tile_index can test *exact* containment (via
    dem_contains_point) without re-reading the file or needing its pixel data.

    A tile whose header can't be parsed (corrupt download, wrong format, a stray
    non-DEM .tif, ...) is skipped with a printed warning rather than failing the
    whole folder -- a bulk multi-tile download is far more likely to have one bad
    file in it than to be entirely unusable.
    """
    paths = sorted(p for ext in ('*.tif', '*.tiff') for p in pathlib.Path(folder).glob(ext))
    index = []
    for path in paths:
        try:
            header = read_geotiff(str(path), header_only=True)
        except (GeoTiffError, OSError, struct.error, zlib.error) as e:
            print(f"[TP3D geotiff] Skipping unreadable tile {path.name}: {e}")
            continue
        footprint = _footprint_from_header(header)
        lats = [lat for lat, _lon in footprint]
        lons = [lon for _lat, lon in footprint]
        bounds = {"north": max(lats), "south": min(lats), "east": max(lons), "west": min(lons)}
        index.append({"path": str(path), "bounds": bounds, "footprint": footprint, "header": header})
    return index


def sample_tile_index(index, lat, lon, cache):
    """Sample the tile in *index* (as built by build_tile_index) covering (lat, lon).

    Each candidate is checked two ways: first the cheap "bounds" lat/lon box (a slight
    over-approximation for a UTM tile, so this only narrows the search -- see
    get_geotiff_footprint), then an exact test in that tile's own native coordinate
    space (dem_contains_point) using its already-known header, before ever reading its
    pixel data. Skipping straight to sample_geotiff on the first bounds-only match would
    let a point in the thin overlap band between two neighboring tiles' bounding boxes
    get silently edge-clamped onto the WRONG tile's boundary pixel -- exactly the
    duplicated-edge seam artifact visible along tile borders in generated terrain.

    *cache* is a dict of {path: dem_dict} the caller keeps across calls -- a tile's
    full pixel grid is only read/decompressed the first time one of its points is
    hit, then reused for every other point that falls in the same tile. Returns
    None if no tile in the index truly contains the point (a gap in the downloaded
    tiles, or a point genuinely outside the whole set).
    """
    for entry in index:
        b = entry["bounds"]
        if not (b["south"] <= lat <= b["north"] and b["west"] <= lon <= b["east"]):
            continue
        if not dem_contains_point(entry["header"], lat, lon):
            continue
        path = entry["path"]
        if path not in cache:
            cache[path] = read_geotiff(path)
        return sample_geotiff(cache[path], lat, lon)
    return None
