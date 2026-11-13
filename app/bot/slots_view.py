"""Day and time screens for booking: a day, then the part of the day, then a grid of times.

«Утро 8–12», «День 12–18», «Вечер 18–23», «Ночь 23–8» — only parts with free time are
offered. «Ночь» of a calendar day is its 00:00–08:00 and 23:00–24:00.

Pure functions over slots already filtered by the 04-09 rules (app/schedule/rules.py).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.bot import texts
from app.config import ScheduleConfig, WorkingHours
from app.flow.schema import SlotPolicy
from app.schedule.base import Slot
from app.schedule.rules import window_distance


@dataclass(frozen=True)
class Period:
    id: str
    label: str
    hours: WorkingHours


PERIODS = (
    Period("morning", texts.PERIOD_MORNING, WorkingHours(start=time(8), end=time(12))),
    Period("day", texts.PERIOD_DAY, WorkingHours(start=time(12), end=time(18))),
    Period("evening", texts.PERIOD_EVENING, WorkingHours(start=time(18), end=time(23))),
    Period("night", texts.PERIOD_NIGHT, WorkingHours(start=time(23), end=time(8))),
)
PERIOD_IDS = frozenset(p.id for p in PERIODS)

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


def period_of(moment: time) -> Period:
    return next(p for p in PERIODS if p.hours.contains(moment))


def periods_with(slots: Sequence[Slot], tz: ZoneInfo) -> list[Period]:
    """Parts of the day that have free time, in the order of the day (night last)."""
    found = {period_of(local(s, tz).time()).id for s in slots}
    return [p for p in PERIODS if p.id in found]


def in_period(slots: Sequence[Slot], period: str | None, tz: ZoneInfo) -> list[Slot]:
    if period is None:
        return list(slots)
    return [s for s in slots if period_of(local(s, tz).time()).id == period]


def nearest_label(slot: Slot, today: date, tz: ZoneInfo) -> str:
    """«Ближайшее: сегодня, 14:30» / «Ближайшее: сб, 14.11, 09:00»."""
    start = local(slot, tz)
    if start.date() == today:
        when = f"{texts.TODAY.lower()}, {start:%H:%M}"
    elif (start.date() - today).days == 1:
        when = f"{texts.TOMORROW.lower()}, {start:%H:%M}"
    else:
        when = f"{texts.WEEKDAYS_SHORT[start.weekday()].lower()}, {start:%d.%m, %H:%M}"
    return texts.NEAREST.format(when=when)


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
