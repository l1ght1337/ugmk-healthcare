"""Menu commands typed as text open the same screen as the menu buttons (follow-up 1)."""

from __future__ import annotations

import pytest

from app.bot import texts
from app.flow.commands import find_command
from app.flow.loader import KnowledgeBase
from tests.chat_harness import Chat
from tests.fake_deepseek import FakeDeepSeek

PHRASES = [
    ("Напомни про мои записи", texts.MENU_MY),
    ("когда у меня приём", texts.MENU_MY),
    ("отмени запись", texts.MENU_MY),
    ("перенеси запись на завтра", texts.MENU_MY),
    ("хочу записаться", texts.MENU_BOOK),
    ("позови администратора", texts.MENU_ADMIN),
]


async def _screen_after(chat: Chat, action) -> tuple[str, list[str]]:  # type: ignore[no-untyped-def]
    await chat.start()
    await action()
    return chat.text, chat.labels()


@pytest.mark.parametrize(("phrase", "button"), PHRASES)
async def test_typed_command_opens_the_menu_screen(
    make_chat, kb: KnowledgeBase, phrase: str, button: str
) -> None:
    fake = FakeDeepSeek()  # no replies queued: the word list must answer without DeepSeek
    typed = make_chat(assistant=fake.assistant(kb))
    pressed = make_chat()
    pressed.user = typed.user = typed.user.__class__(id=5, chat_id=5)
    expected = await _screen_after(pressed, lambda: pressed.press(button))
    got = await _screen_after(typed, lambda: typed.say(phrase))
    assert got == expected, phrase
    assert fake.requests == []
    assert texts.INFO_UNKNOWN not in got[0]


async def test_my_bookings_by_text_lists_the_booking(make_chat, kb: KnowledgeBase) -> None:
    from tests.test_conversation import book_dislocation

    chat = make_chat(assistant=FakeDeepSeek().assistant(kb))
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    for phrase in ("когда у меня приём", "отмени запись", "перенеси запись на завтра"):
        await chat.say(phrase)
        assert texts.MY_TITLE in chat.text and "Петров Иван Сергеевич" in chat.text
        assert {texts.MOVE, texts.CANCEL} <= set(chat.labels())


async def test_admin_by_text_twice_is_one_request(make_chat) -> None:
    from sqlalchemy import select

    from app.db.models import Handoff

    chat = make_chat()
    await chat.start()
    await chat.say("позови администратора")
    assert texts.CONSENT_HANDOFF in chat.text  # the same path as the button
    await chat.press(texts.CONSENT_YES)
    await chat.contact()
    await chat.say("соедините с оператором")
    assert "уже у администратора" in chat.text
    async with chat.conversation.db() as s:
        assert len((await s.scalars(select(Handoff))).all()) == 1


async def test_route_menu_action_covers_other_wordings(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek({"type": "menu_action", "action": "my_bookings"})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("что у меня там запланировано на следующей неделе?")
    assert chat.text == texts.MY_EMPTY and len(fake.requests) == 1


async def test_menu_action_without_action_is_retried(kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek({"type": "menu_action"}, {"type": "menu_action", "action": "book"})
    result = await fake.assistant(kb).route("мне бы к вам")
    assert (result.kind, result.action) == ("menu_action", "book")
    assert [u.reason for u in fake.usage] == ["invalid_answer", None]


async def test_no_information_only_for_clinic_questions(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek({"type": "clinic_question"}, {"found": False})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("Есть ли у вас бассейн?")
    assert chat.text == texts.INFO_UNKNOWN
    for kind in ("offtopic", "other_medical"):
        chat.conversation.assistant = FakeDeepSeek({"type": kind}).assistant(kb)
        await chat.say("что-то ещё")
        assert texts.INFO_UNKNOWN not in chat.text


@pytest.mark.parametrize(
    "text",
    [
        "Скажите, когда у вас приём",  # opening hours, not «Мои записи»
        "Отменили приём у врача, можно к вам?",
        "Можно записать человека без паспорта?",
        "хочу записаться, сломал руку",  # a complaint: route reads it
        "моя рука болит",
        "Во сколько приём?",
    ],
)
def test_not_commands(text: str) -> None:
    assert find_command(text) is None


@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("мне нужно отменить мою запись", "my_bookings"),
        ("когда я записан?", "my_bookings"),
        ("запишите меня, пожалуйста", "book"),
        ("нужен живой человек", "admin"),
        ("у меня вопрос", "ask"),
    ],
)
def test_more_commands(text: str, action: str) -> None:
    assert find_command(text) == action
