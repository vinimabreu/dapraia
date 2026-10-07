from __future__ import annotations

from conftest import FakeModel, make_plan

from dapraia import prefs
from dapraia.rules import Rule

TEXT = "futevôlei de manhã cedo, pouco vento, sem chuva, maré baixa pra areia ficar firme. Pelo menos 2 horas."

GOOD = {
    "activity": {"name": "futevôlei", "quote": "futevôlei"},
    "rules": [
        {"field": "earliest_hour", "value": 6, "quote": "de manhã cedo"},
        {"field": "latest_hour", "value": 9, "quote": "de manhã cedo"},
        {"field": "wind_max_kmh", "value": 15, "quote": "pouco vento"},
        {"field": "rain_chance_max", "value": 20, "quote": "sem chuva"},
        {"field": "tide", "value": "low", "quote": "maré baixa"},
        {"field": "min_hours", "value": 2, "quote": "Pelo menos 2 horas"},
    ],
    "unmapped": ["pra areia ficar firme"],
}


def test_a_good_reading_is_kept_on_the_first_ask():
    fake = FakeModel(GOOD)
    reading = prefs.read_plan(TEXT, fake)
    assert reading.problems == [] and len(fake.calls) == 1
    assert reading.activity == "futevôlei" and reading.unmapped == ["pra areia ficar firme"]
    assert [(r.field, r.value) for r in reading.rules][:2] == [("earliest_hour", 6), ("latest_hour", 9)]
    assert fake.calls[0]["schema"] is prefs.SCHEMA and fake.calls[0]["user"] == TEXT


def test_an_invented_quote_triggers_a_retry_that_lists_it():
    bad = {**GOOD, "rules": GOOD["rules"][:5] + [{"field": "min_hours", "value": 3, "quote": "três horas"}]}
    fake = FakeModel(bad, GOOD)
    reading = prefs.read_plan(TEXT, fake)
    assert reading.problems == [] and len(fake.calls) == 2
    assert "\"três horas\" is not in the text" in fake.calls[1]["user"]


def test_a_wrong_number_and_an_unused_number_are_both_problems():
    bad = {**GOOD, "rules": GOOD["rules"][:5] + [{"field": "min_hours", "value": 3, "quote": "Pelo menos 2 horas"}]}
    fake = FakeModel(bad, bad)
    reading = prefs.read_plan(TEXT, fake)
    assert any("says 2" in p for p in reading.problems)
    assert any("number 2 in the text is not used" in p for p in reading.problems)
    assert "min_hours" not in [r.field for r in reading.rules]


def test_the_better_of_two_answers_is_kept():
    worse = {**GOOD, "rules": [{"field": "wind_max_kmh", "value": 15, "quote": "vento forte"}]}
    slightly_bad = {**GOOD, "rules": GOOD["rules"][:5]}     # forgets "2 horas": one problem
    reading = prefs.read_plan(TEXT, FakeModel(slightly_bad, worse))
    assert len(reading.rules) == 5 and len(reading.problems) == 1


def test_answers_that_are_not_json_or_quote_things_that_are_not_there():
    reading = prefs.read_plan(TEXT, FakeModel("I think you like mornings", "still not json"))
    assert reading.rules == [] and reading.problems == ["the answer is not JSON"]
    padded = "Here you go: " + __import__("json").dumps(GOOD) + " hope it helps"
    assert prefs.read_plan(TEXT, FakeModel(padded)).problems == []
    wrong = {**GOOD, "activity": {"name": "surf", "quote": "surf"}, "unmapped": ["com os amigos"]}
    reading = prefs.read_plan(TEXT, FakeModel(wrong, wrong))
    assert reading.activity == "" and reading.unmapped == []
    assert any("activity quote" in p for p in reading.problems)
    assert any("\"com os amigos\" is not in the text" in p for p in reading.problems)


# ---------------------------------------------------------------- after the outing

SAID = "ventou bem mais do que dizia, umas rajadas chatas, e às 7h30 a areia já tava mole"


def test_went_accepts_an_observation_number_in_the_quote():
    answer = {"activity": {"name": "", "quote": ""}, "unmapped": [], "rules": [
        {"field": "wind_max_kmh", "value": 12, "quote": "ventou bem mais do que dizia"},
        {"field": "tide_hours", "value": 1, "quote": "às 7h30 a areia já tava mole"},
    ]}
    plan = make_plan({"wind_max_kmh": 15, "tide": "low"})
    fake = FakeModel(answer)
    reading = prefs.read_went(plan, SAID, {"verdict": "go"}, fake)
    assert reading.problems == [] and [(r.field, r.value) for r in reading.rules] == [("wind_max_kmh", 12), ("tide_hours", 1)]
    assert '"what_they_said"' in fake.calls[0]["user"] and '"forecast_for_the_outing"' in fake.calls[0]["user"]


def test_went_still_refuses_invented_quotes_and_out_of_range_values():
    answer = {"activity": {"name": "", "quote": ""}, "unmapped": [], "rules": [
        {"field": "wind_max_kmh", "value": 12, "quote": "estava um vendaval"},
        {"field": "gust_max_kmh", "value": 900, "quote": "umas rajadas chatas"},
    ]}
    reading = prefs.read_went(make_plan({"wind_max_kmh": 15}), SAID, {}, FakeModel(answer, answer))
    assert reading.rules == [] and len(reading.problems) == 2


def test_apply_keeps_order_adds_new_rules_and_writes_history():
    plan = make_plan({"wind_max_kmh": 15, "tide": "low"})
    updated = prefs.apply(plan, [Rule("wind_max_kmh", 12, "ventou bem mais"), Rule("tide_hours", 1, "areia já tava mole")],
                          when="2026-10-08T09:10", said=SAID)
    assert [(r.field, r.value) for r in updated.rules] == [("wind_max_kmh", 12), ("tide", "low"), ("tide_hours", 1)]
    assert updated.history[-1]["changes"][0] == {"field": "wind_max_kmh", "from": 15, "to": 12, "quote": "ventou bem mais"}
    assert updated.history[-1]["changes"][1]["from"] is None
    assert plan.get("wind_max_kmh") == 15          # the original is untouched


def test_an_unmapped_phrase_cannot_hide_a_measurable_number():
    text = "futevôlei de manhã, vento até 12 km/h, sem chuva"
    answer = {"activity": {"name": "futevôlei", "quote": "futevôlei"}, "unmapped": ["vento até 12 km/h"], "rules": [
        {"field": "wind_max_kmh", "value": 25, "quote": "vento"},
        {"field": "rain_chance_max", "value": 20, "quote": "sem chuva"}]}
    reading = prefs.read_plan(text, FakeModel(answer, answer))
    assert "wind_max_kmh" not in [r.field for r in reading.rules]
    assert any("which a rule can measure" in p for p in reading.problems)


def test_a_word_rule_beside_a_number_across_a_comma_is_dropped():
    from dapraia.rules import check_rules
    text = "pouco vento, até 12 km/h"
    kept, problems = check_rules([{"field": "wind_max_kmh", "value": 15, "quote": "pouco vento"}], text)
    assert kept == [] and any("left out" in p for p in problems)
