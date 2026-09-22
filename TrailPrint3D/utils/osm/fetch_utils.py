import time
from typing import NamedTuple

import requests


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

    Dense, short-segment tiers (residential/service/footway/cycle_bridle/
    path) are dropped above STREETS_PRIMARY_THRESHOLD to avoid width-scaled
    roads fusing into solid blocks on zoomed-out maps. Sparse, long-segment
    tiers (highways/major/minor/track) survive up to ROADS_MAXSIZE.
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


def _overpass_request(
    query, overpass_url, method="POST", timeout=60, max_retries=3, log_callback=None
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

    Returns the parsed JSON dict on success, or None on failure.
    """
    for attempt in range(max_retries):
        try:
            if method == "POST":
                response = requests.post(
                    overpass_url,
                    data={"data": query},
                    headers={"User-Agent": "TrailPrint3D_3.00", "Accept": "*/*"},
                    timeout=timeout,
                )
            else:
                response = requests.get(
                    overpass_url,
                    params={"data": query},
                    headers={"User-Agent": "TrailPrint3D_3.00", "Accept": "*/*"},
                    timeout=timeout,
                )

            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError:
                    print(f"Attempt {attempt + 1}: Invalid JSON response")
                    # fall through to retry
            else:
                next_attempt = attempt + 2
                if next_attempt <= max_retries:
                    print(
                        f"Status ({response.status_code}), retrying... {next_attempt}/{max_retries}"
                    )
                    if log_callback:
                        log_callback(
                            f"Overpass error {response.status_code} — retrying {next_attempt}/{max_retries}"
                        )
                else:
                    print(
                        f"Status ({response.status_code}), giving up after {max_retries} attempts"
                    )
                    if log_callback:
                        log_callback(
                            f"Overpass error {response.status_code} — giving up"
                        )
                time.sleep(5 + attempt)

        except requests.exceptions.Timeout:
            next_attempt = attempt + 2
            print(f"Request timed out (attempt {attempt + 1}/{max_retries})")
            if log_callback:
                if next_attempt <= max_retries:
                    log_callback(f"Timed out — retrying {next_attempt}/{max_retries}")
                else:
                    log_callback("Timed out — giving up")
            time.sleep(5)
        except requests.RequestException as e:
            print(f"Request failed: {e}")
            time.sleep(5)

    print("Overpass request failed after retries")
    return None
