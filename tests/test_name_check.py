"""Two words on the name step are not always a name.

The bot used to take any two or three words as «фамилия имя»: «не понимаю» became the patient
«Не Понимаю». The list of first names is not made stricter — real rare names would be lost.
Instead: words another reader of the bot understands (a command, a wish for a day or a doctor,
a complaint, «да» / «нет», a question) are not a name; and a name with no word of the list and
no patronymic is asked about: «Пациент: Ли Вэй — верно?».

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

import pytest

from app.bot import texts
from app.flow.identity import name_is_known
from tests import intent_cases as m
from tests.test_intents import session, to_step
from tests.test_safeguards import patients


@pytest.mark.parametrize(
    "said",
    [
        "болит нога",  # a complaint
        "сломал руку",
        "завтра утром",  # a wish for a day and a time
        "в субботу",
        "к Ярунину",  # a wish for a doctor
        "не знаю",
        "не понимаю",
        "что это?",  # a question
        "зачем вам имя?",
        "что делать",  # a request for advice
    ],
)
async def test_words_another_reader_understands_are_not_a_name(make_chat, said: str) -> None:
    chat = make_chat()
    await to_step(chat, m.NAME)
    await chat.say(said)
    state = await session(chat)
    assert state["step"] == "name" and state["draft_name"] is None, chat.text
    assert texts.NAME_CONFIRM.split("{")[0] not in chat.text and texts.ASK_BIRTH not in chat.text


async def test_words_nobody_understands_are_asked_about_too(make_chat) -> None:
    """Not a name, but no reader knows it either: the question catches it, nothing is saved."""
    chat = make_chat()
    await to_step(chat, m.NAME)
    await chat.say("мне шестнадцать")
    assert chat.text.endswith(texts.NAME_CONFIRM.format(name="Мне Шестнадцать"))
    await chat.press(texts.NAME_AGAIN)
    assert (await session(chat))["step"] == "name" and await patients(chat) == []


@pytest.mark.parametrize("said", ["Ли Вэй", "Ким Чен Ын", "Оглоблин Харитон", "нгуен ван"])
async def test_a_name_with_no_known_word_is_asked_about(make_chat, said: str) -> None:
    chat = make_chat()
    await to_step(chat, m.NAME)
    await chat.say(said)
    shown = " ".join(word.capitalize() for word in said.split())
    assert chat.text.endswith(texts.NAME_CONFIRM.format(name=shown))
    assert chat.labels()[:2] == [texts.NAME_YES, texts.NAME_AGAIN]
    assert (await session(chat))["step"] == "name_ok" and await patients(chat) == []
    await chat.press(texts.NAME_YES)
    assert texts.PATIENT_IS.format(name=shown) in chat.text and texts.ASK_BIRTH in chat.text
    await chat.say("05.05.1990")
    (patient,) = await patients(chat)
    assert patient.full_name == shown


async def test_enter_again_asks_for_the_name_once_more(make_chat) -> None:
    chat = make_chat()
    await to_step(chat, m.NAME)
    await chat.say("Ли Вэй")
    await chat.press(texts.NAME_AGAIN)
    state = await session(chat)
    assert texts.ASK_NAME in chat.text and (state["step"], state["draft_name"]) == ("name", None)


@pytest.mark.parametrize(
    ("said", "then"),
    [
        ("да", "birth"),
        ("верно", "birth"),
        ("Иванов Иван Иванович", "birth"),
        ("ну и ну", "name_ok"),
    ],
)
async def test_a_text_on_the_question_about_the_name(make_chat, said: str, then: str) -> None:
    chat = make_chat()
    await to_step(chat, m.NAME)
    await chat.say("Ли Вэй")
    await chat.say(said)
    assert (await session(chat))["step"] == then
    if said.startswith("Иванов"):  # another name typed: it replaces the one asked about
        assert texts.PATIENT_IS.format(name="Иванов Иван Иванович") in chat.text


@pytest.mark.parametrize(
    "said",
    ["Иванов Иван", "Мария Петрова", "петров иван сергеевич", "Хасанова Эльвира Наилевна",
     "Мамедов Али Вели оглы"],
)  # fmt: skip
async def test_a_known_name_is_taken_as_before(make_chat, said: str) -> None:
    chat = make_chat()
    await to_step(chat, m.NAME)
    await chat.say(said)
    assert (await session(chat))["step"] == "birth" and texts.ASK_BIRTH in chat.text


@pytest.mark.parametrize(
    ("name", "known"),
    [
        (("Иванов", "Иван", None), True),
        (("Мария", "Петрова", None), True),
        (("Хасанова", "Эльвира", "Наилевна"), True),
        (("Мамедов", "Али", "Вели оглы"), True),
        (("Ли", "Вэй", None), False),
        (("Ким", "Чен", "Ын"), False),  # three words, the third is not a patronymic
        (("Что", "Вам", "Надо"), False),
    ],
)
def test_which_names_are_known(name: tuple, known: bool) -> None:
    assert name_is_known(name) is known
