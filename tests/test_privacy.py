"""Case 19: no full name, phone, birth date, username or chat id ever reaches DeepSeek."""

from __future__ import annotations

import json
import logging
from datetime import date

import pytest

from app.flow.loader import KnowledgeBase
from app.llm.masking import KnownPerson, mask
from tests.fake_deepseek import FakeDeepSeek

PATIENT = KnownPerson(
    names=("Петров", "Иван", "Сергеевич", "Петрова", "Мария", "Олеговна"),
    phone="+7 912 345-67-89",
    username="ivan_petrov85",
    birth_dates=(date(1985, 3, 12),),
    chat_id=482913577,
)
SECRETS = (
    "Петров", "Иван", "Сергеевич", "Мари", "Олеговн", "912", "345-67-89", "9123456789",
    "12.03.1985", "1985", "ivan_petrov85", "482913577", "petrov@mail.ru", "Кузьмин",
)  # fmt: skip
MESSAGES = (
    "Здравствуйте, я Петров Иван Сергеевич, 12.03.1985 г.р., вчера упал дома, вывих плеча",
    "Это Мария Олеговна, мама Ивана. Телефон +7 (912) 345-67-89, почта petrov@mail.ru",
    "Мой ник в телеграме @ivan_petrov85, chat id 482913577, к врачу не обращался",
    "Петрову Ивану записали к доктору Кузьмину? Перезвоните 8 912 345 67 89",
)


async def test_requests_to_deepseek_contain_no_personal_data(
    kb: KnowledgeBase, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    fake = FakeDeepSeek(*([{"intent": "complaint", "scenario": "dislocation", "facts": {}}] * 4),
                        *([{"facts": {}, "unclear": True}] * 4),
                        *([{"found": False}] * 4))  # fmt: skip
    assistant = fake.assistant(kb)
    sc = kb.scenario("dislocation")
    for text in MESSAGES:
        await assistant.route(text, PATIENT)
    for text in MESSAGES:
        await assistant.extract(sc, sc.question("seen_doctor"), text, PATIENT)
    for text in MESSAGES:
        await assistant.answer(text, PATIENT)

    assert len(fake.requests) == 12
    # The system prompt is static (rules + kb, with the clinic's doctors); everything that
    # depends on the patient follows it.
    systems = {assistant.dialog_system, assistant.answer_system}
    assert all(r["messages"][0]["content"] in systems for r in fake.requests)
    sent = json.dumps([r["messages"][1:] for r in fake.requests], ensure_ascii=False)
    for secret in SECRETS:
        assert secret not in sent, secret
        assert secret not in caplog.text, secret
    # the medical meaning survives masking
    assert "вывих плеча" in sent and "к врачу не обращался" in sent


TODAY = date(2026, 11, 12)


def test_unknown_names_and_numbers_are_masked_too() -> None:
    text = mask(
        "Дочь Маша Козлова упала 10 ноября, звоните 89221234567, СНИЛС 123-456-789 01", today=TODAY
    )
    assert text == "Дочь [ИМЯ] упала 2 дня назад, звоните [ТЕЛЕФОН], СНИЛС [НОМЕР]"


@pytest.mark.parametrize(
    ("text", "masked"),
    [
        ("Меня зовут Анна Сергеевна", "Меня зовут [ИМЯ]"),
        ("ФИО: Смирнов Олег", "ФИО: [ИМЯ]"),
        ("пишите на test.user@ya.ru", "пишите на [EMAIL]"),
        ("мой t.me/somebody", "мой [ЛОГИН]"),
        ("родился 1990 г.р.", "родился [ДАТА]"),
        ("паспорт 6512 345678", "паспорт [НОМЕР]"),
        # birth dates and dates next to a name are masked
        ("Петров Иван, 12.03.1985", "[ИМЯ], [ДАТА]"),
        ("дата рождения 05.06.2018, упал вчера", "дата рождения [ДАТА], упал вчера"),
        ("дочка 12 марта 1990 года рождения", "дочка [ДАТА] рождения"),
        # dates of events become relative time
        ("упал 10 ноября", "упал 2 дня назад"),
        ("упал 10.11.2026 на работе", "упал 2 дня назад на работе"),
        ("упал 2026-11-11", "упал вчера"),
        ("ожог 1 октября", "ожог 42 дня назад"),
        ("20 декабря упал", "10 месяцев назад упал"),
        ("запишите на 15.11", "запишите на через 3 дня"),
        # not dates
        ("можно в 14.30?", "можно в 14.30?"),
        ("ребёнку 1.5 года", "ребёнку 1.5 года"),
        ("1-2 дня назад", "1-2 дня назад"),
    ],
)
def test_mask_patterns(text: str, masked: str) -> None:
    assert mask(text, today=TODAY) == masked


def test_known_birth_date_is_masked_anywhere() -> None:
    known = KnownPerson(birth_dates=(date(1985, 3, 12),))
    assert mask("мне 12.03.1985 исполнилось", known, TODAY) == "мне [ДАТА] исполнилось"


@pytest.mark.parametrize(
    "text",
    [
        "Вчера подвернул ногу на тренировке, отёк",
        "Ребёнку 4 месяца, упал с кровати",
        "Кровь хлещет, не останавливается",
        "Где Шейнкмана 113/2? Можно к 14.30?",
        "1,5 года ребёнку, ожог кипятком 10 дней назад",
        "Укусила собака, обработали перекисью",
    ],
)
def test_complaints_without_personal_data_stay_intact(text: str) -> None:
    assert mask(text, today=TODAY) == text
