"""An injury the patient himself calls old is not booked silently (the customer, 15.11.2026).

Run 8, certificate: the patient wrote «подвернула ногу года три назад» twice and was booked
for today — the scenario of ligaments (04-12) does not ask when the injury happened. The
script 03-1 says: «Принимаем скелетно-костные травмы рук и ног… сроком не более 7-ми дней».

How long ago, said by the patient («года три назад», «месяц назад», «в прошлом году»,
«старая травма»), is read by the code. In a scenario of a first visit that has no question
about it (04-12, the first visit of 04-02) an injury older than 7 days is not booked: the
rule of the script is said and the question goes to the administrator. No route is made up.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

import pytest

from app.bot import texts
from app.config import ClinicData
from app.flow import injury_time
from app.flow.loader import KnowledgeBase
from tests.test_conversation import bookings
from tests.test_intents import session

RULE = "В травмпункте принимаем травмы рук и ног сроком не более 7 дней."
OLD = "Нога. Старая травма, подвернула ногу года три назад, было растяжение. Сейчас снова болит."
WEEK = 7 * 24


@pytest.mark.parametrize(
    "said",
    [
        "подвернула ногу года три назад",
        "три года назад было растяжение",
        "3 года назад",
        "год назад подвернул",
        "в прошлом году упал",
        "старая травма ноги, беспокоит при нагрузке",
        "месяц назад",
        "недели три назад",
        "10 дней назад",
    ],
)
def test_older_than_seven_days_in_the_patients_words(clinic: ClinicData, said: str) -> None:
    found = injury_time.hours(said, clinic.clinic.injury_time)
    assert found and all(low >= WEEK for low, _ in found), found


@pytest.mark.parametrize(
    "said", ["подвернула вчера", "сегодня упал", "неделю назад", "три дня назад", "болит нога"]
)
def test_not_surely_older_than_seven_days(clinic: ClinicData, said: str) -> None:
    found = injury_time.hours(said, clinic.clinic.injury_time)
    assert not found or any(low < WEEK for low, _ in found)


async def ligament(chat, said: str) -> None:
    """«Записаться» → what happened, in words → the buttons of 04-12 up to its outcome."""
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.say(said)
    for label in ("Упал, ударился, подвернул", "Растяжение или разрыв связок",
                  texts.AUDIENCE_SELF, "Бытовая травма", "Я предполагаю"):  # fmt: skip
        if label in chat.labels():
            await chat.press(label)


async def test_a_sprain_of_three_years_ago_is_not_booked(make_chat, kb: KnowledgeBase) -> None:
    chat = make_chat(admin_chat_id=-100)
    await ligament(chat, OLD)
    said = "\n".join(reply.text for reply in chat.replies)
    assert RULE in said and "Выберите, пожалуйста, день приёма" not in said, said
    assert chat.labels() == [texts.ASK_ADMIN_QUESTION, texts.MENU_HOME]
    state = await session(chat)
    assert state["step"] == "offer_admin" and not state["wizard"]
    assert "года три назад" in state["handoff_context"]  # what the administrator will read
    assert "📍" not in said  # no route is made up
    await chat.press(texts.ASK_ADMIN_QUESTION)
    await chat.press(texts.CONSENT_YES)
    await chat.contact()
    assert "обращение № 1" in chat.text and await bookings(chat.conversation.db) == []


@pytest.mark.parametrize("said", ["Подвернула ногу вчера, опухла", "Нога болит, подвернула"])
async def test_a_fresh_sprain_is_booked_as_before(make_chat, said: str) -> None:
    chat = make_chat()
    await ligament(chat, said)
    assert "Выберите, пожалуйста, день приёма" in chat.text and RULE not in chat.text


async def test_a_scenario_with_its_own_question_decides_itself(make_chat) -> None:
    """A dislocation (04-01) asks «Когда была получена травма?»: its own branches answer."""
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.say("вывихнул плечо месяц назад")
    for label in ("Похоже на вывих", texts.AUDIENCE_SELF, "Бытовая травма"):
        if label in chat.labels():
            await chat.press(label)
    assert RULE not in "\n".join(reply.text for reply in chat.replies)


def test_the_scenarios_that_do_not_ask_say_how_old_an_injury_they_take(kb: KnowledgeBase) -> None:
    limited = {s.id: s.injury_days_max for s in kb.scenarios.values() if s.injury_days_max}
    assert limited == {"ligament_rupture": 7, "dressing": 7}
