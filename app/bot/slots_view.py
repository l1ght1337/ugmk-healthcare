"""Day and time screens for booking: a day, then the part of the day, then a grid of times.

«Утро 8–12», «День 12–18», «Вечер 18–23», «Ночь 23–8» — only parts with free time are
offered. «Ночь» of a calendar day is its 00:00–08:00 and 23:00–24:00.

Pure functions over slots already filtered by the 04-09 rules (app/schedule/rules.py).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.bot import texts
from app.config import ScheduleConfig, WorkingHours
from app.flow.schema import SlotPolicy
from app.flow.wishes import Wish
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
NOT_BEFORE = time(6)  # «до 12» is about the morning, not the night before it


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


def nearest_when(slot: Slot, today: date, tz: ZoneInfo) -> str:
    """«сегодня, 14:30» / «сб, 14.11, 09:00»."""
    start = local(slot, tz)
    if start.date() == today:
        return f"{texts.TODAY.lower()}, {start:%H:%M}"
    if (start.date() - today).days == 1:
        return f"{texts.TOMORROW.lower()}, {start:%H:%M}"
    return f"{texts.WEEKDAYS_SHORT[start.weekday()].lower()}, {start:%d.%m, %H:%M}"


def nearest_label(slot: Slot, today: date, tz: ZoneInfo) -> str:
    """«Ближайшее: сегодня, 14:30» / «Ближайшее: сб, 14.11, 09:00»."""
    return texts.NEAREST.format(when=nearest_when(slot, today, tz))


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


# --- the wish as a filter over the slots the 04-09 rules allow ----------------------------


def fits(slot: Slot, wish: Wish, tz: ZoneInfo) -> bool:
    """The doctor and the time of day of the wish; the day is where the picker opens."""
    if wish.doctor and slot.doctor != wish.doctor:
        return False
    moment = local(slot, tz).time()
    if wish.period and period_of(moment).id != wish.period:
        return False
    if wish.start and moment < wish.start:
        return False
    if wish.end:
        if moment >= wish.end:
            return False
        if wish.start is None and wish.end > NOT_BEFORE and moment < NOT_BEFORE:
            return False
    return True


def matching(slots: Sequence[Slot], wish: Wish | None, tz: ZoneInfo) -> list[Slot]:
    return [s for s in slots if wish is None or fits(s, wish, tz)]


def on_day(slots: Sequence[Slot], day: date, tz: ZoneInfo) -> list[Slot]:
    return [s for s in slots if local(s, tz).date() == day]


def closest(slots: Sequence[Slot], moment: time, tz: ZoneInfo) -> Slot | None:
    """The time nearest to «в 14:30» among the offered ones."""

    def gap(slot: Slot) -> int:
        start = local(slot, tz).time()
        return abs(start.hour * 60 + start.minute - moment.hour * 60 - moment.minute)

    return min(slots, key=lambda s: (gap(s), s.start)) if slots else None


# --- who is on duty: every slot of a day, free or taken, as spans --------------------------


def _span_text(start: datetime, end: datetime) -> str:
    """«08:00–20:00», «с 20:00» (till midnight), «до 08:00» (from midnight)."""
    from_midnight = start.time() == time(0)
    till_midnight = end.time() == time(0) and end.date() > start.date()
    if from_midnight and till_midnight:
        return texts.SPAN_ALL_DAY
    if from_midnight:
        return texts.SHIFT_UNTIL.format(time=f"{end:%H:%M}")
    if till_midnight:
        return texts.SHIFT_FROM.format(time=f"{start:%H:%M}")
    return f"{start:%H:%M}–{end:%H:%M}"


def spans(slots: Sequence[Slot], tz: ZoneInfo) -> list[tuple[datetime, str]]:
    """Slots that follow one another, merged: [(start, «08:00–20:00»), …] in time order."""
    merged: list[list[datetime]] = []
    for slot in sorted(slots, key=lambda s: s.start):
        start, end = local(slot, tz), slot.end.astimezone(tz)
        if merged and merged[-1][1] >= start:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, _span_text(start, end)) for start, end in merged]


def shifts_text(day_slots: Sequence[Slot], config: ScheduleConfig, tz: ZoneInfo) -> list[str]:
    """«• 08:00–20:00 — Ярунин …» for a day, then the cabinet for repeat visits."""
    main: list[tuple[datetime, str, str]] = []
    doctors = list(dict.fromkeys(s.doctor for s in day_slots))
    for doctor in doctors:
        own = [s for s in day_slots if s.doctor == doctor and not s.repeat_cabinet]
        main += [(start, span, doctor) for start, span in spans(own, tz)]
    lines = [texts.SHIFT_LINE.format(span=span, doctor=doctor) for _, span, doctor in sorted(main)]
    for doctor in doctors:
        second = [s for s in day_slots if s.doctor == doctor and s.repeat_cabinet]
        for _, span in spans(second, tz):
            lines.append(
                texts.SHIFTS_REPEAT.format(
                    cabinet=config.demo.repeat_cabinet, doctor=doctor, span=span
                )
            )
    return lines


def doctor_spans(day_slots: Sequence[Slot], doctor: str, tz: ZoneInfo) -> str | None:
    """«08:00–20:00» or «08:00–20:00, повторные приёмы» for one doctor on one day."""
    parts = [span for _, span in spans([s for s in day_slots if s.doctor == doctor], tz)]
    if not parts:
        return None
    own = [s for s in day_slots if s.doctor == doctor]
    text = ", ".join(parts)
    return texts.REPEAT_ONLY.format(span=text) if all(s.repeat_cabinet for s in own) else text


def day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0), tzinfo=tz)
    return start, start + timedelta(days=1)
