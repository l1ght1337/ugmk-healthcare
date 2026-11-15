"""«Спасибо, оба записаны, до свидания» is a goodbye, however many words it has.

From the live runs of two_kids, self_and_son and couple (15.11): after both bookings the
patient wrote «Нет, спасибо. Оба записаны, до свидания» and got «Я помогаю только с записью в
травмпункт…»; «Спасибо большое, обоих записали. Больше ничего не нужно» got «Передать Ваше
сообщение администратору?». The word «записаны» was read as a request to book.

The rule: the message thanks or says goodbye, and has no question, no complaint and no
command — it is small talk: a short reply and the menu. «Оба записаны», «обоих записали»,
«обе записи есть» say that it is done, not ask for it.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

import pytest

from app.bot import texts
from app.flow.commands import find_small_talk, says_goodbye
from app.flow.loader import KnowledgeBase
from tests.fake_deepseek import FakeDeepSeek
from tests.test_conversation import book_dislocation

LIVE = [  # the last messages of the patients in the three runs, word for word
    ("Нет, спасибо. Оба записаны, до свидания", "bye"),
    ("Нет, спасибо, оба записаны. До свидания.", "bye"),
    ("Спасибо, оба записаны, больше ничего не надо. До свидания!", "bye"),
    ("Спасибо большое, больше ничего не нужно. Обе записи есть, до свидания.", "bye"),
    ("Спасибо большое, обоих записали", "thanks"),
    ("Спасибо большое, обоих записали. Больше ничего не нужно", "thanks"),
    ("Спасибо большое, теперь оба записаны.", "thanks"),
]
NEGATED = [  # a command with «не» before or after it is not a command (the final run of couple)
    ("Нет, больше никого записывать не надо. Спасибо большое, оба записаны, до свидания.", "bye"),
    ("Больше никого записывать не надо. Спасибо, до свидания", "bye"),
    ("Нет, не надо ничего передавать, всё хорошо. До свидания!", "bye"),
    ("Спасибо, отменять ничего не нужно", "thanks"),
    ("Спасибо, администратора звать не надо", "thanks"),
    ("Спасибо, переносить не буду, до свидания", "bye"),
]
LONG = (
    "Ну всё, спасибо Вам огромное, очень выручили, а то мы с утра не знали, куда бежать и что "
    "вообще делать в такой ситуации, теперь хоть понятно, во сколько и куда приходить и что с "
    "собой взять, всего Вам доброго"
)
NOT_A_GOODBYE = [
    "Спасибо, а когда позвонят?",  # a question
    "Спасибо, оба записаны. А парковка у вас есть",  # a question without the mark
    "Спасибо, до свидания. Хотя нет, я ещё хотела узнать про парковку возле травмпункта",
    "спасибо, запишите ещё сына",  # a command
    "Спасибо, покажите мои записи",
    "Спасибо, записали. А перенести на завтра можно",
    "Спасибо, но нога всё равно болит очень сильно",  # a complaint
    "Спасибо, сын ещё упал вчера, рука опухла",
    "Да не надо мне новую запись оформлять, я уже записан! Всё, спасибо, до свидания.",
    "спасибо доктору Кузнецову, всё прошло отлично",  # a word about the visit: to the admins
    "Спасибо, отменять не надо, лучше перенесите на завтра",  # one is refused, one is asked
    "Спасибо, не надо отменять. Запишите ещё сына",
]


def read(said: str) -> str | None:
    """As the bot reads it: the small talk first, the rule of 15.11 before DeepSeek."""
    return find_small_talk(said) or says_goodbye(said)


@pytest.mark.parametrize(("said", "kind"), [*LIVE, *NEGATED, (LONG, "bye")])
def test_thanks_and_a_goodbye_with_nothing_asked(said: str, kind: str) -> None:
    assert read(said) == kind


@pytest.mark.parametrize("said", NOT_A_GOODBYE)
def test_a_question_a_complaint_or_a_command_is_not_a_goodbye(said: str) -> None:
    assert read(said) is None


async def test_thanks_on_a_question_of_the_scenario_is_its_answer(make_chat) -> None:
    """The rule is for a text that would go to DeepSeek as a free one: on a question of a
    scenario «спасибо, вчера» is the answer."""
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")
    await chat.press("Для себя")
    await chat.press("Бытовая травма")
    await chat.say("Спасибо большое, вчера")
    assert "Обращались ли к врачу" in chat.text


@pytest.mark.parametrize(("said", "kind"), [*LIVE, *NEGATED[:3]])
async def test_after_the_booking_the_bot_says_goodbye(
    make_chat, kb: KnowledgeBase, said: str, kind: str
) -> None:
    fake = FakeDeepSeek({"type": "feedback"})
    chat = make_chat(assistant=fake.assistant(kb))
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    await chat.say(said)
    (reply,) = chat.replies  # a short reply and the menu
    assert reply.text == (kb.common.goodbye.text if kind == "bye" else texts.WELCOME)
    assert chat.labels()[-4:] == [texts.MENU_BOOK, texts.MENU_ASK, texts.MENU_MY, texts.MENU_ADMIN]
    assert fake.requests == []  # DeepSeek is not asked whether it is feedback
    assert texts.OFFTOPIC not in chat.text and texts.FEEDBACK not in chat.text
