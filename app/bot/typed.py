"""A button's label typed as text presses the button: «бытовая», «вечер», «14:30», «да».

Patients often type what they see on a button. The match is strict on purpose: a typed
word must name one button and only one, otherwise the text goes on to the usual reading
(the wish parser, the scenario answer, DeepSeek). Pure functions, no Telegram.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.flow.commands import normalize, yes_no

# What else may be typed for a button, by its callback data.
SYNONYMS: dict[str, frozenset[str]] = {
    "c:yes": frozenset({"согласен", "согласна", "даю согласие", "соглашаюсь", "подтверждаю"}),
    "c:no": frozenset({"не согласен", "не согласна", "отказываюсь"}),
    "t:list": frozenset({"список", "из списка", "выбрать", "покажи список", "темы"}),
}
# «да» / «нет» for buttons whose labels do not start with these words. Consent is not
# here: it is given by «Согласен», typed or pressed, not by a bare «да».
YES_DATA = frozenset({"h:yes", "ca:yes"})
NO_DATA = frozenset({"h:no", "ca:no"})
_SMALL_WORDS = frozenset({"или", "и", "на", "до", "в", "у", "с", "по", "к", "не"})
MIN_PREFIX = 4


def _words(label: str) -> list[str]:
    """The words of a label that carry its meaning: no digits, no «или», «до»."""
    return [w for w in label.split() if w.isalpha() and w not in _SMALL_WORDS]


def _covers(text: str, label: str) -> bool:
    """Every typed word is on the label, and they are at least half of it.

    «бытовая» for «Бытовая травма»; not «другой день» for «День 12–18».
    """
    words = _words(label)
    typed = _words(text)
    return bool(typed) and all(w in words for w in typed) and len(typed) * 2 >= len(words)


def pressed(text: str, buttons: Sequence[tuple[str, str]], *, mode: str = "choice") -> str | None:
    """The callback data of the button the text names, or None.

    ``buttons`` — (label, data). ``mode``: «exact» — the whole label only (topic menus: a
    typed «ребёнок» is not «Ребёнок до 6 месяцев»); «answer» — also the beginning of a
    label (scenario questions: the branch must not be guessed); «choice» — also words in
    any order (days, time, «для кого»).
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
    if mode == "exact" or len(typed) < MIN_PREFIX:
        return None
    starts = {d for label, d in labels if label.startswith(typed) and _covers(typed, label)}
    if (data := only(starts)) is not None or mode == "answer":
        return data
    return only({d for label, d in labels if _covers(typed, label)})
