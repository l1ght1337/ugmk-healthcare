"""Scenario engine: walks the decision graphs from kb/scenarios with structured answers.

No Telegram and no LLM here. Callers pass facts that were already extracted from the
patient's words or taken from pressed buttons; the engine alone picks branches, follows
jumps between scenarios and stops at an outcome (CLAUDE.md, rule 2).

The dialog state is a plain pydantic model, so the bot can keep it per chat in the DB.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from app.config import ClinicData
from app.flow.loader import KnowledgeBase
from app.flow.schema import (
    AUDIENCES,
    CONTEXT_FACTS,
    Audience,
    Branch,
    Facility,
    FactValue,
    Notice,
    Outcome,
    Question,
    Scenario,
)

log = logging.getLogger(__name__)

INFANT_SCENARIO = "infant_under_6m"
INFANT_UNDER_MONTHS = 6  # 05-04: «дети в возрасте до 6 месяцев»
ADULT_FROM_MONTHS = 18 * 12  # teens 15–17 are led as children (customer decision)
MAX_ASKS = 3  # a question that is still unclear after this many asks goes to an admin
MAX_JUMPS = 5  # guard against jump chains between scenarios


class DialogState(BaseModel):
    """Where a patient is. ``question_id`` is set while waiting for an answer."""

    scenario_id: str | None = None
    question_id: str | None = None
    asks: int = 0
    facts: dict[str, FactValue] = {}
    age_months: int | None = None
    trail: list[str] = []
    outcome_id: str | None = None

    @property
    def audience(self) -> Audience | None:
        value = self.facts.get("audience")
        return value if value in AUDIENCES else None  # type: ignore[return-value]

    @property
    def pending(self) -> bool:
        """A scenario is chosen but has not started (waiting for audience or age)."""
        return self.scenario_id is not None and self.question_id is None and not self.outcome_id


# --- steps returned to the caller ------------------------------------------------------


@dataclass(frozen=True)
class AskPatient:
    """Ask who the visit is for, or the child's age, before the scenario starts."""

    need: Literal["audience", "age"]


@dataclass(frozen=True)
class AskQuestion:
    scenario: Scenario
    question: Question
    attempt: int = 1  # 2+ means the previous answer did not settle the question


@dataclass(frozen=True)
class Place:
    facility: Facility
    label: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class Done:
    """The scenario reached an outcome: book, route, handoff or emergency."""

    scenario: Scenario
    outcome_id: str
    outcome: Outcome
    places: tuple[Place, ...] = ()
    notices: tuple[Notice, ...] = ()

    @property
    def kind(self) -> str:
        return self.outcome.type

    @property
    def offer_admin(self) -> bool:
        """Route outcomes where the script also says «запиши на плановый / переводи на КЦ»."""
        return self.outcome.type == "route" and self.outcome.handoff_reason is not None


Step = AskPatient | AskQuestion | Done

FALLBACK_OUTCOME = "fallback"


class Engine:
    def __init__(self, kb: KnowledgeBase, clinic: ClinicData) -> None:
        self.kb = kb
        self.clinic = clinic

    # --- public API --------------------------------------------------------------------

    def start(
        self, state: DialogState, scenario_id: str, facts: Mapping[str, object] | None = None
    ) -> Step:
        """Begin a scenario. ``facts`` may already hold answers taken from the complaint."""
        scenario = self.kb.scenario(scenario_id)
        state.scenario_id = scenario.id
        state.question_id = None
        state.outcome_id = None
        state.asks = 0
        state.trail = [scenario.id]
        state.facts = {k: v for k, v in state.facts.items() if k in CONTEXT_FACTS}
        self._absorb(state, scenario, facts or {})
        return self._enter(state, jumps=0)

    def set_patient(
        self, state: DialogState, audience: Audience, age_months: int | None = None
    ) -> Step | None:
        """Record who the visit is for; continue a pending scenario if there is one."""
        if age_months is not None:
            state.age_months = age_months
            if age_months >= ADULT_FROM_MONTHS:
                audience = "adult"
        state.facts["audience"] = audience
        return self._enter(state, jumps=0) if state.pending else None

    def answer(self, state: DialogState, facts: Mapping[str, object]) -> Step:
        """Apply the patient's answer to the current question and move on."""
        if state.scenario_id is None or state.question_id is None:
            raise RuntimeError("no question is waiting for an answer")
        scenario = self.kb.scenario(state.scenario_id)
        self._absorb(state, scenario, facts)
        return self._visit(state, scenario, scenario.question(state.question_id), jumps=0)

    # --- graph walking -----------------------------------------------------------------

    def _enter(self, state: DialogState, jumps: int) -> Step:
        assert state.scenario_id is not None
        scenario = self.kb.scenario(state.scenario_id)
        if state.audience is None and _needs_audience(scenario):
            return AskPatient("audience")
        if state.audience == "child" and state.age_months is None:
            return AskPatient("age")
        if (
            state.audience == "child"
            and state.age_months is not None
            and state.age_months < INFANT_UNDER_MONTHS
            and scenario.id != INFANT_SCENARIO
        ):
            return self._jump(state, scenario, Branch(scenario=INFANT_SCENARIO), jumps)
        return self._follow(state, scenario, scenario.entry, jumps)

    def _visit(
        self, state: DialogState, scenario: Scenario, question: Question, jumps: int
    ) -> Step:
        missing = [f for f in question.fields if f.name not in state.facts and f.default is None]
        if missing:
            if state.question_id == question.id:
                state.asks += 1
            else:
                state.question_id, state.asks = question.id, 1
            if state.asks > MAX_ASKS:
                return self._fallback(
                    state, scenario, f"не удалось понять ответ на «{question.ask}»"
                )
            return AskQuestion(scenario, question, attempt=state.asks)
        for f in question.fields:
            if f.name not in state.facts and f.default is not None:
                state.facts[f.name] = f.default
        return self._follow(state, scenario, question.branches, jumps)

    def _follow(
        self, state: DialogState, scenario: Scenario, branches: tuple[Branch, ...], jumps: int
    ) -> Step:
        branch = next((b for b in branches if b.matches(state.facts)), None)
        if branch is None:
            return self._fallback(state, scenario, "нет ветки сценария для этих ответов")
        if branch.next is not None:
            return self._visit(state, scenario, scenario.question(branch.next), jumps)
        if branch.outcome is not None:
            return self._finish(state, scenario, branch.outcome)
        return self._jump(state, scenario, branch, jumps)

    def _jump(self, state: DialogState, scenario: Scenario, branch: Branch, jumps: int) -> Step:
        assert branch.scenario is not None
        if jumps >= MAX_JUMPS:
            return self._fallback(state, scenario, "слишком много переходов между сценариями")
        target = self.kb.scenario(branch.scenario)
        state.scenario_id = target.id
        state.question_id = None
        state.asks = 0
        state.trail.append(target.id)
        state.facts = {k: v for k, v in state.facts.items() if k in CONTEXT_FACTS} | branch.carry
        return self._enter(state, jumps + 1)

    def _finish(self, state: DialogState, scenario: Scenario, outcome_id: str) -> Done:
        outcome = scenario.outcomes[outcome_id]
        state.question_id = None
        state.outcome_id = outcome_id
        places = tuple(
            Place(self.kb.facilities[d.facility], d.label, d.note) for d in outcome.destinations
        )
        notices: tuple[Notice, ...] = ()
        if outcome.type == "book":
            notices = tuple(n for n in scenario.notices if self._flags_match(n))
        return Done(scenario, outcome_id, outcome, places, notices)

    def _fallback(self, state: DialogState, scenario: Scenario, why: str) -> Done:
        log.warning("scenario %s: %s (facts=%s)", scenario.id, why, state.facts)
        state.question_id = None
        state.outcome_id = FALLBACK_OUTCOME
        who = {"child": "Ребёнок", "adult": "Взрослый"}.get(state.audience or "", "Пациент")
        outcome = Outcome(
            type="handoff",
            handoff_reason=f"{who}: {why} (сценарий {scenario.number} «{scenario.title}»)",
            source="engine fallback",
        )
        return Done(scenario, FALLBACK_OUTCOME, outcome)

    # --- helpers -----------------------------------------------------------------------

    def _absorb(self, state: DialogState, scenario: Scenario, facts: Mapping[str, object]) -> None:
        """Keep valid answers for this scenario's fields; ignore anything else."""
        fields = scenario.fields()
        for key, value in facts.items():
            if value is None:
                continue
            if key == "audience":
                if value in AUDIENCES:
                    state.facts["audience"] = value  # type: ignore[assignment]
                continue
            spec = fields.get(key)
            if spec is None or not spec.accepts(value):
                log.warning("scenario %s: ignored fact %s=%r", scenario.id, key, value)
                continue
            state.facts[key] = value  # type: ignore[assignment]

    def _flags_match(self, notice: Notice) -> bool:
        return all(getattr(self.clinic.clinic, k) == v for k, v in notice.when_config.items())


def _needs_audience(scenario: Scenario) -> bool:
    """Booking needs it for documents; other scenarios only if a branch depends on it."""
    return scenario.kind == "booking" or any("audience" in b.when for b in scenario.all_branches())
