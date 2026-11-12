"""Entry point: `uv run python -m app`."""

from __future__ import annotations

import logging

from app.config import get_settings

logger = logging.getLogger("app")


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info(
        "Skeleton only: schedule=%s, demo_mode=%s, tz=%s. The Telegram bot arrives in stage 4.",
        settings.schedule_provider,
        settings.demo_mode,
        settings.timezone,
    )


if __name__ == "__main__":
    main()
