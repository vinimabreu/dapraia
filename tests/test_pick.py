from __future__ import annotations

from datetime import datetime, timedelta

from conftest import DAY, SPOT, TODAY, make_forecast, make_plan

from dapraia import pick
from dapraia.forecast import Spot


def windy_after(hour_of_day, speed=25):
    def change(h):
        if h.time.date() == DAY and h.time.hour >= hour_of_day:
            h.wind_kmh = speed
    return change


def test_a_calm_day_is_one_window_from_sunrise_to_sunset():
    result = pick.evaluate_day(make_plan({"wind_max_kmh": 15}), make_forecast(), DAY)
    best = result.best
    # sunrise 05:30 and sunset 18:00: the 05:00 hour is half light, so it counts; 18:00 does not
    assert best.start == datetime(2026, 10, 7, 5) and best.end == datetime(2026, 10, 7, 18)
    assert len(result.windows) == 1


def test_the_window_ends_where_the_wind_picks_up_and_says_why():
    plan = make_plan({"wind_max_kmh": 15, "earliest_hour": 6, "latest_hour": 12})
    result = pick.evaluate_day(plan, make_forecast(change=windy_after(9)), DAY)
    assert (result.best.start.hour, result.best.end.hour) == (6, 9)
    end, check = pick.after(result, result.best)
    assert end.hour == 9 and check.field == "wind_max_kmh" and check.value == 25


def test_min_hours_drops_short_windows():
    def change(h):
        if h.time.date() == DAY and h.time.hour in (8, 11):
            h.rain_chance = 80
    plan = make_plan({"rain_chance_max": 20, "earliest_hour": 6, "latest_hour": 13, "min_hours": 3})
    result = pick.evaluate_day(plan, make_forecast(change=change), DAY)
    assert [(w.start.hour, w.end.hour) for w in result.windows] == []     # runs are 2, 2 and 2 hours
    plan = make_plan({"rain_chance_max": 20, "earliest_hour": 6, "latest_hour": 13, "min_hours": 2})
    result = pick.evaluate_day(plan, make_forecast(change=change), DAY)
    assert sorted((w.start.hour, w.end.hour) for w in result.windows) == [(6, 8), (9, 11), (12, 13)][:2]


def test_daylight_and_asked_hours_both_limit_the_day():
    plan = make_plan({"earliest_hour": 4, "latest_hour": 20})
    result = pick.evaluate_day(plan, make_forecast(sunrise=6.0, sunset=17.75), DAY)
    assert result.slots[0].hour.time.hour == 6 and result.slots[-1].hour.time.hour == 17
    result = pick.evaluate_day(plan, make_forecast(sunrise=6.6, sunset=17.4), DAY)
    assert result.slots[0].hour.time.hour == 7 and result.slots[-1].hour.time.hour == 16
    plan = make_plan({"earliest_hour": 19, "latest_hour": 22, "daylight": False})
    result = pick.evaluate_day(plan, make_forecast(), DAY)
    assert [s.hour.time.hour for s in result.slots] == [19, 20, 21]


def test_weekdays_outside_the_plan_have_no_hours():
    plan = make_plan({"weekdays": [5, 6]})                     # DAY is a Wednesday
    result = pick.evaluate_day(plan, make_forecast(), DAY)
    assert result.slots == [] and result.best is None


def test_low_tide_rule_keeps_the_hours_near_low_tide():
    plan = make_plan({"tide": "low", "tide_hours": 1})
    result = pick.evaluate_day(plan, make_forecast(), DAY)
    morning = [w for w in result.windows if w.start.hour < 12][0]
    assert (morning.start.hour, morning.end.hour) == (5, 7)   # low at about 06:10: 05:30 and 06:30 are within 1 h
    tides = [c.tide_time for s in morning.slots for c in s.checks if c.field == "tide"]
    assert all(abs((t - datetime(2026, 10, 7, 6, 10)).total_seconds()) <= 600 for t in tides)


def test_a_rule_without_data_fails_instead_of_passing():
    plan = make_plan({"wave_max_m": 1.5})
    result = pick.evaluate_day(plan, make_forecast(marine=False), DAY)
    assert result.best is None and all(not s.ok for s in result.slots)
    assert pick.main_reason(result)[0] == "wave_max_m"


def test_windows_rank_by_room_inside_the_limits():
    def change(h):
        if h.time.date() == DAY:
            h.wind_kmh = 14 if h.time.hour < 12 else 4
    plan = make_plan({"wind_max_kmh": 15, "earliest_hour": 6, "latest_hour": 18})
    windows = pick.evaluate_day(plan, make_forecast(change=change), DAY).windows
    assert len(windows) == 1   # one run, all inside the limit
    def split(h):
        change(h)
        if h.time.date() == DAY and h.time.hour == 12:
            h.wind_kmh = 30
    windows = pick.evaluate_day(plan, make_forecast(change=split), DAY).windows
    assert windows[0].start.hour == 13 and windows[1].start.hour == 6


def test_choose_picks_the_spot_with_more_room():
    other = Spot("Outra", -22.97, -43.03)
    calm = make_forecast(other, change=lambda h: setattr(h, "wind_kmh", 2))
    plan = make_plan({"wind_max_kmh": 15}, spots=(SPOT, other))
    chosen = pick.choose(plan, [make_forecast(), calm], DAY, TODAY)
    assert chosen.go.spot == other and chosen.others and chosen.others[0].spot == SPOT
    assert chosen.result.spot == other


def test_no_window_points_to_the_next_one():
    def change(h):
        if h.time.date() == DAY:
            h.rain_chance = 90
    plan = make_plan({"rain_chance_max": 20})
    chosen = pick.choose(plan, [make_forecast(change=change)], DAY, TODAY)
    assert chosen.go is None and chosen.next_window.day == DAY + timedelta(days=1)
    name, count, total = pick.main_reason(chosen.result)
    assert name == "rain_chance_max" and count == total == 13      # 05:00 to 18:00


def test_no_window_anywhere():
    plan = make_plan({"rain_chance_max": 20})
    chosen = pick.choose(plan, [make_forecast(change=lambda h: setattr(h, "rain_chance", 90))], DAY, TODAY)
    assert chosen.go is None and chosen.next_window is None


def test_after_when_the_window_runs_to_the_end():
    plan = make_plan({"wind_max_kmh": 15, "latest_hour": 10})
    result = pick.evaluate_day(plan, make_forecast(), DAY)
    end, check = pick.after(result, result.best)
    assert end.hour == 10 and check is None


def test_a_day_without_forecast_has_no_results():
    chosen = pick.choose(make_plan({}), [make_forecast(days=1)], DAY, TODAY)
    assert chosen.results == [] and chosen.go is None


def test_today_skips_the_hours_that_already_started():
    plan = make_plan({"wind_max_kmh": 15})
    chosen = pick.choose(plan, [make_forecast()], TODAY, TODAY, now=datetime(2026, 10, 6, 14, 20))
    assert chosen.go.start == datetime(2026, 10, 6, 15) and chosen.go.end == datetime(2026, 10, 6, 18)
    later = pick.choose(plan, [make_forecast()], DAY, TODAY, now=datetime(2026, 10, 6, 14, 20))
    assert later.go.start == datetime(2026, 10, 7, 5)        # tomorrow is untouched


def test_a_tide_shift_moves_the_tide_and_the_window():
    plan = make_plan({"tide": "low", "tide_hours": 1, "latest_hour": 12})
    plain = pick.evaluate_day(plan, make_forecast(), DAY)
    plan.tide_shift_min = 60
    moved = pick.evaluate_day(plan, make_forecast(), DAY)
    lows = lambda r: [t.time for t in r.tides if t.kind == "low"]
    assert [t + timedelta(minutes=60) for t in lows(plain)] == lows(moved)
    assert (plain.best.start.hour, plain.best.end.hour) == (5, 7)
    assert (moved.best.start.hour, moved.best.end.hour) == (6, 8)


def test_an_aware_now_is_read_in_each_spot_time_zone():
    from datetime import timezone
    plan = make_plan({"wind_max_kmh": 15})
    now = datetime(2026, 10, 6, 17, 20, tzinfo=timezone.utc)          # 14:20 at the spot (UTC-3)
    chosen = pick.choose(plan, [make_forecast()], TODAY, TODAY, now)
    assert chosen.go.start == datetime(2026, 10, 6, 15)


def test_hours_that_hold_no_whole_clock_hour_say_narrow_not_dark():
    plan = make_plan({"earliest_hour": 6.5, "latest_hour": 7.5})
    result = pick.evaluate_day(plan, make_forecast(), DAY)
    assert result.slots == [] and result.empty_because == "narrow"


def test_the_shift_moves_only_the_rule_tide():
    plan = make_plan({"tide": "low"})
    plain = pick.evaluate_day(plan, make_forecast(), DAY)
    plan.tide_shift_min = 60
    moved = pick.evaluate_day(plan, make_forecast(), DAY)
    highs = lambda r: [t.time for t in r.tides if t.kind == "high"]
    assert highs(plain) == highs(moved)
