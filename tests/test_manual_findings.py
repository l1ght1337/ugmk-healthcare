"""The dialogs from the customer's Telegram run of 13.11 (code 0b53aff): items 2, 3 and 4.

The clinic clock is Thursday, 12 November 2026, 12:00, unless a test sets another one.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.bot import slots_view, texts
from app.db.models import User
from app.flow.loader import KnowledgeBase
from app.schedule.base import Slot
from tests.chat_harness import Chat
from tests.conftest import NOW, TZ
from tests.fake_deepseek import FakeDeepSeek
from tests.test_conversation import book_dislocation, bookings

WISH = "Пожелание: к Ярунину А. А., завтра, 13 ноября"
FALL = "На что больше похоже?"
LIMB = "Что случилось с рукой или ногой?"
# what the real DeepSeek answered to «ударился болит сильно» and «болит рука» (13.11)
FALL_KINDS = ["fracture", "dislocation", "ligament_rupture"]
HIT = {
    "type": "complaint",
    "area": None,
    "scenario": None,
    "candidates": [*FALL_KINDS, "head_trauma", "chest_abdomen"],
    "clarify": "Подскажите, пожалуйста, что и где Вы ударили?",
}
ARM = {
    "type": "complaint",
    "area": "limb",
    "scenario": None,
    "candidates": [*FALL_KINDS, "cut_sutures"],
    "clarify": "Подскажите, пожалуйста, что случилось с рукой?",
}


async def session(chat: Chat) -> dict:
    async with chat.conversation.db() as s:
        return dict((await s.get(User, chat.user.id)).session or {})


async def with_wish_on_limb_topics(make_chat, kb, *replies):  # type: ignore[no-untyped-def]
    fake = FakeDeepSeek(*replies)
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("Запиши меня к ярунину на завтра")
    await chat.press("Рука или нога")
    assert LIMB in chat.text and WISH in chat.text
    return chat, fake


# --- 2. a text on the topic screen ---------------------------------------------------------


async def test_the_dialog_of_item_2(make_chat, kb: KnowledgeBase) -> None:
    chat, _ = await with_wish_on_limb_topics(make_chat, kb, HIT, ARM)
    await chat.say("ударился болит сильно")
    assert FALL in chat.text and WISH in chat.text  # the area is known: straight to the kinds
    assert "своими словами" not in chat.text
    assert chat.labels()[:2] == ["Похоже на вывих", "Похоже на перелом"]
    await chat.say("болит рука")  # nothing narrower: the same choice, and a line why
    why, screen = chat.replies
    assert why.text == "Подскажите, пожалуйста, что случилось с рукой?" and not why.buttons
    assert FALL in screen.text and WISH in screen.text  # not back to «Что случилось с рукой…»
    assert chat.labels()[:2] == ["Похоже на вывих", "Похоже на перелом"]
    assert (await session(chat))["wish"]["doctor"]


@pytest.mark.parametrize(
    "reply",
    [
        HIT,
        {"type": "complaint", "area": None, "scenario": None, "candidates": []},
        {"type": "complaint", "area": "limb", "scenario": None, "candidates": []},
        None,  # DeepSeek does not answer: the word list
    ],
    ids=["route: candidates", "route: nothing", "route: the area only", "no DeepSeek"],
)
async def test_hit_with_a_known_area_asks_what_it_looks_like(make_chat, kb, reply) -> None:
    chat, _ = await with_wish_on_limb_topics(make_chat, kb, *([reply] if reply else []))
    await chat.say("ударился, болит сильно")
    assert FALL in chat.text and WISH in chat.text
    assert (await session(chat))["area"] == "limb"


@pytest.mark.parametrize(
    ("word", "then"),
    [
        ("ударился", FALL),  # «Упал, ударился, подвернул»
        ("упал", FALL),
        ("порез", texts.ASK_AUDIENCE),  # «Порез, рана» → the scenario
        ("укус", texts.ASK_AUDIENCE),  # «Укус животного»
        ("гипс", "Что нужно сделать?"),  # «Гипс, ортез, скотчкаст»
        ("ожог", texts.ASK_AUDIENCE),
    ],
)
async def test_a_word_of_one_button_presses_it(make_chat, kb, word: str, then: str) -> None:
    fake = FakeDeepSeek({"type": "complaint", "area": "limb", "scenario": None})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("болит рука")  # «Упал, ударился, подвернул», «Порез, рана», «Гипс, ортез…»
    assert LIMB in chat.text and "Упал, ударился, подвернул" in chat.labels()
    await chat.say(word)
    assert len(fake.requests) == 1 and then in chat.text, chat.text  # no DeepSeek for the word


@pytest.mark.parametrize(
    ("word", "scenario"),
    [("ожог", "burns"), ("укус", "animal_bite"), ("рана", "cut_sutures")],
)
async def test_a_word_goes_through_the_group_to_its_topic(make_chat, kb, word, scenario) -> None:
    """«Рана, ожог, укус» opens a menu where the same word names one topic: no extra tap."""
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.say(word)
    (screen,) = chat.replies
    assert fake.requests == [] and texts.ASK_AUDIENCE in screen.text
    assert (await session(chat))["dialog"]["scenario_id"] == scenario


@pytest.mark.parametrize(
    ("first", "word"),
    [
        (None, "ребёнок"),  # not «Ребёнок до 6 месяцев»
        (None, "уже"),  # not «Уже лечусь у вас»
        ("Голова, лицо, глаза, уши", "травма"),  # not «Травма глаза»
        ("Рана, ожог, укус", "укол"),  # not «Укол чужой иглой»
        ("Рана, ожог, укус", "плохо"),  # not «Нагноение, плохо заживает»
        ("Шея, спина, грудь, живот, пах", "удар"),  # not «Удар в грудь или живот»
        ("Рука или нога", "гипс"),  # two buttons: «Наложить гипс», «Снять гипс»
    ],
)
async def test_a_word_does_not_press_a_narrower_button(make_chat, kb, first, word) -> None:
    fake = FakeDeepSeek({"type": "complaint", "area": None, "scenario": None})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    if first:
        await chat.press(first)
    await chat.say(word)
    assert len(fake.requests) == 1  # no button fits: DeepSeek reads the text
    assert (await session(chat))["dialog"]["scenario_id"] is None


ROUTES = {
    "complaint": HIT,
    "complaint, nothing else": {"type": "complaint", "area": None, "scenario": None},
    "other_medical": {"type": "other_medical"},
    "offtopic": {"type": "offtopic"},
    "medical_question": {"type": "medical_question"},
    "about_bot": {"type": "about_bot"},
    "no DeepSeek": None,
}


@pytest.mark.parametrize("name", ROUTES)
@pytest.mark.parametrize("text", ["ударился болит сильно", "болит рука", "не могу наступить"])
async def test_free_text_on_step_1_never_drops_the_wish(make_chat, kb, name, text) -> None:
    """Whatever DeepSeek makes of a description, the booking and the wish stay."""
    reply = ROUTES[name]
    chat, _ = await with_wish_on_limb_topics(make_chat, kb, *([reply] if reply else []))
    await chat.say(text)
    state = await session(chat)
    assert state["wizard"] and state["step"] in ("complaint", "topic", "describe"), state["step"]
    assert state["wish"]["doctor"] and WISH in chat.text
    assert chat.buttons()


async def test_wish_does_not_expire_while_the_booking_goes_on(make_chat, clock) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("запишите к Кузнецову")
    clock.now = NOW + timedelta(hours=5)  # the patient came back in the evening
    await chat.press("Рука или нога")
    assert "Пожелание: к Кузнецову Н. А." in chat.text


# --- 3. «отмени и запиши на воскресенье» is a move ---------------------------------------


async def one_booking(make_chat, **kw):  # type: ignore[no-untyped-def]
    chat = make_chat(**kw)
    await book_dislocation(chat)  # today, Thursday 12 November
    await chat.press(texts.PAYMENT_SELF)
    return chat


async def test_cancel_and_book_for_sunday_opens_sunday_at_once(make_chat, kb) -> None:
    fake = FakeDeepSeek()
    chat = await one_booking(make_chat, assistant=fake.assistant(kb))
    (before,) = await bookings(chat.conversation.db)
    await chat.say("эту запись отмени и давай на воскресенье запиши меня когда можно")
    assert fake.requests == []
    line, screen = chat.replies
    assert line.text.startswith("Переношу запись с четверга, 12 ноября, 1") and not line.buttons
    assert "15 ноября (воскресенье)" in screen.text
    labels = [b.label for row in screen.buttons for b in row]
    assert labels[0].startswith("Ближайшее: вс, 15.11, ")
    assert labels[-2:] == [texts.KEEP_AS_IS, texts.MENU]
    (still,) = await bookings(chat.conversation.db)
    assert (still.status, still.slot_id) == ("active", before.slot_id)  # held until a new time

    await chat.pick_time()
    (moved,) = await bookings(chat.conversation.db)
    assert moved.start_at.astimezone(TZ).date() == date(2026, 11, 15) and moved.status == "active"
    assert "Готово, запись перенесена на 15 ноября (воскресенье)" in chat.text


async def test_move_to_a_day_typed_as_text(make_chat) -> None:
    chat = await one_booking(make_chat)
    await chat.say("перенеси на завтра")
    line, screen = chat.replies
    assert line.text.startswith("Переношу запись с четверга, 12 ноября, 1")
    assert "13 ноября (пятница)" in screen.text and texts.KEEP_AS_IS in chat.labels()
    await chat.press(texts.KEEP_AS_IS)
    assert chat.text == texts.KEPT and texts.MENU_BOOK in chat.labels()
    (kept,) = await bookings(chat.conversation.db)
    assert kept.start_at.astimezone(TZ).date() == date(2026, 11, 12)
    assert (await session(chat))["step"] == "idle"


async def test_back_inside_a_move_and_no_back_on_its_first_screen(make_chat) -> None:
    chat = await one_booking(make_chat)
    await chat.say("перенеси на воскресенье")
    assert texts.BACK not in chat.labels()  # nothing behind: «Оставить как есть» is the way out
    assert chat.periods() and chat.nearest().label >= "Ближайшее: вс, 15.11, 08:00"  # not 00:00
    await chat.say("вечером")  # typed: the evening grid
    assert all("18:00" <= b.label < "23:00" for b in chat.times())
    assert chat.labels()[-3:] == [texts.KEEP_AS_IS, texts.BACK, texts.MENU]
    await chat.press(texts.BACK)  # the parts of the day again, not the same grid
    assert len(chat.periods()) > 1 and "вечером" not in chat.text
    assert "Пожелание: в воскресенье, 15 ноября" in chat.text
    assert (await session(chat))["reschedule_of"]


async def test_nearest_of_today_named_as_a_wish_is_the_first_time(make_chat) -> None:
    chat = await one_booking(make_chat)
    await chat.say("перенеси на сегодня")
    first = chat.times()[0].label if chat.times() else None
    label = chat.nearest().label
    assert label.startswith("Ближайшее: сегодня, 1") and (first is None or first in label)


async def test_with_two_bookings_the_bot_asks_which_one(make_chat) -> None:
    chat = await one_booking(make_chat)
    await chat.press(texts.MENU_BOOK)
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")
    await chat.press("Для себя")
    await chat.press("Бытовая травма")
    await chat.press("В течение 7 дней")
    await chat.press("Нет, не обращались")
    await chat.pick_slot("Завтра")
    await chat.press("Петров Иван")
    await chat.press(texts.PAYMENT_SELF)
    await chat.say("перенеси запись на воскресенье")
    (ask,) = chat.replies
    assert ask.text == texts.MOVE_WHICH
    assert [label[:9] for label in chat.labels()[:2]] == ["Чт, 12.11", "Пт, 13.11"]
    assert chat.labels()[-1] == texts.MENU_HOME
    await chat.press(chat.labels()[1])
    assert "Переношу запись с пятницы, 13 ноября" in chat.text
    assert "15 ноября (воскресенье)" in chat.text


async def test_cancel_without_a_new_day_shows_the_booking(make_chat) -> None:
    chat = await one_booking(make_chat)
    for text in ("отмени запись", "отмени запись на сегодня", "не смогу прийти сегодня"):
        await chat.say(text)
        (only,) = chat.replies
        assert only.text.startswith(texts.MY_ONE) and "При переносе" not in only.text, text
        assert chat.labels() == [texts.MOVE, texts.CANCEL]


async def test_explanation_comes_after_the_booking(make_chat, kb: KnowledgeBase) -> None:
    """A day named without «перенеси»: DeepSeek says «my bookings», the day is kept."""
    fake = FakeDeepSeek({"type": "menu_action", "action": "my_bookings"})
    chat = await one_booking(make_chat, assistant=fake.assistant(kb))
    await chat.say("с записью не получается, можно на воскресенье?")
    (only,) = chat.replies
    title, item, note = only.text.split("\n", 2)
    assert title == texts.MY_ONE and item.startswith("12 ноября (четверг) в 1")
    assert note.strip() == (
        "При переносе сразу покажу время по пожеланию: в воскресенье, 15 ноября."
    )


async def test_no_bookings_to_move(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("перенеси запись на завтра")
    assert chat.text == texts.MY_EMPTY and texts.MENU_BOOK in chat.labels()


# --- 4. «какие доктора вообще работают» ----------------------------------------------------

DOCTORS = (
    "В травмпункте принимают:\n"
    "• Кузнецов Николай Александрович — руководитель травмпункта, врач-травматолог-ортопед, "
    "заведующий травмпунктом\n"
    "• Ярунин Андрей Александрович — врач-травматолог-ортопед, врач физической и "
    "реабилитационной медицины\n"
    "• Билалов Руслан Фярисович — врач-травматолог-ортопед\n"
    "• Деменков Денис Сергеевич — врач-травматолог-ортопед\n\n"
    "Сейчас дежурит: Кузнецов Николай Александрович.\n"
    "Расписание врача — кнопкой с его фамилией."
)
DOCTOR_BUTTONS = ["Кузнецов Н. А.", "Ярунин А. А.", "Билалов Р. Ф.", "Деменков Д. С."]


@pytest.mark.parametrize(
    "text",
    ["а какие доктора вообще раюотают", "какие у вас врачи", "кто у вас принимает"],
)
async def test_who_works_here_lists_the_doctors(make_chat, kb: KnowledgeBase, text: str) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say(text)
    assert fake.requests == [] and chat.text == DOCTORS
    assert chat.labels() == [texts.MENU_BOOK, *DOCTOR_BUTTONS, texts.MENU_HOME]
    await chat.press("Ярунин А. А.")
    assert chat.text.startswith("Ярунин Андрей Александрович принимает в ближайшие дни:")
    assert chat.labels() == [texts.BOOK_THIS_DOCTOR, texts.MENU_HOME]


async def test_doctors_question_found_by_route_is_not_about_free_time(make_chat, kb) -> None:
    fake = FakeDeepSeek({"type": "schedule_question"})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("расскажите про докторов травмпункта")
    assert chat.text == DOCTORS and "Ближайшее" not in chat.text


async def test_doctors_list_inside_a_booking_keeps_the_step(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press("Рука или нога")
    await chat.say("а какие у вас врачи?")
    answer, screen = chat.replies
    assert answer.text.startswith("В травмпункте принимают:") and LIMB in screen.text


def test_time_before_eight_tomorrow_is_tonight_in_the_evening() -> None:
    def slot(day: int, hour: int) -> Slot:
        start = datetime(2026, 11, day, hour, 0, tzinfo=TZ)
        return Slot(
            "x", start, start + timedelta(minutes=30), "Врач", "Травматолог 1", False, False
        )

    late = datetime(2026, 11, 13, 23, 39, tzinfo=TZ)
    assert slots_view.nearest_when(slot(14, 1), late, TZ) == "сегодня ночью, 01:00"
    assert slots_view.nearest_when(slot(14, 7), late, TZ) == "сегодня ночью, 07:00"
    assert slots_view.nearest_when(slot(14, 9), late, TZ) == "завтра, 09:00"
    assert slots_view.nearest_when(slot(13, 23), late, TZ) == "сегодня, 23:00"
    noon = datetime(2026, 11, 13, 12, 0, tzinfo=TZ)
    assert (
        slots_view.nearest_when(slot(14, 1), noon, TZ) == "завтра, 01:00"
    )  # by day it is tomorrow
    night = datetime(2026, 11, 14, 1, 39, tzinfo=TZ)
    assert slots_view.nearest_when(slot(14, 3), night, TZ) == "сегодня, 03:00"
    assert slots_view.nearest_when(slot(16, 1), late, TZ) == "пн, 16.11, 01:00"


async def test_nearest_time_late_in_the_evening(make_chat, clock) -> None:
    clock.now = datetime(2026, 11, 13, 23, 39, tzinfo=TZ)
    chat = make_chat()
    await chat.start()
    await chat.say("когда ближайшее свободное время?")
    assert chat.text.startswith("Ближайшее: сегодня ночью, 01:00.")
    await chat.press(texts.MENU_BOOK)
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")
    await chat.press("Для себя")
    await chat.press("Бытовая травма")
    await chat.press("В течение 7 дней")
    await chat.press("Нет, не обращались")
    assert chat.nearest().label == "Ближайшее: сегодня ночью, 01:00"
