"""Patients played by DeepSeek against the real Conversation — run by hand, as the live tests:

    uv run python -m tests.simulate                     # every persona → docs/simulation_report.md
    uv run python -m tests.simulate --only teen16,elder --parallel 2
    uv run python -m tests.simulate --limit 10 --no-write
    uv run python -m tests.simulate --recheck           # the rules again, no DeepSeek

Each persona of tests/simulate_personas.py talks to the bot through tests/chat_harness.py for
up to 25 turns and decides itself whether to press a button or to type. The bot is the real
one: the scenarios, the mock schedule, a database of its own and the real DeepSeek roles
(route / extract / answer). After the dialog the transcript is checked by rules, with no LLM:

* the goal is reached, or the patient got a plain refusal with a way out;
* no screen is shown twice in a row without a new line;
* no question is left without an answer;
* the doctor, the day and «для кого» the patient named are not lost;
* every answer has a button that leads on;
* no diagnoses and no medical advice.

Nothing here is fixed automatically: the report lists what failed, with the transcript.
Needs DEEPSEEK_API_KEY in .env. The transcripts of the last run stay in data/simulation.jsonl
(not in git): ``--recheck`` applies the rules to them again after a rule or a persona is
corrected, without calling DeepSeek. The rules are tested offline in tests/test_simulate.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import tempfile
import traceback
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.bot import texts
from app.bot.conversation import Conversation, Reply, UserRef
from app.config import PROJECT_ROOT, ClinicData, get_settings, load_clinic_data
from app.db.models import Booking, Handoff, Patient, User
from app.db.session import Database
from app.flow.commands import find_audience, is_question
from app.flow.loader import KnowledgeBase, load_kb
from app.llm.assistant import Assistant
from app.llm.client import DeepSeekClient, LLMUnavailable
from app.llm.usage import Usage
from app.schedule.generate import generate
from app.schedule.mock import MockSchedule
from tests.chat_harness import Chat, Clock, make_settings, plain
from tests.simulate_personas import PERSONAS, Persona

TZ = ZoneInfo("Asia/Yekaterinburg")
FIRST_DAY = date(2026, 11, 12)  # Thursday: the day every dialog starts on
MAX_TURNS = 25
TURN_SECONDS = 40  # the clinic's clock moves on with every turn of the patient
REPORT = PROJECT_ROOT / "docs" / "simulation_report.md"
RAW = PROJECT_ROOT / "data" / "simulation.jsonl"  # every transcript of the last run (not in git)
BOOKED_DAYS = ("Завтра", "Вс")  # the days of the visits booked before the dialog

# --- the rules, as the report names them ---------------------------------------------------
GOAL = "цель не достигнута, и понятного отказа с выходом нет"
REPEAT = "один и тот же экран показан дважды подряд без новой строки"
UNANSWERED = "вопрос пациента остался без ответа"
LOST = "названные врач, день или «для кого» потерялись"
NO_BUTTON = "у ответа нет кнопки, которая ведёт дальше"
ADVICE = "диагноз или медицинский совет"
CRASH = "ошибка в боте"
RULES = (GOAL, REPEAT, UNANSWERED, LOST, NO_BUTTON, ADVICE, CRASH)

GOALS = {
    "book": "записаться на приём (или получить ясный ответ, почему нельзя и куда обратиться)",
    "move": "перенести свою запись так, как сказано в истории",
    "keep": "начать перенос, передумать и оставить запись как есть",
    "cancel": "отменить запись или записи так, как сказано в истории",
    "price": "узнать цену; записываться не нужно",
    "ask": "получить ответ на свой вопрос; записываться не нужно",
    "two": "записать обоих пациентов из истории: сначала одного, потом второго",
    "unknown": "узнать, помогут ли в травмпункте, и записаться, если это возможно",
}


# --- what a dialog is made of -------------------------------------------------------------------


@dataclass(frozen=True)
class Shown:
    """A message of the bot as the patient sees it."""

    text: str
    buttons: tuple[str, ...] = ()
    contact: bool = False  # the «Поделиться контактом» keyboard


@dataclass(frozen=True)
class Action:
    kind: Literal["say", "press", "contact", "stop"]
    value: str = ""
    status: str = "going"  # going / reached / refused / gave_up


@dataclass
class Step:
    n: int
    action: Action
    replies: list[Shown]
    state: dict[str, Any]  # the session after the turn: step, scenario, wish, for whom…
    bookings: list[dict[str, Any]]
    handoffs: int
    error: str | None = None  # an exception inside the bot


@dataclass
class Dialog:
    persona: Persona
    today: date
    opening: list[Shown]  # the greeting the patient sees first
    before: list[dict[str, Any]]  # the bookings made before the dialog
    steps: list[Step] = field(default_factory=list)
    ended: str = "turns"  # stop / turns / crash / patient (the simulator itself failed)
    note: str = ""


@dataclass(frozen=True)
class Violation:
    rule: str
    turn: int  # 0 — about the dialog as a whole
    detail: str


class PatientPolicy(Protocol):
    async def act(self, dialog: Dialog, shown: Sequence[Shown]) -> Action: ...


# --- the patients -------------------------------------------------------------------------------


class ScriptedPatient:
    """A fixed list of actions: the offline tests of the runner and the rules."""

    def __init__(self, *actions: Action | tuple[str, str] | str) -> None:
        self.actions = [self._action(a) for a in actions]

    @staticmethod
    def _action(raw: Action | tuple[str, str] | str) -> Action:
        if isinstance(raw, Action):
            return raw
        if isinstance(raw, tuple):
            return Action(raw[0], raw[1])  # type: ignore[arg-type]
        return (
            Action("contact" if raw == "contact" else "stop")
            if raw in ("contact", "stop")
            else (Action("say", raw))
        )

    async def act(self, dialog: Dialog, shown: Sequence[Shown]) -> Action:
        return self.actions.pop(0) if self.actions else Action("stop", status="gave_up")


PATIENT_RULES = "\n".join(
    [
        "Вы — пациент (или родственник пациента), который пишет чат-боту травмпункта клиники в "
        "Telegram. Это проверка бота: ведите себя как живой человек с характером и целью, "
        "описанными ниже. Вы не ассистент и не помогаете боту.",
        "",
        "На каждом ходе верните один JSON-объект:",
        '{"action": "say" | "press" | "contact" | "stop", "value": "…", '
        '"status": "going" | "reached" | "refused" | "gave_up"}',
        '- "say" — написать сообщение; value — текст одного сообщения в манере персонажа, без '
        "кавычек и пояснений;",
        '- "press" — нажать кнопку; value — подпись кнопки ровно как в списке buttons;',
        '- "contact" — поделиться своим номером телефона, только когда contact_requested = '
        "true; value — пустая строка;",
        '- "stop" — закончить разговор; value — пустая строка. Заканчивайте, когда цель '
        'достигнута (status "reached"), когда бот ясно объяснил, что это невозможно или куда '
        'обратиться (status "refused"), или когда живой человек на вашем месте бросил бы '
        'переписку (status "gave_up"). В остальных случаях status — "going".',
        "",
        "Правила.",
        "1. Пишите от первого лица, так, как писал бы этот человек: его манера, длина фраз, "
        "ошибки. Не упоминайте проверку и не называйте себя персонажем.",
        "2. Сами решайте, нажать кнопку или написать текст, — как удобнее этому человеку. "
        "Нажимать можно только кнопки из списка buttons.",
        "3. На вопросы бота отвечайте по своей истории. Чего в истории нет — отвечайте, как "
        "ответил бы обычный человек («не знаю», «вроде нет»); ничего сложного не выдумывайте.",
        "4. Свои ФИО, дату рождения и телефон называйте, только когда бот об этом просит, если "
        "в манере персонажа не сказано иное. Согласие на обработку данных давайте.",
        "5. Не подсказывайте боту, как ему работать. Если бот не понял — скажите иначе, как "
        "сделал бы человек; после трёх неудач подряд можно сдаться.",
        "6. Когда бот прислал запись с датой и временем и спросил об оплате — ответьте "
        "«самостоятельно» и, если цель достигнута, на следующем ходе заканчивайте.",
        "7. Только JSON, без пояснений и Markdown.",
    ]
)


def persona_prompt(persona: Persona, before: Sequence[dict[str, Any]]) -> str:
    lines = [
        "# Персонаж",
        f"Вы: {persona.name}, дата рождения {persona.born}, телефон {persona.phone}.",
        f"Кто Вы: {persona.about}.",
        f"Манера письма: {persona.manner}.",
        f"Что случилось: {persona.story}.",
        f"Цель: {GOALS[persona.goal]}.",
    ]
    if persona.child:
        lines.append(f"Ребёнок, которого записываете: {persona.child[0]}, {persona.child[1]}.")
    if persona.other:
        lines.append(f"Взрослый, которого записываете: {persona.other[0]}, {persona.other[1]}.")
    if before:
        booked = "; ".join(f"{b['date']} в {b['time']}" for b in before)
        lines.append(f"У Вас уже есть запись в травмпункт: {booked}. Сегодня 12 ноября, четверг.")
    return "\n".join(lines)


class DeepSeekPatient:
    """DeepSeek plays the persona; the answer is validated against the buttons on screen."""

    def __init__(self, client: DeepSeekClient, *, temperature: float = 0.7) -> None:
        self.client = client
        self.temperature = temperature

    async def act(self, dialog: Dialog, shown: Sequence[Shown]) -> Action:
        labels = [label for reply in shown for label in reply.buttons]
        contact = any(reply.contact for reply in shown)

        def parse(data: dict[str, Any]) -> Action:
            kind, value = data.get("action"), str(data.get("value") or "").strip()
            status = data.get("status") if data.get("status") in STATUSES else "going"
            if kind == "press":
                if value not in labels:
                    raise ValueError(f"no button {value!r}: press one of buttons or use say")
            elif kind == "say":
                if not value or len(value) > 600:
                    raise ValueError("say needs a message of 1–600 characters in value")
            elif kind == "contact":
                if not contact:
                    raise ValueError("contact is possible only when contact_requested is true")
            elif kind != "stop":
                raise ValueError("action must be say, press, contact or stop")
            return Action(kind, value, status)  # type: ignore[arg-type]

        earlier = [
            {"you": _said(step.action), "bot": [_cut(r.text, 300) for r in step.replies]}
            for step in dialog.steps[:-1]
        ]
        if dialog.steps:
            earlier.append({"you": _said(dialog.steps[-1].action), "bot": "см. ниже"})
        user = {
            "turn": len(dialog.steps) + 1,
            "turns_left": MAX_TURNS - len(dialog.steps),
            "dialog": earlier,
            "bot": [_cut(r.text, 1200) for r in shown],
            "buttons": labels,
            "contact_requested": contact,
        }
        system = PATIENT_RULES + "\n\n" + persona_prompt(dialog.persona, dialog.before)
        return await self.client.complete_json(
            task="patient",
            system=system,
            user=json.dumps(user, ensure_ascii=False),
            parse=parse,
            temperature=self.temperature,
            max_tokens=300,
        )


STATUSES = ("going", "reached", "refused", "gave_up")


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _said(action: Action) -> str:
    return {
        "say": f"написал: {action.value}",
        "press": f"нажал кнопку «{action.value}»",
        "contact": "поделился номером телефона",
        "stop": "закончил разговор",
    }[action.kind]


# --- one dialog ---------------------------------------------------------------------------------


@dataclass
class Env:
    """What every dialog shares: the knowledge base, the clinic data and the bot's DeepSeek."""

    kb: KnowledgeBase
    clinic: ClinicData
    bot_client: DeepSeekClient | None  # None: the bot works on buttons and word lists only
    tmp: Path


def _shown(replies: Sequence[Reply]) -> list[Shown]:
    return [
        Shown(
            r.text,
            tuple(plain(b.label) for row in r.buttons for b in row),
            r.ask_contact,
        )
        for r in replies
    ]


async def _state(chat: Chat, kb: KnowledgeBase) -> dict[str, Any]:
    async with chat.conversation.db() as s:
        user = await s.get(User, chat.user.id)
        session = dict(user.session or {}) if user else {}
    dialog = session.get("dialog") or {}
    wish = session.get("wish") or {}
    outcome = None
    if dialog.get("scenario_id") and dialog.get("outcome_id"):
        found = kb.scenario(dialog["scenario_id"]).outcomes.get(dialog["outcome_id"])
        outcome = found.type if found else None
    return {
        "step": session.get("step", "idle"),
        "wizard": bool(session.get("wizard")),
        "moving": session.get("reschedule_of") is not None,
        "scenario": dialog.get("scenario_id"),
        "question": dialog.get("question_id"),
        "outcome": outcome,
        "audience": (dialog.get("facts") or {}).get("audience"),
        "for_whom": session.get("for_whom"),
        "doctor": wish.get("doctor"),
        "day": wish.get("day"),
    }


async def _bookings(chat: Chat) -> tuple[list[dict[str, Any]], int]:
    async with chat.conversation.db() as s:
        rows = (await s.scalars(select(Booking).order_by(Booking.id))).all()
        found = []
        for b in rows:
            patient = await s.get(Patient, b.patient_id)
            start = b.start_at.astimezone(TZ)
            found.append(
                {
                    "id": b.id,
                    "status": b.status,
                    "date": f"{start:%d.%m}",
                    "day": start.date().isoformat(),
                    "time": f"{start:%H:%M}",
                    "doctor": b.doctor,
                    "visit": b.visit,
                    "patient_id": b.patient_id,
                    "audience": patient.audience if patient else None,
                }
            )
        handoffs = await s.scalar(select(func.count()).select_from(Handoff)) or 0
    return found, handoffs


async def _book_before(chat: Chat, persona: Persona, index: int) -> None:
    """A first visit for the persona, by buttons — the setup of «перенести» and «отменить»."""
    await chat.press(texts.MENU_BOOK)
    for label in ("Рука или нога", "Похоже на вывих", texts.AUDIENCE_SELF, "Бытовая травма"):
        await chat.press(label)
    await chat.press("В течение 7 дней")
    await chat.press("Нет, не обращались")
    await chat.pick_slot(BOOKED_DAYS[index])
    if index == 0:
        await chat.press(texts.CONSENT_YES)
        await chat.say(persona.name)
        await chat.say(persona.born)
        await chat.contact(persona.phone)
    else:
        await chat.press(" ".join(persona.name.split()[:2]))
    await chat.press(texts.PAYMENT_SELF)


async def play(
    persona: Persona, patient: PatientPolicy, env: Env, *, turns: int = MAX_TURNS
) -> Dialog:
    """One dialog: the persona against a bot of its own (database, schedule, clock)."""
    hour, minute = map(int, persona.clock.split(":"))
    clock = Clock(datetime.combine(FIRST_DAY, time(hour, minute), tzinfo=TZ))
    env.tmp.mkdir(parents=True, exist_ok=True)
    db = Database(f"sqlite+aiosqlite:///{env.tmp / (persona.key + '.db')}")
    await db.init()
    rows = generate(FIRST_DAY, env.clinic.schedule.demo.days_ahead, env.clinic.schedule)
    schedule = MockSchedule(rows, tz=TZ, slot_minutes=env.clinic.schedule.demo.slot_minutes)
    assistant = None
    if env.bot_client is not None:
        assistant = Assistant(env.bot_client, env.kb, today=lambda: clock().date())
    conversation = Conversation(
        kb=env.kb,
        clinic=env.clinic,
        settings=make_settings(),
        db=db,
        schedule=schedule,
        assistant=assistant,
        clock=clock,
    )
    chat = Chat(conversation, UserRef(id=1001, chat_id=1001, username=None, display_name=None))
    try:
        await chat.start()
        for index in range(persona.booked):
            await _book_before(chat, persona, index)
        if persona.booked:
            await chat.start()
        before, _ = await _bookings(chat)
        dialog = Dialog(persona, clock().date(), _shown(chat.replies), before)
        shown = dialog.opening
        for n in range(1, turns + 1):
            try:
                action = await patient.act(dialog, shown)
            except LLMUnavailable as exc:
                dialog.ended, dialog.note = "patient", f"симулятор пациента не ответил: {exc}"
                break
            if action.kind == "stop":
                dialog.ended, dialog.note = "stop", action.status
                break
            clock.now += timedelta(seconds=TURN_SECONDS)
            error = None
            try:
                if action.kind == "press":
                    await chat.press(action.value)
                elif action.kind == "contact":
                    await chat.contact(persona.phone)
                else:
                    await chat.say(action.value)
            except Exception as exc:  # a bug in the bot is the most valuable finding
                error = f"{type(exc).__name__}: {exc}".strip()
                error += "\n" + "".join(traceback.format_tb(exc.__traceback__)[-2:]).strip()
            replies = [] if error else _shown(chat.replies)
            bookings, handoffs = await _bookings(chat)
            state = await _state(chat, env.kb)
            dialog.steps.append(Step(n, action, replies, state, bookings, handoffs, error))
            if error:
                dialog.ended = "crash"
                break
            shown = replies
        return dialog
    finally:
        await db.close()


# --- the rules: no LLM --------------------------------------------------------------------------

FALLBACKS = (
    texts.HINT_TIME,
    texts.HINT_BUTTONS,
    texts.SAME_STEP,
    texts.GOING_ON,
    texts.PICK_BUTTON_SHORT,
    texts.ONLY_TEXT,
    texts.REQUEST_UNCLEAR,
    texts.TOPIC_UNCLEAR,
    texts.PICK_TOPIC,
)
REFUSALS = (
    texts.NOT_OUR_PROFILE,
    texts.INFO_UNKNOWN,
    texts.ASIDE_UNKNOWN,
    texts.NO_SLOTS,
    texts.OTHER_DEPARTMENT_NO_PHONE,
)
EXPLAINED = ("не дежурит", "не ведёт", "свободного времени", "не открыта", "Такого врача")
WISH_BUTTONS = (
    texts.WISH_CLEAR,
    texts.OTHER_DAY,
    texts.ANY_DOCTOR,
    texts.WISH_ANOTHER_DOCTOR,
    "другой врач",
    "Ближайшее",
)
DAY_BUTTON = re.compile(r"^(?:Сегодня|Завтра|Пн|Вт|Ср|Чт|Пт|Сб|Вс), \d\d\.\d\d$")
ADVICE_WORDS = re.compile(
    r"\b(?:приложите|прикладывайте|примите|принимайте|выпейте|пейте|намажьте|помажьте|мажьте|"
    r"наложите|зафиксируйте|обработайте|промойте|приподнимите|не\s+наступайте|не\s+нагружайте|"
    r"обеспечьте\s+покой|держите\s+в\s+покое)\b"
    r"|\b(?:рекомендую|советую|рекомендуется)\b"
    r"|\b(?:у\s+вас|это)\s+(?:\w+\s+){0,2}(?:перелом|вывих|растяжение|ушиб|сотрясение|разрыв|"
    r"трещина)\b"
    r"|\b(?:скорее\s+всего|по\s+всей\s+видимости)\b",
    re.IGNORECASE,
)


def fixed_phrases(kb: KnowledgeBase) -> list[str]:
    """Texts the bot says as they are written: the scripts and app/bot/texts.py."""
    found = set(kb.patient_phrases())
    for value in vars(texts).values():
        values = value.values() if isinstance(value, dict) else [value]
        found |= {v for v in values if isinstance(v, str) and "{" not in v and len(v) > 12}
    return sorted(found, key=len, reverse=True)


def _is_fallback(text: str) -> bool:
    return text in FALLBACKS or text.startswith(texts.PICK_BUTTON)


def _way_on(step: Step) -> bool:
    return any(r.buttons or r.contact for r in step.replies)


def _refused(step: Step, earlier_handoffs: int, emergency: str) -> bool:
    if step.state["outcome"] in ("route", "handoff", "emergency"):
        return True
    if step.handoffs > earlier_handoffs:
        return True
    said = "\n".join(r.text for r in step.replies)
    return emergency in said or any(phrase in said for phrase in REFUSALS)


def _key(state: dict[str, Any]) -> tuple[Any, ...]:
    return (state["step"], state["scenario"], state["question"], state["outcome"])


def _goal(dialog: Dialog, clinic: ClinicData) -> str | None:
    """None if the goal is reached (or plainly refused with a way out), else what is wrong."""
    persona, steps = dialog.persona, dialog.steps
    if not steps:
        return "диалог не начался"
    before = {b["id"]: b for b in dialog.before}
    after = {b["id"]: b for b in steps[-1].bookings}
    new = [b for b in after.values() if b["id"] not in before and b["status"] == "active"]
    emergency = clinic.clinic.emergency.reply
    handoffs = [0, *(s.handoffs for s in steps)]
    refused = any(_refused(s, handoffs[i], emergency) for i, s in enumerate(steps))
    way_on = _way_on(steps[-1])
    said = "\n".join(r.text for s in steps for r in s.replies)
    if persona.goal in ("book", "unknown"):
        if new or (refused and way_on):
            return None
        return "записи нет, и понятного отказа с выходом тоже нет"
    if persona.goal == "two":
        patients = {b["patient_id"] for b in new}
        return None if len(patients) >= 2 else f"записано пациентов: {len(patients)} из 2"
    kept = [b for b in before.values() if after[b["id"]]["status"] == "active"]
    moved = [
        b for b in kept if (after[b["id"]]["day"], after[b["id"]]["time"]) != (b["day"], b["time"])
    ]
    if persona.goal == "move":
        return None if moved else "запись не перенесена"
    if persona.goal == "keep":
        if len(kept) == len(before) and not moved and way_on:
            return None
        return "запись не осталась как была"
    if persona.goal == "cancel":
        gone = [b for b in before.values() if after[b["id"]]["status"] != "active"]
        if persona.day is None:
            return None if len(gone) == len(before) else f"отменено {len(gone)} из {len(before)}"
        wanted = (dialog.today + timedelta(days=persona.day)).isoformat()
        right = [b for b in gone if b["day"] == wanted]
        if len(right) == 1 and len(gone) == 1:
            return None
        return f"отменены не те записи: {[b['date'] for b in gone]}"
    if persona.goal == "price":
        prices = clinic.prices
        figures = [str(v) for v in (prices.primary_visit, prices.repeat_visit) if v]
        if any(figure in said for figure in figures) or "уточнит администратор" in said:
            return None
        return "цена не названа"
    # ask: an answer that is not a fallback, or an honest «нет информации» with a way out
    answered = any(
        s.action.kind == "say" and s.replies and not _is_fallback(s.replies[0].text) for s in steps
    )
    return None if answered and way_on else "ответа на вопрос нет"


def _repeats(dialog: Dialog) -> list[Violation]:
    found = []
    last = dialog.opening[-1] if dialog.opening else None
    for step in dialog.steps:
        if not step.replies:
            continue
        if len(step.replies) == 1 and last is not None and step.replies[0] == last:
            found.append(Violation(REPEAT, step.n, _cut(last.text, 120)))
        last = step.replies[-1]
    return found


def _unanswered(dialog: Dialog) -> list[Violation]:
    found = []
    prev_state = {"step": "idle", "scenario": None, "question": None, "outcome": None}
    prev_screen = dialog.opening[-1] if dialog.opening else None
    for step in dialog.steps:
        asked = step.action.kind == "say" and is_question(step.action.value)
        if asked and step.replies and _key(step.state) == _key(prev_state):
            informative = [r for r in step.replies if not _is_fallback(r.text) and r != prev_screen]
            same_buttons = (
                prev_screen is not None and step.replies[-1].buttons == prev_screen.buttons
            )
            reshown = len(informative) == 1 and informative[0] is step.replies[-1] and same_buttons
            if not informative or (reshown and step.state["step"] != "idle"):
                found.append(Violation(UNANSWERED, step.n, _cut(step.action.value, 120)))
        prev_state = step.state
        if step.replies:
            prev_screen = step.replies[-1]
    return found


def _lost(dialog: Dialog) -> list[Violation]:
    found: list[Violation] = []
    persona, steps = dialog.persona, dialog.steps
    said = "\n".join(r.text for s in steps for r in s.replies)
    explained = any(marker in said for marker in EXPLAINED)
    pressed = [s.action.value for s in steps if s.action.kind == "press"]
    chose = any(
        any(w in label for w in WISH_BUTTONS) or DAY_BUTTON.match(label) for label in pressed
    )
    before = {b["id"] for b in dialog.before}
    new = [b for b in (steps[-1].bookings if steps else []) if b["id"] not in before]
    new = [b for b in new if b["status"] == "active"]

    # the doctor the patient named
    if persona.doctor:
        stem = persona.doctor.lower()[:5]
        named = [s for s in steps if s.action.kind == "say" and stem in s.action.value.lower()]
        heard = any(
            s.state["doctor"] and s.state["doctor"].startswith(persona.doctor) for s in steps
        )
        if named and not heard and persona.doctor not in said:
            found.append(Violation(LOST, named[0].n, f"врач {persona.doctor} назван, бот не учёл"))
        for prev, step in pairwise(steps):
            dropped = prev.state["doctor"] and not step.state["doctor"]
            booking = step.state["wizard"] and step.state["step"] not in ("idle", "payment")
            by_patient = step.action.kind == "press" and any(
                w in step.action.value for w in WISH_BUTTONS
            )
            told = any(m in r.text for r in step.replies for m in EXPLAINED)
            if dropped and booking and not by_patient and not told and prev.state["wizard"]:
                found.append(Violation(LOST, step.n, "пожелание по врачу пропало посреди записи"))
        wrong = [b for b in new if not b["doctor"].startswith(persona.doctor)]
        if wrong and named and not explained and not chose:
            found.append(
                Violation(LOST, 0, f"запись к {wrong[0]['doctor']}, просили {persona.doctor}")
            )

    # the day the patient named
    if persona.day is not None and persona.goal in ("book", "move"):
        wanted = (dialog.today + timedelta(days=persona.day)).isoformat()
        after = {b["id"]: b for b in (steps[-1].bookings if steps else [])}
        got = new or [b for b in after.values() if b["id"] in before and b["status"] == "active"]
        changed = [b for b in got if b["day"] != wanted]
        target = new if persona.goal == "book" else [b for b in got if b not in dialog.before]
        if target and changed and not explained and not chose:
            found.append(Violation(LOST, 0, f"запись на {changed[0]['date']}, просили {wanted}"))

    # who the visit is for
    told_at = next(
        (s.n for s in steps if s.action.kind == "say" and find_audience(s.action.value)), None
    )
    if told_at is not None:
        for prev, step in pairwise(steps):
            asked = any(texts.ASK_AUDIENCE in r.text for r in step.replies)
            again = any(texts.ASK_AUDIENCE in r.text for r in prev.replies)
            went_back = step.action.kind == "press" and step.action.value in (
                texts.BACK,
                texts.SUMMARY_EDIT,
            )
            if asked and not again and step.n > told_at and not went_back:
                found.append(Violation(LOST, step.n, "бот спросил «для кого», хотя это сказано"))
                break
    if persona.goal == "book" and new and persona.other is None:
        want = "child" if persona.for_child else "adult"
        if persona.key != "teen16" and any(b["audience"] != want for b in new):
            found.append(Violation(LOST, 0, f"запись оформлена не на того: нужно «{want}»"))
    return found


def _no_button(dialog: Dialog) -> list[Violation]:
    return [
        Violation(NO_BUTTON, step.n, _cut(step.replies[-1].text, 120))
        for step in dialog.steps
        if step.replies and not _way_on(step) and not step.replies[0].text.startswith(CRISIS_HEAD)
    ]


CRISIS_HEAD = texts.CRISIS.split("{")[0][:40]


def _advice(dialog: Dialog, phrases: Sequence[str]) -> list[Violation]:
    found = []
    for step in dialog.steps:
        for reply in step.replies:
            rest = reply.text
            for phrase in phrases:  # what the scripts and the bot's own texts say is not advice
                rest = rest.replace(phrase, " ")
            match = ADVICE_WORDS.search(rest)
            if match:
                start = max(match.start() - 40, 0)
                found.append(
                    Violation(ADVICE, step.n, "…" + rest[start : match.end() + 60].strip())
                )
    return found


def check(dialog: Dialog, kb: KnowledgeBase, clinic: ClinicData) -> list[Violation]:
    """Every rule the dialog breaks; an empty list — the dialog passed."""
    if dialog.ended == "patient":
        return []  # the simulator failed, not the bot: reported apart
    found: list[Violation] = []
    crashed = [s for s in dialog.steps if s.error]
    if crashed:
        found.append(Violation(CRASH, crashed[0].n, crashed[0].error or ""))
    problem = _goal(dialog, clinic)
    if problem is not None and not crashed:
        found.append(Violation(GOAL, 0, problem))
    found += _repeats(dialog)
    found += _unanswered(dialog)
    found += _lost(dialog)
    found += _no_button(dialog)
    found += _advice(dialog, fixed_phrases(kb))
    return found


# --- the report ---------------------------------------------------------------------------------

ENDED = {
    "stop": "пациент закончил",
    "turns": f"лимит {MAX_TURNS} ходов",
    "crash": "ошибка в боте",
    "patient": "сбой симулятора",
}
STATUS = {"reached": "цель достигнута", "refused": "получил отказ", "gave_up": "сдался"}


def transcript(dialog: Dialog) -> list[str]:
    lines = []
    for reply in dialog.opening:
        lines += _bot_lines(reply)
    for step in dialog.steps:
        action = step.action
        said = {
            "say": f"пишет: «{action.value}»",
            "press": f"нажимает «{action.value}»",
            "contact": "делится номером телефона",
        }[action.kind]
        lines.append(f"{step.n}. **Пациент** {said}")
        if step.error:
            lines.append(f"   **Ошибка в боте:** `{step.error.splitlines()[0]}`")
        for reply in step.replies:
            lines += _bot_lines(reply)
    return lines


def _bot_lines(reply: Shown) -> list[str]:
    text = reply.text.strip().replace("\n", "\n   > ") or "(карточка записи)"
    lines = [f"   > {text}"]
    if reply.buttons:
        lines.append("   > " + " ".join(f"[{label}]" for label in reply.buttons))
    if reply.contact:
        lines.append("   > [Поделиться контактом]")
    lines.append("")
    return lines


def render_report(
    results: Sequence[tuple[Dialog, list[Violation]]], meta: dict[str, Any] | None = None
) -> str:
    meta = meta or {}
    held = [(d, v) for d, v in results if d.ended != "patient"]
    failed = [(d, v) for d, v in held if v]
    broken = [d for d, _ in results if d.ended == "patient"]
    by_rule = Counter(
        v.rule for _, violations in failed for v in {x.rule: x for x in violations}.values()
    )
    lines = [
        "# Симуляция пациентов",
        "",
        "Отчёт собирает `uv run python -m tests.simulate`: DeepSeek играет пациентов из "
        "`tests/simulate_personas.py` против настоящего диалога бота (сценарии, демо-расписание, "
        "своя база на каждый диалог, настоящие роли DeepSeek). После диалога расшифровку "
        "проверяют правила в `tests/simulate.py`, без LLM. Все имена, даты рождения и телефоны "
        "вымышленные. Найденное здесь не исправляется автоматически.",
        "",
    ]
    if meta:
        lines += [
            f"Запуск: {meta.get('at', '—')}. Модель: {meta.get('model', '—')}. "
            f"Вызовов DeepSeek: пациент — {meta.get('patient_calls', 0)}, бот — "
            f"{meta.get('bot_calls', 0)}; время прогона — {meta.get('seconds', 0)} с.",
            "",
        ]
    lines += [
        f"**Диалогов: {len(held)}. Прошло: {len(held) - len(failed)}. С нарушениями: "
        f"{len(failed)}.**"
        + (f" Не состоялось из-за сбоя симулятора: {len(broken)}." if broken else ""),
        "",
        "## Нарушения по правилам",
        "",
        "| Правило | Диалогов |",
        "|---|---|",
        *(f"| {rule} | {by_rule.get(rule, 0)} |" for rule in RULES),
        "",
        "## Все диалоги",
        "",
        "| Персонаж | Кто | Цель | Ходов | Чем закончился | Итог |",
        "|---|---|---|---|---|---|",
    ]
    for dialog, violations in results:
        p = dialog.persona
        ended = ENDED[dialog.ended]
        if dialog.ended == "stop" and dialog.note in STATUS:
            ended += f": {STATUS[dialog.note]}"
        verdict = "—" if dialog.ended == "patient" else "нарушения" if violations else "прошёл"
        lines.append(
            f"| `{p.key}` | {p.about} | {p.goal} | {len(dialog.steps)} | {ended} | {verdict} |"
        )
    lines += ["", "## Провалы", ""]
    if not failed:
        lines += ["Провалов нет.", ""]
    for dialog, violations in failed:
        p = dialog.persona
        lines += [
            f"### `{p.key}` — {p.name}",
            "",
            f"Кто: {p.about}. Манера: {p.manner}.",
            f"Что случилось: {p.story}.",
            f"Цель: {GOALS[p.goal]}. Диалог закончился: {ENDED[dialog.ended]}"
            + (f" ({STATUS[dialog.note]})" if dialog.note in STATUS else "")
            + ".",
            "",
            "Нарушено:",
        ]
        for v in violations:
            where = f"ход {v.turn}" if v.turn else "диалог в целом"
            lines.append(f"- **{v.rule}** ({where}): {v.detail.splitlines()[0]}")
        lines += ["", "Расшифровка:", "", *transcript(dialog), ""]
    if broken:
        lines += ["## Не состоялись", ""]
        lines += [f"- `{d.persona.key}`: {d.note}" for d in broken]
        lines.append("")
    return "\n".join(lines)


# --- the run ------------------------------------------------------------------------------------


async def run(
    personas: Sequence[Persona],
    make_patient: Callable[[], PatientPolicy],
    env: Env,
    *,
    parallel: int = 5,
    turns: int = MAX_TURNS,
    on_done: Callable[[Dialog, list[Violation]], None] | None = None,
) -> list[tuple[Dialog, list[Violation]]]:
    gate = asyncio.Semaphore(parallel)

    async def one(persona: Persona) -> tuple[Dialog, list[Violation]]:
        async with gate:
            dialog = await play(persona, make_patient(), env, turns=turns)
            violations = check(dialog, env.kb, env.clinic)
            if on_done is not None:
                on_done(dialog, violations)
            return dialog, violations

    tasks: list[Awaitable[tuple[Dialog, list[Violation]]]] = [one(p) for p in personas]
    return list(await asyncio.gather(*tasks))


def save_raw(
    results: Sequence[tuple[Dialog, list[Violation]]], meta: dict[str, Any], path: Path
) -> None:
    """The transcripts of a run, for ``--recheck``: the first line is the run's meta."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"meta": meta}, ensure_ascii=False) + "\n")
        for dialog, violations in results:
            row = {
                "persona": dialog.persona.key,
                "ended": dialog.ended,
                "note": dialog.note,
                "violations": [asdict(v) for v in violations],
                "steps": [asdict(step) for step in dialog.steps],
            }
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


async def recheck(
    path: Path, env: Env
) -> tuple[list[tuple[Dialog, list[Violation]]], dict[str, Any]]:
    """The rules again over saved transcripts: after a rule or a persona was corrected.

    The greeting and the bookings made before the dialog are not in the file: they are
    replayed (by buttons, no LLM), the bot's schedule and clock being the same every run.
    """
    by_key = {p.key: p for p in PERSONAS}
    meta: dict[str, Any] = {}
    results = []
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if "meta" in row:
            meta = row["meta"]
            continue
        persona = by_key.get(row["persona"])
        if persona is None:
            continue  # the persona was removed since the run
        dialog = await play(persona, ScriptedPatient(), env)
        dialog.steps = [
            Step(
                s["n"],
                Action(**s["action"]),
                [Shown(r["text"], tuple(r["buttons"]), r["contact"]) for r in s["replies"]],
                s["state"],
                s["bookings"],
                s["handoffs"],
                s["error"],
            )
            for s in row["steps"]
        ]
        dialog.ended, dialog.note = row["ended"], row["note"]
        results.append((dialog, check(dialog, env.kb, env.clinic)))
    return results, meta


def _client(recorder: Callable[[Usage], None], *, timeout: float, first: float) -> DeepSeekClient:
    settings = get_settings()
    if settings.deepseek_api_key is None:
        raise SystemExit("DEEPSEEK_API_KEY is not set: the simulator needs the real DeepSeek")
    return DeepSeekClient(
        api_key=settings.deepseek_api_key.get_secret_value(),
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        timeout=timeout,
        first_attempt=first,
        recorders=[recorder],
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Patients played by DeepSeek against the bot")
    parser.add_argument("--only", help="persona keys, comma-separated")
    parser.add_argument("--limit", type=int, help="the first N personas")
    parser.add_argument("--parallel", type=int, default=5, help="dialogs at once")
    parser.add_argument("--turns", type=int, default=MAX_TURNS)
    parser.add_argument("--no-write", action="store_true", help="print the report, keep the file")
    parser.add_argument(
        "--recheck", action="store_true", help="apply the rules to the last run again, no DeepSeek"
    )
    args = parser.parse_args(argv)
    if args.recheck:
        with tempfile.TemporaryDirectory() as tmp:
            offline = Env(load_kb(), load_clinic_data(), None, Path(tmp))
            results, meta = asyncio.run(recheck(RAW, offline))
        report = render_report(results, meta)
        if args.no_write:
            print(report)
        else:
            REPORT.write_text(report, encoding="utf-8")
            failed = sum(1 for _, v in results if v)
            print(f"written {REPORT}: {len(results)} dialogs, with violations {failed}")
        return
    personas = list(PERSONAS)
    if args.only:
        wanted = set(args.only.split(","))
        personas = [p for p in personas if p.key in wanted]
    if args.limit:
        personas = personas[: args.limit]

    settings = get_settings()
    patient_calls: list[Usage] = []
    bot_calls: list[Usage] = []
    patient_client = _client(patient_calls.append, timeout=40.0, first=25.0)
    bot_client = _client(
        bot_calls.append,
        timeout=settings.llm_timeout_seconds,
        first=settings.llm_first_attempt_seconds,
    )

    def progress(dialog: Dialog, violations: list[Violation]) -> None:
        verdict = (
            "сбой симулятора"
            if dialog.ended == "patient"
            else ("нарушений: " + str(len(violations)) if violations else "прошёл")
        )
        print(f"{dialog.persona.key:16} ходов {len(dialog.steps):2}  {verdict}", flush=True)

    started = datetime.now(TZ)
    with tempfile.TemporaryDirectory() as tmp:
        env = Env(load_kb(), load_clinic_data(), bot_client, Path(tmp))
        results = asyncio.run(
            run(
                personas,
                lambda: DeepSeekPatient(patient_client),
                env,
                parallel=args.parallel,
                turns=args.turns,
                on_done=progress,
            )
        )
    meta = {
        "at": f"{started:%d.%m.%Y %H:%M} (Екатеринбург)",
        "model": settings.deepseek_model,
        "patient_calls": len(patient_calls),
        "bot_calls": len(bot_calls),
        "seconds": int((datetime.now(TZ) - started).total_seconds()),
    }
    report = render_report(results, meta)
    if args.no_write:
        print(report)
        return
    REPORT.write_text(report, encoding="utf-8")
    save_raw(results, meta, RAW)
    failed = sum(1 for d, v in results if v)
    print(f"written {REPORT}: {len(results)} dialogs, with violations {failed}")


if __name__ == "__main__":
    main()
