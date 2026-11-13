"""Menu commands typed as text: «когда у меня приём» opens «Мои записи», as the button does.

Checked by code before DeepSeek; phrasings this list misses come back from route as the
``menu_action`` type. Only unambiguous phrasings: «записаться» alone is a command, but
«хочу записаться, сломал руку» is a complaint and goes to route.
"""

from __future__ import annotations

import re
from typing import Literal

MenuAction = Literal["book", "my_bookings", "ask", "admin", "forget", "about_bot"]

_BOOKING = r"(?:запис\w*|при[её]м\w*|визит\w*)"
# Requests, not stories: «отмени запись», not «приём у врача отменили».
_CANCEL = r"(?:отмени|отмените|отменить|отменю|отмена)"
_MOVE = r"(?:перенеси|перенесите|перенести|перенос|поменяй|поменяйте|поменять|изменить|измените)"
_PERSON = r"(?:администратор\w*|админ\w*|оператор\w*|жив\w+\s+человек\w*)"
_CALL = (
    r"(?:позови|позовите|позвать|соедини|соедините|соединить|переключи|переключите|свяжите|"
    r"дайте|нужен|нужна|поговорить)"
)

COMMANDS: tuple[tuple[MenuAction, re.Pattern[str]], ...] = (
    # «Напомни про мои записи», «когда у меня приём», «моя запись», «на когда я записан»
    ("my_bookings", re.compile(rf"\bмо[йяеиюё]\w*\s+{_BOOKING}")),
    ("my_bookings", re.compile(rf"\bкогда\b.*\bу\s+меня\b.*\b{_BOOKING}")),
    ("my_bookings", re.compile(r"\bкогда\b.*\b(?:я|мы)\s+записан\w*")),
    ("my_bookings", re.compile(rf"\bнапомни\w*\b.*\b{_BOOKING}")),
    # «Отмени запись», «перенеси запись на завтра», «запись нужно перенести»
    ("my_bookings", re.compile(rf"\b(?:{_CANCEL}|{_MOVE})\b.*\b{_BOOKING}")),
    ("my_bookings", re.compile(rf"\b{_BOOKING}\b.*\b(?:{_CANCEL}|{_MOVE})\b")),
    # «Позови администратора», «соедините с оператором», «нужен живой человек»
    ("admin", re.compile(rf"\b{_CALL}\b.*\b{_PERSON}")),
    ("admin", re.compile(rf"^{_PERSON}$")),
    # the whole message is «записаться» — nothing about an injury
    (
        "book",
        re.compile(
            r"^(?:я\s+)?(?:хочу|хотел\w*|можно|нужно|надо|как)?\s*(?:бы\s+)?"
            r"(?:записаться|записать\s+меня|запишите(?:\s+меня)?|запиши(?:\s+меня)?|запись)"
            r"(?:\s+(?:к\s+вам|в\s+травмпункт|на\s+при[её]м|к\s+врачу|к\s+травматологу))?"
            r"(?:\s+пожалуйста)?$"
        ),
    ),
    # «Удалите мои данные» — the /forget confirmation
    (
        "forget",
        re.compile(
            r"\b(?:удал\w*|сотр\w*|стер\w*|отзыва\w*|отозва\w*)\b.*\b(?:данн\w*|согласи\w*)"
        ),
    ),
    # «Ты бот?», «ты человек?», «с кем я говорю»
    (
        "about_bot",
        re.compile(r"^(?:а\s+)?(?:ты|вы)\s+(?:бот|человек|робот|живой|живая|нейросеть)\b"),
    ),
    ("about_bot", re.compile(r"\bс\s+кем\s+я\s+(?:говорю|разговариваю|общаюсь|переписываюсь)\b")),
    ("ask", re.compile(r"^(?:у\s+меня\s+)?(?:есть\s+)?(?:хочу\s+задать\s+|задать\s+)?вопрос$")),
)


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", text.lower().replace("ё", "е")).split())


def find_command(text: str) -> MenuAction | None:
    t = normalize(text)
    for action, pattern in COMMANDS:
        if pattern.search(t):
            return action
    return None
