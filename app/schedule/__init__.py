"""Schedule providers: mock (data/schedule.csv) and Medialog (stub)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.config import ClinicData, Settings
from app.schedule.base import ScheduleProvider, Slot, SlotUnavailable

__all__ = ["ScheduleProvider", "Slot", "SlotUnavailable", "make_provider"]


def make_provider(
    settings: Settings, clinic: ClinicData, csv_path: Path | None = None
) -> ScheduleProvider:
    """Provider chosen by SCHEDULE_PROVIDER; the mock tops up its CSV to 14 days ahead."""
    if settings.schedule_provider == "medialog":
        from app.schedule.medialog import MedialogSchedule

        settings.require("medialog_api_url", "medialog_api_key")
        assert settings.medialog_api_url and settings.medialog_api_key
        return MedialogSchedule(
            settings.medialog_api_url, settings.medialog_api_key.get_secret_value()
        )

    from app.schedule.generate import SCHEDULE_CSV, ensure_horizon
    from app.schedule.mock import MockSchedule

    path = csv_path or SCHEDULE_CSV
    ensure_horizon(path, datetime.now(settings.tz).date(), clinic.schedule)
    return MockSchedule.from_csv(path, tz=settings.tz, config=clinic.schedule)
