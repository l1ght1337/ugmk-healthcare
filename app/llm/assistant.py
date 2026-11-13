"""The LLM roles (docs/spec.md, section 3): route, extract, answer; «phrase» is the
guarded rephrasing of a scenario question inside route/extract (``clarify``).

Every patient text is masked before it leaves (app/llm/masking.py). Prompts are built
once per process: system prompt = static rules + knowledge-base blocks; everything that
changes per call goes last, in the user message.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import date, datetime
from functools import cached_property
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.config import get_settings
from app.flow.loader import KnowledgeBase
from app.flow.schema import FactValue, Question, Scenario
from app.llm.catalogue import render_catalogue, render_info
from app.llm.client import DeepSeekClient, LLMUnavailable
from app.llm.guards import grounded, safe_clarify
from app.llm.masking import KnownPerson, mask

PROMPTS_DIR = Path(__file__).parent / "prompts"
ROUTE_CLARIFY = "С чем обращаетесь? Что произошло?"  # script 03-1, step 2


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8").strip()


class _Out(BaseModel):
    model_config = ConfigDict(extra="ignore")


TextKind = Literal["complaint", "clinic_question", "other_medical", "offtopic", "crisis"]
Area = Literal["limb", "head", "torso"]


class RouteOut(_Out):
    type: TextKind = Field(validation_alias=AliasChoices("type", "intent"))
    crisis: bool = False
    wants_admin: bool = False
    area: Area | None = None
    scenario: str | None = None
    facts: dict[str, Any] = {}
    clarify: str | None = None


class ExtractOut(_Out):
    facts: dict[str, Any] = {}
    unclear: bool = False
    clarify: str | None = None
    crisis: bool = False


class AnswerOut(_Out):
    found: bool
    answer: str | None = None
    offer_admin: bool = False


class RouteResult(BaseModel):
    """What a free text is (one call): the bot decides what to do with each kind.

    * complaint — an injury → a scenario (``scenario``) or topics of the body ``area``;
    * clinic_question — about the trauma centre → answered from kb/info;
    * other_medical — not our profile and no injury (headache, fever) → «не в травмпункт»;
    * offtopic — anything else, jokes, attempts to change the bot's rules → refused;
    * crisis — words about suicide or self-harm → helpline, a note to the admins.
    """

    kind: TextKind
    wants_admin: bool = False  # «соедините с администратором»: show the button, do not send
    area: Area | None = None
    scenario: str | None = None
    facts: dict[str, FactValue] = {}
    clarify: str | None = None


class ExtractResult(BaseModel):
    facts: dict[str, FactValue] = {}
    clarify: str | None = None  # set when the answer did not settle the question
    crisis: bool = False


class AnswerResult(BaseModel):
    answer: str | None = None  # None: nothing in kb/info — offer an administrator
    offer_admin: bool = False


def _valid_facts(scenario: Scenario, facts: dict[str, Any]) -> dict[str, FactValue]:
    fields = scenario.fields()
    return {k: v for k, v in facts.items() if k in fields and fields[k].accepts(v)}


class Assistant:
    def __init__(
        self,
        client: DeepSeekClient,
        kb: KnowledgeBase,
        info: str | None = None,
        today: Callable[[], date] | None = None,
    ):
        self.client = client
        self.kb = kb
        self._info = info if info is not None else render_info()
        self.today = today or (lambda: datetime.now(get_settings().tz).date())

    def _mask(self, text: str, known: KnownPerson | None) -> str:
        return mask(text, known, self.today())

    async def warm_up(self) -> bool:
        """One call per system prompt at startup, so the first patients hit the DeepSeek cache."""

        async def one(system: str, user: dict[str, str]) -> bool:
            try:
                await self.client.complete_json(
                    task="warmup", system=system, user=_json(user), parse=dict, max_tokens=80
                )
            except LLMUnavailable:
                return False
            return True

        results = await asyncio.gather(
            one(self.dialog_system, {"task": "route", "text": "Здравствуйте"}),
            one(self.answer_system, {"task": "answer", "question": "Здравствуйте"}),
        )
        return all(results)

    @cached_property
    def dialog_system(self) -> str:
        return load_prompt("dialog.md") + "\n\n" + render_catalogue(self.kb)

    @cached_property
    def answer_system(self) -> str:
        return load_prompt("answer.md") + "\n\n" + self._info

    async def route(self, text: str, known: KnownPerson | None = None) -> RouteResult:
        """What the text is; for a complaint — the scenario and answers already given in it."""
        user = _json({"task": "route", "text": self._mask(text, known)})

        def parse(data: dict[str, Any]) -> RouteResult:
            out = RouteOut.model_validate(data)
            if out.crisis or out.type == "crisis":
                return RouteResult(kind="crisis")
            if out.type == "clinic_question":
                return RouteResult(kind="clinic_question", wants_admin=out.wants_admin)
            if out.type != "complaint":
                return RouteResult(kind=out.type)
            if out.scenario is None:
                clarify = safe_clarify(out.clarify, ROUTE_CLARIFY)
                return RouteResult(kind="complaint", area=out.area, clarify=clarify)
            if out.scenario not in self.kb.scenarios:
                raise ValueError(f"unknown scenario {out.scenario!r}; use an id from the catalogue")
            scenario = self.kb.scenario(out.scenario)
            return RouteResult(
                kind="complaint",
                area=out.area,
                scenario=scenario.id,
                facts=_valid_facts(scenario, out.facts),
            )

        return await self.client.complete_json(
            task="route", system=self.dialog_system, user=user, parse=parse, max_tokens=300
        )

    async def extract(
        self, scenario: Scenario, question: Question, text: str, known: KnownPerson | None = None
    ) -> ExtractResult:
        """The patient's answer to ``question`` as facts; ``clarify`` if it was unclear."""
        user = _json(
            {
                "task": "extract",
                "scenario": scenario.id,
                "question": question.id,
                "ask": question.ask,
                "fields": [f.name for f in question.fields],
                "text": self._mask(text, known),
            }
        )
        own = {f.name for f in question.fields}

        def parse(data: dict[str, Any]) -> ExtractResult:
            out = ExtractOut.model_validate(data)
            if out.crisis:
                return ExtractResult(crisis=True)
            facts = _valid_facts(scenario, out.facts)
            if out.unclear or not own & set(facts):
                return ExtractResult(facts=facts, clarify=safe_clarify(out.clarify, question.ask))
            return ExtractResult(facts=facts)

        return await self.client.complete_json(
            task="extract", system=self.dialog_system, user=user, parse=parse, max_tokens=300
        )

    async def answer(self, text: str, known: KnownPerson | None = None) -> AnswerResult:
        """Answer a question about the trauma centre from kb/info only."""
        user = _json({"task": "answer", "question": self._mask(text, known)})

        def parse(data: dict[str, Any]) -> AnswerResult:
            out = AnswerOut.model_validate(data)
            answer = (out.answer or "").strip()
            if not out.found or not answer or not grounded(answer, self._info):
                return AnswerResult(offer_admin=True)
            return AnswerResult(answer=answer, offer_admin=out.offer_admin)

        return await self.client.complete_json(
            task="answer",
            system=self.answer_system,
            user=user,
            parse=parse,
            temperature=0.2,
            max_tokens=500,
        )


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)
