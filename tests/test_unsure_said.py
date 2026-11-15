"""«Не знаю, я же не врач», «просто ушиб» on «На что больше похоже?» press «Не знаю».

From the live runs of two_kids, self_and_son and couple (15.11): the patient typed «Не знаю,
просто руку ушибла, плачет», «Не знаю, я же не врач. Просто подвернул ногу и хромает», and the
bot asked DeepSeek, got a question back and showed the same screen — up to four turns for one
patient. The words that press the button stand with the button itself (``said`` of the menu
item in kb/scenarios/_menu.json), whole words, every form written out; the code reads them,
DeepSeek is not asked. A text that names a dislocation, a fracture or the ligaments is not
«не знаю»: it goes on as before.

Since the last round (15.11) the rule is wider: on «На что больше похоже?» any text that
names none of the answers (вывих, перелом, связки, растяжение and their forms: сломал,
вывихнул, порвал, растянул) and asks nothing is the button «Не знаю». «Нога опухла, хромает
сильно» is not a choice between them, and the bot does not read symptoms into one.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

import pytest

from app.bot import texts
from app.flow import topics
from app.flow.loader import KnowledgeBase
from tests.fake_deepseek import FakeDeepSeek
from tests.test_intents import session

UNCLEAR = {
    "type": "complaint",
    "area": "limb",
    "scenario": None,
    "facts": {},
    "candidates": ["dislocation", "fracture", "ligament_rupture"],
    "clarify": "Подскажите, пожалуйста, на что больше похоже: вывих, перелом или растяжение?",
}
SAID = [  # the patients of the live runs, word for word
    "Не знаю, просто руку ушибла, плачет",
    "Не знаю, я же не врач. Просто подвернул ногу и хромает",
    "Ой, я же не врач, откуда мне знать",
    "просто ушиб",
    "Ушиб",
    "Колено ушиб на катке, упал",
    # none of the words: the symptoms are not read into an answer (the final live runs)
    "Ногу подвернул, наступить больно, хромает",
    "Нога опухла и он на неё не встаёт почти, хромает сильно",
    "Сегодня днём с горки упал, подвернул ногу. Нога опухла, хромает сильно",
    "Тоже предполагаю, врач его не смотрел",
]
NOT_SAID = [
    "Просто ушиб, колено болит. Ни вывиха, ни перелома вроде нет.",  # names two of them
    "Не знаю, наверное растяжение",
    "не знаю, может перелом?",
    "ушиб, а что делать до приёма?",  # asks for advice: answered as before
    "Нога опухла немного. Думаю, просто подвернул, ничего не сломано.",  # «сломано» is named
    "Наверное, связки потянул",
    "скорее всего растянул",
    "а это надолго?",  # a question
    "на самом деле это ожог",  # another injury: not one of these four
    "порезал ещё руку стеклом",
    "а ещё голова болит",  # another part of the body
]


async def on_the_question(make_chat, kb: KnowledgeBase):
    fake = FakeDeepSeek(*[UNCLEAR] * 4)
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.say("подвернул ногу на лестнице")  # DeepSeek names three: «На что больше похоже?»
    assert "На что больше похоже?" in chat.text and "Не знаю" in chat.labels()
    fake.requests.clear()
    return chat, fake


@pytest.mark.parametrize("said", SAID)
async def test_the_words_press_the_button(make_chat, kb: KnowledgeBase, said: str) -> None:
    chat, fake = await on_the_question(make_chat, kb)
    await chat.say(said)
    state = await session(chat)
    # read by the code: DeepSeek is not asked what the words mean (only, as for any
    # complaint that is kept, which questions of the scenario «подвернул ногу» answers)
    assert [r for r in fake.requests if '"task": "route"' in r["messages"][1]["content"]] == []
    assert state["dialog"]["scenario_id"] == "dislocation" and state["picked"] == "unsure"
    assert "На что больше похоже?" not in chat.text
    await chat.press(texts.AUDIENCE_SELF)
    for label in ("Бытовая травма", "В течение 7 дней", "Нет, не обращались"):
        if label in chat.labels():  # «сегодня» in the text that pressed the button is read
            await chat.press(label)
    assert chat.replies[0].text.startswith(f"Ваши ответы: {texts.SUMMARY_UNSURE}; для себя")


@pytest.mark.parametrize("said", NOT_SAID)
async def test_a_named_injury_or_a_question_is_not_unsure(
    make_chat, kb: KnowledgeBase, said: str
) -> None:
    chat, fake = await on_the_question(make_chat, kb)
    await chat.say(said)
    assert (await session(chat))["picked"] != "unsure"
    assert len(fake.requests) == 1  # as before: DeepSeek reads it


async def test_the_words_work_only_on_that_question(make_chat, kb: KnowledgeBase) -> None:
    wide = {**UNCLEAR, "candidates": ["dislocation", "dressing"], "clarify": None}
    fake = FakeDeepSeek(wide, wide)
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.say("что-то с рукой")  # «Что случилось с рукой или ногой?»: no «Не знаю» here
    assert "Что случилось с рукой или ногой?" in chat.text
    await chat.say("просто ушиб")
    assert len(fake.requests) == 2 and (await session(chat))["picked"] != "unsure"


def test_the_words_stand_with_the_button(kb: KnowledgeBase) -> None:
    item = kb.topics.item("f_unsure")
    assert item is not None and item.unsure
    assert {"не знаю", "я же не врач", "ушиб", "ушибла"} <= set(item.said)
    assert all(word == word.lower().strip() and "ё" not in word for word in item.said)
    assert topics.said_item(kb, "fall", "Ушиб, наверное") is item
    assert topics.said_item(kb, "fall", "ушибленная гордость") is None  # whole words
    assert topics.said_item(kb, "limb_all", "ушиб") is None
