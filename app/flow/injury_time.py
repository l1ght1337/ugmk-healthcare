"""When the injury happened, read by the code: «сегодня», «вчера», «3 дня назад».

The words stand in config/clinic.yaml (``injury_time``), each with how many hours ago it may
be: «вчера» — from 0 to 48. The thresholds stand with the answers of the question in the
scenario (``to_hours`` / ``from_hours`` of an option): «В течение 7 дней» — at most 168 hours.
A word answers the question when its hours lie on one side of the threshold. «Вчера» does not
say whether a day has passed, «неделю назад» — whether seven days have: the question is asked
(``ASK``). So it is when two times in a message answer differently («месяц назад сломал, а
сегодня снова упал»). No word — ``None``: DeepSeek decides, as before.

A part of a message that is a wish for the visit («запишите на сегодня», «можно завтра
утром?») is not about the injury: the caller says which parts to skip.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Final, Protocol

from app.flow.safety import normalize
from app.flow.schema import FieldSpec

ASK: Final = "ask"  # a word is there and does not answer: the question is asked
_PART = re.compile(r"[.!?;,\n]+|\s—\s")
DAY = 24


class _Words(Protocol):
    phrases: tuple[str, ...]
    hours: tuple[int, int]


class _Unit(Protocol):
    forms: tuple[str, ...]
    hours: int


class Config(Protocol):
    """config/clinic.yaml, ``injury_time`` (app/config.py, InjuryTime)."""

    words: tuple[_Words, ...]
    units: tuple[_Unit, ...]
    ago: str
    numbers: dict[str, int]


def _ago(part: str, config: Config) -> list[tuple[int, int]]:
    """«2 дня назад», «час назад», «пару недель назад» in a normalized part: the hours.

    The first form of a unit stands without a number («день назад» is one day); the other
    forms need one before them: «несколько дней назад» says no number and is not read.
    """
    found = []
    words = part.split()
    for at, word in enumerate(words):
        if word != config.ago or at == 0:
            continue
        said = words[at - 1]
        before = words[at - 2] if at >= 2 else ""
        unit = next((u for u in config.units if said in u.forms), None)
        if unit is None:  # «года три назад», «недели две назад»: the number after the unit
            said, before = before, said
            unit = next((u for u in config.units if said in u.forms), None)
        if unit is None:
            continue
        count = int(before) if before.isdigit() else config.numbers.get(before)
        if count is None and said == unit.forms[0]:
            count = 1
        if not count:
            continue
        middle, slack = count * unit.hours, min(unit.hours, DAY)
        found.append((max(0, middle - slack), middle + slack))
    return found


def hours(
    text: str, config: Config, skip: Callable[[str], bool] | None = None
) -> list[tuple[int, int]]:
    """How long ago, by every word of the config found in the text: (from, to) in hours."""
    found = []
    for raw in _PART.split(text):
        if not raw.strip() or (skip is not None and skip(raw)):
            continue
        part = normalize(raw)
        found += _ago(part, config)
        padded = f" {part} "
        for words in config.words:
            if any(f" {phrase} " in padded for phrase in words.phrases):
                found.append(words.hours)
    return found


def read(
    text: str, field: FieldSpec, config: Config, skip: Callable[[str], bool] | None = None
) -> str | None:
    """The answer of ``field`` the text gives: an option's value, ``ASK`` — the words do not
    answer, ``None`` — there are no words about the time (or the field has no thresholds)."""
    options = [o for o in field.options if o.from_hours is not None or o.to_hours is not None]
    found = hours(text, config, skip) if options else []
    if not found:
        return None
    answers = set()
    for low, high in found:
        fits = [
            o.value
            for o in options
            if (o.from_hours is None or low >= o.from_hours)
            and (o.to_hours is None or high <= o.to_hours)
        ]
        answers.add(fits[0] if len(fits) == 1 else ASK)
    return answers.pop() if len(answers) == 1 else ASK
