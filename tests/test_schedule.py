"""Demo schedule generator, mock provider and the 04-09 slot rules."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from app.config import ClinicData, Settings
from app.flow.loader import script_text
from app.flow.schema import Booking
from app.schedule import make_provider
from app.schedule.base import Slot, SlotUnavailable
from app.schedule.generate import ensure_horizon, fingerprint, generate, read_csv, write_csv
from app.schedule.mock import MockSchedule
from app.schedule.rules import find_slots, order_slots, window_distance
from tests.conftest import FIRST_DAY, NOW, TZ

DAYS = 21


@pytest.fixture(scope="module")
def rows(clinic: ClinicData):
    return generate(FIRST_DAY, DAYS, clinic.schedule)


def booking(policy: str) -> Booking:
    return Booking(visit="repeat", service="x", slot_policy=policy, price_key="repeat_visit")


def slot(hour: int, minute: int = 0, *, day: int = 0, window=False, repeat_cabinet=False) -> Slot:
    start = NOW.replace(hour=hour, minute=minute) + timedelta(days=day)
    return Slot(
        f"{day}-{hour}{minute}",
        start,
        start + timedelta(minutes=30),
        "Д",
        "К",
        window,
        repeat_cabinet,
    )


# --- generator --------------------------------------------------------------------------


def test_doctors_are_the_four_the_clinic_confirmed(clinic: ClinicData) -> None:
    source = script_text("03-1_info_travmpunkt").replace(" ", "")
    for name in clinic.schedule.doctors:
        assert name.replace(" ", "") in source, name
    assert [d.split()[0] for d in clinic.schedule.doctors] == [
        "Кузнецов", "Ярунин", "Билалов", "Деменков",
    ]  # fmt: skip


def test_twenty_one_days_of_30_minute_slots(rows, clinic: ClinicData) -> None:
    assert clinic.schedule.demo.days_ahead == DAYS
    assert {r.day for r in rows} == {FIRST_DAY + timedelta(days=i) for i in range(DAYS)}
    per_cabinet = Counter(r.cabinet for r in rows)
    assert per_cabinet == {"Травматолог 1": DAYS * 48, "Травматолог 2": DAYS * 24}


def test_one_doctor_per_shift_and_the_shifts_rotate(rows) -> None:
    """Clinic: one doctor 8:00–20:00, the next one 20:00–8:00; in the demo all four rotate."""
    shifts: dict[tuple[date, str], set[str]] = {}
    for r in rows:
        if r.repeat_cabinet:
            continue
        if time(8) <= r.start < time(20):
            key = (r.day, "day")
        elif r.start >= time(20):
            key = (r.day, "night")
        else:
            key = (r.day - timedelta(days=1), "night")
        shifts.setdefault(key, set()).add(r.doctor)
    assert all(len(doctors) == 1 for doctors in shifts.values())
    for kind in ("day", "night"):
        on_duty = {next(iter(d)) for (_, k), d in shifts.items() if k == kind}
        assert len(on_duty) == 4, kind
    for (day, kind), doctors in shifts.items():  # nobody works a night right after a day shift
        if kind == "day":
            assert doctors != shifts.get((day, "night")), day


def test_repeat_windows_and_second_cabinet(rows) -> None:
    for r in rows:
        in_window = time(8) <= r.start < time(10) or time(16) <= r.start < time(18)
        if r.cabinet == "Травматолог 1":
            assert r.repeat_window == in_window and not r.repeat_cabinet
        else:
            assert r.repeat_cabinet and not r.repeat_window and time(8) <= r.start < time(20)


def _busy_share(rows, start: time, end: time) -> float:
    if start < end:
        chosen = [r for r in rows if start <= r.start < end]
    else:
        chosen = [r for r in rows if r.start >= start or r.start < end]
    return sum(r.busy for r in chosen) / len(chosen)


def test_busy_morning_free_night_and_deterministic(rows, clinic: ClinicData) -> None:
    """Load from the clinic: peak 8:00–11:00 (60–70 % busy), the night almost free."""
    assert 0.6 <= _busy_share(rows, time(8), time(11)) <= 0.7
    assert _busy_share(rows, time(23), time(7)) <= 0.1
    assert 0.3 <= _busy_share(rows, time(11), time(20)) <= 0.5
    assert generate(FIRST_DAY, DAYS, clinic.schedule) == rows


def test_a_doctor_is_never_in_two_cabinets_at_once(rows) -> None:
    by_time = Counter((r.day, r.start, r.doctor) for r in rows)
    assert max(by_time.values()) == 1


def test_csv_round_trip(rows, tmp_path: Path) -> None:
    path = tmp_path / "schedule.csv"
    write_csv(rows, path)
    assert path.read_text(encoding="utf-8").startswith("# Демо-расписание")
    assert read_csv(path) == rows


def test_ensure_horizon_drops_past_and_adds_days(rows, clinic: ClinicData, tmp_path) -> None:
    path = tmp_path / "schedule.csv"
    write_csv(rows, path, fingerprint(clinic.schedule))
    assert ensure_horizon(path, FIRST_DAY, clinic.schedule) is False  # already fresh
    later = FIRST_DAY + timedelta(days=2)
    assert ensure_horizon(path, later, clinic.schedule) is True
    updated = read_csv(path)
    assert min(r.day for r in updated) == later
    assert max(r.day for r in updated) == later + timedelta(days=DAYS - 1)
    kept = {r.slot_id: r for r in rows if r.day >= later}
    assert all(kept[r.slot_id] == r for r in updated if r.slot_id in kept)


def test_changed_settings_rebuild_the_csv(rows, clinic: ClinicData, tmp_path: Path) -> None:
    """Old files (eight doctors, flat 40 % busy) are rebuilt; slot ids stay the same."""
    path = tmp_path / "schedule.csv"
    old = [replace(r, doctor="Гусев Антон Викторович") for r in rows]
    write_csv(old, path, "old-config")
    assert ensure_horizon(path, FIRST_DAY, clinic.schedule) is True
    rebuilt = read_csv(path)
    assert rebuilt == rows and {r.slot_id for r in rebuilt} == {r.slot_id for r in old}
    assert ensure_horizon(path, FIRST_DAY, clinic.schedule) is False


def test_make_provider_creates_missing_csv(clinic: ClinicData, tmp_path: Path) -> None:
    path = tmp_path / "schedule.csv"
    provider = make_provider(Settings(schedule_provider="mock"), clinic, csv_path=path)
    assert isinstance(provider, MockSchedule) and path.is_file()


# --- mock provider ----------------------------------------------------------------------


async def test_book_and_release(schedule: MockSchedule) -> None:
    free = await schedule.free_slots(NOW, NOW + timedelta(hours=6))
    target = free[0]
    await schedule.book(target.id)
    assert target not in await schedule.free_slots(NOW, NOW + timedelta(hours=6))
    with pytest.raises(SlotUnavailable):
        await schedule.book(target.id)
    await schedule.release(target.id)
    assert target in await schedule.free_slots(NOW, NOW + timedelta(hours=6))


async def test_busy_and_unknown_slots_cannot_be_booked(schedule: MockSchedule, rows) -> None:
    busy = next(r for r in rows if r.busy)
    with pytest.raises(SlotUnavailable):
        await schedule.book(busy.slot_id)
    with pytest.raises(SlotUnavailable):
        await schedule.book("no-such-slot")
    await schedule.release(busy.slot_id)  # releasing someone else's slot changes nothing
    with pytest.raises(SlotUnavailable):
        await schedule.book(busy.slot_id)


async def test_restore_bookings_after_restart(schedule: MockSchedule) -> None:
    free = await schedule.free_slots(NOW, NOW + timedelta(hours=3))
    schedule.restore([free[0].id, "gone-slot"])
    assert free[0] not in await schedule.free_slots(NOW, NOW + timedelta(hours=3))


async def test_slot_times_are_in_clinic_zone(schedule: MockSchedule) -> None:
    s = await schedule.get("20261112-0830-main")
    assert s is not None
    assert s.start == datetime(2026, 11, 12, 8, 30, tzinfo=TZ)
    assert s.end - s.start == timedelta(minutes=30)


# --- 04-09 rules ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hour", "minute", "distance"),
    [(8, 30, 0), (7, 30, 0), (10, 0, 0), (11, 0, 60), (13, 0, 150), (21, 0, 180), (2, 0, 330)],
)
def test_window_distance(clinic: ClinicData, hour: int, minute: int, distance: int) -> None:
    assert window_distance(slot(hour, minute), clinic.schedule.repeat_windows) == distance


def test_nearest_skips_windows_and_second_cabinet(clinic: ClinicData) -> None:
    slots = [slot(13, window=True), slot(13, repeat_cabinet=True), slot(14), slot(13, 30)]
    ordered = order_slots(slots, "nearest", now=NOW, windows=clinic.schedule.repeat_windows)
    assert [(s.start.hour, s.start.minute) for s in ordered] == [(13, 30), (14, 0)]


def test_near_window_prefers_today_then_closeness(clinic: ClinicData) -> None:
    slots = [slot(9, day=1, window=True), slot(13), slot(15, 30), slot(20)]
    ordered = order_slots(
        slots, "near_repeat_window", now=NOW, windows=clinic.schedule.repeat_windows
    )
    assert [(s.start.day, s.start.hour) for s in ordered] == [(12, 15), (12, 20), (12, 13), (13, 9)]


def test_min_lead_time(clinic: ClinicData) -> None:
    slots = [slot(12, 0), slot(12, 30), slot(13)]
    ordered = order_slots(
        slots,
        "nearest",
        now=NOW,
        windows=clinic.schedule.repeat_windows,
        min_lead=timedelta(minutes=30),
    )
    assert [s.start.minute for s in ordered] == [30, 0]


async def test_paging_through_other_times(clinic: ClinicData, schedule: MockSchedule) -> None:
    first = await find_slots(schedule, booking("nearest"), now=NOW, config=clinic.schedule)
    second = await find_slots(
        schedule, booking("nearest"), now=NOW, config=clinic.schedule, offset=len(first)
    )
    assert len(first) == 4 and second and not set(first) & set(second)
    assert first[-1].start <= second[0].start


async def test_offers_stay_inside_horizon(clinic: ClinicData, schedule: MockSchedule) -> None:
    every = await find_slots(
        schedule, booking("repeat_window"), now=NOW, config=clinic.schedule, limit=10_000
    )
    assert max(s.start for s in every).date() <= FIRST_DAY + timedelta(days=DAYS)


async def test_control_visit_time_starts_about_ten_days_later(
    clinic: ClinicData, schedule: MockSchedule
) -> None:
    later = NOW + timedelta(days=10)
    slots = await find_slots(
        schedule, booking("repeat_window"), now=NOW, config=clinic.schedule, not_before=later
    )
    assert slots and all(s.start >= later for s in slots)
