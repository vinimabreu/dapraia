from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import DAY, TODAY, FakeModel, make_forecast, make_plan

from dapraia import line, pick


def chosen_for(plan, **kw):
    return pick.choose(plan, [make_forecast(**kw)], DAY, TODAY)


def windy_after_nine(h):
    if h.time.date() == DAY and h.time.hour >= 9:
        h.wind_kmh = 25


PLAN = {"wind_max_kmh": 15, "rain_chance_max": 20, "earliest_hour": 6, "latest_hour": 12}


def test_go_headline_and_template_in_portuguese():
    plan = make_plan(PLAN)
    out = line.compose(plan, chosen_for(plan, change=windy_after_nine))
    assert out.title == "Dá praia amanhã (qua 07/10)"
    assert out.headline == "Dá praia amanhã (qua 07/10): Praia Teste, das 6h às 9h."
    # tightest first: 5% against 20% leaves less room than 5 km/h against 15 km/h
    assert out.why == "Chance de chuva 5%, vento 5 km/h; depois das 9h, o vento passa de 15 km/h."
    assert not out.by_model


def test_go_headline_and_template_in_english():
    plan = make_plan(PLAN, lang="en")
    out = line.compose(plan, chosen_for(plan, change=windy_after_nine))
    assert out.text == ("Go tomorrow (Wed 07/10): Praia Teste, 06:00 to 09:00. "
                        "Rain chance 5%, wind 5 km/h; after 09:00, the wind goes over 15 km/h.")


def test_a_later_day_reads_with_its_weekday():
    plan = make_plan(PLAN)
    chosen = pick.choose(plan, [make_forecast()], DAY + timedelta(days=1), TODAY)
    assert line.compose(plan, chosen).title == "Dá praia na quinta (08/10)"


def test_no_go_names_the_blocker_and_the_next_window():
    def rain(h):
        if h.time.date() == DAY:
            h.rain_chance = 70
    plan = make_plan(PLAN)
    out = line.compose(plan, chosen_for(plan, change=rain))
    assert out.headline == "Não dá praia amanhã (qua 07/10)."
    assert out.why == ("A chance de chuva passa de 20% em todas as 6 horas possíveis. "
                       "Próxima janela: quinta (08/10), das 6h às 12h em Praia Teste.")


def test_no_go_without_a_next_window():
    plan = make_plan(PLAN, lang="en")
    out = line.compose(plan, chosen_for(plan, change=lambda h: setattr(h, "rain_chance", 70)))
    assert out.why.endswith("No window in the rest of the forecast.")


def test_tide_reads_as_the_sea_coming_in_after_a_low_tide_window():
    plan = make_plan({"tide": "low", "tide_hours": 1.5, "latest_hour": 12})
    out = line.compose(plan, chosen_for(plan))
    assert out.why == "Maré baixa às 6h10; depois das 8h, a maré já está enchendo."


def test_facts_hold_what_the_sentence_may_use():
    plan = make_plan(PLAN, activity="futevôlei")
    data = line.facts(plan, chosen_for(plan, change=windy_after_nine))
    assert data["verdict"] == "go" and data["activity"] == "futevôlei"
    assert data["already_said"] == {"day": "amanhã (qua 07/10)", "spot": "Praia Teste", "window": "das 6h às 9h"}
    assert "vento 5 km/h, bem abaixo do limite (limite: vento até 15 km/h)" in data["conditions"]
    assert "chance de chuva 5%, dentro do limite (limite: chance de chuva até 20%)" in data["conditions"]
    assert data["after_the_window"] == "depois das 9h, o vento passa de 15 km/h"


# ---------------------------------------------------------------- the check

def go_facts():
    plan = make_plan(PLAN)
    return line.facts(plan, chosen_for(plan, change=windy_after_nine))


def test_a_faithful_sentence_passes():
    assert line.check_why("Vento de 5 km/h e pouca chuva; depois das 9h o vento passa de 15 km/h.", go_facts()) == []


@pytest.mark.parametrize("sentence, says", [
    ("Vento de 8 km/h, bem tranquilo.", "8 km/h is not in the facts"),
    ("Depois das 10h o vento aperta.", "time 10:00"),
    ("Vai às 6h30 que o vento está fraco.", "time 6:30"),
    ("Vento fraco até sábado.", "sabado"),
    ("Vento fraco, igual a 09/10.", "date 09/10"),
    ("Não dá praia com esse vento de 5 km/h.", "says not to go"),
    ("x" * 161, "characters"),
    ("", "empty"),
    ("Vento fraco.\nMaré boa.", "more than one line"),
])
def test_the_check_catches(sentence, says):
    assert any(says in p for p in line.check_why(sentence, go_facts())), line.check_why(sentence, go_facts())


def test_the_check_on_a_no_go_day():
    plan = make_plan(PLAN)
    data = line.facts(plan, chosen_for(plan, change=lambda h: setattr(h, "rain_chance", 70)
                                       if h.time.date() == DAY else None))
    assert data["verdict"] == "no-go"
    assert line.check_why("Dá praia sim, só levar guarda-chuva.", data)
    assert line.check_why("Chuva demais a manhã toda; na quinta (08/10), das 6h às 12h, fica bom.", data) == []


def test_numbers_written_as_hours_of_the_facts_are_fine():
    assert line.check_why("Vento fraco das 6 às 9.", go_facts()) == []


# ---------------------------------------------------------------- the model

def test_the_model_sentence_is_used_when_it_passes():
    plan = make_plan(PLAN)
    fake = FakeModel("Vento fraco e pouca chuva; depois das 9h o vento passa de 15 km/h.")
    out = line.compose(plan, chosen_for(plan, change=windy_after_nine), fake)
    assert out.by_model and out.why.startswith("Vento fraco") and len(fake.calls) == 1
    assert '"verdict": "go"' in fake.calls[0]["user"]


def test_a_failed_sentence_gets_one_retry_with_the_problems_listed():
    plan = make_plan(PLAN)
    fake = FakeModel("Vento de 3 km/h.", "Vento fraco; depois das 9h ele passa de 15 km/h.")
    out = line.compose(plan, chosen_for(plan, change=windy_after_nine), fake)
    assert out.by_model and len(fake.calls) == 2
    assert "3 km/h is not in the facts" in fake.calls[1]["user"]


def test_two_failures_fall_back_to_the_code_sentence():
    plan = make_plan(PLAN)
    fake = FakeModel("Vento de 3 km/h.", "Às 11h o vento chega.")
    out = line.compose(plan, chosen_for(plan, change=windy_after_nine), fake)
    assert not out.by_model and out.model_problems and "time 11:00" in out.model_problems[0]
    assert out.why == line.template_why(plan, chosen_for(plan, change=windy_after_nine))


def test_a_single_asked_hour_reads_right():
    plan = make_plan({"rain_chance_max": 20, "earliest_hour": 17, "latest_hour": 18}, lang="en")
    out = line.compose(plan, chosen_for(plan, change=lambda h: setattr(h, "rain_chance", 60)))
    assert "in the only hour that fits" in out.why


@pytest.mark.parametrize("rules, now, says", [
    ({"weekdays": [5, 6]}, None, "Não é um dos dias que você escolheu."),
    ({"latest_hour": 9}, "2026-10-07T10:00", "O horário que você pediu já passou."),
    ({"earliest_hour": 20, "latest_hour": 23}, None, "Não tem luz do dia no horário que você pediu."),
])
def test_a_day_without_hours_says_which_reason(rules, now, says):
    from datetime import datetime
    plan = make_plan(rules, spots=(make_forecast().spot, make_forecast().spot))
    chosen = pick.choose(plan, [make_forecast()], DAY, DAY if now else TODAY,
                         datetime.fromisoformat(now) if now else None)
    assert line.compose(plan, chosen).why.startswith(says)


def test_missing_sea_data_never_becomes_a_tide_reason():
    plan = make_plan({"tide": "low", "latest_hour": 12})
    out = line.compose(plan, chosen_for(plan, marine=False))
    assert out.why.startswith("Falta previsão de maré em todas as") and "longe da baixa" not in out.why


def test_a_stretch_too_short_says_so_instead_of_blaming_a_rule():
    from datetime import datetime
    plan = make_plan({"tide": "low", "tide_hours": 1.5, "latest_hour": 9, "min_hours": 2})
    chosen = pick.choose(plan, [make_forecast()], DAY, DAY, datetime(2026, 10, 7, 6, 30))
    out = line.compose(plan, chosen)
    assert "O maior trecho bom tem 1h, e você pediu 2h seguidas" in out.why


@pytest.mark.parametrize("sentence", [
    "Melhor nem ir: vai chover forte e ventar muito.",
    "Melhor ficar em casa, o mar está perigoso.",
    "Don't go out, it will storm.",
    "It is not worth it, stay away from the beach.",
    "Vento de quinze km/h e chuva quase certa.",          # a number in words
    "Vento de 8 km/h e maré baixa.",                     # 8 is an hour in the facts, not a speed
    "Vale ir depois de amanhã.",                         # not a day in the facts
    "Em Itacoatiara está melhor.",                       # a place the facts don't name
])
def test_sentences_found_in_review_are_refused_on_a_go_day(sentence):
    assert line.check_why(sentence, go_facts(), ("Praia Teste", "Itacoatiara")), sentence


@pytest.mark.parametrize("sentence", [
    "Pode ir sem medo, o dia está ótimo para jogar.",
    "Vale a pena ir, tempo perfeito.",
    "Great day for the beach, enjoy.",
])
def test_sentences_found_in_review_are_refused_on_a_no_go_day(sentence):
    plan = make_plan(PLAN)
    data = line.facts(plan, chosen_for(plan, change=lambda h: setattr(h, "rain_chance", 70)
                                       if h.time.date() == DAY else None))
    assert line.check_why(sentence, data), sentence


def test_a_range_in_the_facts_allows_both_ends_with_the_unit():
    plan = make_plan({"wind_max_kmh": 15, "latest_hour": 12})
    def change(h):
        h.wind_kmh = 3 if h.time.hour % 2 else 4
    data = line.facts(plan, chosen_for(plan, change=change))
    assert "vento de 3 a 4 km/h" in str(data)
    assert line.check_why("Vento de 3 km/h, quase parado.", data) == []


@pytest.mark.parametrize("low, says", [
    ("2026-10-07T06:10", "no começo do horário"), ("2026-10-07T07:40", "no meio do horário"),
    ("2026-10-07T08:20", "perto do fim do horário"),
])
def test_where_the_tide_falls_is_worked_out_by_the_code(low, says):
    from datetime import datetime
    from conftest import sea_level
    when = datetime.fromisoformat(low)
    plan = make_plan({"tide": "low", "tide_hours": 3, "earliest_hour": 6, "latest_hour": 9})
    chosen = chosen_for(plan, change=lambda h: setattr(h, "sea_level_m", sea_level(h.time, when)))
    data = line.facts(plan, chosen)
    assert data["conditions"][0].endswith(says), data["conditions"]


@pytest.mark.parametrize("sentence", [
    "Vento de 3 a 4 nós, bem fraco.",                 # a unit the facts don't use
    "Vento de 4 mph.",
    "Melhor ir pra Itaipu ou Camboinhas.",            # places that aren't spots or facts
    "Maré cheia e vento fraco.",                      # the facts only have a low tide
    "Chuva zero e vento fraco.",                      # numbers in words
    "Dá pra jogar em meia hora.",
])
def test_second_review_sentences_are_refused_on_a_go_day(sentence):
    plan = make_plan({"wind_max_kmh": 15, "rain_chance_max": 20, "tide": "low", "latest_hour": 12})
    data = line.facts(plan, chosen_for(plan))
    assert line.check_why(sentence, data, ("Praia Teste",)), sentence


@pytest.mark.parametrize("sentence", ["Dá pra ir sim, só leva um casaco.", "Dá pra jogar de boa.", "You can still go."])
def test_second_review_sentences_are_refused_on_a_no_go_day(sentence):
    plan = make_plan(PLAN)
    data = line.facts(plan, chosen_for(plan, change=lambda h: setattr(h, "rain_chance", 70)
                                       if h.time.date() == DAY else None))
    assert line.check_why(sentence, data), sentence


def test_a_spot_named_in_the_facts_and_a_good_sentence_still_pass():
    plan = make_plan({"wind_max_kmh": 15, "tide": "low", "latest_hour": 12})
    data = line.facts(plan, chosen_for(plan))
    assert line.check_why("Maré baixa às 6h10 e vento fraco em Praia Teste.", data, ("Praia Teste",)) == []


def test_the_sky_is_in_the_facts_and_sunny_words_need_a_clear_one():
    plan = make_plan({"wind_max_kmh": 15, "latest_hour": 12})
    overcast = line.facts(plan, chosen_for(plan, change=lambda h: setattr(h, "cloud", 97)))
    assert overcast["sky"] == "céu encoberto (97% de nuvens)"
    assert line.check_why("O tempo está ótimo e o vento fraco.", overcast)
    assert line.check_why("Céu encoberto, mas vento fraco.", overcast) == []
    clear = line.facts(plan, chosen_for(plan, change=lambda h: setattr(h, "cloud", 10)))
    assert line.check_why("Sol e vento fraco.", clear) == []


@pytest.mark.parametrize("sentence", [
    "Low tide an hour into the window.", "Vento de sessenta km/h? não, fraquinho.", "Eu não iria hoje.",
    "I would not go, honestly.", "Não recomendo, vento forte.",
])
def test_third_review_sentences_are_refused_on_a_go_day(sentence):
    assert line.check_why(sentence, go_facts()), sentence


def test_loose_sand_is_not_sun():
    plan = make_plan({"wind_max_kmh": 15, "latest_hour": 12})
    overcast = line.facts(plan, chosen_for(plan, change=lambda h: setattr(h, "cloud", 97)))
    assert line.check_why("Vento fraco e areia solta.", overcast) == []
    assert line.check_why("Vento fraco e um solzinho.", overcast)


def test_no_sun_is_not_sunny():
    plan = make_plan({"wind_max_kmh": 15, "latest_hour": 12})
    overcast = line.facts(plan, chosen_for(plan, change=lambda h: setattr(h, "cloud", 97)))
    assert line.check_why("Dia sem sol, mas vento fraco.", overcast) == []


def test_in_the_sun_is_sunny():
    plan = make_plan({"wind_max_kmh": 15, "latest_hour": 12})
    overcast = line.facts(plan, chosen_for(plan, change=lambda h: setattr(h, "cloud", 92)))
    assert line.check_why("Dá pra jogar no sol da manhã com vento fraco.", overcast)
