from __future__ import annotations

import pytest

from dapraia import rules
from dapraia.rules import Plan, Rule, check_rule, check_rules, quoted


@pytest.mark.parametrize("quote, text", [
    ("maré baixa", "e maré baixa pra areia ficar firme"),
    ("MARE BAIXA", "e maré baixa pra areia"),           # case and accents don't matter
    ("pouco vento", "futevôlei, pouco vento, sem chuva"),  # punctuation around it doesn't matter
    ("1,5 m", "ondas de até 1,5 m"),
    ("Pelo menos 2 horas", "maré baixa. Pelo menos 2 horas."),
])
def test_a_quote_is_found_word_for_word(quote, text):
    assert quoted(quote, text)


@pytest.mark.parametrize("quote, text", [
    ("vento fraco", "pouco vento"),          # same meaning, other words
    ("maré", "marés baixas"),                # part of a word is not the word
    ("", "anything"),
    ("2 horas", "pelo menos 3 horas"),
])
def test_a_quote_that_is_not_in_the_text_is_refused(quote, text):
    assert not quoted(quote, text)


TEXT = "futevôlei de manhã, vento até 12 km/h, depois das 6h, antes das 5 da tarde, sem chuva, 15 nós no máximo"


def rule(field, value, quote):
    return check_rule({"field": field, "value": value, "quote": quote}, TEXT)


def test_a_number_written_in_the_quote_must_be_the_rule_value():
    assert rule("wind_max_kmh", 12, "vento até 12 km/h")[0].value == 12
    kept, problem = rule("wind_max_kmh", 15, "vento até 12 km/h")
    assert kept is None and "says 12" in problem


def test_a_word_only_quote_lets_the_model_pick_a_number():
    kept, problem = rule("rain_chance_max", 20, "sem chuva")
    assert problem is None and kept.value == 20


def test_afternoon_hours_may_add_twelve():
    assert rule("latest_hour", 17, "antes das 5 da tarde")[0].value == 17
    assert rule("latest_hour", 5, "antes das 5 da tarde")[1]          # "da tarde" right after: only 17
    assert rule("earliest_hour", 18, "depois das 6h")[1] is not None   # no "tarde" in that quote


def test_knots_and_metres_per_second_convert_to_kmh():
    assert rule("wind_max_kmh", 28, "15 nós no máximo")[0].value == 28
    text = "vento de no máximo 5 m/s"
    assert check_rule({"field": "wind_max_kmh", "value": 18, "quote": "5 m/s"}, text)[0].value == 18


def test_clock_times_with_minutes():
    text = "começar depois das 6h30"
    assert check_rule({"field": "earliest_hour", "value": 6.5, "quote": "depois das 6h30"}, text)[0].value == 6.5
    assert check_rule({"field": "earliest_hour", "value": 7, "quote": "depois das 6h30"}, text)[0].value == 7
    assert check_rule({"field": "earliest_hour", "value": 6, "quote": "depois das 6h30"}, text)[1]   # earlier than said
    text = "terminar antes das 9h30"
    assert check_rule({"field": "latest_hour", "value": 9, "quote": "antes das 9h30"}, text)[0].value == 9
    assert check_rule({"field": "latest_hour", "value": 10, "quote": "antes das 9h30"}, text)[1]     # later than said


@pytest.mark.parametrize("item, says", [
    ({"field": "sunshine", "value": 1, "quote": "sem chuva"}, "not a field"),
    ({"field": "rain_chance_max", "value": 20, "quote": ""}, "no quote"),
    ({"field": "rain_chance_max", "value": 20, "quote": "nada de chuva"}, "not in the text"),
    ({"field": "rain_chance_max", "value": 140, "quote": "sem chuva"}, "outside"),
    ({"field": "rain_chance_max", "value": True, "quote": "sem chuva"}, "needs a number"),
    ({"field": "rain_chance_max", "value": "20", "quote": "sem chuva"}, "needs a number"),
    ({"field": "tide", "value": "medium", "quote": "sem chuva"}, "low"),
    ({"field": "weekdays", "value": [5, 9], "quote": "sem chuva"}, "0 (Monday)"),
    ({"field": "weekdays", "value": [], "quote": "sem chuva"}, "0 (Monday)"),
    ({"field": "daylight", "value": "no", "quote": "sem chuva"}, "true or false"),
])
def test_bad_rules_say_why(item, says):
    kept, problem = check_rule(item, TEXT)
    assert kept is None and says in problem


def test_weekdays_and_tide_and_daylight_values():
    text = "só fim de semana, maré cheia, à noite"
    assert check_rule({"field": "weekdays", "value": [6, 5, 5], "quote": "só fim de semana"}, text)[0].value == [5, 6]
    assert check_rule({"field": "tide", "value": "high", "quote": "maré cheia"}, text)[0].value == "high"
    assert check_rule({"field": "daylight", "value": False, "quote": "à noite"}, text)[0].value is False


def test_every_number_in_the_text_must_be_used():
    text = "vento até 12 km/h e chuva no máximo 30%"
    kept, problems = check_rules([{"field": "wind_max_kmh", "value": 12, "quote": "vento até 12 km/h"}], text)
    assert [r.field for r in kept] == ["wind_max_kmh"]
    assert problems == ["the number 30 in the text is not used by any rule"]


def test_a_number_in_an_unmapped_phrase_or_the_activity_counts_as_used():
    text = "vôlei 2x2 perto do Posto 6, sem chuva"
    _, problems = check_rules([{"field": "rain_chance_max", "value": 20, "quote": "sem chuva"}], text,
                              unmapped=["perto do Posto 6"], extra_quotes=["vôlei 2x2"])
    assert problems == []


def test_a_number_written_twice_needs_two_uses():
    text = "rajadas até 20 km/h e vento até 20 km/h"
    _, problems = check_rules([{"field": "gust_max_kmh", "value": 20, "quote": "rajadas até 20 km/h"}], text)
    assert problems == ["the number 20 in the text is not used by any rule"]


def test_contradicting_pairs_are_dropped_together():
    text = "das 10h às 8h"
    kept, problems = check_rules([{"field": "earliest_hour", "value": 10, "quote": "das 10h"},
                                  {"field": "latest_hour", "value": 8, "quote": "às 8h"}], text)
    assert kept == [] and "must be below" in problems[0]


def test_a_field_twice_keeps_the_first():
    text = "pouco vento, vento fraco"
    kept, problems = check_rules([{"field": "wind_max_kmh", "value": 15, "quote": "pouco vento"},
                                  {"field": "wind_max_kmh", "value": 12, "quote": "vento fraco"}], text)
    assert [r.value for r in kept] == [15] and "twice" in problems[0]


def test_tide_hours_needs_a_tide():
    kept, problems = check_rules([{"field": "tide_hours", "value": 1, "quote": "1 hora"}], "1 hora")
    assert kept == [] and "needs a tide rule" in problems[0]


def test_rules_that_are_not_dicts_or_not_a_list():
    assert check_rules("rules", "x")[0] == []
    kept, problems = check_rules(["wind"], "x")
    assert kept == [] and "not a rule" in problems[0]


def test_the_plan_survives_a_round_trip(tmp_path):
    plan = Plan(text="pouco vento", lang="pt", activity="futevôlei", activity_quote="futevôlei",
                spots=[rules.Spot("Piratininga", -22.9553, -43.0809)],
                rules=[Rule("wind_max_kmh", 15, "pouco vento"), Rule("tide", "low", "maré baixa")],
                unmapped=["perto de casa"])
    path = tmp_path / "plan.json"
    rules.save(plan, path)
    again = rules.load(path)
    assert again.as_dict() == plan.as_dict()
    assert "Piratininga" in path.read_text() and "futevôlei" in path.read_text()   # stored as written
    assert again.needs_marine and again.get("wind_max_kmh") == 15 and again.get("nothing", 7) == 7


def test_default_path_follows_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert rules.default_path() == tmp_path / "dapraia" / "plan.json"


@pytest.mark.parametrize("text, field, quote, wrong, right", [
    ("kitesurf com vento entre 15 e 25 nós, de tarde", "wind_min_kmh", "entre 15", 15, 28),   # unit after the 25
    ("kitesurf com vento entre 15 e 25 nós, de tarde", "wind_max_kmh", "25 nós", 25, 46),
    ("wind under 5 m/s", "wind_max_kmh", "under 5 m/s", 5, 18),
    ("waves over 3 ft", "wave_min_m", "over 3 ft", 3, 0.9),
    ("not hotter than 82F", "temp_max_c", "not hotter than 82F", 82, 28),   # 82 also fails the range
])
def test_a_unit_written_in_the_clause_allows_only_the_converted_value(text, field, quote, wrong, right):
    assert check_rule({"field": field, "value": wrong, "quote": quote}, text)[0] is None
    assert check_rule({"field": field, "value": right, "quote": quote}, text)[0] is not None


def test_a_plain_number_must_match_exactly():
    text = "vento até 12 km/h, até 28 graus"
    assert check_rule({"field": "wind_max_kmh", "value": 13, "quote": "vento até 12 km/h"}, text)[1]
    assert check_rule({"field": "temp_max_c", "value": 27, "quote": "até 28 graus"}, text)[1]


def test_a_decimal_comma_is_not_a_clause_break():
    assert rules.clause_of("1,5 m", "onda de até 1,5 m, vento fraco") == "onda de até 1,5 m"


# ---------------------------------------------------------------- holes found in review

def test_a_quote_with_two_numbers_cannot_hide_one_of_them():
    text = "vento até 12 e rajadas até 15 km/h"
    kept, problems = check_rules([{"field": "wind_max_kmh", "value": 15, "quote": "12 e rajadas até 15"}], text)
    assert "the number 12 in the text is not used by any rule" in problems
    text = "vento entre 10 e 20 km/h"
    _, problems = check_rules([{"field": "wind_max_kmh", "value": 10, "quote": "entre 10 e 20 km/h"}], text)
    assert "the number 20 in the text is not used by any rule" in problems


def test_one_quote_may_carry_a_min_and_a_max():
    text = "vento entre 10 e 20 km/h"
    kept, problems = check_rules([{"field": "wind_min_kmh", "value": 10, "quote": "entre 10 e 20 km/h"},
                                  {"field": "wind_max_kmh", "value": 20, "quote": "entre 10 e 20 km/h"}], text)
    assert problems == [] and len(kept) == 2


def test_long_quotes_are_refused():
    text = "vento até 12 km/h e rajadas até 15 km/h"
    kept, problem = check_rule({"field": "wind_max_kmh", "value": 15, "quote": text}, text)
    assert kept is None and "9 words" in problem


@pytest.mark.parametrize("field, value, quote", [
    ("wind_max_kmh", 20, "acima de 20 km/h"),
    ("wind_min_kmh", 20, "até 20 km/h"),
    ("temp_max_c", 28, "at least 28 degrees"),
    ("earliest_hour", 9, "before 9"),
    ("latest_hour", 17, "depois das 17h"),
])
def test_a_number_pointing_the_other_way_is_flagged_for_you_not_refused(field, value, quote):
    kept, problem = check_rule({"field": field, "value": value, "quote": quote}, quote)
    assert kept is not None and problem is None          # the model is never told to flip it
    assert rules.direction_warnings(quote, [kept]) == [kept]


@pytest.mark.parametrize("quote, way", [
    ("not more than 5 km/h", {"down"}), ("no mínimo 2 horas", {"up"}), ("not colder than 68F", {"up"}),
    ("entre 15 e 25 nós", set()), ("até as 9h", {"down"}), ("a partir das 6h", {"up"}),
])
def test_directions(quote, way):
    assert rules.directions(quote) == way


def test_a_quote_cannot_stitch_two_sentences():
    assert not quoted("vento chuva", "sem vento. Chuva forte")
    assert quoted("1,5 m", "onda de 1,5 m. Vento fraco")


def test_a_hand_edited_plan_fails_in_one_line(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text("{not json")
    with pytest.raises(rules.PlanError, match="not valid JSON"):
        rules.load(path)
    path.write_text('{"rules": [{"field": "wind_max", "value": 10, "quote": "x"}]}')
    with pytest.raises(rules.PlanError, match="not a field"):
        rules.load(path)
    path.write_text('{"rules": [{"field": "wind_max_kmh", "value": "dez", "quote": "x"}]}')
    with pytest.raises(rules.PlanError, match="needs a number"):
        rules.load(path)
    path.write_text('{"spots": [{"name": "x"}]}')
    with pytest.raises(rules.PlanError, match="missing or mistypes"):
        rules.load(path)


# ---------------------------------------------------------------- second review

@pytest.mark.parametrize("quote, way", [
    ("nunca acima de 15 km/h", {"down"}), ("não acima de 30", {"down"}), ("água não abaixo de 22 graus", {"up"}),
    ("20 km/h ou mais", {"up"}), ("15 nós pra cima", {"up"}), ("28 or less", {"down"}), ("never below 20", {"up"}),
])
def test_directions_with_negations_and_words_after_the_number(quote, way):
    assert rules.directions(quote) == way


def test_never_above_is_a_maximum():
    text = "futevôlei de manhã, vento nunca acima de 15 km/h, sem chuva"
    as_max = check_rule({"field": "wind_max_kmh", "value": 15, "quote": "vento nunca acima de 15 km/h"}, text)[0]
    as_min = check_rule({"field": "wind_min_kmh", "value": 15, "quote": "vento nunca acima de 15 km/h"}, text)[0]
    assert rules.direction_warnings(text, [as_max, as_min]) == [as_min]


@pytest.mark.parametrize("text, quote, way", [
    ("kitesurf à tarde, evito vento acima de 30 km/h", "vento acima de 30 km/h", "down"),
    ("never more than 15 km/h of wind", "never more than 15 km/h", "down"),
    ("vento nunca mais que 15", "nunca mais que 15", "down"),
    ("vento jamais acima de 15", "jamais acima de 15", "down"),
    ("avoid anything above 20 km/h", "anything above 20 km/h", "down"),
    ("água nunca menos que 20 graus", "nunca menos que 20 graus", "up"),
    ("vento mais do que 20 km/h", "mais do que 20 km/h", "up"),
    ("ondas acima dos 2 metros", "acima dos 2 metros", "up"),
    ("vento superior a 20 km/h", "superior a 20 km/h", "up"),
    ("vento de 20 km por hora ou mais", "20 km por hora ou mais", "up"),
])
def test_the_way_a_number_points_with_negations_and_more_phrases(text, quote, way):
    number = rules.numbers_in(quote)[0]
    ways = dict(rules.number_directions(rules.clause_of(quote, text)))
    assert ways[number] == way


def test_from_seven_to_nine_flags_nothing_for_the_end_hour():
    text = "a walk from 7 to 9 am"
    rule = check_rule({"field": "latest_hour", "value": 9, "quote": "from 7 to 9 am"}, text)[0]
    assert rule is not None and rules.direction_warnings(text, [rule]) == []


def test_mph_converts():
    text = "beach run, wind under 15 mph"
    assert check_rule({"field": "wind_max_kmh", "value": 24, "quote": "wind under 15 mph"}, text)[0]
    assert check_rule({"field": "wind_max_kmh", "value": 15, "quote": "wind under 15 mph"}, text)[0] is None


def test_the_unit_is_the_one_after_the_number_not_any_in_the_clause():
    text = "vento até 20 km/h e rajada até 10 nós"
    assert check_rule({"field": "wind_max_kmh", "value": 20, "quote": "vento até 20 km/h"}, text)[0]
    assert check_rule({"field": "gust_max_kmh", "value": 19, "quote": "rajada até 10 nós"}, text)[0]


@pytest.mark.parametrize("field, value, quote", [
    ("wind_max_kmh", 20, "chuva até 20%"), ("wave_max_m", 1.5, "1,5 km/h"), ("rain_chance_max", 8, "8 nós"),
])
def test_a_unit_of_another_kind_is_refused(field, value, quote):
    kept, problem = check_rule({"field": field, "value": value, "quote": quote}, quote)
    assert kept is None and "gives" in problem


def test_a_decomposed_accent_still_reads_as_knots():
    import unicodedata
    text = unicodedata.normalize("NFD", "vento até 12 nós")
    assert check_rule({"field": "wind_max_kmh", "value": 12, "quote": unicodedata.normalize("NFD", "até 12 nós")}, text)[0] is None
    assert check_rule({"field": "wind_max_kmh", "value": 22, "quote": "até 12 nós"}, text)[0] is not None


def test_an_unrelated_number_does_not_take_the_word_rules_down_with_it():
    text = "futevôlei com 4 amigos de manhã com pouco vento"
    kept, problems = check_rules([{"field": "earliest_hour", "value": 6, "quote": "de manhã"},
                                  {"field": "wind_max_kmh", "value": 15, "quote": "pouco vento"}], text)
    assert len(kept) == 2 and problems == ["the number 4 in the text is not used by any rule"]


@pytest.mark.parametrize("text, quote", [
    ("vento até 12 no fim de semana", "até 12"),
    ("vento até 10 nos dias de semana", "até 10"),
    ("vento 15 no máximo", "vento 15 no máximo"),
])
def test_no_and_nos_without_the_accent_are_not_knots(text, quote):
    n = rules.numbers_in(quote)[0]
    assert check_rule({"field": "wind_max_kmh", "value": n, "quote": quote}, text)[0] is not None
    assert check_rule({"field": "wind_max_kmh", "value": round(n * 1.852), "quote": quote}, text)[0] is None


def test_a_word_only_quote_cannot_replace_the_number_written_next_to_it():
    text = "futevôlei, vento até 12 km/h, sem chuva"
    kept, problems = check_rules([{"field": "wind_max_kmh", "value": 15, "quote": "vento"},
                                  {"field": "rain_chance_max", "value": 20, "quote": "sem chuva"}], text)
    assert [r.field for r in kept] == ["rain_chance_max"]
    assert any("the number 12 in the text is not used" in p for p in problems)
    assert any("wind_max_kmh was left out" in p for p in problems)


def test_a_rule_with_its_own_number_survives_a_missing_neighbour():
    text = "vento até 12 km/h e rajadas até 20 km/h"
    kept, problems = check_rules([{"field": "wind_max_kmh", "value": 12, "quote": "vento até 12 km/h"}], text)
    assert [r.field for r in kept] == ["wind_max_kmh"] and problems == ["the number 20 in the text is not used by any rule"]


def test_gusts_below_the_wind_limit_are_refused():
    text = "vento até 12 km/h, rajadas até 10 km/h"
    kept, problems = check_rules([{"field": "wind_max_kmh", "value": 12, "quote": "vento até 12 km/h"},
                                  {"field": "gust_max_kmh", "value": 10, "quote": "rajadas até 10 km/h"}], text)
    assert [r.field for r in kept] == ["wind_max_kmh"] and "gusts are always at least the wind" in problems[0]


def test_a_hand_edited_plan_with_hours_the_wrong_way_round_or_a_folder(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text('{"rules": [{"field": "earliest_hour", "value": 10, "quote": "x"},'
                    ' {"field": "latest_hour", "value": 9, "quote": "y"}]}')
    with pytest.raises(rules.PlanError, match="must be below"):
        rules.load(path)
    with pytest.raises(rules.PlanError, match="folder"):
        rules.load(tmp_path)


def test_one_and_a_half_metres_and_the_ordinal_degree_sign():
    text = "ondas até 1 metro e meio, água não abaixo de 70ºF"
    assert check_rule({"field": "wave_max_m", "value": 1.5, "quote": "até 1 metro e meio"}, text)[0]
    assert check_rule({"field": "water_min_c", "value": 21, "quote": "não abaixo de 70ºF"}, text)[0]


@pytest.mark.parametrize("field, value, quote, flagged", [
    ("wind_min_kmh", 5, "pouco vento", True), ("wind_max_kmh", 15, "pouco vento", False),
    ("wave_max_m", 2, "ondas fortes", True), ("wave_min_m", 1, "ondas fortes", False),
])
def test_soft_and_strong_words_saved_the_other_way_are_flagged(field, value, quote, flagged):
    rule = Rule(field, value, quote)
    assert (rules.direction_warnings(quote, [rule]) == [rule]) is flagged


# ---------------------------------------------------------------- fifth review

@pytest.mark.parametrize("text, quote, wrong, right", [
    ("surfe a partir das 7 e meia, pouco vento", "a partir das 7 e meia", 7, 7.5),
    ("corrida a partir das 6 e meia da manhã", "a partir das 6 e meia da manhã", 6, 6.5),
    ("a walk from half past 7", "from half past 7", 7, 7.5),
    ("a partir das 5 da tarde, sem chuva", "a partir das 5 da tarde", 5, 17),
    ("a run from 7 pm", "from 7 pm", 7, 19),
])
def test_half_hours_and_part_of_day_belong_to_the_clock(text, quote, wrong, right):
    assert check_rule({"field": "earliest_hour", "value": wrong, "quote": quote}, text)[0] is None
    assert check_rule({"field": "earliest_hour", "value": right, "quote": quote}, text)[0] is not None


def test_a_quote_that_cuts_off_the_half_leaves_the_half_hour_unused():
    text = "surfe a partir das 7 e meia, pouco vento"
    kept, problems = check_rules([{"field": "earliest_hour", "value": 7, "quote": "a partir das 7"}], text)
    assert problems and any("7 e meia" in p for p in problems)
    kept, problems = check_rules([{"field": "earliest_hour", "value": 7.5, "quote": "a partir das 7 e meia"}], text)
    assert problems == [] and kept[0].value == 7.5


def test_one_and_a_half_metres_leaves_no_unused_number():
    text = "ondas até 1 metro e meio"
    kept, problems = check_rules([{"field": "wave_max_m", "value": 1.5, "quote": "até 1 metro e meio"}], text)
    assert problems == [] and kept[0].value == 1.5


def test_a_number_followed_by_another_word_is_not_the_rule_number():
    text = "corrida a partir das 5 e meia da tarde, sem chuva, uns 40 minutos pelo menos"
    kept, problems = check_rules([{"field": "earliest_hour", "value": 17.5, "quote": "a partir das 5 e meia da tarde"},
                                  {"field": "rain_chance_max", "value": 20, "quote": "sem chuva"}], text,
                                 unmapped=[])
    assert "rain_chance_max" in [r.field for r in kept]


def test_avoid_strong_wind_saved_as_a_maximum_is_not_flagged():
    rule = Rule("wind_max_kmh", 25, "evito vento forte")
    assert rules.direction_warnings("evito vento forte", [rule]) == []


def test_a_rule_that_cuts_a_half_hour_short_is_dropped():
    text = "surfe a partir das 7 e meia, pouco vento"
    kept, problems = check_rules([{"field": "earliest_hour", "value": 7, "quote": "a partir das 7"},
                                  {"field": "wind_max_kmh", "value": 15, "quote": "pouco vento"}], text)
    assert [r.field for r in kept] == ["wind_max_kmh"]
    assert any("cuts \"7 e meia\" short" in p for p in problems)
    assert any("quote it whole" in p for p in problems)


def test_no_rain_saved_as_a_maximum_is_not_flagged():
    rule = Rule("rain_chance_max", 20, "sem chuva")
    assert rules.direction_warnings("sem chuva", [rule]) == []


def test_minutes_become_a_whole_hour_for_the_window():
    text = "corrida, uns 40 minutos pelo menos"
    assert check_rule({"field": "min_hours", "value": 1, "quote": "40 minutos pelo menos"}, text)[0] is not None
    assert check_rule({"field": "min_hours", "value": 40, "quote": "40 minutos pelo menos"}, text)[0] is None


# ---------------------------------------------------------------- sixth review

@pytest.mark.parametrize("text, quote, field, right, wrong", [
    ("a partir das 7h e meia", "a partir das 7h e meia", "earliest_hour", 7.5, 7),
    ("até as 9h e meia", "até as 9h e meia", "latest_hour", 9.5, 10),
    ("pelo menos 2h e meia", "pelo menos 2h e meia", "min_hours", 2.5, 2),
    ("after 5:30pm", "after 5:30pm", "earliest_hour", 17.5, 5),
    ("from 7:30am", "from 7:30am", "earliest_hour", 7.5, 7),
    ("a partir das 7h30min", "a partir das 7h30min", "earliest_hour", 7.5, 7),
    ("a partir das 19:30h", "a partir das 19:30h", "earliest_hour", 19.5, 19),
    ("a partir das 7 e 30", "a partir das 7 e 30", "earliest_hour", 7.5, 7),
])
def test_common_clock_spellings_keep_their_half_hour(text, quote, field, right, wrong):
    kept, problems = check_rules([{"field": field, "value": right, "quote": quote}], text)
    assert problems == [] and kept[0].value == right, problems
    assert check_rule({"field": field, "value": wrong, "quote": quote}, text)[0] is None


def test_a_number_is_not_found_inside_another():
    text = "vento até 14 km/h e depois das 4 da tarde"
    assert check_rule({"field": "earliest_hour", "value": 4, "quote": "depois das 4 da tarde"}, text)[0] is None
    assert check_rule({"field": "earliest_hour", "value": 16, "quote": "depois das 4 da tarde"}, text)[0] is not None


def test_twelve_at_night_is_midnight():
    text = "até as 12 da noite"
    assert check_rule({"field": "latest_hour", "value": 24, "quote": "até as 12 da noite"}, text)[0] is not None
    assert check_rule({"field": "latest_hour", "value": 12, "quote": "até as 12 da noite"}, text)[0] is None


# ---------------------------------------------------------------- seventh review

@pytest.mark.parametrize("text, rules_in", [
    ("kitesurf com vento entre 10 e 15 km/h", [("wind_min_kmh", 10, "entre 10 e 15 km/h"), ("wind_max_kmh", 15, "entre 10 e 15 km/h")]),
    ("temperatura entre 20 e 30 graus", [("temp_min_c", 20, "entre 20 e 30 graus"), ("temp_max_c", 30, "entre 20 e 30 graus")]),
    ("vento entre 15 e 30 km/h", [("wind_min_kmh", 15, "entre 15 e 30 km/h"), ("wind_max_kmh", 30, "entre 15 e 30 km/h")]),
])
def test_a_range_ending_in_15_30_45_is_not_a_clock(text, rules_in):
    kept, problems = check_rules([{"field": f, "value": v, "quote": q} for f, v, q in rules_in], text)
    assert problems == [] and len(kept) == 2, problems
    assert check_rules([{"field": rules_in[1][0], "value": rules_in[0][1], "quote": rules_in[1][2]}], text)[1]


@pytest.mark.parametrize("text, quote, value", [
    ("surf das 6 da manhã às 6 da tarde", "às 6 da tarde", 18),
    ("surfing from 7 am to 7 pm", "to 7 pm", 19),
    ("das 5h30 da manhã às 5h30 da tarde", "às 5h30 da tarde", 17.5),
])
def test_part_of_day_is_read_where_the_quote_is(text, quote, value):
    assert check_rule({"field": "latest_hour", "value": value, "quote": quote}, text)[0] is not None


def test_twelve_am_is_midnight():
    assert check_rule({"field": "latest_hour", "value": 24, "quote": "until 12 am"}, "until 12 am")[0] is not None


# ---------------------------------------------------------------- eighth review

def test_an_hour_and_thirty_minutes_is_one_and_a_half_hours():
    text = "futevôlei, pelo menos 1h e 30 min"
    kept, problems = check_rules([{"field": "min_hours", "value": 1.5, "quote": "pelo menos 1h e 30 min"}], text)
    assert problems == [] and kept[0].value == 1.5
    assert check_rule({"field": "min_hours", "value": 1, "quote": "pelo menos 1h e 30 min"}, text)[0] is None


@pytest.mark.parametrize("text, quote", [
    ("futevôlei a partir das 5h30min da tarde", "a partir das 5h30min da tarde"),
    ("a run from 5h30min pm", "from 5h30min pm"),
])
def test_minutes_marker_keeps_the_part_of_day(text, quote):
    assert check_rule({"field": "earliest_hour", "value": 5.5, "quote": quote}, text)[0] is None
    assert check_rule({"field": "earliest_hour", "value": 17.5, "quote": quote}, text)[0] is not None


def test_nos_without_the_accent_does_not_stop_a_half_hour():
    text = "a partir das 7 e 30 nos fins de semana"
    kept, problems = check_rules([{"field": "earliest_hour", "value": 7.5, "quote": "a partir das 7 e 30"}], text)
    assert problems == [] and kept[0].value == 7.5


def test_twelve_thirty_am_keeps_its_minutes():
    assert check_rule({"field": "earliest_hour", "value": 0.5, "quote": "from 12:30 am"}, "from 12:30 am")[0] is not None


@pytest.mark.parametrize("quote", ["às 6 da tarde", "as 6 da tarde", "às 6 da tarde."])
def test_the_quoted_occurrence_is_found_without_accents_or_end_punctuation(quote):
    text = "das 6 da manhã às 6 da tarde"
    assert check_rule({"field": "latest_hour", "value": 18, "quote": quote}, text)[0] is not None


def test_pm_with_dots_does_not_end_the_sentence():
    text = "surf until 6 p.m. with light wind"
    assert check_rule({"field": "latest_hour", "value": 18, "quote": "until 6 p.m."}, text)[0] is not None
    assert check_rule({"field": "latest_hour", "value": 6, "quote": "until 6 p.m."}, text)[0] is None
    assert not quoted("vento chuva", "sem vento. Chuva forte")     # a real full stop still ends one


@pytest.mark.parametrize("text, quote", [("a partir das 5 de tardinha", "a partir das 5 de tardinha"),
                                         ("a partir das 5 à tardinha", "a partir das 5 à tardinha")])
def test_tardinha_is_afternoon(text, quote):
    assert check_rule({"field": "earliest_hour", "value": 17, "quote": quote}, text)[0] is not None
    assert check_rule({"field": "earliest_hour", "value": 5, "quote": quote}, text)[0] is None
