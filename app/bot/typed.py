"""A button's label typed as text presses the button: «бытовая», «вечер», «14:30», «да».

Patients often type what they see on a button. The match is strict on purpose: a typed
word must name one button and only one, otherwise the text goes on to the usual reading
(the wish parser, the scenario answer, DeepSeek). Pure functions, no Telegram.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import time

from app.flow.commands import normalize, yes_no

# What else may be typed for a button, by its callback data.
SYNONYMS: dict[str, frozenset[str]] = {
    "c:yes": frozenset({"согласен", "согласна", "даю согласие", "соглашаюсь", "подтверждаю"}),
    "c:no": frozenset({"не согласен", "не согласна", "отказываюсь"}),
}
# «да» / «нет» for buttons whose labels do not start with these words. Consent is not
# here: it is given by «Согласен», typed or pressed, not by a bare «да».
YES_DATA = frozenset({"h:yes", "ca:yes"})
NO_DATA = frozenset({"h:no", "ca:no"})
_SMALL_WORDS = frozenset({"или", "и", "на", "до", "в", "у", "с", "по", "к", "не"})
MIN_PREFIX = 4
# Words that are on topic buttons but name no topic: «травма» is not «Травма глаза».
_TOPIC_STOP_WORDS = frozenset({"травма", "травмы", "прием", "другой", "другая"})


# «13:30», «13.30» — not a part of a date («12.11.2026») or of a longer number.
_CLOCK = re.compile(r"(?<![\d:.])([01]?\d|2[0-3])([:.])([0-5]\d)(?![\d:]|\.\d)")
_SENTENCE_END = re.compile(r"[.!?;…]+(?:\s+|$)")
_NOT_THIS = re.compile(r"\b(?:не|ни|нет|кроме|вместо)\b")


def clock_times(text: str) -> list[tuple[time, bool]]:
    """The clock times a text names: (the time, written with a colon).

    «13.30» may be a time or a date («12.11»): the caller counts it only if such a time is
    on the screen.
    """
    return [(time(int(m[1]), int(m[3])), m[2] == ":") for m in _CLOCK.finditer(text)]


def refuses_time(text: str) -> bool:
    """A «не», «кроме», «вместо» in a sentence that names a time: «только не на 13:30»,
    «13:30 не подходит». «Я кнопки не нажимаю. Запишите на 13:30» is not a refusal."""
    sentences = _SENTENCE_END.split(text)
    return any(_CLOCK.search(s) and _NOT_THIS.search(normalize(s)) for s in sentences)


def _words(label: str) -> list[str]:
    """The words of a label that carry its meaning: no digits, no «или», «до»."""
    return [w for w in label.split() if w.isalpha() and w not in _SMALL_WORDS]


def _covers(text: str, label: str) -> bool:
    """Every typed word is on the label, and they are at least half of it.

    «бытовая» for «Бытовая травма»; not «другой день» for «День 12–18». A number typed with
    them must be on the label too: «завтра в 3» is a wish for a time, not «Завтра, 13.11».
    """
    words = _words(label)
    typed = _words(text)
    numbers = [w for w in text.split() if w.isdigit()]
    if any(n not in label.split() for n in numbers):
        return False
    return bool(typed) and all(w in words for w in typed) and len(typed) * 2 >= len(words)


def _names_topic(typed: str, label: str) -> bool:
    """A topic button named by its word: «ударился» for «Упал, ударился, подвернул»,
    «укус» for «Укус животного».

    A label that is a list — one or more of its items, whole. A short label — at least
    half of its words. A label with a number («Ребёнок до 6 месяцев») — never: the number
    decides the route, so only the whole label presses it.
    """
    if any(ch.isdigit() for ch in label):
        return False
    items = [normalize(part) for part in label.split(",")]
    if len(items) > 1:
        return set(typed.split()) <= set(items)
    if len(typed) < MIN_PREFIX or set(typed.split()) <= _TOPIC_STOP_WORDS:
        return False
    return _covers(typed, items[0])


def pressed(text: str, buttons: Sequence[tuple[str, str]], *, mode: str = "choice") -> str | None:
    """The callback data of the button the text names, or None.

    ``buttons`` — (label, data). ``mode``: «exact» — the whole label only; «topic» — also a
    word that is on one topic button only (a typed «ребёнок» is still not «Ребёнок до 6
    месяцев»); «answer» — also the beginning of a label (scenario questions: the branch
    must not be guessed); «choice» — also words in any order (days, time, «для кого»).
    """
    typed = normalize(text)
    if not typed:
        return None
    labels = [(normalize(label), data) for label, data in buttons]

    def only(found: set[str]) -> str | None:
        return found.pop() if len(found) == 1 else None

    if (data := only({d for label, d in labels if label == typed})) is not None:
        return data
    datas = {d for _, d in labels}
    for data, phrases in SYNONYMS.items():
        if typed in phrases and data in datas:
            return data
    answer = yes_no(text)
    if answer is not None:
        head = "да" if answer else "нет"
        by_label = only({d for label, d in labels if label.split()[:1] == [head]})
        return by_label or only(datas & (YES_DATA if answer else NO_DATA))
    if mode == "topic":
        return only({data for label, data in buttons if _names_topic(typed, label)})
    if mode == "exact" or len(typed) < MIN_PREFIX:
        return None
    starts = {d for label, d in labels if label.startswith(typed) and _covers(typed, label)}
    if (data := only(starts)) is not None or mode == "answer":
        return data
    return only({d for label, d in labels if _covers(typed, label)})
