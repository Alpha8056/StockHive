from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

import weather_client

# How close before we create a "proximity" alert
DEFAULT_THRESHOLD_MILES = 50.0


def _miles_per_degree(lat: float) -> Tuple[float, float]:
    # Approx conversions at latitude
    miles_per_deg_lat = 69.0
    miles_per_deg_lon = 69.0 * math.cos(math.radians(lat))
    return miles_per_deg_lat, miles_per_deg_lon


def _point_segment_distance_miles(
    lat: float, lon: float, a_lat: float, a_lon: float, b_lat: float, b_lon: float
) -> float:
    """
    Approx distance from point to segment in miles using local equirectangular projection.
    Good enough for “storm within X miles” logic.
    """
    mpl, mplon = _miles_per_degree(lat)

    px, py = (lon * mplon), (lat * mpl)
    ax, ay = (a_lon * mplon), (a_lat * mpl)
    bx, by = (b_lon * mplon), (b_lat * mpl)

    vx, vy = (bx - ax), (by - ay)
    wx, wy = (px - ax), (py - ay)

    vv = vx * vx + vy * vy
    if vv <= 1e-12:
        # a==b
        dx, dy = (px - ax), (py - ay)
        return math.hypot(dx, dy)

    t = (wx * vx + wy * vy) / vv
    t = max(0.0, min(1.0, t))

    cx, cy = (ax + t * vx), (ay + t * vy)
    return math.hypot(px - cx, py - cy)


def _iter_rings_from_geometry(geom: Dict[str, Any]) -> Iterable[List[Tuple[float, float]]]:
    """
    Yield rings as lists of (lat, lon) from Polygon or MultiPolygon.
    GeoJSON coords are [lon, lat].
    """
    gtype = geom.get("type")
    coords = geom.get("coordinates")

    if not coords or not gtype:
        return

    if gtype == "Polygon":
        # coords: [ [ [lon,lat], ... ] , hole..., ]
        for ring in coords:
            ring_latlon = [(pt[1], pt[0]) for pt in ring if isinstance(pt, (list, tuple)) and len(pt) >= 2]
            if ring_latlon:
                yield ring_latlon

    elif gtype == "MultiPolygon":
        # coords: [ polygon1, polygon2, ... ] where polygon = rings
        for poly in coords:
            for ring in poly:
                ring_latlon = [(pt[1], pt[0]) for pt in ring if isinstance(pt, (list, tuple)) and len(pt) >= 2]
                if ring_latlon:
                    yield ring_latlon


def distance_to_geometry_miles(lat: float, lon: float, geom: Dict[str, Any]) -> Optional[float]:
    rings = list(_iter_rings_from_geometry(geom))
    if not rings:
        return None

    best = None
    for ring in rings:
        # distance to each segment in ring
        for i in range(len(ring) - 1):
            a_lat, a_lon = ring[i]
            b_lat, b_lon = ring[i + 1]
            d = _point_segment_distance_miles(lat, lon, a_lat, a_lon, b_lat, b_lon)
            if best is None or d < best:
                best = d

        # if ring isn't closed, also connect last->first
        if ring[0] != ring[-1] and len(ring) >= 2:
            a_lat, a_lon = ring[-1]
            b_lat, b_lon = ring[0]
            d = _point_segment_distance_miles(lat, lon, a_lat, a_lon, b_lat, b_lon)
            if best is None or d < best:
                best = d

    return best


def get_storm_banner(threshold_miles: float = DEFAULT_THRESHOLD_MILES) -> Optional[str]:
    """
    Checks currently-active NWS alerts and returns a banner string for the
    nearest one whose polygon comes within threshold_miles of home, or None
    if nothing is close. Computed live on each call (weather_client already
    caches the underlying /alerts/active response), so there's no need for
    a persisted alert history here.
    """
    home_lat, home_lon = weather_client.resolve_zip_to_latlon()

    data = weather_client.get_alerts()
    features = data.get("features", []) if isinstance(data, dict) else []

    best_title = None
    best_distance = None

    for f in features:
        if not isinstance(f, dict):
            continue
        props = f.get("properties") or {}
        geom = f.get("geometry") or {}

        if not isinstance(props, dict) or not isinstance(geom, dict):
            continue

        d = distance_to_geometry_miles(home_lat, home_lon, geom)
        if d is None or d > threshold_miles:
            continue

        if best_distance is None or d < best_distance:
            event = props.get("event") or "Weather Alert"
            best_distance = d
            best_title = f"{event} within {d:.1f} miles"

    return best_title


if __name__ == "__main__":
    banner = get_storm_banner()
    print(banner or "No active alerts within threshold.")
