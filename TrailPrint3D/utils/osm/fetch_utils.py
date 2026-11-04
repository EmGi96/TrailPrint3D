import json
import time
from typing import NamedTuple

import requests

from ..elevation import format_api_error


class OsmFetchSettings(NamedTuple):
    """Snapshot of all bpy.context values needed by fetch_osm_data.

    Read these on the main thread before spawning worker threads so that
    workers never touch bpy.context.
    """

    disable_cache: int
    api_retries: int
    mapsize: float
    road_tiers: "dict[str, bool]"  # {tier_id: active} -- see utils.osm.roads.TIER_TAGS
    water_ponds: bool
    water_small_rivers: bool
    water_big_rivers: bool
    # Trailing default so existing call sites (tests, older callers) that
    # don't know about this option keep working unchanged.
    exclude_alleys: bool = True


def resolve_road_tiers(settings) -> "dict[str, bool]":
    """Return {tier_id: active} for every tier in TIER_TAGS.

    Reads from *settings* (worker-thread snapshot) when given, otherwise
    live from bpy.context.scene.tp3d (main thread only).
    """
    from .roads import TIER_TAGS  # deferred -- see roads.py's own deferred imports

    if settings is not None:
        return dict(settings.road_tiers)

    import bpy  # local: only the live-context branch needs it

    from ...props import get_road_active  # deferred to avoid circular import at load time

    tp3d = bpy.context.scene.tp3d
    return {tier: get_road_active(tp3d, tier) for tier in TIER_TAGS}


def allowed_road_tiers(mapsize: float) -> "set[str]":
    """Tiers not dropped for performance at the given map size (km).

    Dense, short-segment tiers (residential/service/footway/pedestrian/
    cycle_bridle/path) are dropped above STREETS_PRIMARY_THRESHOLD to avoid width-scaled
    roads fusing into solid blocks on zoomed-out maps. Sparse, long-segment
    tiers (highways/major/minor/track/raceway) survive up to ROADS_MAXSIZE.
    """
    from ... import constants as const
    from .roads import DENSE_TIERS, SPARSE_TIERS

    if mapsize > const.ROADS_MAXSIZE:
        return {"highways", "major"}
    if mapsize > const.STREETS_PRIMARY_THRESHOLD:
        return set(SPARSE_TIERS)
    return set(SPARSE_TIERS | DENSE_TIERS)


def requested_highway_tags(tier_active: "dict[str, bool]", mapsize: float) -> set:
    """Union of raw OSM highway=* tags to fetch, given tier checkboxes + mapsize."""
    from .roads import TIER_TAGS

    allowed_tiers = allowed_road_tiers(mapsize)
    requested = set()
    for tier, tags in TIER_TAGS.items():
        if tier_active.get(tier) and tier in allowed_tiers:
            requested |= tags
    return requested


def _read_with_progress(response, progress_callback, label):
    """Read a streamed response body, reporting the downloaded size about
    twice a second via *progress_callback*."""
    chunks = []
    received = 0
    last_report = 0.0
    for chunk in response.iter_content(chunk_size=65536):
        chunks.append(chunk)
        received += len(chunk)
        now = time.monotonic()
        if now - last_report >= 0.5:
            progress_callback(f"Downloading{label}… {received / 1e6:.1f} MB")
            last_report = now
    return b"".join(chunks)


def _overpass_request(
    query,
    overpass_url,
    method="POST",
    timeout=60,
    max_retries=3,
    log_callback=None,
    progress_callback=None,
    progress_label="",
):
    """Make one Overpass API request with retry/backoff.

    Parameters
    ----------
    query        : Overpass QL string
    overpass_url : endpoint URL
    method       : 'POST' (default) or 'GET'
    timeout      : per-request timeout in seconds
    max_retries  : total attempts before giving up
    log_callback : optional callable(str) for progress messages — called from
                   whatever thread this runs on, so keep it thread-safe.
    progress_callback : optional callable(str), same threading rules. When
                   given, the body is streamed and the downloaded size is
                   reported while it arrives; it also receives the retry
                   messages if no log_callback is given.
    progress_label : text appended to "Downloading" in those messages
                   (e.g. " (tile 2/4)").

    Returns the parsed JSON dict on success, or None on failure.
    """
    log_callback = log_callback or progress_callback
    last_error = None
    last_status = None
    for attempt in range(max_retries):
        try:
            stream = progress_callback is not None
            if method == "POST":
                response = requests.post(
                    overpass_url,
                    data={"data": query},
                    headers={"User-Agent": "TrailPrint3D_3.00", "Accept": "*/*"},
                    timeout=timeout,
                    stream=stream,
                )
            else:
                response = requests.get(
                    overpass_url,
                    params={"data": query},
                    headers={"User-Agent": "TrailPrint3D_3.00", "Accept": "*/*"},
                    timeout=timeout,
                    stream=stream,
                )

            if response.status_code == 200:
                try:
                    if stream:
                        return json.loads(
                            _read_with_progress(response, progress_callback, progress_label)
                        )
                    return response.json()
                except ValueError as e:
                    last_error = e
                    last_status = response.status_code
                    print(f"Attempt {attempt + 1}: Invalid JSON response")
                    # fall through to retry
            else:
                last_status = response.status_code
                next_attempt = attempt + 2
                if next_attempt <= max_retries:
                    print(
                        f"Overpass API returned HTTP {response.status_code} — retrying {next_attempt}/{max_retries}"
                    )
                    if log_callback:
                        log_callback(
                            f"Overpass API returned HTTP {response.status_code} — retrying {next_attempt}/{max_retries}"
                        )
                else:
                    print(
                        f"Overpass API returned HTTP {response.status_code} after {max_retries} attempts"
                    )
                    if log_callback:
                        log_callback(
                            format_api_error(
                                "Overpass API",
                                status_code=response.status_code,
                                extra=f"after {max_retries} attempts",
                            )
                        )
                time.sleep(5 + attempt)

        except requests.exceptions.Timeout as e:
            last_error = e
            next_attempt = attempt + 2
            print(f"Overpass API timed out (attempt {attempt + 1}/{max_retries})")
            if log_callback:
                if next_attempt <= max_retries:
                    log_callback(
                        format_api_error(
                            "Overpass API",
                            e,
                            extra=f"retrying {next_attempt}/{max_retries}",
                        )
                    )
                else:
                    log_callback(
                        format_api_error(
                            "Overpass API",
                            e,
                            extra=f"after {max_retries} attempts",
                        )
                    )
            time.sleep(5)
        except requests.RequestException as e:
            last_error = e
            print(format_api_error("Overpass API", e))
            if log_callback:
                log_callback(format_api_error("Overpass API", e))
            time.sleep(5)

    print(
        format_api_error(
            "Overpass API",
            last_error,
            status_code=last_status,
            extra=f"after {max_retries} attempts",
        )
    )
    return None
