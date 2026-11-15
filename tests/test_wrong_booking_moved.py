"""A complaint about the son must not move the mother's booking.

Found on a phone (15.11): there is one booking — the mother, Sunday 15.11; «пока не записывай
мужа еще посмотрим» → «Ваша запись: …»; «Сын 8 лет упал с велосипеда сегодня, болит рука,
можно сегодня вечером?» → the time for tonight → «Готово, запись перенесена». The wish of the
message about the son was kept for the booking on the screen: «Перенести» led straight to the
time, and one more press moved the mother. The son was not booked.

The rules: a message with a complaint or with a new person is a new booking of that person,
from any screen, and its wish for a day and a time goes with it. A move and a cancellation
begin only with a button or with the words of a move and a cancellation; a wish without them
never touches a booking that exists. A message that names somebody who is not the patient of
a booking does not move or cancel it. Every screen of a move begins with whose booking it is.

The clinic clock is Sunday, 15 November 2026, 15:00.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.bot import render, texts
from app.flow.loader import KnowledgeBase
from tests.conftest import NOW
from tests.fake_deepseek import FakeDeepSeek
from tests.test_conversation import book_dislocation, bookings
from tests.test_intents import session
from tests.test_two_kids_dialog import ByTask

NOT_HUSBAND = "пока не записывай мужа еще посмотрим"
SON = "Сын 8 лет упал с велосипеда сегодня, болит рука, можно сегодня вечером?"
MINE = {"type": "menu_action", "action": "my_bookings"}
READINGS = {  # what DeepSeek may make of the message about the son
    "свои записи": MINE,
    "вопрос о расписании": {"type": "schedule_question"},
    "просьба записать": {"type": "menu_action", "action": "book"},
    "жалоба": {"type": "complaint", "area": "limb", "scenario": None, "facts": {},
               "candidates": ["dislocation", "fracture", "ligament_rupture"]},
}  # fmt: skip
HEADER = "Перенос записи: Петров И. С., вс 15.11, 18:00"


async def with_a_booking(make_chat, kb: KnowledgeBase, clock, *replies):
    clock.now = NOW + timedelta(days=3, hours=3)  # Sunday, 15:00
    fake = FakeDeepSeek(*replies) if replies else None
    chat = make_chat(assistant=fake.assistant(kb) if fake else None)
    await book_dislocation(chat, day="Сегодня")
    await chat.press(texts.PAYMENT_SELF)
    (booking,) = await bookings(chat.conversation.db)
    return chat, booking


@pytest.mark.parametrize("reading", READINGS)
async def test_the_message_about_the_son_begins_his_booking(
    make_chat, kb: KnowledgeBase, clock, reading: str
) -> None:
    chat, mother = await with_a_booking(make_chat, kb, clock, MINE, READINGS[reading], {}, {})
    await chat.say(NOT_HUSBAND)
    await chat.say(SON)
    state = await session(chat)
    assert state["reschedule_of"] is None and state["wizard"], chat.text
    assert state["for_whom"] == "child" and "Запись для взрослого" not in chat.text
    assert state["wish"]["period"] == "evening"  # the wish goes with the new booking
    assert "При переносе" not in chat.text and texts.MOVE not in chat.labels()
    (still,) = await bookings(chat.conversation.db)
    assert (still.start_at, still.status) == (mother.start_at, "active")


async def test_the_three_messages_from_the_phone(make_chat, kb: KnowledgeBase, clock) -> None:
    nothing = {"facts": {}, "unclear": True, "clarify": None, "crisis": False}
    clock.now = NOW + timedelta(days=3, hours=3)
    fake = ByTask(route=MINE, extract=nothing)
    chat = make_chat(assistant=fake.assistant(kb))
    await book_dislocation(chat, day="Сегодня")
    await chat.press(texts.PAYMENT_SELF)
    (mother,) = await bookings(chat.conversation.db)
    await chat.say(NOT_HUSBAND)
    await chat.say(SON)  # DeepSeek calls it «свои записи»: the code sees the complaint
    assert "📝 Запись для ребёнка" in chat.text and "вечером" in chat.text
    await chat.press("Похоже на перелом")
    await chat.press("Да, ребёнок 8 лет")  # the age of the message is confirmed, not asked
    for label in ("Бытовая травма", "Нет, не обращались"):  # «сегодня» is read by the code
        await chat.press(label)
    assert "Перенести её или записать ещё раз" not in chat.text  # another patient
    await chat.press(chat.nearest().label)
    await chat.press(texts.CONSENT_YES)
    await chat.say("Петров Егор Иванович, 02.02.2018")
    assert "Егор Иванович записан" in chat.text
    first, second = await bookings(chat.conversation.db)
    assert (first.id, first.start_at, first.status) == (mother.id, mother.start_at, "active")
    assert second.patient_id != first.patient_id and second.status == "active"


async def test_a_wish_without_the_words_of_a_move_is_not_kept_for_it(
    make_chat, kb: KnowledgeBase, clock
) -> None:
    chat, mother = await with_a_booking(make_chat, kb, clock, MINE)
    await chat.say("покажите мою запись, я сегодня вечером приду")  # a day and a time, no move
    card = chat.replies[-1]
    assert card.text.startswith(texts.MY_ONE) and "При переносе" not in card.text
    await chat.press(texts.MOVE)  # the button of the screen: a move, with no day chosen for it
    state = await session(chat)
    assert state["reschedule_of"] == mother.id and state["wish"] is None
    assert state["step"] == "days" and "Пожелание" not in chat.text


async def test_the_words_of_a_move_take_the_wish(make_chat, kb: KnowledgeBase, clock) -> None:
    chat, mother = await with_a_booking(make_chat, kb, clock)
    await chat.say("перенесите мою запись на сегодня вечером")
    state = await session(chat)
    assert state["reschedule_of"] == mother.id and state["wish"]["period"] == "evening"


@pytest.mark.parametrize(
    "said",
    ["перенесите запись сына на завтра", "отмените запись жены", "отмените запись дочки"],
)
async def test_a_booking_of_another_person_is_not_touched(
    make_chat, kb: KnowledgeBase, clock, said: str
) -> None:
    """The only booking is of Иван Сергеевич, an adult man: not the son's, not the wife's."""
    chat, mother = await with_a_booking(make_chat, kb, clock)
    await chat.say(said)
    state = await session(chat)
    assert state["reschedule_of"] is None and "Отменить запись на" not in chat.text
    assert texts.MY_ONE in chat.text  # what there is, with its buttons
    (still,) = await bookings(chat.conversation.db)
    assert (still.start_at, still.status) == (mother.start_at, "active")


async def test_the_husbands_booking_may_be_his(make_chat, kb: KnowledgeBase, clock) -> None:
    chat, _ = await with_a_booking(make_chat, kb, clock)
    await chat.say("отмените запись мужа")  # an adult man: it may be his
    assert "Отменить запись на 15 ноября" in chat.text


async def test_every_screen_of_a_move_says_whose_booking_it_is(
    make_chat, kb: KnowledgeBase, clock
) -> None:
    chat, mother = await with_a_booking(make_chat, kb, clock)
    await chat.press(texts.MENU_MY)
    await chat.press(texts.MOVE)
    assert chat.replies[-1].text.split("\n")[0] == HEADER  # the day picker
    await chat.pick_day("Сегодня")
    assert chat.replies[-1].text.split("\n")[0] == HEADER  # the parts of the day or the times
    if chat.periods():
        await chat.pick_period("19:00")
        assert chat.replies[-1].text.split("\n")[0] == HEADER
    await chat.press("19:00")
    assert chat.text.startswith("Готово, запись Ивана Сергеевича перенесена с 18:00 на 19:00.")
    (moved,) = await bookings(chat.conversation.db)
    assert moved.id == mother.id and moved.start_at == mother.start_at + timedelta(hours=1)


@pytest.mark.parametrize(
    ("first", "patronymic", "whose"),
    [
        ("Лена", "Владимировна", "Лены Владимировны"),
        ("Иван", "Сергеевич", "Ивана Сергеевича"),
        ("Мария", "Кирилловна", "Марии Кирилловны"),
        ("Игорь", "Семёнович", "Игоря Семёновича"),
        ("Андрей", "Ильич", "Андрея Ильича"),
        ("Ольга", "Никитична", "Ольги Никитичны"),
        ("Илья", None, "Ильи"),
        ("Любовь", "Тимофеевна", "Любови Тимофеевны"),
        ("Пётр", "Павлович", "Петра Павловича"),
        ("Лев", None, "Льва"),
        ("Наталья", None, "Натальи"),
        ("Ли", None, "Ли"),  # no rule: as it is
    ],
)
def test_whose_booking_in_the_genitive(first: str, patronymic: str | None, whose: str) -> None:
    assert render.whose(first, patronymic) == whose
