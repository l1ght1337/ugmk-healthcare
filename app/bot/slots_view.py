"""Day and time screens for booking (part C): first a day, then a grid of times.

Pure functions over slots already filtered by the 04-09 rules (app/schedule/rules.py).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.bot import texts
from app.config import ScheduleConfig, WorkingHours
from app.flow.schema import SlotPolicy
from app.schedule.base import Slot
from app.schedule.rules import window_distance

DAYS_PER_PAGE = 6
NEAR_WINDOW_TIMES = 8  # sick leave open: «любое свободное время, близкое к» the windows


def local(slot: Slot, tz: ZoneInfo) -> datetime:
    return slot.start.astimezone(tz)


def days_with_slots(slots: Sequence[Slot], tz: ZoneInfo) -> list[date]:
    return sorted({local(s, tz).date() for s in slots})


def page_of_days(days: Sequence[date], start: date | None) -> list[date]:
    if not days:
        return []
    first = start if start is not None else days[0]
    page = [d for d in days if d >= first][:DAYS_PER_PAGE]
    return page or list(days[:DAYS_PER_PAGE])


def earlier_page_start(days: Sequence[date], page: Sequence[date]) -> date | None:
    before = [d for d in days if d < page[0]]
    return before[-DAYS_PER_PAGE:][0] if before else None


def later_page_start(days: Sequence[date], page: Sequence[date]) -> date | None:
    after = [d for d in days if d > page[-1]]
    return after[0] if after else None


def nearest_day(days: Sequence[date], target: date) -> date | None:
    """The control visit: the day closest to «about 10 days later», later days first."""
    if not days:
        return None
    return min(days, key=lambda d: (abs((d - target).days), d < target))


def times_of_day(
    slots: Sequence[Slot],
    day: date,
    policy: SlotPolicy,
    windows: Sequence[WorkingHours],
    tz: ZoneInfo,
) -> list[Slot]:
    """One button per start time (two cabinets may be free at once), in time order."""
    by_time: dict[datetime, Slot] = {}
    day_slots = (s for s in slots if local(s, tz).date() == day)
    for s in sorted(day_slots, key=lambda s: (s.start, s.repeat_cabinet)):
        by_time.setdefault(s.start, s)
    shown = list(by_time.values())
    if policy == "near_repeat_window":
        shown = sorted(shown, key=lambda s: (window_distance(s, windows), s.start))
        shown = shown[:NEAR_WINDOW_TIMES]
    return sorted(shown, key=lambda s: s.start)


def _shift_span(moment: datetime, config: ScheduleConfig) -> str:
    shift = config.demo.day_shift
    start, end = f"{shift.start:%H:%M}", f"{shift.end:%H:%M}"
    if shift.contains(moment.time()):
        return f"{start}–{end}"
    if moment.time() >= shift.end:
        return texts.SHIFT_FROM.format(time=end)
    return texts.SHIFT_UNTIL.format(time=start)


def doctors_text(
    slots: Sequence[Slot], all_day: Sequence[Slot], config: ScheduleConfig, tz: ZoneInfo
) -> str:
    """«Врач смены: Кузнецов Николай Александрович (08:00–20:00)» above the time grid.

    ``all_day`` are the day's free slots before de-duplication: a time may be served by
    the second cabinet too, and its doctor is named as well.
    """
    shown = {s.start for s in slots}
    main: list[tuple[str, str]] = []
    repeat: list[str] = []
    for s in sorted(all_day, key=lambda s: s.start):
        if s.start not in shown:
            continue
        if s.repeat_cabinet:
            if s.doctor not in repeat:
                repeat.append(s.doctor)
            continue
        entry = (s.doctor, _shift_span(local(s, tz), config))
        if entry not in main:
            main.append(entry)
    lines = []
    if len(main) == 1:
        doctor, span = main[0]
        lines.append(texts.SHIFT_DOCTOR.format(doctor=doctor, span=span))
    elif main:
        lines.append(texts.SHIFT_DOCTORS)
        lines += [f"• {span} — {doctor}" for doctor, span in main]
    for doctor in repeat:
        lines.append(
            texts.REPEAT_CABINET_DOCTOR.format(cabinet=config.demo.repeat_cabinet, doctor=doctor)
        )
    return "\n".join(lines)
