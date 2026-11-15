"""A topic the reference says nothing about is answered by the code (the customer, 15.11.2026).

Run 9, certificate, 4 dialogs of 4: «куда обратиться за справкой в бассейн?» was answered
«Справку для бассейна… можно оформить на плановой консультации врача — записать на неё
поможет администратор». The reference says it of the sick leave only (kb/info/bolnichnyy.md);
of certificates it says nothing but those of beatings. In one dialog the same question got
the list of the doctors of the trauma centre with «Записаться в травмпункт» — right after
«принимаем травмы… сроком не более 7 дней».

The topics with no data are a list in config/clinic.yaml (no_data_topics): «справк…» with no
«больничн…» — «О справках в моих данных ничего нет. Передать вопрос администратору?», and
DeepSeek is not asked. After the refusal by the age of the injury the talk is as after a
route: nothing is said about our trauma centre for this injury.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

import pytest

from app.bot import texts
from app.config import ClinicData
from app.flow.loader import KnowledgeBase
from tests.fake_deepseek import FakeDeepSeek
from tests.test_after_route import infant_route
from tests.test_conversation import bookings
from tests.test_intents import session
from tests.test_old_injury import OLD, RULE, ligament

NO_DATA = "О справках в моих данных ничего нет."
ASKED = f"{NO_DATA} Передать вопрос администратору?"
POOL = "Куда мне обратиться за справкой в бассейн и освобождением от физкультуры?"
FIRST = (  # run 9, certificate, the first message word for word
    "Здравствуйте! Мне нужна справка в бассейн и освобождение от физкультуры. Года три назад "
    "подвернула ногу, было растяжение, сейчас при нагрузке побаливает. Можете помочь?"
)
OURS = ("В травмпункте принимают", "есть свободное время", "Записаться можно")
BOOK_BUTTONS = {texts.MENU_BOOK, "Записаться на это время", "Записаться на этот день"}


def said(chat) -> str:
    return "\n".join(reply.text for reply in chat.replies)


# --- «справк…»: the code answers, DeepSeek is not asked ---------------------------------------


@pytest.mark.parametrize(
    "asked",
    [
        "Справку в бассейн дадите?",
        POOL,
        "Можно у вас получить справку в бассейн и освобождение от физкультуры?",
        "а справки вы выдаёте?",
        "нужна справка в школу, выдаёте?",
        "дадите справку для налогового вычета?",
    ],
)
async def test_a_question_about_a_certificate_is_answered_by_the_code(
    make_chat, kb: KnowledgeBase, asked: str
) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb), admin_chat_id=-100)
    await chat.start()
    await chat.say(asked)
    assert said(chat) == ASKED
    assert chat.labels() == [texts.HANDOFF_YES, texts.HANDOFF_NO]
    assert fake.requests == []


async def test_the_question_goes_to_the_administrator_by_the_button(
    make_chat, kb: KnowledgeBase
) -> None:
    chat = make_chat(assistant=FakeDeepSeek().assistant(kb), admin_chat_id=-100)
    await chat.start()
    await chat.say(POOL)
    await chat.press(texts.HANDOFF_YES)
    await chat.press(texts.CONSENT_YES)
    await chat.contact()
    assert "обращение № 1" in chat.text and POOL in chat.admin[-1].text


async def test_asked_once_more_the_line_is_not_said_twice(make_chat, kb: KnowledgeBase) -> None:
    chat = make_chat(assistant=FakeDeepSeek().assistant(kb), admin_chat_id=-100)
    await chat.start()
    await chat.say(POOL)
    await chat.say("так справку дадите или нет?")
    assert chat.replies[0].text == texts.ADMIN_WILL_ANSWER
    assert chat.labels() == [texts.HANDOFF_YES, texts.HANDOFF_NO]


@pytest.mark.parametrize(
    "asked",
    [
        "а больничный и справку дадите?",  # the sick leave: the reference has it
        "справку о больничном листе дадите?",
        "справку о побоях дадите?",  # kb/info/uslugi.md, «Справки о побоях»
        "мне нужна справка для полиции, выдаёте?",
        "справка для суда нужна",
    ],
)
async def test_the_sick_leave_and_beatings_are_in_the_reference(
    make_chat, kb: KnowledgeBase, asked: str
) -> None:
    answer = {"found": True, "answer": "По справке клиники."}
    fake = FakeDeepSeek({"type": "clinic_question"}, answer)
    chat = make_chat(assistant=fake.assistant(kb), admin_chat_id=-100)
    await chat.start()
    await chat.say(asked)
    assert NO_DATA not in said(chat)


async def test_in_the_middle_of_a_booking_the_step_stays(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek({"type": "clinic_question"})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")
    await chat.say("а справку в бассейн потом дадите?")
    assert chat.replies[0].text == NO_DATA
    assert "Для кого запись" in chat.replies[-1].text
    assert len(fake.requests) == 1  # what the message is; the reference is not asked


async def test_with_a_complaint_the_line_is_said_and_the_complaint_goes_on(
    make_chat, kb: KnowledgeBase
) -> None:
    reading = {"type": "complaint", "area": "limb", "scenario": None, "candidates": [],
               "facts": {}, "question": True}  # fmt: skip
    fake = FakeDeepSeek(reading)
    chat = make_chat(assistant=fake.assistant(kb), admin_chat_id=-100)
    await chat.start()
    await chat.say(FIRST)
    assert chat.replies[0].text == NO_DATA, said(chat)
    assert "планов" not in said(chat) and "Шаг 1 из 5" in said(chat)
    assert len(fake.requests) == 1


def test_the_topic_is_a_list_in_the_config(clinic: ClinicData) -> None:
    (topic,) = clinic.clinic.no_data_topics
    assert topic.answer == NO_DATA
    assert {"справка", "справку", "справки", "справок", "справкой"} <= set(topic.words)
    assert {"больничный", "больничного", "побоях", "полиции"} <= set(topic.unless)


def test_the_prompt_forbids_carrying_one_document_over_to_another(kb: KnowledgeBase) -> None:
    system = FakeDeepSeek().assistant(kb).answer_system
    assert "не переноси" in system.lower() and "больничн" in system.lower()


# --- after the refusal by the age of the injury: as after a route ------------------------------


UNNAMED = {"type": "complaint", "area": "limb", "scenario": None, "candidates": [], "facts": {}}


async def refused(make_chat, kb: KnowledgeBase, *replies):
    """«…года три назад» → the scenario of ligaments → the rule, not a booking."""
    # what DeepSeek makes of the first message: the complaint, then no answer to «Как
    # получена травма?» in it
    fake = FakeDeepSeek(UNNAMED, {}, *replies)
    chat = make_chat(assistant=fake.assistant(kb), admin_chat_id=-100)
    await ligament(chat, OLD)
    assert RULE in said(chat)
    fake.requests.clear()
    return chat, fake


async def test_a_certificate_asked_after_the_refusal(make_chat, kb: KnowledgeBase) -> None:
    chat, fake = await refused(make_chat, kb)
    await chat.say(f"То есть со старой травмой вы не поможете? {POOL}")
    assert said(chat) == ASKED and fake.requests == []


@pytest.mark.parametrize(
    "asked",
    [
        "какие у вас врачи?",
        "к какому врачу или в какое отделение мне тогда обратиться?",  # run 9, partial В
        "кто принимает сегодня?",
        "есть время на завтра?",
    ],
)
async def test_after_the_refusal_our_doctors_and_times_are_not_offered(
    make_chat, kb: KnowledgeBase, asked: str
) -> None:
    chat, _ = await refused(make_chat, kb, {"type": "schedule_question"})
    await chat.say(asked)
    assert not any(ours in said(chat) for ours in OURS), said(chat)
    assert not BOOK_BUTTONS & set(chat.labels()), chat.labels()
    assert texts.ADMIN_WILL_ANSWER in said(chat)
    assert await bookings(chat.conversation.db) == []


async def test_after_the_refusal_the_rule_is_said_with_the_answer(
    make_chat, kb: KnowledgeBase
) -> None:
    chat, fake = await refused(make_chat, kb)
    await chat.say("какие у вас врачи?")
    assert said(chat) == f"{RULE}\n\n{texts.ADMIN_WILL_ANSWER}"
    assert chat.labels() == [texts.ASK_ADMIN_QUESTION, texts.MENU_HOME]
    assert fake.requests == []


async def test_after_the_refusal_the_reference_is_not_asked_about_us(
    make_chat, kb: KnowledgeBase
) -> None:
    chat, fake = await refused(make_chat, kb, {"type": "clinic_question"})
    await chat.say("а сколько стоит приём?")
    assert texts.ADMIN_WILL_ANSWER in said(chat) and "рублей" not in said(chat)
    assert len(fake.requests) == 1  # what the message is; the reference is not asked


async def test_somebody_else_after_the_refusal_is_answered_and_booked(
    make_chat, kb: KnowledgeBase
) -> None:
    chat, _ = await refused(make_chat, kb, UNNAMED, {}, {})
    await chat.say("у сына тоже нога болит, упал вчера")
    state = await session(chat)
    assert state["wizard"] and state["for_whom"] == "child", said(chat)


async def test_a_booking_begun_by_the_patient_is_answered_as_usual(
    make_chat, kb: KnowledgeBase
) -> None:
    chat, _ = await refused(make_chat, kb)
    await chat.press(texts.MENU_HOME)
    await chat.press(texts.MENU_BOOK)
    await chat.say("какие у вас врачи?")
    assert "В травмпункте принимают" in said(chat)  # his own booking: the questions are its


# --- the state after a route says nothing about us either --------------------------------------


@pytest.mark.parametrize("asked", ["какие у вас врачи?", "кто принимает сегодня?"])
async def test_after_a_route_the_schedule_read_by_the_code_is_not_ours(
    make_chat, kb: KnowledgeBase, asked: str
) -> None:
    chat = make_chat(assistant=FakeDeepSeek().assistant(kb), admin_chat_id=-100)
    await infant_route(chat)
    await chat.say(asked)
    assert not any(ours in said(chat) for ours in OURS), said(chat)
    assert "Областная детская клиническая больница № 1" in said(chat)
    assert texts.ADMIN_WILL_ANSWER in said(chat)
