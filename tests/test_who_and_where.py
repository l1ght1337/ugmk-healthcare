"""Whom the bot books, and where to: a refusal, a birth date in the first message, a tooth.

Found on a phone and in full run 6 (15.11).

«Пока не записывай мужа, ещё посмотрим» showed «Ваша запись» with «Перенести» and «Отменить»,
and the husband stayed in the conversation as the one a booking is for. A refusal to book a
person is one line and the menu; he is not booked and not remembered.

«Запишите на завтра утром, сегодня порезал палец на кухне, к врачу не ходил. Петров Иван
Сергеевич, 12.03.1985» was asked «для кого запись?». A birth date in the message says whether
the patient is an adult or a child; the name and the date are kept for step 5.

A knocked-out tooth: four questions of DeepSeek in a row over the same buttons, then the
route of a head injury. The scripts say nothing about teeth: the bot says so. A question of
DeepSeek is said once for a complaint; after it — «Другое».

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.bot import texts
from app.db.models import Patient
from app.flow.loader import KnowledgeBase
from app.flow.people import refused
from tests.fake_deepseek import FakeDeepSeek
from tests.test_conversation import book_dislocation, bookings
from tests.test_intents import session
from tests.test_two_named import ANSWERS, COUPLE, MENU

NOT_HUSBAND = "Хорошо, мужа не записываю. Понадобится — напишите."


@pytest.mark.parametrize(
    ("said", "whom"),
    [
        ("пока не записывай мужа еще посмотрим", ["муж"]),  # the phone, word for word
        ("Пока не записывай мужа, ещё посмотрим", ["муж"]),
        ("мужа не надо", ["муж"]),
        ("мужа потом", ["муж"]),
        ("сына пока не надо записывать", ["сын"]),
        ("Ваню не записывайте", ["Ваня"]),
        # an order of two, a request for another, a complaint: not a refusal
        ("сначала меня, потом мужа", []),
        ("мужа не надо, запишите сына", []),
        ("у мужа не сгибается нога", []),
        ("не надо", []),
    ],
)
def test_who_is_not_to_be_booked(said: str, whom: list[str]) -> None:
    assert [p.name for p in refused(said)] == whom


async def test_not_the_husband_is_one_line_and_the_menu(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek({"type": "menu_action", "action": "my_bookings"})
    chat = make_chat(assistant=fake.assistant(kb))
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    await chat.say("пока не записывай мужа еще посмотрим")
    (reply,) = chat.replies
    assert reply.text == NOT_HUSBAND and chat.labels() == MENU
    assert fake.requests == [] and texts.MY_ONE not in chat.text
    assert (await session(chat))["talk"]["who"] is None  # not the one a booking is for


async def test_the_one_who_waits_is_taken_out_of_the_queue(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say(COUPLE)
    await chat.press("Меня")
    await chat.press("Похоже на вывих")
    for label in ANSWERS:
        await chat.press(label)
    await chat.pick_slot("Завтра")
    await chat.press(texts.CONSENT_YES)
    await chat.say("Захарова Любовь Тимофеевна, 18.11.1979")
    await chat.contact()
    await chat.press(texts.PAYMENT_SELF)
    assert chat.labels()[0] == "Записать мужа"
    await chat.say("мужа пока не надо")
    assert chat.text == NOT_HUSBAND and chat.labels() == MENU  # no «Записать мужа»
    assert (await session(chat))["people"] == []


SAID = (
    "Запишите на завтра утром, сегодня порезал палец на кухне, к врачу не ходил. Петров Иван "
    "Сергеевич, 12.03.1985"
)
CUT = {"type": "complaint", "area": "limb", "scenario": "cut_sutures",
       "candidates": ["cut_sutures"],
       "facts": {"wound_location": "body", "motor_function": True}}  # fmt: skip


async def test_the_birth_date_of_the_first_message_says_who_it_is(
    make_chat, kb: KnowledgeBase
) -> None:
    fake = FakeDeepSeek(CUT)
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say(SAID)
    seen = [chat.text]
    assert texts.ASK_AUDIENCE not in chat.text  # an adult, by the date: not asked
    state = await session(chat)
    assert state["dialog"]["facts"]["audience"] == "adult"
    assert state["dialog"]["facts"]["injury_age"] == "within_24h"  # «сегодня», by the code
    assert state["wish"]["period"] == "morning"  # «на завтра утром» is the wish
    assert "Петров" not in fake.sent() and "12.03.1985" not in fake.sent()  # not to DeepSeek
    while (await session(chat))["step"] == "question":  # what the message did not say
        await chat.press(chat.labels()[0])
        seen.append(chat.text)
    await chat.pick_time()
    await chat.press(texts.CONSENT_YES)
    seen.append(chat.text)
    assert "Пациент: Петров Иван Сергеевич, 12.03.1985" in chat.text  # taken, not asked
    await chat.contact()
    seen.append(chat.text)
    assert "Иван Сергеевич записан" in chat.text
    assert not any(texts.ASK_NAME in text or texts.ASK_AUDIENCE in text for text in seen)
    async with chat.conversation.db() as db:
        patient = (await db.scalars(select(Patient))).one()
    assert (patient.full_name, patient.birth_date) == ("Петров Иван Сергеевич", date(1985, 3, 12))
    assert len(await bookings(chat.conversation.db)) == 1


async def test_a_child_by_the_birth_date(make_chat) -> None:
    chat = make_chat()  # no DeepSeek: the word lists
    await chat.start()
    await chat.say("Дочка сегодня упала, болит рука. Петрова Мария Ивановна, 03.03.2019")
    assert texts.ASK_AUDIENCE not in chat.text and "Сколько лет ребёнку" not in chat.text
    await chat.press("Похоже на вывих")
    state = await session(chat)
    assert (state["dialog"]["facts"]["audience"], state["dialog"]["age_months"]) == ("child", 92)
    assert texts.ASK_AUDIENCE not in chat.text and "Сколько лет ребёнку" not in chat.text


TOOTH = "Здравствуйте, выбил зуб при падении. Принимаете с таким?"
HEAD = {"type": "complaint", "area": "head", "scenario": "head_trauma",
        "candidates": ["head_trauma"], "facts": {}}  # fmt: skip
ASKS_BACK = {"type": "complaint", "area": "limb", "scenario": None, "candidates": [], "facts": {},
             "clarify": "Подскажите, пожалуйста, что именно случилось?"}  # fmt: skip


@pytest.mark.parametrize(
    "said", [TOOTH, "Упал, ударился лицом, выбил передний зуб. Что делать?", "зубы выбили"]
)
async def test_the_scripts_say_nothing_about_teeth(make_chat, kb: KnowledgeBase, said: str) -> None:
    fake = FakeDeepSeek(HEAD)
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say(said)
    assert chat.text.startswith("Куда с этим обращаться, в моих данных нет.")
    assert "сотрясение" not in chat.text and fake.requests == []
    assert chat.labels()[0] == texts.ASK_ADMIN_QUESTION  # it may go to the administrators


async def test_a_question_of_deepseek_is_said_once_for_a_complaint(
    make_chat, kb: KnowledgeBase
) -> None:
    fake = FakeDeepSeek(*[ASKS_BACK] * 4)
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press("Рука или нога")
    await chat.say("что-то с рукой, не пойму")
    assert ASKS_BACK["clarify"] in chat.text
    for more in ("ну рука, говорю же", "да рука же", "рука"):
        await chat.say(more)
        assert ASKS_BACK["clarify"] not in chat.text  # not asked again and again
        assert texts.NONE_FITS in chat.text and "Другое" in chat.labels()
