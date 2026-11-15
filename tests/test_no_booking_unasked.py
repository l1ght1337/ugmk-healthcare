"""A booking is not begun for a patient who did not ask for one (the customer, 15.11.2026).

Run 7, questions, turns 19–23: the patient has a visit in an hour and a request with the
administrators; «Я успею до 20:00 дождаться или уже самой ехать? А то у меня нога болит,
ждать некогда» opened step 1 of a new booking five times in a row («зачем вы опять всё
сначала?»). The rule of the round before — a complaint by the word lists is a new booking —
took the words «нога болит» for one.

A patient with a visit ahead, the words of a complaint in the message, no request to book
and nobody else named: no new booking is opened. «Мне самой ехать или ждать звонка?» with a
visit ahead is answered by the code: «Запись действует: сегодня, 13:30. Приходите к этому
времени; ответ администратора на запись не влияет.»

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

import pytest

from app.bot import texts
from app.flow.loader import KnowledgeBase
from tests.fake_deepseek import FakeDeepSeek
from tests.test_conversation import book_dislocation, bookings
from tests.test_intents import session

HOLDS = (
    "Запись действует: сегодня, 13:30. Приходите к этому времени; ответ администратора на "
    "запись не влияет."
)
GO_OR_WAIT = (
    "А сейчас-то сколько? Я успею до 20:00 дождаться или уже самой ехать? А то у меня нога "
    "болит, ждать некогда"
)
READINGS = {  # what DeepSeek may make of a message with «нога болит» in it
    "жалоба": {"type": "complaint", "area": "limb", "scenario": None, "facts": {},
               "candidates": ["dislocation", "fracture"], "question": True},
    "жалоба со сценарием": {"type": "complaint", "area": "limb", "scenario": "dislocation",
                            "candidates": ["dislocation"], "facts": {}},
    "просьба записать": {"type": "menu_action", "action": "book"},
    "вопрос о расписании": {"type": "schedule_question"},
}  # fmt: skip


async def booked(make_chat, kb: KnowledgeBase, *replies, day: str = "Сегодня", handed=False):
    """The patient has a visit ahead (today, 13:30) — and, if asked, a request with the
    administrators."""
    fake = FakeDeepSeek(*replies)
    chat = make_chat(assistant=fake.assistant(kb), admin_chat_id=-100)
    await book_dislocation(chat, day=day)
    await chat.press(texts.PAYMENT_SELF)
    if handed:
        await chat.press(texts.MENU_ADMIN)
        assert "обращение № 1" in chat.text
    return chat, fake


@pytest.mark.parametrize(
    ("asked", "handed"),
    [
        (GO_OR_WAIT, True),  # run 7, word for word: the request is with the administrators
        ("мне самой ехать или ждать звонка?", False),
        ("Администратор так и не позвонил. Мне что, самой ехать или ждать звонка?", True),
    ],
)
async def test_go_or_wait_with_a_visit_ahead_is_answered_by_the_code(
    make_chat, kb: KnowledgeBase, asked: str, handed: bool
) -> None:
    chat, fake = await booked(make_chat, kb, READINGS["жалоба"], handed=handed)
    await chat.say(asked)
    assert chat.replies[0].text == HOLDS, chat.text
    state = await session(chat)
    assert not state["wizard"] and state["step"] == "idle" and fake.requests == []
    assert len(await bookings(chat.conversation.db)) == 1


async def test_the_day_of_the_visit_is_said_as_it_is(make_chat, kb: KnowledgeBase) -> None:
    chat, _ = await booked(make_chat, kb, day="Завтра")
    await chat.say("мне самой ехать или ждать звонка?")
    assert chat.replies[0].text.startswith("Запись действует: завтра, 11:00.")


@pytest.mark.parametrize("reading", READINGS)
@pytest.mark.parametrize(
    "said",
    [
        "а то нога болит, а до четверга ещё дожить надо",
        "нога всё ещё болит, опухла сильнее",
    ],
)
async def test_the_words_of_a_complaint_do_not_open_a_booking(
    make_chat, kb: KnowledgeBase, reading: str, said: str
) -> None:
    chat, _ = await booked(make_chat, kb, READINGS[reading], {}, {})
    await chat.say(said)
    state = await session(chat)
    assert not state["wizard"] and "Шаг 1 из 5" not in chat.text, chat.text
    assert len(await bookings(chat.conversation.db)) == 1


async def test_a_request_to_book_opens_a_booking(make_chat, kb: KnowledgeBase) -> None:
    chat, _ = await booked(make_chat, kb, READINGS["жалоба со сценарием"], {}, {})
    await chat.say("запишите меня ещё раз, нога болит")
    assert (await session(chat))["wizard"]


async def test_somebody_else_with_a_complaint_is_booked(make_chat, kb: KnowledgeBase) -> None:
    chat, _ = await booked(make_chat, kb, READINGS["жалоба"], {}, {})
    await chat.say("у сына тоже нога болит, упал вчера")
    state = await session(chat)
    assert state["wizard"] and state["for_whom"] == "child"


async def test_with_no_visit_ahead_a_complaint_is_a_booking_as_before(
    make_chat, kb: KnowledgeBase
) -> None:
    chat = make_chat(assistant=FakeDeepSeek(READINGS["жалоба"]).assistant(kb))
    await chat.start()
    await chat.say("нога болит, подвернул вчера")
    assert (await session(chat))["wizard"]
    await chat.press(texts.MENU)
    await chat.say("мне самой ехать или ждать звонка?")
    assert "Запись действует" not in chat.text


# --- «запись останется?» — the same answer (it never came in four runs) --------------------------

STILL_HOLDS = [
    "а если до 13:30 не позвонит, мне всё равно ехать или запись отменят?",  # run 8, questions
    "если администратор не позвонит до 13:30, запись останется или её снимут? мне же ехать",
    "запись останется?",
    "её не снимут?",
    "запись в силе?",
    "так мне приходить?",
    "приезжать к 13:30?",
]


@pytest.mark.parametrize("asked", STILL_HOLDS)
async def test_does_the_visit_hold_is_answered_by_the_code(
    make_chat, kb: KnowledgeBase, asked: str
) -> None:
    chat, fake = await booked(make_chat, kb, READINGS["жалоба"])
    await chat.say(asked)
    assert chat.replies[0].text == HOLDS, chat.text
    assert fake.requests == [] and len(await bookings(chat.conversation.db)) == 1


@pytest.mark.parametrize("said", ["запись останется, спасибо", "приезжать к 13:30, понял"])
async def test_not_a_question_is_not_answered_so(make_chat, kb: KnowledgeBase, said: str) -> None:
    chat, _ = await booked(make_chat, kb, {"type": "offtopic"})
    await chat.say(said)
    assert "Запись действует" not in chat.text


async def test_with_no_visit_the_words_ask_nothing(make_chat, kb: KnowledgeBase) -> None:
    chat = make_chat(
        assistant=FakeDeepSeek({"type": "clinic_question"}, {"found": False}).assistant(kb)
    )
    await chat.start()
    await chat.say("так мне приходить?")
    assert "Запись действует" not in chat.text
