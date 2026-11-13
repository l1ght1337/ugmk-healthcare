"""Schedule provider interface: the mock (data/schedule.csv) now, «Медиалог» later."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Slot:
    id: str
    start: datetime  # aware, clinic time zone
    end: datetime
    doctor: str
    cabinet: str
    repeat_window: bool  # inside the «Повторный приём» exclusive event (04-09)
    repeat_cabinet: bool  # the second cabinet for repeat visits, «Травматолог 2» (04-09)


class SlotUnavailable(Exception):
    """The slot is unknown or already taken."""


class ScheduleProvider(ABC):
    @abstractmethod
    async def free_slots(self, start: datetime, end: datetime) -> list[Slot]:
        """Free slots starting in [start, end), ordered by start."""

    @abstractmethod
    async def slots(self, start: datetime, end: datetime) -> list[Slot]:
        """Every slot starting in [start, end), free or taken: who is on duty and when."""

    @abstractmethod
    async def get(self, slot_id: str) -> Slot | None: ...

    @abstractmethod
    async def book(self, slot_id: str) -> Slot:
        """Take a free slot or raise SlotUnavailable."""

    @abstractmethod
    async def release(self, slot_id: str) -> None:
        """Free a slot taken through ``book`` (cancel or reschedule)."""
