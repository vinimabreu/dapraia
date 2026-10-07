"""Command line.

    dapraia plan "futevôlei de manhã, pouco vento, maré baixa" --spot "Piratininga:-22.9553,-43.0809"
    dapraia                  # tomorrow's window, one line
    dapraia --why            # the hours behind it
    dapraia went "ventou mais do que dizia"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import __version__, forecast, line, model, notify, prefs, words
from . import pick as picking
from .rules import FIELDS, Plan, PlanError, Rule, check_pairs, default_path, direction_warnings, load, save

ATTRIBUTION = {
    "pt": "Previsão: Open-Meteo.com (CC BY 4.0). Onda e maré vêm de um modelo com grade de ~8 km: "
          "servem pra planejar, não pra navegar.",
    "en": "Forecast: Open-Meteo.com (CC BY 4.0). Waves and tide come from a model with a ~8 km grid: "
          "fine for planning, not for navigation.",
}


def _state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return Path(base) / "dapraia"


def parse_spot(text: str) -> forecast.Spot:
    """``Name:lat,lon``. The name may have spaces; the last colon splits it from the coordinates."""
    name, sep, coords = text.rpartition(":")
    try:
        lat_text, lon_text = coords.split(",")
        lat, lon = float(lat_text), float(lon_text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"cannot read {text!r}; use Name:lat,lon, like Piratininga:-22.9553,-43.0809") from None
    if not sep or not name.strip():
        raise argparse.ArgumentTypeError(f"{text!r} has no name; use Name:lat,lon")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise argparse.ArgumentTypeError(f"{lat},{lon} is not a place on Earth")
    return forecast.Spot(name.strip(), lat, lon)


def _hour_text(value: float, lang: str) -> str:
    h, m = int(value), round((value - int(value)) * 60)
    moment = datetime(2000, 1, 1) + timedelta(hours=h, minutes=m)
    if value >= 24:
        return "24h" if lang == "pt" else "24:00"
    return words.clock(moment, lang)


def describe(rule: Rule, lang: str, plan: Plan) -> str:
    """A rule in plain words: ``vento até 15 km/h``, ``from 06:00``."""
    pt = lang == "pt"
    name, value = rule.field, rule.value
    if name == "earliest_hour":
        return f"a partir das {_hour_text(float(value), lang)}" if pt else f"from {_hour_text(float(value), lang)}"
    if name == "latest_hour":
        return f"até as {_hour_text(float(value), lang)}" if pt else f"until {_hour_text(float(value), lang)}"
    if name == "min_hours":
        n = words.duration(float(value), lang)
        return (f"pelo menos {n} seguida" + ("" if float(value) <= 1 else "s")) if pt else f"at least {n} in a row"
    if name == "weekdays":
        names = [words.WEEKDAYS[lang][d] for d in value]
        joined = (", ".join(names[:-1]) + (" e " if pt else " and ") + names[-1]) if len(names) > 1 else names[0]
        return f"só {joined}" if pt else f"only on {joined}"
    if name == "daylight":
        return ("só com luz do dia" if value else "também no escuro") if pt else ("daylight only" if value else "after dark too")
    if name == "tide":
        return ("maré baixa" if value == "low" else "maré cheia") if pt else f"{value} tide"
    if name == "tide_hours":
        n = words.duration(float(value), lang)
        return f"até {n} antes ou depois da maré" if pt else f"within {n} of that tide"
    return words.limit(name, value, lang)


def show_plan(plan: Plan, out) -> None:
    lang = words.lang_of(plan.lang)
    pt = lang == "pt"
    if plan.activity:
        print(f"  {'atividade' if pt else 'activity':<12} {plan.activity:<36} ← \"{plan.activity_quote}\"", file=out)
    for rule in plan.rules:
        print(f"  {'':<12} {describe(rule, lang, plan):<36} ← \"{rule.quote}\"", file=out)
    if not plan.rules:
        print("  (nenhuma regra)" if pt else "  (no rules)", file=out)
    for phrase in plan.unmapped:
        print(f"  {'não sei medir' if pt else 'cannot measure'}: \"{phrase}\"", file=out)
    for spot in plan.spots:
        print(f"  {'lugar' if pt else 'spot'}: {spot.name} ({spot.lat:.4f}, {spot.lon:.4f})", file=out)
    if plan.tide_shift_min:
        shift = f"{plan.tide_shift_min:+d} min"
        print(f"  {'ajuste da maré' if pt else 'tide shift'}: {shift}", file=out)


def _confirm(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        return False
    try:
        answer = input(question + " ").strip().lower()
    except EOFError:
        return False
    return answer in ("s", "sim", "y", "yes")


def _ask(args):
    return model.asker(model=args.model, host=model.DEFAULT_HOST, allow_remote=args.allow_remote_model)


def cmd_plan(args, out, err) -> int:
    lang = words.lang_of(args.lang)
    pt = lang == "pt"
    try:
        reading = prefs.read_plan(args.text, _ask(args))
    except (model.ModelUnavailable, model.RemoteHostRefused) as error:
        print(f"dapraia: {error}", file=err)
        return 2
    plan = Plan(text=args.text, lang=lang, activity=reading.activity, activity_quote=reading.activity_quote,
                spots=list(args.spot), rules=reading.rules, unmapped=reading.unmapped)
    print("Entendi assim:" if pt else "Here is what I read:", file=out)
    show_plan(plan, out)
    if reading.problems:
        print("Ficou de fora:" if pt else "Left out:", file=out)
        for problem in reading.problems:
            print(f"  - {problem}", file=out)
    for rule in direction_warnings(args.text, plan.rules):
        print((f"Confira: \"{rule.quote}\" parece apontar pro outro lado, e virou {describe(rule, lang, plan)}."
               if pt else f"Check: \"{rule.quote}\" seems to point the other way, and became "
               f"{describe(rule, lang, plan)}."), file=out)
    if not plan.spots:
        print("Falta um lugar: --spot \"Nome:lat,lon\"." if pt else "No spot yet: add --spot \"Name:lat,lon\".", file=err)
    if not _confirm("Salvar? [s/N]" if pt else "Save? [y/N]", args.yes):
        print("Nada salvo." if pt else "Nothing saved.", file=out)
        return 1
    save(plan, args.plan)
    print(f"{'Salvo em' if pt else 'Saved to'} {args.plan}", file=out)
    return 0


def _target_day(text: str, today: date) -> date:
    text = text.strip().lower()
    if text in ("today", "hoje"):
        return today
    if text in ("tomorrow", "amanha", "amanhã"):
        return today + timedelta(days=1)
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise SystemExit(f"dapraia: cannot read the day {text!r}; use today, tomorrow or 2026-10-10") from None


def _get_forecasts(plan: Plan, args, err) -> list[forecast.Forecast]:
    found = []
    for spot in plan.spots:
        if args.forecast_dir:
            try:
                found.append(forecast.load_dir(spot, args.forecast_dir))
            except (OSError, ValueError) as error:
                raise forecast.ForecastError(f"no saved forecast for {spot.name} in {args.forecast_dir} "
                                             f"({getattr(error, 'strerror', None) or error})") from None
        else:
            found.append(forecast.fetch(spot, days=args.days, marine=plan.needs_marine))
        if plan.needs_marine and not found[-1].has_marine:
            print(f"dapraia: no wave or tide forecast at {spot.name}; rules about the sea can't pass there", file=err)
    return found


def why_table(plan: Plan, chosen: picking.Pick, out) -> None:
    lang = words.lang_of(plan.lang)
    pt = lang == "pt"
    result = chosen.result if chosen.results else None
    if result is None:
        return
    measured = [r for r in plan.rules if FIELDS[r.field].kind in ("max", "min")]
    columns = []
    for rule in measured:
        attr = FIELDS[rule.field].attr
        if attr not in [c[1] for c in columns]:
            columns.append((words.COLUMNS[attr][0 if pt else 1], attr, words.MEASURES[rule.field][2]))
    has_tide = plan.rule("tide") is not None
    note = ""
    if result.marine_km is not None and plan.needs_marine:
        km = words.num(result.marine_km, lang, 1)
        note = f" · onda e maré do ponto do modelo a {km} km" if pt else f" · waves and tide from a model point {km} km away"
    print(f"{result.spot.name} · {words.day_name(result.day, chosen.today, lang)}{note}", file=out)
    width = max([len(c[0]) for c in columns] + [5]) + 2
    tide_title = "h da maré" if pt else "h to tide"
    header = f"  {'hora' if pt else 'hour':<8}" + "".join(f"{c[0]:>{width}}" for c in columns)
    header += f"{tide_title:>{width}}" if has_tide else ""
    print(header, file=out)
    window = chosen.go if chosen.go is not None and chosen.go.spot == result.spot else None
    for slot in result.slots:
        inside = window is not None and window.start <= slot.hour.time < window.end
        mark = "✓" if slot.ok else "·"
        cells = []
        for _, attr, decimals in columns:
            value = getattr(slot.hour, attr)
            limits = [float(r.value) for r in measured if FIELDS[r.field].attr == attr]
            shown_decimals = decimals
            # 10.4 against a limit of 10 would print as "10" and look like a pass: show the decimal
            if value is not None and any(round(value, decimals) == lim and value != lim for lim in limits):
                shown_decimals = decimals + 1
            cells.append(f"{'-' if value is None else words.num(value, lang, shown_decimals):>{width}}")
        if has_tide:
            check = next((c for c in slot.checks if c.field == "tide"), None)
            gap = "-" if check is None or check.value is None else words.num(float(check.value), lang, 1)
            cells.append(f"{gap:>{width}}")
        failing = ", ".join(_short(c.field, lang) for c in slot.failed)
        tail = f"  {('fora: ' if pt else 'fails: ') + failing}" if failing else ""
        star = " \u25c0" if inside else ""
        print(f"  {words.clock(slot.hour.time, lang):<5} {mark}" + "".join(cells) + tail + star, file=out)
    if result.tides:
        marks = ", ".join(words.tide_at(t.kind, t.time, lang) for t in result.tides)
        if plan.tide_shift_min and plan.get("tide"):
            kind = plan.get("tide")
            marks += ((f" (maré {'baixa' if kind == 'low' else 'cheia'}: modelo {plan.tide_shift_min:+d} min, "
                       "do seu plano)") if pt else f" ({kind} tide: model {plan.tide_shift_min:+d} min, from your plan)")
        print(f"  {marks}", file=out)
    if not result.slots:
        print("  (nenhuma hora nesse dia dentro do que você pediu)" if pt
              else "  (no hour that day inside what you asked for)", file=out)
    print(ATTRIBUTION[lang], file=out)


def _short(name: str, lang: str) -> str:
    if name == "tide":
        return "maré" if lang == "pt" else "tide"
    return words.MEASURES[name][0 if lang == "pt" else 1] if name in words.MEASURES else name


def _load_plan(args, err) -> Plan | None:
    try:
        return load(args.plan)
    except FileNotFoundError:
        print(f"dapraia: no plan at {args.plan}; start with: dapraia plan \"how you like it\" --spot Name:lat,lon",
              file=err)
    except PlanError as error:
        print(f"dapraia: the plan can't be used: {error}", file=err)
    except OSError as error:
        print(f"dapraia: can't read the plan at {args.plan}: {error.strerror or error}", file=err)
    return None


def _parse_now(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise SystemExit(f"dapraia: cannot read --now {text!r}; use 2026-10-07T06:30 or 2026-10-07T06:30-03:00") from None


def cmd_pick(args, out, err) -> int:
    plan = _load_plan(args, err)
    if plan is None:
        return 2
    if not plan.spots:
        print("dapraia: the plan has no spot; run dapraia plan again with --spot Name:lat,lon", file=err)
        return 2
    if not 1 <= args.days <= 16:
        print("dapraia: --days goes from 1 to 16 (what Open-Meteo forecasts)", file=err)
        return 2
    try:
        forecasts = _get_forecasts(plan, args, err)
    except forecast.ForecastError as error:
        print(f"dapraia: {error}", file=err)
        return 3
    now = _parse_now(args.now)
    if args.forecast_dir and now is None and forecasts[0].days():
        today = forecasts[0].days()[0]               # saved answers: their first day is "today"
    else:
        now = now or datetime.now(timezone.utc)
        today = (picking.local_now(forecasts[0], now) or datetime.now()).date()
    day = _target_day(args.day, today)
    available = sorted({d for f in forecasts for d in f.days()})
    if day < today:
        print(f"dapraia: {day.isoformat()} has already gone by", file=err)
        return 5
    if day not in available:
        last = available[-1].isoformat() if available else "nothing"
        print(f"dapraia: no forecast for {day.isoformat()}; it runs until {last} (ask for more with --days, up to 16)",
              file=err)
        return 5
    chosen = picking.choose(plan, forecasts, day, today, now)

    ask = None if args.no_model else _ask(args)
    try:
        result = line.compose(plan, chosen, ask)
    except (model.ModelUnavailable, model.RemoteHostRefused) as error:
        print(f"dapraia: {error}; the line below is the code's own", file=err)
        result = line.compose(plan, chosen, None)

    if args.json:
        print(json.dumps({"title": result.title, "line": result.text, "by_model": result.by_model,
                          "model_problems": result.model_problems, "facts": line.facts(plan, chosen)},
                         ensure_ascii=False, indent=2), file=out)
    else:
        print(result.text, file=out)
        if result.model_problems:
            print(f"(the model's sentence failed the check twice: {'; '.join(result.model_problems)})", file=err)
        if args.why:
            print(file=out)
            why_table(plan, chosen, out)

    _remember(plan, chosen, result, source="saved" if args.forecast_dir else "live")
    if args.ntfy:
        try:
            notify.send(args.ntfy, result.title, result.text[len(result.title):].lstrip(" .:") or result.text,
                        go=chosen.go is not None, server=args.ntfy_server)
        except notify.NotifyError as error:
            print(f"dapraia: {error}", file=err)
            return 4
    return 0


def _picks_path(source: str = "live") -> Path:
    """Picks from saved forecasts (the demo) go to their own file, so they never mix with yours."""
    return _state_dir() / ("picks.json" if source == "live" else "picks-saved.json")


def _read_picks(source: str = "live") -> dict:
    try:
        data = json.loads(_picks_path(source).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


_HOUR_FIELDS = ("wind_kmh", "gust_kmh", "rain_chance", "rain_mm", "temp_c", "uv", "cloud", "wave_m", "water_c")


def _hours_record(slots: list[picking.Slot]) -> list[dict]:
    """Every forecast number for those hours, not only the ones the plan has rules for, so what
    you say about gusts or the water can be compared with what the forecast said about them."""
    rows = []
    for slot in slots:
        row = {"time": slot.hour.time.strftime("%H:%M")}
        for name in _HOUR_FIELDS:
            value = getattr(slot.hour, name)
            if value is not None:
                row[name] = round(value, 1)
        rows.append(row)
    return rows


def plan_id(plan: Plan) -> str:
    """A fingerprint of what decides a pick: the rules, the spots and the tide shift."""
    import hashlib
    key = json.dumps({"rules": [r.as_dict() for r in plan.rules], "spots": [s.as_dict() for s in plan.spots],
                      "shift": plan.tide_shift_min}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _remember(plan: Plan, chosen: picking.Pick, result: line.Line, *, source: str = "live") -> None:
    """Keep each day's pick, so ``dapraia went`` compares with the forecast for the day you went."""
    try:
        record = {"day": chosen.day.isoformat(), "source": source, "plan": plan_id(plan), "line": result.text,
                  "facts": line.facts(plan, chosen)}
        if chosen.results:
            day_result = chosen.result
            record["spot"] = day_result.spot.name
            record["tides"] = [{"kind": t.kind, "time": t.time.isoformat()} for t in day_result.around]
            slots = chosen.go.slots if chosen.go is not None else day_result.slots
            record["hours"] = _hours_record(slots)
        if chosen.go is not None:
            record["window"] = [chosen.go.start.isoformat(), chosen.go.end.isoformat()]
        picks = _read_picks(source)
        picks[record["day"]] = record
        keep = dict(sorted(picks.items())[-60:])
        _picks_path(source).parent.mkdir(parents=True, exist_ok=True)
        _picks_path(source).write_text(json.dumps(keep, ensure_ascii=False, indent=1))
    except OSError:
        pass


def _outing_day(text: str) -> date:
    text = text.strip().lower()
    today = datetime.now().date()
    if text in ("today", "hoje"):
        return today
    if text in ("yesterday", "ontem"):
        return today - timedelta(days=1)
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise SystemExit(f"dapraia: cannot read the day {text!r}; use today, yesterday or 2026-10-08") from None


def cmd_went(args, out, err) -> int:
    plan = _load_plan(args, err)
    if plan is None:
        return 2
    lang = words.lang_of(plan.lang)
    pt = lang == "pt"
    day = _outing_day(args.day)
    picks = _read_picks()
    outing = picks.get(day.isoformat())
    if outing is None:                                   # a demo pick only counts for the very plan it was made with
        saved = _read_picks("saved").get(day.isoformat())
        if saved and saved.get("plan") == plan_id(plan):
            outing = saved
    same_day = outing is not None
    if outing is not None:
        print((f"(comparando com a escolha de {day.isoformat()}: {outing.get('line', '')})" if pt
               else f"(comparing with the pick for {day.isoformat()}: {outing.get('line', '')})"), file=err)
    if outing is None:
        before = (day - timedelta(days=1)).isoformat()
        if before in picks:
            outing = picks[before]
            print((f"(sem escolha guardada pra {day.isoformat()}; comparando com a de {before}, "
                   "sem conta de maré)" if pt else
                   f"(no pick saved for {day.isoformat()}; comparing with {before}, no tide sums)"), file=err)
        else:
            outing = {}
            print(("(nenhuma escolha guardada pra esse dia; o modelo vai só pelas suas palavras)" if pt
                   else "(no pick saved for that day; the model goes by your words alone)"), file=err)
    context = {key: outing[key] for key in ("line", "window", "hours", "tides") if key in outing}
    try:
        reading = prefs.read_went(plan, args.text, context, _ask(args))
    except (model.ModelUnavailable, model.RemoteHostRefused) as error:
        print(f"dapraia: {error}", file=err)
        return 2
    current_values = {r.field: r.value for r in plan.rules}
    if "tide" in current_values and "tide_hours" not in current_values:
        current_values["tide_hours"] = 2
    reading.rules = [r for r in reading.rules if current_values.get(r.field) != r.value]   # no-op proposals
    window = tuple(datetime.fromisoformat(t) for t in outing["window"]) if outing.get("window") else None
    notes = (prefs.settle_tide_hours(reading.rules, plan, outing.get("tides", []), day, lang, window, args.text)
             if same_day else [])
    reading.rules = [r for r in reading.rules if current_values.get(r.field) != r.value]   # a sum can land back on it
    if not reading.rules:
        print("Nada a mudar no plano." if pt else "Nothing to change in the plan.", file=out)
        for problem in reading.problems:
            print(f"  - {problem}", file=out)
        _log_outing(args.text, outing, [])
        return 0
    current = {r.field: r for r in plan.rules}
    print("Mudaria assim:" if pt else "I would change:", file=out)
    for change in reading.rules:
        if change.field in current:
            before = describe(current[change.field], lang, plan)
        elif change.field == "tide_hours" and "tide" in current:
            before = describe(Rule("tide_hours", 2, ""), lang, plan) + (" (padrão)" if pt else " (default)")
        else:
            before = "(nada)" if pt else "(nothing)"
        print(f"  {before}  \u2192  {describe(change, lang, plan)}   \u2190 \"{change.quote}\"", file=out)
    for note in notes:
        print(f"  ({note})", file=out)
    for problem in reading.problems:
        print(f"  - {problem}", file=out)
    updated = prefs.apply(plan, reading.rules, when=datetime.now().isoformat(timespec="minutes"), said=args.text)
    if updated.tide_shift_min and plan.get("tide") and updated.get("tide") != plan.get("tide"):
        updated.tide_shift_min = 0       # the shift was measured for the other kind of tide
        shift_note = (f"  (o ajuste de {plan.tide_shift_min:+d} min era da maré {'baixa' if plan.get('tide') == 'low' else 'cheia'}; "
                      "fica zerado: meça de novo com dapraia tide-shift)" if pt else
                      f"  (the {plan.tide_shift_min:+d} min shift was for the {plan.get('tide')} tide; it is reset to 0: "
                      "measure again with dapraia tide-shift)")
        print(shift_note, file=out)
    _, conflicts = check_pairs(updated.rules)
    if conflicts:
        print(("Não dá pra aplicar: " if pt else "Can't apply: ") + "; ".join(conflicts), file=err)
        return 1
    if not _confirm("Aplicar? [s/N]" if pt else "Apply? [y/N]", args.yes):
        print("Plano mantido." if pt else "Plan kept as it was.", file=out)
        _log_outing(args.text, outing, [])
        return 1
    save(updated, args.plan)
    _log_outing(args.text, outing, [r.as_dict() for r in reading.rules])
    print(f"{'Salvo em' if pt else 'Saved to'} {args.plan}", file=out)
    return 0


def _log_outing(said: str, outing: dict, changes: list) -> None:
    try:
        state = _state_dir()
        state.mkdir(parents=True, exist_ok=True)
        with (state / "outings.jsonl").open("a") as log:
            log.write(json.dumps({"when": datetime.now().isoformat(timespec="minutes"), "said": said,
                                  "pick_day": outing.get("day"), "pick": outing.get("line"), "changes": changes},
                                 ensure_ascii=False) + "\n")
    except OSError:
        pass


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--plan", type=Path, default=default_path(), help="plan file (default %(default)s)")
    parser.add_argument("--model", default=model.DEFAULT_MODEL, help="Ollama model (default %(default)s)")
    parser.add_argument("--allow-remote-model", action="store_true",
                        help="let OLLAMA_HOST point at another machine (off by default)")


def main(argv: list[str] | None = None, *, out=None, err=None) -> int:
    out, err = out or sys.stdout, err or sys.stderr
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] == "plan":
        parser = argparse.ArgumentParser(prog="dapraia plan", description="Say how you like it; the model turns it into rules.")
        parser.add_argument("text", help="how you like it, in your own words")
        parser.add_argument("--spot", action="append", type=parse_spot, default=[], required=True,
                            help="Name:lat,lon (repeat for more than one place)")
        parser.add_argument("--lang", default="pt", help="pt or en: the language of the daily line (default pt)")
        parser.add_argument("--yes", action="store_true", help="save without asking")
        _common(parser)
        return cmd_plan(parser.parse_args(argv[1:]), out, err)

    if argv and argv[0] in ("went", "fui"):
        parser = argparse.ArgumentParser(prog=f"dapraia {argv[0]}", description="Say how the outing went; the model proposes changes to the plan.")
        parser.add_argument("text", help="how it went, in your own words")
        parser.add_argument("--day", default="today", help="the day you went: today (default), yesterday or a date")
        parser.add_argument("--yes", action="store_true", help="apply without asking")
        _common(parser)
        return cmd_went(parser.parse_args(argv[1:]), out, err)

    if argv and argv[0] == "tide-shift":
        parser = argparse.ArgumentParser(
            prog="dapraia tide-shift",
            description="Move the model's tide times by a fixed number of minutes, measured once against "
                        "an official tide table for your coast.")
        parser.add_argument("minutes", type=int, help="minutes to add (negative to subtract), -180 to 180")
        _common(parser)
        args = parser.parse_args(argv[1:])
        if not -180 <= args.minutes <= 180:
            parser.error("the shift must be between -180 and 180 minutes")
        plan = _load_plan(args, err)
        if plan is None:
            return 2
        if not plan.get("tide"):
            print("dapraia: the plan has no tide rule; the shift is for the tide a rule is about", file=err)
            return 2
        plan.tide_shift_min = args.minutes
        save(plan, args.plan)
        print(f"tide shift: {args.minutes:+d} min", file=out)
        return 0

    if argv and argv[0] == "show":
        parser = argparse.ArgumentParser(prog="dapraia show", description="Print the saved plan.")
        _common(parser)
        args = parser.parse_args(argv[1:])
        plan = _load_plan(args, err)
        if plan is None:
            return 2
        show_plan(plan, out)
        return 0

    parser = argparse.ArgumentParser(
        prog="dapraia",
        description="The hour to go outside tomorrow, from an open forecast and your own words, "
                    "explained by a model running on this machine.",
        epilog="Other commands: dapraia plan \"...\" --spot Name:lat,lon · dapraia went \"...\" · "
               "dapraia tide-shift MINUTES · dapraia show")
    parser.add_argument("--day", default="tomorrow", help="today, tomorrow (default) or a date like 2026-10-10")
    parser.add_argument("--why", action="store_true", help="show the hours behind the pick")
    parser.add_argument("--json", action="store_true", help="print the line and its facts as JSON")
    parser.add_argument("--no-model", action="store_true", help="the code writes the whole line")
    parser.add_argument("--ntfy", metavar="TOPIC", help="also send the line to this ntfy topic")
    parser.add_argument("--ntfy-server", default=notify.DEFAULT_SERVER, help="ntfy server (default %(default)s)")
    parser.add_argument("--days", type=int, default=4, help="days of forecast to fetch (default 4)")
    parser.add_argument("--forecast-dir", type=Path, help="read saved Open-Meteo answers from here instead of the network")
    parser.add_argument("--now", help="pretend it is this moment, ISO format (for demos and tests)")
    parser.add_argument("--version", action="version", version=f"dapraia {__version__}")
    _common(parser)
    return cmd_pick(parser.parse_args(argv), out, err)
