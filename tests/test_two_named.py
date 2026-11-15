"""Two patients named in one message: who is first, each with his own part, the next by name.

From the second simulation run (two_kids, turns 1–7): «Маша 7 лет руку ушибла, Ваня 10 лет ногу
подвернул» — the bot promised to book one at a time and kept the patient on step 1 for seven
turns, asking DeepSeek about both injuries at once. Now it asks «Кого записать первым?» with a
button for each name, leads step 1 with the part about the chosen one, does not ask the age it
has been told (it stands over the step: «Записываю Машу, 7 лет: руку ушибла»), and after the
card offers the other by name: «Записать Ваню». The brother is not offered the sister booked a
minute ago on «Кого записываем?»: the bot asks his name at once.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

import json

import pytest

from app.bot import texts
from app.flow.loader import KnowledgeBase
from tests import intent_cases as m
from tests.chat_harness import Chat
from tests.fake_deepseek import FakeDeepSeek
from tests.test_conversation import bookings
from tests.test_intents import session, to_step

KIDS = (
    "Здравствуйте, дети сегодня с горки упали, Маша 7 лет руку ушибла, Ваня 10 лет ногу "
    "подвернул. Надо обоих записать к врачу"
)
BOTH = "У меня запястье — похоже на перелом. У сына колено — ушиб."
ANSWERS = ("Бытовая травма", "В течение 7 дней", "Нет, не обращались")
# «дети сегодня с горки упали» is said about both: «Когда?» is read by the code, not asked
ANSWERS_TODAY = ("Бытовая травма", "Нет, не обращались")
MENU = [texts.MENU_BOOK, texts.MENU_ASK, texts.MENU_MY, texts.MENU_ADMIN]


async def through(chat: Chat, name: str, born: str, *, first: bool) -> None:
    """From the topics of «рука или нога» to the booking card of this patient; the message
    has said «сегодня»."""
    await chat.press("Похоже на вывих")
    for label in ANSWERS_TODAY:
        await chat.press(label)
    await chat.pick_slot("Завтра")
    if first:
        await chat.press(texts.CONSENT_YES)
    await chat.say(f"{name}, {born}")
    if first:
        await chat.contact()


async def test_two_named_children_are_asked_who_is_first(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say(KIDS)
    (screen,) = chat.replies  # one message, no question about «рука и нога» of both at once
    assert screen.text == f"{texts.ONE_BY_ONE} {texts.WHO_FIRST}"
    assert chat.labels() == ["Машу, 7 лет", "Ваню, 10 лет", texts.MENU]
    state = await session(chat)
    assert state["step"] == "first" and [p["name"] for p in state["people"]] == ["Маша", "Ваня"]


async def test_step_one_gets_only_the_part_about_the_chosen_child(
    make_chat, kb: KnowledgeBase
) -> None:
    unclear = {
        "type": "complaint",
        "area": "limb",
        "scenario": None,
        "facts": {},
        "candidates": ["dislocation", "fracture"],
        "clarify": "На что больше похоже?",
    }
    fake = FakeDeepSeek(unclear)
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say(KIDS)
    assert fake.requests == []  # DeepSeek is not asked about two injuries at once
    await chat.press("Машу, 7 лет")
    (request,) = fake.requests
    sent = json.loads(request["messages"][1]["content"])["text"]
    assert sent == "руку ушибла"  # not a word about Ваня and his leg
    assert "Записываю Машу, 7 лет: руку ушибла" in chat.text
    assert texts.PERSON_EDIT in chat.labels()
    state = await session(chat)
    assert state["for_whom"] == "child" and [p["name"] for p in state["people"]] == ["Ваня"]


async def test_the_age_told_is_not_asked_and_the_other_child_is_offered_by_name(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say(KIDS)
    await chat.press("Машу, 7 лет")
    assert "Записываю Машу, 7 лет: руку ушибла" in chat.text
    await chat.press("Похоже на вывих")
    assert "Как получена травма" in chat.text  # neither «для кого», nor «сколько лет»
    assert (await session(chat))["dialog"]["age_months"] == 84
    for label in ANSWERS_TODAY:  # «сегодня» was said about both: «Когда?» is not asked
        await chat.press(label)
    await chat.pick_slot("Завтра")
    await chat.press(texts.CONSENT_YES)
    await chat.say("Воронина Мария Кирилловна, 03.03.2019")
    await chat.contact()
    await chat.press(texts.PAYMENT_SELF)
    assert chat.text == texts.NEXT_PATIENT and chat.labels() == ["Записать Ваню", *MENU]

    await chat.press("Записать Ваню")  # his part and his age are in place: no «для кого»
    assert "Записываю Ваню, 10 лет: ногу подвернул" in chat.text
    assert texts.ASK_AUDIENCE not in chat.text
    await chat.press("Похоже на вывих")
    assert "Как получена травма" in chat.text
    assert (await session(chat))["dialog"]["age_months"] == 120
    for label in ANSWERS_TODAY:
        await chat.press(label)
    await chat.pick_slot("Завтра")
    # «Кого записываем?» with Мария on it is not shown: «Записываю Ваню» and the name at once
    assert texts.PATIENT_PICK not in chat.text and texts.ASK_NAME in chat.text
    await chat.say("Воронин Иван Кириллович, 05.05.2016")
    assert "Иван Кириллович записан" in chat.text
    assert len(await bookings(chat.conversation.db)) == 2
    await chat.press(texts.PAYMENT_SELF)  # nobody else waits: the usual «Записать ещё одного»
    assert chat.labels() == [texts.BOOK_MORE, *MENU]


@pytest.mark.parametrize(
    ("said", "name"),
    [("сначала Ваню", "Ваня"), ("Машу", "Маша"), ("давайте с Вани начнём", "Ваня"),
     ("второго", "Ваня"), ("первую", "Маша")],
)  # fmt: skip
async def test_who_is_first_in_words(make_chat, said: str, name: str) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say(KIDS)
    await chat.say(said)
    state = await session(chat)
    assert state["person"]["name"] == name and state["step"] != "first"


async def test_words_that_name_nobody_keep_the_question(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say(KIDS)
    await chat.say("да всё равно")
    assert (await session(chat))["step"] == "first" and "Машу, 7 лет" in chat.labels()


async def test_me_and_my_son_each_with_his_own_injury(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say(BOTH)
    assert chat.labels() == ["Меня", "Сына", texts.MENU]
    await chat.press("Сына")
    assert "Записываю сына: колено, ушиб" in chat.text
    await chat.press("Похоже на вывих")
    assert chat.text.endswith(texts.ASK_CHILD_AGE)  # the age was not told: asked
    await chat.say("12 лет")
    for label in ANSWERS:
        await chat.press(label)
    await chat.pick_slot("Завтра")
    await chat.press(texts.CONSENT_YES)
    await chat.say("Агапов Егор Михайлович, 22.08.2014")
    await chat.contact()
    await chat.press(texts.PAYMENT_SELF)
    assert chat.labels() == ["Записать меня", *MENU]
    await chat.press("Записать меня")
    assert "Записываю Вас: запястье, похоже на перелом" in chat.text
    assert (await session(chat))["for_whom"] == "self"


async def test_the_one_named_first_is_taken_and_the_other_waits(make_chat) -> None:
    """«Сначала для себя, потом сына»: the order is said — no question about it."""
    chat = make_chat()
    await to_step(chat, m.AUDIENCE)
    await chat.say("Сначала для себя, потом сына надо будет записать")
    line, screen = chat.replies
    assert line.text == texts.ONE_BY_ONE and "Как получена травма" in screen.text
    state = await session(chat)
    assert state["dialog"]["facts"]["audience"] == "adult"
    assert [p["name"] for p in state["people"]] == ["сын"]
    for label in ANSWERS:
        await chat.press(label)
    await chat.pick_slot("Завтра")
    await chat.press(texts.CONSENT_YES)
    await chat.say("Агапов Михаил Степанович, 04.07.1980")
    await chat.contact()
    await chat.press(texts.PAYMENT_SELF)
    assert chat.text == texts.NEXT_PATIENT and chat.labels() == ["Записать сына", *MENU]
    await chat.press("Записать сына")  # «для кого» is not asked: he was named
    assert chat.text.startswith("Шаг 1 из 5") and "Записываю сына" in chat.text
    assert (await session(chat))["for_whom"] == "child"


async def test_change_opens_the_usual_question(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say(KIDS)
    await chat.press("Ваню, 10 лет")
    await chat.press(texts.PERSON_EDIT)
    assert texts.ASK_AUDIENCE in chat.text
    assert chat.labels()[:2] == [texts.WHOM_ADULT, texts.AUDIENCE_CHILD]
    assert (await session(chat))["person"] is None


@pytest.mark.parametrize(
    "said", ["запишите к Андрею Александровичу на завтра", "запишите сына", "меня укусила собака"]
)
async def test_one_patient_or_a_doctor_is_not_two(make_chat, said: str) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say(said)
    assert (await session(chat))["step"] != "first" and texts.WHO_FIRST not in chat.text


# --- more than two: a queue by the names --------------------------------------------------------

THREE = (
    "Дети сегодня с горки упали. Маша, 7 лет, руку ушибла, Ваня, 10 лет, ногу подвернул, "
    "и Петя, 5 лет, ногу ушиб."
)


async def test_three_named_children_are_booked_one_after_another(make_chat) -> None:
    """After each card the button names the next one: «Записать Машу», then «Записать Петю» —
    not «Записать ещё одного» with «для кого» and the age asked again."""
    chat = make_chat()
    await chat.start()
    await chat.say(THREE)
    assert chat.text == f"{texts.ONE_BY_ONE_MANY} {texts.WHO_FIRST}"
    assert chat.labels() == ["Машу, 7 лет", "Ваню, 10 лет", "Петю, 5 лет", texts.MENU]

    await chat.press("Ваню, 10 лет")
    assert "Записываю Ваню, 10 лет: ногу подвернул" in chat.text
    await through(chat, "Воронин Иван Кириллович", "05.05.2016", first=True)
    await chat.press(texts.PAYMENT_SELF)
    assert chat.text == texts.NEXT_PATIENT and chat.labels() == ["Записать Машу", *MENU]
    assert [p["name"] for p in (await session(chat))["people"]] == ["Маша", "Петя"]

    await chat.press("Записать Машу")
    assert "Записываю Машу, 7 лет: руку ушибла" in chat.text
    assert texts.ASK_AUDIENCE not in chat.text
    await through(chat, "Воронина Мария Кирилловна", "03.03.2019", first=False)
    await chat.press(texts.PAYMENT_SELF)
    # the third, not «второго»
    assert chat.text == texts.NEXT_PATIENT_MORE and chat.labels() == ["Записать Петю", *MENU]

    await chat.press("Записать Петю")
    assert "Записываю Петю, 5 лет: ногу ушиб" in chat.text
    assert (await session(chat))["dialog"]["age_months"] is None  # the topic is not chosen yet
    await through(chat, "Воронин Пётр Кириллович", "01.02.2021", first=False)
    assert len(await bookings(chat.conversation.db)) == 3
    await chat.press(texts.PAYMENT_SELF)  # nobody waits: the usual «Записать ещё одного»
    assert chat.labels() == [texts.BOOK_MORE, *MENU]
    assert chat.text.startswith(texts.THANKS)


async def test_two_named_keep_the_words_about_the_second(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say(KIDS)
    assert chat.text == f"{texts.ONE_BY_ONE} {texts.WHO_FIRST}"  # «потом второго»: two of them


# --- «оба» when everybody named is known ---------------------------------------------------------

COUPLE = "Мы с мужем в походе подвернули ноги, оба. Запишите нас."
BOTH_AGAIN = "Тоже предполагаю, врач его не смотрел. Мы же вместе в походе были, оба и подвернули."


async def test_both_said_about_the_named_does_not_start_a_queue(make_chat) -> None:
    """The final live run of couple: in the booking of the husband, the last of the two, «оба
    и подвернули» was answered with «Запишу по очереди…», and after his card the bot asked
    «Теперь запишем следующего пациента?». Those named are known: «оба» is about them."""
    chat = make_chat()
    await chat.start()
    await chat.say(COUPLE)
    await chat.press("Меня")
    await chat.press("Похоже на вывих")
    await chat.say(BOTH_AGAIN)  # on a question; the husband waits: said already, not once more
    assert texts.ONE_BY_ONE not in chat.text and not (await session(chat))["more"]
    for label in ANSWERS:
        await chat.press(label)
    await chat.pick_slot("Завтра")
    await chat.press(texts.CONSENT_YES)
    await chat.say("Захарова Любовь Тимофеевна, 18.11.1979")
    await chat.contact()
    await chat.press(texts.PAYMENT_SELF)
    assert chat.text == texts.NEXT_PATIENT and chat.labels()[0] == "Записать мужа"

    await chat.press("Записать мужа")
    await chat.press("Похоже на вывих")
    await chat.say(BOTH_AGAIN)  # the last of those named is being booked
    assert texts.ONE_BY_ONE not in chat.text and not (await session(chat))["more"]
    for label in ANSWERS:
        await chat.press(label)
    await chat.pick_slot("Завтра")
    await chat.say("Захаров Игорь Семёнович, 05.05.1977")
    await chat.press(texts.PAYMENT_SELF)
    # nobody waits: the usual end, not the next of a queue
    assert chat.text == f"{texts.THANKS} Могу я ещё чем-нибудь Вам помочь?"
    assert chat.labels() == [texts.BOOK_MORE, *MENU]


async def test_both_with_nobody_named_still_starts_one(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.say("нас двое, обоих надо записать, упали вчера")
    assert texts.ONE_BY_ONE in chat.text and (await session(chat))["more"]
