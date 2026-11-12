"""Which slots to offer for a booking (script 04-09 rules).

* ``nearest`` — first visits: the nearest free time. The «Повторный приём» windows and the
  second cabinet are kept for repeat visits.
* ``repeat_window`` — repeat visit, sick leave not open: strictly inside the windows
  8:00–10:00 / 16:00–18:00 or in the second cabinet («Травматолог 2»).
* ``near_repeat_window`` — repeat visit, sick leave open: any free time close to the
  windows. Earliest day first, then by distance to the nearest window.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta

from app.config import ScheduleConfig, WorkingHours
from app.flow.schema import Booking, SlotPolicy
from app.schedule.base import ScheduleProvider, Slot

DEFAULT_LIMIT = 4


def eligible(slot: Slot, policy: SlotPolicy) -> bool:
    if policy == "nearest":
        return not slot.repeat_window and not slot.repeat_cabinet
    if policy == "repeat_window":
        return slot.repeat_window or slot.repeat_cabinet
    if policy == "near_repeat_window":
        return True
    raise ValueError(f"unknown slot policy {policy!r}")


def _minutes(moment: datetime) -> int:
    return moment.hour * 60 + moment.minute


def window_distance(slot: Slot, windows: Sequence[WorkingHours]) -> int:
    """Minutes between the slot and the nearest repeat window of the same day (0 = touching)."""
    start = _minutes(slot.start)
    end = start + int((slot.end - slot.start).total_seconds() // 60)
    best = None
    for w in windows:
        w_start = w.start.hour * 60 + w.start.minute
        w_end = w.end.hour * 60 + w.end.minute
        gap = max(0, w_start - end, start - w_end)
        best = gap if best is None else min(best, gap)
    return best if best is not None else 0


def order_slots(
    slots: Iterable[Slot],
    policy: SlotPolicy,
    *,
    now: datetime,
    windows: Sequence[WorkingHours],
    min_lead: timedelta = timedelta(0),
) -> list[Slot]:
    earliest = now + min_lead
    chosen = [s for s in slots if s.start >= earliest and eligible(s, policy)]
    if policy == "near_repeat_window":
        return sorted(chosen, key=lambda s: (s.start.date(), window_distance(s, windows), s.start))
    return sorted(chosen, key=lambda s: s.start)


async def find_slots(
    provider: ScheduleProvider,
    booking: Booking,
    *,
    now: datetime,
    config: ScheduleConfig,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[Slot]:
    """Slots to offer as buttons; ``offset`` pages through «другое время»."""
    horizon = now + timedelta(days=config.demo.days_ahead)
    free = await provider.free_slots(now, horizon)
    ordered = order_slots(
        free,
        booking.slot_policy,
        now=now,
        windows=config.repeat_windows,
        min_lead=timedelta(minutes=config.demo.min_lead_minutes),
    )
    return ordered[offset : offset + limit]
