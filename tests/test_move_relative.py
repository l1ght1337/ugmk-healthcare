"""«Пораньше», «попозже» at a move are a wish relative to the visit (the customer, 15.11.2026).

Run 9, questions: a visit today at 13:30; «А перенести-то на пораньше можно?» opened the move
with no wish line and «Ближайшее: сегодня, 14:30» on top — pressed, and the visit went an
hour later. The patient also took «четверг» for another day: it was today.

«Пораньше», «раньше», «попозже», «позже» with no time named: only the times earlier (or
later) than the visit are offered, and «Ближайшее» is one of them or is not shown. No such
time that day — said plainly: «Раньше 13:30 сегодня свободного времени нет.» with «Другой
день» and «Оставить как есть». «Ваша запись» and the header of a move name the day in words
when it is today or tomorrow: «сегодня, 12 ноября, 13:30».

The clinic clock is Thursday, 12 November 2026, 12:00: the first free time today is 13:30,
tomorrow by day — 11:00.
"""

from __future__ import annotations

import pytest

from app.bot import texts
from app.flow.loader import KnowledgeBase
from tests.fake_deepseek import FakeDeepSeek
from tests.test_conversation import book_dislocation, bookings

NONE_EARLIER = "Раньше 13:30 сегодня свободного времени нет."
RUN_9 = "А перенести-то на пораньше можно? А то до четверга нога ещё хуже станет."


async def booked(make_chat, day: str = "Сегодня"):
    """A visit ahead: today at 13:30 or tomorrow at 11:00."""
    chat = make_chat()
    await book_dislocation(chat, day=day)
    await chat.press(texts.PAYMENT_SELF)
    return chat


async def visit(chat) -> str:
    """«12.11 13:30» — when the only visit is, by the clinic's clock."""
    (booking,) = [b for b in await bookings(chat.conversation.db) if b.status == "active"]
    return f"{booking.start_at.astimezone(chat.conversation.settings.tz):%d.%m %H:%M}"


def nearest_labels(chat) -> list[str]:
    return [label for label in chat.labels() if label.startswith("Ближайшее")]


# --- «пораньше»: nothing earlier that day ----------------------------------------------------


@pytest.mark.parametrize(
    "asked",
    [
        "перенесите на пораньше",
        "перенесите запись пораньше, пожалуйста",
        "можно перенести на раньше?",
        "перенесите как можно раньше",
    ],
)
async def test_nothing_earlier_that_day_is_said_plainly(make_chat, asked: str) -> None:
    chat = await booked(make_chat)
    await chat.say(asked)
    assert NONE_EARLIER in chat.text, chat.text
    assert texts.OTHER_DAY in chat.labels() and texts.KEEP_AS_IS in chat.labels()
    assert not nearest_labels(chat) and not chat.times() and not chat.periods()
    assert await visit(chat) == "12.11 13:30"


async def test_asked_as_a_question_the_word_goes_with_the_button(make_chat) -> None:
    """Run 9 without its last sentence: «Ваша запись» with «Перенести» under it."""
    chat = await booked(make_chat)
    await chat.say(RUN_9)
    assert chat.text.startswith("Ваша запись:\nсегодня, 12 ноября (четверг) в 13:30"), chat.text
    assert "При переносе сразу покажу время по пожеланию: раньше 13:30." in chat.text
    await chat.press(texts.MOVE)
    assert NONE_EARLIER in chat.text and not nearest_labels(chat)
    assert await visit(chat) == "12.11 13:30"


async def test_run_9_word_for_word(make_chat, kb: KnowledgeBase) -> None:
    """The message as it was: a move and a question; the answer, then the move."""
    answer = {"found": True, "answer": "Приехать без записи нельзя."}
    fake = FakeDeepSeek(answer, answer)
    chat = make_chat(assistant=fake.assistant(kb))
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    await chat.say(f"{RUN_9} Или мне просто так прийти?")
    assert NONE_EARLIER in chat.text, chat.text
    assert not nearest_labels(chat) and await visit(chat) == "12.11 13:30"


async def test_the_header_and_the_wish_say_what_is_moved_and_where(make_chat) -> None:
    chat = await booked(make_chat)
    await chat.say("перенесите на пораньше")
    assert chat.text.startswith("Перенос записи: Петров И. С., сегодня, 12 ноября, 13:30")
    assert "Пожелание: сегодня, 12 ноября, раньше 13:30" in chat.text


async def test_other_day_after_it_offers_no_nearest_time_that_is_later(make_chat) -> None:
    chat = await booked(make_chat)
    await chat.say("перенесите на пораньше")
    await chat.press(texts.OTHER_DAY)
    assert texts.DAY_PICK in chat.text
    assert not nearest_labels(chat)  # «Ближайшее: сегодня, 14:30» is later than the visit
    days = [b.label for b in chat.days()]
    assert days and not any(label.startswith(texts.TODAY) for label in days), days
    assert texts.KEEP_AS_IS in chat.labels()
    await chat.pick_day("Завтра")  # the patient's own choice of another day
    await chat.pick_time()
    assert (await visit(chat)).startswith("13.11")


async def test_keep_as_it_is_leaves_the_visit(make_chat) -> None:
    chat = await booked(make_chat)
    await chat.say("перенесите на пораньше")
    await chat.press(texts.KEEP_AS_IS)
    assert texts.KEPT in chat.text and await visit(chat) == "12.11 13:30"


async def test_typed_on_a_screen_of_the_move(make_chat) -> None:
    chat = await booked(make_chat)
    await chat.press(texts.MENU_MY)
    await chat.press(texts.MOVE)
    assert nearest_labels(chat) == ["Ближайшее: сегодня, 14:30"]  # no wish yet: as before
    await chat.say("а пораньше есть?")
    assert NONE_EARLIER in chat.text and not nearest_labels(chat)


async def test_the_wish_dropped_brings_the_plain_move_back(make_chat) -> None:
    chat = await booked(make_chat)
    await chat.say("перенесите на попозже")
    await chat.press(texts.WISH_CLEAR)
    assert "Пожелание" not in chat.text
    assert any(label.startswith(texts.TODAY) for label in (b.label for b in chat.days()))


# --- «пораньше», «попозже»: there is such a time ----------------------------------------------


async def test_later_that_day_offers_only_later_times(make_chat) -> None:
    chat = await booked(make_chat)
    await chat.say("перенесите на попозже")
    assert "Пожелание: сегодня, 12 ноября, позже 13:30" in chat.text
    assert nearest_labels(chat) == ["Ближайшее: сегодня, 14:30"]
    for period in [b.data for b in chat.periods()]:
        await chat.data(period)
        times = [b.label for b in chat.times()]
        assert times and all(label > "13:30" for label in times), times
        await chat.press("Назад") if "Назад" in chat.labels() else await chat.say("попозже")
    await chat.press(chat.nearest().label)
    assert await visit(chat) == "12.11 14:30"


async def test_earlier_tomorrow_offers_only_earlier_times(make_chat) -> None:
    chat = await booked(make_chat, "Завтра")  # 13.11, 11:00; the night before it is free
    await chat.say("перенесите запись пораньше")
    assert chat.text.startswith("Перенос записи: Петров И. С., завтра, 13 ноября, 11:00")
    assert "Раньше 11:00" not in chat.text
    if chat.periods():
        await chat.data(chat.periods()[0].data)
    times = [b.label for b in chat.times()]
    assert times and all(label < "11:00" for label in times), times
    await chat.pick_time()
    assert (await visit(chat)) < "13.11 11:00"


# --- not a wish relative to the visit ---------------------------------------------------------


async def test_another_day_named_is_a_plain_wish(make_chat) -> None:
    chat = await booked(make_chat)
    await chat.say("перенесите на завтра пораньше")
    assert "Пожелание: завтра, 13 ноября" in chat.text
    assert "Раньше" not in chat.text and "раньше 13:30" not in chat.text


@pytest.mark.parametrize(
    ("asked", "bound"),
    [("перенесите не раньше 15:00", "после 15:00"), ("перенесите не позже 16:00", "до 16:00")],
)
async def test_a_bound_with_a_time_is_read_as_before(make_chat, asked: str, bound: str) -> None:
    chat = await booked(make_chat)
    await chat.say(asked)
    assert f"Пожелание: {bound}" in chat.text or f", {bound}" in chat.text, chat.text
    assert "свободного времени нет" not in chat.text


async def test_the_button_of_a_move_carries_no_wish(make_chat) -> None:
    chat = await booked(make_chat)
    await chat.press(texts.MENU_MY)
    await chat.press(texts.MOVE)
    assert "Пожелание" not in chat.text and texts.DAY_PICK in chat.text


# --- the day in words: «Ваша запись» and the header of a move ---------------------------------


@pytest.mark.parametrize(
    ("day", "said", "header"),
    [
        ("Сегодня", "сегодня, 12 ноября (четверг) в 13:30", "сегодня, 12 ноября, 13:30"),
        ("Завтра", "завтра, 13 ноября (пятница) в 11:00", "завтра, 13 ноября, 11:00"),
    ],
)
async def test_today_and_tomorrow_are_named_in_words(
    make_chat, day: str, said: str, header: str
) -> None:
    chat = await booked(make_chat, day)
    await chat.press(texts.MENU_MY)
    assert chat.text.startswith(f"Ваша запись:\n{said} — Петров Иван Сергеевич."), chat.text
    await chat.press(texts.MOVE)
    assert chat.text.startswith(f"Перенос записи: Петров И. С., {header}\n"), chat.text


async def test_any_other_day_is_named_as_before(make_chat) -> None:
    chat = await booked(make_chat, "Вс")
    await chat.press(texts.MENU_MY)
    assert chat.text.startswith("Ваша запись:\n15 ноября (воскресенье) в "), chat.text
    await chat.press(texts.MOVE)
    assert chat.text.startswith("Перенос записи: Петров И. С., вс 15.11, "), chat.text
