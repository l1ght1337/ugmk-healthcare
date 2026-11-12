"""Generate the demo schedule data/schedule.csv (no access to «Медиалог» yet).

Doctors come from script 03-1 and the «Повторный приём» windows from 04-09
(config/schedule.yaml). Everything else — slot length, shifts, the second cabinet's hours,
busy share — is a demo assumption, and the file says so in its first line.

    uv run python -m app.schedule.generate            # 14 days from today
    uv run python -m app.schedule.generate --start 2026-11-12 --days 14
"""

from __future__ import annotations

import argparse
import csv
import random
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from app.config import PROJECT_ROOT, ScheduleConfig, get_clinic_data, get_settings

SCHEDULE_CSV = PROJECT_ROOT / "data" / "schedule.csv"
HEADER_NOTE = (
    "# Демо-расписание травмпункта: время и занятость вымышленные, это не расписание клиники. "
    "Создано app/schedule/generate.py"
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
DAY_SHIFT = (time(8), time(20))  # main cabinet: day 8–20, night 20–8 (demo)


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


def _in_window(moment: time, config: ScheduleConfig) -> bool:
    return any(w.contains(moment) for w in config.repeat_windows)


def _main_doctor(day: date, moment: time, doctors: tuple[str, ...]) -> str:
    k = day.toordinal()
    if DAY_SHIFT[0] <= moment < DAY_SHIFT[1]:
        return doctors[(2 * k) % len(doctors)]
    if moment >= DAY_SHIFT[1]:
        return doctors[(2 * k + 1) % len(doctors)]
    return doctors[(2 * (k - 1) + 1) % len(doctors)]  # after midnight: yesterday's night shift


def _repeat_doctor(day: date, doctors: tuple[str, ...]) -> str:
    return doctors[(2 * day.toordinal() + 3) % len(doctors)]


def _busy(slot_id: str, share: float, seed: str) -> bool:
    return random.Random(f"{seed}:{slot_id}").random() < share


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
            slot_id = f"{day:%Y%m%d}-{start:%H%M}-main"
            rows.append(
                Row(
                    slot_id=slot_id,
                    day=day,
                    start=start,
                    end=end,
                    cabinet=demo.main_cabinet,
                    doctor=_main_doctor(day, start, config.doctors),
                    repeat_window=_in_window(start, config),
                    repeat_cabinet=False,
                    busy=_busy(slot_id, demo.busy_share, seed),
                )
            )
            if demo.repeat_cabinet_hours.contains(start):
                slot_id = f"{day:%Y%m%d}-{start:%H%M}-repeat"
                rows.append(
                    Row(
                        slot_id=slot_id,
                        day=day,
                        start=start,
                        end=end,
                        cabinet=demo.repeat_cabinet,
                        doctor=_repeat_doctor(day, config.doctors),
                        repeat_window=False,
                        repeat_cabinet=True,
                        busy=_busy(slot_id, demo.busy_share, seed),
                    )
                )
            moment += step
    rows.sort(key=lambda r: (r.day, r.start, r.repeat_cabinet))
    return rows


def write_csv(rows: Iterable[Row], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(HEADER_NOTE + "\n")
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

    Existing rows are kept as they are, so slot ids stay stable. Returns True if the file
    was written.
    """
    rows = read_csv(path) if path.is_file() else []
    kept = [r for r in rows if r.day >= today]
    last_needed = today + timedelta(days=config.demo.days_ahead - 1)
    first_missing = max((r.day for r in kept), default=today - timedelta(days=1))
    first_missing += timedelta(days=1)
    added = []
    if first_missing <= last_needed:
        added = generate(first_missing, (last_needed - first_missing).days + 1, config, seed)
    if not added and len(kept) == len(rows) and path.is_file():
        return False
    write_csv([*kept, *added], path)
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
    write_csv(rows, args.out)
    busy = sum(r.busy for r in rows)
    print(f"{args.out}: {len(rows)} slots from {start}, busy {busy / len(rows):.0%}")


if __name__ == "__main__":
    main()
