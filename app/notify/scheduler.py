"""APScheduler job that sends due notifications.

The ``reminders`` table is the source of truth: the job polls it, so a restart loses
nothing and there is nothing to re-create in the scheduler.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.bot.conversation import Conversation, Turn

log = logging.getLogger(__name__)

POLL_SECONDS = 20


def start_scheduler(
    conversation: Conversation,
    deliver: Callable[[Turn], Awaitable[None]],
    *,
    every_seconds: int = POLL_SECONDS,
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")

    async def tick() -> None:
        try:
            for turn in await conversation.due():
                await deliver(turn)
        except Exception:
            log.exception("sending notifications failed; will retry on the next tick")
        try:
            conversation.top_up_schedule()  # cheap: rewrites the CSV only when a day passed
        except Exception:
            log.exception("topping up the demo schedule failed")

    scheduler.add_job(
        tick, "interval", seconds=every_seconds, id="reminders", max_instances=1, coalesce=True
    )
    scheduler.start()
    return scheduler
