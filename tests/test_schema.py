"""The scenario schema rejects broken graphs, so JSON mistakes fail fast."""

import pytest
from pydantic import ValidationError

from app.flow.schema import Branch, Outcome, Scenario

BOOK = {
    "type": "book",
    "booking": {"visit": "primary", "service": "x", "slot_policy": "nearest", "price_key": "p"},
    "source": "test",
}
HANDOFF = {"type": "handoff", "handoff_reason": "Взрослый: x", "source": "test"}


def scenario(**overrides: object) -> dict:
    data = {
        "id": "demo",
        "script": "00-00_demo",
        "number": "0",
        "title": "Demo",
        "kind": "booking",
        "router_hint": "demo",
        "entry": [{"next": "q1"}],
        "questions": [
            {
                "id": "q1",
                "ask": "?",
                "source": "test",
                "fields": [{"name": "ok", "type": "bool", "description": "d"}],
                "branches": [
                    {"when": {"ok": True}, "outcome": "book"},
                    {"when": {"ok": False}, "outcome": "handoff"},
                ],
            }
        ],
        "outcomes": {"book": BOOK, "handoff": HANDOFF},
        "summary": "{patient} {date} {time} {price} {address}",
    }
    data.update(overrides)
    return data


def test_valid_scenario() -> None:
    Scenario.model_validate(scenario())


def test_unknown_next_question() -> None:
    with pytest.raises(ValidationError, match="unknown question"):
        Scenario.model_validate(scenario(entry=[{"next": "nope"}]))


def test_condition_value_must_fit_field() -> None:
    bad = scenario()
    bad["questions"][0]["branches"][0]["when"] = {"ok": "yes"}
    with pytest.raises(ValidationError, match="bad value"):
        Scenario.model_validate(bad)


def test_condition_on_unknown_field() -> None:
    bad = scenario()
    bad["questions"][0]["branches"][0]["when"] = {"missing": True}
    with pytest.raises(ValidationError, match="unknown field"):
        Scenario.model_validate(bad)


def test_unreachable_question() -> None:
    bad = scenario()
    extra = dict(bad["questions"][0], id="q2")
    extra["fields"] = [{"name": "other", "type": "bool", "description": "d"}]
    extra["branches"] = [{"outcome": "book"}]
    bad["questions"].append(extra)
    with pytest.raises(ValidationError, match="unreachable"):
        Scenario.model_validate(bad)


def test_loop_rejected() -> None:
    bad = scenario()
    bad["questions"][0]["branches"][1] = {"when": {"ok": False}, "next": "q1"}
    with pytest.raises(ValidationError, match="loop"):
        Scenario.model_validate(bad)


def test_unused_outcome_rejected() -> None:
    bad = scenario()
    bad["outcomes"]["spare"] = HANDOFF
    with pytest.raises(ValidationError, match="unused outcomes"):
        Scenario.model_validate(bad)


def test_routing_scenario_cannot_book() -> None:
    with pytest.raises(ValidationError, match="must not book"):
        Scenario.model_validate(scenario(kind="routing"))


def test_unknown_summary_placeholder() -> None:
    with pytest.raises(ValidationError, match="placeholders"):
        Scenario.model_validate(scenario(summary="{patient} {doctor}"))


def test_branch_needs_exactly_one_target() -> None:
    with pytest.raises(ValidationError):
        Branch.model_validate({"next": "a", "outcome": "b"})
    with pytest.raises(ValidationError):
        Branch.model_validate({"when": {"x": True}})


def test_route_needs_destinations() -> None:
    with pytest.raises(ValidationError, match="destinations"):
        Outcome.model_validate({"type": "route", "source": "t"})


def test_handoff_with_addresses_must_be_route() -> None:
    with pytest.raises(ValidationError, match="must be a route"):
        Outcome.model_validate({**HANDOFF, "destinations": [{"facility": "dgkb9"}]})


def test_branch_matching_is_type_strict() -> None:
    branch = Branch.model_validate({"when": {"flag": True, "kind": ["a", "b"]}, "outcome": "x"})
    assert branch.matches({"flag": True, "kind": "a"})
    assert not branch.matches({"flag": 1, "kind": "a"})
    assert not branch.matches({"flag": True, "kind": "c"})
    assert not branch.matches({"kind": "a"})
