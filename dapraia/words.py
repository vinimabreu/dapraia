"""How numbers, times, days and conditions are written, in Portuguese (Brazil) and English."""

from __future__ import annotations

from datetime import date, datetime

WEEKDAYS = {
    "pt": ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"),
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
}
SHORT = {
    "pt": ("seg", "ter", "qua", "qui", "sex", "sáb", "dom"),
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
}


def lang_of(code: str) -> str:
    return "pt" if str(code).lower().startswith("pt") else "en"


def duration(hours: float, lang: str) -> str:
    """``1h``, ``1h30``, ``30 min`` in Portuguese; ``1 h``, ``1.5 h`` in English."""
    hours = float(hours)
    if lang == "pt":
        whole, minutes = int(hours), round((hours - int(hours)) * 60)
        if not whole:
            return f"{minutes} min"
        return f"{whole}h{minutes:02d}" if minutes else f"{whole}h"
    return f"{hours:g} h"


def num(value: float, lang: str, decimals: int = 0) -> str:
    text = f"{value:.{decimals}f}"
    if decimals and text.endswith("0" * decimals):
        text = text[: -(decimals + 1)]
    if text in ("-0",):
        text = "0"
    return text.replace(".", ",") if lang == "pt" else text


def clock(moment: datetime, lang: str) -> str:
    if lang == "pt":
        return f"{moment.hour}h" if moment.minute == 0 else f"{moment.hour}h{moment.minute:02d}"
    return f"{moment.hour:02d}:{moment.minute:02d}"


def span(start: datetime, end: datetime, lang: str) -> str:
    if lang == "pt":
        return f"das {clock(start, lang)} às {clock(end, lang)}"
    return f"{clock(start, lang)} to {clock(end, lang)}"


def day_name(day: date, today: date, lang: str) -> str:
    """``amanhã (qua 07/10)``, ``sábado (10/10)``, ``tomorrow (Wed 07/10)``."""
    stamp = f"{day.day:02d}/{day.month:02d}"
    delta = (day - today).days
    if delta == 0:
        return f"{'hoje' if lang == 'pt' else 'today'} ({SHORT[lang][day.weekday()]} {stamp})"
    if delta == 1:
        return f"{'amanhã' if lang == 'pt' else 'tomorrow'} ({SHORT[lang][day.weekday()]} {stamp})"
    return f"{WEEKDAYS[lang][day.weekday()]} ({stamp})"


def on_day(day: date, today: date, lang: str) -> str:
    """The day as it reads after a verb: ``amanhã (qua 07/10)``, ``na quinta (08/10)``, ``on Thursday (08/10)``."""
    name = day_name(day, today, lang)
    if (day - today).days in (0, 1):
        return name
    if lang == "pt":
        return ("no " if day.weekday() in (5, 6) else "na ") + name
    return "on " + name


def _range(values: list[float], lang: str, decimals: int, unit: str) -> str:
    low, high = min(values), max(values)
    a, b = num(low, lang, decimals), num(high, lang, decimals)
    sep = " " if unit and unit not in ("%",) else ""
    if a == b:
        return f"{a}{sep}{unit}"
    joiner = " a " if lang == "pt" else " to "
    return f"{a}{joiner}{b}{sep}{unit}"


# field -> (Portuguese label, English label, decimals, unit, hour attribute)
MEASURES = {
    "wind_max_kmh": ("vento", "wind", 0, "km/h", "wind_kmh"),
    "wind_min_kmh": ("vento", "wind", 0, "km/h", "wind_kmh"),
    "gust_max_kmh": ("rajadas", "gusts", 0, "km/h", "gust_kmh"),
    "rain_chance_max": ("chance de chuva", "rain chance", 0, "%", "rain_chance"),
    "rain_mm_max": ("chuva", "rain", 1, "mm", "rain_mm"),
    "temp_min_c": ("temperatura", "temperature", 0, "°C", "temp_c"),
    "temp_max_c": ("temperatura", "temperature", 0, "°C", "temp_c"),
    "uv_max": ("UV", "UV", 0, "", "uv"),
    "cloud_max": ("nuvens", "cloud", 0, "%", "cloud"),
    "wave_max_m": ("onda", "waves", 1, "m", "wave_m"),
    "wave_min_m": ("onda", "waves", 1, "m", "wave_m"),
    "water_min_c": ("água", "water", 0, "°C", "water_c"),
}


# Short column titles for the hour table.
COLUMNS = {
    "wind_kmh": ("vento km/h", "wind km/h"), "gust_kmh": ("rajada km/h", "gust km/h"),
    "rain_chance": ("chuva %", "rain %"), "rain_mm": ("chuva mm", "rain mm"), "temp_c": ("ar °C", "air °C"),
    "uv": ("UV", "UV"), "cloud": ("nuvem %", "cloud %"), "wave_m": ("onda m", "waves m"),
    "water_c": ("água °C", "water °C"),
}


def measure(field: str, values: list[float], lang: str) -> str:
    """``vento de 5 a 9 km/h``, ``wind 5 to 9 km/h``."""
    pt, en, decimals, unit, _ = MEASURES[field]
    label = pt if lang == "pt" else en
    text = _range(values, lang, decimals, unit)
    if lang == "pt" and " a " in text:
        return f"{label} de {text}"
    return f"{label} {text}"


def limit(field: str, value: float, lang: str) -> str:
    """The rule itself: ``vento até 15 km/h``, ``onda de pelo menos 1 m``."""
    pt, en, decimals, unit, _ = MEASURES[field]
    shown = num(float(value), lang, decimals if not float(value).is_integer() else 0)
    sep = " " if unit and unit != "%" else ""
    upper = field.endswith("_max") or "_max_" in field
    if lang == "pt":
        return f"{pt} {'até' if upper else 'de pelo menos'} {shown}{sep}{unit}"
    return f"{en} {'up to' if upper else 'at least'} {shown}{sep}{unit}"


_FAILS = {
    "wind_max_kmh": ("o vento passa de {v} km/h", "the wind goes over {v} km/h"),
    "wind_min_kmh": ("o vento cai abaixo de {v} km/h", "the wind drops under {v} km/h"),
    "gust_max_kmh": ("as rajadas passam de {v} km/h", "gusts go over {v} km/h"),
    "rain_chance_max": ("a chance de chuva passa de {v}%", "the rain chance goes over {v}%"),
    "rain_mm_max": ("a chuva passa de {v} mm", "rain goes over {v} mm"),
    "temp_min_c": ("a temperatura cai abaixo de {v} °C", "it gets colder than {v} °C"),
    "temp_max_c": ("a temperatura passa de {v} °C", "it gets hotter than {v} °C"),
    "uv_max": ("o UV passa de {v}", "UV goes over {v}"),
    "cloud_max": ("as nuvens passam de {v}%", "cloud goes over {v}%"),
    "wave_max_m": ("a onda passa de {v} m", "waves go over {v} m"),
    "wave_min_m": ("a onda cai abaixo de {v} m", "waves drop under {v} m"),
    "water_min_c": ("a água fica abaixo de {v} °C", "the water is under {v} °C"),
}


def fails(field: str, value: object, lang: str, *, missing: bool = False, tide_kind: str = "",
          after: bool = False) -> str:
    """What going over a rule looks like: ``o vento passa de 15 km/h``.

    With ``after=True`` a tide rule reads as what the sea does next: after a low
    tide it comes in, after a high tide it goes out.
    """
    if missing:
        if field == "tide":
            return "falta previsão de maré" if lang == "pt" else "there is no tide forecast"
        label = MEASURES[field][0 if lang == "pt" else 1]
        return f"falta previsão de {label}" if lang == "pt" else f"there is no {label} forecast"
    if field == "tide" and after:
        if lang == "pt":
            return "a maré já está enchendo" if tide_kind == "low" else "a maré já está vazando"
        return "the tide is coming in" if tide_kind == "low" else "the tide is going out"
    if field == "tide":
        if lang == "pt":
            return "a maré fica longe da baixa" if tide_kind == "low" else "a maré fica longe da cheia"
        return "the tide is far from low" if tide_kind == "low" else "the tide is far from high"
    decimals = MEASURES[field][2]
    shown = num(float(value), lang, decimals if not float(value).is_integer() else 0)
    return _FAILS[field][0 if lang == "pt" else 1].format(v=shown)


def tide_at(kind: str, moment: datetime, lang: str) -> str:
    if lang == "pt":
        return f"maré {'baixa' if kind == 'low' else 'cheia'} às {clock(moment, lang)}"
    return f"{kind} tide at {clock(moment, lang)}"


def hours_count(n: int, total: int, lang: str) -> str:
    """Counted over the hours that fit: the ones you asked for, in daylight, not already gone."""
    if total == 1:
        return "na única hora possível" if lang == "pt" else "in the only hour that fits"
    if lang == "pt":
        return f"em todas as {total} horas possíveis" if n == total else f"em {n} das {total} horas possíveis"
    return f"in all {total} hours that fit" if n == total else f"in {n} of the {total} hours that fit"


def too_short(longest: int, need: float, lang: str) -> str:
    need_text = num(need, lang, 1)
    if lang == "pt":
        return f"o maior trecho bom tem {longest}h, e você pediu {need_text}h seguidas"
    return f"the longest good stretch is {longest} h, and you asked for {need_text} h in a row"
