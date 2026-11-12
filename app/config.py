"""Application settings, read from environment variables and the project's `.env` file.

All configuration goes through this module (see CLAUDE.md). Secrets are optional at
load time so that tests and tooling work without a `.env`; components that need a
secret call `Settings.require(...)` and get a clear error naming the missing variable.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class MissingSettingError(RuntimeError):
    """Raised when a component needs a setting that is not configured."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    # Telegram
    telegram_bot_token: SecretStr | None = None
    admin_chat_id: int | None = None
    # Telegram user IDs allowed to use the bot; empty means everyone.
    allowed_user_ids: Annotated[list[int], NoDecode] = []

    # DeepSeek (OpenAI-compatible API)
    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    # Schedule
    schedule_provider: Literal["mock", "medialog"] = "mock"
    medialog_api_url: str | None = None
    medialog_api_key: SecretStr | None = None

    # General
    timezone: str = "Asia/Yekaterinburg"
    database_url: str = "sqlite+aiosqlite:///data/bot.db"
    demo_mode: bool = False
    log_level: str = "INFO"

    @field_validator("allowed_user_ids", mode="before")
    @classmethod
    def _split_user_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(part) for part in re.split(r"[,\s;]+", value) if part]
        if isinstance(value, int):
            return [value]
        return value

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone: {value!r}") from exc
        return value

    @property
    def tz(self) -> ZoneInfo:
        """Clinic time zone. Times are shown in it and stored in UTC."""
        return ZoneInfo(self.timezone)

    @property
    def is_demo_booking(self) -> bool:
        """Bookings go to the mock schedule, so patients must be told they are not real."""
        return self.schedule_provider == "mock"

    def user_allowed(self, user_id: int) -> bool:
        return not self.allowed_user_ids or user_id in self.allowed_user_ids

    def require(self, *fields: str) -> None:
        """Fail with a readable message if any of the given settings is empty."""
        missing = [name.upper() for name in fields if getattr(self, name) in (None, "")]
        if missing:
            raise MissingSettingError(
                "Missing settings in .env: " + ", ".join(missing) + " (template: .env.example)"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
