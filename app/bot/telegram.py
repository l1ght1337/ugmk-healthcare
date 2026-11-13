"""Telegram layer (aiogram 3.31, Bot API 10.3): updates → Conversation, Turn → messages.

* private chats: patients; only ALLOWED_USER_IDS while the test is closed;
* ADMIN_CHAT_ID: administrators answer by replying to the bot's message (the bot gets
  replies to its own messages even with privacy mode on) and the answer goes to the patient.

Newer Bot API features, each with a plain fallback:
* button colours (``style``, 9.4) — older apps show ordinary buttons; if the API refuses,
  the message goes again without colours;
* the booking card as a rich message (``sendRichMessage``, 10.x) — on any API error, and
  with ``RICH_MESSAGES=false``, the same content as plain text;
* answers streamed as a draft (``sendMessageDraft``, 9.3) — if the API refuses once, drafts
  are switched off and only «печатает…» (``sendChatAction``) is shown;
* the trauma centre on a map (``sendVenue``) — only when its point is in config/contacts.yaml;
* animations and photos from assets/ (``sendAnimation``, ``sendPhoto``) — uploaded once,
  then sent by file_id (data/media_ids.json); a missing file or MEDIA_ENABLED=false — the
  text alone;
* the booking card pinned silently, unpinned on cancel; reactions 👍 and ❤ on the pressed
  message — cosmetic, any refusal is ignored.
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputRichBlockFooter,
    InputRichBlockList,
    InputRichBlockListItem,
    InputRichBlockParagraph,
    InputRichBlockSectionHeading,
    InputRichMessage,
    KeyboardButton,
    Message,
    ReactionTypeEmoji,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    RichTextBold,
    TelegramObject,
)
from aiogram.utils.chat_action import ChatActionSender

from app.bot import texts
from app.bot.access import ALLOW_PREFIX, APPROVED_NOTE, Access
from app.bot.conversation import Button, Conversation, DraftSink, Reply, Turn, UserRef
from app.bot.render import Card
from app.config import PROJECT_ROOT, ClinicData, Settings

log = logging.getLogger(__name__)

CLOSE_PREFIX = "close:"
ASSETS_DIR = PROJECT_ROOT / "assets"
MEDIA_IDS = PROJECT_ROOT / "data" / "media_ids.json"
CAPTION_MAX = 1024
DRAFT_EVERY = 0.6  # seconds between draft updates
DRAFT_MIN_GROWTH = 12  # characters
Markup = InlineKeyboardMarkup | ReplyKeyboardMarkup | ReplyKeyboardRemove | None


def inline(buttons: tuple[tuple[Button, ...], ...], *, styles: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=b.label, callback_data=b.data, style=b.style if styles else None
            )
            for b in row
        ]
        for row in buttons
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def markup(reply: Reply, *, styles: bool = True) -> Markup:
    if reply.ask_contact:
        button = KeyboardButton(text=texts.CONTACT_BUTTON, request_contact=True)
        return ReplyKeyboardMarkup(
            keyboard=[[button]], resize_keyboard=True, one_time_keyboard=True
        )
    if reply.buttons:
        return inline(reply.buttons, styles=styles)
    if reply.remove_keyboard:
        return ReplyKeyboardRemove()
    return None


def rich_card(card: Card) -> InputRichMessage:
    """The booking card as rich blocks: heading, fields, «Что взять с собой» list, notes."""
    blocks: list[Any] = [
        InputRichBlockSectionHeading(text=card.title, size=3),
        InputRichBlockParagraph(text=card.lead),
    ]
    blocks += [
        InputRichBlockParagraph(text=[RichTextBold(text=f"{name}: "), value])
        for name, value in card.fields
    ]
    blocks += [InputRichBlockParagraph(text=p) for p in card.paragraphs]
    blocks.append(InputRichBlockSectionHeading(text=card.list_title, size=4))
    blocks.append(
        InputRichBlockList(
            items=[
                InputRichBlockListItem(blocks=[InputRichBlockParagraph(text=item)])
                for item in card.items
            ]
        )
    )
    blocks += [InputRichBlockFooter(text=note) for note in card.notes]
    return InputRichMessage(blocks=blocks)


def user_ref(obj: Message | CallbackQuery) -> UserRef:
    user = obj.from_user
    assert user is not None
    chat_id = obj.chat.id if isinstance(obj, Message) else user.id
    name = " ".join(p for p in (user.first_name, user.last_name) if p) or None
    return UserRef(id=user.id, chat_id=chat_id, username=user.username, display_name=name)


class MediaStore:
    """Files from assets/: uploaded once, then reused by Telegram file_id.

    The id is kept with the file's size and modification time, so a replaced file is
    uploaded again. Nothing is sent when MEDIA_ENABLED=false or the file is missing.
    """

    def __init__(
        self, assets: Path = ASSETS_DIR, cache: Path = MEDIA_IDS, *, enabled: bool = True
    ) -> None:
        self.assets, self.cache, self.enabled = assets, cache, enabled
        try:
            self._ids: dict[str, dict[str, Any]] = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._ids = {}

    def _stamp(self, name: str) -> str | None:
        path = self.assets / name
        if not path.is_file():
            return None
        stat = path.stat()
        return f"{stat.st_size}:{int(stat.st_mtime)}"

    def available(self, name: str) -> bool:
        return self.enabled and self._stamp(name) is not None

    def file(self, name: str) -> str | FSInputFile:
        known = self._ids.get(name)
        if known and known.get("stamp") == self._stamp(name):
            return str(known["file_id"])
        return FSInputFile(self.assets / name)

    def remember(self, name: str, file_id: str) -> None:
        stamp = self._stamp(name)
        if stamp is None or self._ids.get(name, {}).get("file_id") == file_id:
            return
        self._ids[name] = {"file_id": file_id, "stamp": stamp}
        try:
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            self.cache.write_text(json.dumps(self._ids, ensure_ascii=False), encoding="utf-8")
        except OSError:
            log.warning("could not save %s", self.cache, exc_info=True)


class Delivery:
    """Sends a Turn: patient replies, then posts to the admin chat (remembering their ids).

    A pressed button's message is replaced by the next wizard screen (``Reply.edit``);
    otherwise it shows what was chosen and loses its buttons. When a new screen is sent
    below, the previous screen loses its buttons, so old steps cannot be pressed.
    """

    def __init__(
        self,
        bot: Bot,
        conversation: Conversation,
        admin_chat_id: int | None,
        *,
        rich_messages: bool = True,
        media: MediaStore | None = None,
    ) -> None:
        self.bot = bot
        self.conversation = conversation
        self.admin_chat_id = admin_chat_id
        self.rich_messages = rich_messages
        self.media = media or MediaStore(enabled=False)
        self._screens: dict[int, int] = {}  # chat → message id of the current screen

    async def __call__(
        self,
        turn: Turn,
        chat_id: int | None = None,
        *,
        pressed: Message | None = None,
        label: str | None = None,
    ) -> None:
        replies = list(turn.replies)
        if pressed is not None:
            first = replies[0] if replies else None
            if first is not None and first.edit and await self._replace(pressed, first):
                replies = replies[1:]
                if first.screen:
                    self._screens[pressed.chat.id] = pressed.message_id
                else:
                    self._screens.pop(pressed.chat.id, None)
            else:
                await self._mark_pressed(pressed, label)
        for reply in replies:
            target = reply.chat_id or chat_id
            if target is None:
                log.error("reply without a chat: %.40s", reply.text)
                continue
            if reply.screen or reply.card or reply.ask_contact:
                await self._strip_screen(target)
            sent = await self._send(target, reply)
            if reply.screen and sent is not None:
                self._screens[target] = sent.message_id
            if reply.pin is not None and sent is not None:
                await self._pin(target, sent.message_id, reply.pin)
        target = chat_id if chat_id is not None else pressed.chat.id if pressed else None
        if target is not None:
            for message_id in turn.unpin:
                await self._cosmetic(
                    self.bot.unpin_chat_message(chat_id=target, message_id=message_id)
                )
        if turn.reaction and pressed is not None:
            reaction = [ReactionTypeEmoji(emoji=turn.reaction)]
            await self._cosmetic(
                self.bot.set_message_reaction(
                    chat_id=pressed.chat.id, message_id=pressed.message_id, reaction=reaction
                )
            )
        for post in turn.admin:
            if self.admin_chat_id is None:
                log.error("ADMIN_CHAT_ID is not set; handoff %s not delivered", post.handoff_id)
                continue
            if post.edit_message is not None:  # e.g. the booking card got the payment
                try:
                    await self.bot.edit_message_text(
                        post.text, chat_id=self.admin_chat_id, message_id=post.edit_message
                    )
                except TelegramBadRequest:
                    log.debug("could not edit the admin chat message", exc_info=True)
                continue
            sent = await self.bot.send_message(
                self.admin_chat_id,
                post.text,
                reply_to_message_id=post.reply_to,
                reply_markup=inline(post.buttons) if post.buttons else None,
            )
            if post.handoff_id is not None:
                await self.conversation.register_admin_message(sent.message_id, post.handoff_id)
            if post.booking_id is not None:
                await self.conversation.register_booking_card(post.booking_id, sent.message_id)

    async def _cosmetic(self, call: Awaitable[Any]) -> None:
        try:
            await call
        except TelegramAPIError:
            log.debug("cosmetic Telegram call refused", exc_info=True)

    async def _pin(self, chat_id: int, message_id: int, booking_id: int) -> None:
        try:
            await self.bot.pin_chat_message(
                chat_id=chat_id, message_id=message_id, disable_notification=True
            )
        except TelegramAPIError:
            log.debug("could not pin the booking card", exc_info=True)
            return
        await self.conversation.register_pinned_card(booking_id, message_id)

    async def _send_media(self, chat_id: int, reply: Reply) -> Message | None:
        assert reply.media is not None
        name, caption = reply.media.name, reply.text or None
        if caption and len(caption) > CAPTION_MAX:
            caption = None
        keyboard = markup(reply) if caption or not reply.text else None
        try:
            if reply.media.kind == "animation":
                sent = await self.bot.send_animation(
                    chat_id, animation=self.media.file(name), caption=caption,
                    reply_markup=keyboard,
                )  # fmt: skip
                file_id = sent.animation.file_id if sent.animation else None
            else:
                sent = await self.bot.send_photo(
                    chat_id, photo=self.media.file(name), caption=caption, reply_markup=keyboard
                )
                file_id = sent.photo[-1].file_id if sent.photo else None
        except TelegramAPIError:
            log.warning("could not send %s, sending the text alone", name, exc_info=True)
            return None
        if file_id:
            self.media.remember(name, file_id)
        if reply.text and caption is None:  # too long for a caption: the text follows
            return await self.bot.send_message(chat_id, reply.text, reply_markup=markup(reply))
        return sent

    async def _send(self, chat_id: int, reply: Reply) -> Message | None:
        if reply.media is not None and self.media.available(reply.media.name):
            sent = await self._send_media(chat_id, reply)
            if sent is not None:
                return sent
        if reply.media_only or not (reply.text or reply.venue):
            return None
        if reply.venue is not None:
            v = reply.venue
            return await self.bot.send_venue(
                chat_id,
                latitude=v.latitude,
                longitude=v.longitude,
                title=v.title,
                address=v.address,
            )
        if reply.card is not None and self.rich_messages:
            try:
                return await self.bot.send_rich_message(
                    chat_id, rich_message=rich_card(reply.card), reply_markup=markup(reply)
                )
            except TelegramAPIError:
                log.warning("rich message refused, sending the card as text", exc_info=True)
        try:
            return await self.bot.send_message(chat_id, reply.text, reply_markup=markup(reply))
        except TelegramBadRequest:
            if not any(b.style for row in reply.buttons for b in row):
                raise
            log.warning("buttons with colours refused, sending plain ones", exc_info=True)
            plain = markup(reply, styles=False)
            return await self.bot.send_message(chat_id, reply.text, reply_markup=plain)

    async def _replace(self, message: Message, reply: Reply) -> bool:
        """The wizard's next screen in place of the pressed message."""
        if message.text is None:  # a card or a venue cannot become a text screen
            return False
        try:
            await message.edit_text(reply.text, reply_markup=markup(reply))
        except TelegramBadRequest:
            try:
                await message.edit_text(reply.text, reply_markup=markup(reply, styles=False))
            except TelegramBadRequest:
                log.debug("could not replace the pressed message", exc_info=True)
                return False
        return True

    async def _mark_pressed(self, message: Message, label: str | None) -> None:
        """Show what was chosen and drop the buttons, so they cannot be pressed twice."""
        try:
            if message.text is not None:
                suffix = f"\n\n— {label}" if label else ""
                await message.edit_text(message.text + suffix, reply_markup=None)
            else:  # a rich card: keep it, only remove the buttons
                await message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:  # editing is cosmetic (old or unchanged message)
            log.debug("could not edit the message with buttons", exc_info=True)
        self._screens.pop(message.chat.id, None)

    async def _strip_screen(self, chat_id: int) -> None:
        message_id = self._screens.pop(chat_id, None)
        if message_id is None:
            return
        try:
            await self.bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=message_id, reply_markup=None
            )
        except TelegramBadRequest:
            log.debug("could not remove buttons of an old screen", exc_info=True)


class Drafts:
    """A live draft of an answer (sendMessageDraft); off for good after the first refusal."""

    def __init__(self, bot: Bot, enabled: bool = True) -> None:
        self.bot = bot
        self.enabled = enabled

    def sink(self, chat_id: int) -> DraftSink | None:
        if not self.enabled:
            return None
        draft_id = random.randint(1, 2**31 - 1)
        sent_at, sent_len = 0.0, 0

        async def send(text: str) -> None:
            nonlocal sent_at, sent_len
            now = time.monotonic()
            if not self.enabled or now - sent_at < DRAFT_EVERY:
                return
            if len(text) - sent_len < DRAFT_MIN_GROWTH:
                return
            sent_at, sent_len = now, len(text)
            try:
                await self.bot.send_message_draft(chat_id=chat_id, draft_id=draft_id, text=text)
            except TelegramAPIError:
                log.warning("sendMessageDraft refused: drafts are off, «печатает…» stays")
                self.enabled = False

        return send


class AllowedUsers(BaseMiddleware):
    """Closed test: ALLOWED_USER_IDS and users approved with «Разрешить» in the admin chat."""

    def __init__(self, access: Access, delivery: Callable[..., Awaitable[None]]) -> None:
        self.access = access
        self.delivery = delivery

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is None or await self.access.allowed(user.id):
            return await handler(event, data)
        if isinstance(event, CallbackQuery):
            await event.answer(texts.NOT_ALLOWED, show_alert=True)
        elif isinstance(event, Message):
            await self.delivery(await self.access.refuse(user_ref(event)), event.chat.id)
        return None


def _pressed_label(callback: CallbackQuery) -> str | None:
    message = callback.message
    keyboard = getattr(message, "reply_markup", None)
    for row in getattr(keyboard, "inline_keyboard", None) or []:
        for button in row:
            if button.callback_data == callback.data:
                return button.text
    return None


def bot_commands(settings: Settings) -> list[BotCommand]:
    commands = [BotCommand(command=c, description=d) for c, d in texts.COMMANDS]
    if settings.demo_mode:
        command, description = texts.DEMO_COMMAND
        commands.append(BotCommand(command=command, description=description))
    return commands


def bot_descriptions(settings: Settings, clinic: ClinicData) -> tuple[str, str]:
    """The bot's description (empty chat) and short description (profile, ≤ 120 characters)."""
    description = texts.BOT_DESCRIPTION.format(address=clinic.contacts.trauma_center.address)
    short = texts.BOT_SHORT_DESCRIPTION
    if settings.is_demo_booking:
        description += "\n\n" + texts.BOT_DEMO_NOTE
        short = f"{texts.BOT_DEMO_NOTE} {short}"
    return description[:512], short[:120]


async def setup_profile(bot: Bot, settings: Settings, clinic: ClinicData) -> None:
    """Commands in the menu, description and short description. Avatar — by the customer."""
    description, short = bot_descriptions(settings, clinic)
    for name, call in (
        ("commands", bot.set_my_commands(bot_commands(settings))),
        ("description", bot.set_my_description(description=description)),
        ("short description", bot.set_my_short_description(short_description=short)),
    ):
        try:
            await call
        except TelegramAPIError:
            log.warning("could not set the bot %s", name, exc_info=True)


def build_dispatcher(
    settings: Settings,
    conversation: Conversation,
    delivery: Delivery,
    access: Access,
    drafts: Drafts | None = None,
) -> Dispatcher:
    dp = Dispatcher()
    admin = Router(name="admin")
    patients = Router(name="patients")
    patients.message.filter(F.chat.type == "private")
    patients.message.outer_middleware(AllowedUsers(access, delivery))
    patients.callback_query.outer_middleware(AllowedUsers(access, delivery))

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
            if isinstance(turn, str):  # e.g. a crisis note: nothing goes to the patient
                await message.reply(turn)
                return
            await delivery(turn)
            await message.reply("Ответ отправлен пациенту.")

        @admin.message(F.chat.id == settings.admin_chat_id, Command("today"))
        async def today(message: Message) -> None:
            await message.reply(await conversation.admin_today())

        @admin.callback_query(
            F.message.chat.id == settings.admin_chat_id, F.data.startswith(CLOSE_PREFIX)
        )
        async def close(callback: CallbackQuery) -> None:
            ref = (callback.data or "").removeprefix(CLOSE_PREFIX)
            by = callback.from_user.full_name or str(callback.from_user.id)
            closed = await conversation.close_handoff(int(ref), by) if ref.isdigit() else False
            await callback.answer("Закрыто" if closed else "Уже закрыто")
            message = callback.message
            if isinstance(message, Message):
                note = texts.ADMIN_CLOSED.format(by=by)
                try:
                    await message.edit_text(f"{message.text or ''}\n\n{note}", reply_markup=None)
                except TelegramBadRequest:
                    log.debug("could not mark the request as closed", exc_info=True)

        @admin.callback_query(
            F.message.chat.id == settings.admin_chat_id, F.data.startswith(ALLOW_PREFIX)
        )
        async def allow(callback: CallbackQuery) -> None:
            user_id = (callback.data or "").removeprefix(ALLOW_PREFIX)
            by = callback.from_user.full_name or str(callback.from_user.id)
            turn = await access.approve(int(user_id), by) if user_id.isdigit() else None
            await callback.answer("Готово" if turn else "Запрос не найден")
            if turn is None:
                return
            message = callback.message
            if isinstance(message, Message):
                note = APPROVED_NOTE.format(by=by)
                await message.edit_text(f"{message.text or ''}\n\n{note}", reply_markup=None)
            await delivery(turn)

    @patients.message(CommandStart())
    async def start(message: Message) -> None:
        await delivery(await conversation.start(user_ref(message)), message.chat.id)

    @patients.message(Command("menu"))
    async def menu(message: Message) -> None:
        await delivery(await conversation.menu(user_ref(message)), message.chat.id)

    @patients.message(Command("book", "ask", "my"))
    async def menu_command(message: Message) -> None:
        item = (message.text or "").split()[0].lstrip("/").split("@")[0]
        await delivery(await conversation.command(user_ref(message), item), message.chat.id)

    @patients.message(Command("admin"))
    async def call_admin(message: Message) -> None:
        await delivery(await conversation.call_admin(user_ref(message)), message.chat.id)

    @patients.message(Command("forget"))
    async def forget(message: Message) -> None:
        await delivery(await conversation.forget(user_ref(message)), message.chat.id)

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
    async def text(message: Message, bot: Bot) -> None:
        assert message.text is not None
        chat_id = message.chat.id
        draft = drafts.sink(chat_id) if drafts is not None else None
        async with ChatActionSender.typing(bot=bot, chat_id=chat_id):  # «печатает…»
            turn = await conversation.text(user_ref(message), message.text, draft=draft)
        await delivery(turn, chat_id)

    @patients.message()
    async def other(message: Message) -> None:  # a photo, a voice message, a sticker
        await delivery(await conversation.other(user_ref(message)), message.chat.id)

    @patients.callback_query()
    async def button(callback: CallbackQuery) -> None:
        await callback.answer()
        message = callback.message
        pressed = message if isinstance(message, Message) else None
        turn = await conversation.button(user_ref(callback), callback.data or "")
        await delivery(turn, callback.from_user.id, pressed=pressed, label=_pressed_label(callback))

    dp.include_router(admin)
    dp.include_router(patients)
    return dp
