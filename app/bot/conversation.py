"""The patient dialog, independent of the messenger (Telegram now, MAX later).

Input: a text, a pressed button (callback data), a shared contact, a command.
Output: a ``Turn`` — replies to the patient (text + buttons) and posts for the
administrators' chat. The Telegram layer (app/bot/telegram.py) only translates.

Branches come from the engine; the LLM only reads free text (app/llm/reader.py) and the
bot falls back to buttons when it cannot. Per-user state lives in ``users.session``.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import render, texts
from app.config import ClinicData, Settings
from app.db.models import AdminMessage, Booking, Handoff, Patient, Reminder, User, utcnow
from app.db.session import Database
from app.flow.age import find_child_age, parse_age_months
from app.flow.engine import AskPatient, AskQuestion, DialogState, Done, Engine, Step
from app.flow.identity import (
    age_in_months,
    normalize_phone,
    parse_birth_date,
    parse_full_name,
    parse_phone,
)
from app.flow.loader import KnowledgeBase
from app.flow.safety import find_emergency
from app.flow.schema import Booking as BookingSpec
from app.flow.schema import FactValue, Question, Scenario
from app.flow.words import days as days_word
from app.flow.words import format_age
from app.llm.assistant import Assistant
from app.llm.masking import KnownPerson
from app.llm.reader import read_answer, read_complaint, read_question
from app.notify.planner import AFTER_VISIT, DAY_BEFORE, TWO_HOURS, plan
from app.schedule.base import ScheduleProvider, Slot, SlotUnavailable
from app.schedule.rules import find_slots

log = logging.getLogger(__name__)

SLOTS_PER_PAGE = 4


# --- what goes in and out ----------------------------------------------------------------


@dataclass(frozen=True)
class UserRef:
    id: int
    chat_id: int
    username: str | None = None
    display_name: str | None = None


@dataclass(frozen=True)
class Button:
    label: str
    data: str  # callback data, at most 64 bytes


@dataclass(frozen=True)
class Reply:
    text: str
    buttons: tuple[tuple[Button, ...], ...] = ()
    ask_contact: bool = False  # «Поделиться контактом» keyboard
    remove_keyboard: bool = False
    chat_id: int | None = None  # None: the chat of the user being served


@dataclass(frozen=True)
class AdminPost:
    text: str
    handoff_id: int | None = None  # set: an administrator's reply goes back to the patient
    reply_to: int | None = None  # admin chat message to reply to (a thread)
    buttons: tuple[tuple[Button, ...], ...] = ()


@dataclass
class Turn:
    replies: list[Reply] = field(default_factory=list)
    admin: list[AdminPost] = field(default_factory=list)


StepName = Literal[
    "idle",
    "complaint",
    "child_confirm",
    "audience",
    "age",
    "question",
    "slots",
    "consent",
    "patient",
    "name",
    "birth",
    "contact",
    "payment",
    "info",
    "offer_admin",
    "admin_reply",
    "visit_question",
]


class Session(BaseModel):
    """Per-user dialog state, stored as JSON in users.session."""

    step: StepName = "idle"
    dialog: DialogState = DialogState()
    pending_scenario: str | None = None
    pending_facts: dict[str, FactValue] = {}
    child_age: int | None = None
    slot_offset: int = 0
    slot_id: str | None = None
    patient_id: int | None = None
    draft_name: list[str | None] | None = None
    resume: Literal["identify", "handoff"] | None = None
    handoff_reason: str | None = None
    handoff_context: str | None = None
    control_of: int | None = None
    anchor: date | None = None  # control visit: offer time around this day
    reschedule_of: int | None = None
    booking_id: int | None = None
    visit_booking: int | None = None
    reply_handoff: int | None = None
    consent_child: bool = False  # the consent shown includes «законный представитель»


@dataclass
class Ctx:
    db: AsyncSession
    user: User
    s: Session
    turn: Turn
    now: datetime  # UTC
    deleted: bool = False  # /forget removed the user row

    def say(self, text: str, buttons: list[list[Button]] | None = None, **kw: object) -> None:
        rows = tuple(tuple(row) for row in (buttons or []))
        self.turn.replies.append(Reply(text, rows, **kw))  # type: ignore[arg-type]


def _answer_label(question: Question, facts: dict[str, FactValue]) -> str | None:
    """The button that matches the given answer, for the administrators' summary."""
    for choice in question.buttons():
        if all(facts.get(k) == v for k, v in choice.facts.items()):
            return choice.label
    return None


def _menu() -> list[list[Button]]:
    return [
        [Button(texts.MENU_BOOK, "m:book")],
        [Button(texts.MENU_ASK, "m:ask")],
        [Button(texts.MENU_MY, "m:my")],
        [Button(texts.MENU_ADMIN, "m:admin")],
    ]


class Conversation:
    def __init__(
        self,
        *,
        kb: KnowledgeBase,
        clinic: ClinicData,
        settings: Settings,
        db: Database,
        schedule: ScheduleProvider,
        assistant: Assistant | None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.kb = kb
        self.clinic = clinic
        self.settings = settings
        self.db = db
        self.schedule = schedule
        self.assistant = assistant
        self.clock = clock
        self.engine = Engine(kb, clinic)
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    # --- entry points ------------------------------------------------------------------

    async def start(self, user: UserRef) -> Turn:
        return await self._run(user, self._greet)

    async def text(self, user: UserRef, text: str) -> Turn:
        return await self._run(user, lambda c: self._on_text(c, text.strip()))

    async def button(self, user: UserRef, data: str) -> Turn:
        return await self._run(user, lambda c: self._on_button(c, data))

    async def contact(self, user: UserRef, phone: str, owner_id: int | None) -> Turn:
        return await self._run(user, lambda c: self._on_contact(c, phone, owner_id))

    async def call_admin(self, user: UserRef) -> Turn:
        return await self._run(user, lambda c: self._handoff(c, texts.ASK_ADMIN_REASON))

    async def menu(self, user: UserRef) -> Turn:
        return await self._run(user, self._show_menu)

    async def demo_next(self, user: UserRef) -> Turn:
        return await self._run(user, self._demo_next)

    async def forget(self, user: UserRef) -> Turn:
        """/forget: withdraw consent and delete the user's data, after a confirmation."""
        return await self._run(user, self._forget_ask)

    async def admin_reply(self, message_id: int, text: str) -> Turn | None:
        """An administrator replied to a bot message in the admin chat."""
        async with self.db() as db:
            link = await db.get(AdminMessage, message_id)
            if link is None:
                return None
            handoff = await db.get(Handoff, link.handoff_id)
            user = await db.get(User, handoff.user_id) if handoff else None
            if handoff is None or user is None:
                return None
            reply = Reply(
                texts.ADMIN_ANSWER.format(text=text),
                ((Button(texts.ADMIN_ANSWER_REPLY, f"ar:{handoff.id}"),),),
                chat_id=user.chat_id,
            )
            return Turn([reply])

    async def register_admin_message(self, message_id: int, handoff_id: int) -> None:
        async with self.db() as db:
            db.add(AdminMessage(message_id=message_id, handoff_id=handoff_id))
            await db.commit()

    async def due(self) -> list[Turn]:
        """Notifications whose time has come (called by the scheduler)."""
        now = self.clock()
        async with self.db() as db:
            rows = await db.scalars(
                select(Reminder)
                .where(Reminder.due_at <= now, Reminder.sent_at.is_(None), ~Reminder.cancelled)
                .order_by(Reminder.due_at)
            )
            turns = [await self._fire(db, r, now) for r in rows.all()]
            await db.commit()
        return [t for t in turns if t.replies]

    async def restore_schedule(self) -> None:
        """After a restart: slots booked through the bot stay taken in the mock schedule."""
        restore = getattr(self.schedule, "restore", None)
        if restore is None:
            return
        async with self.db() as db:
            ids = await db.scalars(select(Booking.slot_id).where(Booking.status == "active"))
            restore(ids.all())

    # --- plumbing ----------------------------------------------------------------------

    async def _run(self, user: UserRef, handler: Callable[[Ctx], Awaitable[None]]) -> Turn:
        async with self._locks[user.id], self.db() as db:
            row = await db.get(User, user.id)
            if row is None:
                row = User(id=user.id, chat_id=user.chat_id, session={})
                db.add(row)
            row.chat_id, row.username = user.chat_id, user.username
            row.display_name = user.display_name
            ctx = Ctx(db, row, Session.model_validate(row.session or {}), Turn(), self.clock())
            await handler(ctx)
            if not ctx.deleted:
                row.session = ctx.s.model_dump(mode="json")
            await db.commit()
            return ctx.turn

    def _local(self, moment: datetime) -> datetime:
        return moment.astimezone(self.settings.tz)

    async def _known(self, c: Ctx) -> KnownPerson:
        patients = (await c.db.scalars(select(Patient).where(Patient.user_id == c.user.id))).all()
        names = [n for p in patients for n in (p.last_name, p.first_name, p.patronymic) if n]
        names += (c.user.display_name or "").split()
        return KnownPerson(
            names=tuple(names),
            phone=c.user.phone,
            username=c.user.username,
            birth_dates=tuple(p.birth_date for p in patients),
            chat_id=c.user.chat_id,
        )

    def _reset(self, c: Ctx) -> None:
        c.s = Session()

    async def _greet(self, c: Ctx) -> None:
        self._reset(c)
        common = self.kb.common
        text = common.greeting.text.format(
            part_of_day=common.greeting_for(self._local(c.now).time())
        )
        if self.settings.is_demo_booking:
            text += "\n\n" + texts.DEMO_GREETING
        c.say(text, _menu())

    async def _show_menu(self, c: Ctx) -> None:
        self._reset(c)
        c.say(self.kb.common.more_help.text, _menu())

    # --- text --------------------------------------------------------------------------

    async def _on_text(self, c: Ctx, text: str) -> None:
        if not text:
            return
        if find_emergency(text, self.clinic.clinic.emergency.phrases):
            self._reset(c)
            c.say(render.emergency_message(self.clinic), _menu())
            return
        step = c.s.step
        handlers: dict[str, Callable[[Ctx, str], Awaitable[None]]] = {
            "age": self._on_age,
            "question": self._on_answer_text,
            "audience": self._on_audience_text,
            "name": self._on_name,
            "birth": self._on_birth,
            "contact": self._on_phone_text,
            "info": self._answer_question,
            "admin_reply": self._on_admin_reply_text,
            "visit_question": self._on_visit_question,
            "payment": self._on_payment_text,
        }
        if step in handlers:
            await handlers[step](c, text)
        elif step == "slots":
            c.say(texts.PICK_SLOT)
            await self._offer_slots(c, wait=False)
        elif step in ("child_confirm", "consent", "patient", "offer_admin"):
            c.say(texts.PICK_BUTTON_SHORT)
            await self._repeat_buttons(c)
        else:  # idle / complaint
            await self._on_complaint(c, text)

    async def _on_complaint(self, c: Ctx, text: str) -> None:
        reading = await read_complaint(self.assistant, text, await self._known(c))
        if reading.unavailable:
            c.s.step = "complaint"
            self._topic_menu(c, render.texts.PICK_TOPIC)
            return
        if reading.intent == "complaint" and reading.scenario_id:
            await self._begin(c, reading.scenario_id, reading.facts, find_child_age(text))
        elif reading.intent == "complaint":
            c.s.step = "complaint"
            c.say(reading.clarify or texts.ASK_COMPLAINT, [[Button(texts.TOPIC_LIST, "t:list")]])
        elif reading.intent == "question":
            await self._answer_question(c, text)
        elif reading.intent == "admin":
            await self._handoff(c, texts.ASK_ADMIN_REASON, text)
        else:
            c.say(texts.OTHER_INTENT, _menu())

    async def _on_age(self, c: Ctx, text: str) -> None:
        months = parse_age_months(text)
        if months is None:
            c.say(texts.ASK_CHILD_AGE_AGAIN)
            return
        step = self.engine.set_patient(c.s.dialog, "child", months)
        if step is not None:
            await self._show(c, step)

    async def _on_audience_text(self, c: Ctx, text: str) -> None:
        lowered = text.lower()
        if any(w in lowered for w in ("реб", "сын", "доч", "дет")):
            await self._on_button(c, "a:child")
        elif any(w in lowered for w in ("себ", "я ", "взросл")) or lowered == "я":
            await self._on_button(c, "a:adult")
        else:
            c.say(texts.PICK_BUTTON_SHORT)
            await self._repeat_buttons(c)

    async def _on_answer_text(self, c: Ctx, text: str) -> None:
        dialog = c.s.dialog
        if dialog.scenario_id is None or dialog.question_id is None:
            await self._on_complaint(c, text)
            return
        scenario = self.kb.scenario(dialog.scenario_id)
        question = scenario.question(dialog.question_id)
        reading = await read_answer(self.assistant, scenario, question, text, await self._known(c))
        current = AskQuestion(scenario, question, attempt=max(dialog.asks, 1))
        if reading.unavailable:
            self._ask(c, render.ask_question(current, pick=True))
            return
        step = self.engine.answer(dialog, reading.facts)
        if isinstance(step, AskQuestion) and step.question.id == question.id:
            self._ask(c, render.ask_question(step, reading.clarify))
        else:
            await self._show(c, step)

    async def _on_name(self, c: Ctx, text: str) -> None:
        name = parse_full_name(text)
        if name is None:
            c.say(texts.ASK_NAME_AGAIN)
            return
        c.s.draft_name = list(name)
        c.s.step = "birth"
        c.say(texts.ASK_BIRTH)

    async def _on_birth(self, c: Ctx, text: str) -> None:
        birth = parse_birth_date(text, self._local(c.now).date())
        if birth is None or not c.s.draft_name:
            c.say(texts.ASK_BIRTH_AGAIN)
            return
        last, first, patronymic = c.s.draft_name
        patient = Patient(
            user_id=c.user.id,
            audience=c.s.dialog.audience or "adult",
            last_name=last or "",
            first_name=first or "",
            patronymic=patronymic,
            birth_date=birth,
        )
        c.db.add(patient)
        await c.db.flush()
        c.s.patient_id, c.s.draft_name = patient.id, None
        await self._identify(c)

    async def _on_phone_text(self, c: Ctx, text: str) -> None:
        phone = parse_phone(text)
        if phone is None:
            c.say(texts.ASK_CONTACT, ask_contact=True)
            return
        await self._got_phone(c, phone)

    async def _on_contact(self, c: Ctx, phone: str, owner_id: int | None) -> None:
        if c.s.step != "contact":
            c.user.phone = normalize_phone(phone)
            c.say(texts.THANKS, remove_keyboard=True)
            return
        if owner_id is not None and owner_id != c.user.id:
            c.say(texts.CONTACT_NOT_OWN, ask_contact=True)
            return
        await self._got_phone(c, normalize_phone(phone))

    async def _got_phone(self, c: Ctx, phone: str) -> None:
        c.user.phone = phone
        resume, c.s.resume = c.s.resume, None
        if resume == "handoff":
            c.say(texts.THANKS, remove_keyboard=True)
            await self._send_handoff(c)
        else:
            await self._identify(c)

    async def _on_payment_text(self, c: Ctx, text: str) -> None:
        lowered = text.lower()
        if "страх" in lowered or "дмс" in lowered or "омс" in lowered:
            await self._on_button(c, "pay:ins")
        elif "сам" in lowered or "налич" in lowered or "карт" in lowered:
            await self._on_button(c, "pay:self")
        else:
            c.s.step = "idle"
            await self._on_complaint(c, text)

    # --- buttons -----------------------------------------------------------------------

    async def _on_button(self, c: Ctx, data: str) -> None:
        head, _, rest = data.partition(":")
        handler = {
            "m": self._on_menu,
            "t": self._on_topic,
            "a": self._on_audience,
            "ca": self._on_child_confirm,
            "q": self._on_question_button,
            "s": self._on_slot,
            "c": self._on_consent,
            "p": self._on_patient,
            "pay": self._on_payment,
            "h": self._on_offer_admin,
            "r": self._on_reminder,
            "b": self._on_booking_action,
            "v": self._on_visit,
            "k": self._on_control,
            "rate": self._on_rate,
            "ar": self._on_admin_reply_button,
            "fg": self._on_forget,
        }.get(head)
        if handler is None:
            c.say(texts.STALE)
            return
        await handler(c, rest)

    async def _on_menu(self, c: Ctx, item: str) -> None:
        if item == "book":
            self._reset(c)
            c.s.step = "complaint"
            c.say(texts.ASK_COMPLAINT, [[Button(texts.TOPIC_LIST, "t:list")]])
        elif item == "ask":
            self._reset(c)
            c.s.step = "info"
            c.say(texts.ASK_INFO)
        elif item == "my":
            await self._my_bookings(c)
        elif item == "admin":  # keep the scenario answers: the administrator sees the context
            await self._handoff(c, texts.ASK_ADMIN_REASON)
        else:
            await self._show_menu(c)

    async def _on_topic(self, c: Ctx, value: str) -> None:
        if value == "list":
            c.s.step = "complaint"
            self._topic_menu(c, texts.PICK_TOPIC)
        elif value in self.kb.scenarios:
            await self._begin(c, value, {}, None)
        else:
            c.say(texts.STALE)

    async def _on_audience(self, c: Ctx, value: str) -> None:
        if c.s.step != "audience" or value not in ("adult", "child"):
            c.say(texts.STALE)
            return
        step = self.engine.set_patient(c.s.dialog, value)  # type: ignore[arg-type]
        if step is not None:
            await self._show(c, step)

    async def _on_child_confirm(self, c: Ctx, value: str) -> None:
        if c.s.step != "child_confirm" or c.s.pending_scenario is None:
            c.s.step = "idle"
            c.say(texts.STALE)
            return
        scenario, facts = c.s.pending_scenario, c.s.pending_facts
        c.s.dialog = DialogState()
        if value == "yes" and c.s.child_age is not None:
            self.engine.set_patient(c.s.dialog, "child", c.s.child_age)
        c.s.pending_scenario, c.s.pending_facts, c.s.child_age = None, {}, None
        await self._show(c, self.engine.start(c.s.dialog, scenario, facts))

    async def _on_question_button(self, c: Ctx, value: str) -> None:
        dialog = c.s.dialog
        if c.s.step != "question" or dialog.scenario_id is None or dialog.question_id is None:
            c.say(texts.STALE)
            return
        buttons = self.kb.scenario(dialog.scenario_id).question(dialog.question_id).buttons()
        if not value.isdigit() or int(value) >= len(buttons):
            c.say(texts.STALE)
            return
        await self._show(c, self.engine.answer(dialog, buttons[int(value)].facts))

    async def _on_slot(self, c: Ctx, value: str) -> None:
        if c.s.step != "slots":
            c.say(texts.STALE)
            return
        if value == "more":
            c.s.slot_offset += SLOTS_PER_PAGE
            await self._offer_slots(c, wait=False)
            return
        if value == "day":  # control visit: leave «about 10 days» and show the nearest time
            c.s.anchor, c.s.slot_offset = None, 0
            await self._offer_slots(c, wait=False)
            return
        slot = await self.schedule.get(value)
        free = slot is not None and slot in await self.schedule.free_slots(
            slot.start, slot.start + timedelta(minutes=1)
        )
        if slot is None or not free:
            c.say(texts.SLOT_TAKEN)
            c.s.slot_offset = 0
            await self._offer_slots(c, wait=False)
            return
        c.s.slot_id = slot.id
        if c.s.reschedule_of is not None:
            await self._reschedule(c, slot)
        else:
            await self._identify(c)

    async def _on_consent(self, c: Ctx, value: str) -> None:
        if c.s.step != "consent":
            c.say(texts.STALE)
            return
        if value != "yes":
            self._reset(c)
            c.say(texts.CONSENT_DECLINED, _menu())
            return
        c.user.consent_at = c.now
        if c.s.consent_child:
            c.user.guardian_at = c.now
        resume, c.s.resume = c.s.resume, None
        if resume == "handoff":
            await self._handoff(
                c, c.s.handoff_reason or texts.ASK_ADMIN_REASON, c.s.handoff_context
            )
        else:
            await self._identify(c)

    async def _on_patient(self, c: Ctx, value: str) -> None:
        if c.s.step != "patient":
            c.say(texts.STALE)
            return
        if value == "new":
            self._ask_name(c)
            return
        patient = await c.db.get(Patient, int(value)) if value.isdigit() else None
        if patient is None or patient.user_id != c.user.id:
            c.say(texts.STALE)
            return
        c.s.patient_id = patient.id
        await self._identify(c)

    async def _on_payment(self, c: Ctx, value: str) -> None:
        booking = await c.db.get(Booking, c.s.booking_id) if c.s.booking_id else None
        if booking is not None and booking.user_id == c.user.id:
            booking.payment = "insurance" if value == "ins" else "self"
        self._reset(c)
        c.say(f"{texts.THANKS} {self.kb.common.more_help.text}", _menu())

    async def _on_offer_admin(self, c: Ctx, value: str) -> None:
        if value == "yes" and c.s.handoff_reason:
            await self._handoff(c, c.s.handoff_reason, c.s.handoff_context)
        else:
            await self._show_menu(c)

    # --- scenario steps ----------------------------------------------------------------

    async def _begin(
        self, c: Ctx, scenario_id: str, facts: dict[str, FactValue], child_age: int | None
    ) -> None:
        self._reset(c)
        if child_age is not None:
            c.s.step = "child_confirm"
            c.s.pending_scenario, c.s.pending_facts, c.s.child_age = scenario_id, facts, child_age
            self._child_confirm(c)
            return
        await self._show(c, self.engine.start(c.s.dialog, scenario_id, facts))

    def _child_confirm(self, c: Ctx) -> None:
        age = format_age(c.s.child_age or 0)
        c.say(
            texts.CHILD_CONFIRM.format(age=age),
            [
                [Button(texts.CHILD_YES.format(age=age), "ca:yes")],
                [Button(texts.CHILD_NO, "ca:no")],
            ],
        )

    async def _show(self, c: Ctx, step: Step) -> None:
        if isinstance(step, AskPatient):
            c.s.step = "audience" if step.need == "audience" else "age"
            prompt = render.ask_patient(step)
            buttons = [[Button(ch.label, f"a:{ch.facts['audience']}")] for ch in prompt.choices]
            c.say(prompt.text, buttons)
        elif isinstance(step, AskQuestion):
            self._ask(c, render.ask_question(step))
        else:
            await self._done(c, step)

    def _ask(self, c: Ctx, prompt: render.Prompt) -> None:
        c.s.step = "question"
        c.say(prompt.text, [[Button(ch.label, f"q:{i}")] for i, ch in enumerate(prompt.choices)])

    async def _done(self, c: Ctx, done: Done) -> None:
        local = self._local(c.now)
        text = render.outcome_message(done, self.clinic, local, include_handoff=False)
        if done.kind == "book":
            if text:
                c.say(text)
            c.s.slot_offset = 0
            await self._offer_slots(c, wait=True)
        elif done.kind == "handoff":
            if text:
                c.say(text)
            await self._handoff(c, done.outcome.handoff_reason or "Нужна помощь администратора")
        elif done.offer_admin:
            c.s.step = "offer_admin"
            c.s.handoff_reason = done.outcome.handoff_reason
            c.say(text, [[Button(texts.HANDOFF_YES, "h:yes")], [Button(texts.HANDOFF_NO, "h:no")]])
        else:
            c.s.step = "idle"
            c.say(f"{text}\n\n{self.kb.common.more_help.text}", _menu())

    def _topic_menu(self, c: Ctx, text: str) -> None:
        menu = render.topic_menu(self.kb)
        labels = [Button(ch.label, f"t:{ch.facts['scenario']}") for ch in menu.choices]
        c.say(text, [labels[i : i + 2] for i in range(0, len(labels), 2)])

    async def _repeat_buttons(self, c: Ctx) -> None:
        step = c.s.step
        if step == "child_confirm":
            self._child_confirm(c)
        elif step == "consent":
            self._consent_prompt(c, c.s.resume or "identify")
        elif step == "patient":
            await self._identify(c)
        elif step == "offer_admin":
            c.say(
                texts.OFFER_HANDOFF,
                [[Button(texts.HANDOFF_YES, "h:yes")], [Button(texts.HANDOFF_NO, "h:no")]],
            )
        elif step == "audience":
            await self._show(c, AskPatient("audience"))

    # --- slots and booking ---------------------------------------------------------------

    def _dialog_done(self, c: Ctx) -> Done | None:
        d = c.s.dialog
        if d.scenario_id is None or d.outcome_id is None:
            return None
        scenario = self.kb.scenario(d.scenario_id)
        outcome = scenario.outcomes.get(d.outcome_id)
        return Done(scenario, d.outcome_id, outcome) if outcome else None

    async def _booking_spec(self, c: Ctx) -> tuple[BookingSpec, Scenario] | None:
        if c.s.reschedule_of is not None:
            b = await c.db.get(Booking, c.s.reschedule_of)
            if b is None:
                return None
            spec = BookingSpec(
                visit=b.visit, service=b.service, slot_policy=b.slot_policy, price_key=b.price_key
            )  # type: ignore[arg-type]
            return spec, self.kb.scenario(b.scenario_id)
        done = self._dialog_done(c)
        if done is None or done.outcome.booking is None:
            return None
        return done.outcome.booking, done.scenario

    async def _offer_slots(self, c: Ctx, *, wait: bool) -> None:
        found = await self._booking_spec(c)
        if found is None:
            await self._show_menu(c)
            return
        spec, scenario = found
        slots = await self._find(c, spec)
        if not slots and c.s.anchor is not None:  # nothing around the control day
            c.s.anchor, c.s.slot_offset = None, 0
            slots = await self._find(c, spec)
        if not slots and c.s.slot_offset:
            c.s.slot_offset = 0
            slots = await self._find(c, spec)
        if not slots:
            c.say(texts.NO_SLOTS)
            who = {"child": "Ребёнок", "adult": "Взрослый"}.get(
                c.s.dialog.audience or "", "Пациент"
            )
            await self._handoff(
                c, f"{who}: нет свободного времени, сценарий {scenario.number} «{scenario.title}»"
            )
            return
        if wait:
            c.say(self.kb.common.wait.text)
        c.s.step = "slots"
        rows = [[Button(self._slot_label(s), f"s:{s.id}")] for s in slots]
        rows.append([Button(texts.MORE_TIME, "s:more")])
        text = self.kb.common.offer.text
        if c.s.anchor is not None:
            text = texts.CONTROL_NEAR.format(date=render.format_date(c.s.anchor)) + "\n" + text
            rows.append([Button(texts.OTHER_DAY, "s:day")])
        rows.append([Button(texts.MENU_ADMIN, "m:admin")])
        c.say(text, rows)

    async def _find(self, c: Ctx, spec: BookingSpec) -> list[Slot]:
        not_before = None
        if c.s.anchor is not None:
            not_before = datetime.combine(c.s.anchor, datetime.min.time(), tzinfo=self.settings.tz)
        return await find_slots(
            self.schedule,
            spec,
            now=c.now,
            config=self.clinic.schedule,
            limit=SLOTS_PER_PAGE,
            offset=c.s.slot_offset,
            not_before=not_before,
        )

    def _slot_label(self, slot: Slot) -> str:
        start = self._local(slot.start)
        last, *initials = slot.doctor.split()
        doctor = f"{last} {' '.join(f'{n[0]}.' for n in initials)}".strip()
        day = f"{texts.WEEKDAYS_SHORT[start.weekday()]} {start:%d.%m}"
        return f"{day}, {start:%H:%M} · {doctor}"

    def _needs_consent(self, c: Ctx) -> bool:
        """Consent once per user; for a child also «я законный представитель ребёнка»."""
        child = c.s.dialog.audience == "child"
        return c.user.consent_at is None or (child and c.user.guardian_at is None)

    def _consent_prompt(self, c: Ctx, purpose: str) -> None:
        c.s.step = "consent"
        c.s.consent_child = c.s.dialog.audience == "child"
        items = [texts.CONSENT_ITEM_PD]
        if c.s.consent_child:
            items.append(texts.CONSENT_ITEM_GUARDIAN)
        lead = texts.CONSENT_HANDOFF if purpose == "handoff" else texts.CONSENT
        text = f"{lead}\n\n{texts.CONSENT_CONFIRM}\n" + ";\n".join(items) + "."
        url = self.clinic.contacts.privacy_policy_url
        if url:
            text += "\n" + texts.CONSENT_POLICY.format(url=url)
        text += "\n\n" + texts.CONSENT_WITHDRAW
        c.say(text, [[Button(texts.CONSENT_YES, "c:yes")], [Button(texts.CONSENT_NO, "c:no")]])

    def _ask_name(self, c: Ctx) -> None:
        c.s.step = "name"
        c.say(f"{self.kb.common.identify.text}\n{texts.ASK_NAME}")

    async def _identify(self, c: Ctx) -> None:
        """Script step 4: consent → patient (name, birth date) → phone → booking."""
        if self._needs_consent(c):
            c.s.resume = "identify"
            self._consent_prompt(c, "identify")
            return
        if c.s.patient_id is None:
            audience = c.s.dialog.audience or "adult"
            saved = (
                await c.db.scalars(
                    select(Patient).where(
                        Patient.user_id == c.user.id, Patient.audience == audience
                    )
                )
            ).all()
            if not saved:
                self._ask_name(c)
                return
            c.s.step = "patient"
            rows = [
                [Button(f"{p.short_name}, {p.birth_date:%d.%m.%Y}", f"p:{p.id}")] for p in saved
            ]
            rows.append([Button(texts.PATIENT_NEW, "p:new")])
            c.say(texts.PATIENT_PICK, rows)
            return
        if c.user.phone is None:
            c.s.resume, c.s.step = "identify", "contact"
            c.say(texts.ASK_CONTACT, ask_contact=True)
            return
        await self._book(c)

    async def _book(self, c: Ctx) -> None:
        found = await self._booking_spec(c)
        done = self._dialog_done(c)
        patient = await c.db.get(Patient, c.s.patient_id) if c.s.patient_id else None
        if found is None or done is None or patient is None or c.s.slot_id is None:
            await self._show_menu(c)
            return
        spec, _ = found
        try:
            slot = await self.schedule.book(c.s.slot_id)
        except SlotUnavailable:
            c.say(texts.SLOT_TAKEN)
            c.s.slot_offset = 0
            await self._offer_slots(c, wait=False)
            return
        parent = await c.db.get(Booking, c.s.control_of) if c.s.control_of else None
        booking = Booking(
            user_id=c.user.id,
            patient_id=patient.id,
            scenario_id=done.scenario.id,
            outcome_id=done.outcome_id,
            visit=spec.visit,
            service=spec.service,
            slot_policy=spec.slot_policy,
            price_key=spec.price_key,
            slot_id=slot.id,
            start_at=slot.start,
            end_at=slot.end,
            doctor=slot.doctor,
            cabinet=slot.cabinet,
            cycle=(parent.cycle + 1) if parent else 1,
            parent_id=parent.id if parent else None,
            demo=self.settings.is_demo_booking,
        )
        c.db.add(booking)
        await c.db.flush()
        self._plan_reminders(c, booking)
        c.say(self.kb.common.identify_thanks.text, remove_keyboard=True)
        text = render.booking_confirmation(
            self.kb,
            done,
            slot,
            first_name=patient.first_name,
            patronymic=patient.patronymic,
            audience=patient.audience,
            clinic=self.clinic,
            demo=self.settings.is_demo_booking,
        )  # type: ignore[arg-type]
        self._reset(c)
        c.s.step, c.s.booking_id = "payment", booking.id
        c.say(
            text,
            [
                [Button(texts.PAYMENT_SELF, "pay:self")],
                [Button(texts.PAYMENT_INSURANCE, "pay:ins")],
            ],
        )

    def _plan_reminders(self, c: Ctx, booking: Booking) -> None:
        for kind, due in plan(booking.start_at, booking.end_at, c.now):
            c.db.add(
                Reminder(booking_id=booking.id, user_id=booking.user_id, kind=kind, due_at=due)
            )

    async def _cancel_reminders(self, c: Ctx, booking: Booking) -> None:
        rows = await c.db.scalars(
            select(Reminder).where(Reminder.booking_id == booking.id, Reminder.sent_at.is_(None))
        )
        for r in rows.all():
            r.cancelled = True

    # --- my bookings: reschedule / cancel ----------------------------------------------------

    async def _user_booking(self, c: Ctx, value: str) -> Booking | None:
        booking = await c.db.get(Booking, int(value)) if value.isdigit() else None
        if booking is None or booking.user_id != c.user.id or booking.status != "active":
            return None
        return booking

    async def _my_bookings(self, c: Ctx) -> None:
        self._reset(c)
        rows = await c.db.scalars(
            select(Booking)
            .where(
                Booking.user_id == c.user.id, Booking.status == "active", Booking.start_at > c.now
            )
            .order_by(Booking.start_at)
        )
        bookings = rows.all()
        if not bookings:
            c.say(texts.MY_EMPTY, _menu())
            return
        c.say(texts.MY_TITLE)
        for b in bookings:
            patient = await c.db.get(Patient, b.patient_id)
            start = self._local(b.start_at)
            c.say(
                texts.MY_ITEM.format(
                    date=render.format_date(start.date()),
                    time=f"{start:%H:%M}",
                    patient=patient.full_name if patient else "—",
                    doctor=b.doctor,
                ),
                [[Button(texts.MOVE, f"b:move:{b.id}"), Button(texts.CANCEL, f"b:cancel:{b.id}")]],
            )

    async def _on_booking_action(self, c: Ctx, value: str) -> None:
        action, _, booking_ref = value.partition(":")
        booking = await self._user_booking(c, booking_ref)
        if booking is None or booking.start_at <= c.now:
            c.say(texts.BOOKING_GONE, _menu())
            return
        start = self._local(booking.start_at)
        when = {"date": render.format_date(start.date()), "time": f"{start:%H:%M}"}
        if action == "move":
            self._reset(c)
            c.s.reschedule_of = booking.id
            c.say(texts.MOVE_PICK)
            await self._offer_slots(c, wait=False)
        elif action == "cancel":
            c.say(
                texts.CANCEL_ASK.format(**when),
                [
                    [Button(texts.CANCEL_YES, f"b:drop:{booking.id}")],
                    [Button(texts.CANCEL_KEEP, f"b:keep:{booking.id}")],
                ],
            )
        elif action == "drop":
            await self.schedule.release(booking.slot_id)
            booking.status = "cancelled"
            await self._cancel_reminders(c, booking)
            self._reset(c)
            c.say(texts.CANCELLED, _menu())
        else:
            c.say(texts.KEPT, _menu())

    async def _reschedule(self, c: Ctx, slot: Slot) -> None:
        booking = await c.db.get(Booking, c.s.reschedule_of) if c.s.reschedule_of else None
        if booking is None or booking.status != "active":
            c.say(texts.BOOKING_GONE, _menu())
            return
        try:
            await self.schedule.book(slot.id)
        except SlotUnavailable:
            c.say(texts.SLOT_TAKEN)
            await self._offer_slots(c, wait=False)
            return
        await self.schedule.release(booking.slot_id)
        await self._cancel_reminders(c, booking)
        booking.slot_id, booking.start_at, booking.end_at = slot.id, slot.start, slot.end
        booking.doctor, booking.cabinet, booking.confirmed_at = slot.doctor, slot.cabinet, None
        self._plan_reminders(c, booking)
        self._reset(c)
        start = self._local(slot.start)
        text = texts.MOVED.format(
            date=render.format_date(start.date()), time=f"{start:%H:%M}", doctor=slot.doctor
        )
        if self.settings.is_demo_booking:
            text += "\n\n" + texts.DEMO_BOOKING
        c.say(text, _menu())

    # --- notifications ---------------------------------------------------------------------

    async def _fire(self, db: AsyncSession, reminder: Reminder, now: datetime) -> Turn:
        reminder.sent_at = now
        booking = await db.get(Booking, reminder.booking_id)
        user = await db.get(User, reminder.user_id)
        patient = await db.get(Patient, booking.patient_id) if booking else None
        if booking is None or user is None or patient is None or booking.status != "active":
            return Turn()
        return Turn([self._reminder_reply(reminder.kind, booking, patient, user.chat_id)])

    def _reminder_reply(self, kind: str, b: Booking, patient: Patient, chat_id: int) -> Reply:
        start = self._local(b.start_at)
        actions = (
            (Button(texts.CONFIRM, f"r:ok:{b.id}"),),
            (Button(texts.MOVE, f"b:move:{b.id}"), Button(texts.CANCEL, f"b:cancel:{b.id}")),
        )
        common = self.kb.common
        if kind == DAY_BEFORE:
            text = texts.REMINDER_DAY.format(
                when=render.format_when(start), patient=patient.full_name, doctor=b.doctor
            )
            docs = render.documents(self.kb, patient.audience)  # type: ignore[arg-type]
            return Reply(f"{text}\n\n{docs}", actions, chat_id=chat_id)
        if kind == TWO_HOURS:
            center = self.clinic.contacts.trauma_center
            text = texts.REMINDER_TWO_HOURS.format(
                when=render.format_when(start), address=center.address, entrance=center.entrance
            )
            day = center.underground_parking_hours.contains(start.time())
            parking = common.parking_day.text if day else common.parking_night.text
            return Reply(f"{text}\n\n{parking}", actions, chat_id=chat_id)
        if kind == AFTER_VISIT and b.cycle >= 2:
            return Reply(
                texts.FEELING,
                (
                    (Button(texts.FEELING_OK, f"v:ok:{b.id}"),),
                    (Button(texts.AFTER_VISIT_QUESTION, f"v:q:{b.id}"),),
                ),
                chat_id=chat_id,
            )
        return Reply(
            texts.AFTER_VISIT,
            (
                (Button(texts.AFTER_VISIT_OK, f"v:ok:{b.id}"),),
                (Button(texts.AFTER_VISIT_QUESTION, f"v:q:{b.id}"),),
            ),
            chat_id=chat_id,
        )

    async def _demo_next(self, c: Ctx) -> None:
        if not self.settings.demo_mode:
            c.say(texts.DEMO_ONLY)
            return
        reminder = await c.db.scalar(
            select(Reminder)
            .join(Booking, Booking.id == Reminder.booking_id)
            .where(
                Reminder.user_id == c.user.id,
                Reminder.sent_at.is_(None),
                ~Reminder.cancelled,
                Booking.status == "active",
            )
            .order_by(Reminder.due_at)
            .limit(1)
        )
        if reminder is None:
            c.say(texts.DEMO_NONE, _menu())
            return
        c.turn.replies += (await self._fire(c.db, reminder, c.now)).replies

    async def _on_reminder(self, c: Ctx, value: str) -> None:
        _, _, ref = value.partition(":")
        booking = await self._user_booking(c, ref)
        if booking is None:
            c.say(texts.BOOKING_GONE)
            return
        booking.confirmed_at = c.now
        c.say(texts.CONFIRMED)

    async def _on_visit(self, c: Ctx, value: str) -> None:
        answer, _, ref = value.partition(":")
        booking = await self._user_booking(c, ref)
        if booking is None:
            c.say(texts.BOOKING_GONE)
            return
        booking.survey = "ok" if answer == "ok" else "question"
        if answer == "ok":
            if booking.cycle >= 2:
                self._rate_prompt(c, booking)
            else:
                c.say(texts.GLAD)
                self._control_prompt(c, booking)
        else:
            self._reset(c)
            c.s.step, c.s.visit_booking = "visit_question", booking.id
            c.say(texts.ASK_VISIT_QUESTION)

    async def _on_visit_question(self, c: Ctx, text: str) -> None:
        booking = await self._user_booking(c, str(c.s.visit_booking or ""))
        if booking is None:
            await self._on_complaint(c, text)
            return
        start = self._local(booking.start_at)
        reason = texts.VISIT_QUESTION_REASON.format(
            date=render.format_date(start.date()), booking=booking.id
        )
        await self._handoff(c, reason, text)
        if booking.cycle >= 2:
            self._rate_prompt(c, booking)
        else:
            self._control_prompt(c, booking)

    def _control_prompt(self, c: Ctx, booking: Booking) -> None:
        after = days_word(self.clinic.clinic.control_visit_after_days)
        c.say(
            texts.OFFER_CONTROL.format(days=after),
            [
                [Button(texts.CONTROL_YES, f"k:yes:{booking.id}")],
                [Button(texts.CONTROL_NO, f"k:no:{booking.id}")],
            ],
        )

    def _rate_prompt(self, c: Ctx, booking: Booking) -> None:
        c.say(texts.RATE, [[Button(str(n), f"rate:{n}:{booking.id}") for n in range(1, 6)]])

    async def _on_control(self, c: Ctx, value: str) -> None:
        answer, _, ref = value.partition(":")
        booking = await self._user_booking(c, ref)
        patient = await c.db.get(Patient, booking.patient_id) if booking else None
        if answer != "yes" or booking is None or patient is None:
            await self._show_menu(c)
            return
        self._reset(c)
        c.s.control_of, c.s.patient_id = booking.id, patient.id
        after = timedelta(days=self.clinic.clinic.control_visit_after_days)
        c.s.anchor = self._local(booking.start_at).date() + after
        age = age_in_months(patient.birth_date, self._local(c.now).date())
        self.engine.set_patient(c.s.dialog, patient.audience, age)  # type: ignore[arg-type]
        step = self.engine.start(c.s.dialog, "repeat_visit", {"first_visit_here": True})
        await self._show(c, step)

    async def _on_rate(self, c: Ctx, value: str) -> None:
        score, _, ref = value.partition(":")
        booking = await self._user_booking(c, ref)
        if booking is not None and score.isdigit() and 1 <= int(score) <= 5:
            booking.rating = int(score)
        self._reset(c)
        c.say(f"{texts.RATED} {self.kb.common.more_help.text}", _menu())

    # --- questions and administrators ------------------------------------------------------

    async def _answer_question(self, c: Ctx, text: str) -> None:
        reading = await read_question(self.assistant, text, await self._known(c))
        c.s.handoff_reason, c.s.handoff_context = "Вопрос пациента о травмпункте", text
        offer = [[Button(texts.HANDOFF_YES, "h:yes")], [Button(texts.HANDOFF_NO, "h:no")]]
        if reading.answer and not reading.offer_admin:
            c.s.step = "idle"
            c.say(f"{reading.answer}\n\n{self.kb.common.more_help.text}", _menu())
            return
        c.s.step = "offer_admin"
        if reading.answer:
            c.say(f"{reading.answer}\n\n{texts.OFFER_HANDOFF}", offer)
        else:
            lead = texts.INFO_DOWN if reading.unavailable else texts.INFO_UNKNOWN
            c.say(f"{lead} {texts.OFFER_HANDOFF}", offer)

    async def _handoff(self, c: Ctx, reason: str, context: str | None = None) -> None:
        """Pass the request to ADMIN_CHAT_ID; consent and a phone number come first."""
        c.s.handoff_reason, c.s.handoff_context = reason, context
        if not self.settings.admin_chat_id:
            log.error("ADMIN_CHAT_ID is not set: cannot hand off %r", reason)
            c.s.step = "idle"
            c.say(texts.ADMIN_UNAVAILABLE, _menu())
            return
        if self._needs_consent(c):
            c.s.resume = "handoff"
            self._consent_prompt(c, "handoff")
            return
        if c.user.phone is None:
            c.s.resume, c.s.step = "handoff", "contact"
            c.say(texts.ASK_CONTACT, ask_contact=True)
            return
        await self._send_handoff(c)

    async def _send_handoff(self, c: Ctx) -> None:
        reason = c.s.handoff_reason or texts.ASK_ADMIN_REASON
        handoff = Handoff(user_id=c.user.id, reason=reason[:512])
        c.db.add(handoff)
        await c.db.flush()
        c.turn.admin.append(AdminPost(await self._admin_text(c, handoff), handoff.id))
        text = render.handoff_message(self.clinic, self._local(c.now))
        c.s.step, c.s.handoff_reason, c.s.handoff_context = "idle", None, None
        c.say(f"{text}\n\n{self.kb.common.more_help.text}", _menu(), remove_keyboard=False)

    async def _admin_text(self, c: Ctx, handoff: Handoff) -> str:
        lines = [f"Обращение № {handoff.id} из бота травмпункта", f"Причина: {handoff.reason}"]
        dialog = c.s.dialog
        if dialog.scenario_id:
            scenario = self.kb.scenario(dialog.scenario_id)
            lines.append(f"Сценарий: {scenario.number} «{scenario.title}»")
            if dialog.audience:
                who = "ребёнок" if dialog.audience == "child" else "взрослый"
                if dialog.age_months is not None and dialog.audience == "child":
                    who += f", {format_age(dialog.age_months)}"
                lines.append(f"Пациент: {who}")
            for q in scenario.questions:
                label = _answer_label(q, dialog.facts)
                if label and all(f.name in dialog.facts for f in q.fields if f.default is None):
                    lines.append(f"• {q.ask} — {label}")
        if c.s.handoff_context:
            lines.append(f"Сообщение пациента: {c.s.handoff_context}")
        if c.s.patient_id and (patient := await c.db.get(Patient, c.s.patient_id)):
            lines.append(f"ФИО пациента: {patient.full_name}, {patient.birth_date:%d.%m.%Y}")
        lines.append(f"Телефон: {c.user.phone or '—'}")
        tg = c.user.display_name or ""
        if c.user.username:
            tg += f" @{c.user.username}"
        lines.append(f"Telegram: {tg.strip() or '—'} (id {c.user.id})")
        lines.append("Ответьте реплаем на это сообщение — бот перешлёт ответ пациенту.")
        return "\n".join(lines)

    async def _on_admin_reply_button(self, c: Ctx, value: str) -> None:
        handoff = await c.db.get(Handoff, int(value)) if value.isdigit() else None
        if handoff is None or handoff.user_id != c.user.id:
            c.say(texts.STALE)
            return
        self._reset(c)
        c.s.step, c.s.reply_handoff = "admin_reply", handoff.id
        c.say(texts.ASK_ADMIN_REPLY)

    async def _on_admin_reply_text(self, c: Ctx, text: str) -> None:
        handoff_id = c.s.reply_handoff
        last = await c.db.scalar(
            select(AdminMessage.message_id)
            .where(AdminMessage.handoff_id == handoff_id)
            .order_by(AdminMessage.message_id.desc())
            .limit(1)
        )
        self._reset(c)
        if handoff_id is None:
            await self._on_complaint(c, text)
            return
        c.turn.admin.append(
            AdminPost(f"Пациент (обращение № {handoff_id}): {text}", handoff_id, reply_to=last)
        )
        c.say(texts.ADMIN_REPLY_SENT, _menu())

    # --- /forget: withdraw consent ---------------------------------------------------------

    async def _forget_ask(self, c: Ctx) -> None:
        self._reset(c)
        c.say(
            texts.FORGET_ASK,
            [[Button(texts.FORGET_YES, "fg:yes")], [Button(texts.FORGET_NO, "fg:no")]],
        )

    async def _on_forget(self, c: Ctx, value: str) -> None:
        if value != "yes":
            c.say(texts.FORGET_KEPT, _menu())
            return
        user_id = c.user.id
        bookings = (await c.db.scalars(select(Booking).where(Booking.user_id == user_id))).all()
        for b in bookings:
            if b.status == "active":
                await self.schedule.release(b.slot_id)
        handoffs = (await c.db.scalars(select(Handoff).where(Handoff.user_id == user_id))).all()
        handoff_ids = [h.id for h in handoffs]
        for model, column, values in (
            (Reminder, Reminder.user_id, [user_id]),
            (AdminMessage, AdminMessage.handoff_id, handoff_ids),
            (Handoff, Handoff.user_id, [user_id]),
        ):
            if values:
                await c.db.execute(delete(model).where(column.in_(values)))
        for b in sorted(bookings, key=lambda b: b.cycle, reverse=True):  # control visits first
            await c.db.delete(b)
        await c.db.flush()
        await c.db.execute(delete(Patient).where(Patient.user_id == user_id))
        await c.db.delete(c.user)
        c.deleted = True
        if self.settings.admin_chat_id and handoff_ids:
            numbers = ", ".join(f"№ {n}" for n in handoff_ids)
            c.turn.admin.append(
                AdminPost(texts.FORGET_ADMIN.format(user=user_id, handoffs=numbers))
            )
        c.say(texts.FORGOTTEN, _menu())
