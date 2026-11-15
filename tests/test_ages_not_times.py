"""An age is a number with «лет», «год», «года» or «мес…» — never a time.

From the final live run of couple (15.11): «меня на 18:00, мужа на 19:00» — the bot showed
the buttons «Мужа, 14 лет» and then «Мужа, 19 лет»: any number next to a person was read as
an age. Now a number before a colon, «часов», «утра», «вечера» is not one, and a bare number
is not one either: «Маше 7 лет, а Ване 10» names Ваня, his age is asked.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

import pytest

from app.bot import texts
from app.flow.people import find_people
from tests import intent_cases as m
from tests.test_intents import session, to_step


def told(text: str) -> list[tuple[str, str | None, int | None]]:
    return [(p.name, p.audience, p.age_months) for p in find_people(text)]


@pytest.mark.parametrize(
    ("said", "people"),
    [
        # the live run, word for word
        ("Нам надо сегодня вечером, после похода: меня на 18:00, мужа на 19:00.",
         [("", "self", None), ("муж", "adult", None)]),
        ("Вы только что подтвердили: я на 13:30, муж на 14:30", [("муж", "adult", None)]),
        ("мужа на 14:30", [("муж", "adult", None)]),
        ("сына в 9 утра привезу", [("сын", "child", None)]),
        ("дочку к 7 вечера", [("дочка", "child", None)]),
        ("сына через 2 часа привезём", [("сын", "child", None)]),
        ("мужа на 12 ноября", [("муж", "adult", None)]),
        ("Ваню на 14:30 запишите", []),  # a name with a time: neither an age, nor an injury
        ("к Андрею на 12", []),
        # an age has its word
        ("сыну 12 лет", [("сын", "child", 144)]),
        ("дочке 3 года", [("дочка", "child", 36)]),
        ("ребёнку 5 месяцев", [("ребёнок", "child", 5)]),
        ("внуку 4 мес", [("внук", "child", 4)]),
        ("мужу 45 лет", [("муж", "adult", 540)]),
        ("сына, 12 лет, на 14:30", [("сын", "child", 144)]),
        # a bare number is not one: the child is named, the age is asked
        ("Маше 7 лет, а Ване 10", [("Маша", "child", 84), ("Ваня", None, None)]),
        ("сын, ему 10, упал", [("сын", "child", None)]),
        ("Маша, 7, руку ушибла", [("Маша", None, None)]),
    ],
)  # fmt: skip
def test_a_time_is_not_an_age(said: str, people: list) -> None:
    assert told(said) == people


async def test_the_button_has_no_age_from_a_time(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("Мы с мужем подвернули ноги. Меня запишите на 18:00, мужа на 19:00")
    assert chat.labels()[:2] == ["Меня", "Мужа"]
    assert all(p["age_months"] is None for p in (await session(chat))["people"])


async def test_a_bare_number_names_the_child_and_his_age_is_asked(make_chat) -> None:
    chat = make_chat()
    await to_step(chat, m.AGE)
    await chat.say("Маше 7 лет, а Ване 10. Мне обоих надо записать")
    assert chat.replies[0].text == f"{texts.ONE_BY_ONE} {texts.WHO_FIRST}"
    assert chat.labels()[:2] == ["Машу, 7 лет", "Ваню"]
    await chat.press("Ваню")  # «10» with no word: who he is and how old is asked
    assert chat.text.endswith(texts.ASK_AUDIENCE) and "📝 Записываю Ваню" in chat.text
    await chat.press(texts.AUDIENCE_CHILD)
    assert chat.text.endswith(texts.ASK_CHILD_AGE)
