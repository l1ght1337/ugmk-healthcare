"""Mock schedule on top of data/schedule.csv.

Busy flags in the CSV stand for other patients and never change. Bookings made through
the bot are kept in memory; on startup the bot restores them from its own bookings table
(``restore``), so the CSV stays a read-only fixture.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import ScheduleConfig
from app.schedule.base import ScheduleProvider, Slot, SlotUnavailable
from app.schedule.generate import Row, read_csv


class MockSchedule(ScheduleProvider):
    def __init__(self, rows: Iterable[Row], *, tz: ZoneInfo, slot_minutes: int) -> None:
        self._slots: dict[str, Slot] = {}
        self._busy: set[str] = set()
        self._taken: set[str] = set()
        length = timedelta(minutes=slot_minutes)
        for row in rows:
            start = datetime.combine(row.day, row.start, tzinfo=tz)
            self._slots[row.slot_id] = Slot(
                id=row.slot_id,
                start=start,
                end=start + length,
                doctor=row.doctor,
                cabinet=row.cabinet,
                repeat_window=row.repeat_window,
                repeat_cabinet=row.repeat_cabinet,
            )
            if row.busy:
                self._busy.add(row.slot_id)

    @classmethod
    def from_csv(cls, path: Path, *, tz: ZoneInfo, config: ScheduleConfig) -> MockSchedule:
        return cls(read_csv(path), tz=tz, slot_minutes=config.demo.slot_minutes)

    def restore(self, taken: Iterable[str]) -> None:
        """Mark slots booked earlier (from the bot's DB) as taken."""
        self._taken.update(s for s in taken if s in self._slots)

    def _is_free(self, slot_id: str) -> bool:
        return slot_id not in self._busy and slot_id not in self._taken

    async def free_slots(self, start: datetime, end: datetime) -> list[Slot]:
        found = [s for s in self._slots.values() if start <= s.start < end and self._is_free(s.id)]
        return sorted(found, key=lambda s: (s.start, s.repeat_cabinet))

    async def get(self, slot_id: str) -> Slot | None:
        return self._slots.get(slot_id)

    async def book(self, slot_id: str) -> Slot:
        slot = self._slots.get(slot_id)
        if slot is None or not self._is_free(slot_id):
            raise SlotUnavailable(slot_id)
        self._taken.add(slot_id)
        return slot

    async def release(self, slot_id: str) -> None:
        self._taken.discard(slot_id)
