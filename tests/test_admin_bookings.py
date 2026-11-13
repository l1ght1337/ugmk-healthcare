"""Block 1: bookings in the admin chat for the test (BOOKINGS_TO_ADMIN_CHAT)."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.bot import texts
from app.db.models import Booking
from tests.chat_harness import Chat
from tests.conftest import NOW
from tests.test_conversation import book_dislocation

CARD_ID = 9001


async def booked(chat: Chat) -> Booking:
    await book_dislocation(chat)
    card = next(p for p in chat.admin if p.booking_id is not None)
    await chat.conversation.register_booking_card(card.booking_id, CARD_ID)
    async with chat.conversation.db() as s:
        return (await s.scalars(select(Booking))).one()


async def test_off_by_default(make_chat) -> None:
    chat = make_chat()
    await book_dislocation(chat)
    assert not [p for p in chat.admin if p.booking_id]
    assert await chat.conversation.admin_today() == texts.TODAY_OFF


async def test_card_with_every_detail(make_chat) -> None:
    chat = make_chat(bookings_to_admin_chat=True)
    booking = await booked(chat)
    card = next(p for p in chat.admin if p.booking_id == booking.id)
    for part in (
        f"Запись № {booking.id} (демо)",
        "Дата и время: четверг, 12 ноября, ",
        f"Врач: {booking.doctor}",
        "Приём: первичный",
        "Тема: 4.1 Вывихи",
        "Пациент: Петров Иван Сергеевич, 12.03.1985 — взрослый, 41 год",
        "Телефон: +79123456789",
        "Оплата: не выбрана",
        "@tester (id 1001)",
    ):
        assert part in card.text, part

    await chat.press(texts.PAYMENT_INSURANCE)  # the same card now shows the payment
    edit = chat.admin[-1]
    assert edit.edit_message == CARD_ID and "Оплата: по страховке" in edit.text


async def test_move_cancel_survey_and_rating_answer_the_card(make_chat, clock) -> None:
    chat = make_chat(bookings_to_admin_chat=True)
    await booked(chat)
    await chat.press(texts.PAYMENT_SELF)

    await chat.press(texts.MENU_MY)
    await chat.press(texts.MOVE)
    await chat.pick_slot("Завтра")
    moved = chat.admin[-1]
    assert moved.reply_to == CARD_ID and moved.text.startswith("Перенесена на пятница, 13 ноября")

    await chat.demo_next()
    while texts.AFTER_VISIT not in chat.text:
        await chat.demo_next()
    await chat.press(texts.AFTER_VISIT_OK)
    survey = chat.admin[-1]
    assert (survey.reply_to, survey.text) == (CARD_ID, "«Как прошёл приём?» — «Всё хорошо».")

    await chat.press(texts.CONTROL_YES)  # the control visit gets its own card
    await chat.press("Нет")
    await chat.pick_time()
    control = next(p for p in reversed(chat.admin) if p.booking_id)
    assert "Приём: повторный" in control.text and "Тема: 4.9" in control.text
    await chat.conversation.register_booking_card(control.booking_id, CARD_ID + 1)
    await chat.press(texts.PAYMENT_SELF)
    while texts.FEELING not in chat.text:
        await chat.demo_next()
    await chat.press(texts.FEELING_OK)
    await chat.press("5")
    rating = chat.admin[-1]
    assert (rating.reply_to, rating.text) == (CARD_ID + 1, "Оценка сервиса: 5 из 5.")

    await chat.data(f"b:cancel:{control.booking_id}")
    await chat.press(texts.CANCEL_YES)
    assert (chat.admin[-1].reply_to, chat.admin[-1].text) == (CARD_ID + 1, texts.ADMIN_CANCELLED)


async def test_today_lists_today_and_tomorrow(make_chat, clock) -> None:
    chat = make_chat(bookings_to_admin_chat=True)
    booking = await booked(chat)
    at = booking.start_at.astimezone(chat.conversation.settings.tz)
    today = await chat.conversation.admin_today()
    assert today.startswith("Записи на 12 ноября (четверг) и 13 ноября (пятница):")
    assert (
        f"• {at:%H:%M} — № {booking.id}, Петров Иван Сергеевич, 12.03.1985 — взрослый, "
        "41 год, 4.1 Вывихи, первичный. Врач: "
    ) in today
    clock.now = NOW + timedelta(days=3)
    assert (await chat.conversation.admin_today()).startswith("На 15 ноября")


async def test_reply_to_a_card_is_not_forwarded(make_chat) -> None:
    chat = make_chat(bookings_to_admin_chat=True)
    await booked(chat)
    assert await chat.conversation.admin_reply(CARD_ID, "Ждём") == texts.ADMIN_CARD_NOTE


async def test_forget_names_the_cards_to_delete(make_chat) -> None:
    chat = make_chat(bookings_to_admin_chat=True)
    booking = await booked(chat)
    await chat.press(texts.PAYMENT_SELF)
    chat._take(await chat.conversation.forget(chat.user))
    await chat.press(texts.FORGET_YES)
    note = chat.admin[-1].text
    assert "отозвал согласие" in note and f"карточки записей № {booking.id}" in note


async def test_delivery_remembers_the_card_and_edits_it() -> None:
    from app.bot.conversation import AdminPost, Turn
    from app.bot.telegram import Delivery

    class Bot:
        def __init__(self) -> None:
            self.calls: list = []

        async def send_message(self, chat_id, text, **kw):  # type: ignore[no-untyped-def]
            self.calls.append(("send", text, kw.get("reply_to_message_id")))
            return type("Sent", (), {"message_id": 77})()

        async def edit_message_text(self, text, **kw):  # type: ignore[no-untyped-def]
            self.calls.append(("edit", text, kw["message_id"]))

    class Conv:
        def __init__(self) -> None:
            self.cards: list = []

        async def register_booking_card(self, booking_id, message_id):  # type: ignore[no-untyped-def]
            self.cards.append((booking_id, message_id))

    bot, conv = Bot(), Conv()
    delivery = Delivery(bot, conv, -100)  # type: ignore[arg-type]
    await delivery(Turn([], [AdminPost("Запись № 3", booking_id=3)]))
    await delivery(Turn([], [AdminPost("Запись № 3, оплата", edit_message=77)]))
    await delivery(Turn([], [AdminPost("Отменена пациентом.", reply_to=77)]))
    assert conv.cards == [(3, 77)]
    assert bot.calls == [
        ("send", "Запись № 3", None),
        ("edit", "Запись № 3, оплата", 77),
        ("send", "Отменена пациентом.", 77),
    ]
