"""The patient dialog, independent of the messenger (Telegram now, MAX later).

Input: a text, a pressed button (callback data), a shared contact, a command.
Output: a ``Turn`` — replies to the patient (text + buttons) and posts for the
administrators' chat. The Telegram layer (app/bot/telegram.py) only translates.

Branches come from the engine; the LLM only reads free text (app/llm/reader.py) and the
bot falls back to buttons when it cannot. Per-user state lives in ``users.session``.

Booking is a wizard in one message (part C): a pressed button replaces the message with
the next screen («Шаг N из 5», «Назад»); the topic buttons follow what the patient said
(body area); the time is chosen as a day, then a grid of times with the doctor on duty.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import render, slots_view, texts
from app.bot.layout import DAY_COLUMNS, TIME_COLUMNS, grid, pack
from app.bot.render import Card, Venue
from app.config import ClinicData, Settings
from app.db.models import AdminMessage, Booking, Handoff, Patient, Reminder, User, utcnow
from app.db.session import Database
from app.flow import topics
from app.flow.age import find_child_age, parse_age_months
from app.flow.commands import find_command
from app.flow.engine import (
    FALLBACK_OUTCOME,
    AskPatient,
    AskQuestion,
    DialogState,
    Done,
    Engine,
    Step,
)
from app.flow.identity import (
    age_in_months,
    normalize_phone,
    parse_birth_date,
    parse_full_name,
    parse_phone,
)
from app.flow.loader import KnowledgeBase
from app.flow.safety import find_phrase
from app.flow.schema import Booking as BookingSpec
from app.flow.schema import FactValue, Question, Scenario
from app.flow.words import days as days_word
from app.flow.words import format_age
from app.llm.assistant import Assistant
from app.llm.masking import KnownPerson
from app.llm.reader import read_answer, read_question, read_text
from app.notify.planner import AFTER_VISIT, DAY_BEFORE, TWO_HOURS, plan
from app.schedule.base import ScheduleProvider, Slot, SlotUnavailable
from app.schedule.rules import all_slots

log = logging.getLogger(__name__)

HANDOFF_IDLE = timedelta(days=1)  # an open request with no activity for a day is closed
CRISIS_THREAD = timedelta(minutes=30)  # later crisis messages go under the same note
MAX_HISTORY = 12  # «Назад» steps kept per user
CONTROL_DAYS_BEFORE = 3  # «Другой день» for a control visit starts a little before the target

DraftSink = Callable[[str], Awaitable[None]]


# --- what goes in and out ----------------------------------------------------------------


@dataclass(frozen=True)
class UserRef:
    id: int
    chat_id: int
    username: str | None = None
    display_name: str | None = None


ButtonStyle = Literal["primary", "success", "danger"]


@dataclass(frozen=True)
class Button:
    label: str
    data: str  # callback data, at most 64 bytes
    style: ButtonStyle | None = None  # Bot API 9.4 colours; older clients show plain buttons


@dataclass(frozen=True)
class Reply:
    text: str
    buttons: tuple[tuple[Button, ...], ...] = ()
    ask_contact: bool = False  # «Поделиться контактом» keyboard
    remove_keyboard: bool = False
    chat_id: int | None = None  # None: the chat of the user being served
    edit: bool = False  # replace the message whose button was pressed (wizard screens)
    screen: bool = False  # a wizard screen: the previous one loses its buttons
    card: Card | None = None  # sent as a rich message if possible; ``text`` is the fallback
    venue: Venue | None = None  # a map point (sendVenue); ``text`` is the address


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
    "topic",
    "describe",
    "child_confirm",
    "audience",
    "age",
    "question",
    "days",
    "periods",
    "times",
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

# Wizard screens: «Назад» works between them. Stage numbers for «Шаг N из 5».
STAGE_OF: dict[str, int] = {
    "complaint": 1,
    "topic": 1,
    "describe": 1,
    "child_confirm": 2,
    "audience": 2,
    "age": 2,
    "question": 3,
    "days": 4,
    "periods": 4,
    "times": 4,
    "consent": 5,
    "patient": 5,
    "name": 5,
    "birth": 5,
}
WIZARD_STEPS = frozenset(STAGE_OF)
# Steps where the text is data (age, name, phone…): «отмени запись» there is not a command.
DATA_STEPS = frozenset({"age", "name", "birth", "contact", "admin_reply", "visit_question"})
MENU_ITEM = {"book": "book", "my_bookings": "my", "ask": "ask", "admin": "admin"}


class Session(BaseModel):
    """Per-user dialog state, stored as JSON in users.session."""

    step: StepName = "idle"
    dialog: DialogState = DialogState()
    wizard: bool = False  # booking started from «Записаться» or a complaint: «Шаг N из 5»
    menu: str | None = None  # topic menu on screen
    area: str | None = None  # body area from the complaint (app/flow/schema.py, Area)
    pending_scenario: str | None = None
    pending_facts: dict[str, FactValue] = {}
    child_age: int | None = None
    day: date | None = None  # chosen day (time grid)
    period: str | None = None  # «Утро 8–12» … «Ночь 23–8» on the chosen day
    day_from: date | None = None  # first day on the day picker page
    slot_id: str | None = None
    patient_id: int | None = None
    draft_name: list[str | None] | None = None
    resume: Literal["identify", "handoff"] | None = None
    handoff_reason: str | None = None  # for the administrators
    handoff_context: str | None = None
    handoff_why: str | None = None  # for the patient: the outcome's patient_reason
    control_of: int | None = None
    anchor: date | None = None  # control visit: offer time around this day
    reschedule_of: int | None = None
    booking_id: int | None = None
    visit_booking: int | None = None
    reply_handoff: int | None = None
    consent_child: bool = False  # the consent shown includes «законный представитель»
    history: list[dict[str, Any]] = []  # earlier wizard screens, for «Назад»


def _screen_key(s: Session) -> tuple[object, ...]:
    d = s.dialog
    return (
        s.step,
        s.menu,
        d.scenario_id,
        d.question_id,
        d.facts.get("audience"),
        d.age_months,
        s.day,
        s.period,
        s.day_from,
        s.slot_id,
        s.patient_id,
        s.draft_name is not None,
    )


@dataclass
class Ctx:
    db: AsyncSession
    user: User
    s: Session
    turn: Turn
    now: datetime  # UTC
    deleted: bool = False  # /forget removed the user row
    via_button: bool = False  # the input was a pressed button: its message can be replaced
    back: bool = False  # «Назад» restored an earlier screen
    draft: DraftSink | None = None  # streams a partial answer (Telegram sendMessageDraft)

    def say(self, text: str, buttons: Sequence[Sequence[Button]] | None = None, **kw: Any) -> None:
        rows = tuple(tuple(row) for row in (buttons or []))
        self.turn.replies.append(Reply(text, rows, **kw))

    def first(self) -> bool:
        """Nothing sent yet in this turn: the reply may replace the pressed message."""
        return self.via_button and not self.turn.replies


def _answer_label(question: Question, facts: dict[str, FactValue]) -> str | None:
    """The button that matches the given answer, for the administrators' summary."""
    for choice in question.buttons():
        if all(facts.get(k) == v for k, v in choice.facts.items()):
            return choice.label
    return None


def _home() -> list[Button]:
    return [Button(texts.MENU_HOME, "m:home")]


def _menu() -> list[list[Button]]:
    return [
        [Button(texts.MENU_BOOK, "m:book", style="primary")],
        [Button(texts.MENU_ASK, "m:ask")],
        [Button(texts.MENU_MY, "m:my")],
        [Button(texts.MENU_ADMIN, "m:admin")],
    ]


def _parse_day(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        return None


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

    async def text(self, user: UserRef, text: str, draft: DraftSink | None = None) -> Turn:
        return await self._run(user, lambda c: self._on_text(c, text.strip()), draft=draft)

    async def button(self, user: UserRef, data: str) -> Turn:
        return await self._run(user, lambda c: self._on_button(c, data), via_button=True)

    async def contact(self, user: UserRef, phone: str, owner_id: int | None) -> Turn:
        return await self._run(user, lambda c: self._on_contact(c, phone, owner_id))

    async def call_admin(self, user: UserRef) -> Turn:
        return await self._run(user, lambda c: self._handoff(c, texts.ASK_ADMIN_REASON))

    async def menu(self, user: UserRef) -> Turn:
        return await self._run(user, self._show_menu)

    async def command(self, user: UserRef, item: str) -> Turn:
        """/book, /ask, /my — the same as the main menu buttons."""
        return await self._run(user, lambda c: self._on_menu(c, item))

    async def demo_next(self, user: UserRef) -> Turn:
        return await self._run(user, self._demo_next)

    async def forget(self, user: UserRef) -> Turn:
        """/forget: withdraw consent and delete the user's data, after a confirmation."""
        return await self._run(user, self._forget_ask)

    async def admin_reply(self, message_id: int, text: str) -> Turn | str | None:
        """An administrator replied to a bot message in the admin chat.

        Turn — the answer for the patient; str — a note for the admin chat; None — unknown.
        """
        async with self.db() as db:
            link = await db.get(AdminMessage, message_id)
            if link is None:
                return None
            handoff = await db.get(Handoff, link.handoff_id)
            user = await db.get(User, handoff.user_id) if handoff else None
            if handoff is None or user is None:
                return None
            if handoff.kind == "crisis":
                return texts.CRISIS_NO_FORWARD
            handoff.updated_at = self.clock()
            await db.commit()
            reply = Reply(
                texts.ADMIN_ANSWER.format(text=text),
                ((Button(texts.ADMIN_ANSWER_REPLY, f"ar:{handoff.id}"),),),
                chat_id=user.chat_id,
            )
            return Turn([reply])

    async def close_handoff(self, handoff_id: int, by: str) -> bool:
        """«Закрыть обращение» in the admin chat: the next request starts a new one."""
        async with self.db() as db:
            handoff = await db.get(Handoff, handoff_id)
            if handoff is None or handoff.status != "open":
                return False
            handoff.status, handoff.closed_at, handoff.closed_by = "closed", self.clock(), by[:128]
            await db.commit()
            return True

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

    def top_up_schedule(self) -> None:
        """The mock schedule grows by a day each day (called by the scheduler)."""
        top_up = getattr(self.schedule, "top_up", None)
        if top_up is not None and top_up(self._local(self.clock()).date()):
            log.info(
                "demo schedule topped up to %s days ahead", self.clinic.schedule.demo.days_ahead
            )

    async def restore_schedule(self) -> None:
        """After a restart: slots booked through the bot stay taken in the mock schedule."""
        restore = getattr(self.schedule, "restore", None)
        if restore is None:
            return
        async with self.db() as db:
            ids = await db.scalars(select(Booking.slot_id).where(Booking.status == "active"))
            restore(ids.all())

    # --- plumbing ----------------------------------------------------------------------

    async def _run(
        self,
        user: UserRef,
        handler: Callable[[Ctx], Awaitable[None]],
        *,
        via_button: bool = False,
        draft: DraftSink | None = None,
    ) -> Turn:
        async with self._locks[user.id], self.db() as db:
            row = await db.get(User, user.id)
            if row is None:
                row = User(id=user.id, chat_id=user.chat_id, session={})
                db.add(row)
            row.chat_id, row.username = user.chat_id, user.username
            row.display_name = user.display_name
            session = Session.model_validate(row.session or {})
            ctx = Ctx(db, row, session, Turn(), self.clock(), via_button=via_button, draft=draft)
            before = session.model_dump(mode="json")
            before_key = _screen_key(session)
            await handler(ctx)
            if not ctx.deleted:
                self._remember_screen(ctx, before, before_key)
                row.session = ctx.s.model_dump(mode="json")
            await db.commit()
            return ctx.turn

    def _remember_screen(self, c: Ctx, before: dict[str, Any], before_key: tuple) -> None:
        """Keep the previous wizard screen for «Назад» when the screen changed."""
        if c.back:
            return
        if c.s.step not in WIZARD_STEPS:
            c.s.history = []
            return
        earlier = before.pop("history", [])
        if before["step"] in WIZARD_STEPS and _screen_key(c.s) != before_key:
            c.s.history = [*earlier, before][-MAX_HISTORY:]
        else:
            c.s.history = earlier

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
        c.say(self.kb.common.more_help.text, _menu(), edit=c.first())

    # --- screens -----------------------------------------------------------------------

    def _header(self, c: Ctx) -> str | None:
        """«Шаг 2 из 5 · Для кого запись» while booking from «Записаться» or a complaint."""
        stage = STAGE_OF.get(c.s.step)
        if not c.s.wizard or stage is None or c.s.resume == "handoff":
            return None
        return texts.STEP.format(n=stage, total=len(texts.STAGES), name=texts.STAGES[stage - 1])

    def _screen(
        self,
        c: Ctx,
        text: str,
        buttons: Sequence[Button] = (),
        *,
        top: Sequence[Sequence[Button]] = (),
        rows: Sequence[Sequence[Button]] = (),
        columns: int | None = None,
        back: bool | None = None,
    ) -> None:
        """A wizard screen: replaces the pressed message; short labels two in a row."""
        header = self._header(c)
        body = f"{header}\n\n{text}" if header else text
        layout = [list(r) for r in top]
        layout += grid(list(buttons), columns) if columns else pack(buttons)
        layout += [list(r) for r in rows]
        if back is None:
            back = c.s.wizard or bool(c.s.history) or c.s.reschedule_of is not None
        if back:
            layout.append([Button(texts.BACK, "w:back")])
        c.say(body, layout, edit=c.first(), screen=True)

    def _final(self, c: Ctx, text: str, rows: Sequence[Sequence[Button]] = ()) -> None:
        """The last message of a wizard (a route, an answer): it replaces the pressed one."""
        c.say(text, rows, edit=c.first())

    async def _render_screen(self, c: Ctx) -> None:
        """Show the screen of the current step again («Назад», or text instead of a button)."""
        step = c.s.step
        if step == "complaint":
            self._complaint_screen(c)
        elif step == "topic":
            self._menu_screen(c, c.s.menu or topics.start_menu(self.kb, c.s.area))
        elif step == "describe":
            self._screen(c, texts.DESCRIBE)
        elif step == "child_confirm":
            self._child_confirm(c)
        elif step in ("audience", "age"):
            await self._show(c, AskPatient(step))  # type: ignore[arg-type]
        elif step == "question" and c.s.dialog.scenario_id and c.s.dialog.question_id:
            scenario = self.kb.scenario(c.s.dialog.scenario_id)
            c.s.dialog.asks = 1
            current = AskQuestion(scenario, scenario.question(c.s.dialog.question_id))
            self._ask(c, render.ask_question(current))
        elif step == "days":
            await self._show_days(c)
        elif step == "periods":
            await self._show_periods(c)
        elif step == "times":
            await self._show_times(c)
        elif step == "consent":
            self._consent_prompt(c, c.s.resume or "identify")
        elif step == "patient":
            await self._identify(c)
        elif step == "name":
            self._ask_name(c)
        elif step == "birth":
            self._ask_birth(c)
        else:
            await self._show_menu(c)

    async def _on_back(self, c: Ctx, _: str) -> None:
        if not c.s.history:
            await self._show_menu(c)
            return
        *rest, snapshot = c.s.history
        c.s = Session.model_validate(snapshot)
        c.s.history = rest
        c.back = True
        await self._render_screen(c)

    def _complaint_screen(self, c: Ctx) -> None:
        c.s.step = "complaint"
        self._screen(c, texts.ASK_COMPLAINT, [Button(texts.TOPIC_LIST, "t:list", "primary")])

    def _menu_screen(self, c: Ctx, menu_id: str) -> None:
        """Topic buttons about what happened, only those that fit the body area."""
        c.s.step, c.s.menu = "topic", menu_id
        items = topics.visible(self.kb, menu_id, c.s.area)
        buttons = [Button(item.label, f"t:{item.id}") for item in items]
        self._screen(c, self.kb.topics.menus[menu_id].text, buttons)

    # --- text --------------------------------------------------------------------------

    async def _on_text(self, c: Ctx, text: str) -> None:
        if not text:
            return
        if find_phrase(text, self.clinic.clinic.crisis.phrases):
            await self._crisis(c, text)
            return
        if find_phrase(text, self.clinic.clinic.emergency.phrases):
            self._reset(c)
            c.say(render.emergency_message(self.clinic), _menu())
            return
        step = c.s.step
        if step not in DATA_STEPS and (action := find_command(text)) is not None:
            await self._on_menu(c, MENU_ITEM[action])  # the same screen as the menu button
            return
        handlers: dict[str, Callable[[Ctx, str], Awaitable[None]]] = {
            "age": self._on_age,
            "question": self._on_answer_text,
            "audience": self._on_audience_text,
            "name": self._on_name,
            "birth": self._on_birth,
            "contact": self._on_phone_text,
            "info": self._on_free_text,
            "admin_reply": self._on_admin_reply_text,
            "visit_question": self._on_visit_question,
            "payment": self._on_payment_text,
        }
        if step in handlers:
            await handlers[step](c, text)
        elif step in ("days", "periods", "times"):
            c.say(texts.PICK_DAY if step == "days" else texts.PICK_SLOT)
            await self._render_screen(c)
        elif step in ("child_confirm", "consent", "patient", "offer_admin"):
            c.say(texts.PICK_BUTTON_SHORT)
            await self._repeat_buttons(c)
        else:  # idle / complaint / topic / describe
            await self._on_free_text(c, text)

    async def _on_free_text(self, c: Ctx, text: str) -> None:
        """Any free text outside a scenario question: one route call decides what it is.

        Nothing here reaches the administrators: only buttons and scenario outcomes do.
        """
        asked = c.s.step == "info"
        reading = await read_text(self.assistant, text, await self._known(c))
        if reading.unavailable:
            if asked:
                c.s.handoff_reason, c.s.handoff_context = texts.QUESTION_REASON, text
                c.s.step = "offer_admin"
                c.say(texts.INFO_DOWN, self._ask_admin_buttons())
            else:  # no LLM: the word list narrows the topics
                area = topics.guess_area(text) or c.s.area
                self._topics_for(c, area, topics.guess_candidates(text), texts.PICK_TOPIC)
            return
        if reading.kind == "crisis":
            await self._crisis(c, text)
        elif reading.kind == "complaint" and reading.scenario_id:
            area = reading.area or c.s.area  # «Опишите своими словами» after «Рука или нога»
            fits = self.kb.scenario(reading.scenario_id).fits(area)
            c.s.area = area if fits else reading.area
            await self._begin(c, reading.scenario_id, reading.facts, find_child_age(text))
        elif reading.kind == "complaint":
            area = reading.area or topics.guess_area(text) or c.s.area
            self._topics_for(c, area, reading.candidates, reading.clarify or texts.ASK_COMPLAINT)
        elif reading.kind == "menu_action" and reading.action in MENU_ITEM:
            await self._on_menu(c, MENU_ITEM[reading.action])
        elif reading.kind == "clinic_question":  # «У меня нет этой информации» only here
            await self._answer_question(c, text)
        elif reading.kind == "other_medical":
            self._reset(c)
            c.say(texts.NOT_OUR_PROFILE, _menu())
        else:  # offtopic: the request is not carried out
            self._reset(c)
            c.say(texts.OFFTOPIC, _menu())

    def _topics_for(self, c: Ctx, area: str | None, candidates: list[str], ask: str) -> None:
        """A complaint without a clear scenario: the narrowest fitting topic menu."""
        self._reset(c)
        c.s.wizard, c.s.area = True, area
        menu = topics.best_menu(self.kb, area, candidates)
        if menu is not None:
            self._menu_screen(c, menu)
            return
        c.s.step = "complaint"
        self._screen(c, ask, [Button(texts.TOPIC_LIST, "t:list", "primary")])

    async def _crisis(self, c: Ctx, text: str) -> None:
        """Suicide or self-harm: a calm answer with a helpline, a separate note to the admins."""
        self._reset(c)
        line = self.clinic.contacts.crisis_line_phone
        c.say(texts.CRISIS.format(phone=line))
        if not self.settings.admin_chat_id:
            log.error("ADMIN_CHAT_ID is not set: crisis note for user %s not delivered", c.user.id)
            return
        recent = await c.db.scalar(
            select(Handoff)
            .where(
                Handoff.user_id == c.user.id,
                Handoff.kind == "crisis",
                Handoff.updated_at >= c.now - CRISIS_THREAD,
            )
            .order_by(Handoff.id.desc())
            .limit(1)
        )
        if recent is not None:
            recent.updated_at = c.now
            thread = await self._first_admin_message(c, recent.id)
            c.turn.admin.append(
                AdminPost(texts.CRISIS_MORE.format(text=text), recent.id, reply_to=thread)
            )
            return
        note = Handoff(
            user_id=c.user.id, reason=texts.CRISIS_REASON, kind="crisis", status="closed"
        )
        c.db.add(note)
        await c.db.flush()
        who = c.user.display_name or "без имени"
        if c.user.username:
            who += f" @{c.user.username}"
        phone = f", телефон {c.user.phone}" if c.user.phone else ""
        c.turn.admin.append(
            AdminPost(
                texts.CRISIS_ADMIN.format(who=who, id=c.user.id, phone=phone, text=text, line=line),
                note.id,
            )
        )

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
            await self._on_audience(c, "child")
        elif any(w in lowered for w in ("себ", "я ", "взросл")) or lowered == "я":
            await self._on_audience(c, "adult")
        else:
            c.say(texts.PICK_BUTTON_SHORT)
            await self._repeat_buttons(c)

    async def _on_answer_text(self, c: Ctx, text: str) -> None:
        dialog = c.s.dialog
        if dialog.scenario_id is None or dialog.question_id is None:
            await self._on_free_text(c, text)
            return
        scenario = self.kb.scenario(dialog.scenario_id)
        question = scenario.question(dialog.question_id)
        reading = await read_answer(self.assistant, scenario, question, text, await self._known(c))
        if reading.crisis:
            await self._crisis(c, text)
            return
        current = AskQuestion(scenario, question, attempt=max(dialog.asks, 1))
        if reading.unavailable:
            self._ask(c, render.ask_question(current, pick=True), stuck=current.stuck)
            return
        step = self.engine.answer(dialog, reading.facts)
        if isinstance(step, AskQuestion) and step.question.id == question.id:
            clarify = None if step.stuck else reading.clarify
            self._ask(c, render.ask_question(step, clarify, pick=step.stuck), stuck=step.stuck)
        else:
            await self._show(c, step)

    async def _on_name(self, c: Ctx, text: str) -> None:
        name = parse_full_name(text)
        if name is None:
            c.say(texts.ASK_NAME_AGAIN)
            return
        c.s.draft_name = list(name)
        self._ask_birth(c)

    def _ask_birth(self, c: Ctx) -> None:
        c.s.step = "birth"
        self._screen(c, texts.ASK_BIRTH)

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
            await self._on_payment(c, "ins")
        elif "сам" in lowered or "налич" in lowered or "карт" in lowered:
            await self._on_payment(c, "self")
        else:
            c.s.step = "idle"
            await self._on_free_text(c, text)

    # --- buttons -----------------------------------------------------------------------

    async def _on_button(self, c: Ctx, data: str) -> None:
        head, _, rest = data.partition(":")
        handler = {
            "m": self._on_menu,
            "w": self._on_back,
            "t": self._on_topic,
            "a": self._on_audience,
            "ca": self._on_child_confirm,
            "q": self._on_question_button,
            "d": self._on_day,
            "pr": self._on_period,
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
            c.s.wizard = True
            self._complaint_screen(c)
        elif item == "ask":
            self._reset(c)
            c.s.step = "info"
            c.say(texts.ASK_INFO)
        elif item == "my":
            await self._my_bookings(c)
        elif item == "admin":  # keep the scenario answers: the administrator sees the context
            await self._handoff(c, texts.ASK_ADMIN_REASON)
        else:  # «В главное меню»
            await self._show_menu(c)

    async def _on_topic(self, c: Ctx, value: str) -> None:
        if value == "list":  # «Выбрать из списка»: the area's topics, or the five groups
            c.s.wizard = True
            self._menu_screen(c, topics.start_menu(self.kb, c.s.area))
            return
        if value in self.kb.scenarios:  # a button from an older message
            await self._begin(c, value, {}, None)
            return
        item = self.kb.topics.item(value)
        if item is None:
            c.say(texts.STALE)
            return
        c.s.wizard = True
        if item.area is not None:
            c.s.area = item.area
        if item.menu is not None:
            self._menu_screen(c, item.menu)
        elif item.scenario is not None:
            await self._begin(c, item.scenario, dict(item.facts), None)
        elif item.action == "describe":
            c.s.step = "describe"
            self._screen(c, texts.DESCRIBE)
        else:
            await self._handoff(c, texts.ASK_ADMIN_REASON)

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
        question_id, _, index = value.rpartition(":")
        if question_id and question_id != dialog.question_id:  # a button of an earlier question
            c.say(texts.STALE)
            return
        buttons = self.kb.scenario(dialog.scenario_id).question(dialog.question_id).buttons()
        if not index.isdigit() or int(index) >= len(buttons):
            c.say(texts.STALE)
            return
        await self._show(c, self.engine.answer(dialog, buttons[int(index)].facts))

    async def _on_day(self, c: Ctx, value: str) -> None:
        if c.s.step not in ("days", "periods", "times"):
            c.say(texts.STALE)
            return
        kind, _, ref = value.partition(":")
        if kind == "from":  # «‹ Раньше» / «Позже ›»
            c.s.day_from = _parse_day(ref)
            await self._show_days(c)
        elif kind == "other":  # «Другой день» from the time grid
            start = c.s.anchor - timedelta(days=CONTROL_DAYS_BEFORE) if c.s.anchor else None
            c.s.day_from = start
            await self._show_days(c)
        elif (day := _parse_day(kind)) is not None:
            c.s.day, c.s.period = day, None
            await self._show_periods(c)
        else:
            c.say(texts.STALE)

    async def _on_period(self, c: Ctx, value: str) -> None:
        if c.s.step != "periods" or value not in slots_view.PERIOD_IDS:
            c.say(texts.STALE)
            return
        c.s.period = value
        await self._show_times(c)

    async def _on_slot(self, c: Ctx, value: str) -> None:
        """A time from the grid, or «Ближайшее: …» from the day or period screen."""
        if c.s.step not in ("days", "periods", "times"):
            c.say(texts.STALE)
            return
        slot = await self.schedule.get(value)
        free = slot is not None and slot in await self.schedule.free_slots(
            slot.start, slot.start + timedelta(minutes=1)
        )
        if slot is None or not free:
            c.say(texts.SLOT_TAKEN)
            await self._render_screen(c)
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
            reason = c.s.handoff_reason or texts.ASK_ADMIN_REASON
            await self._handoff(c, reason, c.s.handoff_context, c.s.handoff_why)
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
            await self._handoff(c, c.s.handoff_reason, c.s.handoff_context, c.s.handoff_why)
        else:
            await self._show_menu(c)

    # --- scenario steps ----------------------------------------------------------------

    async def _begin(
        self, c: Ctx, scenario_id: str, facts: dict[str, FactValue], child_age: int | None
    ) -> None:
        """Start a scenario of the wizard; the body area may already answer a question."""
        area = c.s.area
        self._reset(c)
        c.s.wizard, c.s.area = True, area
        facts = {**topics.area_facts(self.kb, scenario_id, area), **facts}
        if child_age is not None:
            c.s.step = "child_confirm"
            c.s.pending_scenario, c.s.pending_facts, c.s.child_age = scenario_id, facts, child_age
            self._child_confirm(c)
            return
        await self._show(c, self.engine.start(c.s.dialog, scenario_id, facts))

    def _child_confirm(self, c: Ctx) -> None:
        age = format_age(c.s.child_age or 0)
        self._screen(
            c,
            texts.CHILD_CONFIRM.format(age=age),
            [Button(texts.CHILD_YES.format(age=age), "ca:yes"), Button(texts.CHILD_NO, "ca:no")],
        )

    async def _show(self, c: Ctx, step: Step) -> None:
        if isinstance(step, AskPatient):
            c.s.step = "audience" if step.need == "audience" else "age"
            prompt = render.ask_patient(step)
            buttons = [Button(ch.label, f"a:{ch.facts['audience']}") for ch in prompt.choices]
            self._screen(c, prompt.text, buttons)
        elif isinstance(step, AskQuestion):
            self._ask(c, render.ask_question(step, pick=step.stuck), stuck=step.stuck)
        else:
            await self._done(c, step)

    def _ask(self, c: Ctx, prompt: render.Prompt, *, stuck: bool = False) -> None:
        """A scenario question; ``stuck`` (three unclear answers) adds «В главное меню»."""
        c.s.step = "question"
        qid = c.s.dialog.question_id
        buttons = [Button(ch.label, f"q:{qid}:{i}") for i, ch in enumerate(prompt.choices)]
        self._screen(c, prompt.text, buttons, rows=[_home()] if stuck else [])

    def _ask_admin_buttons(self) -> list[list[Button]]:
        return [[Button(texts.ASK_ADMIN_QUESTION, "h:yes", "primary")], _home()]

    async def _done(self, c: Ctx, done: Done) -> None:
        if done.outcome_id == FALLBACK_OUTCOME:  # a knowledge-base gap: no automatic handoff
            c.s.step = "idle"
            c.s.handoff_reason = done.outcome.handoff_reason
            self._final(c, texts.FLOW_BROKEN, [[Button(texts.MENU_ADMIN, "m:admin")], _home()])
            return
        local = self._local(c.now)
        text = render.outcome_message(done, self.clinic, local)
        if done.kind == "book":
            if text:  # script phrases before the time stay in the chat
                c.say(text, edit=c.first())
            c.s.day = c.s.day_from = None
            await self._offer_time(c)
        elif done.kind == "handoff":
            if text:
                c.say(text, edit=c.first())
            reason = done.outcome.handoff_reason or "Нужна помощь администратора"
            await self._handoff(c, reason, why=done.outcome.patient_reason)
        elif done.offer_admin:
            c.s.step = "offer_admin"
            c.s.handoff_reason = done.outcome.handoff_reason
            c.s.handoff_why = done.outcome.patient_reason
            self._final(
                c,
                text,
                [
                    [Button(texts.HANDOFF_YES, "h:yes", "primary")],
                    [Button(texts.HANDOFF_NO, "h:no")],
                ],
            )
        else:
            c.s.step = "idle"
            self._final(c, f"{text}\n\n{self.kb.common.more_help.text}", _menu())

    async def _repeat_buttons(self, c: Ctx) -> None:
        if c.s.step == "offer_admin":
            c.say(
                texts.OFFER_HANDOFF,
                [
                    [Button(texts.HANDOFF_YES, "h:yes", "primary")],
                    [Button(texts.HANDOFF_NO, "h:no")],
                ],
            )
        else:
            await self._render_screen(c)

    # --- day and time ------------------------------------------------------------------

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

    async def _free(self, c: Ctx) -> tuple[BookingSpec, list[Slot]] | None:
        found = await self._booking_spec(c)
        if found is None:
            return None
        spec, _ = found
        slots = await all_slots(self.schedule, spec, now=c.now, config=self.clinic.schedule)
        return spec, slots

    def _no_slots(self, c: Ctx) -> None:
        c.s.step = "idle"
        self._final(c, texts.NO_SLOTS, [[Button(texts.MENU_ADMIN, "m:admin")], _home()])

    async def _offer_time(self, c: Ctx) -> None:
        """First a day; a control visit opens straight on the time around +10 days."""
        if c.s.anchor is not None:
            free = await self._free(c)
            days = slots_view.days_with_slots(free[1], self.settings.tz) if free else []
            day = slots_view.nearest_day(days, c.s.anchor)
            if day is not None:
                c.s.day, c.s.period = day, None
                await self._show_periods(c)
                return
        await self._show_days(c)

    def _nearest(self, c: Ctx, slots: Sequence[Slot]) -> list[list[Button]]:
        """«Ближайшее: сегодня, 14:30» — the first time by the 04-09 rules, one tap."""
        if not slots:
            return []
        label = slots_view.nearest_label(slots[0], self._local(c.now).date(), self.settings.tz)
        return [[Button(label, f"s:{slots[0].id}", "primary")]]

    async def _show_days(self, c: Ctx) -> None:
        free = await self._free(c)
        if free is None:
            await self._show_menu(c)
            return
        days = slots_view.days_with_slots(free[1], self.settings.tz)
        if not days:
            self._no_slots(c)
            return
        c.s.step = "days"
        page = slots_view.page_of_days(days, c.s.day_from)
        today = self._local(c.now).date()
        buttons = [Button(render.day_label(d, today), f"d:{d:%Y%m%d}") for d in page]
        nav = []
        if (earlier := slots_view.earlier_page_start(days, page)) is not None:
            nav.append(Button(texts.EARLIER, f"d:from:{earlier:%Y%m%d}"))
        if (later := slots_view.later_page_start(days, page)) is not None:
            nav.append(Button(texts.LATER, f"d:from:{later:%Y%m%d}"))
        rows = [nav] if nav else []
        rows.append([Button(texts.MENU_ADMIN, "m:admin")])  # no convenient day: a person helps
        top = self._nearest(c, free[1])
        self._screen(c, texts.DAY_PICK, buttons, top=top, rows=rows, columns=DAY_COLUMNS)

    async def _day_times(self, c: Ctx) -> tuple[BookingSpec, list[Slot], list[Slot]] | None:
        """(rules, all free slots, the day's times one per start) for the chosen day."""
        free = await self._free(c)
        if free is None or c.s.day is None:
            return None
        spec, slots = free
        windows = self.clinic.schedule.repeat_windows
        shown = slots_view.times_of_day(slots, c.s.day, spec.slot_policy, windows, self.settings.tz)
        return spec, slots, shown

    def _day_header(self, c: Ctx, shown: list[Slot], slots: list[Slot]) -> list[str]:
        tz = self.settings.tz
        assert c.s.day is not None
        day_slots = [s for s in slots if slots_view.local(s, tz).date() == c.s.day]
        doctors = slots_view.doctors_text(shown, day_slots, self.clinic.schedule, tz)
        lines = [render.format_date(c.s.day).capitalize(), doctors]
        if c.s.anchor is not None:
            lines.insert(0, texts.CONTROL_NEAR.format(date=render.format_date(c.s.anchor)))
        return lines

    async def _show_periods(self, c: Ctx) -> None:
        """«Утро 8–12» / «День 12–18» / «Вечер 18–23» / «Ночь 23–8» — only those with time."""
        found = await self._day_times(c)
        if found is None:
            await self._show_menu(c)
            return
        _, slots, shown = found
        if not shown:
            c.say(texts.DAY_GONE)
            c.s.day = None
            await self._show_days(c)
            return
        periods = slots_view.periods_with(shown, self.settings.tz)
        if len(periods) == 1:  # one part of the day: straight to its times
            c.s.period = periods[0].id
            await self._show_times(c)
            return
        c.s.step, c.s.period = "periods", None
        lines = [*self._day_header(c, shown, slots), "", texts.PERIOD_PICK]
        buttons = [Button(p.label, f"pr:{p.id}") for p in periods]
        rows = [[Button(texts.OTHER_DAY, "d:other")]] if c.s.anchor is not None else []
        # «Ближайшее» belongs to the first screen of the choice: the day picker, or here for a
        # control visit (no day picker; the 04-09 rules leave no night times)
        top = self._nearest(c, [s for s in slots if s in shown]) if c.s.anchor else []
        self._screen(c, "\n".join(lines), buttons, top=top, rows=rows)

    async def _show_times(self, c: Ctx) -> None:
        found = await self._day_times(c)
        if found is None:
            await self._show_menu(c)
            return
        _, slots, day_times = found
        tz = self.settings.tz
        shown = slots_view.in_period(day_times, c.s.period, tz)
        if not shown:  # the part of the day filled up meanwhile
            c.say(texts.DAY_GONE if not day_times else texts.SLOT_TAKEN)
            await (self._show_periods(c) if day_times else self._show_days(c))
            return
        c.s.step = "times"
        lines = [*self._day_header(c, shown, slots), "", texts.TIME_PICK]
        buttons = [Button(f"{slots_view.local(s, tz):%H:%M}", f"s:{s.id}") for s in shown]
        rows = [[Button(texts.OTHER_DAY, "d:other")]] if c.s.anchor is not None else []
        self._screen(c, "\n".join(lines), buttons, rows=rows, columns=TIME_COLUMNS)

    # --- identification and booking ----------------------------------------------------

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
        buttons = [Button(texts.CONSENT_YES, "c:yes", "success"), Button(texts.CONSENT_NO, "c:no")]
        self._screen(c, text, buttons)

    def _ask_name(self, c: Ctx) -> None:
        c.s.step = "name"
        self._screen(c, f"{self.kb.common.identify.text}\n{texts.ASK_NAME}")

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
            buttons = [
                Button(f"{p.short_name}, {p.birth_date:%d.%m.%Y}", f"p:{p.id}") for p in saved
            ]
            buttons.append(Button(texts.PATIENT_NEW, "p:new"))
            self._screen(c, texts.PATIENT_PICK, buttons)
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
            await self._show_times(c)
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
        card = render.booking_card(
            self.kb,
            done,
            slot,
            first_name=patient.first_name,
            patronymic=patient.patronymic,
            audience=patient.audience,  # type: ignore[arg-type]
            clinic=self.clinic,
            demo=self.settings.is_demo_booking,
        )
        actions = [
            [
                Button(texts.MOVE, f"b:move:{booking.id}"),
                Button(texts.CANCEL, f"b:cancel:{booking.id}", "danger"),
            ]
        ]
        c.say(card.plain(), actions, card=card)
        if (place := render.venue(self.clinic)) is not None:
            c.say(place.address, venue=place)
        self._reset(c)
        c.s.step, c.s.booking_id = "payment", booking.id
        c.say(
            render.payment_question(done),
            [
                [
                    Button(texts.PAYMENT_SELF, "pay:self"),
                    Button(texts.PAYMENT_INSURANCE, "pay:ins"),
                ]
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
                [
                    [
                        Button(texts.MOVE, f"b:move:{b.id}"),
                        Button(texts.CANCEL, f"b:cancel:{b.id}", "danger"),
                    ]
                ],
            )

    async def _on_booking_action(self, c: Ctx, value: str) -> None:
        action, _, booking_ref = value.partition(":")
        booking = await self._user_booking(c, booking_ref)
        if booking is None or booking.start_at <= c.now:
            c.say(texts.BOOKING_GONE, _menu())
            return
        start = self._local(booking.start_at)
        when = {"date": render.format_date(start.date()), "time": f"{start:%H:%M}"}
        if action == "move":  # the card or the reminder stays; the day picker comes below
            self._reset(c)
            c.s.reschedule_of = booking.id
            c.say(texts.MOVE_PICK)
            await self._show_days(c)
        elif action == "cancel":
            c.say(
                texts.CANCEL_ASK.format(**when),
                [
                    [Button(texts.CANCEL_YES, f"b:drop:{booking.id}", "danger")],
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
            await self._show_times(c)
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
            (Button(texts.CONFIRM, f"r:ok:{b.id}", "success"),),
            (
                Button(texts.MOVE, f"b:move:{b.id}"),
                Button(texts.CANCEL, f"b:cancel:{b.id}", "danger"),
            ),
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
            await self._on_free_text(c, text)
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
                [Button(texts.CONTROL_YES, f"k:yes:{booking.id}", "primary")],
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
        """A question about the trauma centre, from kb/info; an administrator only by button."""
        known = await self._known(c)
        reading = await read_question(self.assistant, text, known, on_partial=c.draft)
        self._reset(c)
        if reading.answer and not reading.offer_admin:
            c.say(f"{reading.answer}\n\n{self.kb.common.more_help.text}", _menu())
            return
        c.s.step = "offer_admin"
        c.s.handoff_reason, c.s.handoff_context = texts.QUESTION_REASON, text
        if reading.answer:
            c.say(reading.answer, self._ask_admin_buttons())
        else:
            lead = texts.INFO_DOWN if reading.unavailable else texts.INFO_UNKNOWN
            c.say(lead, self._ask_admin_buttons())

    async def _handoff(
        self, c: Ctx, reason: str, context: str | None = None, why: str | None = None
    ) -> None:
        """Pass the request to ADMIN_CHAT_ID; consent and a phone number come first.

        Only after a button («Позвать администратора», «Передать вопрос администратору») or
        a scenario outcome. One open request per user — new ones are added to it; at most
        HANDOFF_DAILY_LIMIT (3) requests a day, then the clinic's phone.
        """
        c.s.handoff_reason, c.s.handoff_context, c.s.handoff_why = reason, context, why
        if not self.settings.admin_chat_id:
            log.error("ADMIN_CHAT_ID is not set: cannot hand off %r", reason)
            c.s.step = "idle"
            c.say(texts.ADMIN_UNAVAILABLE, _menu())
            return
        current = await self._open_handoff(c)
        if current is not None:
            await self._add_to_handoff(c, current)
            return
        sent = await self._handoffs_today(c)
        if sent >= self.settings.handoff_daily_limit:
            text = texts.HANDOFF_LIMIT.format(count=sent)
            if self.clinic.contacts.call_center_phone:
                text += texts.HANDOFF_LIMIT_PHONE.format(
                    phone=self.clinic.contacts.call_center_phone
                )
            self._handoff_done(c)
            c.say(f"{text}\n\n{self.kb.common.more_help.text}", _menu())
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

    async def _open_handoff(self, c: Ctx) -> Handoff | None:
        return await c.db.scalar(
            select(Handoff)
            .where(
                Handoff.user_id == c.user.id,
                Handoff.status == "open",
                Handoff.updated_at >= c.now - HANDOFF_IDLE,
            )
            .order_by(Handoff.id.desc())
            .limit(1)
        )

    async def _handoffs_today(self, c: Ctx) -> int:
        count = await c.db.scalar(
            select(func.count(Handoff.id)).where(
                Handoff.user_id == c.user.id,
                or_(Handoff.kind.is_(None), Handoff.kind != "crisis"),
                Handoff.created_at >= c.now - timedelta(days=1),
            )
        )
        return int(count or 0)

    async def _first_admin_message(self, c: Ctx, handoff_id: int) -> int | None:
        return await c.db.scalar(
            select(AdminMessage.message_id)
            .where(AdminMessage.handoff_id == handoff_id)
            .order_by(AdminMessage.message_id)
            .limit(1)
        )

    def _handoff_note(self, c: Ctx) -> str:
        return f"{c.s.handoff_reason or texts.ASK_ADMIN_REASON}|{c.s.handoff_context or ''}"[:512]

    async def _add_to_handoff(self, c: Ctx, handoff: Handoff) -> None:
        """The request is already with the administrators: add what is new, as a reply."""
        now, why = self._local(c.now), c.s.handoff_why
        note = self._handoff_note(c)
        if note in (handoff.last_note, f"{handoff.reason}|"):
            template = texts.HANDOFF_ALREADY
        else:
            handoff.last_note, handoff.updated_at = note, c.now
            thread = await self._first_admin_message(c, handoff.id)
            body = await self._admin_text(c, handoff, addendum=True)
            c.turn.admin.append(AdminPost(body, handoff.id, reply_to=thread))
            template = texts.HANDOFF_ADDED
        text = render.handoff_message(self.clinic, now, handoff.id, why, template)
        self._handoff_done(c)
        c.say(f"{text}\n\n{self.kb.common.more_help.text}", _menu())

    def _handoff_done(self, c: Ctx) -> None:
        c.s.step = "idle"
        c.s.handoff_reason = c.s.handoff_context = c.s.handoff_why = None

    async def _send_handoff(self, c: Ctx) -> None:
        reason = c.s.handoff_reason or texts.ASK_ADMIN_REASON
        handoff = Handoff(
            user_id=c.user.id,
            reason=reason[:512],
            kind="request",
            status="open",
            created_at=c.now,
            updated_at=c.now,
            last_note=self._handoff_note(c),
        )
        c.db.add(handoff)
        await c.db.flush()
        close = ((Button(texts.ADMIN_CLOSE, f"close:{handoff.id}", style="success"),),)
        c.turn.admin.append(
            AdminPost(await self._admin_text(c, handoff), handoff.id, buttons=close)
        )
        why = c.s.handoff_why
        text = render.handoff_message(self.clinic, self._local(c.now), handoff.id, why)
        self._handoff_done(c)
        c.say(f"{text}\n\n{self.kb.common.more_help.text}", _menu(), remove_keyboard=False)

    async def _admin_text(self, c: Ctx, handoff: Handoff, *, addendum: bool = False) -> str:
        reason = c.s.handoff_reason or handoff.reason
        if addendum:
            lines = [texts.ADMIN_ADDENDUM.format(number=handoff.id), f"Причина: {reason}"]
        else:
            lines = [f"Обращение № {handoff.id} из бота травмпункта", f"Причина: {reason}"]
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
        if addendum:  # contacts are in the first message of the thread
            return "\n".join(lines)
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
            await self._on_free_text(c, text)
            return
        if handoff := await c.db.get(Handoff, handoff_id):
            handoff.updated_at = c.now
        c.turn.admin.append(
            AdminPost(f"Пациент (обращение № {handoff_id}): {text}", handoff_id, reply_to=last)
        )
        c.say(texts.ADMIN_REPLY_SENT, _menu())

    # --- /forget: withdraw consent ---------------------------------------------------------

    async def _forget_ask(self, c: Ctx) -> None:
        self._reset(c)
        c.say(
            texts.FORGET_ASK,
            [[Button(texts.FORGET_YES, "fg:yes", "danger")], [Button(texts.FORGET_NO, "fg:no")]],
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
