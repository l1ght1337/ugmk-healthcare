"""Which notifications a booking gets and when (docs/spec.md, path steps 8–10).

* confirmation — right away, part of the booking reply;
* day_before — 24 h before: reminder and what to bring;
* two_hours — 2 h before: address, entrance, parking (day or after 20:00);
* after_visit — 2 h after the slot ends: «Как прошёл приём?» with the control visit offered
  at once (after a control visit — «Как Вы себя чувствуете?» and a 1–5 rating). Not at
  night: a moment in the clinic's quiet hours is the morning after.

Reminders whose time has already passed at booking are skipped (the confirmation
already lists the documents).
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, tzinfo
from typing import Protocol

DAY_BEFORE = "day_before"
TWO_HOURS = "two_hours"
AFTER_VISIT = "after_visit"
KINDS = (DAY_BEFORE, TWO_HOURS, AFTER_VISIT)

BEFORE = {DAY_BEFORE: timedelta(hours=24), TWO_HOURS: timedelta(hours=2)}
AFTER = timedelta(hours=2)


class Quiet(Protocol):
    """The hours a message is not sent in (config: unfinished_booking.quiet_hours)."""

    end: time

    def contains(self, moment: time) -> bool: ...


def after_visit_due(
    end: datetime, quiet: Quiet | None = None, tz: tzinfo | None = None
) -> datetime:
    """Two hours after the visit — or, if that is night by the clinic's clock, the morning
    after: the end of the quiet hours."""
    due = end + AFTER
    if quiet is None or tz is None:
        return due
    local = due.astimezone(tz)
    if not quiet.contains(local.time()):
        return due
    morning = local.replace(hour=quiet.end.hour, minute=quiet.end.minute, second=0, microsecond=0)
    if morning <= local:  # 22:00–24:00: the morning of the next day
        morning += timedelta(days=1)
    return morning.astimezone(due.tzinfo)


def plan(
    start: datetime,
    end: datetime,
    now: datetime,
    *,
    quiet: Quiet | None = None,
    tz: tzinfo | None = None,
) -> list[tuple[str, datetime]]:
    reminders = [(kind, start - delta) for kind, delta in BEFORE.items() if start - delta > now]
    reminders.append((AFTER_VISIT, after_visit_due(end, quiet, tz)))
    return reminders
