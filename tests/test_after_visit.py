"""The notification after the visit (the customer, 16.11.2026).

It came 3 hours after the end of the visit with «Как прошёл приём?», and the control visit
was offered only after «Всё хорошо», about the 10th day. Now:

* it comes 2 hours after the end of the visit — and not at night: a moment in the quiet hours
  of the clinic (config/clinic.yaml, unfinished_booking.quiet_hours) is the morning after;
* the control visit is offered in the notification itself, a week after the visit:
  «Если врач назначил контрольный приём, запишу Вас на него. Обычно его назначают через
  неделю — предлагаю время около четверга, 19 ноября.» The bot does not decide for the
  doctor: it never says that a control visit is needed;
* the buttons: «Записаться на контрольный приём», «Запись не нужна», «Есть вопрос», «Не был
  на приёме»;
* after the control visit everything is as it was: «Как Вы себя чувствуете?» and the rating,
  no third visit.

The clinic clock is Thursday, 12 November 2026, 12:00; the visit is today at 13:30–14:00.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest
from sqlalchemy import select

from app.bot import texts
from app.config import ClinicData
from app.db.models import Reminder
from app.db.session import Database
from app.notify.planner import AFTER, AFTER_VISIT, DAY_BEFORE, TWO_HOURS, after_visit_due, plan
from tests.chat_harness import Clock
from tests.conftest import NOW, TZ
from tests.test_conversation import book_dislocation, bookings
from tests.test_notify_style import marked

OFFER = (
    "Если врач назначил контрольный приём, запишу Вас на него. Обычно его назначают через "
    "неделю — предлагаю время около четверга, 19 ноября."
)
NOT_NEEDED = "Запись не нужна"
BUTTONS = ["Записаться на контрольный приём", NOT_NEEDED, "Есть вопрос", "Не был на приёме"]
NEEDED = ("нужен контрольный", "необходим контрольный", "Вам нужно прийти", "обязательно")


def local(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ)


async def to_survey(chat) -> None:
    """A visit today at 13:30, paid by oneself; the notifications up to the one after it."""
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    for _ in range(3):
        await chat.demo_next()
        if chat.text.startswith(texts.AFTER_VISIT):
            return
    raise AssertionError(chat.text)


async def after_visit(db: Database) -> list[Reminder]:
    async with db() as s:
        rows = await s.scalars(select(Reminder).where(Reminder.kind == AFTER_VISIT))
        return list(rows.all())


# --- when: two hours after the visit, not at night ---------------------------------------------


def test_the_survey_is_due_two_hours_after_the_visit(clinic: ClinicData) -> None:
    quiet = clinic.clinic.unfinished_booking.quiet_hours
    start = NOW + timedelta(days=2)  # Saturday, 12:00
    end = start + timedelta(minutes=30)
    assert timedelta(hours=2) == AFTER
    assert plan(start, end, NOW, quiet=quiet, tz=TZ) == [
        (DAY_BEFORE, start - timedelta(hours=24)),
        (TWO_HOURS, start - timedelta(hours=2)),
        (AFTER_VISIT, end + timedelta(hours=2)),
    ]


@pytest.mark.parametrize(
    ("end", "due"),
    [
        (local(12, 14), local(12, 16)),  # by day: two hours later
        (local(12, 19, 59), local(12, 21, 59)),  # the last minute before the quiet hours
        (local(12, 20), local(13, 8)),  # 22:00 is night: the next morning
        (local(12, 21, 30), local(13, 8)),
        (local(12, 23), local(13, 8)),  # 01:00
        (local(13, 1, 30), local(13, 8)),  # a night visit: the same morning
        (local(13, 5, 59), local(13, 8)),
        (local(13, 6), local(13, 8)),  # 08:00 sharp is the morning already
        (local(13, 6, 30), local(13, 8, 30)),
    ],
)
def test_a_moment_at_night_is_the_morning_after(
    clinic: ClinicData, end: datetime, due: datetime
) -> None:
    quiet = clinic.clinic.unfinished_booking.quiet_hours
    assert (quiet.start, quiet.end) == (time(22), time(8))
    assert after_visit_due(end, quiet, TZ) == due


async def test_a_visit_by_day_is_asked_about_two_hours_later(make_chat, db: Database) -> None:
    chat = make_chat()
    await book_dislocation(chat)
    (booking,) = await bookings(db)
    (survey,) = await after_visit(db)
    assert survey.due_at == booking.end_at + timedelta(hours=2)


async def test_an_evening_visit_is_asked_about_in_the_morning(
    make_chat, db: Database, clock: Clock
) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    for label in ("Рука или нога", "Похоже на вывих", texts.AUDIENCE_SELF, "Бытовая травма",
                  "В течение 7 дней", "Нет, не обращались"):  # fmt: skip
        await chat.press(label)
    await chat.pick_slot("Сегодня", not_before="21:00")
    await chat.press(texts.CONSENT_YES)
    await chat.say("петров иван сергеевич")
    await chat.say("12.03.1985")
    await chat.contact()
    (booking,) = await bookings(db)
    assert booking.end_at.astimezone(TZ) >= local(12, 21, 30)
    (survey,) = await after_visit(db)
    assert survey.due_at.astimezone(TZ) == local(13, 8)
    clock.now = local(12, 23, 45)
    assert [t for t in await chat.due() if AFTER_VISIT_TEXT in t.replies[0].text] == []
    clock.now = local(13, 8)
    sent = [t for t in await chat.due() if AFTER_VISIT_TEXT in t.replies[0].text]
    assert len(sent) == 1


AFTER_VISIT_TEXT = texts.AFTER_VISIT


async def test_a_survey_overdue_at_night_waits_for_the_morning(
    make_chat, db: Database, clock: Clock
) -> None:
    """The bot was down in the evening: what was due by day is not sent at 3 a.m."""
    chat = make_chat()
    await book_dislocation(chat)
    clock.now = local(13, 3)
    assert not any(AFTER_VISIT_TEXT in t.replies[0].text for t in await chat.due())
    clock.now = local(13, 8, 1)
    assert any(AFTER_VISIT_TEXT in t.replies[0].text for t in await chat.due())


async def test_planned_surveys_are_counted_anew(make_chat, db: Database) -> None:
    """Bookings made before the change: +3 hours, at night too."""
    chat = make_chat()
    await book_dislocation(chat)
    (booking,) = await bookings(db)
    async with db() as s:
        (row,) = (await s.scalars(select(Reminder).where(Reminder.kind == AFTER_VISIT))).all()
        row.due_at = booking.end_at + timedelta(hours=3)
        await s.commit()
    assert await chat.conversation.replan_after_visit() == 1
    (survey,) = await after_visit(db)
    assert survey.due_at == booking.end_at + timedelta(hours=2)
    assert await chat.conversation.replan_after_visit() == 0  # nothing to change the next time


# --- what: the control visit is offered at once ------------------------------------------------


async def test_the_notification_offers_the_control_visit_at_once(make_chat) -> None:
    chat = make_chat()
    await to_survey(chat)
    (survey,) = chat.replies
    assert survey.text == f"{texts.AFTER_VISIT}\n{OFFER}"
    assert marked(survey) == [("bold", texts.AFTER_VISIT)]  # what a push shows
    assert chat.labels() == BUTTONS
    assert not any(word in survey.text for word in NEEDED)  # the doctor decides, not the bot


async def test_book_the_control_visit_from_the_notification(make_chat, db: Database) -> None:
    chat = make_chat()
    await to_survey(chat)
    await chat.press(texts.CONTROL_YES)
    assert chat.text == "Открывали больничный лист?"
    await chat.press("Нет")  # the time grid of the day a week after the visit
    assert chat.text.startswith("Предлагаю время около 19 ноября (четверг).")
    assert texts.OTHER_DAY in chat.labels() and chat.periods() and not chat.days()
    await chat.pick_time()
    assert "Иван Сергеевич записан на приём" in chat.text
    _, control = await bookings(db)
    assert (control.cycle, control.status) == (2, "active")
    assert control.start_at.astimezone(TZ).date() == local(19, 0).date()


async def test_no_booking_wanted_is_thanks_and_the_menu(make_chat, db: Database) -> None:
    chat = make_chat(bookings_to_admin_chat=True)
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    (booking,) = await bookings(db)
    await chat.conversation.register_booking_card(booking.id, 700)  # its card in the group
    for _ in range(3):
        await chat.demo_next()
        if chat.text.startswith(texts.AFTER_VISIT):
            break
    posted = len(chat.admin)
    await chat.press(NOT_NEEDED)
    assert chat.text == "Спасибо!"
    assert chat.labels()[:2] == [texts.MENU_BOOK, texts.MENU_ASK]
    (booking,) = await bookings(db)
    assert booking.survey == "no_control"
    (note,) = chat.admin[posted:]
    assert (note.text, note.reply_to) == ("«Как прошёл приём?» — «Запись не нужна».", 700)
    await chat.demo_next()
    assert chat.text == texts.DEMO_NONE  # nothing else is waiting


async def test_a_question_goes_to_the_administrator_and_the_offer_stays(make_chat) -> None:
    chat = make_chat(admin_chat_id=-100)
    await to_survey(chat)
    await chat.press(texts.AFTER_VISIT_QUESTION)
    assert chat.text == texts.ASK_VISIT_QUESTION
    await chat.say("когда снимать повязку?")
    assert "обращение № 1" in chat.text and "когда снимать повязку?" in chat.admin[-1].text
    assert chat.replies[-1].text == OFFER
    assert chat.labels()[-2:] == [texts.CONTROL_YES, NOT_NEEDED]


async def test_did_not_come_is_as_it_was(make_chat) -> None:
    chat = make_chat()
    await to_survey(chat)
    await chat.press(texts.AFTER_VISIT_MISSED)
    assert chat.text == texts.MISSED and chat.labels() == [texts.MENU_BOOK, texts.MENU_HOME]


async def test_the_old_button_under_a_survey_sent_before_still_answers(
    make_chat, db: Database
) -> None:
    """«Всё хорошо» is under the surveys sent before the change."""
    chat = make_chat()
    await to_survey(chat)
    (booking,) = await bookings(db)
    await chat.data(f"v:ok:{booking.id}")
    glad, offer = chat.replies
    assert glad.text == texts.GLAD and offer.text == OFFER
    assert chat.labels() == [texts.CONTROL_YES, NOT_NEEDED]


# --- after the control visit: as it was --------------------------------------------------------


async def test_after_the_control_visit_no_third_visit_is_offered(make_chat, db: Database) -> None:
    chat = make_chat()
    await to_survey(chat)
    await chat.press(texts.CONTROL_YES)
    await chat.press("Нет")
    await chat.pick_time()
    await chat.press(texts.PAYMENT_SELF)
    for _ in range(4):
        await chat.demo_next()
        if chat.text == texts.FEELING:
            break
    assert chat.text == texts.FEELING, chat.text
    assert chat.labels() == [texts.FEELING_OK, texts.AFTER_VISIT_QUESTION]
    await chat.press(texts.FEELING_OK)
    assert chat.text == texts.RATE
    await chat.press("5")
    assert chat.text.startswith(texts.RATED) and "контрольн" not in chat.text.lower()
    await chat.demo_next()
    assert chat.text == texts.DEMO_NONE


def test_the_control_visit_is_a_week_after_the_first(clinic: ClinicData) -> None:
    assert clinic.clinic.control_visit_after_days == 7


def test_no_text_says_the_control_visit_is_needed() -> None:
    said = [value for value in vars(texts).values() if isinstance(value, str)]
    about = [text for text in said if "контрольн" in text.lower()]
    assert about and not any(word in text for text in about for word in NEEDED)
