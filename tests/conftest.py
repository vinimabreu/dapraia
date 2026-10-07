from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from dapraia import forecast, model, notify
from dapraia.forecast import Forecast, Hour, Spot
from dapraia.rules import Plan, Rule

DAY = date(2026, 10, 7)            # a Wednesday; "today" in these tests is the day before
TODAY = DAY - timedelta(days=1)
SPOT = Spot("Praia Teste", -22.95, -43.08)
LOW = datetime(2026, 10, 7, 6, 10)  # a low tide in the synthetic sea
EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "forecast"


class NetworkInTest(BaseException):
    """Not an Exception, so the code's own error handling can't turn it into a clean message."""


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """No test reads your plan, writes your state, or reaches a model or the network."""
    for name in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    monkeypatch.setattr(model, "DEFAULT_HOST", "http://127.0.0.1:9")

    def no_network():
        raise NetworkInTest("a test tried to reach the network")
    monkeypatch.setattr(forecast, "_default_opener", no_network)
    monkeypatch.setattr(notify, "_default_opener", no_network)


def sea_level(moment: datetime, low: datetime = LOW) -> float:
    """A semi-diurnal tide, 12 h 25 min from low to low, half a metre either side."""
    hours = (moment - low).total_seconds() / 3600
    return round(-0.5 * math.cos(2 * math.pi * hours / 12.42), 3)


def make_forecast(spot: Spot = SPOT, *, start: date = TODAY, days: int = 3, marine: bool = True,
                  sunrise: float = 5.5, sunset: float = 18.0, change=None) -> Forecast:
    """Calm, dry, warm hours; ``change(hour)`` edits each one."""
    hours = []
    for i in range(24 * days):
        t = datetime.combine(start, datetime.min.time()) + timedelta(hours=i)
        h = Hour(time=t, temp_c=24, rain_chance=5, rain_mm=0, wind_kmh=5, gust_kmh=10, cloud=20, uv=3,
                 is_day=sunrise <= t.hour + 0.5 < sunset)
        if marine:
            h.wave_m, h.sea_level_m, h.water_c = 0.8, sea_level(t), 23
        if change:
            change(h)
        hours.append(h)
    base = [start + timedelta(days=d) for d in range(days)]
    rise = {d: datetime.combine(d, datetime.min.time()) + timedelta(hours=sunrise) for d in base}
    fall = {d: datetime.combine(d, datetime.min.time()) + timedelta(hours=sunset) for d in base}
    return Forecast(spot, hours, "America/Sao_Paulo", rise, fall,
                    marine_point=(spot.lat - 0.04, spot.lon) if marine else None,
                    marine_km=4.4 if marine else None, utc_offset=-10800)


def make_plan(rules: dict, *, lang: str = "pt", spots=(SPOT,), activity: str = "") -> Plan:
    return Plan(text="", lang=lang, activity=activity, activity_quote=activity,
                spots=list(spots), rules=[Rule(k, v, k) for k, v in rules.items()])


class FakeModel:
    """Answers in order; records what it was asked."""

    def __init__(self, *answers):
        self.answers = [a if isinstance(a, str) else json.dumps(a, ensure_ascii=False) for a in answers]
        self.calls = []

    def __call__(self, system, user, schema=None):
        self.calls.append({"system": system, "user": user, "schema": schema})
        if not self.answers:
            raise AssertionError("the model was asked more times than expected")
        return self.answers.pop(0)
