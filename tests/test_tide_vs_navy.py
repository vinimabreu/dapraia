"""The README's tide claim, recomputed: Open-Meteo's modelled tides against the Navy's table.

The forecast in examples/forecast was recorded on 2026-10-06; the table is the
official 2026 prediction for Ilha Fiscal, the nearest station with one.
"""

from __future__ import annotations

import csv
from datetime import datetime

from conftest import EXAMPLES

from dapraia import forecast, tide

TABLE = EXAMPLES.parent / "tide-check" / "ilha-fiscal-2026-10.csv"


def navy():
    with TABLE.open() as handle:
        rows = [line for line in handle if not line.startswith("#")]
    for row in csv.DictReader(rows):
        yield row["kind"], datetime.fromisoformat(f"{row['date']}T{row['time']}")


def gaps(kind: str, spot: forecast.Spot) -> list[float]:
    found = tide.extremes(forecast.load_dir(spot, EXAMPLES).hours)
    minutes = []
    for k, when in navy():
        model = tide.nearest(found, when, k)
        if k == kind and model is not None and abs((model.time - when).total_seconds()) < 3 * 3600:
            minutes.append((model.time - when).total_seconds() / 60)
    return minutes


def test_the_model_low_tides_come_about_an_hour_early_every_day():
    lows = gaps("low", forecast.Spot("Piratininga", -22.9553, -43.0809))
    assert len(lows) == 8
    assert round(min(lows)) == -69 and round(max(lows)) == -53     # 53 to 69 minutes early
    highs = gaps("high", forecast.Spot("Piratininga", -22.9553, -43.0809))
    assert len(highs) == 7                                # 00:08 on the 6th is before the forecast starts
    assert round(min(highs)) == -39 and round(max(highs)) == -21   # 21 to 39 minutes early


def test_a_sixty_minute_shift_brings_the_lows_within_ten_minutes_and_leaves_the_highs_late():
    spot = forecast.Spot("Piratininga", -22.9553, -43.0809)
    found = tide.shifted(tide.extremes(forecast.load_dir(spot, EXAMPLES).hours), 60)
    for kind, when in navy():
        if kind == "low":
            assert abs((tide.nearest(found, when, "low").time - when).total_seconds()) <= 10 * 60
    highs = [(tide.nearest(found, when, "high").time - when).total_seconds() / 60
             for kind, when in navy() if kind == "high"]
    highs = [h for h in highs if abs(h) < 180]       # 00:08 on the 6th is before the forecast starts
    assert round(min(highs)) == 21 and round(max(highs)) == 39      # why the shift is for one kind of tide


def test_the_point_inside_the_bay_is_just_as_early_for_the_lows():
    lows = gaps("low", forecast.Spot("Icaraí", -22.9090, -43.1114))
    assert len(lows) == 8 and round(min(lows)) == -69 and round(max(lows)) == -53
