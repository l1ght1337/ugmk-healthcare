"""The dialogs from the customer's check of 899d068 (16.11): cancelling by text, the word
order of a full name, a second booking of the same patient.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

import pytest

from app.bot import texts
from app.bot.render import format_date
from app.db.models import Booking, User
from app.flow.identity import parse_full_name
from app.flow.loader import KB_DIR, KnowledgeBase, care_lines
from app.flow.wishes import dative
from app.schedule.mock import MockSchedule
from tests.chat_harness import Chat
from tests.conftest import TZ
from tests.fake_deepseek import FakeDeepSeek
from tests.test_conversation import book_dislocation, bookings
from tests.test_wish_flow import through_dislocation

PATIENT = "Петров Иван Сергеевич"


async def session(chat: Chat) -> dict:
    async with chat.conversation.db() as s:
        return dict((await s.get(User, chat.user.id)).session or {})


async def book_again(chat: Chat, day: str) -> None:
    """One more first visit of the same patient, on another day."""
    await chat.press(texts.MENU_BOOK)
    await through_dislocation(chat)
    if texts.ALREADY_AGAIN in chat.labels():
        await chat.press(texts.ALREADY_AGAIN)
    await chat.pick_slot(day)
    await chat.press("Петров Иван")
    await chat.press(texts.PAYMENT_SELF)


async def booked(make_chat, *days: str, **kw):  # type: ignore[no-untyped-def]
    """The first booking is today (Thursday, 12.11); ``days`` — the days of the others."""
    chat = make_chat(**kw)
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    for day in days:
        await book_again(chat, day)
    rows = await bookings(chat.conversation.db)
    for n, row in enumerate(rows):  # as the Telegram layer does after sending the cards
        await chat.conversation.register_booking_card(row.id, 700 + n)
        await chat.conversation.register_pinned_card(row.id, 900 + n)
    return chat


def when(booking: Booking) -> tuple[str, str]:
    start = booking.start_at.astimezone(TZ)
    return format_date(start.date()), f"{start:%H:%M}"


def ask(booking: Booking) -> str:
    date, time = when(booking)
    return texts.CANCEL_ASK.format(date=date, time=time)


async def active(chat: Chat) -> list[Booking]:
    return [b for b in await bookings(chat.conversation.db) if b.status == "active"]


# --- 1. «отмени все» and a booking named by its day, doctor or time -----------------------------


@pytest.mark.parametrize(
    "phrase", ["отмени все", "отмени всё", "отмените все записи", "все записи отмени"]
)
async def test_cancel_all_asks_first(make_chat, phrase: str) -> None:
    chat = await booked(make_chat, "Завтра", "Вс")
    await chat.say(phrase)
    (only,) = chat.replies  # one message: the bookings and the question
    assert only.text.startswith(texts.MY_TITLE) and only.text.endswith(texts.CANCEL_ALL_ASK)
    for booking in await active(chat):
        date, time = when(booking)
        assert f"• {date} в {time} — {PATIENT}. Врач: {booking.doctor}." in only.text
    assert chat.labels() == [texts.CANCEL_ALL_YES, texts.CANCEL_KEEP]
    assert len(await active(chat)) == 3  # nothing is cancelled without a «да»


async def test_cancel_all_after_yes(make_chat, schedule: MockSchedule) -> None:
    chat = await booked(make_chat, "Завтра", "Вс", bookings_to_admin_chat=True)
    rows = await active(chat)
    await chat.say("отмени все")
    posted = len(chat.admin)
    await chat.press(texts.CANCEL_ALL_YES)
    (only,) = chat.replies  # one confirmation
    assert only.text == texts.CANCELLED_ALL and texts.MENU_BOOK in chat.labels()
    assert await active(chat) == []
    assert all(schedule._is_free(row.slot_id) for row in rows)  # the time is free again
    notes = chat.admin[posted:]  # as an ordinary cancellation: a reply to each card
    assert [(n.text, n.reply_to) for n in notes] == [
        (texts.ADMIN_CANCELLED, 700 + n) for n in range(3)
    ]
    assert sorted(chat.turn.unpin) == [900, 901, 902]
    await chat.say("мои записи")
    assert chat.text == texts.MY_EMPTY


async def test_cancel_all_can_be_declined_and_the_old_button_does_nothing(make_chat) -> None:
    chat = await booked(make_chat, "Завтра")
    await chat.say("отмени все")
    yes = chat.button(texts.CANCEL_ALL_YES)
    await chat.press(texts.CANCEL_KEEP)
    assert chat.text == texts.KEPT and len(await active(chat)) == 2
    await chat.data(yes.data)  # the question is gone: its button cancels nothing
    assert chat.text == texts.STALE and len(await active(chat)) == 2


async def test_cancel_all_with_one_booking_is_the_usual_question(make_chat) -> None:
    chat = await booked(make_chat)
    (only,) = await active(chat)
    await chat.say("отмени все")
    assert chat.text == ask(only) and chat.labels() == [texts.CANCEL_YES, texts.CANCEL_KEEP]


async def test_cancel_the_booking_named_by_day_doctor_or_time(make_chat) -> None:
    chat = await booked(make_chat, "Завтра", "Вс")
    today, tomorrow, sunday = await active(chat)
    time = when(sunday)[1]
    phrases = {
        "отмени запись на воскресенье": sunday,
        "отмените запись на завтра": tomorrow,
        f"отмени запись на 15 ноября в {time}": sunday,
        "отмени запись на сегодня": today,
    }
    by_doctor: dict[str, list[Booking]] = {}
    for row in (today, tomorrow, sunday):
        by_doctor.setdefault(row.doctor, []).append(row)
    for doctor, rows in by_doctor.items():
        if len(rows) == 1:
            phrases[f"отмени запись {dative(doctor, initials=False)}"] = rows[0]
    assert len(phrases) > 4  # at least one doctor has one booking only
    for phrase, booking in phrases.items():
        await chat.say(phrase)
        assert chat.text == ask(booking), phrase  # found one: the question at once
        assert chat.labels() == [texts.CANCEL_YES, texts.CANCEL_KEEP]
        assert len(await active(chat)) == 3
    await chat.say("отмени запись на воскресенье")
    await chat.press(texts.CANCEL_YES)
    assert chat.text == texts.CANCELLED
    assert [b.id for b in await active(chat)] == [today.id, tomorrow.id]


async def test_several_found_are_shown_alone(make_chat) -> None:
    chat = await booked(make_chat, "Завтра", "Завтра", "Вс")
    _, first, second, sunday = await active(chat)
    await chat.say("отмени запись на завтра")
    (only,) = chat.replies
    assert only.text.startswith(texts.CANCEL_WHICH) and only.text.count("•") == 2
    assert when(sunday)[0] not in only.text
    labels = [texts.CANCEL_PICK.format(day="пт, 13.11", time=when(b)[1]) for b in (first, second)]
    assert chat.labels() == [*labels, texts.MENU_HOME]
    await chat.press(labels[1])
    assert chat.text == ask(second) and len(await active(chat)) == 4


async def test_nothing_found_is_said_and_all_are_shown(make_chat) -> None:
    chat = await booked(make_chat, "Завтра", "Вс")
    await chat.say("отмени запись на 15 ноября в 03:15")  # no booking at that time
    (only,) = chat.replies
    assert only.text.startswith(texts.CANCEL_NOT_FOUND) and only.text.count("•") == 3
    assert len([b for b in chat.buttons() if b.data.startswith("b:cancel:")]) == 3
    assert chat.labels()[-2:] == [texts.CANCEL_ALL, texts.MENU_HOME]
    assert len(await active(chat)) == 3


async def test_plain_cancel_asks_which_one_or_all(make_chat) -> None:
    chat = await booked(make_chat, "Завтра")
    await chat.say("отмени запись")
    (only,) = chat.replies
    assert only.text.startswith(texts.CANCEL_WHICH) and only.text.count("•") == 2
    assert chat.labels()[-2:] == [texts.CANCEL_ALL, texts.MENU_HOME]
    await chat.press(texts.CANCEL_ALL)
    assert chat.text.endswith(texts.CANCEL_ALL_ASK)
    assert chat.labels() == [texts.CANCEL_ALL_YES, texts.CANCEL_KEEP]


async def test_no_bookings_to_cancel(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    for phrase in ("отмени все", "отмени запись на воскресенье"):
        await chat.say(phrase)
        assert chat.text == texts.MY_EMPTY and texts.MENU_BOOK in chat.labels()


async def test_cancel_and_book_again_is_still_a_move(make_chat) -> None:
    chat = await booked(make_chat)
    await chat.say("отмени и запиши на воскресенье")
    assert chat.text.startswith("Переношу запись с четверга, 12 ноября")


# --- 3. the word order of a full name -----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("Петр Андреев Александрович", ("Андреев", "Петр", "Александрович")),
        ("Мария Иванова", ("Иванова", "Мария", None)),
        ("Анна Сергеевна Петрова", ("Петрова", "Анна", "Сергеевна")),
        ("иванов иван иванович", ("Иванов", "Иван", "Иванович")),
        ("Иван Иванович Иванов", ("Иванов", "Иван", "Иванович")),
        ("Александрович Андреев Пётр", ("Андреев", "Пётр", "Александрович")),
        ("Ильинична Ольга Кузнецова", ("Кузнецова", "Ольга", "Ильинична")),
        ("Сергей Ильич Петров", ("Петров", "Сергей", "Ильич")),
        ("Петрова-Водкина Анна", ("Петрова-Водкина", "Анна", None)),
        ("анна петрова-водкина", ("Петрова-Водкина", "Анна", None)),
        ("Алиев Али Вели оглы", ("Алиев", "Али", "Вели оглы")),
        ("Марина Мамедова Ахмед кызы", ("Мамедова", "Марина", "Ахмед кызы")),
        # a surname that looks like a patronymic: the patronymic follows the first name
        ("Роман Аркадьевич Абрамович", ("Абрамович", "Роман", "Аркадьевич")),
        ("Абрамович Роман Аркадьевич", ("Абрамович", "Роман", "Аркадьевич")),
        ("Александр Александрович Александров", ("Александров", "Александр", "Александрович")),
        # not clear what is what: the order as it is written
        ("Сидоров Захар Петрович", ("Сидоров", "Захар", "Петрович")),
        ("Захар Сидоров", ("Захар", "Сидоров", None)),
        ("Ким Ир Сен", ("Ким", "Ир", "Сен")),
        ("Павел Роман", ("Павел", "Роман", None)),
    ],
)
def test_full_name_in_any_word_order(text: str, name: tuple) -> None:
    assert parse_full_name(text) == name, text


@pytest.mark.parametrize("text", ["Иван", "а б", "Иванов Иван Иванович младший сын", "12 34"])
def test_not_a_full_name(text: str) -> None:
    assert parse_full_name(text) is None, text


async def test_the_birth_step_shows_how_the_name_was_read(make_chat, db) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await through_dislocation(chat)
    await chat.pick_slot("Завтра")
    await chat.press(texts.CONSENT_YES)
    await chat.say("Петр Андреев Александрович")
    assert texts.PATIENT_IS.format(name="Андреев Петр Александрович") in chat.text
    assert texts.ASK_BIRTH in chat.text
    await chat.press(texts.BACK)  # it can be corrected
    assert texts.ASK_NAME in chat.text
    await chat.say("Мария Иванова")
    assert texts.PATIENT_IS.format(name="Иванова Мария") in chat.text
    await chat.say("12.03.1985")
    await chat.contact()
    assert "Мария записана на приём" in chat.text or "Мария записан" in chat.text
    (booking,) = await bookings(db)
    async with db() as s:
        from app.db.models import Patient

        patient = await s.get(Patient, booking.patient_id)
    assert (patient.last_name, patient.first_name, patient.patronymic) == ("Иванова", "Мария", None)


# --- 4. the lines of the reference the patient may read word for word ----------------------------


def test_lines_of_the_reference_read_as_the_bot_speaking() -> None:
    text = (KB_DIR / "info" / "uslugi.md").read_text(encoding="utf-8")
    face = next(line for line in text.splitlines() if "на лице, шее и голове" in line)
    assert face == (
        "- Порезы и раны на лице, шее и голове в травмпункте не зашивают: куда обратиться, "
        "подскажу по ходу записи."
    )
    for line in (face, *care_lines().values()):
        low = line.lower()
        assert "бот" not in low and "сценари" not in low and "ответ клиники" not in low, line


# --- 2. the fracture button ---------------------------------------------------------------------


def test_open_fracture_button_reads_as_an_answer(kb: KnowledgeBase) -> None:
    labels = [c.label for c in kb.scenario("fracture").question("how").buttons()]
    assert labels == [
        "Бытовая травма",
        "Открытый перелом, смещение",
        "Нападение, драка, сбила машина",
    ]
    assert texts.SUMMARY_ANSWERS[("open_or_displaced", "True")] == (
        "открытый перелом или перелом со смещением"
    )


# --- 5. a second first visit of the same patient -------------------------------------------------


async def test_second_first_visit_is_noticed_before_the_time(make_chat) -> None:
    chat = await booked(make_chat)
    (first,) = await active(chat)
    date, time = when(first)
    await chat.press(texts.MENU_BOOK)
    await through_dislocation(chat)
    summary, screen = chat.replies
    assert "Ваши ответы: вывих; для себя" in summary.text
    line = texts.ALREADY_BOOKED.format(date=date, time=time, patient=PATIENT)
    assert screen.text.endswith(line) and "Шаг 4 из 5" in screen.text
    assert chat.labels()[-4:] == [texts.ALREADY_MOVE, texts.ALREADY_AGAIN, texts.BACK, texts.MENU]
    assert not chat.days() and (await session(chat))["step"] == "second"
    await chat.press(texts.ALREADY_AGAIN)
    assert chat.days() and texts.DAY_PICK in chat.text
    await chat.pick_slot("Завтра")
    await chat.press("Петров Иван")
    assert len(await active(chat)) == 2


async def test_move_that_booking_instead_of_a_second_one(make_chat) -> None:
    chat = await booked(make_chat)
    (first,) = await active(chat)
    await chat.say("запишите меня на завтра")
    await through_dislocation(chat)
    assert texts.ALREADY_MOVE in chat.labels() and "Пожелание: завтра, 13 ноября" in chat.text
    await chat.press(texts.ALREADY_MOVE)
    assert chat.text.startswith("Переношу запись с четверга, 12 ноября")
    assert "13 ноября (пятница)" in chat.text and texts.KEEP_AS_IS in chat.labels()
    await chat.pick_time()
    (moved,) = await active(chat)
    assert moved.id == first.id and moved.start_at.astimezone(TZ).day == 13


async def test_typed_answer_on_the_second_visit_question(make_chat) -> None:
    chat = await booked(make_chat)
    await chat.press(texts.MENU_BOOK)
    await through_dislocation(chat)
    await chat.say("записать ещё раз")
    assert chat.days()


async def test_edit_answers_does_not_ask_about_the_second_visit_again(make_chat) -> None:
    chat = await booked(make_chat)
    await chat.press(texts.MENU_BOOK)
    await through_dislocation(chat)
    await chat.press(texts.ALREADY_AGAIN)
    await chat.press(texts.BACK)  # the question again: it is a screen of the booking
    assert texts.ALREADY_AGAIN in chat.labels()
    await chat.press(texts.ALREADY_AGAIN)
    await chat.say("ближайшее")
    assert (await session(chat))["step"] == "patient"


async def test_a_child_after_an_adult_is_not_a_second_visit(make_chat) -> None:
    chat = await booked(make_chat)
    await chat.press(texts.MENU_BOOK)
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")
    await chat.press(texts.AUDIENCE_CHILD)
    await chat.say("8 лет")
    await chat.press("Бытовая травма")
    await chat.press("В течение 7 дней")
    await chat.press("Нет, не обращались")
    assert chat.days() and texts.ALREADY_MOVE not in chat.labels()


async def test_a_repeat_visit_is_not_a_second_first_visit(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek()
    chat = await booked(make_chat, assistant=fake.assistant(kb))
    await chat.press(texts.MENU_BOOK)
    await chat.press("Уже лечусь у вас")
    await chat.press("Повторный приём")
    await chat.press(texts.AUDIENCE_SELF)
    for _ in range(4):  # the scenario's questions: the first answer each time
        if chat.days() or chat.periods() or chat.times():
            break
        await chat.data(chat.buttons()[0].data)
    assert texts.ALREADY_MOVE not in chat.labels()
    assert chat.days() or chat.periods() or chat.times()
