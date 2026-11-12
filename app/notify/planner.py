"""Which notifications a booking gets and when (docs/spec.md, path steps 8–10).

* confirmation — right away, part of the booking reply;
* day_before — 24 h before: reminder and what to bring;
* two_hours — 2 h before: address, entrance, parking (day or after 20:00);
* after_visit — 3 h after the slot ends: «Как прошёл приём?» (after a control visit —
  «Как Вы себя чувствуете?» and a 1–5 rating).

Reminders whose time has already passed at booking are skipped (the confirmation
already lists the documents).
"""

from __future__ import annotations

from datetime import datetime, timedelta

DAY_BEFORE = "day_before"
TWO_HOURS = "two_hours"
AFTER_VISIT = "after_visit"
KINDS = (DAY_BEFORE, TWO_HOURS, AFTER_VISIT)

BEFORE = {DAY_BEFORE: timedelta(hours=24), TWO_HOURS: timedelta(hours=2)}
AFTER = timedelta(hours=3)


def plan(start: datetime, end: datetime, now: datetime) -> list[tuple[str, datetime]]:
    reminders = [(kind, start - delta) for kind, delta in BEFORE.items() if start - delta > now]
    reminders.append((AFTER_VISIT, end + AFTER))
    return reminders
