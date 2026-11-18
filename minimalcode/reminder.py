"""User meta information for the ``latest_reminder`` message.

DeepSeek's own clients tell the model the current date, where the user is, the
platform, and the language. Over the API that is a ``latest_reminder`` message
placed immediately before the user message it belongs to, formatted as
``<date>,<weekday>,<location>,<platform>,<language>``.

The Responses API has no such role, so the caller only injects it for the
protocols that accept it.
"""

from __future__ import annotations

import datetime as dt

from .config import REMINDER_PLATFORMS, Settings

PLATFORMS = REMINDER_PLATFORMS
# Weekday names, Sunday first, as the language writes them itself. Languages
# without an entry fall back to English rather than sending a half-translated
# string the model would have to guess at.
_WEEKDAYS: dict[str, tuple[str, ...]] = {
    "English": (
        "Sunday",
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
    ),
    "中文": ("星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六"),
    "中文繁體": ("星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六"),
    "中文香港": ("星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六"),
    "日本語": ("日曜日", "月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日"),
    "한국어": ("일요일", "월요일", "화요일", "수요일", "목요일", "금요일", "토요일"),
    "Deutsch": (
        "Sonntag",
        "Montag",
        "Dienstag",
        "Mittwoch",
        "Donnerstag",
        "Freitag",
        "Samstag",
    ),
    "français": (
        "dimanche",
        "lundi",
        "mardi",
        "mercredi",
        "jeudi",
        "vendredi",
        "samedi",
    ),
    "español": (
        "domingo",
        "lunes",
        "martes",
        "miércoles",
        "jueves",
        "viernes",
        "sábado",
    ),
    "português": (
        "domingo",
        "segunda-feira",
        "terça-feira",
        "quarta-feira",
        "quinta-feira",
        "sexta-feira",
        "sábado",
    ),
    "italiano": (
        "domenica",
        "lunedì",
        "martedì",
        "mercoledì",
        "giovedì",
        "venerdì",
        "sabato",
    ),
    "русский": (
        "воскресенье",
        "понедельник",
        "вторник",
        "среда",
        "четверг",
        "пятница",
        "суббота",
    ),
    "Nederlands": (
        "zondag",
        "maandag",
        "dinsdag",
        "woensdag",
        "donderdag",
        "vrijdag",
        "zaterdag",
    ),
    "polski": (
        "niedziela",
        "poniedziałek",
        "wtorek",
        "środa",
        "czwartek",
        "piątek",
        "sobota",
    ),
    "Türkçe": (
        "Pazar",
        "Pazartesi",
        "Salı",
        "Çarşamba",
        "Perşembe",
        "Cuma",
        "Cumartesi",
    ),
}

# Roles accepted by the Messages and Chat Completions APIs. The Responses API
# rejects anything that is not a user/assistant/system/developer item.
UNSUPPORTED_PROTOCOLS = frozenset({"openai_responses", "deepseek_web"})


def weekday_name(language: str, day: int) -> str:
    """Return the weekday ``day`` (Monday = 0) written in ``language``."""
    table = _WEEKDAYS.get(language) or _WEEKDAYS["English"]
    # ``strftime("%w")`` order is Sunday first, the same order as every table.
    return table[(day + 1) % 7]


def supports_reminder(protocol: str) -> bool:
    """Return True when ``protocol`` accepts a ``latest_reminder`` message."""
    return protocol not in UNSUPPORTED_PROTOCOLS


def build_reminder(
    settings: Settings,
    *,
    now: dt.datetime | None = None,
) -> str | None:
    """Build the ``latest_reminder`` content, or None when it is disabled.

    ``location`` is optional and may contain commas; ``platform`` and
    ``language`` are validated by the configuration loader so the string the
    model receives always has five fields.
    """
    if not bool(settings.get("reminder.enabled", True)):
        return None
    moment = now or dt.datetime.now()
    language = str(settings.get("reminder.language", "English"))
    platform = str(settings.get("reminder.platform", "App"))
    location = str(settings.get("reminder.location", "") or "")
    return ",".join(
        (
            moment.strftime("%Y-%m-%d"),
            weekday_name(language, moment.weekday()),
            location,
            platform,
            language,
        )
    )


__all__ = [
    "PLATFORMS",
    "UNSUPPORTED_PROTOCOLS",
    "build_reminder",
    "supports_reminder",
    "weekday_name",
]
