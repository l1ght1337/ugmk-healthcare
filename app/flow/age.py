"""Parse a child's age typed by the patient («8», «8 лет», «4 месяца», «полтора года»).

Deterministic, no LLM: the age decides the 05-04 infant route (under 6 months), so it
must not depend on a network call.
"""

from __future__ import annotations

import re

_NUMBER_WORDS = {
    "один": 1, "одна": 1, "одного": 1, "два": 2, "две": 2, "двух": 2, "три": 3, "трех": 3,
    "четыре": 4, "четырех": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
    "десять": 10, "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17,
}  # fmt: skip
_YEARS = r"(?:год|года|годик|годика|годиков|лет|г)"
_MONTHS = r"(?:месяц|месяца|месяцев|мес|месяцок|месяцочка)"
_WEEKS = r"(?:неделя|недели|неделю|недель|нед)"
_DAYS = r"(?:день|дня|дней)"
_NUM = r"(\d+(?:[.,]\d+)?)"
MAX_MONTHS = 18 * 12 * 6  # anything above is not an age


def _words_to_digits(text: str) -> str:
    return " ".join(str(_NUMBER_WORDS.get(w, w)) for w in text.split())


def parse_age_months(text: str) -> int | None:
    """Age in whole months, or None if the text does not state an age."""
    t = text.lower().replace("ё", "е").replace(",", ".")
    t = _words_to_digits(" ".join(re.sub(r"[^\w.]+", " ", t).split()))
    if re.search(r"\bноворожд|\bгрудничок\b", t):
        return 0
    if re.search(r"\bполгода\b|\bпол года\b", t):
        return 6
    if re.search(r"\bполтора год", t):
        return 18
    if re.search(rf"\b\d+ {_WEEKS}\b|\b{_WEEKS}\b|\b\d+ {_DAYS}\b", t):
        return 0
    years = re.search(rf"\b{_NUM} ?{_YEARS}\b", t)
    months = re.search(rf"\b{_NUM} ?{_MONTHS}\b", t)
    if years or months:
        total = float(years.group(1).replace(",", ".")) * 12 if years else 0.0
        total += float(months.group(1)) if months else 0.0
        return int(total) if total <= MAX_MONTHS else None
    bare = re.fullmatch(r"\s*(\d{1,2})\s*", t)
    if bare:  # «8» — years
        return int(bare.group(1)) * 12
    if re.search(r"\bгод(ик)?\b", t):  # «ребёнку год»
        return 12
    return None
