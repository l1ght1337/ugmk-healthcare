"""Parse identification answers typed by the patient: full name, birth date, phone."""

from __future__ import annotations

import re
from datetime import date

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


def parse_full_name(text: str) -> tuple[str, str, str | None] | None:
    """«иванов иван иванович» → («Иванов», «Иван», «Иванович»); patronymic is optional."""
    words = _WORD.findall(text)
    if not 2 <= len(words) <= 3 or any(len(w) < 2 for w in words):
        return None
    last, first, *rest = (_cap(w) for w in words)
    return last, first, rest[0] if rest else None


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
