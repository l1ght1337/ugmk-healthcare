"""After a route nothing is said about our trauma centre (the customer, 15.11.2026).

Run 7 and the partial runs before it, found by reading: after a route the reference was
answering about OUR trauma centre — its address («назови конкретный травмпункт» → ул.
Шейнкмана, to a patient sent to a municipal one), its hours («ночью приём тоже возможен», to
the mother of a baby under 6 months), «Да, с раной на стопе в травмпункт записывают…
Записаться можно в этом чате» (after the route to the AIDS centre), «Ожоги мы принимаем» (a
burn of the face). Nobody was booked, but the patient was told to come or to book here.

While the talk is about the injury a route was given for:

- «вы меня запишете?», «к вам можно?» — «Нет, с этим в травмпункт не записываем» and the route;
- a question about the place of the route, however it is named — by its number, its name, its
  street, «туда», «это далеко», «что брать» — the route and «Как там принимают, у меня данных
  нет…», never «У меня нет этой информации»;
- anything else — the route and «На этот вопрос ответит администратор»;
- a route with no address («Любой муниципальный травмпункт»): «куда конкретно», «назови
  адрес» — «Адресов муниципальных травмпунктов в моих данных нет», and not our address;
- «Записаться в травмпункт» begins with «Это та же травма или другая?».

It ends with «Другая», with another person, with a new conversation.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

import pytest

from app.bot import texts
from app.flow.loader import KnowledgeBase
from tests.fake_deepseek import FakeDeepSeek
from tests.test_after_route import AIDS, ODKB, infant_route, needle_route
from tests.test_conversation import bookings
from tests.test_sim_findings_2 import session
from tests.test_urgency import routed_burn

DGKB9 = "📍 ДГКБ № 9, приёмный покой, ул. Решетская, 51"
MUNICIPAL = "📍 Любой муниципальный травмпункт"
NO = "Нет, с этим в травмпункт не записываем."
THERE = "Как там принимают, у меня данных нет — уточните в самом учреждении."
ADMIN = "На этот вопрос ответит администратор."
NO_ADDRESS = "Адресов муниципальных травмпунктов в моих данных нет."
CLINIC = {"type": "clinic_question"}
OURS = {"found": True, "answer": "Травмпункт работает круглосуточно по предварительной записи."}


def said(chat) -> str:
    return "\n".join(reply.text for reply in chat.replies)


def about_us(text: str) -> bool:
    """A reply invites to our trauma centre: its address, its hours, «записаться можно»."""
    marks = ("Шейнкмана", "круглосуточно", "записаться можно", "мы принимаем", "записывают")
    return any(mark in text.lower() for mark in (m.lower() for m in marks))


async def criminal_route(chat) -> None:
    """A dislocation got in a fight, by buttons: «Любой муниципальный травмпункт»."""
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    for label in ("Рука или нога", "Похоже на вывих", texts.AUDIENCE_SELF,
                  "Нападение, драка, сбила машина"):  # fmt: skip
        await chat.press(label)
    assert MUNICIPAL in chat.text and (await session(chat))["step"] == "idle"


# --- «вы меня запишете?» — «Нет» -----------------------------------------------------------

TAKEN = [
    "вы меня запишете?",
    "а к вам можно?",
    "Так вы нас не запишете, да?",
    "Подождите, а в травмпункт меня всё-таки не запишут?",
    "к вам на приём с этим вообще не попасть? Да или нет?",
    "Так запишут меня в травмпункт или нет?",
]


@pytest.mark.parametrize("asked", TAKEN)
async def test_will_you_take_me_after_a_route_begins_with_no(
    make_chat, kb: KnowledgeBase, asked: str
) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await needle_route(chat)
    await chat.say(asked)
    assert chat.replies[0].text.startswith(f"{NO}\nОбратиться нужно:\n{AIDS}"), chat.text
    assert fake.requests == [] and not about_us(said(chat))


@pytest.mark.parametrize("asked", TAKEN[:3])
async def test_no_after_the_route_of_a_booking_scenario_too(
    make_chat, kb: KnowledgeBase, asked: str
) -> None:
    chat = await routed_burn(make_chat, kb)
    await chat.say(asked)
    assert chat.replies[0].text.startswith(f"{NO}\nОбратиться нужно:\n{DGKB9}"), chat.text
    assert not about_us(said(chat))


# --- the place of the route, however it is named ------------------------------------------------

ABOUT_THERE = [
    "А в ДГКБ № 9 нас без записи примут, круглосуточно?",  # its name
    "на Решетской ночью нас примут или отправят домой?",  # its street
    "Только скажите хоть, сколько ехать до Решетской? Мы на Победе",
    "А это далеко? Мы в центре",
    "А это далеко от нас? И что с собой брать, документы какие?",
    "а туда прямо сейчас можно ехать? ночью примут?",
    "в девятой нас примут?",  # its number
]


@pytest.mark.parametrize("asked", ABOUT_THERE)
async def test_a_question_about_the_place_of_the_route_is_about_it(
    make_chat, kb: KnowledgeBase, asked: str
) -> None:
    chat = await routed_burn(make_chat, kb, CLINIC, OURS)
    await chat.say(asked)
    assert chat.replies[0].text == f"{THERE}\n{DGKB9}", chat.text
    assert texts.INFO_UNKNOWN not in said(chat) and not about_us(said(chat))


# --- anything else: the route and the administrator ------------------------------------------

ELSE = [
    "а сейчас ночь же, вы говорите с 8 до 20… то есть до утра ждать?",
    "а вы сами круглосуточно работаете?",
    "а сколько у вас стоит приём?",
    "какой у вас адрес?",
]


@pytest.mark.parametrize("asked", ELSE)
@pytest.mark.parametrize("kind", ["clinic_question", "schedule_question", "other_medical"])
async def test_the_reference_about_our_centre_is_not_said_after_a_route(
    make_chat, kb: KnowledgeBase, asked: str, kind: str
) -> None:
    fake = FakeDeepSeek({"type": kind}, OURS)
    chat = make_chat(assistant=fake.assistant(kb))
    await infant_route(chat)
    await chat.say(asked)
    assert chat.replies[0].text == f"Обратиться нужно:\n{ODKB}\n\n{ADMIN}", chat.text
    assert not about_us(said(chat)) and texts.NOT_OUR_PROFILE not in said(chat)
    assert len(fake.requests) == 1  # what the message is; the reference is not asked
    assert chat.labels()[0] == texts.ASK_ADMIN_QUESTION  # the question may go to him
    assert (await session(chat))["handoff_context"] == asked


async def test_the_same_after_the_route_of_a_booking_scenario(make_chat, kb: KnowledgeBase) -> None:
    chat = await routed_burn(make_chat, kb, CLINIC, OURS)
    await chat.say("Сейчас вечер уже, а если они только до восьми? Мне тогда что, утром звонить?")
    assert chat.replies[0].text == f"Обратиться нужно:\n{DGKB9}\n\n{ADMIN}", chat.text
    state = await session(chat)
    assert not about_us(said(chat)) and not state["wizard"] and state["wish"] is None


async def test_a_question_typed_on_same_or_other_is_not_answered_about_us(
    make_chat, kb: KnowledgeBase
) -> None:
    """needle, run 7, turn 5: «Да, с раной на стопе в травмпункт записывают…» on the screen
    «Это та же травма или другая?»."""
    fake = FakeDeepSeek(CLINIC, {"found": True, "answer": "Да, с раной на стопе записывают."})
    chat = make_chat(assistant=fake.assistant(kb))
    await needle_route(chat)
    await chat.press(texts.MENU_BOOK)
    assert (await session(chat))["step"] == "same"
    await chat.say("Та же, конечно. Но вы так и не ответили — в травмпункт меня запишут или нет?")
    assert chat.replies[0].text.startswith(f"{NO}\nОбратиться нужно:\n{AIDS}"), chat.text
    assert not about_us(said(chat)) and fake.requests == []
    await chat.press(texts.MENU_HOME)
    await chat.press(texts.MENU_BOOK)
    await chat.say("а вы круглосуточно работаете?")  # neither «та же» nor «другая»
    assert not about_us(said(chat)) and texts.SAME_OR_OTHER in chat.text


# --- a route with no address ------------------------------------------------------------------

WHERE_EXACTLY = [
    "В смысле не принимает? Мне справку для полиции надо. Куда конкретно ехать?",
    "Скажите хоть один травмпункт, куда ехать, адрес.",
    "Ты издеваешься? Я третий раз спрашиваю адрес. Назови конкретный травмпункт или скажи, "
    "что не знаешь.",
    "а какой именно травмпункт?",
]


@pytest.mark.parametrize("asked", WHERE_EXACTLY)
async def test_a_route_with_no_address_says_it_has_none(
    make_chat, kb: KnowledgeBase, asked: str
) -> None:
    fake = FakeDeepSeek(CLINIC, {"found": True, "answer": "Травмпункт: ул. Шейнкмана, 113/2."})
    chat = make_chat(assistant=fake.assistant(kb))
    await criminal_route(chat)
    await chat.say(asked)
    assert chat.replies[0].text == f"{NO_ADDRESS}\n\nОбратиться нужно:\n{MUNICIPAL}", chat.text
    assert not about_us(said(chat)) and fake.requests == []
    assert THERE not in said(chat)  # «уточните в самом учреждении» names no place here


# --- «Записаться в травмпункт» after a route ---------------------------------------------------


async def test_a_booking_after_a_route_begins_with_same_or_other(make_chat) -> None:
    """criminal, run 7, turn 9: step 1 at once, the same complaint, the same route — a circle."""
    chat = make_chat()
    await criminal_route(chat)
    await chat.press(texts.MENU_BOOK)
    assert chat.replies[-1].text.endswith(texts.SAME_OR_OTHER)
    assert texts.ASK_COMPLAINT not in chat.text and (await session(chat))["step"] == "same"
    await chat.press(texts.SAME_INJURY)
    assert MUNICIPAL in chat.text and "не записываем" in chat.text
    await chat.say("запишите меня на сегодня")  # typed: the same question first
    assert chat.replies[-1].text.endswith(texts.SAME_OR_OTHER)
    await chat.press(texts.OTHER_INJURY)
    assert texts.ASK_COMPLAINT in chat.text  # another injury: step 1, as usual
    state = await session(chat)
    assert state["wizard"] and state["wish"]["day"] == "2026-11-12"  # «на сегодня» is kept
    await chat.press(texts.MENU)
    await chat.press(texts.MENU_BOOK)
    assert texts.ASK_COMPLAINT in chat.text  # «Другая» was said: not asked again
    assert await bookings(chat.conversation.db) == []


# --- where it ends -----------------------------------------------------------------------------


async def test_after_another_injury_the_reference_answers_again(
    make_chat, kb: KnowledgeBase
) -> None:
    fake = FakeDeepSeek(CLINIC, {"found": True, "answer": "Круглосуточно."})
    chat = make_chat(assistant=fake.assistant(kb))
    await needle_route(chat)
    await chat.press(texts.MENU_BOOK)
    await chat.press(texts.OTHER_INJURY)
    await chat.press(texts.MENU)
    await chat.say("а вы сами круглосуточно работаете?")
    assert chat.replies[0].text == "Круглосуточно."


async def test_a_question_about_another_person_is_not_about_the_route(
    make_chat, kb: KnowledgeBase
) -> None:
    fake = FakeDeepSeek(CLINIC, {"found": True, "answer": "Круглосуточно."})
    chat = make_chat(assistant=fake.assistant(kb))
    await infant_route(chat)
    await chat.say("а мужа вы примете? он руку ушиб, вы круглосуточно работаете?")
    assert ODKB not in said(chat) and NO not in said(chat)


async def test_a_new_conversation_answers_about_us_again(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek(CLINIC, {"found": True, "answer": "Круглосуточно."})
    chat = make_chat(assistant=fake.assistant(kb))
    await needle_route(chat)
    await chat.start()
    await chat.say("а вы сами круглосуточно работаете?")
    assert chat.replies[0].text == "Круглосуточно."
