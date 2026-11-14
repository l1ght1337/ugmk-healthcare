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
    kb = markup(Reply("телефон", ask_contact=True))  # «Назад» is the second row
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
    # a reply to a request, /today and /misses; «Закрыть обращение» and «Разрешить»
    assert len(admin.message.handlers) == 3 and len(admin.callback_query.handlers) == 2


# --- service messages and bots never reach the dialog (the pinned card, 13.11) -------------


def _message(**kw):  # type: ignore[no-untyped-def]
    from datetime import datetime

    from aiogram.types import Chat, Message, User

    who = kw.pop("who", User(id=1001, is_bot=False, first_name="Тест"))
    chat = kw.pop("chat", Chat(id=1001, type="private"))
    return Message(
        message_id=kw.pop("message_id", 7), date=datetime.now(), chat=chat, from_user=who, **kw
    )


class _Deleting:
    def __init__(self) -> None:
        self.deleted: list[tuple[int, int]] = []

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        self.deleted.append((chat_id, message_id))
        return True


async def test_pin_service_message_from_the_bot_gets_no_answer(tmp_path) -> None:
    """The bot pins the booking card; Telegram reports it with the bot as the author."""
    from aiogram.types import Chat, User
    from sqlalchemy import select

    from app.bot.access import Access
    from app.bot.telegram import OnlyPatients
    from app.db.models import AccessRequest
    from app.db.session import Database

    db = Database(f"sqlite+aiosqlite:///{tmp_path}/a.db")
    await db.init()
    answers: list[object] = []
    reached: list[str] = []

    async def handler(event, data):  # type: ignore[no-untyped-def]
        reached.append(event.content_type)
        return "ok"

    async def deliver(turn, chat_id=None):  # type: ignore[no-untyped-def]
        answers.append(turn)

    access = Access(Settings(_env_file=None, allowed_user_ids="1001", admin_chat_id=-100), db)
    allowed = AllowedUsers(access, deliver)
    patients = OnlyPatients()

    async def chain(event):  # type: ignore[no-untyped-def]
        async def inner(e, d):  # type: ignore[no-untyped-def]
            return await allowed(handler, e, d)

        return await patients(inner, event, {"bot": api})

    api = _Deleting()
    bot = User(id=9021237787, is_bot=True, first_name="Ассистент УГМК", username="ummc_ai")
    card = _message(message_id=5, who=bot, text="Запись в травмпункт")
    pinned = _message(message_id=7, who=bot, pinned_message=card)
    assert await chain(pinned) is None
    assert reached == [] and answers == []  # no reply to the patient, no post to the group
    assert api.deleted == [(1001, 7)]  # «… закрепил(а) сообщение» is removed from the chat
    async with db() as s:
        assert (await s.scalars(select(AccessRequest))).all() == []

    assert await chain(_message(who=bot, text="привет от бота")) is None  # any update from a bot
    group = Chat(id=-100, type="supergroup")
    assert await chain(_message(chat=group, text="в группе")) is None  # not a private chat
    assert await chain(_message(chat=group, new_chat_title="УГМК")) is None
    assert reached == [] and answers == [] and api.deleted == [(1001, 7)]

    assert await chain(_message(text="запишите меня")) == "ok"  # a patient is served as before
    from aiogram.types import Contact

    contact = Contact(phone_number="+79123456789", first_name="Тест", user_id=1001)
    assert await chain(_message(contact=contact)) == "ok"
    assert reached == ["text", "contact"]
    await db.close()


async def test_the_bot_itself_is_removed_from_access_requests(tmp_path) -> None:
    from sqlalchemy import select

    from app.bot.access import Access
    from app.bot.conversation import UserRef
    from app.db.models import AccessRequest
    from app.db.session import Database

    db = Database(f"sqlite+aiosqlite:///{tmp_path}/a.db")
    await db.init()
    access = Access(Settings(_env_file=None, allowed_user_ids="1", admin_chat_id=-100), db)
    bot = UserRef(id=9021237787, chat_id=1001, username="ummc_ai", display_name="Ассистент УГМК")
    await access.refuse(bot)  # what the old code did when the bot pinned a card
    await access.approve(bot.id, "Администратор")
    assert await access.allowed(bot.id)
    assert await access.forget(bot.id)  # at startup: the bot is not a user
    assert not await access.allowed(bot.id)
    async with db() as s:
        assert (await s.scalars(select(AccessRequest))).all() == []
    assert not await access.forget(bot.id)  # nothing left to remove
    await db.close()


def test_only_patients_comes_before_the_access_check(kb, clinic, schedule, tmp_path) -> None:
    from app.bot.access import Access
    from app.bot.conversation import Conversation
    from app.bot.telegram import OnlyPatients, build_dispatcher
    from app.db.session import Database

    settings = Settings(_env_file=None, admin_chat_id=-100123, allowed_user_ids="1")
    conversation = Conversation(
        kb=kb, clinic=clinic, settings=settings, schedule=schedule, assistant=None,
        db=Database(f"sqlite+aiosqlite:///{tmp_path}/b.db"),
    )  # fmt: skip

    async def deliver(turn, chat_id=None):  # type: ignore[no-untyped-def]
        return None

    dp = build_dispatcher(settings, conversation, deliver, Access(settings, conversation.db))  # type: ignore[arg-type]
    patients = dp.sub_routers[1]
    for observer in (patients.message, patients.callback_query):
        first, second = list(observer.outer_middleware)[:2]
        assert isinstance(first, OnlyPatients) and isinstance(second, AllowedUsers)
