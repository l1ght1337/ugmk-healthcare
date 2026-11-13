"""Telegram translation: keyboards, the allowed-users filter, notification timing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove

from app.bot import texts
from app.bot.conversation import Button, Reply
from app.bot.telegram import AllowedUsers, markup
from app.config import Settings
from app.notify.planner import AFTER_VISIT, DAY_BEFORE, TWO_HOURS, plan
from tests.conftest import NOW


def test_inline_buttons() -> None:
    reply = Reply("?", ((Button("Да", "q:0"), Button("Нет", "q:1")),))
    kb = markup(reply)
    assert isinstance(kb, InlineKeyboardMarkup)
    assert [[(b.text, b.callback_data) for b in row] for row in kb.inline_keyboard] == [
        [("Да", "q:0"), ("Нет", "q:1")]
    ]


def test_contact_keyboard_and_removal() -> None:
    kb = markup(Reply("телефон", ask_contact=True))
    assert isinstance(kb, ReplyKeyboardMarkup)
    button = kb.keyboard[0][0]
    assert button.text == texts.CONTACT_BUTTON and button.request_contact
    assert isinstance(markup(Reply("спасибо", remove_keyboard=True)), ReplyKeyboardRemove)
    assert markup(Reply("просто текст")) is None


@dataclass
class _User:
    id: int


@dataclass
class _Event:
    from_user: _User


async def test_only_allowed_users_reach_the_bot(tmp_path) -> None:
    from app.bot.access import Access
    from app.db.session import Database

    db = Database(f"sqlite+aiosqlite:///{tmp_path}/a.db")
    await db.init()
    calls: list[int] = []

    async def handler(event, data):  # type: ignore[no-untyped-def]
        calls.append(event.from_user.id)
        return "ok"

    async def deliver(turn, chat_id=None):  # type: ignore[no-untyped-def]
        return None

    closed = Settings(_env_file=None, allowed_user_ids="111, 222")
    middleware = AllowedUsers(Access(closed, db), deliver)
    assert await middleware(handler, _Event(_User(111)), {}) == "ok"
    assert await middleware(handler, _Event(_User(333)), {}) is None
    opened = AllowedUsers(Access(Settings(_env_file=None, allowed_user_ids=""), db), deliver)
    assert await opened(handler, _Event(_User(333)), {}) == "ok"
    assert calls == [111, 333]
    await db.close()


async def test_unknown_user_is_offered_to_admins_once(tmp_path) -> None:
    from app.bot.access import Access
    from app.bot.conversation import UserRef
    from app.db.session import Database

    db = Database(f"sqlite+aiosqlite:///{tmp_path}/a.db")
    await db.init()
    access = Access(Settings(_env_file=None, allowed_user_ids="111", admin_chat_id=-100), db)
    stranger = UserRef(id=777, chat_id=777, username="nurse_olga", display_name="Ольга")
    assert not await access.allowed(777)

    first = await access.refuse(stranger)
    assert first.replies[0].text == f"{texts.NOT_ALLOWED} {texts.ACCESS_REQUESTED}"
    (post,) = first.admin
    assert "Ольга @nurse_olga, Telegram id 777" in post.text and post.handoff_id is None
    assert post.buttons[0][0] == Button(texts.ACCESS_ALLOW, "allow:777")
    again = await access.refuse(stranger)
    assert again.admin == [] and again.replies[0].text == texts.NOT_ALLOWED

    granted = await access.approve(777, "Администратор")
    assert granted is not None
    assert granted.replies[0].chat_id == 777 and granted.replies[0].text == texts.ACCESS_GRANTED
    assert await access.allowed(777)
    fresh = Access(Settings(_env_file=None, allowed_user_ids="111", admin_chat_id=-100), db)
    assert await fresh.allowed(777)  # stored in the DB, survives a restart
    assert await access.approve(999, "Администратор") is None
    await db.close()


def test_reminder_plan() -> None:
    start = NOW + timedelta(days=2)
    end = start + timedelta(minutes=30)
    assert plan(start, end, NOW) == [
        (DAY_BEFORE, start - timedelta(hours=24)),
        (TWO_HOURS, start - timedelta(hours=2)),
        (AFTER_VISIT, end + timedelta(hours=3)),
    ]
    soon = NOW + timedelta(hours=3)
    assert [kind for kind, _ in plan(soon, soon + timedelta(minutes=30), NOW)] == [
        TWO_HOURS,
        AFTER_VISIT,
    ]


def test_dispatcher_wires_patient_and_admin_routers(kb, clinic, schedule, tmp_path) -> None:
    from app.bot.conversation import Conversation
    from app.bot.telegram import build_dispatcher
    from app.db.session import Database

    settings = Settings(_env_file=None, admin_chat_id=-100123, allowed_user_ids="1")
    conversation = Conversation(
        kb=kb,
        clinic=clinic,
        settings=settings,
        db=Database(f"sqlite+aiosqlite:///{tmp_path}/b.db"),
        schedule=schedule,
        assistant=None,
    )

    async def deliver(turn, chat_id=None):  # type: ignore[no-untyped-def]
        return None

    from app.bot.access import Access

    access = Access(settings, conversation.db)
    dp = build_dispatcher(settings, conversation, deliver, access)  # type: ignore[arg-type]
    names = [r.name for r in dp.sub_routers]
    assert names == ["admin", "patients"]
    patients = dp.sub_routers[1]
    assert len(patients.message.handlers) == 9 and len(patients.callback_query.handlers) == 1
    admin = dp.sub_routers[0]
    assert len(admin.message.handlers) == 2 and len(admin.callback_query.handlers) == 2
