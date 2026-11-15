"""A booking is not replaced with a request to the administrators behind the patient's back.

Full run 6, `questions`: the patient chose Saturday 11:30 and on the consent step asked three
times «кто мне будет звонить — администратор?». The third time DeepSeek called it a request
for an administrator; the bot took the phone and passed a request instead of the booking. The
patient believed she was booked and five times in a row read «У Вас нет предстоящих записей».

A request to the administrators begins with its button or with a plain request — never with
a question. When it is made in the middle of a booking, the bot says that the booking is not
made, names the day and the time it was for, passes them to the administrators and offers
«Продолжить запись». «Мои записи» with no booking and that request open says the same.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

import pytest

from app.bot import texts
from app.flow.loader import KnowledgeBase
from tests.chat_harness import Chat
from tests.fake_deepseek import FakeDeepSeek
from tests.test_conversation import bookings
from tests.test_intents import session

ASKS = (
    "А звонить мне кто будет — администратор или врач? И зачем вам телефон, если запись уже есть?"
)
ADMIN = {"type": "menu_action", "action": "admin"}
NOT_BOOKED = "Запись на чт, 12.11, "


async def to_consent(chat: Chat) -> str:
    """A booking with its time chosen: the consent step. Returns the time."""
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")
    await chat.press(texts.AUDIENCE_SELF)
    for label in ("Бытовая травма", "В течение 7 дней", "Нет, не обращались"):
        await chat.press(label)
    label = chat.nearest().label  # «Ближайшее: сегодня, 13:30»
    await chat.press(label)
    assert (await session(chat))["step"] == "consent"
    return label[-5:]


async def test_a_question_about_the_administrator_is_not_a_request(
    make_chat, kb: KnowledgeBase
) -> None:
    fake = FakeDeepSeek(ADMIN, {"found": False}, ADMIN, {"found": False}, ADMIN, {"found": False})
    chat = make_chat(assistant=fake.assistant(kb))
    await to_consent(chat)
    for n in range(3):  # asked three times, as in the run
        await chat.say(ASKS)
        state = await session(chat)
        assert state["step"] == "consent" and state["resume"] == "identify", chat.text
        assert "Чтобы администратор мог с Вами связаться" not in chat.text
        if n < 2:
            assert texts.CONSENT_YES in chat.labels()
        else:  # the same answer a third time in a row: one line that offers the administrator
            assert [r.text for r in chat.replies] == [texts.STUCK_ASK]  # tests/test_circles.py
    assert chat.admin == [] and await bookings(chat.conversation.db) == []


@pytest.mark.parametrize("how", ["позовите администратора", "button"])
async def test_a_request_in_the_middle_of_a_booking_says_it_is_not_made(
    make_chat, how: str
) -> None:
    chat = make_chat(admin_chat_id=-100)
    time = await to_consent(chat)
    if how == "button":
        await chat.data("m:admin")
    else:
        await chat.say(how)
    await chat.press(texts.CONSENT_YES)  # the consent of the request
    await chat.contact()
    said = chat.replies[-1]
    assert said.text.startswith(f"{NOT_BOOKED}{time} не оформлена.")
    assert "Я передал обращение № 1 администратору" in said.text
    assert chat.labels()[0] == texts.RESUME  # the way back to the booking
    (post,) = chat.admin  # the day and the time go to the administrators
    assert f"Выбранное время: чт, 12.11, {time}" in post.text and "не оформлена" in post.text
    assert await bookings(chat.conversation.db) == []

    await chat.press(texts.MENU_MY)  # not «У Вас нет предстоящих записей»
    assert texts.MY_EMPTY not in chat.text
    assert chat.text.startswith(f"{NOT_BOOKED}{time} не оформлена.")
    assert "обращение № 1" in chat.text and chat.labels()[0] == texts.RESUME
    await chat.say("а почему в моих записях пусто, если я записалась?")
    assert texts.MY_EMPTY not in chat.text and chat.labels()[0] == texts.RESUME

    await chat.press(texts.RESUME)  # the booking goes on from where it was left
    assert (await session(chat))["step"] in ("consent", "name")
    if texts.CONSENT_YES in chat.labels():
        await chat.press(texts.CONSENT_YES)
    await chat.say("Петров Иван Сергеевич, 12.03.1985")
    assert "Иван Сергеевич записан" in chat.text
    await chat.press(texts.PAYMENT_SELF)
    await chat.press(texts.MENU_MY)
    assert chat.text.startswith(texts.MY_ONE) and "не оформлена" not in chat.text


async def test_a_request_with_no_booking_under_way_says_nothing_about_one(make_chat) -> None:
    chat = make_chat(admin_chat_id=-100)
    await chat.start()
    await chat.press(texts.MENU_ADMIN)
    await chat.press(texts.CONSENT_YES)
    await chat.contact()
    assert "не оформлена" not in chat.text and texts.RESUME not in chat.labels()
    await chat.press(texts.MENU_MY)
    assert chat.text == texts.MY_EMPTY
