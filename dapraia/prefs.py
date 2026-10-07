"""The model reads what you said and proposes rules; the rules module decides which survive.

Two places use the model this way: ``plan`` (how you like it, in your words)
and ``went`` (how it actually went, after you came back). In both, each rule
must quote the words it came from, and nothing is saved until you say yes.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import words
from .rules import FIELDS, Plan, Rule, check_pairs, check_rule, check_rules, measurable_number, quoted

_FIELD_NAMES = list(FIELDS)

SCHEMA = {
    "type": "object",
    "properties": {
        "activity": {"type": "object", "properties": {"name": {"type": "string"}, "quote": {"type": "string"}},
                     "required": ["name", "quote"]},
        "rules": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "field": {"type": "string", "enum": _FIELD_NAMES},
                "value": {"anyOf": [{"type": "number"}, {"type": "string"}, {"type": "boolean"},
                                    {"type": "array", "items": {"type": "integer"}}]},
                "quote": {"type": "string"},
            },
            "required": ["field", "value", "quote"],
        }},
        "unmapped": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["activity", "rules", "unmapped"],
}

FIELD_GUIDE = """Fields (use only these, and only for what the person actually said):
- earliest_hour, latest_hour: local clock hours, 0 to 24. The outing starts at or after earliest_hour and ends by latest_hour.
  A part of the day is a range, so it sets both, from the same quote: early morning about 5 to 9,
  morning 6 to 12, afternoon 12 to 18, evening 17 to 21. Half hours count: "7 e meia" or "half past 7"
  is 7.5, and "5 e meia da tarde" is 17.5; quote the whole time, "a partir das 7 e meia".
- min_hours: how many hours in a row they need.
- weekdays: list of day numbers, 0 = Monday to 6 = Sunday. A weekend is [5, 6].
- daylight: false only when they want to be out in the dark. Leave it out otherwise.
- wind_max_kmh, wind_min_kmh: wind speed in km/h.
- gust_max_kmh: wind gusts in km/h.
- rain_chance_max: chance of rain, in percent. Forecasts rarely say 0, so "no rain" is about 20.
- rain_mm_max: rain in one hour, in mm.
- temp_min_c, temp_max_c: air temperature in °C.
- uv_max: UV index.
- cloud_max: cloud cover in percent.
- wave_max_m, wave_min_m: wave height in metres.
- water_min_c: sea water temperature in °C.
- tide: "low" or "high".
- tide_hours: how many hours before or after that tide still count. Only with a tide rule."""

PLAN_SYSTEM = """You turn what a person says about when they like to go outside into rules that a weather
and sea forecast can be checked against.

""" + FIELD_GUIDE + """

How to answer:
1. Every rule has "quote": the exact words from the text it comes from, copied character for character,
   1 to 6 words. A quote with a number in it must be the quote for the rule that uses that number.
2. When the text gives a number, the rule uses that number. When it says it in words ("pouco vento",
   "sem chuva", "light wind"), pick a sensible number; the person will see it and can change it.
3. Anything they care about that no field can measure goes in "unmapped", copied from the text,
   numbers included ("perto de casa", "Posto 6").
4. "activity": what they want to do, with its quote. Empty name and quote when they don't say.
5. No rule for something they did not say.

Example. Text: "corrida cedo, antes das 8h, sem chuva e com pouco vento"
{"activity": {"name": "corrida", "quote": "corrida"},
 "rules": [{"field": "latest_hour", "value": 8, "quote": "antes das 8h"},
           {"field": "earliest_hour", "value": 5, "quote": "cedo"},
           {"field": "rain_chance_max", "value": 20, "quote": "sem chuva"},
           {"field": "wind_max_kmh", "value": 15, "quote": "pouco vento"}],
 "unmapped": []}"""

WENT_SYSTEM = """A person went outside at a time picked from their plan and the forecast. Now they say how it went.
Propose changes to the plan only where their words say the plan should have been stricter or looser.

""" + FIELD_GUIDE + """

How to answer:
1. "rules" lists only the rules to change or add, each with its new value and "quote": the exact words
   from what they said about the outing, copied character for character, 1 to 8 words.
2. Compare their words with the forecast for those hours ("hours" holds every number it gave). If it was windier than they liked while the
   forecast said 9 km/h, a wind limit under the current one makes sense. Move a limit one modest step,
   about a quarter of its value, not all the way down to what the forecast said.
3. What they say about the sand or the water belongs to the tide rule: sand already soft or wet, or the
   water already high, some time after a low tide means tide_hours was too wide; make it the time
   between that low tide and the moment they describe, rounded down to a half hour.
4. Nothing to change when they were happy: an empty "rules" list.
5. What they said that no field can measure goes in "unmapped".
6. "activity": empty name and quote."""


@dataclass
class Reading:
    activity: str = ""
    activity_quote: str = ""
    rules: list[Rule] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    raw: str = ""


def _parse(raw: str) -> dict | None:
    try:
        data = json.loads(raw)
    except ValueError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(raw[start:end + 1])
        except ValueError:
            return None
    return data if isinstance(data, dict) else None


def _check_plan_answer(raw: str, text: str) -> Reading:
    data = _parse(raw)
    if data is None:
        return Reading(problems=["the answer is not JSON"], raw=raw)
    reading = Reading(raw=raw)
    activity = data.get("activity") or {}
    name, quote = str(activity.get("name", "")).strip(), str(activity.get("quote", "")).strip()
    if name and quoted(quote, text):
        reading.activity, reading.activity_quote = name, quote
    elif name:
        reading.problems.append(f"the activity quote \"{quote}\" is not in the text")
    unmapped = []
    for phrase in data.get("unmapped") or []:
        phrase = str(phrase).strip()
        measurable = measurable_number(phrase, text)
        if phrase and quoted(phrase, text) and measurable is None:
            unmapped.append(phrase)
        elif phrase and measurable is not None:
            reading.problems.append(f"the unmapped phrase \"{phrase}\" has {measurable}, which a rule can measure")
        elif phrase:
            reading.problems.append(f"the unmapped phrase \"{phrase}\" is not in the text")
    reading.unmapped = unmapped
    rules, problems = check_rules(data.get("rules") or [], text, unmapped,
                                  [reading.activity_quote] if reading.activity_quote else [])
    reading.rules = rules
    reading.problems += problems
    return reading


def _retry_prompt(text: str, raw: str, problems: list[str]) -> str:
    return (text + "\n\nYour last answer was:\n" + raw + "\n\nIt has these problems:\n- "
            + "\n- ".join(problems) + "\nAnswer again. Quote the text exactly, and use every number in it.")


def read_plan(text: str, ask) -> Reading:
    """``ask(system, user, schema) -> str``. Asks at most twice and keeps the better answer."""
    first = _check_plan_answer(ask(PLAN_SYSTEM, text, SCHEMA), text)
    if not first.problems:
        return first
    second = _check_plan_answer(ask(PLAN_SYSTEM, _retry_prompt(text, first.raw, first.problems), SCHEMA), text)
    return min((first, second), key=lambda r: (len(r.problems), -len(r.rules)))


def _check_went_answer(raw: str, said: str) -> Reading:
    data = _parse(raw)
    if data is None:
        return Reading(problems=["the answer is not JSON"], raw=raw)
    reading = Reading(raw=raw)
    seen = set()
    for item in data.get("rules") or []:
        if not isinstance(item, dict):
            continue
        # What they say about the outing is an observation ("at 9 the tide was up"), not a limit,
        # so a number in it need not be the rule's value. The quote must still be theirs.
        rule, problem = check_rule(item, said, match_numbers=False)
        if problem:
            reading.problems.append(problem)
        elif rule.field in seen:
            reading.problems.append(f"{rule.field} appears twice; keep one")
        else:
            seen.add(rule.field)
            reading.rules.append(rule)
    reading.unmapped = [str(p).strip() for p in data.get("unmapped") or [] if quoted(str(p), said)]
    return reading


def read_went(plan: Plan, said: str, outing: dict, ask) -> Reading:
    """Changes to the plan proposed from what you said about the outing; at most two asks.

    Changes that would contradict the rest of the plan (a gust limit under the wind
    limit, a minimum above a maximum) are dropped here and listed as problems.
    """
    context = json.dumps({
        "plan": [r.as_dict() for r in plan.rules],
        "forecast_for_the_outing": outing,
        "what_they_said": said,
    }, ensure_ascii=False, indent=1)
    first = _against_plan(_check_went_answer(ask(WENT_SYSTEM, context, SCHEMA), said), plan)
    if not first.problems:
        return first
    second = _against_plan(_check_went_answer(
        ask(WENT_SYSTEM, _retry_prompt(context, first.raw, first.problems), SCHEMA), said), plan)
    return min((first, second), key=lambda r: (len(r.problems), -len(r.rules)))


def _against_plan(reading: Reading, plan: Plan) -> Reading:
    merged = {r.field: r for r in plan.rules}
    merged.update({r.field: r for r in reading.rules})
    kept, conflicts = check_pairs(list(merged.values()))
    kept_fields = {r.field for r in kept}
    reading.rules = [r for r in reading.rules if r.field in kept_fields]
    reading.problems += conflicts
    return reading


def apply(plan: Plan, changes: list[Rule], *, when: str, said: str) -> Plan:
    """A new plan with the changes in place, and a history entry saying why."""
    rules = {r.field: r for r in plan.rules}
    entry = {"when": when, "said": said, "changes": []}
    for change in changes:
        before = rules.get(change.field)
        entry["changes"].append({"field": change.field, "from": before.value if before else None,
                                 "to": change.value, "quote": change.quote})
        rules[change.field] = change
    order = [r.field for r in plan.rules] + [c.field for c in changes if c.field not in {r.field for r in plan.rules}]
    updated = Plan.from_dict(plan.as_dict())
    updated.rules = [rules[name] for name in order]
    updated.history = plan.history + [entry]
    return updated


_BEFORE_AFTER = r"(?:depois|antes|após|apos|after|before)"
# A moment on the clock: "às 8h30", "at 9 pm", "por volta das 7", "às 4 da tarde", "às 8 e meia", "07:40", "8h30".
# A bare "1h30" right before "depois/antes" is a span, not a clock.
_PARTS = (r"am\b|pm\b|a\.m\.|p\.m\.|d[ae] manh[ãa]|d[ae] madrugada|d[ae] tarde|[àa] tarde|d[ae] noite|[àa] noite|"
          r"of the morning|in the morning|in the afternoon|in the evening|at night")
_SAID_CLOCK = re.compile(
    r"\b(?:às|as|at|around|por volta das|lá pelas|la pelas)\s+(\d{1,2})(?!\d)(?!\s*(?:hours?|min|minutos|minutes)\b)"
    r"(?:\s*(?:horas?|hrs?|hs|h|:)\s*(\d{2})?)?"
    r"(?:\s*(e meia|e trinta|e quinze|e quarenta e cinco|e 30|e 15|e 45))?(?:\s*(" + _PARTS + r"))?"
    r"|\b(\d{1,2}):(\d{2})\s*(am\b|pm\b|a\.m\.|p\.m\.)?", re.I)
# A reading is used only when nothing time-like follows it. Rather than list every spelling
# ("8.30", "8 e dez", "9 de noitinha", "tonight"), the check refuses: a digit or a "." / ":" / ","
# then a digit, "e"/"and", a unit of time or am/pm, and any word with a part-of-day root within
# the next four words.
_DAY_ROOT = re.compile(r"\b(?:tard|noit|manh|madrug|afternoon|evening|night|morning|tonight)\w*", re.I)
_TIME_NEXT = re.compile(r"^(?:[.,:]?\s*\d|\s*(?:e|and|ish|&)\b|\s*(?:hs?|hrs?|horas?|hours?|min\w*|[ap]\.?m\b|"
                        r"o'?clock)\b)", re.I)


def _read_whole(quote: str, end: int) -> bool:
    rest = quote[end:]
    if _TIME_NEXT.match(rest):
        return False
    return not _DAY_ROOT.search(" ".join(rest.split()[:4]))


# A span of time from the tide: "2h depois", "1h30 antes", "1h e meia depois", "40 min após",
# "uma hora depois", "meia hora depois", "an hour after". Not after "às/as/at", which is a clock.
_SAID_SPAN = re.compile(
    r"(?<!\bàs )(?<!\bas )(?<!\bat )(?<![\d.,])\b(\d{1,2})\s*h\s*(\d{2}|e meia)?\s+" + _BEFORE_AFTER + r"\b"
    r"|\b(\d+(?:[.,]\d+)?)\s*(h|horas?|hours?|min|minutos|minutes)(?:\s*e meia)?\s+" + _BEFORE_AFTER + r"\b"
    r"|\b(uma hora e meia|uma hora|meia hora|an hour and a half|an hour|half an hour)\s+" + _BEFORE_AFTER + r"\b",
    re.I)
_SPAN_AFTER = re.compile(r"\b(?:depois de|após|apos|after)\s+(\d{1,2})\s*h\s*(\d{2}|e meia)?\b", re.I)
# A span that names the tide right after it ("2h depois da maré", "2 hours after the low tide") is a span
# even after "às"/"por volta das"/"around".
_TO_TIDE = (r"\s+(?:d[ao]s?\s+|of\s+the\s+|the\s+)?(?:maré|mare|baixa|cheia|preamar|tide|low|high)")
_SPAN_TO_TIDE = re.compile(
    r"\b(\d{1,2})\s*h\s*(\d{2}|e meia)?\s+" + _BEFORE_AFTER + _TO_TIDE +
    r"|\b(\d+(?:[.,]\d+)?)\s*(h|horas?|hours?|min|minutos|minutes)(?:\s*(e meia))?\s+" + _BEFORE_AFTER + _TO_TIDE,
    re.I)
_SPELLED_SPANS = {"uma hora e meia": 90, "uma hora": 60, "meia hora": 30, "an hour and a half": 90,
                  "an hour": 60, "half an hour": 30}


def _gap_text(minutes: int, lang: str) -> str:
    h, m = divmod(minutes, 60)
    if lang == "pt":
        return f"{h}h{m:02d}" if h and m else (f"{h}h" if h else f"{m} min")
    return " ".join(part for part in (f"{h} h" if h else "", f"{m} min" if m else "") if part) or "0 min"


def _span_minutes(quote: str) -> tuple[int, bool] | None:
    """(minutes, after the tide?) for "2h depois", "1h30 antes", "meia hora depois", "40 min after"."""
    m = _SAID_SPAN.search(quote)
    if m is None:
        lead = _SPAN_AFTER.search(quote)
        if lead is None:
            return None
        extra = lead.group(2) or "0"
        return int(lead.group(1)) * 60 + (30 if extra == "e meia" else int(extra)), True
    after = not re.search(r"\b(?:antes|before)\b", m.group(0), re.I)
    if m.group(1) is not None:
        extra = m.group(2) or "0"
        return int(m.group(1)) * 60 + (30 if extra == "e meia" else int(extra)), after
    if m.group(5) is not None:
        return _SPELLED_SPANS[m.group(5).lower()], after
    amount, unit = float(m.group(3).replace(",", ".")), m.group(4).lower()
    minutes = round(amount * (1 if unit.startswith("min") else 60))     # "h" and "horas" are hours
    if re.search(r"e meia", m.group(0), re.I) and not unit.startswith("min"):
        minutes += 30
    return minutes, after


def _span_to_tide(quote: str) -> tuple[int, bool] | None:
    """A span that names the tide: wins over a clock reading of the same digits."""
    m = _SPAN_TO_TIDE.search(quote)
    if m is None:
        return None
    after = not re.search(r"\b(?:antes|before)\b", m.group(0), re.I)
    if m.group(1) is not None:
        extra = m.group(2) or "0"
        return int(m.group(1)) * 60 + (30 if extra == "e meia" else int(extra)), after
    amount, unit = float(m.group(3).replace(",", ".")), m.group(4).lower()
    minutes = round(amount * (1 if unit.startswith("min") else 60))
    return minutes + (30 if m.group(5) else 0), after


def _clock_detail(quote: str) -> tuple[int, int, bool, bool] | None:
    """(hour, minute, part of day given?, read whole?) for the first clock time in the words."""
    m = _SAID_CLOCK.search(quote)
    if m is None:
        return None
    if m.group(1) is not None:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        extra = (m.group(3) or "").lower()
        minute += {"e meia": 30, "e trinta": 30, "e quinze": 15, "e quarenta e cinco": 45,
                   "e 30": 30, "e 15": 15, "e 45": 45}.get(extra, 0)
        part = (m.group(4) or "").lower()
    else:
        hour, minute, part = int(m.group(5)), int(m.group(6)), (m.group(7) or "").lower()
    late = part.startswith(("pm", "p.m.", "da tarde", "de tarde", "à tarde", "a tarde", "da noite", "de noite",
                            "à noite", "a noite", "in the afternoon", "in the evening", "at night"))
    early = part.startswith(("am", "a.m.", "da manh", "de manh", "da madrugada", "de madrugada", "of the morning",
                             "in the morning"))
    if late and hour == 12 and "noite" in part:
        hour = 24                     # "12 da noite" is midnight, at the end of that day
    elif late and hour < 12:
        hour += 12
    if early and hour == 12:
        hour = 0
    if hour > 24 or minute > 59:
        return None
    whole = _read_whole(quote, m.end()) and len(_SAID_CLOCK.findall(quote)) == 1
    return hour, minute, bool(part), whole


def _choose_half_of_day(detail: tuple[int, int, bool, bool], day: date, plan: Plan,
                        window: tuple[datetime, datetime] | None) -> tuple[int, int] | None:
    """Morning or evening for a time with no part of day: the reading nearer the hours of that
    outing (the pick's window, else the plan's hours, else 6 to 18). None when it's a toss-up."""
    hour, minute, given, _ = detail
    if given or hour > 12 or hour == 0:
        return hour, minute
    readings = [(hour, minute)] + ([(hour + 12, minute)] if hour < 12 else [(0, minute)])
    if window is not None:
        start = window[0].hour + window[0].minute / 60
        end = window[1].hour + window[1].minute / 60 or 24.0
    else:
        start, end = float(plan.get("earliest_hour", 6)), float(plan.get("latest_hour", 18))
    def distance(reading):
        at = reading[0] + reading[1] / 60
        return 0.0 if start <= at <= end else min(abs(at - start), abs(at - end))
    near, far = sorted(readings, key=distance)
    if distance(near) == distance(far):
        return None
    return near


def _clock(quote: str) -> tuple[int, int] | None:
    """(hour, minute) on a 24-hour clock, or None when the words name no moment."""
    detail = _clock_detail(quote)
    return None if detail is None else (detail[0], detail[1])


def _hours(value: float) -> float | int:
    return int(value) if float(value).is_integer() else value


def settle_tide_hours(rules: list[Rule], plan: Plan, tides: list[dict], day: date, lang: str,
                      window: tuple[datetime, datetime] | None = None, said: str = "") -> list[str]:
    """Do the tide arithmetic in code, in the direction the model proposed.

    The model reads which rule the words are about and whether the window should get
    tighter or looser. The hours come from here: from a span the person wrote ("2h depois
    da maré"), or from a clock time ("às 8h30") and the tide of that day. Tighter means the
    last half hour before that moment; looser means the half hour at or before it. If the
    sum points the other way from the proposal, the proposal stays and the note says so.

    It fails closed: a clock time it can't read whole ("8hs e meia"), or one that could be
    morning or evening as easily ("às 8" with no window to go by), gets a note and no sum.
    """
    kind = next((r.value for r in rules if r.field == "tide"), None) or plan.get("tide")
    if not kind:
        if any(r.field == "tide_hours" for r in rules):
            return [("sem conta do código: o plano não tem regra de maré" if lang == "pt"
                     else "no sum by the code: the plan has no tide rule")]
        return []
    marks = [datetime.fromisoformat(t["time"]) for t in tides if t.get("kind") == kind]
    current = float(plan.get("tide_hours", 2))
    tide_name = ("maré baixa" if kind == "low" else "maré cheia") if lang == "pt" else f"{kind} tide"
    notes = []
    for rule in rules:
        if rule.field != "tide_hours":
            continue
        proposed = float(rule.value)
        if proposed == current:
            continue
        tide_span, clock_reading = _span_to_tide(rule.quote), _clock(rule.quote)
        if tide_span and clock_reading:
            # "às 2h depois da maré" can be a time (2:00) or a span (two hours): the code won't pick one
            notes.append((f"sem conta do código: \"{rule.quote}\" pode ser um horário ou um intervalo; "
                          "ficou a proposta do modelo" if lang == "pt" else
                          f"no sum by the code: \"{rule.quote}\" can be a time or a span; the model's proposal stays"))
            continue
        span = tide_span or (None if clock_reading else _span_minutes(rule.quote))
        if span is None and _clock(rule.quote) is None:
            notes.append((f"sem conta do código: não achei um horário escrito com números em \"{rule.quote}\""
                          if lang == "pt" else
                          f"no sum by the code: no time written in digits in \"{rule.quote}\""))
            continue
        if span is None and not marks:
            notes.append(("sem conta do código: a escolha desse dia não tem horários de maré" if lang == "pt"
                          else "no sum by the code: that day's pick has no tide times"))
            continue
        if span is None:
            detail = _clock_detail(rule.quote)
            # a part of the day said anywhere ("fui à noite, e às 9...") that the reading didn't take in
            loose_part = not detail[2] and bool(_DAY_ROOT.search(said))
            if not detail[3] or loose_part:
                notes.append((f"sem conta do código: não consegui ler o horário inteiro em \"{rule.quote}\""
                              if lang == "pt" else f"no sum by the code: couldn't read the whole time in \"{rule.quote}\""))
                continue
            clock = _choose_half_of_day(detail, day, plan, window)
            if clock is None:
                notes.append((f"sem conta do código: \"{rule.quote}\" pode ser de manhã ou de noite"
                              if lang == "pt" else f"no sum by the code: \"{rule.quote}\" could be morning or evening"))
                continue
        if span is not None:
            minutes, after = span
            side = ("depois" if after else "antes") if lang == "pt" else ("after" if after else "before")
            seen = (f"{_gap_text(minutes, lang)} {side} da {tide_name}" if lang == "pt"
                    else f"{_gap_text(minutes, lang)} {side} the {tide_name}")
        else:
            moment = datetime.combine(day, datetime.min.time()) + timedelta(hours=clock[0], minutes=clock[1])
            tide_time = min(marks, key=lambda t: abs((t - moment).total_seconds()))
            minutes = round(abs((moment - tide_time).total_seconds()) / 60)
            at = (f"{tide_time.hour}h{tide_time.minute:02d}" if tide_time.minute else f"{tide_time.hour}h") \
                if lang == "pt" else tide_time.strftime("%H:%M")
            side = (("depois" if moment >= tide_time else "antes") if lang == "pt"
                    else ("after" if moment >= tide_time else "before"))
            shown_hour = clock[0] % 24
            said_at = ("meia-noite" if clock == (24, 0) else
                       f"{shown_hour}h{clock[1]:02d}" if clock[1] else f"{shown_hour}h")
            seen = (f"{said_at} fica {_gap_text(minutes, lang)} {side} da {tide_name} das {at}"
                    if lang == "pt" else f"{clock[0] % 24:02d}:{clock[1]:02d} is {_gap_text(minutes, lang)} {side} "
                    f"the {at} {tide_name}")
        gap = minutes / 60
        tighter = proposed < current
        if tighter:
            computed = min(6.0, max(0.5, math.ceil(gap * 2 - 1e-9) / 2 - 0.5))
            agrees = computed < current
        else:
            computed = min(6.0, max(0.5, math.floor(gap * 2 + 1e-9) / 2))
            agrees = computed > current
        shown = words.duration(computed, lang)
        if agrees:
            rule.value = _hours(computed)
            notes.append((f"conta do código: {seen}, então até {shown}" if lang == "pt"
                          else f"worked out by the code: {seen}, so within {shown}"))
        else:
            notes.append((f"conta do código: {seen}, o que daria até {shown}, no sentido contrário; "
                          "ficou a proposta do modelo" if lang == "pt" else
                          f"worked out by the code: {seen}, which gives {shown}, the other way; "
                          "the model's proposal stays"))
    return notes
