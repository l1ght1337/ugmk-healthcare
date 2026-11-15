"""A full name is not one more patient, and nobody is read on the steps of the data.

From the final live run of couple (15.11): «И мужа, Захарова Игоря Семёновича, пятого мая 1977
года рождения, тоже на эти же дни запишите» — the first name inside the husband's full name
became a third patient: «Меня», «Мужа», «Игоря». A first name next to a surname or a
patronymic is somebody's full name, not a new person; and on the steps where the patient
types a full name, a birth date or a phone the people readers do not run at all.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

import pytest

from app.bot import texts
from app.flow.people import asked_too, find_people
from tests.test_intents import session
from tests.test_two_named import KIDS

LIVE = [  # the messages of the run, word for word
    "Самостоятельно. Только вот незадача — вы записали меня на четверг, 12 ноября, а нам нужно "
    "сегодня или завтра, мы же ноги подвернули. И мужа моего, Захарова Игоря Семёновича, тоже "
    "надо записать.",
    "Да я уже записана, только не на тот день! Вы меня записали на четверг, 12 ноября, а нам "
    "нужно сегодня или завтра — мы же только что ноги подвернули в походе. Неужели нельзя на "
    "сегодня-завтра? И мужа, Захарова Игоря Семёновича, пятого мая 1977 года рождения, тоже на "
    "эти же дни запишите, пожалуйста.",
]


@pytest.mark.parametrize("said", LIVE)
def test_the_name_in_a_full_name_is_not_one_more_patient(said: str) -> None:
    assert [p.name for p in asked_too(said)] == ["муж"]
    assert "Игорь" not in [p.name for p in find_people(said)]


def test_a_first_name_alone_is_still_asked_for() -> None:
    assert [p.name for p in asked_too("и Ваню тоже запишите")] == ["Ваня"]
    assert [p.name for p in asked_too("Запишите ещё Ваню, ему 10 лет, ногу подвернул")] == ["Ваня"]
    assert asked_too("и Ярунина Андрея Александровича тоже запишите") == []


async def test_a_full_name_on_who_is_first_adds_nobody(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("Мы с мужем подвернули ноги, запишите обоих")
    assert chat.labels()[:2] == ["Меня", "Мужа"]
    await chat.say(LIVE[1])
    assert [p["name"] for p in (await session(chat))["people"]] == ["", "муж"]
    assert "Игоря" not in chat.labels()


async def to_name_step(chat) -> None:
    """Two children named, the first is being booked: the step «Начнём с ФИО»."""
    await chat.start()
    await chat.say(KIDS)
    await chat.press("Машу, 7 лет")
    await chat.press("Похоже на вывих")
    for label in ("Бытовая травма", "Нет, не обращались"):
        await chat.press(label)
    await chat.pick_slot("Завтра")
    await chat.press(texts.CONSENT_YES)
    assert (await session(chat))["step"] == "name"


@pytest.mark.parametrize(
    "said",
    [
        "Воронина Мария Кирилловна, и мужа, Воронина Кирилла Ивановича, тоже запишите",
        "и мужа тоже запишите",
        "Воронина Мария Кирилловна, 7 лет, а Ване 10 лет, оба упали",
    ],
)
async def test_nobody_is_read_on_the_name_step(make_chat, said: str) -> None:
    chat = make_chat()
    await to_name_step(chat)
    await chat.say(said)
    state = await session(chat)
    assert [p["name"] for p in state["people"]] == ["Ваня"]  # as it was: nobody is added
    assert "запишу следующим" not in chat.text and texts.ONE_BY_ONE not in chat.text
    assert texts.WHO_FIRST not in chat.text and not state["more"]


async def test_nobody_is_read_on_the_birth_date_and_the_phone(make_chat) -> None:
    chat = make_chat()
    await to_name_step(chat)
    await chat.say("Воронина Мария Кирилловна")
    assert (await session(chat))["step"] == "birth"
    await chat.say("03.03.2019, и мужа, Воронина Кирилла Ивановича, тоже запишите")
    state = await session(chat)
    assert [p["name"] for p in state["people"]] == ["Ваня"] and "запишу следующим" not in chat.text
    if state["step"] == "birth":
        await chat.say("03.03.2019")
    assert (await session(chat))["step"] == "contact"
    await chat.say("+79123456789, и ещё мужа запишите, мы оба упали")
    state = await session(chat)
    assert [p["name"] for p in state["people"]] == ["Ваня"]
    assert "запишу следующим" not in chat.text and texts.ONE_BY_ONE not in chat.text
