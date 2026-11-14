"""Play docs/manual_test.md through Conversation: one account, cases in order, as in Telegram."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.bot.conversation import Conversation
from app.config import ClinicData, Settings
from app.db.session import Database
from app.flow.loader import KnowledgeBase
from app.schedule.mock import MockSchedule
from tests.chat_harness import Chat, Clock
from tests.conftest import NOW
from tests.fake_deepseek import FakeDeepSeek
from tests.manual_cases import CASES, DOC, SECRETS, Case, Step, render_doc


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}")
    await database.init()
    yield database
    await database.close()


async def _do(chat: Chat, step: Step) -> None:
    if step.do == "start":
        await chat.start()
    elif step.do == "menu":
        chat._take(await chat.conversation.menu(chat.user))
    elif step.do == "say":
        await chat.say(step.value)
    elif step.do == "press":
        await chat.press(step.value)
    elif step.do == "slot":
        await chat.pick_slot()
    elif step.do == "contact":
        await chat.contact()
    elif step.do == "forget":
        chat._take(await chat.conversation.forget(chat.user))
    elif step.do == "demo_until":
        for _ in range(4):
            await chat.demo_next()
            if step.value in chat.text:
                break
    elif step.do == "close":
        post = next(p for p in reversed(chat.admin) if p.buttons)
        handoff = int(post.buttons[0][0].data.removeprefix("close:"))
        assert await chat.conversation.close_handoff(handoff, "Администратор")
    elif step.do == "admin":
        post = chat.admin[-1]
        message_id = 10_000 + len(chat.admin)
        await chat.conversation.register_admin_message(message_id, post.handoff_id)
        chat._take(await chat.conversation.admin_reply(message_id, step.value))
    else:
        raise AssertionError(f"unknown action {step.do!r}")


async def _play(chat: Chat, fake: FakeDeepSeek, case: Case) -> None:
    for i, step in enumerate(case.steps, 1):
        where = f"кейс {case.number}, шаг {i}: {step.do} {step.value!r}"
        if step.llm:
            fake.replies = list(step.llm)
        sent, posted = len(fake.requests), len(chat.admin)
        await _do(chat, step)
        for fragment in step.expect:
            assert fragment in chat.text, f"{where}: нет «{fragment}» в ответе:\n{chat.text}"
        for fragment in step.absent:
            assert fragment not in chat.text, f"{where}: «{fragment}» в ответе:\n{chat.text}"
        for fragment in step.admin:
            assert len(chat.admin) > posted, f"{where}: нет сообщения администраторам"
            assert fragment in chat.admin[-1].text, f"{where}: «{fragment}»"
        if step.private:
            assert len(fake.requests) > sent, f"{where}: DeepSeek не вызывался"
            outgoing = json.dumps(
                [r["messages"][1:] for r in fake.requests[sent:]], ensure_ascii=False
            )
            for secret in SECRETS:
                assert secret not in outgoing, f"{where}: в DeepSeek ушло «{secret}»"


async def test_all_manual_cases_in_order(
    kb: KnowledgeBase, clinic: ClinicData, db: Database, schedule: MockSchedule, tmp_path: Path
) -> None:
    fake = FakeDeepSeek()
    settings = Settings(
        _env_file=None,
        schedule_provider="mock",
        demo_mode=True,
        admin_chat_id=-100123,
        handoff_daily_limit=10,  # the run has more than three requests in a day
        misses_log=tmp_path / "misses.jsonl",  # the journal is on, as in the bot — not in data/
    )
    conversation = Conversation(
        kb=kb,
        clinic=clinic,
        settings=settings,
        db=db,
        schedule=schedule,
        assistant=fake.assistant(kb),
        clock=Clock(NOW),
    )
    chat = Chat(conversation)
    for case in CASES:
        await _play(chat, fake, case)
    numbers = {c.number.split(".")[0] for c in CASES if not c.extra}
    assert numbers == {str(n) for n in range(1, 21)}


def test_manual_test_doc_is_up_to_date() -> None:
    assert DOC.read_text(encoding="utf-8") == render_doc(), (
        "run: uv run python -m tests.manual_cases --write"
    )
