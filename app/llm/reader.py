"""Free text → structured input, with buttons as the fallback.

Buttons never go through here: a pressed button already carries its facts. Free text is
read by the LLM; when DeepSeek is not configured, times out or fails, the result says
``unavailable`` and the bot shows the buttons of the same step instead of waiting.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.flow.schema import FactValue, Question, Scenario
from app.llm.assistant import Assistant
from app.llm.client import LLMUnavailable
from app.llm.masking import KnownPerson

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ComplaintReading:
    intent: str | None = None  # complaint / question / admin / other
    scenario_id: str | None = None
    facts: dict[str, FactValue] = field(default_factory=dict)
    clarify: str | None = None
    unavailable: bool = False  # show the topic menu (render.topic_menu)


@dataclass(frozen=True)
class AnswerReading:
    facts: dict[str, FactValue] = field(default_factory=dict)
    clarify: str | None = None
    unavailable: bool = False  # show the question's buttons (render.ask_question(pick=True))


@dataclass(frozen=True)
class InfoReading:
    answer: str | None = None
    offer_admin: bool = True
    unavailable: bool = False


async def read_complaint(
    assistant: Assistant | None, text: str, known: KnownPerson | None = None
) -> ComplaintReading:
    if assistant is None:
        return ComplaintReading(unavailable=True)
    try:
        r = await assistant.route(text, known)
    except LLMUnavailable as exc:
        log.warning("route unavailable: %s", exc)
        return ComplaintReading(unavailable=True)
    return ComplaintReading(r.intent, r.scenario, r.facts, r.clarify)


async def read_answer(
    assistant: Assistant | None,
    scenario: Scenario,
    question: Question,
    text: str,
    known: KnownPerson | None = None,
) -> AnswerReading:
    if assistant is None:
        return AnswerReading(unavailable=True)
    try:
        r = await assistant.extract(scenario, question, text, known)
    except LLMUnavailable as exc:
        log.warning("extract unavailable: %s", exc)
        return AnswerReading(unavailable=True)
    return AnswerReading(r.facts, r.clarify)


async def read_question(
    assistant: Assistant | None, text: str, known: KnownPerson | None = None
) -> InfoReading:
    if assistant is None:
        return InfoReading(unavailable=True)
    try:
        r = await assistant.answer(text, known)
    except LLMUnavailable as exc:
        log.warning("answer unavailable: %s", exc)
        return InfoReading(unavailable=True)
    return InfoReading(r.answer, r.offer_admin)
