"""The journal of what the bot did not understand: data/misses.jsonl and /misses.

One line per fallback answer: the time, the step, the kind of the answer and the patient's
text after masking. No user id, no chat id, no name.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from app.bot import misses, texts
from app.flow.loader import KnowledgeBase
from tests.chat_harness import ADMIN_CHAT, PATIENT, Chat
from tests.conftest import NOW
from tests.fake_deepseek import FakeDeepSeek
from tests.test_wish_flow import through_dislocation


@pytest.fixture
def journal(tmp_path: Path) -> Path:
    return tmp_path / "misses.jsonl"


def lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


async def to_days(chat: Chat) -> None:
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await through_dislocation(chat)


async def test_a_hint_after_route_is_written_down(make_chat, kb: KnowledgeBase, journal) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb), misses_log=journal)
    await to_days(chat)
    assert lines(journal) == []  # a booking by buttons leaves nothing
    fake.replies = [{"type": "offtopic"}]
    await chat.say("ну и погода выдалась")
    (row,) = lines(journal)
    assert row == {
        "at": NOW.isoformat(timespec="seconds"),
        "step": "days",
        "kind": misses.HINT,
        "text": "ну и погода выдалась",
    }


async def test_personal_data_is_masked_and_the_user_is_not_named(make_chat, kb, journal) -> None:
    fake = FakeDeepSeek({"type": "offtopic"})
    chat = make_chat(assistant=fake.assistant(kb), misses_log=journal)
    await chat.start()
    await chat.say("меня зовут Петров Иван Сергеевич, телефон +7 912 345-67-89, расскажи анекдот")
    (row,) = lines(journal)
    assert row["kind"] == misses.OFFTOPIC and row["step"] == "idle"
    assert "[ИМЯ]" in row["text"] and "[ТЕЛЕФОН]" in row["text"] and "анекдот" in row["text"]
    raw = journal.read_text(encoding="utf-8")
    for secret in ("Петров", "Иван", "912", str(PATIENT.id), "tester", "Тестов"):
        assert secret not in raw, secret
    assert set(row) == {"at", "step", "kind", "text"}


@pytest.mark.parametrize(
    ("replies", "text", "kind"),
    [
        (({"type": "clinic_question"}, {"found": False}), "а кофе у вас есть?", misses.NO_INFO),
        (({"type": "offtopic"},), "расскажи анекдот", misses.OFFTOPIC),
        (({"type": "other_medical"},), "третий день температура", misses.NOT_OURS),
        (({"type": "complaint", "area": None, "scenario": None},), "болит", misses.UNCLEAR),
        ((), "сколько стоит приём?", misses.DOWN),
    ],
    ids=["нет информации", "не по теме", "не наш профиль", "не понял", "DeepSeek молчит"],
)
async def test_fallback_answers_in_the_menu(make_chat, kb, journal, replies, text, kind) -> None:
    fake = FakeDeepSeek(*replies)
    chat = make_chat(assistant=fake.assistant(kb), misses_log=journal)
    await chat.start()
    if kind == misses.DOWN:
        await chat.press(texts.MENU_ASK)
    await chat.say(text)
    assert [(r["kind"], r["text"]) for r in lines(journal)] == [(kind, text)]


async def test_not_a_text_and_unread_data(make_chat, journal) -> None:
    chat = make_chat(misses_log=journal)
    await to_days(chat)
    chat._take(await chat.conversation.other(chat.user))  # a photo, a voice message
    await chat.pick_slot("Завтра")
    await chat.press(texts.CONSENT_YES)
    await chat.say("я же всё написала выше")  # no name in it
    got = [(r["step"], r["kind"], r["text"]) for r in lines(journal)]
    assert got == [
        ("days", misses.NOT_TEXT, ""),
        ("name", misses.UNREAD, "я же всё написала выше"),
    ]


async def test_a_question_passed_to_an_administrator(make_chat, kb, journal) -> None:
    fake = FakeDeepSeek({"type": "clinic_question"}, {"found": False})
    chat = make_chat(assistant=fake.assistant(kb), misses_log=journal)
    await chat.start()
    await chat.say("делаете МРТ колена?")
    await chat.press(texts.ASK_ADMIN_QUESTION)
    await chat.press(texts.CONSENT_YES)
    await chat.contact()
    assert len(chat.admin) == 1
    kinds = [(r["kind"], r["text"]) for r in lines(journal)]
    assert kinds == [(misses.NO_INFO, "делаете МРТ колена?"), (misses.ADMIN, "делаете МРТ колена?")]


async def test_a_journal_that_cannot_be_written_does_not_break_the_dialog(
    make_chat, tmp_path
) -> None:
    chat = make_chat(misses_log=tmp_path)  # a directory: writing fails
    await to_days(chat)
    await chat.say("ну и погода выдалась")
    assert chat.replies[0].text == texts.HINT_TIME and chat.days()


async def test_no_journal_when_it_is_switched_off(make_chat, tmp_path) -> None:
    chat = make_chat()  # the tests' default: MISSES_LOG is off
    await to_days(chat)
    await chat.say("ну и погода выдалась")
    assert chat.conversation.misses.path is None
    assert await chat.conversation.admin_misses() == texts.MISSES_OFF


async def test_misses_command_shows_the_last_twenty_and_a_day_of_counters(
    make_chat, clock, journal
) -> None:
    chat = make_chat(misses_log=journal)
    assert await chat.conversation.admin_misses() == texts.MISSES_EMPTY
    await to_days(chat)
    await chat.say("ну и погода выдалась")  # yesterday: listed, not counted
    clock.now = NOW + timedelta(days=1, hours=1)
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await through_dislocation(chat)
    for n in range(22):
        await chat.say(f"что-то непонятное {n}")
    chat._take(await chat.conversation.other(chat.user))
    report = await chat.conversation.admin_misses()
    head, _, tail = report.partition("\n\n")
    assert head.splitlines() == [
        texts.MISSES_DAY.format(count=23),
        f"• {misses.HINT} — 22",
        f"• {misses.NOT_TEXT} — 1",
    ]
    rows = tail.splitlines()
    assert rows[0] == texts.MISSES_LAST.format(count=20) and len(rows) == 21
    assert rows[-1] == f"13.11 13:00 · days · {misses.NOT_TEXT}"
    assert rows[-2] == f"13.11 13:00 · days · {misses.HINT} · «что-то непонятное 21»"
    assert "ну и погода" not in report and "что-то непонятное 1»" not in report
    assert len(report) < 4000  # one Telegram message


def test_dispatcher_has_the_misses_command(kb, clinic, schedule, tmp_path) -> None:
    from app.bot.access import Access
    from app.bot.conversation import Conversation
    from app.bot.telegram import build_dispatcher
    from app.db.session import Database
    from tests.chat_harness import make_settings

    settings = make_settings()
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}")
    conversation = Conversation(
        kb=kb, clinic=clinic, settings=settings, db=db, schedule=schedule, assistant=None
    )

    async def delivery(*args, **kwargs):  # type: ignore[no-untyped-def]
        return None

    dp = build_dispatcher(settings, conversation, delivery, Access(settings, db))
    admin = next(r for r in dp.sub_routers if r.name == "admin")
    commands = [
        str(command)
        for handler in admin.message.handlers
        for f in handler.filters
        for command in getattr(f.callback, "commands", ())
    ]
    assert {"today", "misses"} <= set(commands) and settings.admin_chat_id == ADMIN_CHAT
