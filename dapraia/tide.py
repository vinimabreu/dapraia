"""Low and high tide times, read from the modelled sea level.

Open-Meteo gives the sea level once an hour, from a model with a grid of about
8 km, and says plainly that it is not for navigation. A turning point is placed
between the hours by fitting a parabola through three readings, then rounded to
10 minutes, which is as much precision as an 8 km model deserves.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .forecast import Hour


@dataclass(frozen=True)
class Tide:
    kind: str          # "low" or "high"
    time: datetime
    level_m: float


def _round_10(moment: datetime) -> datetime:
    minutes = round((moment.minute + moment.second / 60) / 10) * 10
    return moment.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=minutes)


def extremes(hours: list[Hour]) -> list[Tide]:
    """Every low and high tide in the series, in time order."""
    series = [(h.time, h.sea_level_m) for h in hours if h.sea_level_m is not None]
    found: list[Tide] = []
    for i in range(1, len(series) - 1):
        (t0, a), (t1, b), (t2, c) = series[i - 1], series[i], series[i + 1]
        if (t1 - t0) != timedelta(hours=1) or (t2 - t1) != timedelta(hours=1):
            continue
        if b < a and b <= c:
            kind = "low"
        elif b > a and b >= c:
            kind = "high"
        else:
            continue
        curve = a - 2 * b + c
        offset = 0.5 * (a - c) / curve if curve else 0.0
        offset = max(-0.5, min(0.5, offset))
        level = b - 0.25 * (a - c) * offset
        found.append(Tide(kind, _round_10(t1 + timedelta(hours=offset)), round(level, 2)))
    return found


def shifted(found: list[Tide], minutes: int, kind: str | None = None) -> list[Tide]:
    """The same tides, moved by a fixed number of minutes.

    Against the Brazilian Navy's table for Ilha Fiscal, the model's low tides
    in Niterói came 53 to 69 minutes early on every day checked. A steady error
    like that is fixed by one number, measured once against a local table.

    The shift was measured for one kind of tide, so with ``kind`` only that kind moves:
    with +60 the lows match the table and the highs would come out 21 to 39 minutes late.
    """
    if not minutes:
        return found
    return [Tide(t.kind, t.time + timedelta(minutes=minutes), t.level_m) if kind in (None, t.kind) else t
            for t in found]


def nearest(tides: list[Tide], moment: datetime, kind: str) -> Tide | None:
    """The tide of that kind closest to ``moment``."""
    candidates = [t for t in tides if t.kind == kind]
    if not candidates:
        return None
    return min(candidates, key=lambda t: abs((t.time - moment).total_seconds()))
