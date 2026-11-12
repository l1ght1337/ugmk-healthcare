import pytest
from pydantic import ValidationError

from app.config import MissingSettingError, Settings

ENV_VARS = [
    "TELEGRAM_BOT_TOKEN",
    "ADMIN_CHAT_ID",
    "ALLOWED_USER_IDS",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
    "SCHEDULE_PROVIDER",
    "MEDIALOG_API_URL",
    "MEDIALOG_API_KEY",
    "TIMEZONE",
    "DATABASE_URL",
    "DEMO_MODE",
    "LOG_LEVEL",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def make(**env: str) -> Settings:
    """Build settings from the given variables only, ignoring the real .env file."""
    return Settings(_env_file=None, **{k.lower(): v for k, v in env.items()})


def test_defaults_without_env() -> None:
    s = make()
    assert s.schedule_provider == "mock"
    assert s.is_demo_booking
    assert s.allowed_user_ids == []
    assert s.timezone == "Asia/Yekaterinburg"
    assert s.telegram_bot_token is None


def test_allowed_user_ids_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_USER_IDS", "123, 456;789")
    s = Settings(_env_file=None)
    assert s.allowed_user_ids == [123, 456, 789]
    assert s.user_allowed(456)
    assert not s.user_allowed(1)


def test_empty_allowed_user_ids_means_everyone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_USER_IDS", "")
    s = Settings(_env_file=None)
    assert s.allowed_user_ids == []
    assert s.user_allowed(42)


def test_empty_optional_values_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEDIALOG_API_KEY", "")
    monkeypatch.setenv("ADMIN_CHAT_ID", "")
    s = Settings(_env_file=None)
    assert s.medialog_api_key is None
    assert s.admin_chat_id is None


def test_medialog_is_not_demo_booking() -> None:
    assert not make(SCHEDULE_PROVIDER="medialog").is_demo_booking


def test_require_names_missing_variables() -> None:
    s = make(DEEPSEEK_API_KEY="sk-test")
    s.require("deepseek_api_key")
    with pytest.raises(MissingSettingError, match="TELEGRAM_BOT_TOKEN, ADMIN_CHAT_ID"):
        s.require("telegram_bot_token", "admin_chat_id")


def test_invalid_timezone_rejected() -> None:
    with pytest.raises(ValidationError):
        make(TIMEZONE="Mars/Olympus")
