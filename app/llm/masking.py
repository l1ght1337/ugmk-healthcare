"""Mask personal data before any text goes to DeepSeek (CLAUDE.md, rule 4).

Only the complaint or question text is sent, and it passes through ``mask`` first:
names, phones, e-mails, logins and long numbers (documents, chat ids) become placeholders.
Dates of events are kept as relative time («упал 10 ноября» → «упал 2 дня назад»), so
the LLM can tell how long ago the injury was; birth dates and dates next to a name are
masked. Personal data the bot already holds for the chat (``KnownPerson``) is masked in
any grammatical form.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from app.flow.names import FIRST_NAMES, stem_pattern
from app.flow.words import relative_day

NAME = "[ИМЯ]"
PHONE = "[ТЕЛЕФОН]"
DATE = "[ДАТА]"
EMAIL = "[EMAIL]"
NUMBER = "[НОМЕР]"
LOGIN = "[ЛОГИН]"
DOCTOR = "[ВРАЧ]"  # a trauma centre doctor named by the patient (app/flow/wishes.py)
PLACEHOLDERS = (NAME, PHONE, DATE, EMAIL, NUMBER, LOGIN, DOCTOR)


@dataclass(frozen=True)
class KnownPerson:
    """Personal data of the chat's patient that must never reach the LLM."""

    names: tuple[str, ...] = ()  # last, first, patronymic — patient and applicant
    phone: str | None = None
    username: str | None = None
    birth_dates: tuple[date, ...] = ()  # patient and children
    chat_id: int | None = None


_MONTHS = (
    r"(?:январ[яеь]|феврал[яеь]|марта?|апрел[яеь]|ма[яй]|июн[яеь]|июл[яеь]|августа?|"
    r"сентябр[яеь]|октябр[яеь]|ноябр[яеь]|декабр[яеь])"
)
_L = r"[А-ЯЁA-Z]"  # capital letter
_l = r"[а-яёa-z]"

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LOGIN = re.compile(r"(?:https?://)?t\.me/\w+|(?<![\w@])@\w{3,}")
_MONTH_NUMBERS = (
    ("январ", 1), ("феврал", 2), ("март", 3), ("апрел", 4), ("ма", 5), ("июн", 6),
    ("июл", 7), ("август", 8), ("сентябр", 9), ("октябр", 10), ("ноябр", 11), ("декабр", 12),
)  # fmt: skip
_DATE = re.compile(
    r"\b(?P<iy>\d{4})-(?P<im>\d{1,2})-(?P<id>\d{1,2})\b"
    r"|\b(?P<nd>\d{1,2})[./-](?P<nm>\d{1,2})[./-](?P<ny>\d{4}|\d{2})\b"
    r"|\b(?P<sd>\d{1,2})[./](?P<sm>0[1-9]|1[0-2])\b(?![.,]\d|\s*(?:год|лет|мес))"
    rf"|\b(?P<td>\d{{1,2}})\s+(?P<tm>{_MONTHS})(?:\s+(?P<ty>\d{{4}})(?:\s*(?:г\.|года|г\b))?)?",
    re.IGNORECASE,
)
_BIRTH_BEFORE = re.compile(
    r"(?:дат\w*\s+рожд\w*|д\.\s?р\.?|\bдр\b|родил\w*|рожд\w*|\[ИМЯ\])[\s:,;()—-]{0,3}$",
    re.IGNORECASE,
)
_BIRTH_AFTER = re.compile(
    r"^(?:\s*(?:г\.?\s*р\.?|года рождения|рождения)|[\s,;:()—-]{0,3}\[ИМЯ\])", re.IGNORECASE
)
_BIRTH_YEAR = re.compile(r"\b(?:19|20)\d{2}\s*(?:г\.?\s*р\.?|года рождения|г\.р)", re.IGNORECASE)


def _month_number(word: str) -> int:
    word = word.lower()
    return next(n for prefix, n in _MONTH_NUMBERS if word.startswith(prefix))


def _parse_date(m: re.Match[str], today: date) -> date | None:
    g = m.groupdict()
    try:
        if g["iy"]:
            return date(int(g["iy"]), int(g["im"]), int(g["id"]))
        if g["nd"]:
            year = int(g["ny"])
            if year < 100:
                year += 2000 if 2000 + year <= today.year + 1 else 1900
            return date(year, int(g["nm"]), int(g["nd"]))
        day, month = (
            (int(g["sd"]), int(g["sm"])) if g["sd"] else (int(g["td"]), _month_number(g["tm"]))
        )
        if g["ty"]:
            return date(int(g["ty"]), month, day)
        guess = date(today.year, month, day)
        return guess if guess <= today + timedelta(days=31) else guess.replace(year=today.year - 1)
    except ValueError:  # 31.02, 14.30 and the like are not dates
        return None


def _dates(text: str, today: date, birth_dates: tuple[date, ...]) -> str:
    def replace(m: re.Match[str]) -> str:
        day = _parse_date(m, today)
        if day is None:
            return m.group(0)
        before, after = text[max(0, m.start() - 30) : m.start()], text[m.end() : m.end() + 20]
        if day in birth_dates or _BIRTH_BEFORE.search(before) or _BIRTH_AFTER.search(after):
            return DATE
        return relative_day((today - day).days)

    return _DATE.sub(replace, text)


_PHONE = re.compile(
    r"(?<![\d+])(?:(?:\+7|8|7)[\s(-]*|\()?\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)"
)
_LONG_NUMBER = re.compile(r"(?<!\d)\d(?:[\s-]?\d){6,}(?!\d)")
_PATRONYMIC = rf"{_L}{_l}+(?:ович|евич|ич|овна|евна|ична|инична)(?:а|у|ем|ом|е|ы|ой)?"
_FIO = re.compile(rf"\b(?:{_L}{_l}+\s+){{1,2}}{_PATRONYMIC}\b")
_PATRONYMIC_RE = re.compile(rf"\b{_PATRONYMIC}\b")
_SURNAME = re.compile(
    rf"\b{_L}{_l}{{2,}}(?:ов|ев|ёв|ин|ын|ова|ева|ина|ына|ский|ская|цкий|цкая|ных|их)"
    rf"(?:а|у|ым|ом|е|ой|ую)?\b"
)
_INTRO = re.compile(rf"(?i:зовут|фамилия|фио|имя)[:\s]+((?:{_L}{_l}+\s*){{1,3}})")

_FIRST_NAME_RE = re.compile(r"\b(?:" + "|".join(stem_pattern(n) for n in FIRST_NAMES) + r")\b")


def _known_names(known: KnownPerson) -> list[tuple[re.Pattern[str], str]]:
    found: list[tuple[re.Pattern[str], str]] = []
    for name in known.names:
        name = name.strip()
        if len(name) < 2:
            continue
        stem = name[:-1] if len(name) >= 4 and name[-1].lower() in "аяйьоеиы" else name
        found.append((re.compile(rf"\b{re.escape(stem)}{_l}{{0,3}}\b", re.IGNORECASE), NAME))
    if known.username:
        login = re.escape(known.username.lstrip("@"))
        found.append((re.compile(rf"@?\b{login}\b", re.IGNORECASE), LOGIN))
    return found


def _known_numbers(known: KnownPerson) -> list[tuple[re.Pattern[str], str]]:
    found: list[tuple[re.Pattern[str], str]] = []
    if known.phone:
        digits = re.sub(r"\D", "", known.phone)[-10:]
        if digits:
            found.append((re.compile(r"[\s()-]*".join(digits)), PHONE))
    if known.chat_id is not None:
        found.append((re.compile(rf"(?<!\d){abs(known.chat_id)}(?!\d)"), NUMBER))
    return found


def mask(text: str, known: KnownPerson | None = None, today: date | None = None) -> str:
    """Replace personal data in ``text`` with placeholders; event dates become relative."""
    known = known or KnownPerson()
    today = today or date.today()
    text = _EMAIL.sub(EMAIL, text)
    text = _LOGIN.sub(LOGIN, text)
    for pattern, placeholder in _known_names(known):
        text = pattern.sub(placeholder, text)
    text = _INTRO.sub(lambda m: m.group(0).replace(m.group(1).strip(), NAME), text)
    text = _FIO.sub(NAME, text)
    text = _PATRONYMIC_RE.sub(NAME, text)
    text = _FIRST_NAME_RE.sub(NAME, text)
    text = _SURNAME.sub(NAME, text)
    text = _collapse(text)
    text = _dates(text, today, known.birth_dates)
    text = _BIRTH_YEAR.sub(DATE, text)
    text = _PHONE.sub(PHONE, text)
    text = _LONG_NUMBER.sub(NUMBER, text)
    for pattern, placeholder in _known_numbers(known):
        text = pattern.sub(placeholder, text)
    return text.strip()


def _collapse(text: str) -> str:
    return re.sub(r"\[ИМЯ\](?:\s+\[ИМЯ\])+", NAME, text)
