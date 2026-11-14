"""Parse identification answers typed by the patient: full name, birth date, phone."""

from __future__ import annotations

import re
from datetime import date

from app.flow.names import FIRST_NAMES

_WORD = re.compile(r"[А-ЯЁа-яёA-Za-z]+(?:-[А-ЯЁа-яёA-Za-z]+)?")
_MONTHS = (
    ("январ", 1), ("феврал", 2), ("март", 3), ("апрел", 4), ("ма", 5), ("июн", 6),
    ("июл", 7), ("август", 8), ("сентябр", 9), ("октябр", 10), ("ноябр", 11), ("декабр", 12),
)  # fmt: skip
_NUMERIC = re.compile(r"\b(\d{1,2})[./\-\s](\d{1,2})[./\-\s](\d{4}|\d{2})\b")
_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_TEXTUAL = re.compile(r"\b(\d{1,2})\s+([а-яё]+)\s+(\d{4})\b", re.IGNORECASE)
MAX_AGE_YEARS = 120


def _cap(word: str) -> str:
    return "-".join(part[:1].upper() + part[1:].lower() for part in word.split("-"))


def _key(word: str) -> str:
    return word.lower().replace("ё", "е")


_FIRST_NAMES = frozenset(_key(name) for name in FIRST_NAMES)
_PATRONYMIC_ENDS = ("вич", "вна", "чна")
_PATRONYMIC_WORDS = frozenset({"ильич", "кузьмич", "фомич", "лукич", "никитич", "саввич"})
_PATRONYMIC_PARTICLES = frozenset({"оглы", "оглу", "кызы", "кизи", "гызы", "улы", "уулу"})


def _is_patronymic(word: str) -> bool:
    return _key(word).endswith(_PATRONYMIC_ENDS) or _key(word) in _PATRONYMIC_WORDS


def parse_full_name(text: str) -> tuple[str, str, str | None] | None:
    """(surname, first name, patronymic) from the words in any order; patronymic is optional.

    «Петр Андреев Александрович» → («Андреев», «Петр», «Александрович»). The patronymic is
    known by its ending (-вич, -вна, -чна, «оглы», «кызы»), the first name by the list in
    app/flow/names.py, the rest is the surname. Where the words do not tell what is what,
    the order stays as it is written: surname, first name, patronymic.
    """
    words = _WORD.findall(text)
    if len(_WORD.sub("", text).strip(" ,.;")) > 0 or any(len(w) < 2 for w in words):
        return None
    patronymic: str | None = None
    particles = [i for i, w in enumerate(words) if _key(w) in _PATRONYMIC_PARTICLES]
    if particles:  # «Вели оглы»: the father's name and the particle after it
        at = particles[0]
        if len(particles) > 1 or at == 0 or len(words) != 4:
            return None
        patronymic = f"{_cap(words[at - 1])} {_key(words[at])}"
        words = [w for i, w in enumerate(words) if i not in (at - 1, at)]
    if not 2 <= len(words) + bool(patronymic) <= 3 or len(words) < 2:
        return None
    words = [_cap(w) for w in words]
    names = [i for i, w in enumerate(words) if _key(w) in _FIRST_NAMES]
    if patronymic is None and len(words) == 3:
        found = [i for i, w in enumerate(words) if _is_patronymic(w)]
        if len(found) > 1 and len(names) == 1 and names[0] + 1 in found:
            found = [names[0] + 1]  # «Роман Аркадьевич Абрамович»: it follows the first name
        if len(found) != 1:
            return words[0], words[1], words[2]  # not clear: as it is written
        patronymic = words.pop(found[0])
        names = [i for i, w in enumerate(words) if _key(w) in _FIRST_NAMES]
    if len(names) == 1:  # the first name is known: the other word is the surname
        first = words[names[0]]
        return words[1 - names[0]], first, patronymic
    return words[0], words[1], patronymic  # not clear: as it is written


def parse_birth_date(text: str, today: date) -> date | None:
    """12.03.2015, 12/03/15, 2015-03-12 or «12 марта 2015»; not in the future, not absurd."""
    found: date | None = None
    try:
        if m := _ISO.search(text):
            found = date(int(m[1]), int(m[2]), int(m[3]))
        elif m := _NUMERIC.search(text):
            year = int(m[3])
            if year < 100:
                year += 2000 if 2000 + year <= today.year else 1900
            found = date(year, int(m[2]), int(m[1]))
        elif m := _TEXTUAL.search(text):
            month = next((n for p, n in _MONTHS if m[2].lower().startswith(p)), None)
            if month is not None:
                found = date(int(m[3]), month, int(m[1]))
    except ValueError:
        return None
    if found is None or found > today or today.year - found.year > MAX_AGE_YEARS:
        return None
    return found


def age_in_months(birth: date, today: date) -> int:
    months = (today.year - birth.year) * 12 + today.month - birth.month
    return months - 1 if today.day < birth.day else months


def parse_phone(text: str) -> str | None:
    """A typed Russian phone number as +7XXXXXXXXXX."""
    digits = re.sub(r"\D", "", text)
    if len(digits) == 11 and digits[0] in "78":
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return f"+7{digits}"


def normalize_phone(phone: str) -> str:
    """Telegram contacts come as «79123456789» or «+79123456789»."""
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits[0] in "78":
        return f"+7{digits[1:]}"
    return f"+{digits}" if digits else phone
