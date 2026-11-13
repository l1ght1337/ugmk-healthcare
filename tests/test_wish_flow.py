"""Booking with wishes through the whole dialog (task 1): the ten phrases and what follows.

The clinic clock is Thursday, 12 November 2026, 12:00. The demo rota of those days:
Thu 12 — Кузнецов by day; Fri 13 — Ярунин by day, Билалов until 08:00, Деменков from 20:00,
Кузнецов in «Травматолог 2»; Sat 14 — Билалов by day, Ярунин in «Травматолог 2».
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.bot import texts
from app.db.models import User
from app.flow.loader import KnowledgeBase
from app.schedule.mock import MockSchedule
from app.schedule.rules import eligible
from tests.chat_harness import Chat
from tests.conftest import NOW, TZ
from tests.fake_deepseek import FakeDeepSeek
from tests.test_wishes import BILALOV, DEMENKOV, KUZNETSOV, YARUNIN

FRIDAY = "13 ноября (пятница)"
NO_LLM = "the wish is read by code: DeepSeek is not called"


async def through_dislocation(chat: Chat) -> None:
    """From the «Что случилось» screen to the time choice, by buttons."""
    await chat.press(texts.TOPIC_LIST)
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")
    await chat.press("Для себя")
    await chat.press("Бытовая травма")
    await chat.press("В течение 7 дней")
    await chat.press("Нет, не обращались")


async def offered(chat: Chat, schedule: MockSchedule) -> list:
    """The slots behind every time button on the screen («Ближайшее» too)."""
    return [await schedule.get(b.data[2:]) for b in chat.buttons() if b.data.startswith("s:")]


async def session(chat: Chat) -> dict:
    async with chat.conversation.db() as s:
        return (await s.get(User, chat.user.id)).session


def screen_labels(chat: Chat) -> list[str]:
    """Buttons of the last message: the screen, not the script phrase said before it."""
    return [b.label for row in chat.replies[-1].buttons for b in row]


def local_day(slot) -> date:  # type: ignore[no-untyped-def]
    return slot.start.astimezone(TZ).date()


# --- the ten phrases ---------------------------------------------------------------------


async def test_1_doctor_and_day(make_chat, kb: KnowledgeBase, schedule: MockSchedule) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("Запиши меня к ярунину на завтра")
    assert fake.requests == [], NO_LLM
    assert texts.INFO_UNKNOWN not in chat.text
    assert "Пожелание: к Ярунину А. А., завтра, 13 ноября" in chat.text  # one line
    assert texts.ASK_COMPLAINT in chat.text  # «Что случилось?» stays

    await through_dislocation(chat)
    assert "Шаг 4 из 5" in chat.text and FRIDAY in chat.text  # straight to tomorrow
    assert "Врач смены: Ярунин Андрей Александрович (08:00–20:00)" in chat.text
    assert not chat.days() and texts.WISH_CLEAR in chat.labels()
    await chat.pick_period()
    slots = await offered(chat, schedule)
    assert slots and {s.doctor for s in slots} == {YARUNIN}
    assert {local_day(s) for s in slots} == {date(2026, 11, 13)}

    await chat.pick_time()
    await chat.press(texts.CONSENT_YES)
    await chat.say("петров иван сергеевич")
    await chat.say("12.03.1985")
    await chat.contact()
    assert "Врач: Ярунин Андрей Александрович" in chat.text
    assert (await session(chat)).get("wish") is None  # the wish ends with the booking


async def test_2_doctor_without_a_day(make_chat, schedule: MockSchedule) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("запишите к Кузнецову")
    assert "Пожелание: к Кузнецову Н. А." in chat.text and texts.ASK_COMPLAINT in chat.text
    await through_dislocation(chat)
    days = [b.label for b in chat.days()]
    # the nearest days he is on duty; on Friday he sees repeat visits only
    assert days[:3] == ["Сегодня, 12.11", "Сб, 14.11", "Вс, 15.11"]
    assert chat.nearest().label.startswith("Ближайшее: сегодня, 1")
    assert {s.doctor for s in await offered(chat, schedule)} == {KUZNETSOV}
    await chat.pick_day("Сб")
    assert [b.label for b in chat.periods()] == ["Вечер 18–23", "Ночь 23–8"]  # from 20:00
    await chat.pick_period()
    slots = await offered(chat, schedule)
    assert slots and {s.doctor for s in slots} == {KUZNETSOV}


async def test_3_day_and_evening(make_chat, schedule: MockSchedule) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("хочу на завтра вечером")
    assert "Пожелание: завтра, 13 ноября, вечером" in chat.text
    await through_dislocation(chat)
    assert FRIDAY in chat.text and not chat.days() and not chat.periods()  # the evening grid
    times = [b.label for b in chat.times()]
    assert times and all("18:00" <= t < "23:00" for t in times)


async def test_4_complaint_with_a_wish(make_chat, kb: KnowledgeBase, schedule) -> None:
    fake = FakeDeepSeek(
        {"type": "complaint", "area": "limb", "scenario": None,
         "candidates": ["ligament_rupture", "dislocation", "fracture"]}
    )  # fmt: skip
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("подвернул ногу, можно к Билалову в субботу утром?")
    sent = fake.sent()
    assert "[ВРАЧ]" in sent and "Билалов" not in sent and "билалов" not in sent
    assert "Пожелание: к Билалову Р. Ф., в субботу, 14 ноября, утром" in chat.text
    assert "Шаг 1 из 5" in chat.text and "Похоже на вывих" in chat.labels()  # the scenario

    await chat.press("Похоже на вывих")
    await chat.press("Для себя")
    await chat.press("Бытовая травма")
    await chat.press("В течение 7 дней")
    await chat.press("Нет, не обращались")
    assert "14 ноября (суббота)" in chat.text
    slots = await offered(chat, schedule)
    assert slots and {s.doctor for s in slots} == {BILALOV}
    assert all(s.start.astimezone(TZ).hour < 12 for s in slots)


async def test_5_doctor_is_off_that_day(make_chat, schedule: MockSchedule, clock) -> None:
    clock.now = datetime(2026, 11, 13, 12, 0, tzinfo=TZ)  # Friday: tomorrow he is not on duty
    chat = make_chat()
    await chat.start()
    await chat.say("запиши к Ярунину на завтра")
    await through_dislocation(chat)
    assert "Завтра, 14 ноября, Ярунин А. А. не дежурит." in chat.text  # said plainly
    labels = screen_labels(chat)
    assert labels[0].startswith("Ближайшее у врача: сегодня, 1")
    assert labels[1:4] == ["Завтра, 14.11 — другой врач", "Другой день", texts.WISH_CLEAR]
    assert not chat.days()

    (nearest,) = await offered(chat, schedule)  # the only time button: his nearest
    assert nearest.doctor == YARUNIN
    await chat.press("Завтра, 14.11 — другой врач")  # the same day, the doctor on duty
    assert "14 ноября (суббота)" in chat.text and "Пожелание: завтра, 14 ноября" in chat.text
    assert YARUNIN not in {s.doctor for s in await offered(chat, schedule)}


async def test_5_other_day_keeps_the_doctor(make_chat, schedule: MockSchedule, clock) -> None:
    clock.now = datetime(2026, 11, 13, 12, 0, tzinfo=TZ)
    chat = make_chat()
    await chat.start()
    await chat.say("запиши к Ярунину на завтра")
    await through_dislocation(chat)
    await chat.press("Другой день")
    assert "Пожелание: к Ярунину А. А." in chat.text and "завтра" not in chat.text
    assert [b.label for b in chat.days()][:2] == ["Сегодня, 13.11", "Вс, 15.11"]


async def test_6_unknown_doctor(make_chat, clinic) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("запиши к Иванову")
    assert chat.text.startswith(texts.NO_SUCH_DOCTOR)
    for doctor in clinic.schedule.doctors:  # the list from the data
        assert doctor in chat.text
    assert chat.labels()[:4] == [
        "Кузнецов Н. А.",
        "Ярунин А. А.",
        "Билалов Р. Ф.",
        "Деменков Д. С.",
    ]
    assert texts.ANY_DOCTOR in chat.labels()
    await chat.press("Ярунин А. А.")
    assert "Пожелание: к Ярунину А. А." in chat.text and texts.ASK_COMPLAINT in chat.text


async def test_7_text_on_the_day_step(make_chat, schedule: MockSchedule) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await through_dislocation(chat)
    assert chat.days()
    await chat.say("в пятницу после обеда")
    assert FRIDAY in chat.text and "Пожелание: завтра, 13 ноября, днём" in chat.text
    times = [b.label for b in chat.times()]
    assert times and all("12:00" <= t < "18:00" for t in times) and not chat.days()


async def test_8_reschedule_to_tomorrow(make_chat) -> None:
    from tests.test_conversation import book_dislocation, bookings

    chat = make_chat()
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    await chat.say("перенеси запись на завтра")
    assert texts.MY_TITLE in chat.text and texts.MOVE in chat.labels()  # «Мои записи»
    assert "завтра, 13 ноября" in chat.text
    await chat.press(texts.MOVE)
    assert FRIDAY in chat.text and not chat.days()  # tomorrow at once
    await chat.pick_time()
    (booking,) = await bookings(chat.conversation.db)
    assert booking.start_at.astimezone(TZ).date() == date(2026, 11, 13)


async def test_9_who_is_on_duty_tomorrow(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("кто завтра принимает?")
    assert fake.requests == [], NO_LLM
    assert chat.text == (
        "Завтра, 13 ноября, в травмпункте принимают:\n"
        "• до 08:00 — Билалов Руслан Фярисович\n"
        "• 08:00–20:00 — Ярунин Андрей Александрович\n"
        "• с 20:00 — Деменков Денис Сергеевич\n"
        "Повторные приёмы, кабинет «Травматолог 2»: Кузнецов Николай Александрович "
        "(08:00–20:00)"
    )
    assert chat.labels() == [texts.BOOK_THIS_DAY, texts.MENU_HOME]
    await chat.press(texts.BOOK_THIS_DAY)
    assert "Пожелание: завтра, 13 ноября" in chat.text and texts.ASK_COMPLAINT in chat.text


async def test_10_head_injury_is_routed_and_the_wish_dropped(make_chat, kb) -> None:
    fake = FakeDeepSeek({"type": "complaint", "area": "head", "scenario": "head_trauma"})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("Ударился головой, запишите к Ярунину")
    assert texts.ASK_AUDIENCE in chat.text
    await chat.press("Для себя")
    assert "Рижский пер., 16" in chat.text  # script 5.5: where to go, no booking
    assert "Пожелание" not in chat.text and "Ярунин" not in chat.text
    assert not chat.days() and not chat.times()
    assert (await session(chat)).get("wish") is None


# --- questions about the schedule ---------------------------------------------------------


async def test_when_a_doctor_works(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("когда работает Ярунин?")
    assert fake.requests == [], NO_LLM
    lines = chat.text.split("\n")
    assert lines[0] == "Ярунин Андрей Александрович принимает в ближайшие дни:"
    assert lines[1:4] == [
        "• завтра, 13.11 — 08:00–20:00",
        "• сб, 14.11 — 08:00–20:00, повторные приёмы",
        "• вс, 15.11 — с 20:00",
    ]
    assert chat.labels() == [texts.BOOK_THIS_DOCTOR, texts.MENU_HOME]
    await chat.press(texts.BOOK_THIS_DOCTOR)
    assert "Пожелание: к Ярунину А. А." in chat.text


async def test_is_a_doctor_on_duty_that_day(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("Кузнецов завтра работает?")
    assert chat.text.startswith(
        "Завтра, 13 ноября, Кузнецов Н. А. принимает: 08:00–20:00, повторные приёмы."
    )
    await chat.say("Ярунин сегодня дежурит?")  # his night shift ended at 08:00
    assert chat.text.startswith("Сегодня, 12 ноября, Ярунин А. А. не принимает.")
    assert "• завтра, 13.11 — 08:00–20:00" in chat.text


async def test_is_there_time_tonight(make_chat, schedule: MockSchedule) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("есть время сегодня вечером?")
    assert chat.text.startswith("Сегодня, 12 ноября, вечером есть свободное время: 18:")
    assert "Врач" in chat.text and texts.FREE_NOTE in chat.text
    assert chat.labels() == [texts.BOOK_THIS_TIME, texts.MENU_HOME]
    await chat.press(texts.BOOK_THIS_TIME)
    assert "Пожелание: сегодня, 12 ноября, вечером" in chat.text


async def test_no_time_is_said_plainly(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("есть время сегодня утром?")  # it is noon already
    assert chat.text.startswith("Сегодня, 12 ноября, утром свободного времени нет.")
    assert "Ближайшее: сегодня, 1" in chat.text
    assert chat.labels() == [texts.MENU_BOOK, texts.MENU_HOME]
    await chat.press(texts.MENU_BOOK)  # a time that is gone is not kept as a wish
    assert "Пожелание" not in chat.text and texts.ASK_COMPLAINT in chat.text


async def test_doctor_off_on_the_asked_day_keeps_the_doctor_only(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("Ярунин сегодня дежурит?")
    await chat.press(texts.BOOK_THIS_DOCTOR)
    assert "Пожелание: к Ярунину А. А." in chat.text and "сегодня" not in chat.text


async def test_nearest_free_time(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("когда ближайшее свободное время?")
    assert chat.text.startswith("Ближайшее: сегодня, 1")
    assert "Врач смены: Кузнецов Николай Александрович" in chat.text


async def test_a_schedule_question_inside_the_wizard_keeps_the_step(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await through_dislocation(chat)
    await chat.say("кто принимает завтра?")
    answer, screen = chat.replies
    assert answer.text.startswith("Завтра, 13 ноября, в травмпункте принимают:")
    assert "Шаг 4 из 5" in screen.text and chat.days()  # the day choice is still here


async def test_schedule_question_found_by_route(make_chat, kb: KnowledgeBase) -> None:
    """Wordings the code does not know come back from DeepSeek as «schedule_question»."""
    fake = FakeDeepSeek({"type": "schedule_question"})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("подскажите, в воскресенье получится попасть?")
    assert len(fake.requests) == 1  # one route call, no «answer» call
    assert chat.text.startswith("В воскресенье, 15 ноября, есть свободное время: ")
    assert chat.labels() == [texts.BOOK_THIS_TIME, texts.MENU_HOME]
    assert texts.INFO_UNKNOWN not in chat.text


# --- the wish and the rules ---------------------------------------------------------------


async def test_wish_is_dropped_with_one_button(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("запишите к Кузнецову")
    await through_dislocation(chat)
    await chat.press(texts.WISH_CLEAR)
    assert "Пожелание" not in chat.text and texts.WISH_CLEAR not in chat.labels()
    assert [b.label for b in chat.days()][:2] == ["Сегодня, 12.11", "Завтра, 13.11"]
    await chat.press(texts.BACK)  # «Назад» does not bring the wish back
    await chat.press("Нет, не обращались")
    assert "Пожелание" not in chat.text


async def test_repeat_visit_rules_come_before_the_wish(make_chat, schedule) -> None:
    """04-09, sick leave not open: only the «Повторный приём» windows or the second cabinet."""
    chat = make_chat()
    await chat.start()
    await chat.say("запишите к Кузнецову")
    await chat.press(texts.TOPIC_LIST)
    await chat.press("Уже лечусь у вас")
    await chat.press("Повторный приём")
    await chat.press("Для себя")
    await chat.press("Да, в вашем травмпункте")
    await chat.press("Нет")
    assert chat.days()
    for button in chat.days()[:3]:
        await chat.data(button.data)
        if chat.periods():
            await chat.pick_period()
        slots = await offered(chat, schedule)
        assert slots and all(s.doctor == KUZNETSOV for s in slots)
        assert all(eligible(s, "repeat_window") for s in slots)
        await chat.press(texts.BACK)
        if not chat.days():
            await chat.press(texts.BACK)


async def test_wish_typed_as_an_answer_goes_on_with_the_question(make_chat, kb) -> None:
    fake = FakeDeepSeek({"facts": {"injury_age": "within_7_days"}, "unclear": False})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press(texts.TOPIC_LIST)
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")
    await chat.press("Для себя")
    await chat.press("Бытовая травма")
    assert "Когда была получена травма?" in chat.text
    await chat.say("вчера, запишите к Кузнецову на завтра")
    assert "[ВРАЧ]" in fake.sent() and "Кузнецов" not in fake.sent()
    assert "Обращались ли к врачу" in chat.text  # the answer «вчера» was taken
    assert "Пожелание: к Кузнецову Н. А., завтра, 13 ноября" in chat.text


async def test_only_a_wish_on_a_question_step_repeats_the_question(make_chat, kb) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press(texts.TOPIC_LIST)
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")
    await chat.press("Для себя")
    await chat.say("запишите на завтра к Билалову")
    assert fake.requests == [], NO_LLM
    assert "Как получена травма" in chat.text or "Бытовая травма" in chat.labels()
    assert "Пожелание: к Билалову Р. Ф., завтра, 13 ноября" in chat.text


async def test_answer_to_a_question_is_not_taken_for_a_wish(make_chat, kb) -> None:
    fake = FakeDeepSeek({"facts": {"injury_age": "within_7_days"}, "unclear": False})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press(texts.TOPIC_LIST)
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")
    await chat.press("Для себя")
    await chat.press("Бытовая травма")
    await chat.say("в пятницу")  # when the injury happened
    assert len(fake.requests) == 1 and "Пожелание" not in chat.text


async def test_day_beyond_the_schedule(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("запишите на 15 декабря")
    await through_dislocation(chat)
    assert "Запись на 15 декабря пока не открыта: расписание есть по 2 декабря." in chat.text
    assert screen_labels(chat)[0].startswith("Ближайшее: сегодня, 1")
    assert texts.OTHER_DAY in chat.labels()
    await chat.press(texts.OTHER_DAY)
    assert chat.days() and "Пожелание" not in chat.text


async def test_time_of_day_that_is_gone(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("запишите на сегодня утром")  # it is noon
    await through_dislocation(chat)
    note, screen = chat.replies[-2:]
    assert note.text == (
        "Сегодня, 12 ноября, утром свободного времени по Вашему пожеланию нет. "
        "Есть другое время в этот день:"
    )
    assert "12 ноября (четверг)" in screen.text and chat.periods()
    assert "Пожелание: сегодня, 12 ноября" in screen.text and "утром" not in screen.text


async def test_exact_time_is_offered_first(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("запишите на завтра в 14:30")
    assert "Пожелание: завтра, 13 ноября, около 14:30" in chat.text
    await through_dislocation(chat)
    assert FRIDAY in chat.text and chat.times()
    assert screen_labels(chat)[0].startswith("Ближе всего к 14:30: 1")


async def test_time_window(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("хочу завтра после 18")
    assert "Пожелание: завтра, 13 ноября, после 18:00" in chat.text
    await through_dislocation(chat)
    if chat.periods():
        await chat.pick_period()
    times = [b.label for b in chat.times()]
    assert times and all(t >= "18:00" for t in times)


async def test_doctor_typed_on_the_time_step_keeps_the_day(make_chat, schedule) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await through_dislocation(chat)
    await chat.pick_day("Завтра")
    await chat.say("вечером")  # the day on the screen stays
    assert FRIDAY in chat.text
    times = [b.label for b in chat.times()]
    assert times and all("18:00" <= t < "23:00" for t in times)
    await chat.say("к Деменкову")
    slots = await offered(chat, schedule)
    assert slots and {s.doctor for s in slots} == {DEMENKOV}


async def test_unknown_doctor_with_a_complaint_goes_on(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek({"type": "complaint", "area": "limb", "scenario": "fracture"})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("сломал руку, запишите к Иванову")
    note, screen = chat.replies
    assert note.text.startswith(texts.NO_SUCH_DOCTOR) and "Ярунин Андрей Александрович" in note.text
    assert texts.ASK_AUDIENCE in screen.text
    assert "Иванов" not in fake.sent()  # masked like any other surname


async def test_old_wish_is_forgotten(make_chat, clock) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("запишите к Кузнецову")
    clock.now = NOW + timedelta(hours=4)
    await through_dislocation(chat)
    assert "Пожелание" not in chat.text and chat.days()[1].label == "Завтра, 13.11"


async def test_menu_button_starts_without_the_old_wish(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("кто завтра принимает?")
    await chat.conversation.menu(chat.user)
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    assert "Пожелание" not in chat.text


async def test_wish_after_a_schedule_answer_is_kept_for_the_request(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("кто завтра принимает?")
    await chat.say("хочу записаться")
    assert "Пожелание: завтра, 13 ноября" in chat.text and texts.ASK_COMPLAINT in chat.text


@pytest.mark.parametrize(
    "phrase",
    [
        "Запиши меня к ярунину на завтра",
        "запишите к Кузнецову",
        "хочу на завтра вечером",
        "запиши к Иванову",
        "кто завтра принимает?",
        "когда работает Ярунин?",
        "есть время сегодня вечером?",
        "Можно записаться именно к Кузнецову?",
    ],
)
async def test_requests_never_get_no_information(make_chat, kb, phrase: str) -> None:
    """The reported dead end: «У меня нет этой информации» for the simplest request."""
    fake = FakeDeepSeek({"type": "clinic_question"}, {"found": False})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say(phrase)
    assert texts.INFO_UNKNOWN not in chat.text and fake.requests == []
    assert chat.buttons(), "every answer has a button that leads on"
