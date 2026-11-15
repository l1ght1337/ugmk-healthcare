"""The style of the bot's messages (15.11.2026): one bold line, marks, quotes, the greeting.

- A screen has one bold line: what the patient is to answer or to read first.
- An emoji is a label at the start of a line, of one set (texts.MARK_*).
- An example of what to type is italic; a service note is a quote.
- A line about 103 and 112 stands alone, bold, behind ⚠️.
- What came from the patient is shown as it was typed.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

from datetime import timedelta

from app.bot import texts
from app.bot.conversation import WELCOME, Reply
from tests.test_sim_findings_2 import MENU

GREETING = (
    "Добрый день! Травмпункт «УГМК-Здоровье», я виртуальный помощник. Чем я могу Вам помочь?\n\n"
    "📝 Запишу в травмпункт за минуту — 5 коротких шагов.\n"
    "💬 Отвечу на вопросы о травмпункте: адрес, цены, парковка, документы. Если ответа у меня "
    "нет — передам вопрос администратору.\n\n"
    "Напишите своими словами, например подвернул ногу или запишите к Ярунину на завтра, — или "
    "нажмите кнопку.\n\n"
    "Тестовый режим: записи демонстрационные."
)


def marked(reply: Reply) -> list[tuple[str, str]]:
    return [(s.kind, reply.text[s.start : s.end]) for s in reply.spans]


# --- the greeting ----------------------------------------------------------------------------


async def test_the_greeting(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    (hello,) = chat.replies
    assert hello.text == GREETING
    assert marked(hello) == [
        ("bold", "Добрый день!"),
        ("bold", "Запишу в травмпункт за минуту"),
        ("bold", "Отвечу на вопросы о травмпункте"),
        ("italic", "подвернул ногу"),
        ("italic", "запишите к Ярунину на завтра"),
        ("quote", texts.BOT_DEMO_NOTE),
    ]
    assert chat.labels() == MENU  # the examples are in the text now, not buttons
    assert len(hello.text) < 1024  # fits the caption of the animation


async def test_an_example_button_of_an_old_greeting_still_works(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.data("ex:ankle")
    assert "На что больше похоже?" in chat.text


async def test_the_animation_at_a_start_but_once_a_day(make_chat, clock) -> None:
    chat = make_chat()
    await chat.start()
    assert chat.replies[0].media == WELCOME
    await chat.start()  # the same day: the text alone
    assert chat.replies[0].media is None and chat.replies[0].text == GREETING
    clock.now += timedelta(hours=23, minutes=59)
    await chat.start()
    assert chat.replies[0].media is None
    clock.now += timedelta(minutes=2)  # a day has passed since the animation
    await chat.start()
    assert chat.replies[0].media == WELCOME
