"""Extraction examples (tests/data/extraction_cases.yaml) against the knowledge base.

Offline half: each case names a real question and a valid value, and the engine leads the
expected answer the right way. The LLM half (does DeepSeek return that value for the
phrase?) is the live test of stage 3.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.flow.engine import AskQuestion, DialogState, Done, Engine
from app.flow.loader import KnowledgeBase

CASES = yaml.safe_load((Path(__file__).parent / "data" / "extraction_cases.yaml").read_text())


def case_id(case: dict) -> str:
    return f"{case['scenario']}.{case['question']}: {case['text']}"


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_case_fits_the_scenario(kb: KnowledgeBase, case: dict) -> None:
    question = kb.scenario(case["scenario"]).question(case["question"])
    own = {f.name: f for f in question.fields}
    for name, value in case["expect"].items():
        assert name in own, f"{name} is not asked by {case['question']}"
        assert own[name].accepts(value), (name, value)


def test_training_injury_is_household(engine: Engine) -> None:
    """«Подвернул ногу на тренировке» → бытовая: the scenario goes on, not to 5.1."""
    case = next(c for c in CASES if c["text"] == "подвернул ногу на тренировке")
    state = DialogState()
    engine.set_patient(state, "adult")
    engine.start(state, case["scenario"])
    step = engine.answer(state, case["expect"])
    assert isinstance(step, AskQuestion) and step.question.id == "when"
    assert state.trail == ["dislocation"]


def test_hit_by_car_is_criminal(engine: Engine) -> None:
    case = next(c for c in CASES if c["text"] == "меня сбила машина")
    state = DialogState()
    engine.set_patient(state, "adult")
    engine.start(state, case["scenario"])
    step = engine.answer(state, case["expect"])
    assert isinstance(step, Done) and step.scenario.id == "criminal_trauma"


def test_assault_definition_is_shared(kb: KnowledgeBase) -> None:
    """One wording of «бытовая / криминальная» for every «Как получена травма?»."""
    shared = kb.common.shared_fields["assault"].field
    for word in ("на тренировке", "на работе", "на улице", "дома", "сбила машина", "побои"):
        assert word in shared.description, word
    users = [sc.id for sc in kb.scenarios.values() if "assault" in sc.fields()]
    assert sorted(users) == ["dislocation", "fracture", "ligament_rupture"]
    for sid in users:
        assert kb.scenario(sid).fields()["assault"] == shared
