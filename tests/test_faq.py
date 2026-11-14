"""docs/faq_matrix.md: every question goes the stated way; data rows really have the data."""

from __future__ import annotations

import pytest

from app.bot import texts
from app.flow.commands import find_command
from app.flow.loader import KB_DIR, KnowledgeBase, normalize_ws
from app.llm.catalogue import render_info
from tests.chat_harness import Chat
from tests.fake_deepseek import FakeDeepSeek
from tests.faq_cases import (
    CONFIG,
    DOC,
    FAQS,
    KB,
    NO_DATA,
    SCENARIO,
    SCHEDULE,
    TEMPLATE,
    Faq,
    no_data_questions,
    render_doc,
    summary,
)


def test_at_least_sixty_questions_in_twelve_groups() -> None:
    assert len(FAQS) >= 60
    groups = {f.group for f in FAQS}
    assert len(groups) == 12
    assert {f.group for f in FAQS if f.live} == groups  # a live test per group
    assert sum(summary().values()) == len(FAQS)


@pytest.mark.parametrize(
    "faq", [f for f in FAQS if f.source in (KB, CONFIG)], ids=lambda f: f.question
)
def test_data_answers_are_in_the_spravka(faq: Faq) -> None:
    info = normalize_ws(render_info()).lower()
    assert faq.facts, faq.question
    for fact in faq.facts:
        assert normalize_ws(fact).lower() in info, (faq.question, fact)


def test_questions_without_data_are_asked_of_the_clinic() -> None:
    requests = (KB_DIR.parent / "docs" / "clinic_requests.md").read_text(encoding="utf-8")
    for faq in no_data_questions():
        assert f"«{faq.question}»" in requests, faq.question


def test_faq_doc_is_up_to_date() -> None:
    assert DOC.read_text(encoding="utf-8") == render_doc(), (
        "run: uv run python -m tests.faq_cases --write"
    )


def _route(faq: Faq) -> dict:
    if faq.source == SCENARIO:
        return {"type": "complaint", "scenario": faq.scenario}
    if faq.kind == "menu_action":
        return {"type": "menu_action", "action": faq.action}
    return {"type": faq.kind}


def _answer(faq: Faq) -> dict | None:
    if faq.kind != "clinic_question":
        return None
    if faq.source == NO_DATA:
        return {"found": False}
    return {"found": True, "answer": f"{faq.facts[0]}."}


@pytest.mark.parametrize("faq", FAQS, ids=lambda f: f.question)
async def test_the_bot_answers_as_the_matrix_says(make_chat, kb: KnowledgeBase, faq: Faq) -> None:
    replies = [] if faq.command or faq.source == SCHEDULE else [_route(faq)]
    if (answer := _answer(faq)) is not None:
        replies.append(answer)
    fake = FakeDeepSeek(*replies)
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say(faq.question)
    if faq.command:
        assert find_command(faq.question) == faq.action and fake.requests == []
    if faq.source == SCHEDULE:
        assert fake.requests == []
    await _check(chat, faq)


async def _check(chat: Chat, faq: Faq) -> None:
    text, labels = chat.text, chat.labels()
    if faq.source in (KB, CONFIG):
        assert text.startswith(f"{faq.facts[0]}."), text  # the answer is shown, it is grounded
        assert texts.INFO_UNKNOWN not in text
    elif faq.source == SCHEDULE:  # built by code from the schedule: DeepSeek is not asked
        assert faq.expect and faq.expect in text, text
        assert texts.INFO_UNKNOWN not in text and labels
    elif faq.kind == "clinic_question":  # no data
        assert text == texts.INFO_UNKNOWN and labels[0] == texts.ASK_ADMIN_QUESTION
        assert chat.admin == []  # only by the button
    elif faq.kind == "other_department":
        assert text == texts.OTHER_DEPARTMENT_NO_PHONE and labels[0] == texts.ASK_ADMIN_QUESTION
    elif faq.source == SCENARIO:
        from app.db.models import User

        async with chat.conversation.db() as s:
            session = (await s.get(User, chat.user.id)).session
        assert session["dialog"]["scenario_id"] == faq.scenario
    elif faq.kind == "complaint":  # a medical question next to an injury: the complaint goes on
        from app.db.models import User

        async with chat.conversation.db() as s:
            session = (await s.get(User, chat.user.id)).session
        assert chat.replies[0].text == texts.MEDICAL_ASIDE and session["wizard"]
        assert "На что больше похоже?" in chat.replies[-1].text
    elif faq.kind == "medical_question":
        assert text == texts.MEDICAL and labels[0] == texts.MENU_BOOK
    elif faq.kind == "about_bot":
        assert text == texts.ABOUT_BOT and texts.MENU_ADMIN in labels
    elif faq.kind == "feedback":
        assert text == texts.FEEDBACK and labels[0] == texts.HANDOFF_YES and chat.admin == []
    elif faq.action == "book":
        assert texts.ASK_COMPLAINT in text
        if "ребёнка" in faq.question or "маму" in faq.question:  # who the visit is for is kept
            who = "child" if "ребёнка" in faq.question else "adult"
            assert texts.FOR_WHOM[who] in text
    elif faq.action == "feedback":
        assert text == texts.ASK_FEEDBACK
    elif faq.action == "my_bookings":
        assert text == texts.MY_EMPTY
    elif faq.action == "forget":
        assert text == texts.FORGET_ASK
    elif faq.action == "admin":
        assert texts.CONSENT_HANDOFF in text
    else:
        raise AssertionError(f"no check for {faq}")
    assert faq.source in (KB, CONFIG, SCENARIO, SCHEDULE, TEMPLATE, NO_DATA)


async def test_feedback_goes_to_the_admins_with_the_text_after_the_button(
    make_chat, kb: KnowledgeBase
) -> None:
    chat = make_chat(assistant=FakeDeepSeek({"type": "feedback"}).assistant(kb))
    await chat.start()
    await chat.say("Огромное спасибо доктору!")
    await chat.press(texts.HANDOFF_YES)
    await chat.press(texts.CONSENT_YES)
    await chat.contact()
    post = chat.admin[-1].text
    assert texts.FEEDBACK_REASON in post and "Огромное спасибо доктору!" in post


async def test_other_department_names_the_phone_once_it_is_known(
    make_chat, kb: KnowledgeBase, clinic
) -> None:
    chat = make_chat(assistant=FakeDeepSeek({"type": "other_department"}).assistant(kb))
    contacts = clinic.contacts.model_copy(update={"call_center_phone": "+7 343 000-00-00"})
    chat.conversation.clinic = clinic.model_copy(update={"contacts": contacts})
    await chat.start()
    await chat.say("Как записаться к стоматологу?")
    assert chat.text == texts.OTHER_DEPARTMENT.format(phone="+7 343 000-00-00")
