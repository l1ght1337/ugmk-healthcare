"""The patient simulation of 14.11 (docs/simulation_report.md), fixes 5–9.

5. «Передать администратору?»: «да, передайте», a question and a request to book are dealt
   with, not answered with «выберите кнопкой».
6. After a route or a handoff the same complaint does not run the scenario again; «когда
   позвонят?» gets the administrators' hours; «спасибо, буду ждать» is not added to the
   request; «Подробнее» has buttons under it.
7. «В ближайшее время» is not a question about the schedule, «до завтра терпеть» is not a wish.
8. Two days in a request to cancel: the bot asks which booking and offers none itself.
9. Two patients in one message: the first is booked, the second is offered after the card
   with «Записать ещё одного»; who is first is never chosen silently.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest

from app.bot import texts
from app.config import ClinicData
from app.flow.commands import (
    asks_when_admin,
    find_audience,
    find_small_talk,
    names_several,
    wants_handoff,
)
from app.flow.loader import KnowledgeBase
from app.flow.wishes import days_named
from app.flow.wishes import parse as parse_wish
from tests import intent_cases as m
from tests.chat_harness import Chat
from tests.conftest import NOW
from tests.fake_deepseek import FakeDeepSeek
from tests.test_conversation import book_dislocation, bookings
from tests.test_intents import session, to_step
from tests.test_manual_findings_3 import booked
from tests.test_wish_flow import through_dislocation

TODAY = date(2026, 11, 12)
MENU = [texts.MENU_BOOK, texts.MENU_ASK, texts.MENU_MY, texts.MENU_ADMIN]
NEEDLE = {"type": "complaint", "area": "limb", "scenario": "needle_injury",
          "candidates": ["needle_injury"], "facts": {}, "clarify": None}  # fmt: skip
LIGAMENT = {"type": "complaint", "area": "limb", "scenario": "ligament_rupture",
            "candidates": ["ligament_rupture"], "facts": {}, "clarify": None}  # fmt: skip


async def to_offer(chat: Chat) -> None:
    """A confirmed rupture of ligaments: the route and «Передать администратору» / «Не нужно»."""
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    for label in ("Рука или нога", "Растяжение или разрыв связок", "Для себя", "Бытовая травма",
                  "Подтвердил врач или МРТ"):  # fmt: skip
        await chat.press(label)
    assert (await session(chat))["step"] == "offer_admin"
    assert chat.labels()[-2:] == [texts.HANDOFF_YES, texts.HANDOFF_NO]


async def handed(chat: Chat) -> None:
    """The request has gone to the administrators: № 1."""
    await to_offer(chat)
    await chat.press(texts.HANDOFF_YES)
    await chat.press(texts.CONSENT_YES)
    await chat.contact()
    assert "обращение № 1" in chat.text and (await session(chat))["step"] == "idle"


# --- 5. «Передать администратору?» --------------------------------------------------------------

CERTIFICATE = (
    "Да, передайте, пожалуйста, администратору. И если можно, уточните, что мне нужна именно "
    "справка в бассейн и освобождение от физкультуры. Спасибо!"
)


@pytest.mark.parametrize(
    "said",
    [
        CERTIFICATE,
        "передайте",
        "да, передавайте",
        "Передайте, пожалуйста. И скажите честно: мне сегодня ещё можно куда-то успеть?",
        "пусть администратор позвонит",
        "хорошо, пусть свяжется",
    ],
)
async def test_yes_in_words_passes_the_request(make_chat, said: str) -> None:
    assert wants_handoff(said) is True
    chat = make_chat()
    await to_offer(chat)
    await chat.say(said)
    state = await session(chat)
    assert (state["step"], state["resume"]) == ("consent", "handoff")  # consent, then the phone
    assert texts.PICK_BUTTON_SHORT not in chat.text


async def test_what_is_said_with_the_yes_goes_to_the_administrator(make_chat) -> None:
    chat = make_chat()
    await to_offer(chat)
    await chat.say(CERTIFICATE)
    await chat.press(texts.CONSENT_YES)
    await chat.contact()
    assert "обращение № 1" in chat.text
    assert "справка в бассейн" in chat.admin[-1].text


@pytest.mark.parametrize("said", ["не надо, спасибо", "нет, не нужно передавать", "не передавайте"])
async def test_no_in_words_leaves_the_request(make_chat, said: str) -> None:
    assert wants_handoff(said) is False
    chat = make_chat()
    await to_offer(chat)
    await chat.say(said)
    assert (await session(chat))["step"] == "idle" and chat.labels()[-4:] == MENU
    assert not chat.admin


@pytest.mark.parametrize("said", ["а что он скажет?", "ну и погода", "спасибо", "передал уже врач"])
def test_what_is_neither_yes_nor_no(said: str) -> None:
    assert wants_handoff(said) is None


async def test_a_question_on_the_offer_is_not_answered_with_pick_a_button(make_chat) -> None:
    chat = make_chat()
    await to_offer(chat)
    await chat.say("а администратор мне на 13:30 запишет или тоже откажет? вы уж скажите прямо")
    line, offer = chat.replies
    assert line.text == texts.ADMIN_WILL_ANSWER and offer.text == texts.OFFER_HANDOFF
    assert chat.labels() == [texts.HANDOFF_YES, texts.HANDOFF_NO]
    state = await session(chat)
    assert state["step"] == "offer_admin" and "запишет или тоже откажет" in state["handoff_context"]


async def test_a_question_with_an_answer_in_the_reference(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek({"type": "clinic_question"}, {"found": True, "answer": "Круглосуточно."})
    chat = make_chat(assistant=fake.assistant(kb))
    await to_offer(chat)
    await chat.say("а травмпункт до скольки работает?")
    answer, offer = chat.replies
    assert answer.text == "Круглосуточно." and offer.text == texts.OFFER_HANDOFF
    assert (await session(chat))["step"] == "offer_admin"


async def test_a_request_to_book_on_the_offer_starts_a_booking(
    make_chat, kb: KnowledgeBase
) -> None:
    fake = FakeDeepSeek({"type": "menu_action", "action": "book"})
    chat = make_chat(assistant=fake.assistant(kb))
    await to_offer(chat)
    await chat.say("А кто может помочь? Мне бы записаться на приём к травматологу, если можно.")
    state = await session(chat)
    assert state["step"] == "complaint" and state["wizard"]
    assert texts.PICK_BUTTON_SHORT not in chat.text


async def test_words_that_say_nothing_still_get_the_hint(make_chat) -> None:
    chat = make_chat()
    await to_offer(chat)
    await chat.say("ну и погода")
    assert chat.replies[0].text == texts.PICK_BUTTON_SHORT


# --- 6. after a route or a handoff --------------------------------------------------------------


async def test_the_same_complaint_after_a_route_is_not_the_same_screen(
    make_chat, kb: KnowledgeBase, clock
) -> None:
    fake = FakeDeepSeek(NEEDLE, NEEDLE, NEEDLE)
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("я наступила на иглу от шприца, проколола стопу")
    assert "Центр борьбы со СПИДом" in chat.text

    clock.now = NOW + timedelta(minutes=45)  # much later the same words are a new complaint
    await chat.say("я наступила на иглу от шприца, проколола стопу")
    assert "Центр борьбы со СПИДом" in chat.text

    clock.now += timedelta(minutes=2)
    await chat.say("Это же и есть травма ноги! Вы меня примете или нет?")
    (only,) = chat.replies
    assert only.text == texts.NOT_BOOKED_HERE and "Центр борьбы" not in only.text
    assert chat.labels() == [texts.ASK_ADMIN_QUESTION, texts.MENU_HOME]
    state = await session(chat)
    assert state["step"] == "offer_admin" and "примете или нет" in state["handoff_context"]


async def test_the_same_complaint_after_a_handoff_does_not_ask_the_questions_again(
    make_chat, kb: KnowledgeBase, clinic: ClinicData
) -> None:
    fake = FakeDeepSeek(LIGAMENT)
    chat = make_chat(assistant=fake.assistant(kb))
    await handed(chat)
    posted = len(chat.admin)
    await chat.say("Так вы меня запишете или нет? Связки же порваны, нога болит")
    (only,) = chat.replies
    assert "Ваше обращение № 1 уже у администратора" in only.text
    assert "Как получена травма" not in chat.text and chat.labels() == MENU
    assert len(chat.admin) == posted and (await session(chat))["step"] == "idle"


@pytest.mark.parametrize(
    "said",
    [
        "А когда примерно администратор позвонит?",
        "сколько ждать звонка?",
        "когда со мной свяжутся?",
        "через сколько мне перезвонят",
        "Извините, вы не ответили на мой вопрос — примерно когда администратор позвонит? "
        "Сегодня ждать или нет?",
    ],
)
async def test_when_will_they_call_gets_the_hours(make_chat, clinic: ClinicData, said: str) -> None:
    assert asks_when_admin(said)
    chat = make_chat()
    await handed(chat)
    posted = len(chat.admin)
    await chat.say(said)
    hours = clinic.contacts.admin_working_hours
    expected = texts.HANDOFF_WHEN.format(start=f"{hours.start:%H:%M}", end=f"{hours.end:%H:%M}")
    assert chat.replies[0].text == expected and chat.labels() == MENU
    assert len(chat.admin) == posted  # nothing is added to the request


async def test_when_will_they_call_on_the_offer_keeps_the_offer(make_chat) -> None:
    chat = make_chat()
    await to_offer(chat)
    await chat.say("а когда администратор позвонит?")
    line, offer = chat.replies
    assert line.text.startswith("Администраторы отвечают") and offer.text == texts.OFFER_HANDOFF


@pytest.mark.parametrize("said", ["когда у меня приём?", "когда вы работаете?", "позвоните мне"])
def test_what_is_not_about_the_call(said: str) -> None:
    assert not asks_when_admin(said)


@pytest.mark.parametrize(
    ("said", "kind"),
    [
        ("Спасибо, больше вопросов нет. Буду ждать звонка администратора.", "thanks"),
        ("Спасибо, буду ждать звонка. Надеюсь, администратор скажет, куда мне обратиться.",
         "thanks"),
        ("Хорошо, буду ждать звонка. Спасибо, что передали обращение. Всего доброго!", "bye"),
        ("Понял, спасибо. На этом всё.", "thanks"),
        ("спасибо, до свидания", "bye"),
        ("спасибо", "thanks"),
    ],
)  # fmt: skip
def test_thanks_and_waiting_are_small_talk(said: str, kind: str) -> None:
    assert find_small_talk(said) == kind


@pytest.mark.parametrize(
    "said",
    [
        "Спасибо, а когда позвонят?",
        "спасибо, запишите ещё сына",
        "буду ждать, но нога болит очень сильно",
        "Спасибо, передайте администратору, что я жду",
        "Да не надо мне новую запись оформлять, я уже записан! Всё, спасибо, до свидания.",
    ],
)
def test_thanks_with_something_else_is_not_small_talk(said: str) -> None:
    assert find_small_talk(said) is None


async def test_thanks_after_a_handoff_is_not_added_to_the_request(make_chat, kb) -> None:
    chat = make_chat()
    await handed(chat)
    posted = len(chat.admin)
    await chat.say("Спасибо, больше вопросов нет. Буду ждать звонка администратора.")
    assert chat.text == texts.WELCOME and chat.labels() == MENU
    await chat.say("Хорошо, буду ждать звонка. Спасибо, что передали обращение. Всего доброго!")
    assert chat.text == kb.common.goodbye.text
    assert len(chat.admin) == posted


async def test_more_after_a_route_has_buttons(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek(NEEDLE)
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("я наступила на иглу от шприца, проколола стопу")
    await chat.press(texts.MORE)
    assert "травматолог-ортопед" in chat.text and chat.labels() == MENU


async def test_more_in_the_middle_of_a_booking_is_as_before(make_chat) -> None:
    chat = make_chat()
    await to_step(chat, m.DAYS)
    assert texts.MORE in chat.labels()  # the long script phrase above the day picker
    await chat.press(texts.MORE)
    (full,) = chat.replies  # the screen of the step stays above with its buttons
    assert not full.buttons and (await session(chat))["step"] == "days"


async def test_more_on_the_offer_keeps_the_offer(make_chat) -> None:
    chat = make_chat()
    await to_offer(chat)
    await chat.press(texts.MORE)
    full, offer = chat.replies
    assert "травматолог-ортопед" in full.text and offer.text == texts.OFFER_HANDOFF
    assert chat.labels() == [texts.HANDOFF_YES, texts.HANDOFF_NO]


# --- 7. the word lists --------------------------------------------------------------------------


def wish_of(text: str):
    doctors = ("Ярунин Андрей Александрович",)
    return parse_wish(text, doctors, TODAY, context="free", now=time(12, 0))


@pytest.mark.parametrize(
    "said",
    [
        "А если администратор не позвонит в ближайшее время, мне что, в центр этот ехать?",
        "в ближайшее время перезвоню",
        "надеюсь, в ближайшие дни всё решится",
    ],
)
def test_soon_is_not_a_question_about_the_schedule(said: str) -> None:
    assert wish_of(said).question is None


@pytest.mark.parametrize(
    "said",
    ["есть ближайшее время?", "какое ближайшее время для записи", "когда ближайшая запись?"],
)
def test_the_nearest_time_is_still_a_question(said: str) -> None:
    assert wish_of(said).question == "free"


@pytest.mark.parametrize(
    "said",
    [
        "Ну хоть скажите, это вообще срочно или можно до завтра терпеть? Клещ в коже",
        "можно подождать до пятницы?",
        "до завтра потерплю",
    ],
)
def test_until_a_day_is_not_a_wish_for_the_day(said: str) -> None:
    assert wish_of(said).wish.day is None


def test_a_day_asked_for_is_still_a_wish() -> None:
    assert wish_of("запишите на завтра").wish.day == TODAY + timedelta(days=1)
    assert wish_of("можно завтра до 12?").wish.day == TODAY + timedelta(days=1)


# --- 8. two days in a request to cancel ---------------------------------------------------------

TWO_DAYS = (
    "Здравствуйте! У меня две записи, на завтра и на воскресенье. Нужно отменить только ту, "
    "что на воскресенье, а вторую оставить."
)


def test_the_days_a_text_names() -> None:
    assert days_named(TWO_DAYS, TODAY) == [TODAY + timedelta(days=1), TODAY + timedelta(days=3)]
    assert days_named("отмени запись на воскресенье", TODAY) == [TODAY + timedelta(days=3)]
    assert days_named("отмени запись", TODAY) == []


async def test_two_days_in_a_request_to_cancel_are_asked_about(make_chat) -> None:
    chat = await booked(make_chat, "Завтра", "Вс")  # today, tomorrow and Sunday
    await chat.say(TWO_DAYS)
    (only,) = chat.replies
    assert only.text.startswith(texts.CANCEL_WHICH)
    labels = chat.labels()
    assert len([x for x in labels if x.startswith("Отменить: ")]) == 3  # every booking, none picked
    assert texts.CANCEL_ALL not in labels and labels[-1] == texts.MENU_HOME
    assert "Отменить запись на" not in chat.text
    assert len([b for b in await bookings(chat.conversation.db) if b.status == "active"]) == 3


async def test_one_day_in_a_request_to_cancel_is_asked_at_once(make_chat) -> None:
    chat = await booked(make_chat, "Завтра", "Вс")
    await chat.say("отмените запись на воскресенье")
    assert chat.text.startswith("Отменить запись на 15 ноября")


# --- 9. two patients ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "first"),
    [
        ("Сначала для себя, потом сына надо будет записать. Мне 44, запястье болит.", "self"),
        ("Для себя и для мужа. Сначала можно меня записать, потом его.", "self"),
        ("Сыну 12 лет, Егор. Сначала меня запишите, потом его.", "self"),
        ("сначала сына запишите, потом меня", "child"),
        ("запишите мужа после меня", "self"),
        ("меня и сына", None),
        ("Мы с сыном упали на катке. Надо записать обоих.", None),
        ("двоих детей надо записать", None),
    ],
)
def test_who_is_first_of_two(said: str, first: str | None) -> None:
    assert names_several(said)
    assert find_audience(said) == first


@pytest.mark.parametrize(
    "said",
    ["запишите ребёнка", "у мамы перелом", "меня укусила собака", "гуляла с сыном, он упал",
     "запишите меня", "сыну 7 лет", "обе ноги болят", "ушиб оба колена", "болело оба раза"],
)  # fmt: skip
def test_one_patient_is_one_patient(said: str) -> None:
    assert not names_several(said)


async def test_first_for_myself_then_the_son_is_not_read_as_a_child(make_chat) -> None:
    chat = make_chat()
    await to_step(chat, m.AUDIENCE)
    await chat.say("Сначала для себя, потом сына надо будет записать. Мне 44, запястье болит.")
    line, screen = chat.replies
    assert line.text == texts.ONE_BY_ONE
    assert "Как получена травма" in screen.text  # not «Сколько лет ребёнку?»
    state = await session(chat)
    assert state["dialog"]["facts"]["audience"] == "adult" and state["more"]


async def test_two_named_without_an_order_are_asked(make_chat) -> None:
    chat = make_chat()
    await to_step(chat, m.AUDIENCE)
    await chat.say("меня и сына")
    assert chat.replies[0].text == texts.ONE_BY_ONE
    state = await session(chat)
    assert state["step"] == "audience" and state["more"]
    assert texts.AUDIENCE_SELF in chat.labels()


async def test_two_ages_are_not_taken_silently(make_chat) -> None:
    chat = make_chat()
    await to_step(chat, m.AGE)
    await chat.say("Маше 7 лет, а Ване 10. Мне обоих надо записать")
    assert chat.replies[0].text == texts.ONE_BY_ONE
    state = await session(chat)
    assert state["dialog"]["age_months"] == 84 and state["more"]


async def test_the_second_patient_is_offered_after_the_card(make_chat) -> None:
    chat = make_chat()
    await to_step(chat, m.AUDIENCE)
    await chat.say("Сначала для себя, потом сына надо будет записать")
    for label in ("Бытовая травма", "В течение 7 дней", "Нет, не обращались"):
        await chat.press(label)
    await chat.pick_slot("Завтра")
    await chat.press(texts.CONSENT_YES)
    await chat.say("Агапов Михаил Степанович, 04.07.1980")
    await chat.contact()
    await chat.press(texts.PAYMENT_SELF)
    assert chat.text == texts.NEXT_PATIENT
    assert chat.labels() == [texts.BOOK_MORE, *MENU]

    await chat.press(texts.BOOK_MORE)  # a new booking, from «для кого»
    assert texts.ASK_AUDIENCE in chat.text and "Шаг" not in chat.text
    assert chat.labels() == [texts.WHOM_ADULT, texts.AUDIENCE_CHILD, texts.MENU]
    await chat.press(texts.AUDIENCE_CHILD)
    assert chat.text.startswith("Шаг 1 из 5") and texts.FOR_WHOM["child"] in chat.text
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")
    assert chat.text.endswith(texts.ASK_CHILD_AGE)  # «для кого» is not asked again
    await chat.say("12 лет")
    for label in ("Бытовая травма", "В течение 7 дней", "Нет, не обращались"):
        await chat.press(label)
    await chat.pick_slot("Завтра")
    assert texts.CONSENT_ITEM_GUARDIAN in chat.text  # a child: the representative's consent
    await chat.press(texts.CONSENT_YES)
    await chat.say("Агапов Егор Михайлович, 22.08.2014")  # the phone is not asked again
    assert "Егор Михайлович записан" in chat.text
    assert len(await bookings(chat.conversation.db)) == 2
    await chat.press(texts.PAYMENT_SELF)
    assert chat.text == f"{texts.THANKS} {texts_more_help(chat)}"  # no third patient was named


def texts_more_help(chat: Chat) -> str:
    return chat.conversation.kb.common.more_help.text


async def test_one_more_is_offered_after_every_booking(make_chat) -> None:
    chat = make_chat()
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    assert chat.text == f"{texts.THANKS} {texts_more_help(chat)}"
    assert chat.labels() == [texts.BOOK_MORE, *MENU]


async def test_one_more_adult_asks_neither_consent_nor_phone_nor_about_the_first(make_chat) -> None:
    chat = make_chat()
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    await chat.press(texts.BOOK_MORE)
    await chat.press(texts.WHOM_ADULT)
    assert texts.FOR_WHOM["adult"] in chat.text
    await through_dislocation(chat)
    assert texts.ALREADY_AGAIN not in chat.labels()  # «У Вас уже есть запись…» is not asked
    await chat.pick_slot("Завтра")
    assert chat.text.endswith(texts.PATIENT_PICK) and texts.CONSENT_YES not in chat.labels()
    await chat.press(texts.PATIENT_NEW)
    await chat.say("Захаров Игорь Семёнович, 05.05.1977")
    assert "Игорь Семёнович записан" in chat.text
    assert len(await bookings(chat.conversation.db)) == 2


@pytest.mark.parametrize(
    ("said", "whom"),
    [("для сына", "child"), ("ребёнка", "child"), ("мужа", "adult"), ("для взрослого", "adult")],
)
async def test_whom_typed_in_words(make_chat, said: str, whom: str) -> None:
    chat = make_chat()
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    await chat.press(texts.BOOK_MORE)
    await chat.say(said)
    state = await session(chat)
    assert (state["step"], state["for_whom"]) == ("complaint", whom)
    assert texts.FOR_WHOM[whom] in chat.text


async def test_a_complaint_typed_instead_of_whom_goes_on(make_chat) -> None:
    chat = make_chat()
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    await chat.press(texts.BOOK_MORE)
    await chat.say("Теперь сына надо записать, ногу подвернул")
    state = await session(chat)
    assert state["wizard"] and state["for_whom"] == "child" and state["step"] == "topic"
