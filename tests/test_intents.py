"""docs/intent_matrix.md: every phrase, typed on every step where a patient may type it.

Three rules for each of them: a request never gets «У меня нет этой информации» or «Я
помогаю только с…»; no part of the message is lost; every answer has a button that leads on.
"""

from __future__ import annotations

import pytest

from app.bot import texts
from app.db.models import Booking, User
from app.flow.loader import KnowledgeBase
from tests import intent_cases as m
from tests.chat_harness import Chat, to_topic
from tests.fake_deepseek import FakeDeepSeek
from tests.intent_cases import INTENTS, Intent, render_doc

STEP_OF = {
    m.MENU: "idle", m.COMPLAINT: "complaint", m.AUDIENCE: "audience", m.AGE: "age",
    m.QUESTION: "question", m.YES_NO: "question", m.DAYS: "days", m.PERIODS: "periods",
    m.TIMES: "times", m.CONSENT: "consent", m.NAME: "name", m.BIRTH: "birth",
    m.PHONE: "contact", m.PAYMENT: "payment", m.ASK: "info", m.BOOKED: "idle",
    m.REMINDED: "idle",
}  # fmt: skip


async def to_step(chat: Chat, step: str) -> None:
    """Bring the patient to the step by buttons, as in Telegram."""
    await chat.start()
    if step == m.MENU:
        return
    if step == m.ASK:
        await chat.press(texts.MENU_ASK)
        return
    if step == m.COMPLAINT:
        await chat.press(texts.MENU_BOOK)
        return
    if step == m.YES_NO:  # «Первично помощь получали в нашем травмпункте?»
        await to_topic(chat, "Повторный приём")
        await chat.press("Для себя")
        return
    await to_topic(chat, "Вывих")
    if step == m.AUDIENCE:
        return
    if step == m.AGE:
        await chat.press("Для ребёнка (до 18 лет)")
        return
    await chat.press("Для себя")
    if step == m.QUESTION:  # «Как получена травма?»: two options, not «да / нет»
        return
    await chat.press("Бытовая травма")
    await chat.press("В течение 7 дней")
    await chat.press("Нет, не обращались")
    if step == m.DAYS:
        return
    await chat.pick_day("Завтра")
    if step == m.PERIODS:
        return
    await chat.pick_period()
    if step == m.TIMES:
        return
    await chat.pick_time()
    if step == m.CONSENT:
        return
    await chat.press(texts.CONSENT_YES)
    if step == m.NAME:
        return
    await chat.say("петров иван сергеевич")
    if step == m.BIRTH:
        return
    await chat.say("12.03.1985")
    if step == m.PHONE:
        return
    await chat.contact()
    if step == m.PAYMENT:
        return
    await chat.press(texts.PAYMENT_SELF)
    if step == m.BOOKED:
        return
    assert step == m.REMINDED, step
    await chat.demo_next()  # «Скоро приём в травмпункте…» with «Подтверждаю»


async def session(chat: Chat) -> dict:
    async with chat.conversation.db() as s:
        user = await s.get(User, chat.user.id)
        return dict(user.session or {})


CASES = [(i, step, must) for i in INTENTS for step, must in i.at]


@pytest.mark.parametrize(
    ("intent", "step", "must"), CASES, ids=[f"{i.phrase} @ {step}" for i, step, _ in CASES]
)
async def test_phrase_on_step(
    make_chat, kb: KnowledgeBase, intent: Intent, step: str, must: str
) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await to_step(chat, step)
    before = await session(chat)
    assert before.get("step", "idle") == STEP_OF[step], "the setup reached the step"
    phrase = chat.times()[0].label if intent.phrase.startswith("<") else intent.phrase
    fake.replies = list(intent.llm)
    await chat.say(phrase)
    after = await session(chat)
    text, labels, now = chat.text, chat.labels(), after.get("step", "idle")

    # the three rules
    assert texts.INFO_UNKNOWN not in text and texts.OFFTOPIC not in text, text
    assert chat.buttons() or any(r.ask_contact for r in chat.replies), f"no button: {text}"
    if must in m.NO_LLM:
        assert fake.requests == [], "read by code: DeepSeek is not called"

    was = STEP_OF[step]
    if must == m.BOOK:
        assert now == "complaint" and texts.ASK_COMPLAINT in text
    elif must == m.BOOK_WISH:
        assert now == "complaint" and "Пожелание:" in text
    elif must in (m.BOOK_CHILD, m.BOOK_ADULT):
        who = "child" if must == m.BOOK_CHILD else "adult"
        assert now == "complaint" and after.get("for_whom") == who
        assert texts.FOR_WHOM[who] in text
    elif must == m.TOPICS_CHILD:
        assert now == "topic" and after.get("for_whom") == "child"
    elif must in (m.GO_ON, m.STAY):
        assert now == was and after["dialog"] == before["dialog"]
        assert texts.PICK_BUTTON_SHORT not in text and "Уточните" not in text
    elif must == m.MY:
        assert texts.MY_TITLE in text or texts.MY_EMPTY in text
        if step in (m.BOOKED, m.REMINDED, m.PAYMENT):
            assert texts.MY_TITLE in text and {texts.MOVE, texts.CANCEL} <= set(labels)
    elif must == m.MY_MOVE:
        assert texts.MY_TITLE in text and "При переносе сразу покажу" in text
    elif must == m.CONFIRM:
        assert "Спасибо, ждём Вас" in text and "в 1" in text
        async with chat.conversation.db() as s:
            from sqlalchemy import select

            booking = (await s.scalars(select(Booking))).one()
        assert booking.confirmed_at is not None
    elif must == m.CONFIRM_NONE:
        assert texts.MY_EMPTY in text
    elif must == m.ANSWER:
        assert any(a in text for a in m.ANSWERS) and now == "idle"
        assert labels == [texts.HELPED, texts.MENU_ADMIN, texts.MENU_HOME]
    elif must == m.ASIDE:
        assert any(a in chat.replies[0].text for a in m.ANSWERS), text
        assert now == was and after["dialog"] == before["dialog"]  # the booking goes on
        assert len(chat.replies) >= 2
    elif must == m.ADMIN:
        assert texts.CONSENT_HANDOFF in text or texts.ASK_CONTACT in text
        assert after.get("resume") == "handoff"
    elif must == m.FEEDBACK_ASK:
        assert texts.ASK_FEEDBACK in text and now == "feedback_text"
    elif must == m.FEEDBACK:
        assert texts.FEEDBACK in text and texts.HANDOFF_YES in labels
    elif must == m.THANKS:
        assert text == texts.WELCOME and texts.MENU_BOOK in labels
    elif must == m.GREETING:
        assert "я виртуальный помощник" in text and texts.MENU_BOOK in labels
    elif must == m.ACK:
        assert text == kb.common.more_help.text and texts.MENU_BOOK in labels
    elif must == m.BYE:
        assert text == kb.common.goodbye.text and texts.MENU_BOOK in labels
    elif must == m.FORGET:
        assert texts.FORGET_ASK in text
    elif must == m.BACK:
        assert texts.PICK_BUTTON_SHORT not in text
        if was in ("idle", "complaint", "info", "payment"):
            assert now == "idle" and texts.MENU_BOOK in labels
        else:
            assert now != was or step == m.TIMES, (was, now)  # one period that day: the day list
    elif must == m.HOME:
        assert now == "idle" and kb.common.more_help.text in text and texts.MENU_BOOK in labels
    elif must == m.CANCEL:
        assert now == "idle" and texts.FLOW_STOPPED in text and texts.MENU_BOOK in labels
    elif must == m.NEXT:
        assert now != was or after["dialog"] != before["dialog"] or after != before
        assert texts.PICK_BUTTON_SHORT not in text and "Уточните" not in text
    elif must == m.SAME:
        assert now == was and labels
    elif must == m.UNCLEAR:
        assert (
            now == "question" and after["dialog"]["question_id"] == before["dialog"]["question_id"]
        )
        assert "Уточните" in text or texts.PICK_BUTTON in text
    elif must == m.TOPICS:
        assert now == "topic" and "Рука или нога" in labels
    elif must == m.NEAREST:
        assert now == "consent" and after.get("slot_id")
    elif must == m.TO_DAYS:
        assert now == "days" and chat.days()
    elif must == m.TO_PERIODS:
        assert now == "periods" and chat.periods()
    elif must == m.TWO_BOOK:
        assert any(a in chat.replies[0].text for a in m.ANSWERS), text
        assert now == "complaint" and texts.ASK_COMPLAINT in text and "Пожелание:" in text
    elif must == m.TWO_COMPLAINT:
        assert any(a in chat.replies[0].text for a in m.ANSWERS), text
        assert now == "audience" and after["dialog"]["scenario_id"] == "fracture"
    else:
        raise AssertionError(f"no check for {must!r}")


def test_fifty_phrases_and_every_group() -> None:
    assert len(INTENTS) >= 50
    assert len({i.group for i in INTENTS}) == 15
    assert sum(1 for i in INTENTS if i.live) >= 5  # checked with the real DeepSeek too


def test_intent_doc_is_up_to_date() -> None:
    assert m.DOC.read_text(encoding="utf-8") == render_doc(), (
        "run: uv run python -m tests.intent_cases --write"
    )
