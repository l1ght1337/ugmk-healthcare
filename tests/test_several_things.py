"""Several things asked for in one message: a booking, a move, a cancellation, a question.

The customer, 15.11: «бот путает, кого и куда записать». «Отмените запись мужа, а мою перенесите
на вечер» opened the move of the husband's booking with «вечером» — «кто» of one thing was
glued to «когда» of another. A message is cut into the things it asks for; each has its own
«кто» and its own «когда»; they are done one by one, the next is announced with a line.

The parts are cut by the code (app/flow/actions.py) with the words of app/flow/commands.py;
DeepSeek is not asked.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

from datetime import date, time

import pytest

from app.flow.actions import split
from app.flow.wishes import Parsed, Wish
from app.flow.wishes import parse as parse_wish

TODAY = date(2026, 11, 12)


def wish_of(part: str) -> Parsed:
    return parse_wish(part, (), TODAY, context="free", now=time(12, 0))


def cut(text: str) -> list[tuple]:
    """(what, who, the writer's own, the wish in short) for each thing — or [] if the message
    asks for one thing only."""
    plan = split(text, wish_of)
    if plan is None:
        return []
    found = []
    for a in plan.actions:
        wish = a.wish or Wish()
        when = "+".join(
            str(part)
            for part in ((wish.day - TODAY).days if wish.day else None, wish.period)
            if part is not None
        )
        found.append((a.kind, a.who.name if a.who else None, a.mine, when))
    return found


# --- the customer's examples -------------------------------------------------------------------


def test_move_mine_and_book_the_son() -> None:
    said = "Перенесите мою запись на завтра и запишите сына, он руку ушиб"
    assert cut(said) == [("move", None, True, "1"), ("book", "сын", False, "")]
    son = split(said, wish_of).actions[1].who
    assert (son.audience, son.complaint) == ("child", "руку ушиб")


def test_cancel_mine_and_book_the_husband_instead() -> None:
    said = "Отмените мою запись, запишите вместо меня мужа, он ногу подвернул"
    assert cut(said) == [("cancel", None, True, ""), ("book", "муж", False, "")]
    assert split(said, wish_of).actions[1].who.complaint == "ногу подвернул"


def test_cancel_the_husbands_and_move_mine() -> None:
    """«Кто» of one thing is not glued to «когда» of another: «на вечер» is the move's."""
    said = "Отмените запись мужа, а мою перенесите на вечер"
    assert cut(said) == [("cancel", "муж", False, ""), ("move", None, True, "evening")]
    move = split(said, wish_of).actions[1]
    assert [p.name for p in move.not_of] == ["муж"]  # «мою» is not the one named for him


def test_the_complaint_joins_the_booking_of_the_same_person_wherever_it_stands() -> None:
    said = "Сын руку ушиб, запишите его, а мою перенесите на завтра"
    assert cut(said) == [("book", "сын", False, ""), ("move", None, True, "1")]
    assert split(said, wish_of).actions[0].who.complaint == "руку ушиб"


def test_a_complaint_with_no_word_of_a_booking_is_a_booking() -> None:
    said = "Перенесите мою запись на завтра, а сын руку ушиб"
    assert cut(said) == [("move", None, True, "1"), ("book", "сын", False, "")]


def test_a_part_with_a_negation_is_not_a_thing_to_do() -> None:
    plan = split("мужа не записывайте, а мою перенесите на вечер", wish_of)
    assert [(a.kind, a.mine) for a in plan.actions] == [("move", True)]
    assert [p.name for p in plan.refused] == ["муж"]
    assert plan.actions[0].wish.period == "evening"


@pytest.mark.parametrize(
    ("said", "things"),
    [
        # «хочу перенести», «надо отменить», «нужно записать»: the same things
        (
            "хочу перенести свою запись на завтра, и ещё нужно записать дочку, она ногу подвернула",
            [("move", None, True, "1"), ("book", "дочка", False, "")],
        ),
        (
            "надо отменить запись мужа и перенести мою на утро",
            [("cancel", "муж", False, ""), ("move", None, True, "morning")],
        ),
        # the verb goes on: «…и запись мужа»
        (
            "отмените мою запись и запись мужа",
            [("cancel", None, True, ""), ("cancel", "муж", False, "")],
        ),
        # each «когда» stays in its own part
        (
            "Перенесите мою запись на вечер, а сына запишите на завтра утром, он палец ушиб",
            [("move", None, True, "evening"), ("book", "сын", False, "1+morning")],
        ),
        (
            "отмените запись сына на завтра, а меня запишите на вечер, я руку ушибла",
            [("cancel", "сын", False, "1"), ("book", "", True, "evening")],
        ),
        (
            "перезапишите меня на завтра и отмените запись дочки",
            [("move", None, True, "1"), ("cancel", "дочка", False, "")],
        ),
    ],
)
def test_the_things_of_a_message(said: str, things: list[tuple]) -> None:
    assert cut(said) == things


@pytest.mark.parametrize(
    "said",
    [
        # one thing: the message goes the way it went before
        "перенесите мою запись на завтра",
        "отмени и запиши на воскресенье",  # the old time for a new one: a move
        "хочу отменить запись и записаться заново на пятницу",
        "отмените запись сына и запишите его на завтра",  # the same visit, a new time
        "Отмените мою запись и запишите меня на пятницу",
        "отмените запись мужа",
        "перенесите на вечер, жена отвезёт",  # she drives
        "Перенесите мою запись на завтра, сын заболел",  # why, not one more thing
        "перенесите на пораньше, очень болит",  # the reason of the move, not a new booking
        "перенесите мою запись на вечер, у меня нога опухла",
        "отменять ничего не надо, просто перенесите на завтра",
        # bookings only: the queue of patients, as before
        "запишите меня и сына",
        "Маша 7 лет руку ушибла, Ваня 10 лет ногу подвернул, запишите обоих",
        "мужа не записывайте, запишите сына, он руку ушиб",
        # not requests
        "записывался к хирургу, но отменил",
        "приём у врача отменили, перенесли на завтра",
        "спасибо, запись переносить не буду",
        "Сын 8 лет упал с велосипеда сегодня, болит рука, можно сегодня вечером?",
    ],
)
def test_one_thing_is_not_cut(said: str) -> None:
    assert split(said, wish_of) is None


def test_a_booking_of_another_patient_after_a_cancellation_is_a_booking() -> None:
    said = "отмените запись сына и запишите дочку на завтра"
    assert cut(said) == [("cancel", "сын", False, ""), ("book", "дочка", False, "1")]


def test_a_question_is_a_thing_too() -> None:
    plan = split("Перенесите мою запись на завтра. И сколько стоит приём?", wish_of)
    assert [(a.kind, a.mine) for a in plan.actions] == [("move", True)]
    assert plan.question == "И сколько стоит приём?"
    # a time in a question is not a time for the move
    night = split("Перенесите мою запись на воскресенье. А ночью вы работаете?", wish_of)
    assert night.question == "А ночью вы работаете?" and night.actions[0].wish.period is None
    asked = split("Отмените запись мужа, а мою перенесите. Можно на вечер?", wish_of)
    assert asked.question is None and asked.actions[1].wish.period == "evening"
    # a question that is the request itself is not one more thing
    assert split("можно перенести мою запись на завтра?", wish_of) is None
    two = split("можно перенести мою запись на завтра и записать сына, он руку ушиб?", wish_of)
    assert [a.kind for a in two.actions] == ["move", "book"] and two.question is None


@pytest.mark.parametrize(
    "said",
    [
        "Перенесите мою запись, на завтра, и запишите сына, он руку ушиб",  # whose «завтра»?
        "На завтра: перенесите мою запись и запишите сына, он руку ушиб",
    ],
)
def test_a_when_between_two_things_is_nobodys(said: str) -> None:
    plan = split(said, wish_of)
    assert plan.unclear and [a.kind for a in plan.actions] == ["move", "book"]
    assert all(a.wish is None for a in plan.actions)  # not guessed


def test_a_when_after_the_last_thing_is_its_own() -> None:
    plan = split("Отмените запись мужа, а мою перенесите, если можно на вечер", wish_of)
    assert not plan.unclear and plan.actions[1].wish.period == "evening"


def test_four_things() -> None:
    said = (
        "Отмените запись мужа, мою перенесите на вечер, запишите сына, он руку ушиб, "
        "и дочку запишите, у неё нога болит"
    )
    plan = split(said, wish_of)
    assert [(a.kind, a.who.name if a.who else None) for a in plan.actions] == [
        ("cancel", "муж"), ("move", None), ("book", "сын"), ("book", "дочка"),
    ]  # fmt: skip


# --- the dialog: one thing at a time, the next is announced -------------------------------------

from app.bot import texts  # noqa: E402
from app.flow.loader import KnowledgeBase  # noqa: E402
from tests.conftest import TZ  # noqa: E402
from tests.fake_deepseek import FakeDeepSeek  # noqa: E402
from tests.test_conversation import bookings  # noqa: E402
from tests.test_intents import session  # noqa: E402

WIFE = ("Фролова Екатерина Дмитриевна", "08.12.1983")
HUSBAND = ("Фролов Дмитрий Олегович", "30.04.1981")
MOVE_MINE = "Перенесите мою запись на завтра и запишите сына, он руку ушиб"
SWAP = "Отмените мою запись, запишите вместо меня мужа, он ногу подвернул"
TWO_MOVES = "Отмените запись мужа, а мою перенесите на вечер"


async def book(chat, who: tuple[str, str], day: str, *, first: bool = True) -> None:
    """A visit by buttons for the writer (the first one) or for one more adult."""
    await chat.press(texts.MENU_BOOK)
    for label in ("Рука или нога", "Похоже на вывих", texts.AUDIENCE_SELF, "Бытовая травма",
                  "В течение 7 дней", "Нет, не обращались"):  # fmt: skip
        await chat.press(label)
    await chat.pick_slot(day)
    if first:
        await chat.press(texts.CONSENT_YES)
    else:
        await chat.press(texts.PATIENT_NEW)
    await chat.say(who[0])
    await chat.say(who[1])
    if first:
        await chat.contact()
    await chat.press(texts.PAYMENT_SELF)


async def couple(make_chat, *, husband: bool = True, day: str = "Завтра", assistant=None):
    """The wife writes; she is booked (tomorrow), and so is her husband (Sunday)."""
    chat = make_chat(assistant=assistant)
    await chat.start()
    await book(chat, WIFE, day)
    if husband:
        await book(chat, HUSBAND, "Вс", first=False)
    return chat, await bookings(chat.conversation.db)


def said(chat) -> list[str]:
    return [reply.text for reply in chat.replies]


async def test_move_mine_then_book_the_son(make_chat) -> None:
    chat, (mine,) = await couple(make_chat, husband=False, day="Вс")
    await chat.say(MOVE_MINE)
    first, screen = chat.replies
    assert first.text == "Сначала перенесу Вашу запись, потом запишу сына."
    assert screen.text.split("\n")[0].startswith("Перенос записи: Фролова Е. Д., вс 15.11, ")
    state = await session(chat)
    assert state["reschedule_of"] == mine.id and state["wish"]["day"] == "2026-11-13"
    assert state["wish"]["period"] is None  # nothing of the son's part is in the move
    await chat.pick_time()
    done, about, step = chat.replies
    assert done.text.startswith("Готово, запись Екатерины Дмитриевны перенесена с вс 15.11, ")
    assert "на пт 13.11, " in done.text and not done.buttons
    assert about.text == "Теперь запишу сына."
    assert "Записываю сына: руку ушиб" in step.text
    state = await session(chat)
    assert state["wizard"] and state["person"]["name"] == "сын" and state["wish"] is None
    moved, = await bookings(chat.conversation.db)  # fmt: skip
    assert moved.id == mine.id and moved.start_at.date().isoformat() == "2026-11-13"
    assert state["talk"]["todo"] == []  # the last thing is under way


async def test_cancel_mine_then_book_the_husband_instead(make_chat) -> None:
    chat, _ = await couple(make_chat, husband=False)
    await chat.say(SWAP)
    first, ask = chat.replies
    assert first.text == "Сначала отменю Вашу запись, потом запишу мужа."
    assert ask.text.split("\n")[0].startswith("Отмена записи: Фролова Е. Д., пт 13.11, ")
    assert "Отменить запись на 13 ноября (пятница)" in ask.text  # with a «да», as before
    assert [b.status for b in await bookings(chat.conversation.db)] == ["active"]
    await chat.press(texts.CANCEL_YES)
    done, about, step = chat.replies
    assert done.text == texts.CANCELLED and about.text == "Теперь запишу мужа."
    assert "Записываю мужа: ногу подвернул" in step.text
    assert [b.status for b in await bookings(chat.conversation.db)] == ["cancelled"]
    assert (await session(chat))["person"]["name"] == "муж"


async def test_cancel_the_husbands_then_move_mine(make_chat) -> None:
    """Before: the move of the husband's booking was opened, with «вечером» on it."""
    chat, (mine, _) = await couple(make_chat)
    await chat.say(TWO_MOVES)
    first, ask = chat.replies
    assert first.text == "Сначала отменю запись мужа, потом перенесу Вашу запись."
    assert ask.text.split("\n")[0].startswith("Отмена записи: Фролов Д. О., вс 15.11, ")
    state = await session(chat)
    assert state["reschedule_of"] is None and state["wish"] is None
    await chat.press(texts.CANCEL_YES)
    done, about, screen = chat.replies
    assert done.text == texts.CANCELLED and about.text == "Теперь перенесу Вашу запись."
    assert screen.text.split("\n")[0].startswith("Перенос записи: Фролова Е. Д., пт 13.11, ")
    state = await session(chat)
    assert state["reschedule_of"] == mine.id and state["wish"]["period"] == "evening"
    await chat.press(chat.nearest().label)
    assert chat.replies[0].text.startswith("Готово, запись Екатерины Дмитриевны перенесена")
    assert chat.labels()[-4:] == [texts.MENU_BOOK, texts.MENU_ASK, texts.MENU_MY, texts.MENU_ADMIN]
    hers, husbands = await bookings(chat.conversation.db)
    assert (hers.status, husbands.status) == ("active", "cancelled")
    # «на вечер» with no day is the nearest evening: tonight, as on any time screen
    assert hers.start_at != mine.start_at and hers.start_at.astimezone(TZ).hour >= 18
    talk = (await session(chat))["talk"]
    assert talk["todo"] == [] and talk["doing"] is None


async def test_the_one_not_to_be_booked_and_one_move(make_chat) -> None:
    chat, (mine,) = await couple(make_chat, husband=False)
    await chat.say("мужа не записывайте, а мою перенесите на вечер")
    line, screen = chat.replies
    assert line.text == "Хорошо, мужа не записываю. Понадобится — напишите."
    assert screen.text.split("\n")[0].startswith("Перенос записи: Фролова Е. Д., пт 13.11, ")
    state = await session(chat)
    assert state["reschedule_of"] == mine.id and state["wish"]["period"] == "evening"
    assert state["people"] == [] and state["talk"]["who"] is None  # he is not remembered


async def test_a_when_that_is_nobodys_is_asked_about(make_chat) -> None:
    chat, (mine,) = await couple(make_chat, husband=False, day="Вс")
    await chat.say("Перенесите мою запись, на завтра, и запишите сына, он руку ушиб")
    (ask,) = chat.replies
    assert ask.text == "Понял два дела: перенос Вашей записи и запись сына. С чего начать?"
    assert chat.labels() == ["Перенести Вашу запись", "Записать сына", texts.MENU_HOME]
    state = await session(chat)
    assert state["reschedule_of"] is None and not state["wizard"]  # nothing is done yet
    await chat.press("Записать сына")
    assert "Записываю сына: руку ушиб" in chat.text and "Сначала" not in chat.text
    assert (await session(chat))["wish"] is None  # «на завтра» is not guessed for him
    (still,) = await bookings(chat.conversation.db)
    assert still.start_at == mine.start_at


async def test_whose_booking_is_asked_when_nobody_is_named(make_chat) -> None:
    chat, _ = await couple(make_chat)
    await chat.say("Перенесите запись на завтра и запишите сына, он руку ушиб")
    (ask,) = chat.replies
    assert ask.text == "Понял два дела: перенос записи и запись сына. С чего начать?"
    await chat.press("Перенести запись")
    assert texts.MOVE_WHICH in chat.text  # two bookings: which one is not guessed
    assert (await session(chat))["reschedule_of"] is None


async def test_more_than_three_things_are_not_a_queue(make_chat) -> None:
    chat, _ = await couple(make_chat)
    await chat.say(
        "Отмените запись мужа, мою перенесите на вечер, запишите сына, он руку ушиб, "
        "и дочку запишите, у неё нога болит"
    )
    (ask,) = chat.replies
    assert ask.text == (
        "Понял несколько дел: отмена записи мужа, перенос Вашей записи, запись сына, "
        "запись дочки. С чего начать?"
    )
    assert chat.labels() == [
        "Отменить запись мужа", "Перенести Вашу запись", "Записать сына", "Записать дочку",
        texts.MENU_HOME,
    ]  # fmt: skip
    await chat.press("Отменить запись мужа")
    await chat.press(texts.CANCEL_YES)
    done, rest = chat.replies
    assert done.text == texts.CANCELLED
    assert rest.text == (
        "Осталось: перенос Вашей записи, запись сына, запись дочки. С чего продолжить?"
    )
    assert chat.labels()[:3] == ["Перенести Вашу запись", "Записать сына", "Записать дочку"]


async def test_the_menu_in_the_middle_says_what_is_left(make_chat) -> None:
    chat, (mine,) = await couple(make_chat, husband=False, day="Вс")
    await chat.say(MOVE_MINE)
    await chat.press(texts.MENU)
    assert chat.replies[0].text == "Не сделано: перенос Вашей записи, запись сына."
    state = await session(chat)
    assert state["talk"]["todo"] == [] and state["talk"]["doing"] is None
    assert [p["name"] for p in state["people"]] == ["сын"]  # he may be booked from the menu
    (still,) = await bookings(chat.conversation.db)
    assert still.start_at == mine.start_at


async def test_left_as_it_is_in_the_middle_says_what_is_left(make_chat) -> None:
    chat, _ = await couple(make_chat, husband=False, day="Вс")
    await chat.say(MOVE_MINE)
    await chat.press(texts.KEEP_AS_IS)
    kept, left = chat.replies
    assert kept.text == texts.KEPT and left.text == "Не сделано: запись сына."
    assert (await session(chat))["talk"]["todo"] == []


async def test_a_patient_of_the_queue_due_a_route_gets_it_and_the_queue_goes_on(
    make_chat, kb: KnowledgeBase
) -> None:
    head = {"type": "complaint", "area": "head", "scenario": "head_trauma",
            "candidates": ["head_trauma"], "facts": {}}  # fmt: skip
    chat, (mine,) = await couple(
        make_chat, husband=False, day="Вс", assistant=FakeDeepSeek(head).assistant(kb)
    )
    await chat.say("Запишите мужа, он головой ударился, а мою запись перенесите на завтра")
    assert chat.replies[0].text == "Сначала запишу мужа, потом перенесу Вашу запись."
    texts_said = "\n".join(said(chat))
    assert "Травму головы не принимаем" in texts_said  # the route of the scripts, for him
    assert "Теперь перенесу Вашу запись." in said(chat)
    assert chat.replies[-1].text.split("\n")[0].startswith("Перенос записи: Фролова Е. Д.")
    state = await session(chat)
    assert state["reschedule_of"] == mine.id and state["wish"]["day"] == "2026-11-13"


async def test_urgency_and_danger_are_read_before_the_things(make_chat) -> None:
    chat, _ = await couple(make_chat)
    await chat.say(TWO_MOVES + ". Скорую вызывать или самим ехать?")
    assert chat.replies[0].text.startswith("Оценить состояние я не могу.")
    assert (await session(chat))["talk"]["todo"] == []
    await chat.say(TWO_MOVES + ", у него кровь не останавливается")
    assert "103 или 112" in chat.text and (await session(chat))["talk"]["danger"] is not None
    assert (await session(chat))["talk"]["todo"] == []
    assert [b.status for b in await bookings(chat.conversation.db)] == ["active", "active"]


async def test_a_question_is_answered_and_the_move_begins(make_chat, kb: KnowledgeBase) -> None:
    answer = {"found": True, "answer": "Круглосуточно."}
    chat, (mine,) = await couple(
        make_chat, husband=False, assistant=FakeDeepSeek(answer).assistant(kb)
    )
    await chat.say("Перенесите мою запись на воскресенье. А ночью вы работаете?")
    assert chat.replies[0].text == "Круглосуточно."
    assert chat.replies[-1].text.split("\n")[0].startswith("Перенос записи: Фролова Е. Д.")
    assert (await session(chat))["reschedule_of"] == mine.id


async def test_one_thing_goes_the_way_it_went(make_chat) -> None:
    chat, _ = await couple(make_chat)
    await chat.say("отмените запись мужа")
    assert "Сначала" not in chat.text and "Отменить запись на 15 ноября" in chat.text
    assert (await session(chat))["talk"]["doing"] is None


async def test_the_words_of_section_5_are_read_over_the_whole_message(make_chat) -> None:
    """«…он на иглу от шприца наступил»: the booking of the queue asks the scenario's own
    question before it books — the same as with one thing in the message."""
    chat, _ = await couple(make_chat, husband=False, day="Вс")
    await chat.say("Перенесите мою запись на завтра и запишите сына, он на иглу от шприца наступил")
    assert chat.replies[0].text == "Сначала перенесу Вашу запись, потом запишу сына."
    assert (await session(chat))["talk"]["guards"] == ["needle_injury"]
    await chat.pick_time()
    assert "Теперь запишу сына." in said(chat)
    for label in chat.labels():
        if label in ("Порез, рана", "Рана, ожог, укус"):
            await chat.press(label)
            break
    if "Порез, рана" in chat.labels():
        await chat.press("Порез, рана")
    assert "это укол чужой иглой?" in chat.text


# --- «Это возможно?» right after a request is not one more question (run 7, move_time) ----------

MOVE_TODAY = (
    "Здравствуйте! Я записана на завтра на одиннадцать утра, хотела бы перенести на сегодня, "
    "но после шести вечера. Это возможно?"
)


@pytest.mark.parametrize(
    "said",
    [
        MOVE_TODAY,
        "Перенесите мою запись на завтра. Можно?",
        "Перенесите мою запись на завтра. Так получится?",
        "Отмените запись мужа, а мою перенесите на вечер. Это возможно?",
    ],
)
def test_is_it_possible_after_a_request_is_not_a_question(clinic, said: str) -> None:
    plan = split(said, wish_of, clinic.clinic.plain_asks)
    assert plan is None or plan.question is None


def test_a_real_question_after_a_request_still_is(clinic) -> None:
    plan = split(
        "Перенесите мою запись на завтра. А парковка у вас есть?", wish_of, clinic.clinic.plain_asks
    )
    assert plan.question == "А парковка у вас есть?"


async def test_the_move_begins_with_no_line_about_a_question(make_chat) -> None:
    chat, (mine,) = await couple(make_chat, husband=False)
    await chat.say(MOVE_TODAY)
    assert chat.replies[0].text.split("\n")[0].startswith("Перенос записи: Фролова Е. Д.")
    assert texts.ASIDE_UNKNOWN not in chat.text
    assert (await session(chat))["reschedule_of"] == mine.id


# --- «Как это сделать?» after a request to cancel one of two visits (run 8, cancel_by_day) ------

ONE_OF_TWO = (
    "Здравствуйте. Мне нужно отменить только запись на воскресенье 15 ноября в 10:30, а запись "
    "на 13 ноября в 11:00 оставить. Как это сделать?"
)


@pytest.mark.parametrize(
    "said",
    [
        ONE_OF_TWO,
        "Перенесите мою запись на завтра. Что для этого нужно?",
        "Отмените запись мужа, а мою перенесите на вечер. Как это сделать?",
    ],
)
def test_how_to_do_it_after_a_request_is_not_a_question(clinic, said: str) -> None:
    plan = split(said, wish_of, clinic.clinic.plain_asks)
    assert plan is None or plan.question is None


async def test_one_of_two_visits_is_asked_about_not_looked_for(make_chat) -> None:
    """Before: «На этот вопрос у меня нет данных» and «Такой записи не нашлось. Вот все Ваши
    записи — какую отменить?»."""
    chat = make_chat()
    await chat.start()
    await book(chat, WIFE, "Завтра")
    await chat.press(texts.MENU_BOOK)  # one more visit of the same patient, on Sunday
    for label in ("Рука или нога", "Похоже на вывих", texts.AUDIENCE_SELF, "Бытовая травма",
                  "В течение 7 дней", "Нет, не обращались"):  # fmt: skip
        await chat.press(label)
    await chat.pick_slot("Вс")
    await chat.press("Фролова Екатерина")
    await chat.press(texts.PAYMENT_SELF)
    assert len(await bookings(chat.conversation.db)) == 2
    await chat.say(ONE_OF_TWO)
    said_now = "\n".join(reply.text for reply in chat.replies)
    assert texts.ASIDE_UNKNOWN not in said_now and "Такой записи не нашлось" not in said_now
    assert texts.CANCEL_WHICH in said_now
    labels = chat.labels()
    assert any("13.11" in label for label in labels) and any("15.11" in label for label in labels)
    assert [b.status for b in await bookings(chat.conversation.db)] == ["active", "active"]
