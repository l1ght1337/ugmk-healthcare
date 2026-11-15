"""Words about when the injury happened are read by the code, not by DeepSeek.

From the live runs (15.11): «Дети сегодня с горки упали» — DeepSeek took «сегодня» for the
answer to «Когда была получена травма?» in one run and did not in the next. The words that
leave no doubt stand in config/clinic.yaml (``injury_time``): «сегодня», «только что»,
«утром», «вчера», «позавчера», «N дней назад», «неделю назад», «месяц назад» — each with how
many hours ago it may be. The thresholds stand with the answers of the question in the
scenario (``to_hours`` / ``from_hours`` of an option): 7 days in 04-01, 04-05, 04-07, 04-11;
24 hours in 04-03 and 04-06.

A word that does not answer the threshold («вчера» for 24 hours) means the question is asked;
so does a word that disagrees with DeepSeek. With no such word DeepSeek decides, as before.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

import json

import pytest

from app.bot import texts
from app.config import ClinicData
from app.flow import injury_time, wishes
from app.flow.injury_time import ASK
from app.flow.loader import KnowledgeBase
from tests.conftest import NOW
from tests.fake_deepseek import FakeDeepSeek
from tests.test_intents import session
from tests.test_two_kids_dialog import ANSWERED, FIRST, UNCLEAR, ByTask, sent

WEEK, DAY = ("within_7_days", "over_7_days"), ("within_24h", "over_24h")
WHEN = "Когда была получена травма?"


def read(kb: KnowledgeBase, clinic: ClinicData, scenario: str, text: str):
    def wish(part: str) -> bool:  # as the bot cuts it: «запишите на сегодня» is a wish
        found = wishes.parse(part, clinic.schedule.doctors, NOW.date(), context="strict")
        return not found.wish.empty

    field = kb.scenario(scenario).fields()["injury_age"]
    return injury_time.read(text, field, clinic.clinic.injury_time, wish)


@pytest.mark.parametrize(
    ("text", "week", "day"),
    [
        ("Дети сегодня с горки упали", WEEK[0], DAY[0]),
        ("только что упал", WEEK[0], DAY[0]),
        ("час назад порезал палец", WEEK[0], DAY[0]),
        ("утром обожглась", WEEK[0], DAY[0]),
        ("5 часов назад", WEEK[0], DAY[0]),
        ("30 часов назад", WEEK[0], DAY[1]),
        ("вчера упал", WEEK[0], ASK),  # «вчера» may be less or more than 24 hours ago
        ("день назад", WEEK[0], ASK),
        ("позавчера", WEEK[0], DAY[1]),
        ("2 дня назад", WEEK[0], DAY[1]),
        ("три дня назад", WEEK[0], DAY[1]),
        ("пару дней назад", WEEK[0], DAY[1]),
        ("6 дней назад", WEEK[0], DAY[1]),
        ("7 дней назад", ASK, DAY[1]),  # the very threshold: asked
        ("неделю назад", ASK, DAY[1]),
        ("8 дней назад", WEEK[1], DAY[1]),
        ("две недели назад", WEEK[1], DAY[1]),
        ("месяц назад", WEEK[1], DAY[1]),
        ("упал вчера, а сегодня опухло", WEEK[0], ASK),
        ("месяц назад сломал, а сегодня снова упал", ASK, ASK),  # two times that disagree
        ("болит нога", None, None),  # no word: DeepSeek decides
        ("давно уже", None, None),
        ("несколько дней назад", None, None),
        # a wish for the visit is not when it happened
        ("запишите на сегодня", None, None),
        ("можно прийти завтра утром?", None, None),
        ("упал вчера, запишите на сегодня", WEEK[0], ASK),
        ("вчерашний день", None, None),  # whole words
    ],
)
def test_the_words_and_the_thresholds(
    kb: KnowledgeBase, clinic: ClinicData, text: str, week: object, day: object
) -> None:
    assert read(kb, clinic, "dislocation", text) == week
    assert read(kb, clinic, "burns", text) == day


def test_the_thresholds_stand_in_the_scenarios(kb: KnowledgeBase) -> None:
    hours = {}
    for scenario in kb.scenarios.values():
        field = scenario.fields().get("injury_age")
        if field is not None:
            hours[scenario.id] = [(o.value, o.from_hours, o.to_hours) for o in field.options]
    week = [("within_7_days", None, 168), ("over_7_days", 168, None)]
    day = [("within_24h", None, 24), ("over_24h", 24, None)]
    assert hours == {
        "dislocation": week, "sick_leave": week, "fracture": week, "animal_bite": week,
        "cut_sutures": day, "burns": day,
    }  # fmt: skip


CUT = {"wound_location": "body", "motor_function": True}  # the questions before «Когда?»


def route(scenario: str, **facts: object) -> dict:
    return {"type": "complaint", "area": "limb", "scenario": scenario,
            "candidates": [scenario], "facts": facts, "clarify": None}  # fmt: skip


async def complaint(make_chat, kb: KnowledgeBase, said: str, reply: dict):
    fake = FakeDeepSeek(reply)
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.say(said)
    if texts.AUDIENCE_SELF in chat.labels():
        await chat.press(texts.AUDIENCE_SELF)
    return chat, fake


async def test_the_code_reads_what_deepseek_left_out(make_chat, kb: KnowledgeBase) -> None:
    said = "вчера упал на плечо, похоже вывих, к врачу не ходил"
    reply = route("dislocation", assault=False, seen_doctor="no")  # no injury_age
    chat, _ = await complaint(make_chat, kb, said, reply)
    assert WHEN not in chat.text and "в течение 7 дней" in chat.replies[0].text
    assert (await session(chat))["dialog"]["facts"]["injury_age"] == "within_7_days"


async def test_a_word_that_disagrees_with_deepseek_is_asked(make_chat, kb: KnowledgeBase) -> None:
    said = "сегодня упал на плечо, похоже вывих, к врачу не ходил"
    reply = route("dislocation", assault=False, seen_doctor="no", injury_age="over_7_days")
    chat, _ = await complaint(make_chat, kb, said, reply)
    assert WHEN in chat.text
    assert "injury_age" not in (await session(chat))["dialog"]["facts"]


async def test_yesterday_does_not_answer_a_day(make_chat, kb: KnowledgeBase) -> None:
    said = "вчера порезал руку ножом, рана глубокая"
    reply = route("cut_sutures", **CUT, injury_age="within_24h")  # DeepSeek guessed
    chat, _ = await complaint(make_chat, kb, said, reply)
    assert WHEN in chat.text and "Менее суток назад" in chat.labels()


async def test_with_no_word_deepseek_decides(make_chat, kb: KnowledgeBase) -> None:
    said = "упал на плечо на прошлых выходных, похоже вывих, к врачу не ходил"
    reply = route("dislocation", assault=False, seen_doctor="no", injury_age="within_7_days")
    chat, _ = await complaint(make_chat, kb, said, reply)
    assert WHEN not in chat.text and "в течение 7 дней" in chat.replies[0].text


async def to_when(make_chat, kb: KnowledgeBase, scenario: str, said: str):
    before = {"assault": False} if scenario == "dislocation" else CUT
    reply = route(scenario, **before)
    chat, fake = await complaint(make_chat, kb, said, reply)
    assert WHEN in chat.text, chat.text
    fake.requests.clear()
    return chat, fake


@pytest.mark.parametrize("said", ["вчера", "Вчера вечером", "сегодня утром", "3 дня назад"])
async def test_the_answer_to_the_question_is_read_without_deepseek(
    make_chat, kb: KnowledgeBase, said: str
) -> None:
    chat, fake = await to_when(make_chat, kb, "dislocation", "упал на плечо, похоже вывих")
    await chat.say(said)
    assert fake.requests == [] and WHEN not in chat.text
    assert (await session(chat))["dialog"]["facts"]["injury_age"] == "within_7_days"


async def test_an_answer_that_does_not_say_is_asked_again(make_chat, kb: KnowledgeBase) -> None:
    chat, fake = await to_when(make_chat, kb, "cut_sutures", "порезал руку ножом, рана глубокая")
    await chat.say("вчера")  # less or more than a day? The buttons answer
    assert fake.requests == [] and WHEN in chat.text
    assert "injury_age" not in (await session(chat))["dialog"]["facts"]
    await chat.say("позавчера")
    assert fake.requests == [] and WHEN not in chat.text
    assert (await session(chat))["dialog"]["facts"]["injury_age"] == "over_24h"


async def test_what_is_said_about_both_children_is_read_by_the_code(
    make_chat, kb: KnowledgeBase
) -> None:
    """The live run: «Дети сегодня с горки упали» — DeepSeek did not take «сегодня»."""

    def no_when(asked: dict) -> dict:
        facts = {k: v for k, v in ANSWERED["facts"].items() if k in asked["fields"]}
        facts.pop("injury_age", None)
        return {**ANSWERED, "facts": facts, "unclear": not facts}

    fake = ByTask(route=UNCLEAR, extract=no_when)
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say(
        FIRST.replace("Мы сегодня с горки упали, двое детей", "Дети сегодня с горки упали")
    )
    await chat.press("Машу, 7 лет")
    await chat.press("Не знаю")
    asked = [s["question"] for s in map(sent, fake.requests) if s["task"] == "extract"]
    assert asked == ["how", "seen_doctor"]  # «Когда?» is read by the code: no call for it
    assert chat.replies[0].text.startswith("Ваши ответы: ") and WHEN not in chat.text
    assert "в течение 7 дней" in chat.replies[0].text


def test_the_prompt_has_an_example_with_today() -> None:
    from app.llm.assistant import load_prompt  # the extract task: «сегодня» is within 7 days

    prompt = load_prompt("dialog.md")
    example = next(
        line for line in prompt.splitlines() if '"task": "extract"' in line and "сегодня" in line
    )
    assert json.loads(example.removeprefix("Сообщение: "))["fields"] == ["injury_age"]
