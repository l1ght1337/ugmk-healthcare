"""«Для кого запись» is remembered for the whole conversation (14.11.2026).

Runs of the simulation (tick, certificate, needle, infant): the patient said who the visit
is for — in the first message, before a route, on the «Для кого запись?» step of a booking
left for the menu — and the next booking asked again. Now the answer, the child's age and
name live in ``Session.talk.who``: the question is not asked, the line «Запись для себя» /
«Запись для ребёнка, 4 года» stands over the step with the button «Изменить». The memory
is dropped when another person is named, on «Изменить», when the booking is made and with
a new conversation.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

import pytest

from app.bot import texts
from app.flow.loader import KnowledgeBase
from tests.fake_deepseek import FakeDeepSeek
from tests.test_conversation import book_dislocation
from tests.test_sim_findings_2 import session

FIRST_QUESTION = "Как получена травма?"
SELF = "Запись для себя"


def complaint(scenario: str, area: str = "limb") -> dict:
    return {"type": "complaint", "area": area, "scenario": scenario, "candidates": [scenario],
            "facts": {}, "clarify": None}  # fmt: skip


async def to_dislocation(chat) -> None:
    """«Записаться» → «Рука или нога» → «Похоже на вывих»: the first screen of the scenario."""
    await chat.press(texts.MENU_BOOK)
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")


async def who(chat) -> dict | None:
    return (await session(chat))["talk"]["who"]


# --- where it may be said -------------------------------------------------------------------


async def test_said_in_a_question_before_the_booking(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek({"type": "clinic_question"}, {"found": False})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("Мне нужна справка в бассейн после старой травмы ноги. Помогут ли у вас?")
    # «справка»: a topic with no data (config/clinic.yaml) — the line of the code
    assert chat.text == "О справках в моих данных ничего нет."
    assert (await who(chat))["audience"] == "self"
    await chat.press(texts.MENU_HOME)
    await to_dislocation(chat)
    screen = chat.replies[-1].text
    assert texts.ASK_AUDIENCE not in screen and FIRST_QUESTION in screen
    assert SELF in screen and texts.WHO_EDIT in chat.labels()


async def test_said_before_a_route(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek(complaint("head_trauma", "head"))
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("я ударился головой о полку, тошнит")
    assert "Старых Большевиков" in chat.text  # the route for an adult: not asked «для кого»
    await chat.press(texts.MENU_BOOK)
    await chat.press(texts.OTHER_INJURY)  # after a route: «та же травма или другая?» first
    for label in ("Рука или нога", "Похоже на вывих"):
        await chat.press(label)
    screen = chat.replies[-1].text
    assert texts.ASK_AUDIENCE not in screen and FIRST_QUESTION in screen and SELF in screen


async def test_answered_by_the_button_in_a_booking_left_for_the_menu(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await to_dislocation(chat)
    await chat.press(texts.AUDIENCE_CHILD)
    await chat.say("5 лет")
    assert FIRST_QUESTION in chat.text
    assert await who(chat) == {"audience": "child", "age_months": 60, "name": None}
    await chat.press(texts.MENU)
    await chat.press(texts.MENU_BOOK)
    await chat.press("Рана, ожог, укус")
    await chat.press("Ожог")
    screen = chat.replies[-1].text
    assert texts.ASK_AUDIENCE not in screen and "Сколько лет" not in screen
    assert "Запись для ребёнка, 5 лет" in screen and texts.WHO_EDIT in chat.labels()


async def test_a_child_confirmed_from_the_text(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek(complaint("dislocation"))
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("Ребёнку 4 года, упал с кровати, плечо вылетело")
    await chat.press("Да, ребёнок 4 года")
    assert FIRST_QUESTION in chat.text and (await who(chat))["age_months"] == 48
    await chat.press(texts.MENU)
    await chat.press(texts.MENU_BOOK)
    await chat.press("Рана, ожог, укус")
    await chat.press("Ожог")
    screen = chat.replies[-1].text
    assert "Правильно понимаю" not in screen and "Запись для ребёнка, 4 года" in screen


async def test_chosen_on_who_is_first(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("Маша 7 лет руку ушибла, Ваня 10 лет ногу подвернул")
    await chat.press("Ваню, 10 лет")
    assert await who(chat) == {"audience": "child", "age_months": 120, "name": "Ваня"}


# --- the line and «Изменить» -------------------------------------------------------------------


async def test_the_line_stays_up_to_the_scenario_questions(make_chat, kb: KnowledgeBase) -> None:
    chat = make_chat(assistant=FakeDeepSeek(complaint("dislocation")).assistant(kb))
    await chat.start()
    await chat.say("я упал, плечо вылетело")
    assert SELF in chat.text and texts.WHO_EDIT in chat.labels()  # step 3, the first question
    await chat.press("Бытовая травма")
    assert SELF in chat.text and texts.WHO_EDIT in chat.labels()
    await chat.press("В течение 7 дней")
    await chat.press("Нет, не обращались")
    assert "Шаг 4 из 5" in chat.text
    assert SELF not in chat.replies[-1].text and texts.WHO_EDIT not in chat.labels()


async def test_edit_asks_for_whom_and_forgets(make_chat, kb: KnowledgeBase) -> None:
    chat = make_chat(assistant=FakeDeepSeek(complaint("dislocation")).assistant(kb))
    await chat.start()
    await chat.say("я упал, плечо вылетело")
    await chat.press(texts.WHO_EDIT)
    assert texts.ASK_AUDIENCE in chat.text and SELF not in chat.text
    assert await who(chat) is None
    await chat.press(texts.AUDIENCE_CHILD)
    await chat.say("9 лет")
    assert (await who(chat))["age_months"] == 108 and FIRST_QUESTION in chat.text
    assert "Запись для ребёнка" not in chat.text  # answered here: the line is not needed


async def test_edit_before_the_topic_is_chosen(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("запишите меня")
    assert SELF in chat.text and texts.WHO_EDIT in chat.labels()
    await chat.press(texts.WHO_EDIT)
    assert SELF not in chat.text and texts.ASK_COMPLAINT in chat.text
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")
    assert texts.ASK_AUDIENCE in chat.text


# --- when it is dropped ---------------------------------------------------------------------


async def test_another_person_named_replaces_it(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek(complaint("dislocation"), complaint("fracture"))
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("я упал, плечо вылетело")
    await chat.press(texts.MENU)
    await chat.say("запишите сына, он руку сломал")
    assert (await who(chat))["audience"] == "child" and SELF not in chat.text
    assert "Сколько лет ребёнку" in chat.text


async def test_a_booking_made_drops_it(make_chat) -> None:
    chat = make_chat()
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    assert await who(chat) is None
    await to_dislocation(chat)
    assert texts.ASK_AUDIENCE in chat.text


async def test_a_new_conversation_drops_it(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await to_dislocation(chat)
    await chat.press(texts.AUDIENCE_SELF)
    assert (await who(chat))["audience"] == "self"
    await chat.start()
    assert await who(chat) is None
    await to_dislocation(chat)
    assert texts.ASK_AUDIENCE in chat.text


@pytest.mark.parametrize("label", [texts.BACK, texts.MENU])
async def test_back_and_menu_keep_the_memory_of_the_talk(make_chat, label: str) -> None:
    chat = make_chat()
    await chat.start()
    await to_dislocation(chat)
    await chat.press(texts.AUDIENCE_SELF)
    await chat.press("Бытовая травма")
    await chat.press(label)  # an earlier screen, or the menu: the answer is not forgotten
    assert (await who(chat))["audience"] == "self"


async def test_a_patient_named_by_name_is_another_person(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek(complaint("dislocation"))
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await to_dislocation(chat)
    await chat.press(texts.AUDIENCE_SELF)
    await chat.press(texts.MENU)
    await chat.say("запишите на завтра Кузнецову Марию, вывих")
    assert await who(chat) is None  # «Кузнецову Марию»: not the one who writes
    assert texts.ASK_AUDIENCE in chat.text and SELF not in chat.text


async def test_a_route_is_not_given_by_the_memory_alone(make_chat, kb: KnowledgeBase) -> None:
    """Other hospitals for a child and for an adult: with no screen to show «Запись для
    себя» on, the bot asks."""
    fake = FakeDeepSeek(complaint("head_trauma", "head"))
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await to_dislocation(chat)
    await chat.press(texts.AUDIENCE_SELF)
    await chat.press(texts.MENU)
    await chat.say("ударился головой о полку, тошнит")
    assert texts.ASK_AUDIENCE in chat.text and "Старых Большевиков" not in chat.text
