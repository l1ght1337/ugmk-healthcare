"""A button's label typed as text (app/bot/typed.py): one button and only one."""

from __future__ import annotations

import pytest

from app.bot.typed import pressed

ASSAULT = [
    ("Бытовая травма", "q:0"),
    ("Нападение, драка, сбила машина", "q:1"),
    ("Назад", "w:back"),
]
SEEN = [
    ("Нет, не обращались", "q:0"), ("Да, в вашем травмпункте", "q:1"),
    ("Да, к другому врачу", "q:2"), ("Назад", "w:back"),
]  # fmt: skip
FIRST = [("Да, в вашем травмпункте", "q:0"), ("Нет, в другом месте", "q:1")]
TOPICS = [
    ("🦴 Рука или нога", "t:limb"), ("🩹 Рана, ожог, укус", "t:wounds"),
    ("👶 Ребёнок до 6 месяцев", "t:infant"), ("❔ Другое", "t:other"),
]  # fmt: skip
DAYS = [
    ("Ближайшее: сегодня, 13:30", "s:1"), ("Сегодня, 12.11", "d:1"), ("Завтра, 13.11", "d:2"),
    ("Сб, 14.11", "d:3"), ("Позвать администратора", "m:admin"), ("Назад", "w:back"),
]  # fmt: skip
PERIODS = [("Утро 8–12", "pr:m"), ("День 12–18", "pr:d"), ("Вечер 18–23", "pr:e")]
TIMES = [("13:30", "s:a"), ("14:00", "s:b"), ("14:30", "s:c")]
CONSENT = [("Согласен", "c:yes"), ("Не согласен", "c:no"), ("Назад", "w:back")]
OFFER = [("Передать администратору", "h:yes"), ("Не нужно", "h:no")]
WHO = [("Для себя", "a:adult"), ("Для ребёнка (до 18 лет)", "a:child"), ("Назад", "w:back")]


@pytest.mark.parametrize(
    ("text", "buttons", "mode", "data"),
    [
        ("Бытовая травма", ASSAULT, "answer", "q:0"),
        ("бытовая", ASSAULT, "answer", "q:0"),
        ("назад", ASSAULT, "answer", "w:back"),
        ("нападение", ASSAULT, "answer", None),  # one word of four: DeepSeek reads it
        ("да", FIRST, "answer", "q:0"),
        ("нет", FIRST, "answer", "q:1"),
        ("нет", SEEN, "answer", "q:0"),
        ("да", SEEN, "answer", None),  # two buttons start with «Да»: which one?
        ("к врачу", SEEN, "answer", None),  # the branch is not guessed
        ("да", ASSAULT, "answer", None),
        ("другое", TOPICS, "exact", "t:other"),
        ("рука или нога", TOPICS, "exact", "t:limb"),
        ("ребёнок", TOPICS, "exact", None),  # not «Ребёнок до 6 месяцев»
        ("рука", TOPICS, "exact", None),
        ("завтра", DAYS, "choice", "d:2"),
        ("сегодня", DAYS, "choice", "d:1"),
        ("ближайшее", DAYS, "choice", "s:1"),
        ("сб", DAYS, "choice", None),  # too short to be sure
        ("вечер", PERIODS, "choice", "pr:e"),
        ("утро", PERIODS, "choice", "pr:m"),
        ("14:30", TIMES, "choice", "s:c"),
        ("14.30", TIMES, "choice", "s:c"),
        ("15:00", TIMES, "choice", None),
        ("другой день", PERIODS, "choice", None),  # not «День 12–18»
        ("день", PERIODS, "choice", "pr:d"),
        ("согласен", CONSENT, "choice", "c:yes"),
        ("согласна", CONSENT, "choice", "c:yes"),
        ("не согласен", CONSENT, "choice", "c:no"),
        ("да", CONSENT, "choice", None),  # consent is «Согласен», not a bare «да»
        ("нет", CONSENT, "choice", None),
        ("да", OFFER, "choice", "h:yes"),
        ("нет", OFFER, "choice", "h:no"),
        ("не нужно", OFFER, "choice", "h:no"),
        ("для себя", WHO, "choice", "a:adult"),
        ("для ребёнка", WHO, "choice", "a:child"),
        ("", WHO, "choice", None),
        ("упал вчера на руку", ASSAULT, "answer", None),
    ],
)
def test_typed_label(text: str, buttons: list, mode: str, data: str | None) -> None:
    assert pressed(text, buttons, mode=mode) == data, text


def test_a_number_typed_with_the_word_must_be_on_the_label() -> None:
    """«Завтра в 3» is a wish for a time, not the button «Завтра, 13.11» (14.11, item 4)."""
    days = [("Сегодня, 12.11", "d:1"), ("Завтра, 13.11", "d:2"), ("Сб, 14.11", "d:3")]
    assert pressed("завтра", days) == "d:2"
    assert pressed("завтра в 3", days) is None
    assert pressed("завтра в 15", days) is None
    periods = [("Утро 8–12", "pr:m"), ("День 12–18", "pr:d")]
    assert pressed("день", periods) == "pr:d"
    assert pressed("день в 3", periods) is None
