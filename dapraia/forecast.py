"""Hourly forecast for a spot, from Open-Meteo: weather anywhere, waves and tide on the coast.

Open-Meteo needs no key. It sees the coordinates of your spot, because a forecast
can't be made without them, and nothing else: your own words never leave the
machine. Data by Open-Meteo.com, CC BY 4.0.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"

WEATHER_HOURLY = ("temperature_2m", "precipitation_probability", "precipitation", "wind_speed_10m",
                  "wind_gusts_10m", "cloud_cover", "uv_index", "is_day")
MARINE_HOURLY = ("wave_height", "sea_level_height_msl", "sea_surface_temperature")

CACHE_SECONDS = 30 * 60


class ForecastError(RuntimeError):
    """Open-Meteo could not be reached, or answered with an error."""


@dataclass(frozen=True)
class Spot:
    name: str
    lat: float
    lon: float

    def as_dict(self) -> dict:
        return {"name": self.name, "lat": self.lat, "lon": self.lon}


@dataclass
class Hour:
    time: datetime                 # local time at the spot, naive
    temp_c: float | None = None
    rain_chance: float | None = None
    rain_mm: float | None = None
    wind_kmh: float | None = None
    gust_kmh: float | None = None
    cloud: float | None = None
    uv: float | None = None
    is_day: bool | None = None
    wave_m: float | None = None
    sea_level_m: float | None = None
    water_c: float | None = None


@dataclass
class Forecast:
    spot: Spot
    hours: list[Hour]
    timezone: str = ""
    sunrise: dict[date, datetime] = field(default_factory=dict)
    sunset: dict[date, datetime] = field(default_factory=dict)
    marine_point: tuple[float, float] | None = None
    marine_km: float | None = None
    utc_offset: int | None = None   # seconds, as Open-Meteo reports it for the spot

    @property
    def has_marine(self) -> bool:
        return any(h.wave_m is not None or h.sea_level_m is not None for h in self.hours)

    def days(self) -> list[date]:
        return sorted({h.time.date() for h in self.hours})


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance; used to say how far the wave model's grid point is from the spot."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def weather_url(spot: Spot, days: int) -> str:
    query = {
        "latitude": f"{spot.lat:.4f}", "longitude": f"{spot.lon:.4f}",
        "hourly": ",".join(WEATHER_HOURLY), "daily": "sunrise,sunset",
        "timezone": "auto", "forecast_days": str(days), "wind_speed_unit": "kmh",
    }
    return WEATHER_URL + "?" + urllib.parse.urlencode(query, safe=",")


def marine_url(spot: Spot, days: int) -> str:
    query = {
        "latitude": f"{spot.lat:.4f}", "longitude": f"{spot.lon:.4f}",
        "hourly": ",".join(MARINE_HOURLY), "timezone": "auto", "forecast_days": str(days),
    }
    return MARINE_URL + "?" + urllib.parse.urlencode(query, safe=",")


def _num(values: list | None, i: int) -> float | None:
    if not values or i >= len(values) or values[i] is None:
        return None
    return float(values[i])


def parse(spot: Spot, weather: dict, marine: dict | None = None) -> Forecast:
    """Turn Open-Meteo's JSON (weather, and marine when the spot is on the coast) into hours."""
    if weather.get("error"):
        raise ForecastError(weather.get("reason") or "Open-Meteo returned an error")
    hourly = weather.get("hourly") or {}
    times = hourly.get("time") or []
    hours: list[Hour] = []
    for i, stamp in enumerate(times):
        is_day = _num(hourly.get("is_day"), i)
        hours.append(Hour(
            time=datetime.fromisoformat(stamp),
            temp_c=_num(hourly.get("temperature_2m"), i),
            rain_chance=_num(hourly.get("precipitation_probability"), i),
            rain_mm=_num(hourly.get("precipitation"), i),
            wind_kmh=_num(hourly.get("wind_speed_10m"), i),
            gust_kmh=_num(hourly.get("wind_gusts_10m"), i),
            cloud=_num(hourly.get("cloud_cover"), i),
            uv=_num(hourly.get("uv_index"), i),
            is_day=None if is_day is None else bool(is_day),
        ))
    daily = weather.get("daily") or {}
    sunrise = {datetime.fromisoformat(s).date(): datetime.fromisoformat(s) for s in daily.get("sunrise") or [] if s}
    sunset = {datetime.fromisoformat(s).date(): datetime.fromisoformat(s) for s in daily.get("sunset") or [] if s}
    result = Forecast(spot=spot, hours=hours, timezone=weather.get("timezone", ""), sunrise=sunrise, sunset=sunset,
                      utc_offset=weather.get("utc_offset_seconds"))

    if marine and not marine.get("error"):
        m = marine.get("hourly") or {}
        index = {stamp: i for i, stamp in enumerate(m.get("time") or [])}
        for hour, stamp in zip(hours, times):
            i = index.get(stamp)
            if i is None:
                continue
            hour.wave_m = _num(m.get("wave_height"), i)
            hour.sea_level_m = _num(m.get("sea_level_height_msl"), i)
            hour.water_c = _num(m.get("sea_surface_temperature"), i)
        if result.has_marine and "latitude" in marine and "longitude" in marine:
            point = (float(marine["latitude"]), float(marine["longitude"]))
            result.marine_point = point
            result.marine_km = round(distance_km(spot.lat, spot.lon, *point), 1)
    return result


def _default_opener():
    return urllib.request.build_opener()


def _cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "dapraia"


def _get_json(url: str, *, cache: Path | None, opener=None) -> dict:
    path = None
    if cache is not None:
        path = cache / (hashlib.sha256(url.encode()).hexdigest()[:20] + ".json")
        try:
            if time.time() - path.stat().st_mtime < CACHE_SECONDS:
                return json.loads(path.read_text())
        except (OSError, ValueError):
            pass
    request = urllib.request.Request(url, headers={"User-Agent": "dapraia/0.1 (+https://github.com/vinimabreu/dapraia)"})
    try:
        with (opener or _default_opener()).open(request, timeout=30) as response:
            payload = json.load(response)
    except Exception as error:  # urllib raises several unrelated types here
        detail = getattr(error, "read", None)
        reason = ""
        if callable(detail):
            try:
                reason = json.loads(detail().decode()).get("reason", "")
            except Exception:
                reason = ""
        raise ForecastError(f"could not get the forecast from Open-Meteo: {reason or error}") from error
    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload))
        except OSError:
            pass
    return payload


def fetch(spot: Spot, *, days: int = 4, marine: bool = True, cache: bool = True, opener=None) -> Forecast:
    """Ask Open-Meteo for the next ``days`` days at the spot. Answers are kept for 30 minutes."""
    folder = _cache_dir() if cache else None
    weather = _get_json(weather_url(spot, days), cache=folder, opener=opener)
    sea = None
    if marine:
        try:
            sea = _get_json(marine_url(spot, days), cache=folder, opener=opener)
        except ForecastError:
            sea = None     # an inland spot or a marine outage still gets the weather
    return parse(spot, weather, sea)


def load_dir(spot: Spot, folder: Path) -> Forecast:
    """Read saved Open-Meteo answers, ``<slug>.weather.json`` and ``<slug>.marine.json``, for demos and tests."""
    weather = json.loads((folder / f"{slug(spot.name)}.weather.json").read_text())
    marine_path = folder / f"{slug(spot.name)}.marine.json"
    marine = json.loads(marine_path.read_text()) if marine_path.exists() else None
    return parse(spot, weather, marine)


def slug(name: str) -> str:
    text = unicodedata.normalize("NFKD", name.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return "-".join("".join(c if c.isalnum() else " " for c in text).split()) or "spot"
