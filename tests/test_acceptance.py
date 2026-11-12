"""Acceptance cases 1–12 and 20 from docs/test_cases.md, played on the engine.

Answers are given already structured (no LLM). Cases marked «по скрипту» in the doc are
pinned to the outcomes of kb/text; the table for review is docs/engine_outcomes.md.
"""

from __future__ import annotations

import pytest

from app.bot import render
from app.config import ClinicData
from app.flow.loader import KnowledgeBase
from app.flow.safety import find_emergency
from app.schedule.mock import MockSchedule
from app.schedule.rules import find_slots
from tests.conftest import NOW

NO_ASSAULT = {"assault": False}


def facilities(walk) -> list[str]:
    return [p.facility.id for p in walk.step.places]


# --- booking -----------------------------------------------------------------------------


async def test_case_01_adult_dislocation_books_primary(
    run, kb: KnowledgeBase, clinic: ClinicData, schedule: MockSchedule
) -> None:
    """Взрослый, вывих плеча вчера, упал дома, к врачу не обращался."""
    w = run("dislocation", {**NO_ASSAULT, "injury_age": "within_7_days", "seen_doctor": "no"})
    assert w.asked == ["how", "when", "seen_doctor"]
    assert w.step.kind == "book"
    booking = w.step.outcome.booking
    assert (booking.visit, booking.slot_policy) == ("primary", "nearest")
    assert "вывих, врач попробует его вправить" in render.outcome_message(w.step, clinic, NOW)

    slots = await find_slots(schedule, booking, now=NOW, config=clinic.schedule)
    assert slots and all(not s.repeat_window and not s.repeat_cabinet for s in slots)
    slot = await schedule.book(slots[0].id)
    text = render.booking_confirmation(
        kb,
        w.step,
        slot,
        first_name="Иван",
        patronymic="Петрович",
        audience="adult",
        clinic=clinic,
        demo=True,
    )
    assert "Иван Петрович записан на приём травматолога-ортопеда" in text
    assert "ул. Шейнкмана, 113/2, вход через детское отделение" in text
    assert "С собой возьмите паспорт. При первом обращении — ещё СНИЛС." in text
    assert "Стоимость консультации уточнит администратор" in text
    assert slot.doctor in text
    assert "демо" in text


def test_case_01_all_answers_in_the_complaint(run) -> None:
    """The same case written in one message: no question is asked twice."""
    facts = {**NO_ASSAULT, "injury_age": "within_7_days", "seen_doctor": "no"}
    w = run("dislocation", {}, upfront=facts)
    assert w.asked == [] and w.step.outcome_id == "book_primary"


@pytest.mark.parametrize("scenario_id", ["fracture", "dislocation"])
def test_case_02_child_injury_over_7_days_goes_to_planned_visit(
    run, clinic: ClinicData, scenario_id: str
) -> None:
    """Ребёнок 8 лет, упал с велосипеда 10 дней назад, болит рука."""
    w = run(
        scenario_id,
        {**NO_ASSAULT, "injury_age": "over_7_days"},
        audience="child",
        age_months=8 * 12,
    )
    assert w.step.kind == "handoff"
    assert w.step.outcome.handoff_reason.startswith(
        "Ребёнок: записать на ближайший плановый приём к травматологу-ортопеду (не в травмпункт)"
    )
    assert "Передаю Ваше обращение администратору" in render.outcome_message(w.step, clinic, NOW)


def test_case_03_adult_already_seen_doctor_goes_to_call_center(run) -> None:
    """Взрослый, вывих два дня назад, уже был у врача (не в нашем травмпункте)."""
    w = run(
        "dislocation", {**NO_ASSAULT, "injury_age": "within_7_days", "seen_doctor": "other_doctor"}
    )
    assert w.step.kind == "handoff"
    assert "взрослого КЦ (т. 1102)" in w.step.outcome.handoff_reason


# Case 4: one case per scenario, outcome by the script.
CASE_04 = [
    pytest.param(
        "cut_sutures",
        {
            "wound_location": "body",
            "motor_function": True,
            "injury_age": "within_24h",
            "suture_type": "surgical",
        },
        "adult",
        "book_primary",
        id="порез пальца → запись",
    ),
    pytest.param(
        "fracture",
        {
            **NO_ASSAULT,
            "open_or_displaced": False,
            "injury_age": "within_7_days",
            "seen_doctor": "no",
        },
        "adult",
        "book_primary",
        id="перелом → запись",
    ),
    pytest.param(
        "burns",
        {
            "burn_location": "body",
            "burn_cause": "thermal",
            "injury_age": "within_24h",
            "seen_doctor": "no",
        },
        "adult",
        "book_primary",
        id="ожог кипятком → запись",
    ),
    pytest.param(
        "animal_bite",
        {
            "danger_signs": False,
            "animal": "domestic",
            "injury_age": "within_7_days",
            "seen_doctor": "no",
        },
        "child",
        "book_primary",
        id="укус собаки → запись",
    ),
    pytest.param(
        "ligament_rupture",
        {**NO_ASSAULT, "rupture_certainty": "suspects"},
        "adult",
        "book_primary",
        id="разрыв связок, подозревает → запись",
    ),
    pytest.param(
        "ligament_rupture",
        {**NO_ASSAULT, "rupture_certainty": "insists"},
        "adult",
        "insists_adult",
        id="разрыв связок, подтверждён → ГКБ 40 / ГКБ 14 / ЦГБ 23",
    ),
]


@pytest.mark.parametrize(("scenario_id", "answers", "audience", "outcome"), CASE_04)
def test_case_04_one_per_scenario(run, scenario_id, answers, audience, outcome) -> None:
    w = run(scenario_id, answers, audience=audience, age_months=7 * 12)
    assert w.step.outcome_id == outcome


def test_case_04_dog_bite_mentions_vaccine(run, clinic: ClinicData) -> None:
    answers = {
        "danger_signs": False,
        "animal": "domestic",
        "injury_age": "within_7_days",
        "seen_doctor": "no",
    }
    w = run("animal_bite", answers, audience="child", age_months=7 * 12)
    assert "Антирабической вакцины нет" in render.outcome_message(w.step, clinic, NOW)


def test_case_04_ligament_insists_offers_hospitals_and_admin(run, clinic: ClinicData) -> None:
    w = run("ligament_rupture", {**NO_ASSAULT, "rupture_certainty": "insists"})
    assert facilities(w) == ["gkb40", "gkb14", "cgb23"]
    text = render.outcome_message(w.step, clinic, NOW)
    assert "С разрывами связок Вам нужно обратиться:" in text
    assert "подозрением" not in text
    assert "передать Ваше обращение администратору" in text


# --- routing ------------------------------------------------------------------------------


def test_case_05_fight_goes_to_municipal_trauma_center(run, clinic: ClinicData) -> None:
    """«Подрался на улице, хочу снять побои» → 5.1, no question about the audience."""
    w = run("criminal_trauma", {}, audience=None)
    assert facilities(w) == ["municipal_trauma_center"]
    assert "любой муниципальный травмпункт" in render.outcome_message(w.step, clinic, NOW).lower()


@pytest.mark.parametrize("scenario_id", ["dislocation", "fracture", "ligament_rupture"])
def test_case_05_assault_in_booking_scenarios(run, scenario_id: str) -> None:
    w = run(scenario_id, {"assault": True})
    assert w.state.trail == [scenario_id, "criminal_trauma"]
    assert facilities(w) == ["municipal_trauma_center"]


@pytest.mark.parametrize("scenario_id", ["dislocation", "head_trauma", "animal_bite"])
def test_case_06_infant_under_6_months(run, clinic: ClinicData, scenario_id: str) -> None:
    """Ребёнок 4 месяца упал с кровати — whatever scenario was chosen."""
    w = run(scenario_id, {}, audience="child", age_months=4)
    assert w.asked == []
    assert w.step.scenario.id == "infant_under_6m"
    assert "Серафимы Дерябиной, 32" in render.outcome_message(w.step, clinic, NOW)


def test_case_06_six_months_is_not_infant(run) -> None:
    w = run("head_trauma", {}, audience="child", age_months=6)
    assert w.step.scenario.id == "head_trauma"


def test_case_07_adult_head_injury(run) -> None:
    w = run("head_trauma", {}, audience="adult")
    assert facilities(w) == ["cgkb23", "cb24", "gtb36"]


def test_case_08_needle(run, clinic: ClinicData) -> None:
    w = run("needle_injury", {}, audience=None)
    assert "Центр борьбы со СПИДом, ул. Ясная, 46" in render.outcome_message(w.step, clinic, NOW)


def test_case_09_child_pin_removal(run) -> None:
    w = run("pin_removal", {}, audience="child", age_months=10 * 12)
    assert w.step.kind == "handoff"
    assert "плановый приём к одному из хирургов" in w.step.outcome.handoff_reason


# --- repeat visit (4.9) -------------------------------------------------------------------


async def test_case_10_repeat_with_sick_leave(run, clinic: ClinicData, schedule) -> None:
    w = run("repeat_visit", {"first_visit_here": True, "sick_leave_open": True})
    assert w.step.outcome.booking.slot_policy == "near_repeat_window"
    slots = await find_slots(schedule, w.step.outcome.booking, now=NOW, config=clinic.schedule)
    # 12:00 now: today's free time closest to the 16:00–18:00 window comes first.
    assert all(s.start.date() == NOW.date() for s in slots)
    assert all(15 <= s.start.hour < 19 for s in slots), [s.id for s in slots]


async def test_case_11_repeat_without_sick_leave(run, clinic: ClinicData, schedule) -> None:
    w = run("repeat_visit", {"first_visit_here": True, "sick_leave_open": False})
    assert w.step.outcome.booking.slot_policy == "repeat_window"
    slots = await find_slots(
        schedule, w.step.outcome.booking, now=NOW, config=clinic.schedule, limit=50
    )
    assert slots
    for s in slots:
        in_window = 8 <= s.start.hour < 10 or 16 <= s.start.hour < 18
        assert (s.repeat_window and in_window) or s.cabinet == "Травматолог 2", s.id


@pytest.mark.parametrize(
    ("audience", "who"), [("adult", "взрослого КЦ"), ("child", "плановый приём")]
)
def test_case_12_first_visit_elsewhere(run, audience: str, who: str) -> None:
    w = run("repeat_visit", {"first_visit_here": False}, audience=audience, age_months=9 * 12)
    assert w.step.kind == "handoff"
    assert who in w.step.outcome.handoff_reason
    assert w.step.outcome.say[0].startswith("Запись в травмпункт на повторный приём ведётся")


# --- emergency ----------------------------------------------------------------------------


def test_case_20_bleeding_is_an_emergency(clinic: ClinicData) -> None:
    phrases = clinic.clinic.emergency.phrases
    assert find_emergency("Кровь хлещет, не останавливается!", phrases)
    assert render.emergency_message(clinic) == "При угрозе жизни звоните 103 или 112."


@pytest.mark.parametrize(
    "text",
    ["Порезал палец, немного кровь идёт", "кровь", "Сдавал кровь вчера", "Дышать могу нормально"],
)
def test_case_20_no_false_alarm_on_single_words(clinic: ClinicData, text: str) -> None:
    assert find_emergency(text, clinic.clinic.emergency.phrases) is None
