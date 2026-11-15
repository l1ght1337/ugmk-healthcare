"""A time said in words, as digits: «тринадцать тридцать» → «13:30», «в два» → «в 2», «в час
дня» → «в 1 дня», «полвторого» → «13:30».

The wish parser (app/flow/wishes.py) and the buttons of the time read digits; a patient who
does not press buttons writes the time as he says it. ``digits`` rewrites only what it reads
as a time — an hour after «в», «к», «после»…, an hour with its minutes, «пол…» — and leaves
the rest of the message as it is. What the result means is still decided by the wish parser:
«в два раза» becomes «в 2 раза» and is no time there either.
"""

from __future__ import annotations

import re

_NOMINATIVE = {
    "ноль": 0, "час": 1, "один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
    "девятнадцать": 19, "двадцать": 20,
}  # fmt: skip
# «после пяти», «до шести», «к двум», «около трёх»
_OBLIQUE = {
    "часа": 1, "часу": 1, "одного": 1, "двух": 2, "двум": 2, "трех": 3, "трем": 3,
    "четырех": 4, "четырем": 4, "пяти": 5, "шести": 6, "семи": 7, "восьми": 8, "девяти": 9,
    "десяти": 10, "одиннадцати": 11, "двенадцати": 12, "тринадцати": 13, "четырнадцати": 14,
    "пятнадцати": 15, "шестнадцати": 16, "семнадцати": 17, "восемнадцати": 18,
    "девятнадцати": 19, "двадцати": 20,
}  # fmt: skip
_AFTER_TWENTY = {"один": 1, "одну": 1, "одного": 1, "два": 2, "две": 2, "двух": 2, "три": 3,
                 "трех": 3}  # fmt: skip
_UNITS = {"один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
          "шесть": 6, "семь": 7, "восемь": 8, "девять": 9}  # fmt: skip
_TENS = {"двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50}
_TEENS = {word: n for word, n in _NOMINATIVE.items() if 10 <= n <= 19}
# «полвторого» is half past one: the hour before the one named
_HALF = {
    "первого": 12, "второго": 1, "третьего": 2, "четвертого": 3, "пятого": 4, "шестого": 5,
    "седьмого": 6, "восьмого": 7, "девятого": 8, "десятого": 9, "одиннадцатого": 10,
    "двенадцатого": 11,
}  # fmt: skip
AFTERNOON_UNTIL = 6  # «полвторого», «полпятого» are 13:30 and 16:30, as «в 3» is 15:00


def _any(words: dict[str, int] | tuple[str, ...]) -> str:
    return "|".join(sorted(words, key=len, reverse=True))


_TWENTY = rf"двадцать\s+(?:{_any(_AFTER_TWENTY)})|двадцати\s+(?:{_any(_AFTER_TWENTY)})"
_HOUR_SAID = rf"(?:двадцать\s+(?:один|одну|два|две|три)|{_any(_NOMINATIVE)})"
_HOUR_ANY = rf"(?:{_TWENTY}|{_any(_NOMINATIVE)}|{_any(_OBLIQUE)})"
_MINUTES = (
    rf"(?:ноль\s+(?:ноль|{_any(_UNITS)})|(?:{_any(_TENS)})(?:\s+(?:{_any(_UNITS)}))?"
    rf"|{_any(_TEENS)})"
)
_CLOCK = re.compile(
    rf"\b(?P<h>{_HOUR_SAID})\s+(?:час\w*\s+)?(?P<m>{_MINUTES})(?:\s+минут\w*)?\b", re.IGNORECASE
)
_HALF_PAST = re.compile(rf"\b(?:пол[\s-]?|половин[аеуы]\s+)(?P<h>{_any(_HALF)})\b", re.IGNORECASE)
_AT = re.compile(
    rf"\b(?P<prep>в|во|к|на|около|после|до|с)\s+(?P<h>{_HOUR_ANY})\b(?!\s*[:.]\s*\d)",
    re.IGNORECASE,
)
_WITH_PART = re.compile(rf"\b(?P<h>{_HOUR_ANY})\s+(?P<part>утра|вечера|ночи)\b", re.IGNORECASE)


def _plain(word: str) -> str:
    return " ".join(word.lower().replace("ё", "е").split())


def _hour(said: str) -> int:
    words = _plain(said).split()
    if len(words) == 2:  # «двадцать один», «двадцати двух»
        return 20 + _AFTER_TWENTY[words[1]]
    return {**_NOMINATIVE, **_OBLIQUE}[words[0]]


def _minutes(said: str) -> int:
    words = _plain(said).split()
    if words[0] == "ноль":
        return 0 if words[1] == "ноль" else _UNITS[words[1]]
    if words[0] in _TENS:
        return _TENS[words[0]] + (_UNITS[words[1]] if len(words) > 1 else 0)
    return _TEENS[words[0]]


def digits(text: str) -> str:
    """The message with the times it says in words written as digits."""
    same = text.replace("ё", "е").replace("Ё", "Е")  # one letter for one: places are kept

    def clock(m: re.Match[str]) -> str:
        return f"{_hour(m['h'])}:{_minutes(m['m']):02d}"

    def half(m: re.Match[str]) -> str:
        hour = _HALF[_plain(m["h"])]
        return f"{hour + 12 if hour <= AFTERNOON_UNTIL else hour}:30"

    def at(m: re.Match[str]) -> str:
        return f"{m['prep']} {_hour(m['h'])}"

    def with_part(m: re.Match[str]) -> str:
        return f"{_hour(m['h'])} {m['part']}"

    read = _CLOCK.sub(clock, same)
    read = _HALF_PAST.sub(half, read)
    read = _AT.sub(at, read)
    read = _WITH_PART.sub(with_part, read)
    return text if read == same else read
