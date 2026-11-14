"""The bot does not name an injury the patient has not named (rule 1).

From the second simulation run (teen16): the patient pressed «Не знаю» on «На что больше
похоже?», and the bot wrote «Ваши ответы: вывих» and «врач попробует вправить вывих». The
scenario is chosen for the route, not as the patient's answer.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

import re

import pytest

from app.bot import texts
from app.flow.loader import KnowledgeBase
from tests import simulate as sim
from tests.chat_harness import Chat
from tests.fake_deepseek import FakeDeepSeek
from tests.simulate import Action, Shown, check
from tests.simulate_personas import PERSONAS
from tests.test_simulate import made, step

ANSWERS = ("Бытовая травма", "В течение 7 дней", "Нет, не обращались")
PERSONA = next(p for p in PERSONAS if p.key == "teen16")


async def to_summary(chat: Chat) -> str:
    """Through the dislocation questions to «Ваши ответы: …» above the day picker."""
    if texts.AUDIENCE_SELF in chat.labels():
        await chat.press(texts.AUDIENCE_SELF)
    for label in ANSWERS:
        await chat.press(label)
    return next(r.text for r in chat.replies if r.text.startswith("Ваши ответы"))


async def test_unsure_is_not_called_a_dislocation(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.say("подвернул ногу, опухло")  # no DeepSeek: the word list opens the topics
    assert "На что больше похоже?" in chat.text
    await chat.press("Не знаю")
    summary = await to_summary(chat)
    assert summary.startswith(f"Ваши ответы: {texts.SUMMARY_UNSURE}; для себя; бытовая травма")
    assert "Ваши ответы: вывих" not in chat.text
    assert "Если это вывих, врач попробует его вправить" in summary  # only as a condition
    assert "попробует вправить вывих" not in chat.text


async def test_the_topic_the_patient_pressed_is_his_answer(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")
    assert (await to_summary(chat)).startswith("Ваши ответы: вывих; для себя")


@pytest.mark.parametrize(
    ("said", "scenario", "first"),
    [
        ("подвернул ногу на тренировке, болит голеностоп", "dislocation", texts.SUMMARY_BY_WORDS),
        ("кажется, вывихнул плечо", "dislocation", "вывих"),
        ("упала на руку, опухло запястье", "fracture", texts.SUMMARY_BY_WORDS),
        ("похоже, сломала руку", "fracture", "перелом"),
        ("потянул связки на ноге", "ligament_rupture", "разрыв связок"),
    ],
)
async def test_a_scenario_chosen_by_route_is_named_only_in_the_patients_words(
    make_chat, kb: KnowledgeBase, said: str, scenario: str, first: str
) -> None:
    route = {"type": "complaint", "area": "limb", "scenario": scenario, "candidates": [scenario],
             "facts": {"assault": False}, "clarify": None}  # fmt: skip
    chat = make_chat(assistant=FakeDeepSeek(route).assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.say(said)
    for label in (texts.AUDIENCE_SELF, "В течение 7 дней", "Нет, не обращались", "Я предполагаю"):
        if label in chat.labels():
            await chat.press(label)
    summary = next(r.text for r in chat.replies if r.text.startswith("Ваши ответы"))
    assert summary.startswith(f"Ваши ответы: {first}; для себя"), summary


def test_short_phrases_keep_the_conditions_of_the_scripts(kb: KnowledgeBase) -> None:
    """A short version may drop words, not an «если»: the condition is the meaning."""
    kept = {
        "dislocation": "если это вывих",
        "sutures": "если нужен косметический",
        "repeat": "только если",
        "parking_night": "после 20:00",  # «Если Вы приезжаете после 20:00…»
    }
    shorts = {p.id: p for p in kb.shorts.phrases}
    with_if = {
        i for i, p in shorts.items() if re.search(r"\b(?:если|при условии)\b", p.full.lower())
    }
    assert with_if == set(kept)
    for phrase_id, words in kept.items():
        assert words in shorts[phrase_id].short.lower(), shorts[phrase_id].short
    assert all(len(p.short) <= 90 for p in shorts.values())


# --- the simulator's rule ------------------------------------------------------------------------

MENU = Shown("На что больше похоже?", ("Похоже на вывих", "Похоже на перелом", "Не знаю"))
NAMED = Shown("Ваши ответы: вывих; для себя; бытовая травма.")


def kinds(*steps, env_kb, clinic) -> list[str]:
    found = check(made(PERSONA, *steps), env_kb, clinic)
    return [v.detail for v in found if v.rule == sim.ADVICE]


def test_the_rule_an_injury_the_patient_did_not_name(kb, clinic) -> None:
    said = step(1, Action("say", "подвернул ногу, опухло"), MENU)
    unsure = step(2, Action("press", "Не знаю"), NAMED)
    (detail,) = kinds(said, unsure, env_kb=kb, clinic=clinic)
    assert "вывих" in detail


@pytest.mark.parametrize(
    ("first", "second", "reply"),
    [
        (Action("say", "подвернул ногу"), Action("press", "Похоже на вывих"), NAMED.text),
        (Action("say", "кажется, вывихнул плечо"), Action("press", "Не знаю"), NAMED.text),
        (Action("say", "подвернул ногу"), Action("press", "Не знаю"),
         "Можно записаться к травматологу-ортопеду. Если это вывих, врач попробует его вправить."),
        (Action("say", "подвернул ногу"), Action("say", "не знаю"),
         "Подскажите, пожалуйста, на что больше похоже: вывих, перелом или растяжение?"),
        (Action("say", "подвернул ногу"), Action("press", "Не знаю"),
         "Ваши ответы: не знаю, на что похоже; для себя."),
    ],
)  # fmt: skip
def test_the_rule_leaves_what_is_named_asked_or_conditional(
    kb, clinic, first: Action, second: Action, reply: str
) -> None:
    steps = (step(1, first, MENU), step(2, second, Shown(reply)))
    assert kinds(*steps, env_kb=kb, clinic=clinic) == []


def test_the_rule_takes_the_question_the_patient_answered(kb, clinic) -> None:
    """«Разрыв уже подтвердил врач или МРТ?» — «Подтвердил врач или МРТ»: the route about a
    rupture is not a diagnosis of the bot's."""
    asked = Shown("Разрыв уже подтвердил врач или МРТ, или Вы предполагаете?",
                  ("Подтвердил врач или МРТ", "Я предполагаю"))  # fmt: skip
    steps = (
        step(1, Action("say", "болит нога после тренировки"), asked),
        step(2, Action("press", "Подтвердил врач или МРТ"),
             Shown("С разрывами связок Вам нужно обратиться:")),
    )  # fmt: skip
    assert kinds(*steps, env_kb=kb, clinic=clinic) == []
