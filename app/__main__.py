"""Entry point: `uv run python -m app` — the Telegram bot (long polling)."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand

from app.bot.conversation import Conversation
from app.bot.telegram import Delivery, build_dispatcher
from app.config import get_clinic_data, get_settings
from app.db.session import Database
from app.flow.loader import get_kb
from app.llm import make_assistant
from app.notify.scheduler import start_scheduler
from app.schedule import make_provider

log = logging.getLogger("app")


async def run() -> None:
    settings = get_settings()
    settings.require("telegram_bot_token", "admin_chat_id")
    kb, clinic = get_kb(), get_clinic_data()

    db = Database(settings.database_url)
    await db.init()
    schedule = make_provider(settings, clinic)
    assistant = make_assistant(settings, kb)
    conversation = Conversation(
        kb=kb, clinic=clinic, settings=settings, db=db, schedule=schedule, assistant=assistant
    )
    await conversation.restore_schedule()

    assert settings.telegram_bot_token is not None
    bot = Bot(
        settings.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(link_preview_is_disabled=True),
    )
    delivery = Delivery(bot, conversation, settings.admin_chat_id)
    dispatcher = build_dispatcher(settings, conversation, delivery)
    scheduler = start_scheduler(conversation, delivery)

    warm_up = None
    if assistant is not None:  # fill the DeepSeek cache before the first patient writes
        warm_up = asyncio.create_task(assistant.warm_up())
        warm_up.add_done_callback(lambda t: log.info("DeepSeek warm-up ok: %s", t.result()))

    commands = [
        BotCommand(command="start", description="Начать"),
        BotCommand(command="menu", description="Меню"),
        BotCommand(command="admin", description="Позвать администратора"),
    ]
    if settings.demo_mode:
        commands.append(BotCommand(command="demo_next", description="Демо: следующее уведомление"))
    await bot.set_my_commands(commands)

    log.info(
        "Bot started: schedule=%s (demo bookings: %s), demo_mode=%s, LLM=%s, allowed users=%s",
        settings.schedule_provider,
        settings.is_demo_booking,
        settings.demo_mode,
        "on" if assistant else "off (buttons only)",
        len(settings.allowed_user_ids) or "all",
    )
    try:
        await dispatcher.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        await db.close()


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
