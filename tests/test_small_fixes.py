"""Small things from the second simulation run (cancel_by_day, certificate, tick, infant).

- «Спасибо, её не трогаем» about a booking is answered with one line, the booking is not
  shown once more.
- «Ваше обращение уже у администратора» is not said twice in a row: the second time the bot
  says what was passed and that it has nothing new to say.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

import pytest

from app.bot import texts
from app.flow.commands import keeps_booking, says_no_more
from app.flow.loader import KnowledgeBase
from tests.fake_deepseek import FakeDeepSeek
from tests.test_conversation import book_dislocation, bookings
from tests.test_manual_findings_3 import booked, when
from tests.test_sim_findings_2 import LIGAMENT, MENU, handed

KEEPS = (
    "Отлично, значит завтрашнюю оставляем, ничего не переношу. Спасибо!",
    "Отлично, спасибо. Значит на завтра запись на месте, её не трогаем. До свидания.",
    "запись оставьте как есть",
    "пусть запись остаётся",
    "ничего не отменяю",
)


@pytest.mark.parametrize("said", KEEPS)
async def test_keeping_a_booking_is_one_line(make_chat, kb: KnowledgeBase, said: str) -> None:
    assert keeps_booking(said)
    fake = FakeDeepSeek({"type": "menu_action", "action": "my_bookings"})
    chat = make_chat(assistant=fake.assistant(kb))
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    (booking,) = await bookings(chat.conversation.db)
    await chat.say(said)
    (only,) = chat.replies
    date, time = when(booking)
    assert only.text == texts.KEPT_WHEN.format(date=date, time=time)
    assert texts.MY_ONE not in chat.text and chat.labels() == MENU
    assert fake.requests == []  # the words decide, DeepSeek is not asked


async def test_keeping_one_of_several_names_none(make_chat) -> None:
    chat = await booked(make_chat, "Завтра")
    await chat.say("Отлично, значит завтрашнюю оставляем, ничего не переношу. Спасибо!")
    (only,) = chat.replies
    assert only.text == texts.KEPT and chat.labels() == MENU


@pytest.mark.parametrize(
    "said",
    ["отмените запись на завтра, а вторую оставьте", "перенесите запись", "оставьте отзыв",
     "ничего не болит", "спасибо", "не трогайте руку"],
)  # fmt: skip
def test_what_does_not_keep_a_booking(said: str) -> None:
    assert not keeps_booking(said)


async def test_without_a_booking_the_words_are_read_as_usual(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("запись оставьте как есть")
    assert texts.KEPT not in chat.text and "остаётся" not in chat.text


# --- «уже у администратора» is not said twice in a row -------------------------------------------


async def test_already_with_the_administrator_is_not_repeated(make_chat) -> None:
    chat = make_chat()
    await handed(chat)
    await chat.say("позовите администратора")  # something new: added to the request
    assert "Я добавил это к Вашему обращению № 1" in chat.text
    posted = len(chat.admin)
    await chat.say("позовите администратора")
    first = chat.text
    assert "Ваше обращение № 1 уже у администратора" in first
    await chat.say("позовите администратора")  # the same again: what was passed, nothing new
    second = chat.text
    assert second != first and "уже у администратора" not in second
    assert second.startswith("Администратору передано: Ваше сообщение и ответы на мои вопросы")
    assert "Нового сказать не могу" in second and chat.labels() == MENU
    await chat.say("позовите администратора")
    assert chat.text == first and len(chat.admin) == posted  # never the same line twice in a row


async def test_the_same_complaint_again_and_again(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek(LIGAMENT, LIGAMENT, LIGAMENT)
    chat = make_chat(assistant=fake.assistant(kb))
    await handed(chat)
    said = []
    for _ in range(3):
        await chat.say("Так вы меня запишете или нет? Связки же порваны, нога болит")
        said.append(chat.text)
        assert chat.text.startswith(texts.NOT_BOOKED_HERE)  # where to go — every time
    assert "уже у администратора" in said[0] and "уже у администратора" in said[2]
    assert "Администратору передано" in said[1] and "уже у администратора" not in said[1]


async def test_another_reply_in_between_starts_anew(make_chat) -> None:
    chat = make_chat()
    await handed(chat)
    await chat.say("позовите администратора")
    await chat.say("позовите администратора")
    assert "уже у администратора" in chat.text
    await chat.say("спасибо")
    await chat.say("позовите администратора")
    assert "уже у администратора" in chat.text  # the first time after something else


# --- after run 4 (not checked by a live run) ---------------------------------------------------
# questions: «а зачем переносить? мне 13 ноября в 11:00 подходит» showed the booking three
# times. criminal: «Нет. Всё, поеду сам» to «Могу я ещё чем-нибудь Вам помочь?» got the same
# question once more.


@pytest.mark.parametrize(
    "said",
    ["а зачем переносить? мне 13 ноября в 11:00 подходит. просто хотела убедиться, что запись "
     "на меня", "не надо ничего переносить", "переносить не хочу, меня всё устраивает"],
)  # fmt: skip
def test_no_move_wanted_keeps_the_booking(said: str) -> None:
    assert keeps_booking(said)


@pytest.mark.parametrize("said", ["а можно перенести на завтра?", "зачем отменили мою запись?"])
def test_a_move_or_a_cancel_is_not_keeping(said: str) -> None:
    assert not keeps_booking(said)


async def test_why_move_is_one_line_and_not_the_booking_again(make_chat) -> None:
    chat = make_chat()
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    (booking,) = await bookings(chat.conversation.db)
    date, time = when(booking)
    kept = texts.KEPT_WHEN.format(date=date, time=time)
    await chat.say("а зачем переносить? мне это время подходит, просто хотела убедиться")
    assert chat.text == kept
    await chat.say("а зачем переносить? мне это время подходит, я же говорю")
    assert chat.text != kept  # not the same line twice in a row


@pytest.mark.parametrize("said", ["нет", "Нет, всё.", "Нет. Всё, поеду сам.", "нет, спасибо"])
async def test_no_to_anything_else_is_a_goodbye(make_chat, kb: KnowledgeBase, said: str) -> None:
    chat = make_chat(assistant=FakeDeepSeek().assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_ASK)
    await chat.press(texts.MENU_HOME)
    assert chat.text == kb.common.more_help.text  # «Могу я ещё чем-нибудь Вам помочь?»
    await chat.say(said)
    assert chat.text == kb.common.goodbye.text and chat.labels() == MENU
    await chat.say(said)
    assert chat.text != kb.common.goodbye.text  # nor the goodbye twice in a row


@pytest.mark.parametrize("said", ["нет, запишите меня на завтра", "нет, а сколько стоит приём?"])
def test_no_with_a_request_is_not_the_end(said: str) -> None:
    assert not says_no_more(said)
