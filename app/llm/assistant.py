"""The LLM roles (docs/spec.md, section 3): route, extract, answer; «phrase» is the
guarded rephrasing of a scenario question inside route/extract (``clarify``).

Every patient text is masked before it leaves (app/llm/masking.py). Prompts are built
once per process: system prompt = static rules + knowledge-base blocks; everything that
changes per call goes last, in the user message.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from functools import cached_property
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.config import get_settings
from app.flow.loader import KnowledgeBase
from app.flow.schema import AREAS, FactValue, Question, Scenario
from app.llm.catalogue import render_catalogue, render_info
from app.llm.client import DeepSeekClient, LLMUnavailable
from app.llm.guards import grounded, grounded_prefix, safe_clarify
from app.llm.masking import KnownPerson, mask

PROMPTS_DIR = Path(__file__).parent / "prompts"
ROUTE_CLARIFY = "С чем обращаетесь? Что произошло?"  # script 03-1, step 2


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8").strip()


class _Out(BaseModel):
    model_config = ConfigDict(extra="ignore")


TextKind = Literal[
    "complaint", "menu_action", "clinic_question", "other_medical", "offtopic", "crisis"
]
MenuAction = Literal["book", "my_bookings", "ask", "admin"]
Area = Literal["limb", "head", "neck_back", "chest_abdomen", "genital"]


class RouteOut(_Out):
    type: TextKind = Field(validation_alias=AliasChoices("type", "intent"))
    crisis: bool = False
    wants_admin: bool = False
    action: MenuAction | None = None
    area: Area | None = None
    scenario: str | None = None
    candidates: list[str] = []
    facts: dict[str, Any] = {}
    clarify: str | None = None

    @field_validator("action", mode="before")
    @classmethod
    def _unknown_action(cls, value: object) -> object:
        return value if value in ("book", "my_bookings", "ask", "admin") else None

    @field_validator("area", mode="before")
    @classmethod
    def _unknown_area(cls, value: object) -> object:
        """«leg», «hand», "" — not worth a retry: the topic menu simply is not narrowed."""
        return value if value in AREAS else None

    @field_validator("candidates", mode="before")
    @classmethod
    def _candidate_list(cls, value: object) -> object:
        if not isinstance(value, list):
            return []
        return [v for v in value if isinstance(v, str)]


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
    * menu_action — «когда у меня приём», «хочу записаться» → the menu screen (``action``);
    * clinic_question — about the trauma centre → answered from kb/info;
    * other_medical — not our profile and no injury (headache, fever) → «не в травмпункт»;
    * offtopic — anything else, jokes, attempts to change the bot's rules → refused;
    * crisis — words about suicide or self-harm → helpline, a note to the admins.
    """

    kind: TextKind
    action: MenuAction | None = None  # for menu_action: book / my_bookings / ask / admin
    area: Area | None = None
    scenario: str | None = None
    candidates: list[str] = []  # 3–5 scenarios that may fit, for choosing the topic menu
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
            if out.wants_admin or (out.action == "admin" and out.type == "clinic_question"):
                return RouteResult(kind="menu_action", action="admin")
            if out.type == "menu_action":
                if out.action is None:
                    raise ValueError("menu_action needs action: book / my_bookings / ask / admin")
                return RouteResult(kind="menu_action", action=out.action)
            if out.type != "complaint":
                return RouteResult(kind=out.type)
            candidates = [c for c in dict.fromkeys(out.candidates) if c in self.kb.scenarios][:5]
            if out.scenario is None:
                clarify = safe_clarify(out.clarify, ROUTE_CLARIFY)
                return RouteResult(
                    kind="complaint", area=out.area, candidates=candidates, clarify=clarify
                )
            if out.scenario not in self.kb.scenarios:
                raise ValueError(f"unknown scenario {out.scenario!r}; use an id from the catalogue")
            scenario = self.kb.scenario(out.scenario)
            area = out.area
            if not scenario.fits(area):  # the scenario is the stronger signal
                own = [a for a in scenario.body_areas if a != "any"]
                area = own[0] if len(own) == 1 else None
            return RouteResult(
                kind="complaint",
                area=area,
                scenario=scenario.id,
                candidates=candidates,
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

    async def answer(
        self,
        text: str,
        known: KnownPerson | None = None,
        on_partial: Callable[[str], Awaitable[None]] | None = None,
    ) -> AnswerResult:
        """Answer a question about the trauma centre from kb/info only.

        ``on_partial`` gets the answer while it is generated (a Telegram draft); a piece with
        a number that is not in kb/info stops the stream, and the final answer is checked again.
        """
        user = _json({"task": "answer", "question": self._mask(text, known)})
        on_text = self._partial_answers(on_partial) if on_partial else None

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
            on_text=on_text,
        )

    def _partial_answers(
        self, on_partial: Callable[[str], Awaitable[None]]
    ) -> Callable[[str], Awaitable[None]]:
        shown = {"text": "", "stopped": False}

        async def on_text(raw: str) -> None:
            if shown["stopped"]:
                return
            piece = partial_answer(raw)
            if not piece or piece == shown["text"]:
                return
            if not grounded_prefix(piece, self._info):
                shown["stopped"] = True
                return
            shown["text"] = piece
            await on_partial(piece)

        return on_text


_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "n": "\n", "t": "\t", "r": "", "b": "", "f": ""}


def partial_answer(raw: str) -> str | None:
    """The «answer» string of a JSON object that is still being generated, if found = true."""
    found = re.search(r'"found"\s*:\s*(true|false)', raw)
    start = re.search(r'"answer"\s*:\s*"', raw)
    if not found or found.group(1) != "true" or not start:
        return None
    out: list[str] = []
    i = start.end()
    while i < len(raw):
        ch = raw[i]
        if ch == '"':
            break
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= len(raw):
            break
        code = raw[i + 1]
        if code == "u":
            if i + 6 > len(raw):
                break
            out.append(chr(int(raw[i + 2 : i + 6], 16)))
            i += 6
        else:
            out.append(_ESCAPES.get(code, code))
            i += 2
    return "".join(out)


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)
