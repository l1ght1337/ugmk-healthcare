"""A patient of 15–17 who books for himself, and texts on the consent screen.

From the second simulation run (teen16): after the birth date the bot asked a sixteen-year-old
for «я законный представитель ребёнка» and answered «Не понял сообщение» three times. The
clinic's rule for a visit without a parent from 15 to 18 is not known yet
(docs/clinic_requests.md, item 10): the bot neither asks for that line nor books — the request
goes to an administrator, and a parent may take the booking over.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

import pytest

from app.bot import texts
from app.bot.conversation import NOT_UNDERSTOOD
from app.flow.commands import says_self
from tests import intent_cases as m
from tests.chat_harness import Chat
from tests.test_conversation import book_dislocation, bookings
from tests.test_intents import session, to_step
from tests.test_no_dead_ends import EVERY_STEP, to_screen
from tests.test_safeguards import patients, to_birth
from tests.test_wish_flow import through_dislocation

TEEN = (
    "Вам 16 лет. Правила самостоятельной записи с 15 до 18 лет уточнит администратор — я передал "
    "ему обращение, с Вами свяжутся. Если рядом родитель или законный представитель, запись "
    "может оформить он."
)


async def test_a_teen_booking_himself_goes_to_an_administrator(make_chat) -> None:
    chat = make_chat()
    await to_birth(chat, texts.AUDIENCE_SELF)
    slot = (await session(chat))["slot_id"]
    await chat.say("09.09.2010")
    (ask,) = chat.replies  # the phone first: the administrator has to reach him
    assert ask.ask_contact and texts.CONSENT_ITEM_GUARDIAN not in chat.text
    await chat.contact()
    screen = chat.replies[-1]
    assert screen.text == TEEN and chat.labels() == [texts.TEEN_PARENT, texts.MENU]
    assert "законный представитель ребёнка" not in chat.text  # the consent line is not asked for
    assert await bookings(chat.conversation.db) == []  # the bot does not book by itself
    state = await session(chat)
    assert (state["step"], state["slot_id"]) == ("teen", slot)
    (post,) = chat.admin  # the complaint, the answers and the chosen time
    print(post.text)
    for part in ("16 лет записывается сам", "13 ноября", "Сценарий: ", "Бытовая травма",
                 "Белов Артём Игоревич, 09.09.2010", "+79123456789"):  # fmt: skip
        assert part in post.text, part


async def test_a_parent_takes_the_booking_over_with_the_answers_and_the_time(make_chat) -> None:
    chat = make_chat()
    await to_birth(chat, texts.AUDIENCE_SELF)
    slot = (await session(chat))["slot_id"]
    await chat.say("09.09.2010")
    await chat.contact()
    await chat.press(texts.TEEN_PARENT)
    assert texts.CONSENT_ITEM_GUARDIAN in chat.text  # the consent, with the representative's line
    state = await session(chat)
    assert state["dialog"]["age_months"] == 194 and state["slot_id"] == slot
    await chat.press(texts.CONSENT_YES)
    assert "Артём Игоревич записан" in chat.text
    (booking,) = await bookings(chat.conversation.db)
    (patient,) = await patients(chat)
    assert (booking.slot_id, patient.audience) == (slot, "child")


async def test_a_teen_with_a_known_phone_is_answered_at_once(make_chat) -> None:
    chat = make_chat()
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    await chat.press(texts.MENU_BOOK)
    await through_dislocation(chat)
    await chat.press(texts.ALREADY_AGAIN)
    await chat.pick_slot("Завтра")
    await chat.press(texts.PATIENT_NEW)
    await chat.say("Белов Артём Игоревич, 09.09.2010")
    assert chat.replies[-1].text == TEEN and len(chat.admin) == 1
    assert len(await bookings(chat.conversation.db)) == 1


@pytest.mark.parametrize("born", ["09.09.2011", "13.11.2008"])  # fifteen and almost eighteen
async def test_the_edges_of_fifteen_to_seventeen(make_chat, born: str) -> None:
    chat = make_chat()
    await to_birth(chat, texts.AUDIENCE_SELF)
    await chat.say(born)
    assert chat.replies[-1].ask_contact and (await session(chat))["teen_months"] is not None


async def test_under_fifteen_is_led_as_a_child_and_a_text_gets_an_answer(make_chat) -> None:
    chat = make_chat()
    await to_birth(chat, texts.AUDIENCE_SELF)
    await chat.say("09.12.2011")  # fourteen
    line, screen = chat.replies
    assert line.text == texts.AGE_IS_CHILD.format(age="14 лет")
    assert texts.CONSENT_ITEM_GUARDIAN in screen.text
    await chat.say("мне 14, я сам записываюсь, родителей рядом нет")
    answer, again = chat.replies
    assert answer.text == texts.CONSENT_BY_PARENT
    assert [b.label for row in answer.buttons for b in row] == [texts.MENU_ADMIN]
    assert texts.CONSENT_ITEM_GUARDIAN in again.text and (await session(chat))["step"] == "consent"


async def test_i_am_the_patient_on_a_childs_consent_at_sixteen(make_chat) -> None:
    chat = make_chat()
    await to_step(chat, m.AGE)
    await chat.say("16 лет")
    for label in ("Бытовая травма", "В течение 7 дней", "Нет, не обращались"):
        await chat.press(label)
    await chat.pick_slot("Завтра")
    assert texts.CONSENT_ITEM_GUARDIAN in chat.text
    await chat.say("я не законный представитель, я сам пациент. запишите меня")
    assert texts.CONSENT_ITEM_GUARDIAN not in chat.text  # a plain consent for the request
    await chat.press(texts.CONSENT_YES)
    await chat.contact()
    assert chat.replies[-1].text == TEEN and len(chat.admin) == 1


@pytest.mark.parametrize(
    "said",
    ["мне 16, я сам записываюсь", "я не законный представитель, я сам пациент",
     "я не ребёнок, мне 16 и я сам записываюсь", "родителей рядом нет", "я и есть пациент"],
)  # fmt: skip
def test_i_am_the_patient_in_words(said: str) -> None:
    assert says_self(said)


@pytest.mark.parametrize("said", ["согласен", "а зачем это?", "сыну 16 лет", "записывайте"])
def test_what_is_not_i_am_the_patient(said: str) -> None:
    assert not says_self(said)


# --- any text on the consent gets an answer ------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "answer"),
    [
        ("а зачем это?", "CONSENT_WHY"),
        ("а с чем я согласен?", "CONSENT_WHY"),
        ("ну давайте уже", "CONSENT_HINT"),
        ("да сколько можно", "CONSENT_HINT"),
    ],
)
async def test_a_text_on_the_consent_is_answered(make_chat, said: str, answer: str) -> None:
    chat = make_chat()
    await to_step(chat, m.CONSENT)
    await chat.say(said)
    line, screen = chat.replies
    assert line.text == getattr(texts, answer) and "Не понял" not in chat.text
    assert texts.CONSENT_YES in [b.label for row in screen.buttons for b in row]


# --- never three «не понял» in a row -------------------------------------------------------------

NONSENSE = ("ну и ну", "мда уж", "ох", "эх", "ну и ну", "мда уж", "ох")


def missed(chat: Chat) -> bool:
    said = [r.text for r in chat.replies]
    return any(text in NOT_UNDERSTOOD or text.startswith(texts.PICK_BUTTON) for text in said)


@pytest.mark.parametrize("screen", EVERY_STEP)
async def test_never_three_not_understood_in_a_row(make_chat, screen: str) -> None:
    chat = make_chat()
    await to_screen(chat, screen)
    row = 0
    for said in NONSENSE:
        await chat.say(said)
        row = row + 1 if missed(chat) else 0
        assert row < 3, f"«{said}» на шаге {screen}: третий раз подряд\n{chat.text}"
        assert chat.buttons() or chat.replies[-1].ask_contact, f"{screen}: {chat.text}"
        if texts.STUCK in chat.text:  # said instead of the third: with a way to a person
            assert texts.MENU_ADMIN in chat.labels(), f"{screen}: {chat.labels()}"


async def test_the_third_time_the_bot_says_so_and_offers_an_administrator(make_chat) -> None:
    chat = make_chat()
    await to_step(chat, m.BIRTH)
    for _ in range(2):
        await chat.say("ну и ну")
        assert chat.replies[0].text == texts.ASK_BIRTH_AGAIN
    await chat.say("ну и ну")
    assert chat.replies[0].text == texts.STUCK and texts.MENU_ADMIN in chat.labels()
    await chat.say("ну и ну")  # the count starts anew
    assert chat.replies[0].text == texts.ASK_BIRTH_AGAIN
