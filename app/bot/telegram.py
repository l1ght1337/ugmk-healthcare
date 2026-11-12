"""Telegram layer (aiogram 3): updates → Conversation, Turn → messages.

* private chats: patients; only ALLOWED_USER_IDS while the test is closed;
* ADMIN_CHAT_ID: administrators answer by replying to the bot's message (the bot gets
  replies to its own messages even with privacy mode on) and the answer goes to the patient.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    TelegramObject,
)

from app.bot import texts
from app.bot.conversation import Conversation, Reply, Turn, UserRef
from app.config import Settings

log = logging.getLogger(__name__)

Markup = InlineKeyboardMarkup | ReplyKeyboardMarkup | ReplyKeyboardRemove | None


def markup(reply: Reply) -> Markup:
    if reply.ask_contact:
        button = KeyboardButton(text=texts.CONTACT_BUTTON, request_contact=True)
        return ReplyKeyboardMarkup(
            keyboard=[[button]], resize_keyboard=True, one_time_keyboard=True
        )
    if reply.buttons:
        rows = [
            [InlineKeyboardButton(text=b.label, callback_data=b.data) for b in row]
            for row in reply.buttons
        ]
        return InlineKeyboardMarkup(inline_keyboard=rows)
    if reply.remove_keyboard:
        return ReplyKeyboardRemove()
    return None


def user_ref(obj: Message | CallbackQuery) -> UserRef:
    user = obj.from_user
    assert user is not None
    chat_id = obj.chat.id if isinstance(obj, Message) else user.id
    name = " ".join(p for p in (user.first_name, user.last_name) if p) or None
    return UserRef(id=user.id, chat_id=chat_id, username=user.username, display_name=name)


class Delivery:
    """Sends a Turn: patient replies, then posts to the admin chat (remembering their ids)."""

    def __init__(self, bot: Bot, conversation: Conversation, admin_chat_id: int | None) -> None:
        self.bot = bot
        self.conversation = conversation
        self.admin_chat_id = admin_chat_id

    async def __call__(self, turn: Turn, chat_id: int | None = None) -> None:
        for reply in turn.replies:
            target = reply.chat_id or chat_id
            if target is None:
                log.error("reply without a chat: %.40s", reply.text)
                continue
            await self.bot.send_message(target, reply.text, reply_markup=markup(reply))
        for post in turn.admin:
            if self.admin_chat_id is None:
                log.error("ADMIN_CHAT_ID is not set; handoff %s not delivered", post.handoff_id)
                continue
            sent = await self.bot.send_message(
                self.admin_chat_id, post.text, reply_to_message_id=post.reply_to
            )
            await self.conversation.register_admin_message(sent.message_id, post.handoff_id)


class AllowedUsers(BaseMiddleware):
    """While the test is closed, only ALLOWED_USER_IDS may talk to the bot."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is None or self.settings.user_allowed(user.id):
            return await handler(event, data)
        if isinstance(event, CallbackQuery):
            await event.answer(texts.NOT_ALLOWED, show_alert=True)
        elif isinstance(event, Message):
            await event.answer(texts.NOT_ALLOWED)
        return None


def _pressed_label(callback: CallbackQuery) -> str | None:
    message = callback.message
    keyboard = getattr(message, "reply_markup", None)
    for row in getattr(keyboard, "inline_keyboard", None) or []:
        for button in row:
            if button.callback_data == callback.data:
                return button.text
    return None


def build_dispatcher(
    settings: Settings, conversation: Conversation, delivery: Delivery
) -> Dispatcher:
    dp = Dispatcher()
    admin = Router(name="admin")
    patients = Router(name="patients")
    patients.message.filter(F.chat.type == "private")
    patients.message.outer_middleware(AllowedUsers(settings))
    patients.callback_query.outer_middleware(AllowedUsers(settings))

    if settings.admin_chat_id is not None:

        @admin.message(F.chat.id == settings.admin_chat_id, F.reply_to_message)
        async def admin_reply(message: Message, bot: Bot) -> None:
            replied = message.reply_to_message
            me = await bot.me()
            if replied is None or replied.from_user is None or replied.from_user.id != me.id:
                return
            text = message.text or message.caption
            if not text:
                return
            turn = await conversation.admin_reply(replied.message_id, text)
            if turn is None:
                await message.reply("Не нашёл обращение для этого сообщения.")
                return
            await delivery(turn)
            await message.reply("Ответ отправлен пациенту.")

    @patients.message(CommandStart())
    async def start(message: Message) -> None:
        await delivery(await conversation.start(user_ref(message)), message.chat.id)

    @patients.message(Command("menu"))
    async def menu(message: Message) -> None:
        await delivery(await conversation.menu(user_ref(message)), message.chat.id)

    @patients.message(Command("admin"))
    async def call_admin(message: Message) -> None:
        await delivery(await conversation.call_admin(user_ref(message)), message.chat.id)

    @patients.message(Command("demo_next"))
    async def demo_next(message: Message) -> None:
        await delivery(await conversation.demo_next(user_ref(message)), message.chat.id)

    @patients.message(F.contact)
    async def contact(message: Message) -> None:
        assert message.contact is not None
        turn = await conversation.contact(
            user_ref(message), message.contact.phone_number, message.contact.user_id
        )
        await delivery(turn, message.chat.id)

    @patients.message(F.text)
    async def text(message: Message) -> None:
        assert message.text is not None
        await delivery(await conversation.text(user_ref(message), message.text), message.chat.id)

    @patients.message()
    async def other(message: Message) -> None:
        await message.answer(texts.PICK_BUTTON_SHORT)

    @patients.callback_query()
    async def button(callback: CallbackQuery) -> None:
        await callback.answer()
        message = callback.message
        label = _pressed_label(callback)
        if isinstance(message, Message):
            try:  # show what was chosen and drop the buttons, so they cannot be pressed twice
                suffix = f"\n\n— {label}" if label else ""
                await message.edit_text((message.text or "") + suffix, reply_markup=None)
            except TelegramBadRequest:  # editing is cosmetic (old or unchanged message)
                log.debug("could not edit the message with buttons", exc_info=True)
        turn = await conversation.button(user_ref(callback), callback.data or "")
        await delivery(turn, callback.from_user.id)

    dp.include_router(admin)
    dp.include_router(patients)
    return dp
