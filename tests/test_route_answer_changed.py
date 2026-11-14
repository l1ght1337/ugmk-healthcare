"""A booking after a route of a booking scenario with another answer (14.11.2026).

Routes of 04-xx depend on the answers (a burn of the face, a cosmetic suture) and do not
lock the booking: the patient may come back and answer differently. The administrators see
it on the booking: «После маршрута ответ изменён: было «Лицо», стало «Другая часть тела»».

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

from app.bot import texts
from tests.test_conversation import book_dislocation, bookings
from tests.test_sim_findings_2 import session

BURN_CHANGED = (
    "После маршрута ответ изменён: было «Лицо», стало «Другая часть тела» (вопрос: «Что обожгли?»)"
)


async def to_burn(chat) -> None:
    await chat.press(texts.MENU_BOOK)
    await chat.press("Рана, ожог, укус")
    await chat.press("Ожог")
    if texts.AUDIENCE_SELF in chat.labels():  # asked once in a conversation
        await chat.press(texts.AUDIENCE_SELF)
    assert "Что обожгли?" in chat.text


async def burn_of_the_face(chat) -> None:
    await chat.start()
    await to_burn(chat)
    await chat.press("Лицо")
    assert "Ожоговый центр ГКБ № 40" in chat.text


async def finish(chat) -> None:
    await chat.pick_slot("Завтра")
    await chat.press(texts.CONSENT_YES)
    await chat.say("петров иван сергеевич")
    await chat.say("12.03.1985")
    await chat.contact()


async def burn_of_the_body(chat) -> None:
    await to_burn(chat)
    for label in ("Другая часть тела", "Кипяток, пар, огонь, солнце", "Менее суток назад",
                  "Нет, не обращались"):  # fmt: skip
        await chat.press(label)
    await finish(chat)


async def test_the_card_says_that_the_answer_was_changed(make_chat) -> None:
    chat = make_chat(bookings_to_admin_chat=True)
    await burn_of_the_face(chat)
    await burn_of_the_body(chat)
    (booking,) = await bookings(chat.conversation.db)
    card = next(p.text for p in chat.admin if p.booking_id == booking.id)
    assert card.endswith(BURN_CHANGED) and f"Запись № {booking.id}" in card


async def test_without_cards_the_line_goes_as_a_note(make_chat) -> None:
    chat = make_chat()
    await burn_of_the_face(chat)
    await burn_of_the_body(chat)
    (booking,) = await bookings(chat.conversation.db)
    (note,) = [p.text for p in chat.admin]
    assert note.startswith(f"Запись № {booking.id} (") and note.endswith(BURN_CHANGED)


async def test_the_same_answers_have_no_such_line(make_chat) -> None:
    chat = make_chat(bookings_to_admin_chat=True)
    await chat.start()
    await burn_of_the_body(chat)
    assert not any("ответ изменён" in p.text for p in chat.admin)


async def test_another_scenario_after_the_route_has_no_such_line(make_chat) -> None:
    chat = make_chat(bookings_to_admin_chat=True)
    await burn_of_the_face(chat)
    await book_dislocation(chat)
    assert not any("ответ изменён" in p.text for p in chat.admin)


async def test_a_cosmetic_suture_changed_to_a_surgical_one(make_chat) -> None:
    chat = make_chat(bookings_to_admin_chat=True)
    await chat.start()
    answers = ("Руки, ноги, тело", "Да, двигается", "Менее суток назад")
    for last in ("Нужен косметический шов", "Да, на хирургический шов"):
        await chat.press(texts.MENU_BOOK)
        await chat.press("Рана, ожог, укус")
        await chat.press("Порез, рана")
        if texts.AUDIENCE_SELF in chat.labels():
            await chat.press(texts.AUDIENCE_SELF)
        for label in (*answers, last):
            await chat.press(label)
    await finish(chat)
    card = chat.admin[-1].text
    assert "было «Нужен косметический шов», стало «Да, на хирургический шов»" in card
    assert card.count("ответ изменён") == 1  # the other answers are the same


async def test_the_route_is_forgotten_once_the_booking_is_made(make_chat) -> None:
    chat = make_chat(bookings_to_admin_chat=True)
    await burn_of_the_face(chat)
    assert (await session(chat))["talk"]["answered"]["dialog"]["outcome_id"] == "face_adult"
    await burn_of_the_body(chat)
    assert (await session(chat))["talk"]["answered"] is None  # the next booking has no line
