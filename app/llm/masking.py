"""Mask personal data before any text goes to DeepSeek (CLAUDE.md, rule 4).

Only the complaint or question text is sent, and it passes through ``mask`` first:
names, phones, dates, e-mails, logins and long numbers (documents, chat ids) become
placeholders. Personal data the bot already holds for the chat (``KnownPerson``) is
masked in any grammatical form.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

NAME = "[ИМЯ]"
PHONE = "[ТЕЛЕФОН]"
DATE = "[ДАТА]"
EMAIL = "[EMAIL]"
NUMBER = "[НОМЕР]"
LOGIN = "[ЛОГИН]"
PLACEHOLDERS = (NAME, PHONE, DATE, EMAIL, NUMBER, LOGIN)


@dataclass(frozen=True)
class KnownPerson:
    """Personal data of the chat's patient that must never reach the LLM."""

    names: tuple[str, ...] = ()  # last, first, patronymic — patient and applicant
    phone: str | None = None
    username: str | None = None
    birth_date: date | None = None
    chat_id: int | None = None


_MONTHS = (
    r"(?:январ[яеь]|феврал[яеь]|марта?|апрел[яеь]|ма[яй]|июн[яеь]|июл[яеь]|августа?|"
    r"сентябр[яеь]|октябр[яеь]|ноябр[яеь]|декабр[яеь])"
)
_L = r"[А-ЯЁA-Z]"  # capital letter
_l = r"[а-яёa-z]"

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LOGIN = re.compile(r"(?:https?://)?t\.me/\w+|(?<![\w@])@\w{3,}")
_DATES = [
    re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b"),
    re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b"),
    re.compile(r"\b(?:[0-2]?\d|3[01])\.(?:0[1-9]|1[0-2])\b"),
    re.compile(rf"\b\d{{1,2}}\s+{_MONTHS}(?:\s+\d{{4}}(?:\s*(?:г\.|года|г\b))?)?", re.IGNORECASE),
    re.compile(rf"\b{_MONTHS}\s+\d{{4}}\b", re.IGNORECASE),
    re.compile(r"\b(?:19|20)\d{2}\s*(?:г\.?\s*р\.?|года рождения|г\.р)", re.IGNORECASE),
]
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

# Common first names and short forms (nominative). Inflected forms are matched by stem.
_FIRST_NAMES_TEXT = """
Александр Саша Алексей Алёша Лёша Андрей Антон Артём Артемий Борис Вадим Валентин Валерий
Василий Вася Виктор Витя Виталий Владимир Вова Володя Владислав Влад Всеволод Вячеслав Георгий
Гоша Глеб Григорий Гриша Даниил Данил Даня Денис Дима Дмитрий Евгений Женя Егор Иван Ваня Игорь
Илья Кирилл Константин Костя Лев Лёва Леонид Макар Максим Макс Марк Матвей Михаил Миша Никита
Николай Коля Олег Павел Паша Пётр Петя Роман Рома Руслан Семён Сергей Серёжа Станислав Степан
Тимофей Тимур Тимоша Фёдор Федя Юрий Юра Ярослав Ян Анна Аня Алина Алиса Алла Анастасия Настя
Ангелина Валентина Валерия Лера Варвара Варя Вера Вероника Виктория Вика Галина Галя Дарья Даша
Диана Ева Евгения Екатерина Катя Елена Лена Елизавета Лиза Жанна Зоя Злата Инна Ирина Ира Карина
Кира Кристина Ксения Ксюша Лариса Лидия Любовь Люба Людмила Люда Маргарита Рита Марина Мария
Маша Милана Надежда Надя Наталья Наташа Нина Оксана Ольга Оля Полина Поля Светлана Света София
Соня Софья Тамара Татьяна Таня Ульяна Юлия Юля Яна
"""
_FIRST_NAMES = _FIRST_NAMES_TEXT.split()


def _stem_pattern(name: str) -> str:
    """A name and its case forms: Маша/Маши/Машей, Иван/Ивана/Иваном, Юрий/Юрия."""
    word = name.replace("ё", "[её]").replace("Ё", "[ЕЁ]")
    if name[-1] in "ая":
        return word[:-1] + r"(?:а|я|ы|и|е|у|ю|ой|ей|ою|ею)"
    if name[-1] in "йь":
        return word[:-1] + r"(?:й|ь|я|ю|ем|ём|е|и)"
    return word + r"(?:а|у|ом|ем|е|ы|ой)?"


_FIRST_NAME_RE = re.compile(r"\b(?:" + "|".join(_stem_pattern(n) for n in _FIRST_NAMES) + r")\b")


def _known_patterns(known: KnownPerson) -> list[tuple[re.Pattern[str], str]]:
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
    if known.phone:
        digits = re.sub(r"\D", "", known.phone)[-10:]
        if digits:
            found.append((re.compile(r"[\s()-]*".join(digits)), PHONE))
    if known.chat_id is not None:
        found.append((re.compile(rf"(?<!\d){abs(known.chat_id)}(?!\d)"), NUMBER))
    if known.birth_date is not None:
        d = known.birth_date
        year = rf"(?:{d.year}|{d.year % 100:02d})"
        found.append((re.compile(rf"\b0?{d.day}[./-]0?{d.month}[./-]{year}\b"), DATE))
    return found


def mask(text: str, known: KnownPerson | None = None) -> str:
    """Replace personal data in ``text`` with placeholders."""
    text = _EMAIL.sub(EMAIL, text)
    text = _LOGIN.sub(LOGIN, text)
    for pattern in _DATES:
        text = pattern.sub(DATE, text)
    text = _PHONE.sub(PHONE, text)
    text = _LONG_NUMBER.sub(NUMBER, text)
    if known is not None:
        for pattern, placeholder in _known_patterns(known):
            text = pattern.sub(placeholder, text)
    text = _INTRO.sub(lambda m: m.group(0).replace(m.group(1).strip(), NAME), text)
    text = _FIO.sub(NAME, text)
    text = _PATRONYMIC_RE.sub(NAME, text)
    text = _FIRST_NAME_RE.sub(NAME, text)
    text = _SURNAME.sub(NAME, text)
    text = re.sub(r"\[ИМЯ\](?:\s+\[ИМЯ\])+", NAME, text)
    return text.strip()
