from __future__ import annotations

import io
import json
from datetime import date, datetime, timedelta

import pytest
from conftest import EXAMPLES, LOW, make_forecast, sea_level

from dapraia import forecast, tide
from dapraia.forecast import Hour, Spot

PIRATININGA = Spot("Piratininga", -22.9553, -43.0809)


def test_reads_a_real_open_meteo_answer():
    f = forecast.load_dir(PIRATININGA, EXAMPLES)
    assert len(f.hours) == 96 and f.days()[0] == date(2026, 10, 6) and len(f.days()) == 4
    assert f.timezone == "America/Sao_Paulo" and f.utc_offset == -10800
    assert f.has_marine and f.marine_km == pytest.approx(4.5, abs=0.1)
    first = f.hours[0]
    assert first.time == datetime(2026, 10, 6, 0, 0)
    assert None not in (first.wind_kmh, first.gust_kmh, first.rain_chance, first.temp_c, first.wave_m, first.sea_level_m)
    assert f.sunrise[date(2026, 10, 7)] == datetime(2026, 10, 7, 5, 26)
    assert f.sunset[date(2026, 10, 7)] == datetime(2026, 10, 7, 17, 53)


def test_without_marine_data_the_weather_still_works():
    weather = json.loads((EXAMPLES / "piratininga.weather.json").read_text())
    f = forecast.parse(PIRATININGA, weather, None)
    assert not f.has_marine and f.marine_km is None and f.hours[0].wave_m is None
    assert f.hours[0].wind_kmh is not None


def test_an_inland_point_has_no_sea():
    weather = json.loads((EXAMPLES / "piratininga.weather.json").read_text())
    marine = {"latitude": -22.9, "longitude": -43.2, "hourly": {"time": weather["hourly"]["time"],
              "wave_height": [None] * 96, "sea_level_height_msl": [None] * 96, "sea_surface_temperature": [None] * 96}}
    f = forecast.parse(PIRATININGA, weather, marine)
    assert not f.has_marine and f.marine_km is None


def test_an_error_answer_raises():
    with pytest.raises(forecast.ForecastError, match="bad latitude"):
        forecast.parse(PIRATININGA, {"error": True, "reason": "bad latitude"})


def test_urls_ask_for_what_the_rules_read():
    w, m = forecast.weather_url(PIRATININGA, 4), forecast.marine_url(PIRATININGA, 4)
    assert w.startswith(forecast.WEATHER_URL) and "wind_speed_unit=kmh" in w and "timezone=auto" in w
    assert "precipitation_probability" in w and "sunrise,sunset" in w and "latitude=-22.9553" in w
    assert m.startswith(forecast.MARINE_URL) and "sea_level_height_msl" in m and "forecast_days=4" in m


class FakeOpener:
    def __init__(self, answers):
        self.answers, self.urls = list(answers), []

    def open(self, request, timeout=None):
        self.urls.append(request.full_url)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return io.BytesIO(json.dumps(answer).encode())


def _answers():
    return (json.loads((EXAMPLES / "piratininga.weather.json").read_text()),
            json.loads((EXAMPLES / "piratininga.marine.json").read_text()))


def test_fetch_asks_twice_then_answers_from_the_cache():
    weather, marine = _answers()
    opener = FakeOpener([weather, marine])
    first = forecast.fetch(PIRATININGA, opener=opener)
    again = forecast.fetch(PIRATININGA, opener=FakeOpener([]))     # nothing left to answer: must be cached
    assert len(opener.urls) == 2 and first.has_marine and len(again.hours) == len(first.hours)


def test_fetch_without_cache_and_a_marine_outage():
    weather, _ = _answers()
    f = forecast.fetch(PIRATININGA, cache=False, opener=FakeOpener([weather, OSError("down")]))
    assert not f.has_marine and f.hours


def test_fetch_failure_names_open_meteo():
    with pytest.raises(forecast.ForecastError, match="Open-Meteo"):
        forecast.fetch(PIRATININGA, cache=False, opener=FakeOpener([OSError("no route")]))


def test_slug():
    assert forecast.slug("Icaraí") == "icarai" and forecast.slug("Praia de São Francisco") == "praia-de-sao-francisco"
    assert forecast.slug("!!") == "spot"


def test_distance():
    assert forecast.distance_km(-22.9553, -43.0809, -22.958336, -43.124985) == pytest.approx(4.5, abs=0.1)


# ---------------------------------------------------------------- tide

def test_extremes_of_a_synthetic_tide_land_within_ten_minutes():
    f = make_forecast()
    found = tide.extremes(f.hours)
    lows = [t for t in found if t.kind == "low"]
    near = min(lows, key=lambda t: abs(t.time - LOW))
    assert abs((near.time - LOW).total_seconds()) <= 10 * 60
    assert near.time.minute % 10 == 0
    kinds = [t.kind for t in found]
    assert all(a != b for a, b in zip(kinds, kinds[1:]))           # low and high alternate


def test_extremes_on_the_real_answer_alternate_about_six_hours_apart():
    f = forecast.load_dir(PIRATININGA, EXAMPLES)
    found = tide.extremes(f.hours)
    assert len(found) >= 12
    gaps = [(b.time - a.time).total_seconds() / 3600 for a, b in zip(found, found[1:])]
    assert all(a.kind != b.kind for a, b in zip(found, found[1:]))
    assert 4.5 <= min(gaps) and max(gaps) <= 8


def test_a_gap_in_the_series_is_not_a_turning_point():
    start = datetime(2026, 10, 7, 0)
    hours = [Hour(time=start + timedelta(hours=i), sea_level_m=sea_level(start + timedelta(hours=i)))
             for i in range(12) if i != 6]
    found = tide.extremes(hours)
    assert all(abs((t.time - LOW).total_seconds()) > 3600 for t in found)


def test_nearest():
    found = tide.extremes(make_forecast().hours)
    assert tide.nearest(found, LOW + timedelta(minutes=50), "low").kind == "low"
    assert tide.nearest([], LOW, "low") is None


def test_tests_cannot_reach_the_network():
    from conftest import NetworkInTest
    with pytest.raises(NetworkInTest):
        forecast.fetch(PIRATININGA, cache=False)
