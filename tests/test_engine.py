"""Engine mechanics: who the visit is for, skipping known answers, defaults, retries, jumps."""

from __future__ import annotations

import pytest

from app.config import ClinicData
from app.flow.engine import MAX_ASKS, AskPatient, AskQuestion, DialogState, Done, Engine
from app.flow.loader import KnowledgeBase


def test_booking_asks_audience_then_child_age(engine: Engine) -> None:
    state = DialogState()
    assert engine.start(state, "dislocation") == AskPatient("audience")
    assert engine.set_patient(state, "child") == AskPatient("age")
    step = engine.set_patient(state, "child", age_months=8 * 12)
    assert isinstance(step, AskQuestion) and step.question.id == "how"


def test_audience_known_before_scenario(engine: Engine) -> None:
    state = DialogState()
    assert engine.set_patient(state, "adult") is None  # nothing pending yet
    step = engine.start(state, "dislocation")
    assert isinstance(step, AskQuestion) and step.question.id == "how"


@pytest.mark.parametrize(("months", "audience"), [(16 * 12, "child"), (18 * 12, "adult")])
def test_teens_are_children_adults_from_18(engine: Engine, months: int, audience: str) -> None:
    state = DialogState()
    engine.start(state, "dislocation")
    engine.set_patient(state, "child", age_months=months)
    assert state.audience == audience


def test_routing_without_audience_branches_does_not_ask(engine: Engine) -> None:
    step = engine.start(DialogState(), "needle_injury")
    assert isinstance(step, Done) and step.outcome_id == "aids_center"


def test_question_is_asked_again_until_answered(engine: Engine) -> None:
    state = DialogState()
    engine.set_patient(state, "adult")
    engine.start(state, "dislocation")
    step = engine.answer(state, {"assault": "maybe"})  # invalid value is ignored
    assert isinstance(step, AskQuestion) and step.question.id == "how" and step.attempt == 2
    for _ in range(MAX_ASKS - 2):
        step = engine.answer(state, {})
    assert isinstance(step, AskQuestion) and step.attempt == MAX_ASKS and not step.stuck
    stuck = engine.answer(state, {})  # third unclear answer: buttons only, no administrator
    assert isinstance(stuck, AskQuestion) and stuck.question.id == "how" and stuck.stuck
    assert state.outcome_id is None


def test_unknown_facts_are_ignored(engine: Engine) -> None:
    state = DialogState()
    engine.set_patient(state, "adult")
    engine.start(state, "dislocation", {"no_such_field": True, "injury_age": "within_7_days"})
    assert state.facts == {"audience": "adult", "injury_age": "within_7_days"}


def test_bonus_answer_skips_later_question(run) -> None:
    """«Вывихнул вчера» in the first answer: «Когда?» is not asked again."""
    w = run(
        "dislocation", {**{"assault": False, "injury_age": "within_7_days"}, "seen_doctor": "no"}
    )
    assert w.asked == ["how", "when", "seen_doctor"]
    w = run(
        "dislocation",
        {"seen_doctor": "no"},
        upfront={"assault": False, "injury_age": "within_7_days"},
    )
    assert w.asked == ["seen_doctor"]


def test_field_default_when_patient_does_not_mention_it(run) -> None:
    """04-07: open/displaced fracture only if the patient says so."""
    w = run("fracture", {"assault": False, "injury_age": "within_7_days", "seen_doctor": "no"})
    assert w.state.facts["open_or_displaced"] is False
    assert w.step.outcome_id == "book_primary"


@pytest.mark.parametrize(
    ("audience", "places"), [("child", ["dgkb9"]), ("adult", ["gtb36", "gkb14", "cgb23"])]
)
def test_open_fracture_routes(run, audience: str, places: list[str]) -> None:
    w = run(
        "fracture",
        {"assault": False, "open_or_displaced": True},
        audience=audience,
        age_months=5 * 12,
    )
    assert [p.facility.id for p in w.step.places] == places
    assert w.step.offer_admin


def test_already_treated_here_jumps_to_repeat_visit(run) -> None:
    w = run(
        "burns",
        {
            "burn_location": "body",
            "burn_cause": "thermal",
            "injury_age": "within_24h",
            "seen_doctor": "our_trauma_center",
            "sick_leave_open": False,
        },
    )
    assert w.state.trail == ["burns", "repeat_visit"]
    assert w.asked == ["burn_location", "burn_cause", "when", "seen_doctor", "sick_leave"]
    assert w.step.outcome_id == "book_repeat_window"


def test_control_visit_starts_with_first_visit_known(run) -> None:
    """After the first visit the bot books the control one: only «больничный?» is asked."""
    w = run("repeat_visit", {"sick_leave_open": True}, upfront={"first_visit_here": True})
    assert w.asked == ["sick_leave"]
    assert w.step.outcome_id == "book_near_repeat_window"


# --- stage 1 review decisions ----------------------------------------------------------


@pytest.mark.parametrize("audience", ["adult", "child"])
def test_sick_leave_help_already_given_means_planned_visit(run, audience: str) -> None:
    """04-05: help already given (anywhere, our trauma centre too) → planned consultation."""
    w = run(
        "sick_leave",
        {"had_injury": True, "help_provided": "yes"},
        audience=audience,
        age_months=10 * 12,
    )
    assert w.step.kind == "handoff"
    assert w.state.trail == ["sick_leave"]
    assert w.step.outcome.say[0].startswith("Открыть больничный лист можно только на «плановой»")


def test_sick_leave_open_here_extends_via_repeat_visit(run) -> None:
    """The only exception: sick leave opened in our trauma centre → 04-09, no more questions."""
    w = run("sick_leave", {"had_injury": True, "help_provided": "sick_leave_open_here"})
    assert w.state.trail == ["sick_leave", "repeat_visit"]
    assert w.asked == ["had_injury", "help_provided"]
    assert w.step.outcome_id == "book_near_repeat_window"


def test_ligament_certainty_is_asked_only_if_unclear(run) -> None:
    w = run("ligament_rupture", {"assault": False, "rupture_certainty": "suspects"})
    assert w.asked == ["how", "certainty"]
    w = run("ligament_rupture", {"rupture_certainty": "insists"}, upfront={"assault": False})
    assert w.asked == ["certainty"]
    w = run("ligament_rupture", {}, upfront={"assault": False, "rupture_certainty": "insists"})
    assert w.asked == []


def test_ligament_clarifying_question(kb: KnowledgeBase) -> None:
    q = kb.scenario("ligament_rupture").question("certainty")
    assert q.ask == "Разрыв уже подтвердил врач или МРТ, или Вы предполагаете?"


# --- notices ----------------------------------------------------------------------------


def test_vaccine_notice_follows_config(kb: KnowledgeBase, clinic: ClinicData, run) -> None:
    answers = {
        "danger_signs": False,
        "animal": "domestic",
        "injury_age": "within_7_days",
        "seen_doctor": "no",
    }
    assert run("animal_bite", answers).step.notices

    flags = clinic.clinic.model_copy(update={"rabies_vaccine_available": True})
    with_vaccine = Engine(kb, clinic.model_copy(update={"clinic": flags}))
    state = DialogState()
    with_vaccine.set_patient(state, "adult")
    step = with_vaccine.start(state, "animal_bite", answers)
    assert isinstance(step, Done) and step.notices == ()


def test_bite_danger_signs_is_emergency(run) -> None:
    w = run("animal_bite", {"danger_signs": True})
    assert w.step.kind == "emergency"
    assert [p.facility.id for p in w.step.places] == ["gkb14_toxicology"]


def test_state_round_trips_as_json(engine: Engine) -> None:
    state = DialogState()
    engine.set_patient(state, "child", age_months=30)
    engine.start(state, "burns", {"burn_location": "body"})
    restored = DialogState.model_validate_json(state.model_dump_json())
    step = engine.answer(restored, {"burn_cause": "chemical"})
    assert isinstance(step, Done) and step.outcome_id == "chemical_child"
