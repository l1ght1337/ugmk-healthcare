"""Free text → structured input, with buttons as the fallback.

Buttons never go through here: a pressed button already carries its facts. Free text is
read by the LLM; when DeepSeek is not configured, times out or fails, the result says
``unavailable`` and the bot shows the buttons of the same step instead of waiting.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.flow.schema import FactValue, Question, Scenario
from app.llm.assistant import Assistant
from app.llm.client import LLMUnavailable
from app.llm.masking import KnownPerson

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TextReading:
    """A free text, classified (app/llm/assistant.py, RouteResult)."""

    kind: str | None = None  # complaint / menu_action / clinic_question / other_medical / …
    action: str | None = None  # menu_action: book / my_bookings / ask / admin / home / back
    area: str | None = None
    scenario_id: str | None = None
    candidates: list[str] = field(default_factory=list)
    facts: dict[str, FactValue] = field(default_factory=dict)
    clarify: str | None = None
    question: bool = False  # the message also asks about the trauma centre
    advice: bool = False  # the complaint also asks what to do or what it is
    unavailable: bool = False  # no LLM: the bot falls back to buttons (and the word list)


@dataclass(frozen=True)
class AnswerReading:
    facts: dict[str, FactValue] = field(default_factory=dict)
    clarify: str | None = None
    crisis: bool = False
    unavailable: bool = False  # show the question's buttons (render.ask_question(pick=True))


@dataclass(frozen=True)
class InfoReading:
    answer: str | None = None
    offer_admin: bool = True
    unavailable: bool = False


async def read_text(
    assistant: Assistant | None, text: str, known: KnownPerson | None = None
) -> TextReading:
    if assistant is None:
        return TextReading(unavailable=True)
    try:
        r = await assistant.route(text, known)
    except LLMUnavailable as exc:
        log.warning("route unavailable: %s", exc)
        return TextReading(unavailable=True)
    return TextReading(
        r.kind, r.action, r.area, r.scenario, r.candidates, r.facts, r.clarify, r.question, r.advice
    )


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
    return AnswerReading(r.facts, r.clarify, r.crisis)


async def read_question(
    assistant: Assistant | None,
    text: str,
    known: KnownPerson | None = None,
    on_partial: Callable[[str], Awaitable[None]] | None = None,
) -> InfoReading:
    if assistant is None:
        return InfoReading(unavailable=True)
    try:
        r = await assistant.answer(text, known, on_partial)
    except LLMUnavailable as exc:
        log.warning("answer unavailable: %s", exc)
        return InfoReading(unavailable=True)
    return InfoReading(r.answer, r.offer_admin)
