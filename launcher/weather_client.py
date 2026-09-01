from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import requests
import pgeocode

# Cache to avoid hammering APIs / disk
CACHE_DIR = os.path.join(os.path.dirname(__file__), "data_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

USER_AGENT = "HomePanel/1.0 (local dashboard)"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/geo+json, application/json;q=0.9, */*;q=0.8",
}

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")


@dataclass
class Cached:
    data: Dict[str, Any]
    fetched_at: float


def _cache_path(key: str) -> str:
    safe = "".join(c for c in key if c.isalnum() or c in ("-", "_"))
    return os.path.join(CACHE_DIR, f"{safe}.json")


def _load_cache(key: str, ttl_seconds: int) -> Optional[Cached]:
    path = _cache_path(key)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        fetched_at = float(payload.get("fetched_at", 0))
        if (time.time() - fetched_at) > ttl_seconds:
            return None
        data = payload.get("data", {})
        if isinstance(data, dict):
            return Cached(data=data, fetched_at=fetched_at)
    except Exception:
        return None
    return None


def _save_cache(key: str, data: Dict[str, Any]) -> None:
    path = _cache_path(key)
    payload = {"fetched_at": time.time(), "data": data}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def _get_json(url: str, ttl_seconds: int, cache_key: str) -> Dict[str, Any]:
    cached = _load_cache(cache_key, ttl_seconds)
    if cached:
        return cached.data

    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        _save_cache(cache_key, data)
        return data
    raise ValueError("Unexpected JSON response (not an object)")


def _read_config() -> Dict[str, Any]:
    if not os.path.exists(CONFIG_PATH):
        # default config if missing
        return {"weather": {"zip": "67601"}}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_weather_zip() -> str:
    cfg = _read_config()
    z = str(cfg.get("weather", {}).get("zip", "67601")).strip()
    return z or "67601"


def set_weather_zip(zip_code: str) -> None:
    zip_code = (zip_code or "").strip()
    if not zip_code:
        return
    cfg = _read_config()
    cfg.setdefault("weather", {})["zip"] = zip_code
    # A new ZIP invalidates the previously-resolved lat/lon and any
    # downstream caches keyed on the old coordinates.
    cfg["location"] = {"lat": None, "lon": None}
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def resolve_zip_to_latlon(zip_code: Optional[str] = None) -> Tuple[float, float]:
    """
    Resolve a US ZIP to (lat, lon) using pgeocode (offline dataset),
    cached for a week.
    """
    zip_code = (zip_code or get_weather_zip()).strip()

    cached = _load_cache(f"zip_{zip_code}", ttl_seconds=7 * 24 * 3600)
    if cached:
        lat = float(cached.data["lat"])
        lon = float(cached.data["lon"])
        return lat, lon

    nomi = pgeocode.Nominatim("us")
    row = nomi.query_postal_code(zip_code)

    lat = getattr(row, "latitude", None)
    lon = getattr(row, "longitude", None)

    if lat is None or lon is None:
        raise ValueError(f"Could not resolve ZIP {zip_code} to lat/lon")

    lat_f = float(lat)
    lon_f = float(lon)

    _save_cache(f"zip_{zip_code}", {"lat": lat_f, "lon": lon_f})
    return lat_f, lon_f


def get_points() -> Dict[str, Any]:
    lat, lon = resolve_zip_to_latlon()
    url = f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}"
    return _get_json(url, ttl_seconds=24 * 3600, cache_key=f"points_{lat:.4f}_{lon:.4f}")


def get_forecast_hourly() -> Dict[str, Any]:
    points = get_points()
    forecast_hourly_url = points.get("properties", {}).get("forecastHourly")
    if not forecast_hourly_url:
        raise ValueError("No forecastHourly URL found in /points response")
    lat, lon = resolve_zip_to_latlon()
    return _get_json(forecast_hourly_url, ttl_seconds=5 * 60, cache_key=f"hourly_{lat:.4f}_{lon:.4f}")


def get_hourly_fetched_at() -> Optional[float]:
    """Return the epoch timestamp of when the hourly forecast was last fetched, or None."""
    try:
        lat, lon = resolve_zip_to_latlon()
        path = _cache_path(f"hourly_{lat:.4f}_{lon:.4f}")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return float(payload.get("fetched_at", 0)) or None
    except Exception:
        return None


def get_forecast() -> Dict[str, Any]:
    points = get_points()
    forecast_url = points.get("properties", {}).get("forecast")
    if not forecast_url:
        raise ValueError("No forecast URL found in /points response")
    lat, lon = resolve_zip_to_latlon()
    return _get_json(forecast_url, ttl_seconds=10 * 60, cache_key=f"forecast_{lat:.4f}_{lon:.4f}")


def get_alerts() -> Dict[str, Any]:
    lat, lon = resolve_zip_to_latlon()
    url = f"https://api.weather.gov/alerts/active?point={lat:.4f},{lon:.4f}"
    return _get_json(url, ttl_seconds=60, cache_key=f"alerts_{lat:.4f}_{lon:.4f}")
