"""Which hours pass the plan, and which window is the one to go out in.

Plain code. The model never decides whether you go: it only gets to explain
what this module found.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from . import tide as tides
from .forecast import Forecast, Hour, Spot
from .rules import FIELDS, Plan

# How much room counts as "comfortable" for each measure, used to rank windows
# that all pass: a window 1 km/h under the wind limit ranks below one 8 km/h under.
_SCALE = {"wind_kmh": 10, "gust_kmh": 10, "rain_chance": 20, "rain_mm": 1, "temp_c": 3,
          "uv": 2, "cloud": 25, "wave_m": 0.3, "water_c": 2}


@dataclass
class Check:
    field: str
    value: float | None
    limit: object
    ok: bool
    margin: float          # how far inside the limit, in comfortable units; negative when it fails
    tide_time: datetime | None = None


@dataclass
class Slot:
    hour: Hour
    checks: list[Check]

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]

    @property
    def margin(self) -> float:
        return min((c.margin for c in self.checks), default=3.0)


@dataclass
class Window:
    spot: Spot
    day: date
    slots: list[Slot]

    @property
    def start(self) -> datetime:
        return self.slots[0].hour.time

    @property
    def end(self) -> datetime:
        return self.slots[-1].hour.time + timedelta(hours=1)

    @property
    def hours(self) -> int:
        return len(self.slots)

    @property
    def margin(self) -> float:
        return min(s.margin for s in self.slots)


@dataclass
class DayResult:
    spot: Spot
    day: date
    slots: list[Slot]                       # the hours you asked about that day, checked
    windows: list[Window] = field(default_factory=list)   # best first
    marine_km: float | None = None
    tides: list[tides.Tide] = field(default_factory=list)
    around: list[tides.Tide] = field(default_factory=list)   # tides from 12 h before to 12 h after the day
    empty_because: str = ""     # when there are no slots: "weekday", "past", "narrow" or "dark"

    @property
    def best(self) -> Window | None:
        return self.windows[0] if self.windows else None


def _in_daylight(forecast: Forecast, hour: Hour) -> bool:
    """An hour counts as daylight when its middle is between sunrise and sunset: 17:00 to 18:00 is
    daylight with a 17:53 sunset, 18:00 to 19:00 is not."""
    day = hour.time.date()
    rise, fall = forecast.sunrise.get(day), forecast.sunset.get(day)
    if rise is None or fall is None:
        return hour.is_day is not False
    middle = hour.time + timedelta(minutes=30)
    return rise <= middle < fall


def check_hour(plan: Plan, hour: Hour, tide_list: list[tides.Tide]) -> list[Check]:
    checks: list[Check] = []
    for rule in plan.rules:
        spec = FIELDS[rule.field]
        if spec.kind in ("max", "min"):
            value = getattr(hour, spec.attr)
            limit = float(rule.value)
            if value is None:
                checks.append(Check(rule.field, None, rule.value, False, -9.0))
                continue
            room = (limit - value) if spec.kind == "max" else (value - limit)
            margin = min(3.0, room / _SCALE.get(spec.attr, 1))
            checks.append(Check(rule.field, value, rule.value, room >= 0, margin))
        elif spec.kind == "tide":
            window = float(plan.get("tide_hours", 2))
            middle = hour.time + timedelta(minutes=30)
            near = tides.nearest(tide_list, middle, rule.value)
            if near is None:
                checks.append(Check("tide", None, rule.value, False, -9.0))
                continue
            gap = abs((near.time - middle).total_seconds()) / 3600
            checks.append(Check("tide", round(gap, 2), rule.value, gap <= window,
                                min(3.0, (window - gap) / max(window, 0.5) * 3), tide_time=near.time))
    return checks


def evaluate_day(plan: Plan, forecast: Forecast, day: date, not_before: datetime | None = None) -> DayResult:
    shift = plan.tide_shift_min if plan.get("tide") else 0      # the shift belongs to the tide in a rule
    tide_list = (tides.shifted(tides.extremes(forecast.hours), shift, plan.get("tide"))
                 if forecast.has_marine else [])
    start = datetime.combine(day, datetime.min.time())
    result = DayResult(forecast.spot, day, [], marine_km=forecast.marine_km,
                       tides=[t for t in tide_list if t.time.date() == day],
                       around=[t for t in tide_list
                               if start - timedelta(hours=12) <= t.time < start + timedelta(hours=36)])
    weekdays = plan.get("weekdays")
    if weekdays is not None and day.weekday() not in weekdays:
        result.empty_because = "weekday"
        return result
    skipped_past = False
    first = float(plan.get("earliest_hour", 0))
    last = float(plan.get("latest_hour", 24))
    daylight = plan.get("daylight", True)
    for hour in forecast.hours:
        if hour.time.date() != day:
            continue
        begins = hour.time.hour + hour.time.minute / 60
        if begins < first - 1e-9 or begins + 1 > last + 1e-9:
            continue
        if daylight and not _in_daylight(forecast, hour):
            continue
        if not_before is not None and hour.time < not_before:
            skipped_past = True
            continue
        result.slots.append(Slot(hour, check_hour(plan, hour, tide_list)))
    if not result.slots:
        whole_hour_fits = math.ceil(first - 1e-9) + 1 <= last + 1e-9
        result.empty_because = ("past" if skipped_past else "dark" if whole_hour_fits else "narrow")
    result.windows = _windows(plan, forecast.spot, day, result.slots)
    return result


def _windows(plan: Plan, spot: Spot, day: date, slots: list[Slot]) -> list[Window]:
    need = float(plan.get("min_hours", 1))
    runs: list[list[Slot]] = []
    current: list[Slot] = []
    for slot in slots:
        if slot.ok and current and slot.hour.time - current[-1].hour.time == timedelta(hours=1):
            current.append(slot)
        elif slot.ok:
            if current:
                runs.append(current)
            current = [slot]
        else:
            if current:
                runs.append(current)
            current = []
    if current:
        runs.append(current)
    windows = [Window(spot, day, run) for run in runs if len(run) >= need]
    windows.sort(key=lambda w: (-w.margin, -w.hours, w.start))
    return windows


@dataclass
class Pick:
    day: date
    today: date
    go: Window | None                  # the window to go out in on that day, or None
    results: list[DayResult]           # that day, every spot
    next_window: Window | None = None  # when there is no window that day, the first one after it
    others: list[Window] = field(default_factory=list)

    @property
    def result(self) -> DayResult:
        """The day result behind the pick: the chosen spot, or the spot that came closest."""
        if self.go is not None:
            return next(r for r in self.results if r.spot == self.go.spot)
        return max(self.results, key=lambda r: (_nearest_ok(r), r.spot.name == self.results[0].spot.name))


def _nearest_ok(result: DayResult) -> float:
    """How close a day without a window came: share of the checks that passed, over its hours."""
    checks = [c for s in result.slots for c in s.checks]
    if not checks:
        return -1.0
    return sum(c.ok for c in checks) / len(checks)


def local_now(forecast: Forecast, now: datetime | None) -> datetime | None:
    """``now`` as the clock reads at the spot. A naive ``now`` is taken as already local there."""
    if now is None or now.tzinfo is None or forecast.utc_offset is None:
        return now.replace(tzinfo=None) if now is not None else None
    return now.astimezone(timezone(timedelta(seconds=forecast.utc_offset))).replace(tzinfo=None)


def choose(plan: Plan, forecasts: list[Forecast], day: date, today: date,
           now: datetime | None = None) -> Pick:
    """Best window on ``day`` across every spot; when there is none, the first good window after it.

    ``now`` keeps hours that have already started out of today. An aware ``now`` is read
    in each spot's own time zone; a naive one is taken as local at every spot.
    """
    by_day: dict[date, list[DayResult]] = {}
    for forecast in forecasts:
        here = local_now(forecast, now)
        for d in forecast.days():
            if d >= day:
                by_day.setdefault(d, []).append(evaluate_day(plan, forecast, d, here if d == today else None))
    results = by_day.get(day, [])
    windows = sorted((w for r in results for w in r.windows), key=lambda w: (-w.margin, -w.hours, w.start))
    pick = Pick(day, today, windows[0] if windows else None, results)
    pick.others = windows[1:]
    if pick.go is None:
        for later in sorted(d for d in by_day if d > day):
            found = sorted((w for r in by_day[later] for w in r.windows),
                           key=lambda w: (-w.margin, -w.hours, w.start))
            if found:
                pick.next_window = found[0]
                break
    return pick


def blocker(slot: Slot) -> Check | None:
    """The check that fails worst in a slot."""
    failed = slot.failed
    return min(failed, key=lambda c: c.margin) if failed else None


def after(result: DayResult, window: Window) -> tuple[datetime, Check | None] | None:
    """What ends the window: the first checked hour after it and the check that fails there.

    Returns (end, None) when the window runs to the end of the hours you asked
    about, and None when there is nothing after it to tell.
    """
    later = [s for s in result.slots if s.hour.time >= window.end]
    if not later:
        return (window.end, None)
    return (window.end, blocker(later[0]))


def longest_run(result: DayResult) -> int:
    """Hours in the longest stretch of passing hours, long enough or not."""
    best = current = 0
    previous = None
    for slot in result.slots:
        if slot.ok and previous is not None and slot.hour.time - previous == timedelta(hours=1) and current:
            current += 1
        elif slot.ok:
            current = 1
        else:
            current = 0
        previous = slot.hour.time
        best = max(best, current)
    return best


def main_reason(result: DayResult) -> tuple[str, int, int] | None:
    """For a day with no window: the rule that failed in the most hours, and in how many of how many."""
    counts: dict[str, int] = {}
    for slot in result.slots:
        for check in slot.failed:
            counts[check.field] = counts.get(check.field, 0) + 1
    if not counts:
        return None
    name = max(counts, key=lambda k: (counts[k], k))
    return name, counts[name], len(result.slots)
