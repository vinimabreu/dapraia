from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest
from conftest import EXAMPLES, FakeModel

from dapraia import cli, model, notify
from dapraia.rules import load

SPOTS = ["--spot", "Piratininga:-22.9553,-43.0809", "--spot", "Itacoatiara:-22.9749,-43.032"]
TEXT = "futevôlei de manhã cedo, pouco vento, sem chuva, maré baixa. Pelo menos 2 horas."
READING = {
    "activity": {"name": "futevôlei", "quote": "futevôlei"},
    "rules": [
        {"field": "earliest_hour", "value": 6, "quote": "de manhã cedo"},
        {"field": "latest_hour", "value": 11, "quote": "de manhã cedo"},
        {"field": "wind_max_kmh", "value": 15, "quote": "pouco vento"},
        {"field": "rain_chance_max", "value": 20, "quote": "sem chuva"},
        {"field": "tide", "value": "low", "quote": "maré baixa"},
        {"field": "min_hours", "value": 2, "quote": "Pelo menos 2 horas"},
    ],
    "unmapped": [],
}


def run(argv, monkeypatch=None, fake=None):
    """Run the command; with ``fake``, the model is that fake for this call only."""
    out, err = io.StringIO(), io.StringIO()
    if fake is None:
        code = cli.main(argv, out=out, err=err)
    else:
        with monkeypatch.context() as patch:
            patch.setattr(model, "asker", lambda **kw: fake)
            code = cli.main(argv, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def planned(tmp_path, monkeypatch):
    path = tmp_path / "plan.json"
    code, out, _ = run(["plan", TEXT, *SPOTS, "--yes", "--plan", str(path)], monkeypatch, FakeModel(READING))
    assert code == 0, out
    return path


def test_plan_shows_each_rule_with_its_words_and_saves(planned, monkeypatch, tmp_path):
    plan = load(planned)
    assert plan.activity == "futevôlei" and [s.name for s in plan.spots] == ["Piratininga", "Itacoatiara"]
    code, out, _ = run(["plan", TEXT, *SPOTS, "--yes", "--plan", str(tmp_path / "p2.json")], monkeypatch,
                       FakeModel(READING))
    assert "Entendi assim:" in out and "vento até 15 km/h" in out and "← \"pouco vento\"" in out
    assert "a partir das 6h" in out and "até as 11h" in out and "pelo menos 2h seguidas" in out


def test_plan_without_yes_and_without_a_terminal_saves_nothing(monkeypatch, tmp_path):
    path = tmp_path / "plan.json"
    code, out, _ = run(["plan", TEXT, *SPOTS, "--plan", str(path)], monkeypatch, FakeModel(READING))
    assert code == 1 and "Nada salvo." in out and not path.exists()


def test_plan_needs_the_model(monkeypatch, tmp_path):
    def down(*a, **k):
        raise model.ModelUnavailable("Ollama is not reachable")
    code, _, err = run(["plan", TEXT, *SPOTS, "--plan", str(tmp_path / "p.json")], monkeypatch, down)
    assert code == 2 and "not reachable" in err


def test_pick_on_saved_answers_without_the_model(planned):
    code, out, _ = run(["--plan", str(planned), "--forecast-dir", str(EXAMPLES), "--no-model"])
    assert code == 0
    assert out.startswith("Dá praia amanhã (qua 07/10): Piratininga, das 6h às 8h.")
    assert "Maré baixa às 6h10" in out          # the tide anchors the window, so it comes first


def test_pick_with_why_shows_the_hours(planned):
    code, out, _ = run(["--plan", str(planned), "--forecast-dir", str(EXAMPLES), "--no-model", "--why"])
    assert "Piratininga · amanhã (qua 07/10) · onda e maré do ponto do modelo a 4,5 km" in out
    assert "vento km/h" in out and "h da maré" in out and "fora: maré" in out and "Open-Meteo.com" in out


def test_pick_json(planned):
    code, out, _ = run(["--plan", str(planned), "--forecast-dir", str(EXAMPLES), "--no-model", "--json",
                        "--day", "2026-10-08"])
    data = json.loads(out)
    assert data["facts"]["verdict"] == "go" and data["title"] == "Dá praia na quinta (08/10)" and not data["by_model"]


def test_pick_with_the_model_and_its_fallback(planned, monkeypatch):
    code, out, _ = run(["--plan", str(planned), "--forecast-dir", str(EXAMPLES)], monkeypatch,
                       FakeModel("Vento quase parado e maré baixa às 6h10; depois das 8h ela enche."))
    assert out.strip().endswith("Vento quase parado e maré baixa às 6h10; depois das 8h ela enche.")
    code, out, err = run(["--plan", str(planned), "--forecast-dir", str(EXAMPLES)], monkeypatch,
                         FakeModel("Vento de 30 km/h.", "Maré às 11h."))
    assert "failed the check twice" in err and "Maré baixa às 6h10, chance de chuva" in out


def test_pick_when_ollama_is_down_still_prints_the_code_line(planned):
    code, out, err = run(["--plan", str(planned), "--forecast-dir", str(EXAMPLES)])
    assert code == 0 and "Dá praia amanhã" in out and "the line below is the code's own" in err


def test_pick_without_a_plan(tmp_path):
    code, _, err = run(["--plan", str(tmp_path / "none.json"), "--no-model"])
    assert code == 2 and "dapraia plan" in err


def test_pick_sends_to_ntfy(planned, monkeypatch):
    sent = {}
    monkeypatch.setattr(notify, "send", lambda topic, title, message, **kw: sent.update(topic=topic, title=title, message=message, **kw))
    code, out, _ = run(["--plan", str(planned), "--forecast-dir", str(EXAMPLES), "--no-model", "--ntfy", "praia-do-vini-x7"])
    assert code == 0 and sent["title"] == "Dá praia amanhã (qua 07/10)" and sent["go"] is True
    assert sent["message"].startswith("Piratininga, das 6h às 8h.")


def test_went_shows_changes_and_saves_with_yes(planned, monkeypatch):
    run(["--plan", str(planned), "--forecast-dir", str(EXAMPLES), "--no-model"])
    answer = {"activity": {"name": "", "quote": ""}, "unmapped": [], "rules": [
        {"field": "wind_max_kmh", "value": 12, "quote": "ventou mais do que dizia"},
        {"field": "tide_hours", "value": 1, "quote": "às 7h30 a areia já tava mole"}]}
    fake = FakeModel(answer)
    code, out, _ = run(["fui", "ventou mais do que dizia e às 7h30 a areia já tava mole", "--day", "2026-10-07",
                        "--yes", "--plan", str(planned)], monkeypatch, fake)
    assert code == 0 and "vento até 15 km/h  →  vento até 12 km/h" in out
    assert "até 2h antes ou depois da maré (padrão)  →  até 1h antes ou depois da maré" in out
    assert "Piratininga" in fake.calls[0]["user"]          # that day's pick went along
    assert "(conta do código: 7h30 fica 1h20 depois da maré baixa das 6h10, então até 1h)" in out
    plan = load(planned)
    assert plan.get("wind_max_kmh") == 12 and plan.get("tide_hours") == 1 and plan.history


def test_went_with_nothing_to_change(planned, monkeypatch):
    answer = {"activity": {"name": "", "quote": ""}, "unmapped": [], "rules": []}
    code, out, _ = run(["went", "foi perfeito", "--plan", str(planned)], monkeypatch, FakeModel(answer))
    assert code == 0 and "Nada a mudar" in out


def test_show_prints_the_plan(planned):
    code, out, _ = run(["show", "--plan", str(planned)])
    assert code == 0 and "maré baixa" in out and "lugar: Itacoatiara" in out


@pytest.mark.parametrize("text", ["Piratininga", "Piratininga:abc", ":-22.9,-43.0", "X:-95,-43"])
def test_bad_spots(text):
    with pytest.raises(Exception):
        cli.parse_spot(text)


def test_a_spot_name_may_have_spaces_and_colons():
    spot = cli.parse_spot("Praia de São Francisco: Niterói:-22.91,-43.10")
    assert spot.name == "Praia de São Francisco: Niterói" and spot.lat == -22.91


# ---------------------------------------------------------------- model client

@pytest.mark.parametrize("host, local", [
    ("http://127.0.0.1:11434", True), ("localhost:11434", True), ("http://[::1]:11434", True),
    ("0.0.0.0", True), ("http://192.168.0.10:11434", False), ("https://ollama.example.com", False),
])
def test_loopback(host, local):
    assert model.is_loopback(host) is local


def test_a_remote_host_is_refused_before_anything_is_sent():
    with pytest.raises(model.RemoteHostRefused):
        model.chat("s", "u", host="http://10.0.0.5:11434")


class Recorder:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.bodies = reply, error, []

    def open(self, request, timeout=None):
        self.bodies.append(json.loads(request.data))
        if self.error:
            raise self.error
        return io.BytesIO(json.dumps(self.reply).encode())


def test_chat_sends_the_schema_and_no_thinking(monkeypatch):
    recorder = Recorder({"message": {"content": " ok "}})
    monkeypatch.setattr(model, "_OPENER", recorder)
    assert model.chat("s", "u", host="http://127.0.0.1:11434", schema={"type": "object"}) == "ok"
    body = recorder.bodies[0]
    assert body["format"] == {"type": "object"} and body["think"] is False and body["stream"] is False


def test_chat_errors_become_model_unavailable(monkeypatch):
    monkeypatch.setattr(model, "_OPENER", Recorder(error=urllib.error.URLError("refused")))
    with pytest.raises(model.ModelUnavailable, match="ollama serve"):
        model.chat("s", "u", host="http://127.0.0.1:11434")
    missing = urllib.error.HTTPError("u", 404, "nf", {}, io.BytesIO(b"model not found"))
    monkeypatch.setattr(model, "_OPENER", Recorder(error=missing))
    with pytest.raises(model.ModelUnavailable, match="ollama pull"):
        model.chat("s", "u", host="http://127.0.0.1:11434")


def test_the_model_client_ignores_proxy_settings(monkeypatch):
    import importlib
    monkeypatch.setenv("http_proxy", "http://proxy.example:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:3128")
    fresh = importlib.reload(model)          # build the opener again, with a proxy in the environment
    try:
        assert not any(getattr(h, "proxies", None) for h in fresh._OPENER.handlers)
    finally:
        monkeypatch.delenv("http_proxy")
        monkeypatch.delenv("HTTP_PROXY")
        importlib.reload(model)


# ---------------------------------------------------------------- ntfy

def test_ntfy_topics():
    assert notify.valid_topic("praia-do-vini_x7") and not notify.valid_topic("has space") and not notify.valid_topic("")
    with pytest.raises(notify.NotifyError):
        notify.send("bad topic", "t", "m", go=True)


class Answer(io.BytesIO):
    status = 200


def test_ntfy_posts_json_with_accents_intact():
    seen = {}

    class Opener:
        def open(self, request, timeout=None):
            seen["url"], seen["body"] = request.full_url, json.loads(request.data.decode())
            return Answer(b"{}")

    notify.send("praia-x7", "Dá praia amanhã", "Piratininga, das 6h às 8h.", go=True, opener=Opener(),
                server="https://ntfy.example/")
    assert seen["url"] == "https://ntfy.example/" and seen["body"]["title"] == "Dá praia amanhã"
    assert seen["body"]["topic"] == "praia-x7" and seen["body"]["tags"] == ["beach_umbrella"]


def test_ntfy_failure_is_reported():
    class Down:
        def open(self, request, timeout=None):
            raise OSError("no route")
    with pytest.raises(notify.NotifyError, match="could not reach ntfy"):
        notify.send("praia-x7", "t", "m", go=False, opener=Down())


def test_tide_shift_is_saved_shown_and_used(planned):
    code, out, _ = run(["tide-shift", "60", "--plan", str(planned)])
    assert code == 0 and load(planned).tide_shift_min == 60
    _, out, _ = run(["show", "--plan", str(planned)])
    assert "ajuste da maré: +60 min" in out
    _, out, _ = run(["--plan", str(planned), "--forecast-dir", str(EXAMPLES), "--no-model", "--why"])
    assert "maré baixa às 7h10" in out and "(maré baixa: modelo +60 min, do seu plano)" in out
    with pytest.raises(SystemExit):
        run(["tide-shift", "500", "--plan", str(planned)])


def test_went_uses_the_pick_of_the_day_you_went_not_the_last_one_run(planned, monkeypatch):
    for day in ("2026-10-07", "2026-10-09"):                 # the 9th runs last
        run(["--plan", str(planned), "--forecast-dir", str(EXAMPLES), "--no-model", "--day", day])
    answer = {"activity": {"name": "", "quote": ""}, "unmapped": [], "rules": [
        {"field": "tide_hours", "value": 1.5, "quote": "às 7h30 a areia já tava mole"}]}   # right way, wrong sum
    code, out, _ = run(["fui", "às 7h30 a areia já tava mole", "--day", "2026-10-07", "--plan", str(planned)],
                       monkeypatch, FakeModel(answer))
    assert "até 1h antes ou depois da maré" in out and "6h10" in out      # the 7th: low at 06:10, 1h20 before 7h30


def test_went_without_a_saved_pick_says_so(planned, monkeypatch):
    answer = {"activity": {"name": "", "quote": ""}, "unmapped": [], "rules": []}
    code, out, err = run(["fui", "foi ótimo", "--day", "2026-10-07", "--plan", str(planned)], monkeypatch, FakeModel(answer))
    assert "nenhuma escolha guardada" in err


def test_a_day_outside_the_forecast_is_an_error_not_a_no_go(planned):
    code, out, err = run(["--plan", str(planned), "--forecast-dir", str(EXAMPLES), "--no-model", "--day", "2026-10-10"])
    assert code == 5 and out == "" and "no forecast for 2026-10-10" in err and "2026-10-09" in err
    code, out, err = run(["--plan", str(planned), "--forecast-dir", str(EXAMPLES), "--no-model", "--day", "2026-10-01"])
    assert code == 5 and "already gone by" in err


def test_a_broken_plan_is_one_line(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text('{"rules": [{"field": "wind_max", "value": 10, "quote": "x"}]}')
    code, _, err = run(["--plan", str(path), "--no-model"])
    assert code == 2 and "can't be used" in err and "Traceback" not in err


def test_now_with_a_time_zone(planned):
    code, out, err = run(["--plan", str(planned), "--forecast-dir", str(EXAMPLES), "--no-model",
                          "--now", "2026-10-07T09:00:00Z", "--day", "today"])
    assert code == 0 and "hoje (qua 07/10)" in out       # 06:00 at the spot: the morning is still ahead


def _went(planned, monkeypatch, said, value, day="2026-10-07", field="tide_hours", quote=None):
    run(["--plan", str(planned), "--forecast-dir", str(EXAMPLES), "--no-model", "--day", "2026-10-07"])
    answer = {"activity": {"name": "", "quote": ""}, "unmapped": [], "rules": [
        {"field": field, "value": value, "quote": quote or said}]}
    return run(["fui", said, "--day", day, "--plan", str(planned)], monkeypatch, FakeModel(answer, answer))


def test_a_span_after_the_tide_is_a_duration_not_a_clock(planned, monkeypatch):
    code, out, _ = _went(planned, monkeypatch, "joguei de boa, mas umas 2h depois da maré baixa a areia já tava mole", 1,
                         quote="umas 2h depois da maré baixa")
    assert "até 1h30 antes ou depois da maré" in out and "então até 1h30" in out     # tighter than 2h, never 5h


def test_a_sum_against_the_proposal_keeps_the_proposal(planned, monkeypatch):
    # proposed tighter, but 10:30 is 4h20 after the 06:10 low: the sum would widen it, so the proposal stays
    code, out, _ = _went(planned, monkeypatch, "às 10h30 a areia já tava mole", 1.5)
    assert "no sentido contrário; ficou a proposta do modelo" in out and "até 1h30 antes ou depois" in out


def test_a_pick_from_saved_answers_is_never_borrowed_for_another_day(planned, monkeypatch):
    code, out, err = _went(planned, monkeypatch, "às 9h30 a areia já tava mole", 1, day="2026-10-08")
    assert "nenhuma escolha guardada pra esse dia" in err and "conta do código" not in out


def test_no_tide_sum_with_a_neighbouring_day_live_pick(planned, monkeypatch):
    import os
    run(["--plan", str(planned), "--forecast-dir", str(EXAMPLES), "--no-model", "--day", "2026-10-07"])
    folder = Path(os.environ["XDG_STATE_HOME"]) / "dapraia"
    picks = json.loads((folder / "picks-saved.json").read_text())
    picks["2026-10-07"]["source"] = "live"                 # as if the evening run had fetched it
    (folder / "picks.json").write_text(json.dumps(picks))
    (folder / "picks-saved.json").unlink()
    answer = {"activity": {"name": "", "quote": ""}, "unmapped": [], "rules": [
        {"field": "tide_hours", "value": 1, "quote": "às 9h30 a areia já tava mole"}]}
    code, out, err = run(["fui", "às 9h30 a areia já tava mole", "--day", "2026-10-08", "--plan", str(planned)],
                         monkeypatch, FakeModel(answer))
    assert "comparando com a de 2026-10-07, sem conta de maré" in err and "conta do código" not in out


def test_a_pick_two_days_old_is_not_used(planned, monkeypatch):
    code, out, err = _went(planned, monkeypatch, "às 9h30 a areia já tava mole", 1, day="2026-10-09")
    assert "nenhuma escolha guardada pra esse dia" in err


def test_a_gust_limit_under_the_wind_limit_is_dropped_from_went(planned, monkeypatch):
    code, out, _ = _went(planned, monkeypatch, "umas rajadas chatas", 10, field="gust_max_kmh")
    assert "gusts are always at least the wind" in out and "rajadas até 10" not in out


def test_went_sends_every_forecast_number_for_the_outing(planned, monkeypatch):
    run(["--plan", str(planned), "--forecast-dir", str(EXAMPLES), "--no-model", "--day", "2026-10-07"])
    answer = {"activity": {"name": "", "quote": ""}, "unmapped": [], "rules": []}
    fake = FakeModel(answer)
    run(["fui", "rajadas chatas", "--day", "2026-10-07", "--plan", str(planned)], monkeypatch, fake)
    sent = fake.calls[0]["user"]
    assert '"gust_kmh"' in sent and '"temp_c"' in sent and '"water_c"' in sent    # no rules about them, still sent


def test_why_shows_a_decimal_when_rounding_would_hide_a_fail():
    from conftest import DAY, TODAY, make_forecast, make_plan
    from dapraia import pick
    plan = make_plan({"gust_max_kmh": 10, "earliest_hour": 6, "latest_hour": 9})
    def change(h):
        h.gust_kmh = 10.4 if h.time.hour == 7 else 8
    chosen = pick.choose(plan, [make_forecast(change=change)], DAY, TODAY)
    out = io.StringIO()
    cli.why_table(plan, chosen, out)
    row = next(line for line in out.getvalue().splitlines() if line.strip().startswith("7h"))
    assert "10,4" in row and "fora: rajadas" in row


@pytest.mark.parametrize("said, quote, note", [     # the planned plan has its lows at 06:10 and 18:30 on the 7th
    ("at 9 pm the sand was soft", "at 9 pm the sand was soft", "21h fica 2h30 depois da maré baixa das 18h30"),
    ("às 8 e meia a areia já tava mole", "às 8 e meia", "8h30 fica 2h20 depois da maré baixa das 6h10"),
    ("às 4 da tarde a areia já tava mole", "às 4 da tarde", "16h fica 2h30 antes da maré baixa das 18h30"),
    ("às 8h depois do jogo a areia tava mole", "às 8h depois do jogo", "8h fica 1h50 depois da maré baixa das 6h10, então até 1h30"),
])
def test_clock_times_in_what_you_said(planned, monkeypatch, said, quote, note):
    code, out, _ = _went(planned, monkeypatch, said, 1, quote=quote)
    assert note in out, out


def test_plan_shows_a_direction_warning(monkeypatch, tmp_path):
    text = "kitesurf à tarde, evito vento acima de 30 km/h"
    reading = {"activity": {"name": "kitesurf", "quote": "kitesurf"}, "unmapped": [], "rules": [
        {"field": "earliest_hour", "value": 12, "quote": "à tarde"}, {"field": "latest_hour", "value": 18, "quote": "à tarde"},
        {"field": "wind_min_kmh", "value": 30, "quote": "vento acima de 30 km/h"}]}
    code, out, _ = run(["plan", text, *SPOTS, "--yes", "--plan", str(tmp_path / "p.json")], monkeypatch, FakeModel(reading))
    assert "Confira: \"vento acima de 30 km/h\" parece apontar pro outro lado" in out


def test_a_missing_saved_forecast_and_bad_days_are_one_line(planned, tmp_path):
    code, _, err = run(["--plan", str(planned), "--forecast-dir", str(tmp_path), "--no-model"])
    assert code == 3 and "no saved forecast for Piratininga" in err and "Traceback" not in err
    code, _, err = run(["--plan", str(planned), "--no-model", "--days", "0"])
    assert code == 2 and "from 1 to 16" in err


def test_demo_picks_never_touch_your_own_picks(planned):
    import os
    run(["--plan", str(planned), "--forecast-dir", str(EXAMPLES), "--no-model", "--day", "2026-10-07"])
    folder = Path(os.environ["XDG_STATE_HOME"]) / "dapraia"
    assert (folder / "picks-saved.json").exists() and not (folder / "picks.json").exists()


@pytest.mark.parametrize("said, quote, note", [
    ("a areia já tava mole 1h30 depois da maré", "1h30 depois da maré", "1h30 depois da maré baixa, então até 1h"),
    ("meia hora depois da maré já tava mole", "meia hora depois da maré", "30 min depois da maré baixa, então até 30 min"),
    ("1h e meia depois da maré a areia amoleceu", "1h e meia depois da maré", "1h30 depois da maré baixa, então até 1h"),
    ("a areia amoleceu bem cedo", "a areia amoleceu bem cedo", "sem conta do código: não achei um horário"),
])
def test_spans_with_minutes_and_words_and_the_note_when_there_is_no_sum(planned, monkeypatch, said, quote, note):
    code, out, _ = _went(planned, monkeypatch, said, 1, quote=quote)
    assert note in out, out


@pytest.mark.parametrize("said, quote, note", [
    ("1,5h depois da maré já tava mole", "1,5h depois da maré", "1h30 depois da maré baixa, então até 1h"),
    ("depois de 2h30 a areia ainda tava firme", "depois de 2h30 a areia", "2h30 depois da maré baixa"),
])
def test_decimal_spans_and_after_phrases(planned, monkeypatch, said, quote, note):
    code, out, _ = _went(planned, monkeypatch, said, 1, quote=quote)
    assert note in out, out


def test_tide_shift_needs_a_tide_rule(tmp_path):
    from dapraia.forecast import Spot
    from dapraia.rules import Plan, Rule, save
    path = tmp_path / "p.json"
    save(Plan(text="", spots=[Spot("x", 0, 0)], rules=[Rule("wind_max_kmh", 15, "x")]), path)
    code, _, err = run(["tide-shift", "60", "--plan", str(path)])
    assert code == 2 and "no tide rule" in err


@pytest.mark.parametrize("said, quote, note", [
    ("depois de 1h e meia a areia já tava mole", "depois de 1h e meia", "1h30 depois da maré baixa, então até 1h"),
    ("por volta das 2h depois da maré já tava mole", "por volta das 2h depois da maré",
     "pode ser um horário ou um intervalo; ficou a proposta do modelo"),                     # 2:00 or two hours
    ("around 2 hours after the low tide it was soft", "around 2 hours after the low tide", "2h depois da maré baixa, então até 1h30"),
])
def test_spans_that_name_the_tide_win_over_clocks(planned, monkeypatch, said, quote, note):
    code, out, _ = _went(planned, monkeypatch, said, 1, quote=quote)
    assert note in out, out


def test_a_demo_pick_never_counts_for_your_own_plan(planned, monkeypatch, tmp_path):
    # the demo runs with the example plan; the person's own plan has the same spots but other rules
    run(["--plan", str(EXAMPLES.parent / "plan.json"), "--forecast-dir", str(EXAMPLES), "--no-model", "--day", "2026-10-07"])
    answer = {"activity": {"name": "", "quote": ""}, "unmapped": [], "rules": []}
    code, out, err = run(["fui", "foi ótimo", "--day", "2026-10-07", "--plan", str(planned)], monkeypatch, FakeModel(answer))
    assert "nenhuma escolha guardada" in err
    code, out, err = run(["fui", "foi ótimo", "--day", "2026-10-07", "--plan", str(EXAMPLES.parent / "plan.json")],
                         monkeypatch, FakeModel(answer))
    assert "comparando com a escolha de 2026-10-07" in err       # the demo itself still works


@pytest.mark.parametrize("said, quote, note", [
    ("às 10h depois da maré baixa a areia continuava firme", "às 10h depois da maré baixa",
     "pode ser um horário ou um intervalo"),
    ("às 8 e 30 a areia já tava mole", "às 8 e 30", "8h30 fica 2h20 depois da maré baixa das 6h10"),
])
def test_seventh_review_fui_cases(planned, monkeypatch, said, quote, note):
    code, out, _ = _went(planned, monkeypatch, said, 1, quote=quote)
    assert note in out, out


def test_a_proposal_equal_to_the_plan_is_not_shown_as_a_change(planned, monkeypatch):
    code, out, _ = _went(planned, monkeypatch, "foi bom", 2, quote="foi bom")      # 2 h is the default already
    assert "Nada a mudar" in out


def test_eleven_hours_on_the_clock_and_twelve_hours_as_a_span():
    from dapraia import prefs
    assert prefs._clock("às 11 horas a areia ainda tava firme") == (11, 0)
    assert prefs._clock("por volta das 16 horas") == (16, 0)
    assert prefs._clock("around 12 hours after the low tide") is None
    assert prefs._span_to_tide("around 12 hours after the low tide") == (720, True)


def test_eleven_oclock_sum_in_fui(planned, monkeypatch):
    code, out, _ = _went(planned, monkeypatch, "às 11 horas a areia ainda tava firme", 3, quote="às 11 horas")
    assert "11h fica 4h50 depois da maré baixa das 6h10, então até 4h30" in out, out


def test_a_tide_window_without_a_tide_rule_says_no_sum(tmp_path, monkeypatch):
    from dapraia.forecast import Spot
    from dapraia.rules import Plan, Rule, save
    path = tmp_path / "p.json"
    save(Plan(text="", lang="pt", spots=[Spot("Piratininga", -22.9553, -43.0809)],
              rules=[Rule("wind_max_kmh", 15, "x")]), path)
    run(["--plan", str(path), "--forecast-dir", str(EXAMPLES), "--no-model", "--day", "2026-10-07"])
    answer = {"activity": {"name": "", "quote": ""}, "unmapped": [], "rules": [
        {"field": "tide_hours", "value": 1, "quote": "às 8h30 a areia já tava mole"}]}
    code, out, err = run(["fui", "às 8h30 a areia já tava mole", "--day", "2026-10-07", "--plan", str(path)],
                         monkeypatch, FakeModel(answer, answer))
    assert "conta do código" not in out.replace("sem conta do código", "")


@pytest.mark.parametrize("said, reading", [
    ("às 8 horas e meia", (8, 30)), ("às 5 horas da tarde", (17, 0)), ("às 10 horas da noite", (22, 0)),
    ("às 8 horas e 30", (8, 30)), ("às 12 horas da noite", (24, 0)),
])
def test_hours_written_out_keep_what_comes_after(said, reading):
    from dapraia import prefs
    assert prefs._clock(said) == reading


# ---------------------------------------------------------------- tenth review

@pytest.mark.parametrize("said, reading", [
    ("às 8hs e meia", (8, 30)), ("às 8hrs e meia", (8, 30)), ("at 8:30 in the evening", (20, 30)),
    ("às 8 e meia de noite", (20, 30)), ("às 5hs da tarde", (17, 0)), ("at 5 in the afternoon", (17, 0)),
    ("at 9 at night", (21, 0)), ("às 5 à tarde", (17, 0)), ("às 5 de tarde", (17, 0)), ("às 8 e trinta", (8, 30)),
])
def test_more_ways_of_writing_a_time(said, reading):
    from dapraia import prefs
    assert prefs._clock(said) == reading


def test_a_time_not_read_whole_gets_no_sum():
    from dapraia import prefs
    detail = prefs._clock_detail("às 8 o'clock a areia tava mole")
    assert detail is not None and detail[3] is False


def test_morning_or_evening_follows_the_outing_window():
    from datetime import date, datetime
    from dapraia import prefs
    from conftest import make_plan
    plan = make_plan({"tide": "low"})
    morning = (datetime(2026, 10, 7, 6), datetime(2026, 10, 7, 9))
    assert prefs._choose_half_of_day((8, 30, False, True), date(2026, 10, 7), plan, morning) == (8, 30)
    evening = (datetime(2026, 10, 7, 17), datetime(2026, 10, 7, 20))
    assert prefs._choose_half_of_day((8, 30, False, True), date(2026, 10, 7), plan, evening) == (20, 30)
    whole_day = make_plan({"tide": "low", "earliest_hour": 0, "latest_hour": 24})
    assert prefs._choose_half_of_day((8, 0, False, True), date(2026, 10, 7), whole_day, None) is None


def test_eight_hs_and_a_half_in_fui(planned, monkeypatch):
    code, out, _ = _went(planned, monkeypatch, "às 8hs e meia a areia já tava mole", 1, quote="às 8hs e meia")
    assert "8h30 fica 2h20 depois da maré baixa das 6h10" in out, out


def test_changing_the_tide_kind_resets_the_shift(monkeypatch, tmp_path):
    import shutil
    path = tmp_path / "plan.json"
    shutil.copy(EXAMPLES.parent / "plan.json", path)
    run(["--plan", str(path), "--forecast-dir", str(EXAMPLES), "--no-model", "--day", "2026-10-07"])
    answer = {"activity": {"name": "", "quote": ""}, "unmapped": [], "rules": [
        {"field": "tide", "value": "high", "quote": "prefiro maré cheia"}]}
    code, out, _ = run(["fui", "prefiro maré cheia", "--day", "2026-10-07", "--yes", "--plan", str(path)],
                       monkeypatch, FakeModel(answer))
    assert "fica zerado" in out
    assert load(path).tide_shift_min == 0 and load(path).get("tide") == "high"


def test_half_past_midnight():
    from dapraia import prefs
    assert prefs._clock("às 12 horas e meia da noite") == (24, 30)


# ---------------------------------------------------------------- eleventh review

@pytest.mark.parametrize("quote", [
    "at 8.30 the sand was already soft", "às 8.30 a areia já tava mole", "at 8 30 the sand was soft",
    "às 8 e dez a areia já tava mole", "às 8 e cinquenta já tava mole", "às 8 e pouco já tava mole",
    "às 9 de noitinha a areia tava mole", "às 9 à noitinha a areia tava mole", "at 9 tonight it was soft",
    "às 7 tava firme, às 8h30 já mole",
])
def test_a_time_not_read_with_certainty_gives_no_sum(quote):
    from dapraia import prefs
    detail = prefs._clock_detail(quote)
    assert detail is None or detail[3] is False, quote


def test_a_part_of_day_outside_the_quote_blocks_the_sum(planned, monkeypatch):
    said = "fui à noite, e às 9 a areia já tava mole"
    code, out, _ = _went(planned, monkeypatch, said, 1, quote="às 9 a areia já tava mole")
    assert "não consegui ler o horário inteiro" in out and "conta do código:" not in out.replace("sem conta do código:", "")


def test_the_demo_sentence_still_sums(planned, monkeypatch):
    said = "ventou bem mais do que dizia, umas rajadas chatas, e às 8h30 a areia já tava mole"
    code, out, _ = _went(planned, monkeypatch, said, 1, quote="às 8h30 a areia já tava mole")
    assert "conta do código: 8h30 fica 2h20 depois da maré baixa das 6h10" in out
