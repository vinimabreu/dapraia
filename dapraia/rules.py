"""The plan: what you said, turned into rules a forecast can be checked against.

Every rule carries the exact words it came from. A rule is kept only if those
words are in what you wrote, its value is in range, and any number in those
words is the number the rule uses. Every number you wrote has to end up in
some rule, or in the list of things the plan could not measure.
"""

from __future__ import annotations

import json
import math
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .forecast import Spot


@dataclass(frozen=True)
class Field:
    name: str
    kind: str          # "max", "min", "hours", "length", "weekdays", "tide", "tide_hours", "daylight"
    lo: float
    hi: float
    unit: str
    attr: str = ""     # the Hour attribute a max/min rule reads


FIELDS: dict[str, Field] = {f.name: f for f in (
    Field("earliest_hour", "hours", 0, 23.5, "h"),
    Field("latest_hour", "hours", 0.5, 24, "h"),
    Field("min_hours", "length", 1, 12, "h"),
    Field("weekdays", "weekdays", 0, 6, ""),
    Field("daylight", "daylight", 0, 1, ""),
    Field("wind_max_kmh", "max", 0, 100, "km/h", "wind_kmh"),
    Field("wind_min_kmh", "min", 0, 100, "km/h", "wind_kmh"),
    Field("gust_max_kmh", "max", 0, 150, "km/h", "gust_kmh"),
    Field("rain_chance_max", "max", 0, 100, "%", "rain_chance"),
    Field("rain_mm_max", "max", 0, 50, "mm", "rain_mm"),
    Field("temp_min_c", "min", -30, 50, "°C", "temp_c"),
    Field("temp_max_c", "max", -30, 50, "°C", "temp_c"),
    Field("uv_max", "max", 0, 15, "", "uv"),
    Field("cloud_max", "max", 0, 100, "%", "cloud"),
    Field("wave_max_m", "max", 0, 15, "m", "wave_m"),
    Field("wave_min_m", "min", 0, 15, "m", "wave_m"),
    Field("water_min_c", "min", 0, 40, "°C", "water_c"),
    Field("tide", "tide", 0, 0, ""),
    Field("tide_hours", "tide_hours", 0.5, 6, "h"),
)}

MARINE_FIELDS = {"wave_max_m", "wave_min_m", "water_min_c", "tide", "tide_hours"}

# Pairs that would leave no hour at all if the low end sat above the high end.
_PAIRS = (("earliest_hour", "latest_hour"), ("wind_min_kmh", "wind_max_kmh"),
          ("temp_min_c", "temp_max_c"), ("wave_min_m", "wave_max_m"))


@dataclass
class Rule:
    field: str
    value: object
    quote: str

    def as_dict(self) -> dict:
        return {"field": self.field, "value": self.value, "quote": self.quote}


@dataclass
class Plan:
    text: str
    lang: str = "en"
    activity: str = ""
    activity_quote: str = ""
    spots: list[Spot] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    tide_shift_min: int = 0   # minutes added to the model's tide times, measured against a local table

    def get(self, name: str, default=None):
        for rule in self.rules:
            if rule.field == name:
                return rule.value
        return default

    def rule(self, name: str) -> Rule | None:
        return next((r for r in self.rules if r.field == name), None)

    @property
    def needs_marine(self) -> bool:
        return any(r.field in MARINE_FIELDS for r in self.rules)

    def as_dict(self) -> dict:
        return {
            "version": 1, "lang": self.lang, "text": self.text,
            "activity": self.activity, "activity_quote": self.activity_quote,
            "spots": [s.as_dict() for s in self.spots],
            "rules": [r.as_dict() for r in self.rules],
            "unmapped": self.unmapped, "history": self.history, "tide_shift_min": self.tide_shift_min,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Plan:
        return cls(
            text=data.get("text", ""), lang=data.get("lang", "en"),
            activity=data.get("activity", ""), activity_quote=data.get("activity_quote", ""),
            spots=[Spot(s["name"], float(s["lat"]), float(s["lon"])) for s in data.get("spots", [])],
            rules=[Rule(r["field"], r["value"], r.get("quote", "")) for r in data.get("rules", [])],
            unmapped=list(data.get("unmapped", [])), history=list(data.get("history", [])),
            tide_shift_min=int(data.get("tide_shift_min", 0)),
        )


def default_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "dapraia" / "plan.json"


def save(plan: Plan, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan.as_dict(), ensure_ascii=False, indent=2) + "\n")


class PlanError(ValueError):
    """The plan file can't be read, or a value in it is not one this tool can use."""


def load(path: Path) -> Plan:
    """Read the plan, and say in one line what is wrong with it if it was edited by hand."""
    try:
        data = json.loads(path.read_text())
    except ValueError as error:
        raise PlanError(f"{path} is not valid JSON ({error})") from None
    except IsADirectoryError:
        raise PlanError(f"{path} is a folder, not a plan file") from None
    try:
        plan = Plan.from_dict(data)
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise PlanError(f"{path} is missing or mistypes something ({error!r})") from None
    for rule in plan.rules:
        problem = check_value(rule.field, rule.value)
        if problem:
            raise PlanError(f"{path}: {problem}")
    _, conflicts = check_pairs(plan.rules)
    if conflicts:
        raise PlanError(f"{path}: {conflicts[0]}")
    if not -180 <= plan.tide_shift_min <= 180:
        raise PlanError(f"{path}: tide_shift_min must be between -180 and 180")
    return plan


# ---------------------------------------------------------------- checking

MAX_QUOTE_WORDS = 8


def norm(text: str) -> str:
    """Lower case, no accents, punctuation turned into spaces (decimal commas and points kept)."""
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"(?<=\d)[.,](?=\d)", "\x00", text)
    text = re.sub(r"[^\w%\x00]+", " ", text)
    return " ".join(text.replace("\x00", ".").split())


# A sentence ends at ! ? ; a line break, or a full stop that is not a decimal mark.
_SENTENCE = re.compile(r"[!?;\n]|(?<!\b[aApP])(?<!\b[aApP]\.[mM])\.(?!\d)")
# A clause also ends at a comma that is not a decimal mark.
_CLAUSE = re.compile(r"[!?;\n]|,(?!\d)|(?<!\b[aApP])(?<!\b[aApP]\.[mM])\.(?!\d)")


def quoted(quote: str, text: str) -> bool:
    """True when the quote, word for word, is inside one sentence of the text."""
    q = norm(quote)
    return bool(q) and any(f" {q} " in f" {norm(part)} " for part in _SENTENCE.split(text))


def clause_of(quote: str, text: str) -> str:
    """The clause of the text the quote sits in, so "entre 15" in "vento entre 15 e 25 nós" reads as knots."""
    q = norm(quote)
    for part in _CLAUSE.split(text):
        if q and f" {q} " in f" {norm(part)} ":
            return part
    return quote


_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_CLOCK = re.compile(r"\b(\d{1,2})\s*(?:h|:)\s*(\d{2})(?!\d)")
_LATE = re.compile(r"tard\w*|noit\w*|pm\b|afternoon|evening|night", re.I)
# The unit written right after a number, or after the second number of a range ("15 e 25 nós").
# "nó"/"nós" only with the accent: without it, "até 12 no fim de semana" would be read as knots.
_UNIT_AFTER = re.compile(
    r"^(?:\s*(?:e|a|to|and|-|até)\s*\d+(?:[.,]\d+)?)?\s*(?:de\s+)?"
    r"(km/h|kmh|km por hora|mph|m/s|nós|nó|kn|kts|kt|knots?|ft|feet|foot|pés|[°º]\s*f|fahrenheit|f(?![a-zà-ÿ])|"
    r"[°º]\s*c|[°º]|graus|degrees|metros?|meters?|metres?|cm|mm|m(?![a-zà-ÿ/])|%|por cento|percent|"
    r"minutos|minutes|min|horas?|hours?)", re.I)
_UNIT_KIND = {
    "km/h": ("speed", 1.0), "kmh": ("speed", 1.0), "km por hora": ("speed", 1.0), "mph": ("speed", 1.609344),
    "m/s": ("speed", 3.6), "nós": ("speed", 1.852), "nó": ("speed", 1.852), "kn": ("speed", 1.852),
    "kts": ("speed", 1.852), "kt": ("speed", 1.852), "knot": ("speed", 1.852), "knots": ("speed", 1.852),
    "ft": ("length", 0.3048), "feet": ("length", 0.3048), "foot": ("length", 0.3048), "pés": ("length", 0.3048),
    "metro": ("length", 1.0), "metros": ("length", 1.0), "meter": ("length", 1.0), "meters": ("length", 1.0),
    "metre": ("length", 1.0), "metres": ("length", 1.0), "m": ("length", 1.0), "cm": ("length", 0.01),
    "°c": ("temp", None), "°": ("temp", None), "graus": ("temp", None), "degrees": ("temp", None),
    "°f": ("temp", "F"), "f": ("temp", "F"), "fahrenheit": ("temp", "F"),
    "%": ("percent", 1.0), "por cento": ("percent", 1.0), "percent": ("percent", 1.0), "mm": ("rain", 1.0),
    "min": ("time", 1 / 60), "minutos": ("time", 1 / 60), "minutes": ("time", 1 / 60),
    "hora": ("time", 1.0), "horas": ("time", 1.0), "hour": ("time", 1.0), "hours": ("time", 1.0),
}
_FIELD_KIND = {"km/h": "speed", "m": "length", "°C": "temp", "%": "percent", "mm": "rain", "h": "time", "": "none"}


def _nfc(text: str) -> str:
    """Text pasted on a Mac can carry accents as separate marks; compare in one form."""
    return unicodedata.normalize("NFC", text)


def unit_after(raw: str, clause: str, quote: str = "") -> str | None:
    """The unit written after this number in the clause, lower case, or None."""
    clause = _nfc(clause)
    end = _end_in(raw, clause, _nfc(quote))
    if end is None:
        return None
    m = _UNIT_AFTER.match(clause[end:])
    if m is None:
        return None
    unit = m.group(1).lower().replace("º", "°")
    return re.sub(r"\s+", "", unit) if unit.startswith("°") else unit


@dataclass(frozen=True)
class Token:
    """A number as written: ``12``, ``1,5``, or a clock time like ``7h30`` (number 7, minutes 30)."""
    raw: str
    number: float
    minutes: int | None = None


# "7 e meia" is a time on its own; "7 e 30" only after a time word or with an "h" ("7h e 30"), and never
# with a unit after it: "entre 10 e 15 km/h" is a range of speeds, not 10:15.
_HALF = re.compile(r"\b(\d{1,2})\s*h?\s+e\s+(meia|quinze|quarenta e cinco)\b", re.I)
_UNIT_NEXT = r"(?!\s*(?:km|kmh|mph|m/s|nós\b|kn\b|kts?\b|knots?|graus|degrees|[°º]|%|mm\b|cm\b|m\b|metros?|ft\b|feet|pés|f\b))"
_HALF_DIGITS = re.compile(
    r"\b(?:(?:às|as|das|até as|ate as|a partir das|depois das|antes das|from|at|until|after|before|by)\s+(\d{1,2})\s*h?"
    r"|(\d{1,2})\s*h)\s+e\s+(15|30|45)\b" + _UNIT_NEXT, re.I)
_HALF_PAST = re.compile(r"\b(half|quarter) past (\d{1,2})\b", re.I)
_AND_A_HALF = re.compile(r"\b(\d+)\s+(metros?|horas?|hours?)\s+e\s+mei[oa]\b", re.I)


def tokens(text: str) -> list[Token]:
    """Numbers as written, a clock time or a "number and a half" counting as one:
    "7h30", "7 e meia" and "half past 7" are 7 with 30 minutes; "1 metro e meio" is 1.5."""
    found: list[Token] = []
    rest = text

    def take(pattern, make):
        nonlocal rest
        for m in pattern.finditer(rest):
            found.append(make(m))
        rest = pattern.sub(lambda m: " " * len(m.group(0)), rest)

    take(_CLOCK, lambda m: Token(m.group(0), float(m.group(1)), int(m.group(2))))
    take(_HALF, lambda m: Token(m.group(0), float(m.group(1)),
                                {"meia": 30, "quinze": 15}.get(m.group(2).lower(), 45)))
    take(_HALF_DIGITS, lambda m: Token(m.group(0)[(m.start(1) if m.group(1) else m.start(2)) - m.start():],
                                       float(m.group(1) or m.group(2)), int(m.group(3))))
    take(_HALF_PAST, lambda m: Token(m.group(0), float(m.group(2)), 30 if m.group(1).lower() == "half" else 15))
    take(_AND_A_HALF, lambda m: Token(m.group(0), float(m.group(1)) + 0.5))
    found += [Token(m.group(0), float(m.group(0).replace(",", "."))) for m in _NUMBER.finditer(rest)]
    return found


def numbers_in(text: str) -> list[float]:
    return [float(n.replace(",", ".")) for n in _NUMBER.findall(text)]


def measurable_number(phrase: str, text: str) -> str | None:
    """A number in the phrase written with a unit some field measures ("12 km/h"), or None."""
    clause = clause_of(phrase, text)
    for token in tokens(phrase):
        unit = unit_after(token.raw, clause, phrase)
        if unit is not None and _UNIT_KIND.get(unit, (None,))[0] is not None:
            return f"{token.raw} {unit}"
    return None


class UnitMismatch(ValueError):
    """The number is written in a unit of another kind (20% for a wind limit)."""


def _convert(name: str, n: float, unit: str | None) -> tuple[float, float]:
    """(value in the field's unit, tolerance): exact when no unit or the field's own unit is
    written, rounded after converting from another unit of the same kind."""
    if unit is None:
        return n, 0.01
    kind, factor = _UNIT_KIND.get(unit, (None, None))
    wanted = _FIELD_KIND.get(FIELDS[name].unit, "none")
    if kind is None:
        return n, 0.01
    if kind != wanted:
        raise UnitMismatch(unit)
    if factor == "F":
        return (n - 32) * 5 / 9, 1.0
    if factor in (None, 1.0):
        return n, 0.01
    return n * factor, (1.0 if kind == "speed" else 0.05)


def values_for(name: str, token: Token, quote: str, context: str) -> list[tuple[float, float]]:
    """What one written number can stand for in one field, as (value, tolerance) pairs.

    "a partir das 7h30" can start at 7:30 or at the 8:00 hour, never at 7:00;
    "antes das 9h30" can end at 9:30 or at 9:00, never at 10:00.
    """
    kind = FIELDS[name].kind
    if kind in ("length", "tide_hours") and token.minutes is None:
        unit = unit_after(token.raw, context or quote, quote)
        if unit in ("min", "minutos", "minutes"):
            hours = token.number / 60
            # the forecast is hourly, so "40 minutos" of play needs a whole hour of window
            return [(hours, 0.01)] + ([(float(math.ceil(hours - 1e-9)), 0.01)] if kind == "length" else [])
    if kind in ("hours", "length", "tide_hours"):
        values = [token.number + (token.minutes or 0) / 60]
        if kind == "hours" and token.minutes:
            values.append(token.number + 1 if name == "earliest_hour" else token.number)
        if kind == "hours":
            part = _part_of_day(token, context or quote, quote)
            if part == "midnight":
                minutes = (token.minutes or 0) / 60
                values = [minutes] + ([24.0] if not minutes else [])
            elif part == "late":
                values = [v + 12 if v < 12 else v for v in values]
            elif part is None and _LATE.search(context or quote):
                values += [v + 12 for v in values if v < 12]
        return [(v, 0.01) for v in values]
    return [_convert(name, token.number, unit_after(token.raw, context, quote))]


_PART_AFTER = re.compile(r"^\s*(?:min\b|hs?\b|hrs?\b|horas?\b)?\s*(pm\b|p\.m\.|d[ae] tard\w*|d[ae] noit\w*|[àa] tard\w*|[àa] noit\w*|"
                         r"of the afternoon|in the (?:afternoon|evening)|at night|am\b|a\.m\.|d[ae] manh[ãa]|"
                         r"d[ae] madrugada|in the morning)", re.I)


def _find(raw: str, text: str) -> list[int]:
    """Where ``raw`` is written in ``text`` as a whole number: "4" is not found inside "14"."""
    return [m.end() for m in re.finditer(rf"(?<![\d.,]){re.escape(raw)}(?![\d])", text)]


def _end_in(raw: str, clause: str, quote: str) -> int | None:
    """Where this written number ends in the clause, preferring the occurrence inside the quote:
    in "das 6 da manhã às 6 da tarde", the quote "às 6 da tarde" means the second 6."""
    ends = _find(raw, clause)
    if not ends:
        return None
    # compare without accents or case, one character for one, so positions still line up with the clause
    fold = lambda text: "".join(unicodedata.normalize("NFD", ch)[0].lower() for ch in text)
    core = fold(quote.strip(" .,;:!?\"'")) if quote else ""
    found = re.search(rf"(?<!\w){re.escape(core)}(?!\w)", fold(clause)) if core else None
    start, quote = (found.start(), core) if found else (-1, quote)
    inside = [end for end in ends if start >= 0 and start < end <= start + len(quote)]
    return (inside or ends)[0]


def _part_of_day(token: Token, clause: str, quote: str = "") -> str | None:
    """"late" when "da tarde"/"pm" comes right after the number, "midnight" for "12 da noite" or
    "12 am", "early" for "da manhã"/"am", else None."""
    end = _end_in(token.raw, clause, quote)
    if end is None:
        return None
    m = _PART_AFTER.match(clause[end:])
    if m is None:
        return None
    said = m.group(1).lower()
    if token.number == 12 and said.startswith(("am", "a.m")):
        return "midnight"
    if said.startswith(("am", "a.m", "da manh", "de manh", "da madrugada", "de madrugada", "in the morning")):
        return "early"
    if token.number == 12 and ("noite" in said or said == "at night"):
        return "midnight"
    return "late"


def allowed_values(name: str, quote: str, text: str = "") -> list[tuple[float, float]] | None:
    """Values the numbers written in the quote can stand for, or None when the quote has no number.

    The unit is the one written after the number, or after the end of its range: "15" in
    "entre 15 e 25 nós" is 15 knots, so only 28 km/h is allowed, never 15. A unit of
    another kind raises UnitMismatch.
    """
    found = tokens(quote)
    if not found:
        return None
    context = clause_of(quote, text) if text else quote
    return [pair for token in found for pair in values_for(name, token, quote, context)]


def _matches(value: float, allowed: list[tuple[float, float]]) -> bool:
    return any(abs(value - a) <= tolerance + 1e-9 for a, tolerance in allowed)


# Words that say which way a number points. Longer phrases win over the shorter ones inside them,
# so "not more than 5" is a maximum even though "more than 5" is a minimum. This is a list, so it
# can be wrong both ways: it only ever produces a warning for you, never a rejection for the model.
_UP = ("acima de", "acima dos", "acima das", "mais de", "mais que", "mais do que", "pelo menos", "no minimo",
       "a partir de", "a partir das", "a partir da", "depois de", "depois das", "depois da", "apos", "desde",
       "minimo de", "superior a", "maior que", "maior do que", "nao menos que", "nao menos de",
       "nao abaixo de", "nunca abaixo de", "nunca menos de", "above", "over", "more than", "at least", "from",
       "after", "not less than", "no less than", "not colder than", "not below", "never below", "not under",
       "never under", "no lower than", "not lower than", "minimum", "min")
_DOWN = ("ate", "abaixo de", "abaixo dos", "abaixo das", "menos de", "menos que", "menos do que", "no maximo",
         "antes de", "antes das", "antes da", "maximo de", "inferior a", "menor que", "menor do que",
         "nao mais que", "nao mais de", "nao passar de", "sem passar de", "nunca passar de", "nao acima de",
         "nunca acima de", "nunca mais de", "under", "below", "up to", "less than", "at most", "before",
         "until", "till", "by", "no more than", "not more than", "not hotter than", "not above", "never above",
         "not over", "never over", "no higher than", "not higher than", "maximum", "max")
_PHRASES = sorted([(p, "up") for p in _UP] + [(p, "down") for p in _DOWN], key=lambda item: -len(item[0]))
_NEGATORS = {"nao", "nunca", "jamais", "nem", "nada", "sem", "evito", "evitar", "evite", "evitando",
             "avoid", "avoiding", "never", "no", "not", "without"}
_AFTER_UP = re.compile(r"^(?:\S+\s+){0,3}?(?:ou mais|pra cima|para cima|or more|and up|or above|or higher)\b")
_AFTER_DOWN = re.compile(r"^(?:\S+\s+){0,3}?(?:ou menos|pra baixo|para baixo|or less|or below|or lower)\b")
_NUMBER_WORD = re.compile(r"^(\d+(?:\.\d+)?)(?:h\d{0,2}|[a-z%°]{0,3})$")


def _way_before(before: str) -> str | None:
    for phrase, way in _PHRASES:
        m = re.search(rf"(?:^|\s)({phrase})(?:\s+(?:as|os|o|a|the|de|das|da|dos|do))?$", before)
        if m:
            head = before[:m.start(1)].split()[-3:]
            if phrase.split()[0] not in _NEGATORS and any(w in _NEGATORS for w in head):
                way = "up" if way == "down" else "down"
            return way
    return None


def number_directions(clause: str) -> list[tuple[float, str | None]]:
    """Each number in the clause with the way the words around it point, if they say."""
    words = norm(clause).split()
    found = []
    for i, word in enumerate(words):
        m = _NUMBER_WORD.match(word)
        if not m:
            continue
        way = _way_before(" ".join(words[max(0, i - 6):i]))
        after = " ".join(words[i + 1:i + 6])
        if way is None and _AFTER_UP.search(after):
            way = "up"
        elif way is None and _AFTER_DOWN.search(after):
            way = "down"
        found.append((float(m.group(1)), way))
    return found


def directions(quote: str) -> set[str]:
    """The ways the numbers in a quote point: ``{"up"}`` for "acima de 20", ``{"down"}`` for "até 20"."""
    return {way for _, way in number_directions(quote) if way}


def _wanted(name: str) -> str | None:
    kind = FIELDS[name].kind
    if kind == "max" or name == "latest_hour":
        return "down"
    if kind in ("min", "length") or name == "earliest_hour":
        return "up"
    return None


def direction_warnings(text: str, rules: list[Rule]) -> list[Rule]:
    """Rules whose own number reads, by the list above, as pointing the other way. Shown to you
    before saving ("acima de 30 km/h" saved as a maximum is worth a second look)."""
    text = _nfc(text)
    flagged = []
    for rule in rules:
        wanted = _wanted(rule.field)
        if wanted is None or isinstance(rule.value, (bool, list, str)):
            continue
        clause = clause_of(rule.quote, text)
        mine = set()
        for token in tokens(rule.quote):
            try:
                if _matches(float(rule.value), values_for(rule.field, token, rule.quote, clause)):
                    mine.add(token.number)
            except UnitMismatch:
                continue
        ways = {way for number, way in number_directions(rule.quote if not clause else clause)
                if number in mine and way}
        words_way = _word_way(rule.quote)
        if (ways and wanted not in ways) or (not ways and words_way and words_way != wanted):
            flagged.append(rule)
    return flagged


_SOFT = re.compile(r"\b(?:pouc[oa]|fraqu?[oa]?|fraquinh[oa]|leve|calm[oa]|light|little|weak|calm|gentle)\b")
_STRONG = re.compile(r"\b(?:fortes?|muit[oa]|bastante|bom|boas?|grandes?|strong|big|good|plenty)\b")


def _word_way(quote: str) -> str | None:
    """"pouco vento" reads as a maximum, "vento forte" as a minimum, when no number says otherwise."""
    text = norm(quote)
    soft, strong = bool(_SOFT.search(text)), bool(_STRONG.search(text))
    if soft == strong:
        return None
    way = "down" if soft else "up"
    if any(word in _NEGATORS for word in text.split()):     # "evito vento forte" is a maximum
        way = "up" if way == "down" else "down"
    return way


def check_value(name: str, value: object) -> str | None:
    """The value alone: right type and in range. None when it is fine."""
    if name not in FIELDS:
        return f"{name or 'a rule'} is not a field this tool knows"
    spec = FIELDS[name]
    if spec.kind == "tide":
        return None if value in ("low", "high") else f"tide must be \"low\" or \"high\", not {value!r}"
    if spec.kind == "daylight":
        return None if isinstance(value, bool) else f"daylight must be true or false, not {value!r}"
    if spec.kind == "weekdays":
        if not isinstance(value, list) or not value or not all(
                isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in value):
            return f"weekdays must be a list of day numbers 0 (Monday) to 6 (Sunday), not {value!r}"
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return f"{name} needs a number, not {value!r}"
    if not spec.lo <= float(value) <= spec.hi:
        return f"{name} = {float(value):g} is outside {spec.lo:g} to {spec.hi:g}"
    return None


def check_rule(item: dict, text: str, *, match_numbers: bool = True) -> tuple[Rule | None, str | None]:
    """One rule proposed by the model: (Rule, None) when it holds up, (None, problem) when it doesn't.

    ``match_numbers=False`` skips only the test that a number in the quote is the
    rule's value; it is for what you say after an outing, where "at 9 the tide
    was already up" is an observation, not a limit.
    """
    name = str(item.get("field", "")).strip()
    quote = _nfc(str(item.get("quote", "")).strip())
    text = _nfc(text)
    value = item.get("value")
    if name not in FIELDS:
        return None, f"{name or 'a rule'} is not a field this tool knows"
    if not quote:
        return None, f"{name} has no quote; every rule needs the words it came from"
    if len(quote.split()) > MAX_QUOTE_WORDS:
        return None, f"{name}: the quote \"{quote}\" has {len(quote.split())} words; quote at most {MAX_QUOTE_WORDS}"
    if not quoted(quote, text):
        return None, f"{name}: the quote \"{quote}\" is not in the text, word for word, inside one sentence"
    problem = check_value(name, value)
    if problem:
        return None, problem
    spec = FIELDS[name]
    if spec.kind == "weekdays":
        return Rule(name, sorted(set(value)), quote), None
    if spec.kind in ("tide", "daylight"):
        return Rule(name, value, quote), None
    value = float(value)
    try:
        allowed = allowed_values(name, quote, text) if match_numbers else None
    except UnitMismatch as mismatch:
        return None, f"{name} is in {spec.unit or 'plain numbers'}, but \"{quote}\" gives {mismatch.args[0]}"
    if allowed is not None and not _matches(value, allowed):
        digits = 1 if spec.unit == "m" else 0 if spec.unit in ("km/h", "°C") else 2
        shown = " or ".join(f"{round(a, digits):g}" for a in sorted({a for a, _ in allowed}))
        if any(tolerance > 0.01 for _, tolerance in allowed) and spec.kind not in ("hours", "length", "tide_hours"):
            return None, (f"{name} = {value:g}, but the text gives \"{quote}\" in another unit; "
                          f"in {spec.unit} that is {shown}")
        return None, f"{name} = {value:g}, but the quote \"{quote}\" says {shown}"
    return Rule(name, int(value) if value.is_integer() else value, quote), None


def check_rules(items: list, text: str, unmapped: list | None = None,
                extra_quotes: list[str] | None = None) -> tuple[list[Rule], list[str]]:
    """Keep the rules that hold up. Problems name every rule dropped and every number left unused."""
    text = _nfc(text)
    rules: list[Rule] = []
    problems: list[str] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            problems.append(f"{item!r} is not a rule")
            continue
        rule, problem = check_rule(item, text)
        if problem:
            problems.append(problem)
        elif any(r.field == rule.field for r in rules):
            problems.append(f"{rule.field} appears twice; keep one")
        else:
            rules.append(rule)
    rules, pair_problems = check_pairs(rules)
    problems += pair_problems
    unused = unused_numbers(text, rules, list(unmapped or []) + list(extra_quotes or []))
    problems += unused
    if unused:
        rules, dropped = _drop_word_rules_beside_unused_numbers(text, rules, list(unmapped or []) + list(extra_quotes or []))
        problems += dropped
    return rules, problems


def _drop_word_rules_beside_unused_numbers(text: str, rules: list[Rule], phrases: list) -> tuple[list[Rule], list[str]]:
    """A rule that quotes only words ("vento") from a clause whose number nobody used
    ("vento até 12 km/h") picked its own value over the one you wrote, so it goes."""
    left = _unused_tokens(text, rules, phrases)
    kept, dropped = [], []
    for rule in rules:
        cut = next((t for t in left if t.minutes is not None or " " in t.raw.strip()
                    if _cuts(rule.quote, t.raw, text)), None)
        if cut is not None:
            dropped.append(f"{rule.field} was left out: its quote \"{rule.quote}\" cuts \"{cut.raw}\" short")
            continue
        clause = clause_of(rule.quote, text)
        sentence = next((part for part in _SENTENCE.split(text) if f" {norm(rule.quote)} " in f" {norm(part)} "), clause)
        if not tokens(rule.quote) and any(_belongs(rule, t, clause if t.raw in clause else sentence)
                                          for t in left if t.raw in sentence):
            dropped.append(f"{rule.field} was left out: it quotes \"{rule.quote}\" without the number "
                           f"written next to it in \"{clause.strip()}\"")
        else:
            kept.append(rule)
    return kept, dropped


def _cuts(quote: str, raw: str, text: str) -> bool:
    """True when the quote ends partway into a written time or amount: "a partir das 7" in
    "a partir das 7 e meia"."""
    low, q, r = text.lower(), quote.lower().strip(), raw.lower().strip()
    for m in re.finditer(re.escape(r), low):
        q_at = low.find(q)
        while q_at >= 0:
            q_end = q_at + len(q)
            if q_at <= m.start() < q_end < m.end():
                return True
            q_at = low.find(q, q_at + 1)
    return False


def _belongs(rule: Rule, token: Token, clause: str) -> bool:
    """Would this unused number have been the rule's own? Yes when it carries a unit of the
    rule's kind ("12 km/h" for wind) or comes within three words after the quote ("vento até 12")."""
    try:
        unit = unit_after(token.raw, clause)
        if unit is not None:
            _convert(rule.field, token.number, unit)
            return _UNIT_KIND.get(unit, (None,))[0] is not None
    except UnitMismatch:
        return False
    if token.raw not in norm(clause) and token.raw not in clause:
        return False
    words = norm(clause).split()
    quote = norm(rule.quote).split()
    for i in range(len(words) - len(quote) + 1):
        if words[i:i + len(quote)] == quote:
            following = words[i + len(quote):i + len(quote) + 4]
            for j, w in enumerate(following[:3]):
                m = _NUMBER_WORD.match(w)
                if m and float(m.group(1)) == token.number:
                    after = following[j + 1] if j + 1 < len(following) else ""
                    return not after.isalpha()        # "uns 40 minutos" is not the rain rule's number
            return False
    return False


def check_pairs(rules: list[Rule]) -> tuple[list[Rule], list[str]]:
    """Drop rules that contradict each other, such as a minimum above its maximum."""
    by_name = {r.field: r for r in rules}
    problems = []
    for low, high in _PAIRS:
        if low in by_name and high in by_name and float(by_name[low].value) >= float(by_name[high].value):
            problems.append(f"{low} ({by_name[low].value}) must be below {high} ({by_name[high].value})")
            rules = [r for r in rules if r.field not in (low, high)]
    if ("gust_max_kmh" in by_name and "wind_max_kmh" in by_name
            and float(by_name["gust_max_kmh"].value) < float(by_name["wind_max_kmh"].value)):
        problems.append(f"gust_max_kmh ({by_name['gust_max_kmh'].value}) is below wind_max_kmh "
                        f"({by_name['wind_max_kmh'].value}); gusts are always at least the wind")
        rules = [r for r in rules if r.field != "gust_max_kmh"]
    if "tide_hours" in by_name and "tide" not in by_name:
        problems.append("tide_hours needs a tide rule (low or high) to go with it")
        rules = [r for r in rules if r.field != "tide_hours"]
    return rules, problems


def _key(token: Token) -> tuple[float, int | None]:
    return token.number, token.minutes


def unused_numbers(text: str, rules: list[Rule], phrases: list) -> list[str]:
    """Every number written in the text must be the value of some rule that quotes it, or sit
    in a phrase the plan can't measure (or the activity). A number that only appears inside
    a quote, while the rule uses another one, counts as unused."""
    return [] if not text else _report(text, rules, phrases)


def _used_keys(text: str, rules: list[Rule], phrases: list) -> list[tuple[float, int | None]]:
    used: list[tuple[float, int | None]] = []
    for rule in rules:
        spec = FIELDS[rule.field]
        found = tokens(rule.quote)
        if spec.kind in ("tide", "daylight", "weekdays"):
            used += [_key(t) for t in found]
            continue
        context = clause_of(rule.quote, text)
        used += [_key(t) for t in found
                 if _matches(float(rule.value), values_for(rule.field, t, rule.quote, context))]
    for phrase in phrases:
        used += [_key(t) for t in tokens(str(phrase))]
    return used


def _report(text: str, rules: list[Rule], phrases: list) -> list[str]:
    used = _used_keys(text, rules, phrases)
    missing = []
    for token in _unused_tokens(text, rules, phrases, used):
        if token.minutes is not None:
            missing.append(f"the time {token.raw} in the text is not used by any rule (quote it whole: \"{token.raw}\")")
        else:
            missing.append(f"the number {token.number:g} in the text is not used by any rule")
    return missing


def _unused_tokens(text: str, rules: list[Rule], phrases: list, used: list | None = None) -> list[Token]:
    if used is None:
        used = _used_keys(text, rules, phrases)
    used = list(used)
    left = []
    for token in tokens(text):
        if _key(token) in used:
            used.remove(_key(token))
        else:
            left.append(token)
    return left
