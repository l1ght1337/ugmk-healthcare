"""«И сына тоже запишите» in the middle of a booking: one line, and his turn after the card.

The patient is booking himself and remembers the son. The bot answers with one line — «Сына
запишу следующим — после этой записи.» — keeps the step, remembers the son and after the
card offers «Записать сына», as for the patients named at the start (tests/test_two_named).
What is said with it — the age, the injury — is kept for his booking. Read by the code: a
request to book with «тоже», «ещё» and a person in it; DeepSeek is not asked.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

import pytest

from app.bot import texts
from app.bot.conversation import Ctx, Session, Turn
from app.db.models import User
from app.flow.loader import KnowledgeBase
from app.flow.people import asked_too
from tests.chat_harness import Chat
from tests.fake_deepseek import FakeDeepSeek
from tests.test_intents import session
from tests.test_two_named import KIDS, MENU

WHEN = "Когда была получена травма?"


async def to_when(chat: Chat, who: str = texts.AUDIENCE_SELF) -> None:
    """A booking under way: the second question of a dislocation."""
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")
    await chat.press(who)
    if who != texts.AUDIENCE_SELF:
        await chat.say("7 лет")
    await chat.press("Бытовая травма")
    assert WHEN in chat.text


async def to_the_card(chat: Chat, name: str) -> None:
    # the line has no buttons: the question with its buttons stands above it in the chat
    await chat.data(next(b.data for b in await shown(chat) if b.label == "В течение 7 дней"))
    await chat.press("Нет, не обращались")
    await chat.pick_slot("Завтра")
    await chat.press(texts.CONSENT_YES)
    await chat.say(name)
    await chat.contact()
    await chat.press(texts.PAYMENT_SELF)


async def shown(chat: Chat) -> list:
    """The buttons of the step's screen, as they stand in the chat above the bot's line."""
    conversation = chat.conversation
    async with conversation.db() as db:
        user = await db.get(User, chat.user.id)
        state = Session.model_validate(user.session)
        replies = await conversation._screen_now(Ctx(db, user, state, Turn(), conversation.clock()))
    return [button for reply in replies for row in reply.buttons for button in row]


def named(text: str) -> list[tuple[str, str | None, int | None, str]]:
    return [(p.name, p.audience, p.age_months, p.complaint) for p in asked_too(text)]


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("и сына тоже запишите", [("сын", "child", None, "")]),
        ("И сына, пожалуйста, тоже запишите", [("сын", "child", None, "")]),
        ("ещё мужа запишите потом", [("муж", "adult", None, "")]),
        ("а дочку можно тоже записать?", [("дочка", "child", None, "")]),
        ("и Ваню тоже запишите", [("Ваня", None, None, "")]),
        ("Запишите ещё Ваню, ему 10 лет, ногу подвернул",
         [("Ваня", "child", 120, "ногу подвернул")]),
        ("И сына тоже запишите, ему 12, колено ушиб", [("сын", "child", 144, "колено ушиб")]),
        ("и меня тоже запишите", [("", "self", None, "")]),
        # no «тоже», no «ещё»: who the booking is for, not one more patient
        ("сына запишите", []),
        ("запишите меня", []),
        ("сын тоже упал", []),  # nothing is asked
        ("тоже болит нога", []),
    ],
)  # fmt: skip
def test_who_else_is_asked_to_be_booked(text: str, found: list) -> None:
    assert named(text) == found


async def test_the_son_is_named_in_the_middle_of_a_booking(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await to_when(chat)
    await chat.say("и сына тоже запишите")
    (line,) = chat.replies  # one line: the step stays, «Запишу по очереди» is not said
    assert line.text == "Сына запишу следующим — после этой записи." and not line.buttons
    assert fake.requests == []
    state = await session(chat)
    assert state["step"] == "question" and state["dialog"]["question_id"] == "when"
    assert [p["name"] for p in state["people"]] == ["сын"] and not state["more"]

    await to_the_card(chat, "Петров Иван Сергеевич, 12.03.1985")
    assert chat.text == texts.NEXT_PATIENT and chat.labels() == ["Записать сына", *MENU]
    await chat.press("Записать сына")
    assert "📝 Записываю сына" in chat.text and texts.ASK_AUDIENCE not in chat.text


async def test_what_is_said_with_him_is_kept(make_chat) -> None:
    chat = make_chat()
    await to_when(chat)
    await chat.say("И сына тоже запишите, ему 12, колено ушиб")
    assert chat.text == "Сына запишу следующим — после этой записи."
    await to_the_card(chat, "Петров Иван Сергеевич, 12.03.1985")
    await chat.press("Записать сына")
    assert "📝 Записываю сына, 12 лет: колено ушиб" in chat.text


async def test_a_first_name_and_the_writer(make_chat) -> None:
    chat = make_chat()
    await to_when(chat, texts.AUDIENCE_CHILD)
    await chat.say("и Ваню тоже запишите")
    assert chat.text == "Ваню запишу следующим — после этой записи."
    await chat.say("и меня тоже запишите")  # the booking is for a child: the writer is one more
    assert chat.text == "Вас запишу следующим — после этой записи."
    assert [p["name"] for p in (await session(chat))["people"]] == ["", "Ваня"]  # the last asked
    await to_the_card(chat, "Петрова Мария Ивановна, 12.03.2019")
    assert chat.labels()[0] == "Записать меня"


@pytest.mark.parametrize("said", ["и меня тоже запишите", "сына запишите", "сын тоже упал"])
async def test_what_is_not_one_more_patient(make_chat, said: str) -> None:
    chat = make_chat()
    await to_when(chat)  # the booking is for the writer
    await chat.say(said)
    assert "запишу следующим" not in chat.text
    assert (await session(chat))["people"] == []


async def test_named_while_another_waits_he_is_the_next(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say(KIDS)
    await chat.press("Машу, 7 лет")
    await chat.say("и мужа тоже запишите")
    assert chat.text == "Мужа запишу следующим — после этой записи."
    assert [p["name"] for p in (await session(chat))["people"]] == ["муж", "Ваня"]


async def test_named_on_who_is_first_he_joins_the_list(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say(KIDS)
    await chat.say("и мужа тоже запишите")
    assert chat.replies[-1].text == f"{texts.ONE_BY_ONE_MANY} {texts.WHO_FIRST}"
    assert chat.labels() == ["Машу, 7 лет", "Ваню, 10 лет", "Мужа", texts.MENU]
