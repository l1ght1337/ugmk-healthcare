"""Application settings, read from environment variables and the project's `.env` file.

All configuration goes through this module (see CLAUDE.md). Secrets are optional at
load time so that tests and tooling work without a `.env`; components that need a
secret call `Settings.require(...)` and get a clear error naming the missing variable.
"""

from __future__ import annotations

import re
from datetime import time
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
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
    # Hard limit for one LLM call; after it the bot shows buttons instead of waiting.
    llm_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    # Token usage per call (no texts), for the clinic's cost estimate. Empty — log only.
    llm_usage_log: Path | None = PROJECT_ROOT / "data" / "llm_usage.jsonl"

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


# --- clinic data from config/*.yaml (values missing from the scripts are TODO/null) -------

CONFIG_DIR = PROJECT_ROOT / "config"


class _Data(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkingHours(_Data):
    start: time
    end: time

    def contains(self, moment: time) -> bool:
        if self.start <= self.end:
            return self.start <= moment < self.end
        return moment >= self.start or moment < self.end


class TraumaCenter(_Data):
    address: str
    entrance: str
    hours: str
    underground_parking_hours: WorkingHours


class Contacts(_Data):
    trauma_center: TraumaCenter
    call_center_phone: str | None
    admin_working_hours: WorkingHours
    privacy_policy_url: str | None


class Emergency(_Data):
    reply: str
    phrases: tuple[str, ...] = Field(min_length=1)

    @field_validator("phrases")
    @classmethod
    def _phrases_not_words(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        single = [p for p in value if len(p.split()) < 2]
        if single:
            raise ValueError(f"emergency triggers must be phrases, not single words: {single}")
        return value


class ClinicFlags(_Data):
    rabies_vaccine_available: bool
    emergency: Emergency


class DemoSchedule(_Data):
    """Assumptions of the mock schedule; none of these are in the scripts (TODO)."""

    slot_minutes: int = Field(gt=0, le=240)
    main_cabinet: str
    repeat_cabinet: str
    repeat_cabinet_hours: WorkingHours
    busy_share: float = Field(ge=0, lt=1)
    days_ahead: int = Field(gt=0, le=60)
    min_lead_minutes: int = Field(ge=0)


class ScheduleConfig(_Data):
    doctors: tuple[str, ...] = Field(min_length=3)
    repeat_windows: tuple[WorkingHours, ...] = Field(min_length=1)
    demo: DemoSchedule

    @field_validator("repeat_windows")
    @classmethod
    def _same_day_windows(cls, value: tuple[WorkingHours, ...]) -> tuple[WorkingHours, ...]:
        if any(w.start >= w.end for w in value):
            raise ValueError("repeat windows must not cross midnight")
        return value


class ClinicData(_Data):
    prices: dict[str, int | None]
    contacts: Contacts
    clinic: ClinicFlags
    schedule: ScheduleConfig

    def price_text(self, key: str) -> str | None:
        """Price in roubles for patient texts, or None if the clinic has not given it yet."""
        value = self.prices.get(key)
        return f"{value} рублей" if value is not None else None


def _read_yaml(path: Path) -> object:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_clinic_data(config_dir: Path = CONFIG_DIR) -> ClinicData:
    return ClinicData(
        prices=_read_yaml(config_dir / "prices.yaml"),
        contacts=_read_yaml(config_dir / "contacts.yaml"),
        clinic=_read_yaml(config_dir / "clinic.yaml"),
        schedule=_read_yaml(config_dir / "schedule.yaml"),
    )


@lru_cache(maxsize=1)
def get_clinic_data() -> ClinicData:
    return load_clinic_data()
