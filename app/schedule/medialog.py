"""«Медиалог» schedule provider — placeholder until the clinic gives API access.

TODO (docs/clinic_requests.md, item 7): real schedule, booking and the «приём состоялся»
status. Until then SCHEDULE_PROVIDER must stay ``mock``.
"""

from __future__ import annotations

from datetime import datetime

from app.schedule.base import ScheduleProvider, Slot

NOT_AVAILABLE = "No Medialog API access yet (TODO from the clinic); use SCHEDULE_PROVIDER=mock"


class MedialogSchedule(ScheduleProvider):
    def __init__(self, api_url: str, api_key: str) -> None:
        self.api_url = api_url
        self.api_key = api_key

    async def free_slots(self, start: datetime, end: datetime) -> list[Slot]:
        raise NotImplementedError(NOT_AVAILABLE)

    async def get(self, slot_id: str) -> Slot | None:
        raise NotImplementedError(NOT_AVAILABLE)

    async def book(self, slot_id: str) -> Slot:
        raise NotImplementedError(NOT_AVAILABLE)

    async def release(self, slot_id: str) -> None:
        raise NotImplementedError(NOT_AVAILABLE)
