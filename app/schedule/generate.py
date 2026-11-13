"""Generate the demo schedule data/schedule.csv (no access to «Медиалог» yet).

Doctors come from script 03-1 (the list the clinic confirmed on 13.11.2026) and the
«Повторный приём» windows from 04-09 (config/schedule.yaml). The clinic said one doctor is
on duty 8:00–20:00 and the next one 20:00–8:00; in the demo the shifts rotate between the
four doctors. Everything else — slot length, the second cabinet's hours, how busy each hour
is — is a demo assumption, and the file says so in its first line.

    uv run python -m app.schedule.generate            # 21 days from today
    uv run python -m app.schedule.generate --start 2026-11-12 --days 21
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path

from app.config import PROJECT_ROOT, ScheduleConfig, get_clinic_data, get_settings

SCHEDULE_CSV = PROJECT_ROOT / "data" / "schedule.csv"
HEADER_NOTE = (
    "# Демо-расписание травмпункта: время и занятость вымышленные, это не расписание клиники. "
    "Создано app/schedule/generate.py, конфигурация {fingerprint}"
)
COLUMNS = (
    "slot_id",
    "date",
    "start",
    "end",
    "cabinet",
    "doctor",
    "repeat_window",
    "repeat_cabinet",
    "busy",
)
DEFAULT_SEED = "ugmk-demo"


@dataclass(frozen=True)
class Row:
    slot_id: str
    day: date
    start: time
    end: time
    cabinet: str
    doctor: str
    repeat_window: bool
    repeat_cabinet: bool
    busy: bool


def fingerprint(config: ScheduleConfig, seed: str = DEFAULT_SEED) -> str:
    """Changes when anything that shapes the rows changes (not the horizon length)."""
    data = config.model_dump(mode="json")
    data["demo"].pop("days_ahead")
    raw = json.dumps([data, seed], sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(raw.encode()).hexdigest()[:10]


def _in_window(moment: time, config: ScheduleConfig) -> bool:
    return any(w.contains(moment) for w in config.repeat_windows)


def shift_doctor(day: date, moment: time, config: ScheduleConfig) -> str:
    """The doctor on duty in the main cabinet.

    Four-day cycle with four doctors: day shift → «Травматолог 2» next day → night shift the
    day after → rest. Day shift on day k: doctor k; night shift: doctor k + 2.
    """
    doctors, shift = config.doctors, config.demo.day_shift
    k, night = day.toordinal(), len(config.doctors) // 2
    if shift.contains(moment):
        return doctors[k % len(doctors)]
    if moment >= shift.end:
        return doctors[(k + night) % len(doctors)]
    return doctors[(k - 1 + night) % len(doctors)]  # after midnight: yesterday's night shift


def repeat_doctor(day: date, config: ScheduleConfig) -> str:
    """«Травматолог 2»: a doctor who is neither on duty today nor coming off last night's shift."""
    busy = {shift_doctor(day, t, config) for t in (time(0), time(12), time(23))}
    free = [d for d in config.doctors if d not in busy] or list(config.doctors)
    return free[day.toordinal() % len(free)]


def _mark_busy(rows: list[Row], config: ScheduleConfig, seed: str) -> list[Row]:
    """Exactly round(share × n) busy slots per day, cabinet and hour band — no random drift."""
    groups: dict[tuple[date, str, int], list[int]] = defaultdict(list)
    bands = config.demo.busy_by_hour
    for i, r in enumerate(rows):
        band = next(n for n, b in enumerate(bands) if b.contains(r.start))
        groups[(r.day, r.cabinet, band)].append(i)
    busy: set[int] = set()
    for (day, cabinet, band), indexes in groups.items():
        k = round(bands[band].share * len(indexes))
        rnd = random.Random(f"{seed}:{day.isoformat()}:{cabinet}:{band}")
        busy.update(rnd.sample(indexes, k))
    return [replace(r, busy=i in busy) for i, r in enumerate(rows)]


def generate(
    first_day: date, days: int, config: ScheduleConfig, seed: str = DEFAULT_SEED
) -> list[Row]:
    demo = config.demo
    step = timedelta(minutes=demo.slot_minutes)
    rows: list[Row] = []
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        moment = datetime.combine(day, time(0))
        while moment.date() == day:
            start, end = moment.time(), (moment + step).time()
            rows.append(
                Row(
                    slot_id=f"{day:%Y%m%d}-{start:%H%M}-main",
                    day=day,
                    start=start,
                    end=end,
                    cabinet=demo.main_cabinet,
                    doctor=shift_doctor(day, start, config),
                    repeat_window=_in_window(start, config),
                    repeat_cabinet=False,
                    busy=False,
                )
            )
            if demo.repeat_cabinet_hours.contains(start):
                rows.append(
                    Row(
                        slot_id=f"{day:%Y%m%d}-{start:%H%M}-repeat",
                        day=day,
                        start=start,
                        end=end,
                        cabinet=demo.repeat_cabinet,
                        doctor=repeat_doctor(day, config),
                        repeat_window=False,
                        repeat_cabinet=True,
                        busy=False,
                    )
                )
            moment += step
    rows.sort(key=lambda r: (r.day, r.start, r.repeat_cabinet))
    return _mark_busy(rows, config, seed)


def write_csv(rows: Iterable[Row], path: Path, mark: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(HEADER_NOTE.format(fingerprint=mark or "—") + "\n")
        writer = csv.writer(fh)
        writer.writerow(COLUMNS)
        for r in rows:
            writer.writerow(
                [
                    r.slot_id,
                    r.day.isoformat(),
                    f"{r.start:%H:%M}",
                    f"{r.end:%H:%M}",
                    r.cabinet,
                    r.doctor,
                    int(r.repeat_window),
                    int(r.repeat_cabinet),
                    int(r.busy),
                ]
            )


def read_mark(path: Path) -> str | None:
    """The configuration fingerprint written in the file's first line."""
    with path.open(encoding="utf-8") as fh:
        first = fh.readline().strip()
    head, sep, mark = first.rpartition("конфигурация ")
    return mark if sep and head.startswith("#") else None


def read_csv(path: Path) -> list[Row]:
    with path.open(encoding="utf-8", newline="") as fh:
        lines = [line for line in fh if not line.startswith("#")]
    rows = []
    for rec in csv.DictReader(lines):
        rows.append(
            Row(
                slot_id=rec["slot_id"],
                day=date.fromisoformat(rec["date"]),
                start=time.fromisoformat(rec["start"]),
                end=time.fromisoformat(rec["end"]),
                cabinet=rec["cabinet"],
                doctor=rec["doctor"],
                repeat_window=rec["repeat_window"] == "1",
                repeat_cabinet=rec["repeat_cabinet"] == "1",
                busy=rec["busy"] == "1",
            )
        )
    return rows


def ensure_horizon(
    path: Path, today: date, config: ScheduleConfig, seed: str = DEFAULT_SEED
) -> bool:
    """Keep ``days_ahead`` days from today in the file: drop past days, add missing ones.

    Existing rows are kept as they are, so slot ids stay stable. If the schedule settings
    changed (doctors, shifts, busy share…), the file is rebuilt; slot ids are made from the
    date and time, so bookings stay valid. Returns True if the file was written.
    """
    mark = fingerprint(config, seed)
    fresh = path.is_file() and read_mark(path) == mark
    rows = read_csv(path) if fresh else []
    kept = [r for r in rows if r.day >= today]
    last_needed = today + timedelta(days=config.demo.days_ahead - 1)
    first_missing = max((r.day for r in kept), default=today - timedelta(days=1))
    first_missing += timedelta(days=1)
    added = []
    if first_missing <= last_needed:
        added = generate(first_missing, (last_needed - first_missing).days + 1, config, seed)
    if fresh and not added and len(kept) == len(rows):
        return False
    write_csv([*kept, *added], path, mark)
    return True


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--start", type=date.fromisoformat, help="first day, default today")
    parser.add_argument("--days", type=int, help="number of days, default from config")
    parser.add_argument("--seed", default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=SCHEDULE_CSV)
    args = parser.parse_args(argv)
    config = get_clinic_data().schedule
    start = args.start or datetime.now(get_settings().tz).date()
    rows = generate(start, args.days or config.demo.days_ahead, config, args.seed)
    write_csv(rows, args.out, fingerprint(config, args.seed))
    busy = sum(r.busy for r in rows)
    print(f"{args.out}: {len(rows)} slots from {start}, busy {busy / len(rows):.0%}")


if __name__ == "__main__":
    main()
