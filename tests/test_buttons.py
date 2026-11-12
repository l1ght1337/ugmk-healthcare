"""Every step can be answered with buttons alone; typed age is parsed without the LLM."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.bot import render, texts
from app.flow.age import parse_age_months
from app.flow.engine import AskPatient, AskQuestion, DialogState, Engine
from app.flow.loader import KnowledgeBase, load_kb
from app.flow.schema import BUTTON_MAX, Question

QUESTIONS = [(sc.id, q.id) for sc in load_kb().scenarios.values() for q in sc.questions]


@pytest.mark.parametrize(("scenario_id", "question_id"), QUESTIONS)
@pytest.mark.parametrize("audience", ["adult", "child"])
def test_each_button_settles_its_question(
    engine: Engine, kb: KnowledgeBase, scenario_id: str, question_id: str, audience: str
) -> None:
    question = kb.scenario(scenario_id).question(question_id)
    buttons = question.buttons()
    assert len(buttons) >= 2
    assert all(len(b.label) <= BUTTON_MAX for b in buttons)
    for button in buttons:
        state = DialogState(
            scenario_id=scenario_id, question_id=question_id, asks=1,
            facts={"audience": audience}, age_months=120,
        )  # fmt: skip
        step = engine.answer(state, button.facts)
        assert not (isinstance(step, AskQuestion) and step.question.id == question_id), button


def test_audience_is_asked_with_buttons() -> None:
    prompt = render.ask_patient(AskPatient("audience"))
    assert [c.label for c in prompt.choices] == ["Для себя", "Для ребёнка (до 18 лет)"]
    assert [c.facts for c in prompt.choices] == [{"audience": "adult"}, {"audience": "child"}]
    assert render.ask_patient(AskPatient("age")).choices == ()


def test_fracture_buttons_cover_the_script_branches(kb: KnowledgeBase) -> None:
    labels = [c.label for c in kb.scenario("fracture").question("how").buttons()]
    assert labels == ["Бытовая травма", "Открытый перелом или со смещением",
                      "Нападение, драка, сбила машина"]  # fmt: skip


def test_fallback_prompts(engine: Engine, kb: KnowledgeBase) -> None:
    state = DialogState()
    engine.set_patient(state, "adult")
    step = engine.start(state, "burns")
    assert isinstance(step, AskQuestion)
    picked = render.ask_question(step, pick=True)
    assert picked.text.startswith(texts.PICK_BUTTON) and picked.choices
    assert render.ask_question(step, "Что именно обожгли?").text == "Что именно обожгли?"
    menu = render.topic_menu(kb)
    assert {c.facts["scenario"] for c in menu.choices} == set(kb.scenarios)


def _question(**overrides: object) -> dict:
    data = {
        "id": "q",
        "ask": "?",
        "source": "t",
        "fields": [
            {"name": "a", "type": "bool", "description": "d"},
            {"name": "b", "type": "bool", "description": "d", "default": False},
        ],
        "choices": [
            {"label": "Один", "facts": {"a": True}},
            {"label": "Два", "facts": {"a": False}},
        ],
        "branches": [{"outcome": "x"}],
    }
    data.update(overrides)
    return data


def test_question_choice_rules() -> None:
    Question.model_validate(_question())
    with pytest.raises(ValidationError, match="explicit choices"):
        Question.model_validate(_question(choices=[]))
    with pytest.raises(ValidationError, match="unanswered"):
        Question.model_validate(
            _question(
                choices=[{"label": "1", "facts": {"b": True}}, {"label": "2", "facts": {"a": 1}}]
            )
        )
    with pytest.raises(ValidationError, match="bad fact"):
        Question.model_validate(
            _question(
                choices=[
                    {"label": "1", "facts": {"a": "yes"}},
                    {"label": "2", "facts": {"a": True}},
                ]
            )
        )


@pytest.mark.parametrize(
    ("text", "months"),
    [
        ("8", 96), ("8 лет", 96), ("ему восемь лет", 96), ("4 месяца", 4), ("4 мес", 4),
        ("полгода", 6), ("1,5 года", 18), ("полтора года", 18), ("2 года 3 месяца", 27),
        ("две недели", 0), ("новорождённый", 0), ("год", 12), ("Ребёнку 3 года.", 36),
        ("не знаю", None), ("", None),
    ],
)  # fmt: skip
def test_parse_age(text: str, months: int | None) -> None:
    assert parse_age_months(text) == months


def test_button_labels_doc_is_up_to_date(kb: KnowledgeBase) -> None:
    from app.bot.labels import DOC, render_labels

    assert DOC.read_text(encoding="utf-8") == render_labels(kb), (
        "run: uv run python -m app.bot.labels --write"
    )
