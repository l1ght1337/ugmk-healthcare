"""The messages the bot sends by itself, in the style of its screens.

The reminders (a day and two hours before), the survey after the visit and the reminder
about an unfinished booking were built as plain ``Reply`` past ``Ctx.say``: no bold line, no
labels, the address as text. Now the bold line is what the patient is to read or to answer;
the date, the time, the doctor and the address stand on lines behind 📅 🕒 🩺 📍, as on the
booking card; the address is a link to the map; a service note is a quote. The words of the
scripts (what to bring, the parking) are the same.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.bot import render, texts
from app.bot.conversation import Reply
from app.db.models import Patient
from app.notify.planner import DAY_BEFORE, TWO_HOURS
from tests.chat_harness import Chat
from tests.conftest import NOW
from tests.test_conversation import book_dislocation, bookings

MAP = "https://yandex.ru/maps/?text="


def marked(reply: Reply) -> list[tuple[str, str]]:
    return [(s.kind, reply.text[s.start : s.end]) for s in reply.spans]


async def reminder(chat: Chat, kind: str) -> Reply:
    """The reminder of this kind for the only booking, as the scheduler would send it."""
    conversation = chat.conversation
    (booking,) = await bookings(conversation.db)
    async with conversation.db() as db:
        patient = (await db.scalars(select(Patient))).one()
    reply = conversation._reminder_reply(kind, booking, patient, chat.user.chat_id)
    chat._take(type(chat.turn)([reply]))  # the checks of every reply of every test
    return reply


async def test_the_reminder_a_day_before(make_chat, kb) -> None:
    chat = make_chat()
    await book_dislocation(chat, day="Завтра")
    reply = await reminder(chat, DAY_BEFORE)
    head, date, time, doctor, patient, gap, bring = reply.text.split("\n")
    assert head == "Напоминаем: приём травматолога-ортопеда в травмпункте."
    assert date == "📅 Дата: пятница, 13 ноября" and time.startswith("🕒 Время: ")
    assert doctor.startswith("🩺 Врач: ") and patient == "📝 Пациент: Петров Иван Сергеевич"
    assert gap == "" and bring == render.documents(kb, "adult")  # the words of the script
    assert marked(reply) == [("bold", head)]
    assert [b.label for row in reply.buttons for b in row] == [
        texts.CONFIRM,
        texts.MOVE,
        texts.CANCEL,
    ]


async def test_the_reminder_two_hours_before(make_chat, kb) -> None:
    chat = make_chat()
    await book_dislocation(chat)
    reply = await reminder(chat, TWO_HOURS)
    head, date, time, address, gap, parking = reply.text.split("\n")
    assert head == "Скоро приём в травмпункте."
    assert date == "📅 Дата: четверг, 12 ноября" and time.startswith("🕒 Время: ")
    assert address == "📍 Адрес: ул. Шейнкмана, 113/2, вход через детское отделение"
    assert gap == "" and "подземный паркинг" in parking  # the script phrase, in short
    bold, link = reply.spans
    assert (bold.kind, reply.text[bold.start : bold.end]) == ("bold", head)
    assert (link.kind, reply.text[link.start : link.end]) == ("link", "ул. Шейнкмана, 113/2")
    assert link.url is not None and link.url.startswith(MAP) and "%D0%95%D0%BA" in link.url


async def test_a_patients_name_with_marks_in_it_stays_as_typed(make_chat) -> None:
    chat = make_chat()
    await book_dislocation(chat, day="Завтра")
    async with chat.conversation.db() as db:
        patient = (await db.scalars(select(Patient))).one()
        patient.first_name, patient.patronymic = "Ли_Вэй", "**Чжан**"
        await db.commit()
    reply = await reminder(chat, DAY_BEFORE)
    assert "📝 Пациент: Петров Ли_Вэй **Чжан**" in reply.text.split("\n")
    assert [kind for kind, _ in marked(reply)] == ["bold"]  # the first line only


async def test_the_survey_after_the_visit_is_a_bold_question(make_chat) -> None:
    chat = make_chat()
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    for _ in range(3):
        await chat.demo_next()
        if chat.text == texts.AFTER_VISIT:
            break
    (survey,) = chat.replies
    assert marked(survey) == [("bold", texts.AFTER_VISIT)]
    await chat.press(texts.AFTER_VISIT_OK)
    glad, offer = chat.replies
    assert glad.text == texts.GLAD and glad.spans == ()
    assert marked(offer) == [("bold", "Записать Вас на контрольный приём?")]
    await chat.press(texts.CONTROL_NO)


async def test_did_not_come_and_a_question_after_the_visit(make_chat) -> None:
    chat = make_chat()
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    for _ in range(3):
        await chat.demo_next()
        if chat.text == texts.AFTER_VISIT:
            break
    await chat.press(texts.AFTER_VISIT_MISSED)
    assert ("bold", "Записать Вас заново?") in marked(chat.replies[-1])


async def test_the_reminder_about_an_unfinished_booking(make_chat, clock) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.say("вывихнул плечо")
    clock.now = NOW + timedelta(minutes=16)
    (turn,) = [chat._take(t) for t in await chat.conversation.unfinished()]
    (reply,) = turn.replies
    assert reply.text == texts.UNFINISHED
    assert marked(reply) == [("bold", "Продолжить с того же места?")]
