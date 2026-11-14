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
    for phrase in ("когда у меня приём", "отмени запись", "отмени запись на сегодня"):
        await chat.say(phrase)
        assert texts.MY_ONE in chat.text and "Петров Иван Сергеевич" in chat.text
        assert {texts.MOVE, texts.CANCEL} <= set(chat.labels())
    await chat.say("перенеси запись на завтра")  # a new day is named: the move starts at once
    assert chat.text.startswith("Переношу запись с четверга, 12 ноября")
    assert "13 ноября (пятница)" in chat.text
    assert chat.labels()[-2:] == [texts.KEEP_AS_IS, texts.MENU]


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


@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("хочу к врачу", "book"),
        ("мне бы к травматологу попасть", "book"),
        ("запишыте пожалуста", "book"),
        ("запишите ребёнка", "book"),
        ("можно записать маму?", "book"),
        ("во сколько мне приходить?", "my_bookings"),
        ("я записан?", "my_bookings"),
        ("хочу перенести", "my_bookings"),
        ("перезапишите меня на пятницу", "my_bookings"),
        ("можно прийти в другой день?", "my_bookings"),
        ("я не приду", "my_bookings"),
        ("не смогу прийти завтра", "my_bookings"),
        ("подтверждаю", "confirm"),
        ("да, приду", "confirm"),
        ("буду", "confirm"),
        ("хочу поговорить с человеком", "admin"),
        ("перезвоните мне", "admin"),
        ("забудь меня", "forget"),
        ("хочу пожаловаться", "feedback"),
        ("как оставить отзыв", "feedback"),
    ],
)
def test_requests_in_plain_words(text: str, action: str) -> None:
    assert find_command(text) == action


@pytest.mark.parametrize(
    "text",
    [
        "как к вам попасть?",  # the address, not a booking
        "когда мне приходить на контрольный приём?",  # a general question
        "не приду в себя никак",
        "перенёс операцию, болит нога",
        "хочу к врачу, болит нога",  # a complaint
        "ждал приёма час, это безобразие",  # a complaint with its text: DeepSeek reads it
        "подтвердите, что запись есть",
        "буду завтра в городе, можно записаться?",
    ],
)
def test_still_not_commands(text: str) -> None:
    assert find_command(text) is None, text


def test_navigation_small_talk_and_audience() -> None:
    from app.flow.commands import find_audience, find_navigation, find_small_talk

    assert [find_navigation(t) for t in ("Назад", "в меню", "Главное меню", "отмена", "стоп")] == [
        "back", "home", "home", "cancel", "cancel",
    ]  # fmt: skip
    assert find_navigation("назад упал на спину") is None
    assert find_navigation("отмени запись") is None  # a command about the booking
    assert [find_small_talk(t) for t in ("Привет!", "спасибо большое", "ок", "пока")] == [
        "greeting", "thanks", "ack", "bye",
    ]  # fmt: skip
    assert find_small_talk("спасибо доктору Кузнецову, всё прошло отлично") is None
    assert find_audience("запишите ребёнка") == "child"
    assert find_audience("у мамы перелом руки") == "adult"
    assert find_audience("дочку укусила собака") == "child"
    assert find_audience("гуляла с сыном, упала") is None
    assert find_audience("запишите брата") is None  # a child or an adult: the bot asks
    assert find_audience("мама сказала записаться") is None
