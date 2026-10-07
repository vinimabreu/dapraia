"""The one line you read: a headline the code writes, then one sentence of why.

The headline (go or not, where, from when to when) always comes from the
code. The sentence can come from the model, and it is checked before you see
it: every number, clock time, date and day it uses must be in the facts the
code handed over, it can't contradict the verdict, and it has to be short. If
it fails twice, the code's own sentence is used instead.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from . import pick as picking
from . import words
from .rules import FIELDS, Plan

MAX_WHY = 160


@dataclass
class Line:
    title: str              # "Dá praia amanhã (qua 07/10)", the push notification title
    headline: str           # title plus where and when
    why: str                # one sentence
    by_model: bool = False
    model_problems: list[str] | None = None

    @property
    def text(self) -> str:
        return f"{self.headline} {self.why}".strip()


def _window_values(window: picking.Window, attr: str) -> list[float]:
    return [getattr(s.hour, attr) for s in window.slots if getattr(s.hour, attr) is not None]


def conditions(plan: Plan, window: picking.Window, lang: str) -> list[tuple[float, str, str, str]]:
    """(margin, what it was, the rule, the rule's kind) for every rule that reads the forecast,
    the tide first and then the tightest."""
    found: list[tuple[float, str, str, str]] = []
    for rule in plan.rules:
        spec = FIELDS[rule.field]
        if spec.kind in ("max", "min") and rule.field in words.MEASURES:
            values = _window_values(window, spec.attr)
            if not values:
                continue
            margin = min(c.margin for s in window.slots for c in s.checks if c.field == rule.field)
            found.append((margin, words.measure(rule.field, values, lang),
                          words.limit(rule.field, rule.value, lang), spec.kind))
        elif spec.kind == "tide":
            times = [c.tide_time for s in window.slots for c in s.checks if c.field == "tide" and c.tide_time]
            if times:
                margin = min(c.margin for s in window.slots for c in s.checks if c.field == "tide")
                hours = words.num(float(plan.get("tide_hours", 2)), lang, 1)
                asked = (f"perto da maré {'baixa' if rule.value == 'low' else 'cheia'}, até {hours}h dela"
                         if lang == "pt" else f"near {rule.value} tide, within {hours} h of it")
                # The tide anchors the window, so it goes first whatever its margin.
                found.append((-99.0, words.tide_at(rule.value, times[0], lang), asked, "tide"))
    found.sort(key=lambda item: item[0])
    return found


def _condition_text(margin: float, what: str, rule: str, kind: str, window: picking.Window, lang: str) -> str:
    """One condition as a short phrase: "vento de 3 a 4 km/h, bem abaixo do limite (limite: vento até 15 km/h)",
    or for the tide, where it falls against the hours: "maré baixa às 7h10, dentro do horário"."""
    if kind == "tide":
        return f"{what}, {_where_in(window, what, lang)}"
    label = "limite" if lang == "pt" else "limit"
    return f"{what}, {room(margin, lang, kind)} ({label}: {rule})"


def _where_in(window: picking.Window, tide_text: str, lang: str) -> str:
    """Where the tide falls in the window, worked out here so the model doesn't guess it."""
    times = [c.tide_time for s in window.slots for c in s.checks if c.field == "tide" and c.tide_time]
    if not times:
        return ""
    span = (window.end - window.start).total_seconds()
    share = (times[0] - window.start).total_seconds() / span if span else 0.5
    if share < 0:
        return "antes do horário" if lang == "pt" else "before the window"
    if share > 1:
        return "depois do horário" if lang == "pt" else "after the window"
    if share < 1 / 3:
        return "no começo do horário" if lang == "pt" else "early in the window"
    if share <= 2 / 3:
        return "no meio do horário" if lang == "pt" else "in the middle of the window"
    return "perto do fim do horário" if lang == "pt" else "near the end of the window"


def _sky(window: picking.Window, lang: str) -> str:
    """The cloud cover in words, so the sentence can't call an overcast morning sunny."""
    clouds = [s.hour.cloud for s in window.slots if s.hour.cloud is not None]
    if not clouds:
        return ""
    mean = sum(clouds) / len(clouds)
    shown = words.num(mean, lang)
    if mean < 25:
        return f"céu limpo ({shown}% de nuvens)" if lang == "pt" else f"clear sky ({shown}% cloud)"
    if mean < 60:
        return f"algumas nuvens ({shown}% de nuvens)" if lang == "pt" else f"some cloud ({shown}% cloud)"
    if mean < 85:
        return f"nublado ({shown}% de nuvens)" if lang == "pt" else f"cloudy ({shown}% cloud)"
    return f"céu encoberto ({shown}% de nuvens)" if lang == "pt" else f"overcast ({shown}% cloud)"


def room(margin: float, lang: str, kind: str = "max") -> str:
    """How much room a weather condition leaves inside its limit, in words the model can repeat."""
    if margin >= 1:
        if kind == "min":
            return "bem acima do mínimo" if lang == "pt" else "well above the minimum"
        return "bem abaixo do limite" if lang == "pt" else "well inside the limit"
    if margin >= 0.3:
        return "dentro do limite" if lang == "pt" else "inside the limit"
    return "perto do limite" if lang == "pt" else "close to the limit"


def _fail_text(check: picking.Check, lang: str, *, after: bool = False) -> str:
    return words.fails(check.field, check.limit, lang, missing=check.value is None,
                       tide_kind=str(check.limit) if check.field == "tide" else "", after=after)


def _window_text(window: picking.Window, today, lang: str, *, preposition: bool = True) -> str:
    day = (words.on_day if preposition else words.day_name)(window.day, today, lang)
    where = f"em {window.spot.name}" if lang == "pt" else f"at {window.spot.name}"
    return f"{day}, {words.span(window.start, window.end, lang)} {where}"


def facts(plan: Plan, chosen: picking.Pick) -> dict:
    """Everything the sentence may use, already written the way the reader will read it."""
    lang = words.lang_of(plan.lang)
    data: dict = {
        "language": "Portuguese (Brazil)" if lang == "pt" else "English",
        "activity": plan.activity or None,
        "already_said": {"day": words.on_day(chosen.day, chosen.today, lang)},
    }
    if chosen.go is not None:
        window = chosen.go
        result = chosen.result
        data["verdict"] = "go"
        data["already_said"]["spot"] = window.spot.name
        data["already_said"]["window"] = words.span(window.start, window.end, lang)
        data["conditions"] = [_condition_text(margin, what, rule, kind, window, lang)
                              for margin, what, rule, kind in conditions(plan, window, lang)]
        sky = _sky(window, lang)
        if sky:
            data["sky"] = sky
        ending = picking.after(result, window)
        if ending and ending[1] is not None:
            when = words.clock(ending[0], lang)
            what = _fail_text(ending[1], lang, after=True)
            data["after_the_window"] = (f"depois das {when}, {what}" if lang == "pt" else f"after {when}, {what}")
        if chosen.others:
            data["also_good"] = [_window_text(w, chosen.today, lang) for w in chosen.others[:2]]
    else:
        data["verdict"] = "no-go"
        result = chosen.result if chosen.results else None
        if result is not None:
            data["already_said"]["spot"] = result.spot.name
            reason = picking.main_reason(result)
            longest = picking.longest_run(result)
            if longest:
                data["why_not"] = words.too_short(longest, float(plan.get("min_hours", 1)), lang)
            elif reason is not None:
                name, count, total = reason
                example = next(c for s in result.slots for c in s.failed if c.field == name)
                data["why_not"] = f"{_fail_text(example, lang)} {words.hours_count(count, total, lang)}"
            elif not result.slots:
                data["why_not"] = _EMPTY[result.empty_because or "dark"][0 if lang == "pt" else 1]
        if chosen.next_window is not None:
            data["next_window"] = _window_text(chosen.next_window, chosen.today, lang)
    return data


_EMPTY = {
    "weekday": ("não é um dos dias que você escolheu", "it is not one of the days you picked"),
    "past": ("o horário que você pediu já passou", "the hours you asked for have already gone by"),
    "dark": ("não tem luz do dia no horário que você pediu", "there is no daylight in the hours you asked for"),
    "narrow": ("o horário que você pediu não tem uma hora cheia inteira, e a previsão vem de hora em hora",
               "the hours you asked for don't hold a whole clock hour, and the forecast comes hour by hour"),
}


def headline(plan: Plan, chosen: picking.Pick) -> tuple[str, str]:
    lang = words.lang_of(plan.lang)
    day = words.on_day(chosen.day, chosen.today, lang)
    if chosen.go is not None:
        w = chosen.go
        if lang == "pt":
            title = f"Dá praia {day}"
            return title, f"{title}: {w.spot.name}, {words.span(w.start, w.end, lang)}."
        title = f"Go {day}"
        return title, f"{title}: {w.spot.name}, {words.span(w.start, w.end, lang)}."
    title = f"Não dá praia {day}" if lang == "pt" else f"No window {day}"
    return title, title + "."


def template_why(plan: Plan, chosen: picking.Pick) -> str:
    """The code's own sentence, used when there is no model or the model's sentence fails the check."""
    lang = words.lang_of(plan.lang)
    data = facts(plan, chosen)
    if data["verdict"] == "go":
        parts = [what for _, what, _, _ in conditions(plan, chosen.go, lang)[:3]]
        text = ", ".join(parts)
        if lang == "pt":
            text = (text[:1].upper() + text[1:]) if text else "Tudo dentro do que você pediu"
        else:
            text = (text[:1].upper() + text[1:]) if text else "Everything inside what you asked for"
        if "after_the_window" in data:
            text += f"; {data['after_the_window']}"
        return text + "."
    pieces = []
    if "why_not" in data:
        spot = data["already_said"].get("spot", "")
        where = f" em {spot}" if lang == "pt" else f" at {spot}"
        why = data["why_not"]
        spot_matters = spot and len(plan.spots) > 1 and chosen.results and chosen.result.slots
        pieces.append((why[:1].upper() + why[1:]) + (where if spot_matters else "") + ".")
    if chosen.next_window is not None:
        nxt = _window_text(chosen.next_window, chosen.today, lang, preposition=False)
        pieces.append((f"Próxima janela: {nxt}." if lang == "pt" else f"Next window: {nxt}."))
    elif lang == "pt":
        pieces.append("Nenhuma janela nos próximos dias da previsão.")
    else:
        pieces.append("No window in the rest of the forecast.")
    return " ".join(pieces)


# ---------------------------------------------------------------- the model's sentence

SYSTEM = """You write the second half of a one-line daily message about going outside.
The first half is written by code and already says whether to go, the day, the place and the
hours (the "already_said" facts). Don't repeat them.

Your half is one sentence, at most 160 characters, in the language named in "language".
On a "go" day, say why the window works: the tide first if there is one, then the weather and how
much room it leaves. If the facts have "after_the_window", also say what changes after the window;
if they don't, say nothing about what comes after. Say it the way a friend would in one breath,
in your own words: don't copy the facts phrase by phrase, and leave out the limits. On a "no-go" day, say what blocks the day and, if the facts
have one, the next window. Every number and time you write must appear in the facts exactly as
written there. Say only what the facts say: don't call the weather sunny, great or perfect unless
"sky" says clear. Plain words, like a friend texting. No greeting, no emoji, no quotes, no lists.

Example in Portuguese: Maré baixa e vento fraquinho.
Example in English: Low tide and barely any wind."""

_TIME = re.compile(r"\b(\d{1,2})(?::(\d{2})|h(\d{2})?)(?![\d])", re.I)
_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})\b")
_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_UNIT = (r"(km/h|kmh|km|mph|m/s|n[oó]s|knots?|kts?|kn|%|por cento|percent|mm|cm|°c|°f|°|graus|degrees|"
         r"metros?|meters?|metres?|ft|feet|p[eé]s|min(?:utos|utes)?|horas?|hours?|m(?![a-z/]))")
_WITH_UNIT = re.compile(r"(\d+(?:[.,]\d+)?)\s*" + _UNIT, re.I)
_RANGE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:a|to|e|and|-)\s*(\d+(?:[.,]\d+)?)\s*" + _UNIT, re.I)
_SPELLED = re.compile(
    r"\b(?:zero|dois|duas|tres|quatro|cinco|seis|sete|oito|nove|dez|onze|doze|treze|[cq]uatorze|quinze|dezesseis|"
    r"dezessete|dezoito|dezenove|vinte|trinta|quarenta|cinquenta|cem|"
    r"two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
    r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|"
    r"catorze|sessenta|setenta|oitenta|noventa|"
    r"(?:um|uma|meia|meio) (?:hora|metro|grau)|(?:one|an|a) (?:hour|metre|meter|degree)|half (?:an )?hour|"
    r"a couple|um par|uma dupla de horas)\b")
# Which tide a phrase names: the sentence can't call a low tide high.
_TIDE_KIND = {"low": re.compile(r"\bmare baixa\b|\bbaixa-?mar\b|\blow tide\b|\blow water\b"),
              "high": re.compile(r"\bmare (?:cheia|alta)\b|\bpreamar\b|\bhigh tide\b|\bhigh water\b")}
# A capitalised word in the middle of a sentence is a name: a place, most often.
_NAME = re.compile(r"(?<![.!?:]\s)(?<!^)\b([A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ]+)")
_NOT_NAMES = {"I", "UV", "OK"}
_SUNNY = re.compile(r"\bsol(?:zinho|zão|zao|ão)?\b|\bensolarad[oa]\b|\bceu (?:limpo|aberto|azul)\b|\botim[oa]\b|\blind[oa]\b|"
                    r"\b(?:tempo|dia) (?:(?:esta|ta|fica|vai estar) )?(?:otimo|perfeito|lindo)\b|"
                    r"\bsunny\b|\bsunshine\b|\bclear sky\b|\b(?:great|perfect|lovely) (?:weather|day)\b|"
                    r"\bweather (?:is|looks) (?:great|perfect|lovely)\b")


def _flat(data) -> str:
    if isinstance(data, dict):
        return " ".join(_flat(v) for v in data.values())
    if isinstance(data, list):
        return " ".join(_flat(v) for v in data)
    return "" if data is None else str(data)


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in text if not unicodedata.combining(c))


def _unit(raw: str) -> str:
    """One spelling per unit; units the facts never use stay as written, so they can't match."""
    raw = raw.lower()
    if raw in ("°c", "°", "graus", "degrees"):
        return "°C"
    if raw.startswith(("metro", "meter", "metre")) or raw == "m":
        return "m"
    if raw in ("kmh",):
        return "km/h"
    if raw in ("por cento", "percent"):
        return "%"
    if raw.startswith("min"):
        return "min"
    if raw.startswith(("hora", "hour")):
        return "h"
    return raw


def _number(text: str) -> float:
    return float(text.replace(",", "."))


def _with_units(text: str) -> set[tuple[float, str]]:
    """Every number written with a unit, ranges included: "de 3 a 4 km/h" gives 3 and 4 in km/h."""
    pairs = {(_number(n), _unit(u)) for n, u in _WITH_UNIT.findall(text)}
    for a, b, u in _RANGE.findall(text):
        pairs |= {(_number(a), _unit(u)), (_number(b), _unit(u))}
    return pairs


def _tokens(text: str) -> tuple[set, set, list[float]]:
    times = {(int(h), int(m or mm or 0)) for h, m, mm in _TIME.findall(text)}
    dates = {(int(d), int(m)) for d, m in _DATE.findall(text)}
    rest = _DATE.sub(" ", _TIME.sub(" ", text))
    numbers = [float(n.replace(",", ".")) for n in _NUM.findall(rest)]
    return times, dates, numbers


_DAY_WORDS = {
    "pt": ("hoje", "amanha", "segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"),
    "en": ("today", "tomorrow", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"),
}
_DAY_PHRASES = ("depois de amanha", "day after tomorrow")
# Phrases that would turn the verdict around. A list can't catch every way of saying it,
# so this is a guard against the common ones, not a proof.
_NO_GO = re.compile(r"\bnao da\b|\bnao vale\b|\bmelhor (?:nem|nao) ir\b|\bfica(?:r)? em casa\b|"
                    r"\bnao (?:va|vai|ir)\b|\bnem (?:va|vai)\b|\bevite\b|\bperigos[oa]\b|\bno window\b|\bskip\b|"
                    r"\bdon'?t go\b|\bdo not go\b|\bnot worth\b|\bstay (?:in|home|away)\b|\bnot a good\b|"
                    r"\bavoid\b|\bdangerous\b|\bnao iria\b|\bnao recomendo\b|\bwould ?n[o']t go\b|"
                    r"\bnot recommend\b|\bwouldn'?t recommend\b")
_GO = re.compile(r"(?<!nao )\bda praia\b|(?<!nao )\bda (?:pra|para) (?:ir|jogar|correr|nadar)\b|\bpode (?:ir|jogar)\b|"
                 r"\bvale a pena\b|\btempo perfeito\b|\b(?:otimo|bom|perfeito) dia\b|\bde boa\b|"
                 r"\bgo out\b|\bgood window\b|\bgreat day\b|\bperfect day\b|\bgo for it\b|\bworth it\b|"
                 r"\benjoy\b|\bhead out\b|\bcan (?:still )?go\b|\bfine to go\b|\bgood to go\b")


def check_why(sentence: str, data: dict, spots: tuple[str, ...] = ()) -> list[str]:
    """Problems with the model's sentence; an empty list means it can be shown.

    Numbers, clock times, dates, day names and places must come from the facts,
    and a number written with a unit must appear in the facts with that unit.
    The verdict is guarded by a list of phrases that would turn it around.
    """
    problems: list[str] = []
    text = sentence.strip()
    if not text:
        return ["the sentence is empty"]
    if len(text) > MAX_WHY:
        problems.append(f"it has {len(text)} characters; the limit is {MAX_WHY}")
    if "\n" in text:
        problems.append("it has more than one line")
    source = _flat(data)
    times, dates, numbers = _tokens(source)
    hours = {h for h, _ in times}
    s_times, s_dates, s_numbers = _tokens(text)
    for t in sorted(s_times - times):
        problems.append(f"the time {t[0]}:{t[1]:02d} is not in the facts")
    for d in sorted(s_dates - dates):
        problems.append(f"the date {d[0]:02d}/{d[1]:02d} is not in the facts")
    with_units = _with_units(source)
    sentence_units = _with_units(_DATE.sub(" ", _TIME.sub(" ", text)))
    for n, unit in sorted(sentence_units):
        if (n, unit) not in with_units:
            problems.append(f"{n:g} {unit} is not in the facts")
    bound = {n for n, _ in sentence_units}
    for n in s_numbers:
        if n in bound:
            continue
        if n not in numbers and not (n.is_integer() and int(n) in hours):
            problems.append(f"the number {n:g} is not in the facts")
    plain_text, plain_source = _plain(text), _plain(source)
    for word in sorted(set(_SPELLED.findall(plain_text))):
        problems.append(f"\"{word}\" is a number in words; numbers go in digits so they can be checked")
    for phrase in _DAY_PHRASES:
        if phrase in plain_text and phrase not in plain_source:
            problems.append(f"it says \"{phrase}\", which is not a day in the facts")
    for lang_words in _DAY_WORDS.values():
        for word in lang_words:
            if re.search(rf"\b{word}\b", plain_text) and not re.search(rf"\b{word}\b", plain_source):
                problems.append(f"it says \"{word}\", which is not a day in the facts")
    for name in spots:
        if _plain(name) in plain_text and _plain(name) not in plain_source:
            problems.append(f"it names {name}, which the facts don't mention")
    for name in sorted(set(_NAME.findall(text)) - _NOT_NAMES):
        if _plain(name) not in plain_source and not any(_plain(name) == _plain(s) for s in spots):
            problems.append(f"it names {name}, which the facts don't mention")
    sky = _plain(str(data.get("sky", "")))
    unsunny = re.sub(r"\b(?:sem|nada de|without)\s+sol\w*", " ", plain_text)   # "sem sol" is not sunny; "no sol" is
    if _SUNNY.search(unsunny) and not sky.startswith(("ceu limpo", "clear sky")):
        problems.append("it calls the weather sunny or great, and the sky in the facts is not clear")
    for kind, pattern in _TIDE_KIND.items():
        if pattern.search(plain_text) and not pattern.search(plain_source):
            problems.append(f"it mentions a {kind} tide, which the facts don't")
    if data.get("verdict") == "go" and _NO_GO.search(plain_text):
        problems.append("the verdict is go, and the sentence says not to go")
    if data.get("verdict") == "no-go" and _GO.search(plain_text):
        next_day = _plain(str(data.get("next_window", "")))
        names_next = any(re.search(rf"\b{w}\b", plain_text) and re.search(rf"\b{w}\b", next_day)
                         for words_ in _DAY_WORDS.values() for w in words_)
        if not names_next:
            problems.append("the verdict is no-go, and the sentence says to go")
    return problems


def model_why(data: dict, ask, spots: tuple[str, ...] = ()) -> tuple[str | None, list[str]]:
    """Ask the model for the sentence, at most twice. ``ask(system, user) -> str``."""
    import json
    user = json.dumps(data, ensure_ascii=False, indent=1)
    sentence = ask(SYSTEM, user).strip().strip('"').strip()
    problems = check_why(sentence, data, spots)
    if not problems:
        return sentence, []
    retry = (user + "\n\nYour last sentence was:\n" + sentence + "\n\nIt has these problems:\n- "
             + "\n- ".join(problems) + "\nWrite it again, using only the facts.")
    sentence = ask(SYSTEM, retry).strip().strip('"').strip()
    problems = check_why(sentence, data, spots)
    return (sentence, []) if not problems else (None, problems)


def compose(plan: Plan, chosen: picking.Pick, ask=None) -> Line:
    title, head = headline(plan, chosen)
    if ask is None:
        return Line(title, head, template_why(plan, chosen))
    data = facts(plan, chosen)
    sentence, problems = model_why(data, ask, tuple(s.name for s in plan.spots))
    if sentence is None:
        return Line(title, head, template_why(plan, chosen), by_model=False, model_problems=problems)
    return Line(title, head, sentence, by_model=True)
