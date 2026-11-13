"""Pydantic schema for the knowledge base in kb/scenarios/.

A scenario is a small decision graph built from one clinic call-centre script:

* ``entry`` branches are evaluated as soon as the scenario starts (e.g. split by audience);
* each question asks one operator phrase and extracts one or more typed fields;
* branches are checked in order, the first one whose ``when`` matches the known facts wins
  and leads to the next question, an outcome, or another scenario;
* outcomes are terminal: book in the trauma centre, route elsewhere, hand off to an
  administrator, or advise calling an ambulance.

The engine (stage 2) picks branches; the LLM only fills fields and phrases replies.
"""

from __future__ import annotations

import string
from collections.abc import Mapping
from datetime import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Audience = Literal["child", "adult"]
AUDIENCES: tuple[Audience, ...] = ("child", "adult")
# Body areas for choosing topics by context (part C): limb — arm, leg; head — head, face,
# eyes, ears; torso — chest, abdomen, back, neck, genitals; any — not tied to one area.
Area = Literal["limb", "head", "torso"]
AREAS: tuple[Area, ...] = ("limb", "head", "torso")
BodyArea = Literal["limb", "head", "torso", "any"]
# Facts known before any scenario question is asked.
CONTEXT_FACTS = frozenset({"audience"})

FactValue = bool | str
SlotPolicy = Literal["nearest", "repeat_window", "near_repeat_window"]
SNAKE = r"^[a-z][a-z0-9_]*$"
SUMMARY_PLACEHOLDERS = frozenset({"patient", "booked", "date", "time", "price", "address"})


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


BUTTON_MAX = 40


class Option(_Model):
    value: str = Field(pattern=SNAKE)
    meaning: str = Field(min_length=1, description="Script wording, shown to the LLM extractor")
    label: str = Field(min_length=1, max_length=BUTTON_MAX, description="Button text")


class Choice(_Model):
    """A button: pressing it sets these facts, no LLM involved."""

    label: str = Field(min_length=1, max_length=BUTTON_MAX)
    facts: dict[str, FactValue] = Field(min_length=1)


class FieldSpec(_Model):
    name: str = Field(pattern=SNAKE)
    type: Literal["bool", "enum"]
    description: str = Field(min_length=1)
    options: tuple[Option, ...] = ()
    default: FactValue | None = Field(
        default=None,
        description="Value taken when the patient's answer does not mention the field "
        "(the script says «если пациент сам говорит…»)",
    )
    yes_label: str | None = Field(default=None, max_length=BUTTON_MAX)
    no_label: str | None = Field(default=None, max_length=BUTTON_MAX)

    @model_validator(mode="after")
    def _check_options(self) -> FieldSpec:
        if self.type == "bool" and self.options:
            raise ValueError(f"bool field {self.name!r} must not have options")
        if self.type == "enum":
            values = [o.value for o in self.options]
            if len(values) < 2 or len(set(values)) != len(values):
                raise ValueError(f"enum field {self.name!r} needs 2+ unique options")
        if self.default is not None and not self.accepts(self.default):
            raise ValueError(f"bad default for {self.name!r}: {self.default!r}")
        if self.type == "enum" and (self.yes_label or self.no_label):
            raise ValueError(f"yes/no labels are for bool fields only ({self.name!r})")
        return self

    def choices(self) -> tuple[Choice, ...]:
        """One button per allowed value."""
        if self.type == "bool":
            return (
                Choice(label=self.yes_label or "Да", facts={self.name: True}),
                Choice(label=self.no_label or "Нет", facts={self.name: False}),
            )
        return tuple(Choice(label=o.label, facts={self.name: o.value}) for o in self.options)

    def values(self) -> tuple[FactValue, ...]:
        if self.type == "bool":
            return (True, False)
        return tuple(o.value for o in self.options)

    def accepts(self, value: object) -> bool:
        if self.type == "bool":
            return isinstance(value, bool)
        return isinstance(value, str) and value in self.values()


class Branch(_Model):
    when: dict[str, FactValue | list[str]] = {}
    next: str | None = None
    outcome: str | None = None
    scenario: str | None = None
    carry: dict[str, FactValue] = Field(
        default={}, description="Facts handed over to the target scenario"
    )

    @model_validator(mode="after")
    def _one_target(self) -> Branch:
        targets = [t for t in (self.next, self.outcome, self.scenario) if t is not None]
        if len(targets) != 1:
            raise ValueError("branch needs exactly one of next / outcome / scenario")
        if self.carry and self.scenario is None:
            raise ValueError("carry is only allowed when jumping to another scenario")
        return self

    def matches(self, facts: Mapping[str, FactValue]) -> bool:
        for key, expected in self.when.items():
            if key not in facts:
                return False
            actual = facts[key]
            if isinstance(expected, list):
                if actual not in expected:
                    return False
            elif type(actual) is not type(expected) or actual != expected:
                return False
        return True


class Question(_Model):
    id: str = Field(pattern=SNAKE)
    ask: str = Field(min_length=1, description="Operator phrase from the script")
    source: str
    fields: tuple[FieldSpec, ...] = Field(min_length=1)
    choices: tuple[Choice, ...] = Field(
        default=(), description="Buttons; derived from the field when the question has one"
    )
    branches: tuple[Branch, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_choices(self) -> Question:
        if not self.choices:
            if len(self.fields) > 1:
                raise ValueError(f"question {self.id!r} with several fields needs explicit choices")
            return self
        own = {f.name: f for f in self.fields}
        required = {f.name for f in self.fields if f.default is None}
        labels = [c.label for c in self.choices]
        if len(labels) < 2 or len(set(labels)) != len(labels):
            raise ValueError(f"question {self.id!r} needs 2+ distinct choices")
        for choice in self.choices:
            for key, value in choice.facts.items():
                if key not in own or not own[key].accepts(value):
                    raise ValueError(f"choice {choice.label!r}: bad fact {key}={value!r}")
            if missing := required - set(choice.facts):
                raise ValueError(f"choice {choice.label!r} leaves {sorted(missing)} unanswered")
        return self

    def buttons(self) -> tuple[Choice, ...]:
        return self.choices or self.fields[0].choices()


class Destination(_Model):
    facility: str = Field(pattern=SNAKE)
    label: str | None = Field(default=None, description="Who goes there, e.g. «Мальчики до 18 лет»")
    note: str | None = None


class Booking(_Model):
    visit: Literal["primary", "repeat"]
    service: str
    slot_policy: SlotPolicy
    price_key: str = Field(pattern=SNAKE)


class Outcome(_Model):
    """Terminal step.

    * book — booking in the trauma centre;
    * route — go elsewhere (addresses); ``handoff_reason`` set means the script also allows
      «запиши на плановый приём / переводи на КЦ», so the bot offers an administrator as well;
    * handoff — pass to an administrator (ADMIN_CHAT_ID) with ``handoff_reason`` as context;
    * emergency — advise 103/112, optionally with addresses from the script.
    """

    type: Literal["book", "route", "handoff", "emergency"]
    say: tuple[str, ...] = Field(default=(), description="Operator phrases for the patient")
    destinations: tuple[Destination, ...] = ()
    handoff_reason: str | None = None
    booking: Booking | None = None
    source: str
    verify: str | None = Field(default=None, description="Needs confirmation from the clinic")
    todo: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check_shape(self) -> Outcome:
        if (self.type == "book") != (self.booking is not None):
            raise ValueError("booking is required for and only for book outcomes")
        if self.type in ("book", "emergency") and self.handoff_reason:
            raise ValueError(f"{self.type} outcome must not have handoff_reason")
        if self.type == "book" and self.destinations:
            raise ValueError("book outcome must not have destinations")
        if self.type == "route" and not self.destinations:
            raise ValueError("route outcome needs destinations")
        if self.type == "handoff":
            if not self.handoff_reason:
                raise ValueError("handoff outcome needs handoff_reason")
            if self.destinations:
                raise ValueError("handoff with addresses must be a route with handoff_reason")
        return self


class Phrase(_Model):
    text: str
    source: str


class Notice(_Model):
    text: str
    source: str
    when_config: dict[str, bool] = Field(
        default={}, description="Shown only when these config/clinic.yaml flags match"
    )


class Scenario(_Model):
    id: str = Field(pattern=SNAKE)
    script: str = Field(description="Source file stem in kb/text/")
    decision: str | None = Field(
        default=None,
        description="Clinic answer the scenario is built from when the export has no script "
        "page for it; ``script`` then names the page that links to it (the 02 menu)",
    )
    number: str
    title: str
    menu_label: str = Field(min_length=1, max_length=BUTTON_MAX, description="Menu button")
    kind: Literal["booking", "routing"]
    body_areas: tuple[BodyArea, ...] = Field(
        min_length=1, description="Where the injury is; topics of other areas are not shown"
    )
    router_hint: str = Field(description="When the scenario applies, in the script's words")
    entry: tuple[Branch, ...] = Field(min_length=1)
    questions: tuple[Question, ...] = ()
    outcomes: dict[str, Outcome] = Field(min_length=1)
    notices: tuple[Notice, ...] = ()
    summary: str | None = Field(default=None, description="Booking summary template, step 5")
    operator_phrases: dict[str, Phrase] = Field(
        default={}, description="Extra script phrases to use on request (objections, arguments)"
    )
    operator_notes: tuple[str, ...] = Field(
        default=(), description="Staff-only reference; never shown to patients or sent to the LLM"
    )
    todo: tuple[str, ...] = ()

    def fits(self, area: str | None) -> bool:
        """Can be offered once the patient named this body area (None: anything goes)."""
        return area is None or "any" in self.body_areas or area in self.body_areas

    def question(self, question_id: str) -> Question:
        for q in self.questions:
            if q.id == question_id:
                return q
        raise KeyError(question_id)

    def fields(self) -> dict[str, FieldSpec]:
        return {f.name: f for q in self.questions for f in q.fields}

    def all_branches(self) -> list[Branch]:
        return [*self.entry, *(b for q in self.questions for b in q.branches)]

    @model_validator(mode="after")
    def _check_integrity(self) -> Scenario:
        question_ids = [q.id for q in self.questions]
        if len(set(question_ids)) != len(question_ids):
            raise ValueError("duplicate question ids")
        field_names = [f.name for q in self.questions for f in q.fields]
        if len(set(field_names)) != len(field_names):
            raise ValueError("a field may be extracted by one question only")
        fields = self.fields()

        for branch in self.all_branches():
            if branch.next is not None and branch.next not in question_ids:
                raise ValueError(f"unknown question {branch.next!r}")
            if branch.outcome is not None and branch.outcome not in self.outcomes:
                raise ValueError(f"unknown outcome {branch.outcome!r}")
            for key, expected in branch.when.items():
                values = expected if isinstance(expected, list) else [expected]
                if key == "audience":
                    if any(v not in AUDIENCES for v in values):
                        raise ValueError(f"bad audience value in {branch.when}")
                elif key not in fields:
                    raise ValueError(f"condition on unknown field {key!r}")
                elif not all(fields[key].accepts(v) for v in values):
                    raise ValueError(f"bad value for {key!r} in {branch.when}")

        self._check_graph(question_ids)

        used = {b.outcome for b in self.all_branches() if b.outcome}
        if unused := set(self.outcomes) - used:
            raise ValueError(f"unused outcomes: {sorted(unused)}")

        has_booking = any(o.type == "book" for o in self.outcomes.values())
        if self.kind == "booking":
            if not has_booking or not self.summary:
                raise ValueError("booking scenario needs a book outcome and a summary")
            names = {n for _, n, _, _ in string.Formatter().parse(self.summary) if n}
            if bad := names - SUMMARY_PLACEHOLDERS:
                raise ValueError(f"unknown summary placeholders: {sorted(bad)}")
        elif has_booking or self.summary:
            raise ValueError("routing scenario must not book")
        return self

    def _check_graph(self, question_ids: list[str]) -> None:
        """Every question reachable from entry, and no loops."""
        edges = {q.id: [b.next for b in q.branches if b.next] for q in self.questions}
        reachable: set[str] = set()
        stack = [b.next for b in self.entry if b.next]
        while stack:
            qid = stack.pop()
            if qid not in reachable:
                reachable.add(qid)
                stack.extend(edges[qid])
        if unreachable := set(question_ids) - reachable:
            raise ValueError(f"unreachable questions: {sorted(unreachable)}")

        state: dict[str, int] = {}  # 1 = visiting, 2 = done

        def visit(qid: str) -> None:
            state[qid] = 1
            for nxt in edges[qid]:
                if state.get(nxt) == 1:
                    raise ValueError(f"loop through question {nxt!r}")
                if nxt not in state:
                    visit(nxt)
            state[qid] = 2

        for qid in question_ids:
            if qid not in state:
                visit(qid)


class Facility(_Model):
    id: str = Field(pattern=SNAKE)
    name: str
    address: str | None
    note: str | None = None
    source: str = Field(description="Script stem in kb/text/ that names this place")
    quotes: tuple[str, ...] = Field(
        min_length=1, description="Verbatim fragments of the source script (whitespace aside)"
    )
    verify: str | None = None


class SharedField(_Model):
    """A field asked in several scripts that must mean the same everywhere."""

    field: FieldSpec
    source: str


class GreetingSlot(_Model):
    start: time
    end: time
    text: str


class CommonPhrases(_Model):
    """Phrases shared by all booking scripts (steps 2–8) and the greeting."""

    greeting_slots: tuple[GreetingSlot, ...] = Field(min_length=1)
    greeting: Phrase
    wait: Phrase
    offer: Phrase
    identify: Phrase
    identify_thanks: Phrase
    bring_adult: Phrase
    bring_adult_first_visit: Phrase
    bring_child: Phrase
    bring_child_first_visit: Phrase
    more_help: Phrase
    goodbye: Phrase
    no_walk_in_objection: Phrase
    parking_after_20: Phrase
    parking_day: Phrase
    parking_night: Phrase
    shared_fields: dict[str, SharedField] = Field(
        default={}, description="Scenario fields with this name must equal the shared spec"
    )
    todo: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _shared_names(self) -> CommonPhrases:
        for key, shared in self.shared_fields.items():
            if key != shared.field.name:
                raise ValueError(f"shared field key {key!r} != name {shared.field.name!r}")
        return self

    @model_validator(mode="after")
    def _greeting_covers_day(self) -> CommonPhrases:
        minutes = [0] * (24 * 60)
        for slot in self.greeting_slots:
            start = slot.start.hour * 60 + slot.start.minute
            end = slot.end.hour * 60 + slot.end.minute
            span = range(start, end) if start < end else [*range(start, 1440), *range(0, end)]
            for m in span:
                minutes[m] += 1
        if any(count != 1 for count in minutes):
            raise ValueError("greeting slots must cover the day exactly once")
        return self

    def greeting_for(self, at: time) -> str:
        minute = at.hour * 60 + at.minute
        for slot in self.greeting_slots:
            start = slot.start.hour * 60 + slot.start.minute
            end = slot.end.hour * 60 + slot.end.minute
            inside = start <= minute < end if start < end else (minute >= start or minute < end)
            if inside:
                return slot.text
        raise AssertionError("validated slots cover the whole day")


class MenuItem(_Model):
    """A topic button: opens another menu, starts a scenario, or an action."""

    id: str = Field(pattern=SNAKE, description="Unique across all menus (callback data)")
    label: str = Field(min_length=1, max_length=BUTTON_MAX)
    menu: str | None = None
    scenario: str | None = None
    action: Literal["describe", "admin"] | None = None
    area: Area | None = Field(default=None, description="Choosing it names the body area")
    facts: dict[str, FactValue] = Field(
        default={}, description="Answers the button already gives (passed to the scenario)"
    )

    @model_validator(mode="after")
    def _one_target(self) -> MenuItem:
        if sum(t is not None for t in (self.menu, self.scenario, self.action)) != 1:
            raise ValueError(f"menu item {self.id!r} needs exactly one of menu/scenario/action")
        if self.facts and self.scenario is None:
            raise ValueError(f"menu item {self.id!r}: facts only with a scenario")
        return self


class Menu(_Model):
    text: str = Field(min_length=1)
    items: tuple[MenuItem, ...] = Field(min_length=2)


class TopicTree(_Model):
    """kb/scenarios/_menu.json: topics about what happened, not diagnoses (part C)."""

    root: str = Field(description="«Выбрать из списка» before any complaint: five groups")
    areas: dict[Area, str] = Field(description="Menu shown once the body area is known")
    menus: dict[str, Menu]
    area_facts: dict[Area, dict[str, dict[str, FactValue]]] = Field(
        default={}, description="Answers implied by the area, per scenario (a burn on a hand)"
    )
    todo: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _links(self) -> TopicTree:
        ids = [i.id for m in self.menus.values() for i in m.items]
        if len(set(ids)) != len(ids):
            raise ValueError("menu item ids must be unique")
        for name in (self.root, *self.areas.values()):
            if name not in self.menus:
                raise ValueError(f"unknown menu {name!r}")
        for menu in self.menus.values():
            for item in menu.items:
                if item.menu is not None and item.menu not in self.menus:
                    raise ValueError(f"item {item.id!r}: unknown menu {item.menu!r}")
        return self

    def item(self, item_id: str) -> MenuItem | None:
        return next((i for m in self.menus.values() for i in m.items if i.id == item_id), None)
