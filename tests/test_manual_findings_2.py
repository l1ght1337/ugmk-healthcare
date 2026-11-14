"""The dialogs from the customer's check of 453f8ef (14.11): items 1, 2, 3, 7 and 8.

The clinic clock is Thursday, 12 November 2026, 12:00, unless a test sets another one.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.bot import texts
from app.bot.conversation import UNFINISHED_WINDOW
from app.db.models import User
from app.flow.loader import KnowledgeBase
from tests.chat_harness import Chat
from tests.conftest import NOW
from tests.fake_deepseek import FakeDeepSeek
from tests.test_wish_flow import through_dislocation

BITE_NOTICE = (
    "Антирабической вакцины нет — доктор может обработать место укуса и сделать перевязку."
)
BITE_ANSWER = f"{BITE_NOTICE}\n\n{texts.MEDICAL_ASIDE}"
DANGER_SIGNS = "присутствуют какие-либо из признаков"
BITE = {"type": "complaint", "area": None, "scenario": "animal_bite"}
FALL = "На что больше похоже?"
TRUNK = "Шея, спина, грудь, живот, пах"


async def session(chat: Chat) -> dict:
    async with chat.conversation.db() as s:
        return dict((await s.get(User, chat.user.id)).session or {})


async def to_the_time_after_a_bite(chat: Chat) -> None:
    for label in ("Нет", "Кошка, собака, грызун, птица", "В течение 7 дней", "Нет, не обращались"):
        await chat.press(label)


def bottom(chat: Chat) -> list[str]:
    """The labels of the last row of the last message."""
    return [b.label for b in chat.replies[-1].buttons[-1]]


# --- 1. a medical question inside a complaint ----------------------------------------------


@pytest.mark.parametrize("flag", [True, False], ids=["route: advice", "the word list"])
async def test_what_to_do_with_a_bite_is_answered_before_the_first_question(
    make_chat, kb: KnowledgeBase, flag: bool
) -> None:
    fake = FakeDeepSeek({**BITE, "advice": flag})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("что делать укусила собака меня")
    answer, screen = chat.replies
    assert answer.text == BITE_ANSWER and not answer.buttons  # one message, from the data only
    assert DANGER_SIGNS in screen.text and texts.ASK_AUDIENCE not in chat.text  # «меня»
    assert (await session(chat))["dialog"]["scenario_id"] == "animal_bite"


async def test_the_script_notice_is_not_said_twice(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek({**BITE, "advice": True})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("что делать укусила собака меня")
    await to_the_time_after_a_bite(chat)
    assert chat.days() and "; для себя; " in chat.text
    assert BITE_NOTICE not in chat.text


async def test_without_the_question_the_notice_stays_where_the_script_has_it(make_chat, kb) -> None:
    fake = FakeDeepSeek(BITE)
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("меня укусила собака")
    (screen,) = chat.replies  # no question in the text: nothing before the first question
    assert DANGER_SIGNS in screen.text
    await to_the_time_after_a_bite(chat)
    assert chat.days() and BITE_NOTICE in chat.text


async def test_injury_without_a_phrase_in_the_data_gets_the_general_line_only(
    make_chat, kb
) -> None:
    fake = FakeDeepSeek({"type": "complaint", "area": "limb", "scenario": "fracture"})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("я сломал руку, что теперь делать?")
    answer, screen = chat.replies
    assert answer.text == texts.MEDICAL_ASIDE  # nothing of the bot's own is added
    assert (await session(chat))["dialog"]["scenario_id"] == "fracture"
    assert texts.ASK_AUDIENCE not in screen.text


async def test_what_to_do_without_a_clear_topic_goes_to_the_topics(make_chat, kb) -> None:
    fake = FakeDeepSeek(
        {
            "type": "complaint",
            "area": "limb",
            "scenario": None,
            "candidates": ["fracture", "dislocation", "ligament_rupture"],
            "clarify": "Подскажите, пожалуйста, на что больше похоже?",
            "advice": True,
        }
    )
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("Упал на руку, она опухла — это перелом? Что приложить?")
    answer, screen = chat.replies
    assert answer.text == texts.MEDICAL_ASIDE and FALL in screen.text
    assert (await session(chat))["wizard"]


async def test_medical_question_without_an_injury_is_as_before(
    make_chat, kb: KnowledgeBase
) -> None:
    fake = FakeDeepSeek({"type": "medical_question"})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("какое обезболивающее лучше выпить?")
    assert chat.text == texts.MEDICAL and chat.labels() == [texts.MENU_BOOK, texts.MENU_HOME]
    await chat.press(texts.MENU_BOOK)
    assert texts.ASK_COMPLAINT in chat.text and (await session(chat))["step"] == "complaint"


@pytest.mark.parametrize(
    ("text", "scenario", "screen"),
    [
        ("что делать укусила собака", "animal_bite", texts.ASK_AUDIENCE),
        ("упал на руку, опухла — это перелом?", None, FALL),
        ("болит спина, чем мазать?", None, "Что случилось с шеей или спиной?"),
    ],
)
async def test_book_button_under_the_medical_answer_keeps_the_injury(
    make_chat, kb: KnowledgeBase, text: str, scenario: str | None, screen: str
) -> None:
    """DeepSeek said «medical_question» all the same: the injury in the text is not lost."""
    fake = FakeDeepSeek({"type": "medical_question"})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say(text)
    assert chat.text == texts.MEDICAL and chat.labels() == [texts.MENU_BOOK, texts.MENU_HOME]
    await chat.press(texts.MENU_BOOK)
    assert screen in chat.text and texts.ASK_COMPLAINT not in chat.text
    assert (await session(chat))["dialog"]["scenario_id"] == scenario


async def test_medical_question_on_step_1_keeps_the_complaint(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek({"type": "medical_question"})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("запишите на завтра")
    await chat.say("что делать укусила собака меня")
    answer, screen = chat.replies
    assert answer.text == BITE_ANSWER and DANGER_SIGNS in screen.text
    assert "Пожелание: завтра, 13 ноября" in screen.text


async def test_what_to_do_without_deepseek(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("подвернул ногу, что делать?")
    answer, screen = chat.replies
    assert answer.text == texts.MEDICAL_ASIDE and FALL in screen.text


# --- 2. «меня» is a booking for oneself ----------------------------------------------------


async def test_complaint_about_oneself_shows_it_and_does_not_ask(make_chat, kb) -> None:
    fake = FakeDeepSeek({"type": "complaint", "area": "limb", "scenario": None})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("у меня болит рука")
    assert texts.FOR_WHOM["self"] in chat.text  # the header of step 1
    await chat.press("Упал, ударился, подвернул")
    await chat.press("Похоже на вывих")
    assert "Как получена травма" in chat.text and texts.ASK_AUDIENCE not in chat.text
    await chat.press("Бытовая травма")
    await chat.press("В течение 7 дней")
    await chat.press("Нет, не обращались")
    assert "Ваши ответы: вывих; для себя; бытовая травма" in chat.text


async def test_back_from_the_first_question_asks_who_the_visit_is_for(make_chat, kb) -> None:
    fake = FakeDeepSeek(BITE)
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("меня укусила собака")
    assert DANGER_SIGNS in chat.text
    await chat.press(texts.BACK)  # the question the bot skipped: it can be answered otherwise
    assert texts.ASK_AUDIENCE in chat.text and (await session(chat))["for_whom"] is None
    await chat.press(texts.AUDIENCE_CHILD)
    assert texts.ASK_CHILD_AGE in chat.text
    assert (await session(chat))["dialog"]["scenario_id"] == "animal_bite"


async def test_edit_answers_asks_who_the_visit_is_for_again(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("я упал на руку")  # no DeepSeek: the word list → «На что больше похоже?»
    assert texts.FOR_WHOM["self"] in chat.text
    await chat.press("Похоже на вывих")
    await chat.press("Бытовая травма")
    await chat.press("В течение 7 дней")
    await chat.press("Нет, не обращались")
    assert "для себя" in chat.text
    await chat.press(texts.SUMMARY_EDIT)
    assert texts.ASK_AUDIENCE in chat.text
    await chat.press(texts.AUDIENCE_CHILD)
    assert texts.ASK_CHILD_AGE in chat.text


async def test_two_patients_in_the_text_are_asked_about(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek(BITE)
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("меня и сына покусала собака")
    assert texts.ASK_AUDIENCE in chat.text


async def test_the_last_said_wins_and_the_wish_stays(make_chat, kb: KnowledgeBase) -> None:
    """«Запись для ребёнка», the wish «завтра», the trunk topics — then «меня»."""
    fake = FakeDeepSeek(BITE)
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("запишите ребёнка на завтра")
    await chat.press(TRUNK)
    assert texts.FOR_WHOM["child"] in chat.text and "Пожелание: завтра, 13 ноября" in chat.text
    await chat.say("меня укусила собака")
    state = await session(chat)
    assert state["for_whom"] == "self" and state["dialog"]["facts"]["audience"] == "adult"
    assert DANGER_SIGNS in chat.text and texts.ASK_CHILD_AGE not in chat.text
    assert "Пожелание: завтра, 13 ноября" in chat.text and state["wish"]["day"] == "2026-11-13"


async def test_the_last_said_wins_on_the_topics_too(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek({"type": "complaint", "area": "neck_back", "scenario": None})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("запишите ребёнка на завтра")
    await chat.press(TRUNK)
    await chat.say("у меня болит спина")
    assert texts.FOR_WHOM["self"] in chat.text and texts.FOR_WHOM["child"] not in chat.text
    assert "Пожелание: завтра, 13 ноября" in chat.text


# --- 3. «Травма половых органов» in a group that says so --------------------------------------


async def test_trunk_group_names_the_groin(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    assert TRUNK in chat.labels()
    await chat.press(TRUNK)
    assert chat.labels()[-4:-2] == ["Травма половых органов", "Другое"]
    await chat.say("пах")  # a word of the group's label is not a topic of its own
    assert (await session(chat))["dialog"]["scenario_id"] is None


# --- 7. the menu asked for in words; a text on the steps with buttons -----------------------


async def test_menu_in_words_on_the_day_step(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await through_dislocation(chat)
    await chat.say("первоначальное меню открой")
    assert fake.requests == [] and chat.text == kb.common.more_help.text
    assert (await session(chat))["step"] == "idle" and texts.MENU_BOOK in chat.labels()


@pytest.mark.parametrize(
    ("action", "phrase"),
    [("home", "хочу попасть на стартовый экран"), ("back", "верни прошлый вопрос")],
)
async def test_route_understands_home_and_back(make_chat, kb, action: str, phrase: str) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await through_dislocation(chat)
    fake.replies = [{"type": "menu_action", "action": action}]
    await chat.say(phrase)
    assert len(fake.requests) == 1
    if action == "home":
        assert chat.text == kb.common.more_help.text and (await session(chat))["step"] == "idle"
    else:
        assert "Обращались ли к врачу" in chat.text and (await session(chat))["step"] == "question"


BUTTON_STEPS = {
    "день": ((), texts.HINT_TIME),
    "время суток": (("day",), texts.HINT_TIME),
    "согласие": (("day", "time"), texts.HINT_BUTTONS),
}


@pytest.mark.parametrize("step", BUTTON_STEPS)
async def test_text_on_a_step_with_buttons_goes_to_route_first(make_chat, kb, step: str) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await through_dislocation(chat)
    moves, hint = BUTTON_STEPS[step]
    if "day" in moves:
        await chat.pick_day("Завтра")
    if "time" in moves:
        await chat.pick_time()
    before = await session(chat)
    shown = chat.replies[-1]

    fake.replies = [{"type": "clinic_question"}, {"found": True, "answer": "Парковка у входа."}]
    await chat.say("расскажите про парковку")  # not shaped as a question: route decides
    answer, screen = chat.replies
    assert answer.text == "Парковка у входа." and screen.text == shown.text
    assert (await session(chat))["step"] == before["step"]

    fake.replies = [{"type": "offtopic"}]
    await chat.say("ну и погода выдалась")  # route did not help: what can be typed
    line, screen = chat.replies
    assert line.text == hint and screen.text == shown.text and screen.buttons == shown.buttons
    assert "«назад»" in hint and "«меню»" in hint


async def test_hint_without_deepseek(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await through_dislocation(chat)
    await chat.say("ну и погода выдалась")
    line, screen = chat.replies
    assert line.text == texts.HINT_TIME and "«завтра вечером»" in line.text
    assert texts.DAY_PICK in screen.text and chat.days()


# --- 8. «Меню» on every booking screen, «Продолжить запись» in the menu ----------------------


async def test_every_booking_screen_ends_with_back_and_menu(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    assert bottom(chat) == [texts.MENU]  # the first screen: nothing to go back to
    steps = (
        "Рука или нога",
        "Похоже на вывих",
        "Для себя",
        "Бытовая травма",
        "В течение 7 дней",
        "Нет, не обращались",
    )
    for label in steps:
        await chat.press(label)
        assert bottom(chat) == [texts.BACK, texts.MENU], label
    await chat.pick_day("Завтра")
    assert bottom(chat) == [texts.BACK, texts.MENU]
    await chat.pick_time()
    assert bottom(chat) == [texts.BACK, texts.MENU]  # the consent
    await chat.press(texts.CONSENT_YES)
    assert bottom(chat) == [texts.BACK, texts.MENU]  # the name
    await chat.say("петров иван сергеевич")
    assert bottom(chat) == [texts.BACK, texts.MENU]  # the birth date
    await chat.say("12.03.1985")
    assert chat.replies[-1].ask_contact  # the phone: «Назад» and «Меню» are on the keyboard


async def test_moving_a_booking_has_keep_then_menu(make_chat) -> None:
    from tests.test_conversation import book_dislocation

    chat = make_chat()
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    await chat.say("хочу перенести")
    rows = [[b.label for b in row] for row in chat.replies[-1].buttons]
    assert rows[-2:] == [[texts.KEEP_AS_IS], [texts.MENU]]
    await chat.pick_day("Завтра")
    rows = [[b.label for b in row] for row in chat.replies[-1].buttons]
    assert rows[-2:] == [[texts.KEEP_AS_IS], [texts.BACK, texts.MENU]]


async def test_menu_keeps_the_booking_and_resume_returns_to_the_step(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("запишите к Кузнецову")
    await through_dislocation(chat)
    before = await session(chat)
    shown = chat.replies[-1]
    await chat.press(texts.MENU)
    assert chat.labels()[0] == texts.RESUME and chat.labels()[1] == texts.MENU_BOOK
    assert (await session(chat))["step"] == "idle"
    await chat.press(texts.RESUME)
    after = await session(chat)
    assert (after["step"], after["dialog"], after["wish"]) == (
        before["step"],
        before["dialog"],
        before["wish"],
    )
    assert chat.replies[-1].text == shown.text and chat.replies[-1].buttons == shown.buttons
    await chat.press(texts.BACK)  # the history came back too
    assert "Обращались ли к врачу" in chat.text


async def test_resume_stays_after_a_question_and_goes_with_a_new_booking(make_chat, kb) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await through_dislocation(chat)
    await chat.say("меню")  # typed: the same as the button
    assert chat.labels()[0] == texts.RESUME
    fake.replies = [{"type": "clinic_question"}, {"found": True, "answer": "Парковка у входа."}]
    await chat.say("где парковка?")
    await chat.press(texts.MENU_HOME)
    assert chat.labels()[0] == texts.RESUME  # asked a question in between: still there
    await chat.press(texts.MENU_BOOK)  # «Записаться» starts anew
    assert texts.ASK_COMPLAINT in chat.text and (await session(chat))["dialog"]["facts"] == {}
    await chat.press(texts.MENU)
    assert texts.RESUME not in chat.labels()


async def test_resume_is_offered_while_the_booking_is_fresh(make_chat, clock) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await through_dislocation(chat)
    await chat.press(texts.MENU)
    resume = chat.button(texts.RESUME)
    clock.now = NOW + UNFINISHED_WINDOW + timedelta(minutes=1)
    chat._take(await chat.conversation.menu(chat.user))
    assert texts.RESUME not in chat.labels()
    await chat.data(resume.data)  # the button of the old message
    assert texts.RESUME_GONE in chat.text and texts.ASK_COMPLAINT in chat.text


async def test_the_empty_first_screen_is_not_kept(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press(texts.MENU)
    assert texts.RESUME not in chat.labels() and chat.labels()[0] == texts.MENU_BOOK
