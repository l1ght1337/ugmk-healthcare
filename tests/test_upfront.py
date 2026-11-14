"""A field with ``upfront: false`` is asked, never taken from the complaint.

From the first simulation run (questions): the same sprained ankle was booked on the first
pass and sent to other hospitals on the second. The patient wrote «я же сказала — растяжение,
голеностоп», and route (DeepSeek) put ``rupture_certainty: insists`` into ``facts``; the engine
skipped «Разрыв уже подтвердил врач или МРТ, или Вы предполагаете?». The answer to that
question decides between a booking and a refusal, so it comes only from the patient.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

import pytest

from app.bot import texts
from app.flow.loader import KnowledgeBase
from tests.fake_deepseek import FakeDeepSeek
from tests.test_intents import session

ASKS = "Разрыв уже подтвердил врач или МРТ, или Вы предполагаете?"
SAID = (
    "а зачем вы по кругу одно и то же спрашиваете? я же сказала — растяжение, голеностоп. "
    "так запишете на 13:30 или нет?"
)


def route(**facts: object) -> dict:
    return {"type": "complaint", "area": "limb", "scenario": "ligament_rupture",
            "candidates": ["ligament_rupture"], "facts": facts, "clarify": None}  # fmt: skip


@pytest.mark.parametrize("certainty", ["insists", "suspects"])
async def test_the_second_pass_of_questions_asks_the_question(
    make_chat, kb: KnowledgeBase, certainty: str
) -> None:
    fake = FakeDeepSeek(route(assault=False, rupture_certainty=certainty))
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.say(SAID)  # «для себя» is not said here: asked
    await chat.press(texts.AUDIENCE_SELF)
    assert ASKS in chat.text  # whatever route made of «я же сказала — растяжение»
    state = await session(chat)
    assert "rupture_certainty" not in state["dialog"]["facts"]
    await chat.press("Я предполагаю")
    assert (await session(chat))["dialog"]["outcome_id"] == "book_primary"


async def test_route_does_not_return_the_field(kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek(route(assault=False, rupture_certainty="insists"))
    reading = await fake.assistant(kb).route(SAID)
    assert reading.scenario == "ligament_rupture" and reading.facts == {"assault": False}


async def test_extract_keeps_the_field_only_for_its_own_question(kb: KnowledgeBase) -> None:
    scenario = kb.scenario("ligament_rupture")
    said = {"facts": {"assault": False, "rupture_certainty": "insists"}, "unclear": False}
    fake = FakeDeepSeek(said, {"facts": {"rupture_certainty": "insists"}, "unclear": False})
    assistant = fake.assistant(kb)
    how = await assistant.extract(scenario, scenario.question("how"), "дома, врач сказал разрыв")
    assert how.facts == {"assault": False}  # the question about the rupture is still to come
    own = await assistant.extract(scenario, scenario.question("certainty"), "да, МРТ подтвердило")
    assert own.facts == {"rupture_certainty": "insists"}
