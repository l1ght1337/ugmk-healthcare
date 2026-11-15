"""Guard words of a section 5 scenario (14.11.2026, the customer's decision).

Run 2 of the simulation: «наступила на иглу от шприца, проколола стопу» — DeepSeek named
«Порез, швы» and the patient was booked instead of the route to the AIDS centre. No route
had been given, so «та же травма или другая?» could not help.

- The words live in the scenario itself (kb/scenarios/05-07…, ``guard_words``) with the
  question to ask (``guard_question``); the code only reads them.
- A booking scenario is about to start and the complaint has such words: the bot does not
  book silently, it asks «Уточните, пожалуйста: это укол чужой иглой?» «Да» — the route of
  05-07; «Нет» — the booking goes on and the question is not asked again.
- Only 05-07 has the words so far.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.bot import texts
from app.flow import topics
from app.flow.loader import KnowledgeBase
from app.flow.schema import Scenario
from tests.fake_deepseek import FakeDeepSeek
from tests.test_conversation import bookings
from tests.test_sim_findings_2 import session

QUESTION = "Уточните, пожалуйста: это укол чужой иглой?"
AIDS = "📍 Центр борьбы со СПИДом, ул. Ясная, 46"
NEEDLE_TEXT = "Здравствуйте! Я сегодня во дворе наступила на иглу от шприца, проколола стопу"
YES_NO = [texts.GUARD_YES, texts.GUARD_NO, texts.BACK, texts.MENU]


def complaint(scenario: str | None, **more: object) -> dict:
    found = [scenario] if scenario else []
    return {"type": "complaint", "area": "limb", "scenario": scenario, "candidates": found,
            "facts": {}, "clarify": None, **more}  # fmt: skip


CUT = complaint("cut_sutures")

# --- the words are data ------------------------------------------------------------------


def test_the_needle_scenario_has_its_words_and_its_question(kb: KnowledgeBase) -> None:
    needle = kb.scenario("needle_injury")
    assert needle.guard_question == QUESTION
    for word in ("игла", "иглу", "иглой", "шприц", "шприца", "шприцем"):
        assert word in needle.guard_words, word


GUARDED = {
    "criminal_trauma": "Уточните, пожалуйста: травма получена при нападении, в драке или Вас "
    "сбила машина?",
    "genital_trauma": "Уточните, пожалуйста: это травма половых органов?",
    "head_trauma": "Уточните, пожалуйста: это травма головы?",
    "eye_trauma": "Уточните, пожалуйста: это травма глаза?",
    "needle_injury": QUESTION,
    "inflammation": "Уточните, пожалуйста: это нарыв или нагноение?",
    "neurology": "Уточните, пожалуйста: это защемление в шее или спине?",
}
BROAD = ("голова", "голову", "нос", "спина", "спину", "грудь", "ребра", "гной", "гноится", "пах")


def test_the_first_step_of_the_words_is_switched_on(kb: KnowledgeBase) -> None:
    """The narrow words and «глаз»; the broad ones wait for a rule about negations."""
    found = {s.id: s.guard_question for s in kb.scenarios.values() if s.guard_words}
    assert found == GUARDED
    every = {w for s in kb.scenarios.values() for w in s.guard_words}
    assert not every & set(BROAD)


@pytest.mark.parametrize(
    ("said", "scenario"),
    [
        ("меня вчера избили, порезали руку", "criminal_trauma"),
        ("подрался на улице, палец опух", "criminal_trauma"),
        ("сбила машина на переходе, болит нога", "criminal_trauma"),
        ("удар пришёлся в мошонку", "genital_trauma"),
        ("ударилась виском об угол шкафа", "head_trauma"),
        ("упал, у ребёнка сотрясение наверное", "head_trauma"),
        ("в глаз отлетела стружка", "eye_trauma"),
        ("на пальце нарыв после пореза", "inflammation"),
        ("подняла коробку, прострелило поясницу", "neurology"),
    ],
)
def test_the_words_of_the_other_scenarios(kb: KnowledgeBase, said: str, scenario: str) -> None:
    assert topics.guard_scenarios(kb, said) == [scenario]


@pytest.mark.parametrize(
    "said",
    ["упал, болит голова и нога", "потянул спину на тренировке", "ударился грудью, болят рёбра",
     "из носа кровь шла", "рана не гноится", "потянул мышцу в паху"],
)  # fmt: skip
def test_the_broad_words_are_not_switched_on(kb: KnowledgeBase, said: str) -> None:
    assert topics.guard_scenarios(kb, said) == []


def test_several_scenarios_in_the_order_of_the_scripts(kb: KnowledgeBase) -> None:
    said = "подрался, в глаз попали, ещё и на шприц наступил"
    assert topics.guard_scenarios(kb, said) == ["criminal_trauma", "eye_trauma", "needle_injury"]


@pytest.mark.parametrize(
    "said",
    [NEEDLE_TEXT, "наступил на шприц", "укололся иглой в подъезде", "в ногу воткнулась иголка",
     "Наступила на ИГЛУ!"],
)  # fmt: skip
def test_a_complaint_with_the_words(kb: KnowledgeBase, said: str) -> None:
    assert topics.guard_scenario(kb, said) == "needle_injury"


@pytest.mark.parametrize(
    "said",
    ["порезал палец ножом", "после иглоукалывания болит спина", "упал, болит нога",
     "шприцевал соседа"],
)  # fmt: skip
def test_a_complaint_without_them(kb: KnowledgeBase, said: str) -> None:
    assert topics.guard_scenario(kb, said) is None


def _raw(kb: KnowledgeBase, scenario: str) -> dict:
    return json.loads(kb.scenario(scenario).model_dump_json())


def test_words_are_only_for_a_routing_scenario_and_need_a_question(kb: KnowledgeBase) -> None:
    booking = _raw(kb, "cut_sutures") | {"guard_words": ["игла"], "guard_question": QUESTION}
    with pytest.raises(ValidationError):
        Scenario.model_validate(booking)
    no_question = _raw(kb, "needle_injury") | {"guard_question": None}
    with pytest.raises(ValidationError):
        Scenario.model_validate(no_question)


# --- the question instead of a silent booking ---------------------------------------------------


async def test_a_booking_scenario_named_for_a_needle_is_asked_about(
    make_chat, kb: KnowledgeBase
) -> None:
    chat = make_chat(assistant=FakeDeepSeek(CUT).assistant(kb))
    await chat.start()
    await chat.say(NEEDLE_TEXT)
    screen = chat.replies[-1]
    assert screen.text.endswith(QUESTION) and chat.labels() == [*YES_NO[:2], texts.MENU]
    assert (await session(chat))["step"] == "guard"
    await chat.press(texts.GUARD_YES)
    assert AIDS in chat.text and "Шаг 2 из 5" not in chat.text
    assert await bookings(chat.conversation.db) == []
    state = await session(chat)
    assert state["talk"]["routed"]["dialog"]["scenario_id"] == "needle_injury"


async def test_no_goes_on_with_the_booking_and_is_not_asked_again(
    make_chat, kb: KnowledgeBase
) -> None:
    chat = make_chat(assistant=FakeDeepSeek(CUT, CUT).assistant(kb))
    await chat.start()
    await chat.say("я порезала палец швейной иглой, рана глубокая")
    assert chat.replies[-1].text.endswith(QUESTION)
    await chat.press(texts.GUARD_NO)
    assert "Шаг 3 из 5" in chat.text and QUESTION not in chat.text  # «я»: for herself
    await chat.press(texts.MENU)
    await chat.say("я порезала палец швейной иглой, рана глубокая")
    assert QUESTION not in chat.text and "Шаг 3 из 5" in chat.text


@pytest.mark.parametrize("typed", ["да", "нет", "да, чужой шприц", "нет, это швейная"])
async def test_the_answer_may_be_typed(make_chat, kb: KnowledgeBase, typed: str) -> None:
    chat = make_chat(assistant=FakeDeepSeek(CUT).assistant(kb))
    await chat.start()
    await chat.say("я наступил на шприц, проколол ногу")
    await chat.say(typed)
    if typed.startswith("да"):
        assert AIDS in chat.text
    else:
        assert "Шаг 3 из 5" in chat.text


async def test_the_topic_picked_by_a_button_after_the_words_is_asked_about_too(
    make_chat, kb: KnowledgeBase
) -> None:
    unclear = complaint(None, clarify="Что случилось с ногой?")
    chat = make_chat(assistant=FakeDeepSeek(unclear).assistant(kb))
    await chat.start()
    await chat.say("наступил на шприц во дворе")
    assert QUESTION not in chat.text  # no scenario yet: the topics
    await chat.press("Порез, рана")
    assert chat.replies[-1].text.endswith(QUESTION)


async def test_the_right_scenario_is_not_asked_about(make_chat, kb: KnowledgeBase) -> None:
    chat = make_chat(assistant=FakeDeepSeek(complaint("needle_injury")).assistant(kb))
    await chat.start()
    await chat.say(NEEDLE_TEXT)
    assert AIDS in chat.text and QUESTION not in chat.text


async def test_a_complaint_without_the_words_is_not_asked_about(
    make_chat, kb: KnowledgeBase
) -> None:
    chat = make_chat(assistant=FakeDeepSeek(CUT).assistant(kb))
    await chat.start()
    await chat.say("я порезал палец ножом на кухне")
    assert QUESTION not in chat.text and "Шаг 3 из 5" in chat.text


async def test_back_from_the_question_returns_to_the_start(make_chat, kb: KnowledgeBase) -> None:
    chat = make_chat(assistant=FakeDeepSeek(CUT).assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.say(NEEDLE_TEXT)
    assert chat.replies[-1].text.endswith(QUESTION) and chat.labels() == YES_NO
    await chat.press(texts.BACK)
    assert texts.ASK_COMPLAINT in chat.text


# --- the other scenarios of section 5 (the first step, 14.11.2026) ------------------------------

MUNICIPAL = "Любой муниципальный травмпункт"


async def test_an_assault_in_a_scenario_that_does_not_ask_about_it(
    make_chat, kb: KnowledgeBase
) -> None:
    """«Порез, швы» has no «Как получена травма?»: the words of 05-01 are the only guard."""
    chat = make_chat(assistant=FakeDeepSeek(CUT).assistant(kb))
    await chat.start()
    await chat.say("меня вчера избили, порезали руку стеклом")
    assert chat.replies[-1].text.endswith(GUARDED["criminal_trauma"])
    await chat.press(texts.GUARD_YES)
    assert MUNICIPAL in chat.text and await bookings(chat.conversation.db) == []


async def test_no_to_the_assault_is_not_asked_again_by_the_scenario(
    make_chat, kb: KnowledgeBase
) -> None:
    """04-07 asks «Как получена травма?» itself: «Нет» to the guard is its answer."""
    wrong = complaint("fracture", facts={"assault": False})  # DeepSeek read «подрался» as home
    chat = make_chat(assistant=FakeDeepSeek(wrong).assistant(kb))
    await chat.start()
    await chat.say("я подрался, кажется сломал палец на руке")
    assert chat.replies[-1].text.endswith(GUARDED["criminal_trauma"])
    await chat.press(texts.GUARD_NO)
    assert "Как получена травма?" not in chat.text and "Шаг 3 из 5" in chat.text
    assert (await session(chat))["dialog"]["facts"]["assault"] is False


async def test_yes_to_the_assault_is_the_route_whatever_deepseek_said(
    make_chat, kb: KnowledgeBase
) -> None:
    wrong = complaint("fracture", facts={"assault": False})
    chat = make_chat(assistant=FakeDeepSeek(wrong).assistant(kb))
    await chat.start()
    await chat.say("я подрался, кажется сломал палец на руке")
    await chat.press(texts.GUARD_YES)
    assert MUNICIPAL in chat.text


async def test_two_guards_are_asked_one_after_another(make_chat, kb: KnowledgeBase) -> None:
    chat = make_chat(assistant=FakeDeepSeek(CUT).assistant(kb))
    await chat.start()
    await chat.say("я подрался, в глаз попали, и руку порезал")
    assert chat.replies[-1].text.endswith(GUARDED["criminal_trauma"])
    await chat.press(texts.GUARD_NO)
    assert chat.replies[-1].text.endswith(GUARDED["eye_trauma"])
    await chat.press(texts.GUARD_NO)
    assert texts.ASK_AUDIENCE in chat.text and "Уточните" not in chat.text  # the booking


async def test_the_eye_is_the_route_of_the_eye(make_chat, kb: KnowledgeBase) -> None:
    chat = make_chat(assistant=FakeDeepSeek(CUT).assistant(kb))
    await chat.start()
    await chat.say("я порезал бровь, и в глаз попало стекло")
    assert chat.replies[-1].text.endswith(GUARDED["eye_trauma"])
    await chat.press(texts.GUARD_YES)
    assert "ЦГБ № 23" in chat.text
