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
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import misses, render, slots_view, texts, typed
from app.bot.layout import DAY_COLUMNS, TIME_COLUMNS, grid, pack
from app.bot.render import Card, Venue
from app.config import PROJECT_ROOT, ClinicData, Settings
from app.db.models import (
    AdminMessage,
    AnswerRating,
    Booking,
    Handoff,
    Patient,
    Reminder,
    User,
    utcnow,
)
from app.db.session import Database
from app.flow import topics
from app.flow.age import find_child_age, parse_age_months
from app.flow.commands import (
    after_me,
    asks_advice,
    asks_when_admin,
    find_audience,
    find_command,
    find_navigation,
    find_small_talk,
    gives_consent,
    is_booking_order,
    is_question,
    is_unsure,
    names_several,
    refuses_new_booking,
    wants_all,
    wants_cancel,
    wants_handoff,
    wants_move,
    wants_nearest,
    wants_other_day,
    wants_other_time,
    yes_no,
)
from app.flow.engine import (
    ADULT_FROM_MONTHS,
    FALLBACK_OUTCOME,
    INFANT_UNDER_MONTHS,
    AskPatient,
    AskQuestion,
    DialogState,
    Done,
    Engine,
    Place,
    Step,
)
from app.flow.identity import (
    age_in_months,
    find_full_name,
    find_phone,
    normalize_phone,
    parse_birth_date,
)
from app.flow.loader import KnowledgeBase, care_lines, doctor_posts
from app.flow.safety import find_phrase
from app.flow.schema import Booking as BookingSpec
from app.flow.schema import Choice, FactValue, Question, Scenario
from app.flow.wishes import (
    Parsed,
    Wish,
    dative,
    days_named,
    mark_doctors,
    names_doctors,
    short_name,
)
from app.flow.wishes import parse as parse_wish
from app.flow.words import days as days_word
from app.flow.words import format_age
from app.flow.words import years as years_word
from app.llm.assistant import Assistant
from app.llm.masking import KnownPerson, mask
from app.llm.reader import AnswerReading, TextReading, read_answer, read_question, read_text
from app.notify.planner import AFTER_VISIT, DAY_BEFORE, TWO_HOURS, plan
from app.schedule.base import ScheduleProvider, Slot, SlotUnavailable
from app.schedule.rules import all_slots, eligible

log = logging.getLogger(__name__)

HANDOFF_IDLE = timedelta(days=1)  # an open request with no activity for a day is closed
CRISIS_THREAD = timedelta(minutes=30)  # later crisis messages go under the same note
MAX_HISTORY = 12  # «Назад» steps kept per user
CONTROL_DAYS_BEFORE = 3  # «Другой день» for a control visit starts a little before the target
WISH_TTL = timedelta(hours=3)  # outside a booking: a wish named long ago is not carried on
UNFINISHED_WINDOW = timedelta(minutes=45)  # later than this the reminder is not sent at all
EXAMPLE_ANKLE = ("ligament_rupture", "dislocation", "fracture")  # «Подвернул ногу»
EXAMPLE_DOCTOR = 1  # the greeting's example names the second doctor of config/schedule.yaml
FEW_TIMES = 8  # a day with this many free times or fewer shows them at once, no part of the day
FREE_TIMES_SHOWN = 8  # «есть время вечером?» — the first times, then «и позже»
DOCTOR_DAYS_SHOWN = 6  # «когда работает Ярунин?» — the nearest days
LABEL_MAX = 40  # a button's label, so that it is not cut on a phone
TOPIC_DEPTH = 3  # how many topic menus one typed word may go through
SAME_COMPLAINT = timedelta(minutes=30)  # the same complaint this soon is not a new one
FEW_WORDS = 3  # «да, передайте», «для сына»: a longer message says something more

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
class Media:
    """An animation or photo from assets/; if the file is missing, the text goes alone."""

    kind: Literal["animation", "photo"]
    name: str  # file in assets/, e.g. «welcome.mp4»


WELCOME = Media("animation", "welcome.mp4")
BOOKED = Media("animation", "booked.mp4")
REMINDER = Media("animation", "reminder.mp4")
FEEDBACK = Media("photo", "feedback.png")
ENTRANCE = Media("photo", "entrance.jpg")


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
    media: Media | None = None  # ``text`` becomes the caption (MEDIA_ENABLED)
    media_only: bool = False  # nothing at all if the media cannot be sent
    pin: int | None = None  # booking id: pin this message silently and remember it


@dataclass(frozen=True)
class AdminPost:
    text: str
    handoff_id: int | None = None  # set: an administrator's reply goes back to the patient
    reply_to: int | None = None  # admin chat message to reply to (a thread)
    buttons: tuple[tuple[Button, ...], ...] = ()
    booking_id: int | None = None  # a booking card: its message id is remembered
    edit_message: int | None = None  # replace this admin chat message instead of sending


@dataclass
class Turn:
    replies: list[Reply] = field(default_factory=list)
    admin: list[AdminPost] = field(default_factory=list)
    reaction: str | None = None  # an emoji on the pressed message (👍 confirmed, ❤ rated 5)
    unpin: list[int] = field(default_factory=list)  # messages to unpin in the patient's chat


StepName = Literal[
    "idle",
    "complaint",
    "topic",
    "describe",
    "child_confirm",
    "audience",
    "whom",
    "age",
    "question",
    "days",
    "periods",
    "times",
    "second",
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
    "feedback_text",
]

# Wizard screens: «Назад» works between them. Stage numbers for «Шаг N из 5».
STAGE_OF: dict[str, int] = {
    "complaint": 1,
    "topic": 1,
    "describe": 1,
    "child_confirm": 2,
    "audience": 2,
    "whom": 2,  # «Записать ещё одного»: «для кого» before «что случилось»
    "age": 2,
    "question": 3,
    "days": 4,
    "periods": 4,
    "times": 4,
    "second": 4,
    "consent": 5,
    "patient": 5,
    "name": 5,
    "birth": 5,
    "contact": 5,
}
WIZARD_STEPS = frozenset(STAGE_OF)
TIME_STEPS = frozenset({"days", "periods", "times"})
START_STEPS = frozenset({"complaint", "topic", "describe"})  # the scenario is not chosen yet
# Steps where a text is about anything: «сегодня вечером» there may be a wish. On the other
# steps the text answers a question («Когда была получена травма?» — «в пятницу»).
FREE_STEPS = frozenset({"idle", *START_STEPS, "info", "offer_admin", "payment"})
WISH_SHOWN_UNTIL = 4  # the wish line stays on screen up to «День и время»
# The text is an age, a name, a birth date, a phone number — unless it is a command or a
# question: «позови администратора» on the name step is not the patient's name.
INPUT_STEPS = frozenset({"age", "name", "birth", "contact"})
# The text goes to an administrator as it is: «отмените мою запись» there is the message.
MESSAGE_STEPS = frozenset({"admin_reply", "visit_question", "feedback_text"})
DATA_STEPS = INPUT_STEPS | MESSAGE_STEPS
# How a button's label typed as text is matched on a step (app/bot/typed.py).
TYPED_MODE = {**dict.fromkeys(("complaint", "topic", "describe"), "topic"), "question": "answer"}
MENU_ITEM = {"book": "book", "my_bookings": "my", "ask": "ask", "admin": "admin"}
COMMAND_ACTIONS = frozenset({*MENU_ITEM, "forget", "about_bot"})
NAV_ACTIONS = frozenset({"home", "back"})  # route: «хочу на стартовый экран», «верни вопрос»
# Steps answered by a button: a text there is read by route before the bot gives a hint.
BUTTON_STEPS = frozenset({*TIME_STEPS, "second", "consent", "patient", "child_confirm"})


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
    name_asks: int = 0  # full names in a row that could not be read
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
    wish: Wish | None = None  # the doctor, the day and the time the patient asked for
    # «запишите ребёнка», «у мамы перелом», «меня укусила собака»: not asked again
    for_whom: Literal["adult", "child", "self"] | None = None
    hint: list[str] = []  # scenarios the word list found in a medical question («Записаться»)
    notices_said: bool = False  # the script notice was said with «что делать?»: not twice
    paused: dict[str, Any] | None = None  # the booking left for the menu («Продолжить запись»)
    paused_at: datetime | None = None
    cancel_ids: list[int] = []  # the bookings «Отменить все записи?» was asked about
    second_of: int | None = None  # the booking ahead the patient already has (a first visit)
    second_ok: bool = False  # «Записать ещё раз»: the question is not asked again
    nudged: bool = False  # the reminder about this unfinished booking was sent
    more: bool = False  # two patients were named: the second is offered after the card
    ended_at: datetime | None = None  # a route or a handoff was shown (not a booking)
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
    for_whom: str | None = None  # «запишите ребёнка», «у мамы перелом» in this message
    had_history: bool = False  # «Назад» had somewhere to go when the turn began
    came_from: tuple[object, ...] | None = None  # the wizard screen the turn began on
    notices_said: bool = False  # this turn has said the scenario's notice already
    limited: bool = False  # too many DeepSeek calls a minute: this turn went without it
    more: bool = False  # this message names more than one patient

    def say(self, text: str, buttons: Sequence[Sequence[Button]] | None = None, **kw: Any) -> None:
        rows = tuple(tuple(row) for row in (buttons or []))
        self.turn.replies.append(Reply(text, rows, **kw))

    def first(self) -> bool:
        """Nothing sent yet in this turn: the reply may replace the pressed message."""
        return self.via_button and not self.turn.replies


def _answer_choice(question: Question, facts: dict[str, FactValue]) -> Choice | None:
    """The button that matches the given answer."""
    for choice in question.buttons():
        if all(facts.get(k) == v for k, v in choice.facts.items()):
            return choice
    return None


def _answer_label(question: Question, facts: dict[str, FactValue]) -> str | None:
    """The label of that button, for the administrators' summary."""
    choice = _answer_choice(question, facts)
    return choice.label if choice else None


def _home() -> list[Button]:
    return [Button(texts.MENU_HOME, "m:home")]


def _same_screen(reply: Reply, shown: Reply) -> bool:
    return (reply.text, reply.buttons) == (shown.text, shown.buttons)


def _menu() -> list[list[Button]]:
    icons = texts.MENU_ICONS
    return [
        [Button(f"{icons['book']} {texts.MENU_BOOK}", "m:book", style="primary")],
        [Button(f"{icons['ask']} {texts.MENU_ASK}", "m:ask")],
        [Button(f"{icons['my']} {texts.MENU_MY}", "m:my")],
        [Button(f"{icons['admin']} {texts.MENU_ADMIN}", "m:admin")],
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
        self.posts = doctor_posts(clinic.schedule.doctors)  # «врач-травматолог-ортопед»
        self.care = care_lines()  # scenario → what the trauma centre does (kb/info/uslugi.md)
        path = settings.misses_log
        if path is not None and not path.is_absolute():
            path = PROJECT_ROOT / path
        self.misses = misses.MissLog(path)  # fallback answers, for /misses
        self._llm_calls: defaultdict[int, deque[datetime]] = defaultdict(deque)
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    # --- entry points ------------------------------------------------------------------

    async def start(self, user: UserRef) -> Turn:
        return await self._run(user, self._greet)

    async def text(self, user: UserRef, text: str, draft: DraftSink | None = None) -> Turn:
        said = text.strip()
        return await self._run(user, lambda c: self._on_text(c, said), draft=draft, said=said)

    async def button(self, user: UserRef, data: str) -> Turn:
        return await self._run(user, lambda c: self._on_button(c, data), via_button=True)

    async def contact(self, user: UserRef, phone: str, owner_id: int | None) -> Turn:
        return await self._run(user, lambda c: self._on_contact(c, phone, owner_id))

    async def other(self, user: UserRef) -> Turn:
        """A photo, a voice message, a sticker: the bot reads only text and buttons."""
        return await self._run(user, self._on_other, said="")

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
                card = await db.scalar(
                    select(Booking.id).where(Booking.admin_message_id == message_id)
                )
                return texts.ADMIN_CARD_NOTE if card is not None else None
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

    async def register_pinned_card(self, booking_id: int, message_id: int) -> None:
        async with self.db() as db:
            booking = await db.get(Booking, booking_id)
            if booking is not None:
                booking.chat_message_id = message_id
                await db.commit()

    async def register_booking_card(self, booking_id: int, message_id: int) -> None:
        async with self.db() as db:
            booking = await db.get(Booking, booking_id)
            if booking is not None:
                booking.admin_message_id = message_id
                await db.commit()

    async def admin_today(self) -> str:
        """/today in the admin chat: active bookings for today and tomorrow (clinic time)."""
        if not self.settings.bookings_to_admin_chat:
            return texts.TODAY_OFF
        now = self._local(self.clock())
        start = datetime.combine(now.date(), datetime.min.time(), tzinfo=self.settings.tz)
        days = f"{render.format_date(now.date())} и {render.format_date(now.date() + timedelta(1))}"
        async with self.db() as db:
            rows = (
                await db.scalars(
                    select(Booking)
                    .where(
                        Booking.status == "active",
                        Booking.start_at >= start,
                        Booking.start_at < start + timedelta(days=2),
                    )
                    .order_by(Booking.start_at)
                )
            ).all()
            if not rows:
                return texts.TODAY_EMPTY.format(days=days)
            lines = [texts.TODAY_TITLE.format(days=days)]
            shown_day = None
            for b in rows:
                at = self._local(b.start_at)
                if at.date() != shown_day:
                    shown_day = at.date()
                    lines.append(texts.TODAY_DAY.format(date=render.format_date(shown_day)))
                patient = await db.get(Patient, b.patient_id)
                lines.append(
                    texts.TODAY_ITEM.format(
                        time=f"{at:%H:%M}",
                        number=b.id,
                        patient=self._patient_line(patient, now.date()) if patient else "—",
                        topic=self._topic(b),
                        visit=texts.VISIT_KIND.get(b.visit, b.visit),
                        doctor=b.doctor,
                    )
                )
            return "\n".join(lines)

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

    async def unfinished(self) -> list[Turn]:
        """Patients who started a booking and went silent: one reminder each, not at night.

        Called by the scheduler. The reminder goes 15 minutes after the last message
        (config/clinic.yaml); if that moment falls into the quiet hours it is not sent at
        all — a message in the morning about yesterday's booking would be out of place.
        """
        now = self.clock()
        rule = self.clinic.clinic.unfinished_booking
        if rule.quiet_hours.contains(self._local(now).time()):
            return []
        latest = now - timedelta(minutes=rule.remind_after_minutes)
        async with self.db() as db:
            ids = await db.scalars(
                select(User.id).where(
                    User.active_at <= latest, User.active_at > latest - UNFINISHED_WINDOW
                )
            )
            waiting = ids.all()
        buttons = (
            (Button(texts.UNFINISHED_GO, "nd:go", "primary"),),
            (Button(texts.UNFINISHED_AGAIN, "m:book"),),
        )
        turns = []
        for user_id in waiting:
            async with self._locks[user_id], self.db() as db:
                user = await db.get(User, user_id)
                if user is None or user.active_at is None or user.active_at > latest:
                    continue  # the patient wrote meanwhile
                s = Session.model_validate(user.session or {})
                booking = s.wizard and s.step in WIZARD_STEPS and s.resume != "handoff"
                if not booking or s.nudged:
                    continue
                s.nudged = True
                user.session = s.model_dump(mode="json")
                await db.commit()
                turns.append(Turn([Reply(texts.UNFINISHED, buttons, chat_id=user.chat_id)]))
        return turns

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
        said: str | None = None,
    ) -> Turn:
        async with self._locks[user.id], self.db() as db:
            row = await db.get(User, user.id)
            if row is None:
                row = User(id=user.id, chat_id=user.chat_id, session={})
                db.add(row)
            row.chat_id, row.username = user.chat_id, user.username
            row.display_name = user.display_name
            row.active_at = self.clock()
            session = Session.model_validate(row.session or {})
            ctx = Ctx(db, row, session, Turn(), self.clock(), via_button=via_button, draft=draft)
            before = session.model_dump(mode="json")
            before_key = _screen_key(session)
            step = session.step
            ctx.had_history = bool(session.history)
            ctx.came_from = before_key if session.step in WIZARD_STEPS else None
            await handler(ctx)
            if ctx.more and not before["more"] and ctx.s.wizard and ctx.s.step in WIZARD_STEPS:
                ctx.s.more = True  # never chosen silently: one now, the other after the card
                ctx.turn.replies.insert(0, Reply(texts.ONE_BY_ONE))
            if ctx.limited and ctx.turn.replies:  # said once, above the buttons that work
                ctx.turn.replies.insert(0, Reply(texts.TOO_MANY_MESSAGES))
            if said is not None:  # a text or a photo answered with a fallback phrase
                await self._note_misses(ctx, step, said)
            if not ctx.deleted:
                self._remember_screen(ctx, before, before_key)
                under_way = ctx.s.wizard and ctx.s.step in WIZARD_STEPS
                if under_way or not self._paused_fresh(ctx):  # a booking is on screen, or old
                    ctx.s.paused = ctx.s.paused_at = None
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

    def _llm(self, c: Ctx) -> Assistant | None:
        """DeepSeek for one call — or None over LLM_PER_MINUTE for this user.

        Over the limit the bot works as when DeepSeek is down: buttons and word lists.
        """
        if self.assistant is None:
            return None
        calls = self._llm_calls[c.user.id]
        while calls and c.now - calls[0] >= timedelta(minutes=1):
            calls.popleft()
        if len(calls) >= self.settings.llm_per_minute:
            c.limited = True
            return None
        calls.append(c.now)
        return self.assistant

    async def _miss(self, c: Ctx, step: str, kind: str, said: str) -> None:
        """One line of the journal: the masked text, the step and the kind — no user."""
        if self.misses.path is None:
            return
        doctors = self.clinic.schedule.doctors
        text = mask(mark_doctors(said, doctors), await self._known(c), self._local(c.now).date())
        self.misses.write(misses.Miss(c.now.isoformat(timespec="seconds"), step, kind, text))

    async def _note_misses(self, c: Ctx, step: str, said: str) -> None:
        kinds = dict.fromkeys(k for r in c.turn.replies if (k := misses.kind_of(r.text)))
        for kind in kinds:
            await self._miss(c, step, kind, said)

    async def admin_misses(self) -> str:
        """/misses in the admin chat: the last fallback answers and a day of counters."""
        return self.misses.report(self.clock(), self.settings.tz)

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

    def _reset(self, c: Ctx, *, booking: bool = False) -> None:
        """A clean session; a booking left for the menu stays until it is resumed or old.

        ``booking`` — the same booking goes on: the second patient to offer after the card and
        «Записать ещё раз» stay.
        """
        more, again = booking and c.s.more, booking and c.s.second_ok
        c.s = Session(paused=c.s.paused, paused_at=c.s.paused_at, more=more, second_ok=again)

    def _paused_fresh(self, c: Ctx) -> bool:
        at = c.s.paused_at
        return c.s.paused is not None and at is not None and c.now - at <= UNFINISHED_WINDOW

    def _pause(self, c: Ctx) -> None:
        """Leaving for the menu does not erase a booking under way («Продолжить запись»)."""
        s = c.s
        booking = s.wizard and s.step in WIZARD_STEPS and s.resume != "handoff"
        empty = s.step == "complaint" and not (s.wish or s.for_whom or s.area)
        if booking and not empty:
            s.paused = s.model_dump(mode="json", exclude={"paused", "paused_at"})
            s.paused_at = c.now

    async def _resume(self, c: Ctx) -> None:
        """«Продолжить запись»: the step left for the menu, with the answers and the wish."""
        if not self._paused_fresh(c):
            c.s.paused = c.s.paused_at = None
            c.say(texts.RESUME_GONE)
            self._start_booking(c)
            return
        c.s = Session.model_validate(c.s.paused)
        c.back = True  # the «Назад» history comes back as it was
        await self._render_screen(c)

    def _main_menu(self, c: Ctx) -> list[list[Button]]:
        """The main menu; «Продолжить запись» first while a booking left for it is fresh."""
        rows = _menu()
        if self._paused_fresh(c):
            rows.insert(0, [Button(texts.RESUME, "m:resume", "primary")])
        return rows

    async def _greet(self, c: Ctx) -> None:
        self._pause(c)  # /start in the middle of a booking keeps it, as the menu does
        self._reset(c)
        common = self.kb.common
        text = common.greeting.text.format(
            part_of_day=common.greeting_for(self._local(c.now).time())
        )
        doctors = self.clinic.schedule.doctors  # the example names a real doctor
        doctor = dative(doctors[min(EXAMPLE_DOCTOR, len(doctors) - 1)], initials=False)
        text += "\n\n" + texts.GREETING_HINT.format(doctor=doctor)
        if self.settings.is_demo_booking:
            text += "\n\n" + texts.BOT_DEMO_NOTE
        first = c.user.welcomed_at is None  # the welcome animation — at the first /start only
        if first:
            c.user.welcomed_at = c.now
        examples = pack([Button(label, f"ex:{key}") for key, label in texts.EXAMPLES])
        c.say(text, [*examples, *self._main_menu(c)], media=WELCOME if first else None)

    async def _on_example(self, c: Ctx, key: str) -> None:
        """An example button under the greeting: what a typed phrase would do, without the LLM."""
        if key == "ankle":
            self._reset(c)
            self._topics_for(c, "limb", list(EXAMPLE_ANKLE), texts.ASK_COMPLAINT)
        elif key == "dressing":
            await self._begin(c, "dressing", {}, None)
        elif key == "parking":  # script 03-3, by day and after 20:00
            common = self.kb.common
            text, more = self._shorten(f"{common.parking_day.text}\n\n{common.parking_night.text}")
            self._reset(c)
            c.s.handoff_reason = texts.QUESTION_REASON
            c.s.handoff_context = dict(texts.EXAMPLES)[key]
            c.say(text, [*more, *self._answer_buttons()], edit=c.first())
        else:
            c.say(texts.STALE, [_home()])

    async def _show_menu(self, c: Ctx) -> None:
        self._pause(c)
        self._reset(c)
        c.say(self.kb.common.more_help.text, self._main_menu(c), edit=c.first())

    # --- screens -----------------------------------------------------------------------

    def _header(self, c: Ctx) -> str | None:
        """«Шаг 2 из 5 · Для кого запись» and the patient's wish, while booking."""
        stage = STAGE_OF.get(c.s.step)
        if stage is None or c.s.resume == "handoff" or c.s.step == "whom":
            return None
        lines = []
        if c.s.wizard:
            name = texts.STAGES[stage - 1]
            lines.append(texts.STEP.format(n=stage, total=len(texts.STAGES), name=name))
        if c.s.wizard and c.s.for_whom and stage == 1:
            lines.append(texts.FOR_WHOM[c.s.for_whom])
        wish = self._wish(c)
        booking = c.s.wizard or c.s.reschedule_of is not None
        if wish is not None and booking and stage <= WISH_SHOWN_UNTIL:
            lines.append(render.wish_line(wish, self._local(c.now).date()))
        return "\n".join(lines) or None

    def _wish(self, c: Ctx) -> Wish | None:
        """The wish in force: its day is not in the past.

        While a booking or a move goes on, the wish lives until the time is chosen or
        «Убрать пожелание»; outside one, a wish named long ago is dropped.
        """
        wish = c.s.wish
        if wish is None:
            return None
        booking = c.s.wizard or c.s.reschedule_of is not None
        if not booking and wish.noted is not None and c.now - wish.noted > WISH_TTL:
            wish = Wish()
        elif wish.day is not None and wish.day < self._local(c.now).date():
            wish = wish.without("day")
        c.s.wish = None if wish.empty else wish
        return c.s.wish

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
            if c.back:  # «Назад» restored this screen: what is left behind it
                past = bool(c.s.history)
            else:  # what «Назад» will have after this turn (see _remember_screen)
                past = c.had_history or c.came_from not in (None, _screen_key(c.s))
            # with nowhere to go back to there is only «Меню»; «Для кого запись?» answered by
            # the text is a screen to go back to as well
            back = past or self._audience_skipped(c)
        if c.s.reschedule_of is not None:  # a move: the way out is on every screen
            layout.append([Button(texts.KEEP_AS_IS, f"b:keep:{c.s.reschedule_of}")])
        layout.append(self._nav_row(back))
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
        elif step == "whom":
            self._whom_screen(c)
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
        elif step == "second":
            await self._second_screen(c)
        elif step == "consent":
            self._consent_prompt(c, c.s.resume or "identify")
        elif step == "patient":
            await self._identify(c)
        elif step == "name":
            self._ask_name(c)
        elif step == "birth":
            self._ask_birth(c)
        elif step == "contact":
            self._ask_contact(c)
        else:
            await self._show_menu(c)

    def _audience_skipped(self, c: Ctx) -> bool:
        """The first screen of a scenario whose «Для кого запись?» the text had answered."""
        if c.s.for_whom is None or c.s.step not in ("question", "age"):
            return False
        return not c.s.history or c.s.history[-1].get("step") in START_STEPS

    async def _ask_audience(self, c: Ctx) -> None:
        """«Для кого запись?» after all: «меня» was read from the text, the patient decides."""
        d = c.s.dialog
        first = d.trail[0] if d.trail else d.scenario_id
        assert first is not None
        facts = {k: v for k, v in d.facts.items() if k != "audience"}
        c.s.for_whom, c.s.dialog = None, DialogState()
        c.back = True  # the screens before stay in the history
        await self._show(c, self.engine.start(c.s.dialog, first, facts))

    async def _on_back(self, c: Ctx, _: str) -> None:
        if self._audience_skipped(c):
            await self._ask_audience(c)
            return
        if not c.s.history:
            await self._show_menu(c)
            return
        *rest, snapshot = c.s.history
        wish, nudged = c.s.wish, c.s.nudged  # a wish dropped later is not brought back
        on_time = c.s.step in TIME_STEPS
        c.s = Session.model_validate(snapshot)
        if wish is not None and on_time and c.s.step in TIME_STEPS:
            # «вечером» typed on the way here goes back with the step, or the earlier screen
            # (all parts of the day) could not be shown again; the doctor and the day stay
            earlier = (c.s.wish or Wish()).without("doctor", "day", "noted")
            wish = wish.without_time().merge(earlier)
            wish = None if wish.empty else wish
        c.s.history, c.s.wish, c.s.nudged, c.s.name_asks = rest, wish, nudged, 0
        c.back = True
        await self._render_screen(c)

    def _complaint_screen(self, c: Ctx, ask: str = texts.ASK_COMPLAINT) -> None:
        """«С чем обращаетесь?» with the topic groups right under it: one tap less."""
        c.s.step, c.s.menu = "complaint", None
        items = topics.visible(self.kb, topics.start_menu(self.kb, c.s.area), c.s.area)
        self._screen(c, ask, [Button(item.caption, f"t:{item.id}") for item in items])

    def _menu_screen(self, c: Ctx, menu_id: str) -> None:
        """Topic buttons about what happened, only those that fit the body area."""
        c.s.step, c.s.menu = "topic", menu_id
        items = topics.visible(self.kb, menu_id, c.s.area)
        buttons = [Button(item.caption, f"t:{item.id}") for item in items]
        self._screen(c, self.kb.topics.menus[menu_id].text, buttons)

    # --- text --------------------------------------------------------------------------

    async def _on_text(self, c: Ctx, text: str) -> None:
        if not text:
            return
        shown = await self._screen_now(c)
        await self._read_text(c, text, shown)
        replies = c.turn.replies
        if len(replies) == 1 and shown and _same_screen(replies[0], shown[-1]):
            # the same step with no word why leaves the patient guessing
            replies.insert(0, Reply(texts.SAME_STEP))

    async def _read_text(self, c: Ctx, text: str, shown: Sequence[Reply]) -> None:
        if find_phrase(text, self.clinic.clinic.crisis.phrases):
            await self._crisis(c, text)
            return
        if find_phrase(text, self.clinic.clinic.emergency.phrases):
            self._reset(c)
            c.say(render.emergency_message(self.clinic), self._main_menu(c))
            return
        step = c.s.step
        c.for_whom, c.more = find_audience(text), names_several(text)
        if (other := after_me(text)) is not None and await self._upcoming(c):
            c.for_whom, c.more = other, False  # «мужа после меня»: the writer is booked already
        if step not in MESSAGE_STEPS and await self._press_typed(c, text, shown):
            return  # a button's label typed as text: «бытовая», «вечер», «14:30», «да»
        if (move := find_navigation(text)) is not None:  # «назад», «в меню», «отмена»
            await self._navigate(c, move)
            return
        if step in MESSAGE_STEPS:  # the text goes to an administrator as it is
            await self._on_step_text(c, step, text)
            return
        if (talk := find_small_talk(text)) is not None:  # «привет», «спасибо», «ок», «пока»
            await self._small_talk(c, talk)
            return
        if asks_when_admin(text) and await self._when_admin(c, step):
            return  # «когда позвонит администратор?»: the hours, nothing is added to the request
        if step == "offer_admin" and await self._on_offer_words(c, text):
            return  # «да, передайте…», «не надо» on «Передать администратору?»
        if step == "consent" and gives_consent(text):  # «согласна, записывайте»
            await self._on_button(c, "c:yes")
            await self._carry_on(c, text)  # the name and the rest may be in the same message
            return
        if await self._no_new_booking(c, step, text):
            return  # «я уже записан, новую запись не надо» in the middle of a new booking
        action = find_command(text)
        if action == "my_bookings" and (wants_move(text) or wants_cancel(text)):
            wish = self._read_wish(c, text).wish  # the day, the doctor or the time named
            if wants_move(text):  # «перенеси на завтра», «отмени и запиши на воскресенье»
                await self._move_by_text(c, wish)
            else:  # «отмени запись на воскресенье», «отмени все»
                today = self._local(c.now).date()
                unclear = len(days_named(text, today)) > 1  # «на завтра и на воскресенье»
                await self._cancel_by_text(c, wish, everything=wants_all(text), unclear=unclear)
            return
        if step in INPUT_STEPS:
            await self._on_input_text(c, step, text, action)
            return
        found = self._read_wish(c, text)
        under_way = step in WIZARD_STEPS and step not in START_STEPS
        if action == "confirm" and under_way:  # «буду» there is an answer, not a confirmation
            action = None
        if action is not None and action != "book":
            # the same screen as the menu button; «отмени запись на завтра» names the day
            # of the booking, not a wish for a new one
            await self._command(c, action)
            return
        if step in TIME_STEPS and await self._press_time(c, text, found, shown):
            return  # «записывайте на 13:30»: the time is on a button of this screen
        if await self._on_wish_text(c, found):
            return
        if action == "book":
            if step in WIZARD_STEPS:  # «запишите меня» while booking: the same step
                if c.for_whom and step in START_STEPS:
                    c.s.for_whom = c.for_whom  # type: ignore[assignment]
                c.say(texts.GOING_ON)
                await self._render_screen(c)
            else:  # and the wish named just before, if any
                self._start_booking(c, self._wish(c))
            return
        if await self._on_short_text(c, step, text):
            return
        # a question in the middle of booking: answered, the step stays; on a step answered by
        # a button route reads any text before the bot gives a hint
        plain = yes_no(text) is not None or is_unsure(text)  # «да», «не знаю»: nothing to read
        ask_route = is_question(text) or (step in BUTTON_STEPS and not plain)
        if step not in FREE_STEPS and ask_route and await self._aside(c, text):
            return
        await self._on_step_text(c, step, text)

    async def _on_other(self, c: Ctx) -> None:
        c.say(texts.ONLY_TEXT, [] if c.s.step in WIZARD_STEPS else self._main_menu(c))
        if c.s.step in WIZARD_STEPS:
            await self._render_screen(c)

    async def _screen_now(self, c: Ctx) -> list[Reply]:
        """The current step's screen as it is shown: the step is drawn on a copy."""
        if c.s.step not in WIZARD_STEPS and c.s.step != "offer_admin":
            return []
        shadow = Ctx(c.db, c.user, c.s.model_copy(deep=True), Turn(), c.now)
        shadow.had_history, shadow.came_from = bool(c.s.history), _screen_key(c.s)
        await self._repeat_buttons(shadow)
        return shadow.turn.replies

    async def _press_typed(self, c: Ctx, text: str, shown: Sequence[Reply]) -> bool:
        """A button's label typed as text presses the button."""
        buttons = [(b.label, b.data) for r in shown for row in r.buttons for b in row]
        mode = TYPED_MODE.get(c.s.step, "choice")
        data = typed.pressed(text, buttons, mode=mode) if buttons else None
        if data is None:
            return False
        await self._on_button(c, data)
        # «ожог» on the groups opens «Рана, ожог, укус», where the same word names one topic:
        # it is pressed too, so a word leads as far as it can without guessing
        for _ in range(TOPIC_DEPTH):
            if not data.startswith("t:") or c.s.step != "topic" or not c.turn.replies:
                break
            opened = [(b.label, b.data) for row in c.turn.replies[-1].buttons for b in row]
            further = typed.pressed(text, opened, mode="topic")
            if further is None or further == data or not further.startswith("t:"):
                break
            c.turn.replies.pop()
            data = further
            await self._on_button(c, data)
        return True

    async def _press_time(self, c: Ctx, text: str, found: Parsed, shown: Sequence[Reply]) -> bool:
        """A time named in a sentence presses its button: «записывайте на сегодня на 13:30».

        The time must be on one button of this screen («Ближе всего к 13:30: 13:30» and
        «13:30» are one time) and the only one the message names. A «не» next to it («только
        не на 13:30»), a question about the schedule («13:30 свободно?») or another day
        («завтра в 13:30» on today's grid) leave the text to the wish parser.
        """
        named = typed.clock_times(text)
        if not named or found.question is not None or typed.refuses_time(text):
            return False
        tz = self.settings.tz
        on_screen: dict[time, set[str]] = defaultdict(set)
        ids = {b.data[2:] for r in shown for row in r.buttons for b in row if b.data[:2] == "s:"}
        for slot_id in ids:
            slot = await self.schedule.get(slot_id)
            if slot is not None:
                on_screen[slots_view.local(slot, tz).time()].add(slot.id)
        wanted = {moment for moment, _ in named if moment in on_screen}
        with_colon = {moment for moment, colon in named if colon}
        if len(wanted) != 1 or not with_colon <= wanted:
            return False  # none, «13:30 или 14:30», or a time that is not on this screen
        slots = on_screen[wanted.pop()]
        slot = await self.schedule.get(next(iter(slots))) if len(slots) == 1 else None
        day = found.wish.day
        if slot is None or (day is not None and day != slots_view.local(slot, tz).date()):
            return False
        await self._on_slot(c, slot.id)
        return True

    async def _when_admin(self, c: Ctx, step: str) -> bool:
        """«Когда позвонит администратор?»: the administrators' hours from config/contacts.yaml.

        Only when a request is with them or is being offered; nothing is added to it.
        """
        if step != "offer_admin" and await self._open_handoff(c) is None:
            return False
        hours = self.clinic.contacts.admin_working_hours
        line = texts.HANDOFF_WHEN.format(start=f"{hours.start:%H:%M}", end=f"{hours.end:%H:%M}")
        if step == "offer_admin" or step in WIZARD_STEPS:
            c.say(line)
            await self._repeat_buttons(c)
        else:
            c.say(line, self._main_menu(c))
        return True

    async def _on_offer_words(self, c: Ctx, text: str) -> bool:
        """«Передать администратору?» answered in words: «да, передайте…», «не надо»."""
        answer = wants_handoff(text)
        if answer is None and find_command(text) == "admin":
            answer = True  # «позовите администратора»: this very request goes to him
        if answer is None or not c.s.handoff_reason:
            return False
        if answer and len(text.split()) > FEW_WORDS:  # what is said with the «да» goes too
            c.s.handoff_context = "\n".join(p for p in (c.s.handoff_context, text) if p)
        await self._on_offer_admin(c, "yes" if answer else "no")
        return True

    async def _on_offer_text(self, c: Ctx, text: str) -> None:
        """Any other text on «Передать администратору?».

        A question is answered — from the reference, or with «ответит администратор», and
        then it goes to the administrator with the request; a complaint or a request to book
        is carried out. Only words that say nothing get «выберите кнопкой».
        """
        reading = await read_text(self._llm(c), text, await self._known(c))
        note = False
        if reading.unavailable:
            note = is_question(text)
            line = texts.ADMIN_WILL_ANSWER if note else texts.PICK_BUTTON_SHORT
        elif reading.kind == "crisis":
            await self._crisis(c, text)
            return
        elif reading.kind == "menu_action" and reading.action == "admin":
            if not is_question(text):
                await self._on_offer_admin(c, "yes")
                return
            # asked about, not asked for: «а администратор скажет то же самое?»
            line, note = texts.ADMIN_WILL_ANSWER, True
        elif reading.kind in ("clinic_question", "medical_question") or (
            reading.kind == "menu_action" and reading.action == "ask"
        ):
            info = await read_question(self._llm(c), text, await self._known(c))
            note = not info.answer
            line = info.answer or texts.ADMIN_WILL_ANSWER
        elif reading.kind == "about_bot":
            line = texts.ABOUT_BOT
        elif reading.kind == "complaint" and self._same_complaint(c, reading):
            # the injury the route was about, once more: a question for the administrator,
            # not a new booking
            line, note = texts.ADMIN_WILL_ANSWER, True
        elif reading.kind in ("complaint", "menu_action", "schedule_question"):
            await self._on_free_text(c, text, reading)  # the patient has moved on
            return
        else:  # not about the trauma centre
            line = texts.PICK_BUTTON_SHORT
        if note:  # the administrator will see the question
            c.s.handoff_context = "\n".join(p for p in (c.s.handoff_context, text) if p)
        c.say(line)
        await self._repeat_buttons(c)

    def _same_complaint(self, c: Ctx, reading: TextReading) -> bool:
        """The complaint is about the injury the scenario has just ended with."""
        d = c.s.dialog
        named = reading.scenario_id
        return d.outcome_id is not None and (named is None or named in d.trail)

    async def _said_already(self, c: Ctx, text: str, reading: TextReading) -> bool:
        """The same complaint right after its route or handoff: the scenario is not run again.

        The patient has read the answer and writes about the same injury once more («это же
        травма ноги! вы меня примете или нет?»): the same questions and the script phrase
        once more would say nothing new. The answer is said plainly instead — where to go
        (a route) or who books it (the outcome's reason) — with the request, if one is at
        the administrators, or with «Передать вопрос администратору». New facts, another
        patient or a later time run the scenario as usual.
        """
        d, ended = c.s.dialog, c.s.ended_at
        if c.s.step != "idle" or ended is None or c.now - ended > SAME_COMPLAINT:
            return False
        if d.outcome_id is None or reading.scenario_id not in d.trail:
            return False
        if any(d.facts.get(name) != value for name, value in reading.facts.items()):
            return False
        if c.for_whom in ("child", "adult") and c.for_whom != (d.audience or "adult"):
            return False
        done = self._dialog_done(c)
        if done is None or done.kind == "book":
            return False
        places = [
            render.place_line(Place(self.kb.facilities[d.facility], d.label, d.note))
            for d in done.outcome.destinations
        ]
        route = "\n".join([texts.NOT_BOOKED_HERE, *places]) if places else None
        handoff = await self._open_handoff(c)
        if handoff is not None:  # the call is not instead of the route: both are said
            why = None if route else done.outcome.patient_reason
            local = self._local(c.now)
            said = render.handoff_message(
                self.clinic, local, handoff.id, why, texts.HANDOFF_ALREADY
            )
            parts = [p for p in (route, said, self.kb.common.more_help.text) if p]
            c.say("\n\n".join(parts), self._main_menu(c))
            return True
        if route is None:
            return False  # a handoff that did not go out: the scenario offers it again
        c.s.step = "offer_admin"
        c.s.handoff_reason = done.outcome.handoff_reason or texts.QUESTION_REASON
        c.s.handoff_context, c.s.handoff_why = text, done.outcome.patient_reason
        c.say(f"{route}\n\n{texts.QUESTIONS_TO_ADMIN}", self._ask_admin_buttons())
        return True

    async def _no_new_booking(self, c: Ctx, step: str, text: str) -> bool:
        """«Я уже записан, новую запись не надо» while a new booking is under way.

        The new booking is dropped and the one the patient has is shown; a question in the
        same message («…плечо на этом приёме тоже осмотрят?») is answered first.
        """
        under_way = c.s.wizard and step in WIZARD_STEPS and c.s.reschedule_of is None
        if not under_way or not refuses_new_booking(text) or not await self._upcoming(c):
            return False
        if is_question(text):
            info = await read_question(self._llm(c), text, await self._known(c))
            if info.answer:
                c.say(info.answer)
        c.say(texts.NO_NEW_BOOKING)
        await self._my_bookings(c)
        return True

    async def _carry_on(self, c: Ctx, text: str) -> None:
        """«Дроздов Павел Сергеевич, 12.12.1986»: the message answers the next request too.

        The name, the birth date and the phone, as far as the message goes; a request it has
        already answered is not shown.
        """
        today = self._local(c.now).date()
        for _ in range(3):
            step = c.s.step
            if step == "name" and find_full_name(text, whole=False) is not None:
                handler = self._on_name
            elif step == "birth" and parse_birth_date(text, today) is not None:
                handler = self._on_birth
            elif step == "contact" and find_phone(text) is not None:
                handler = self._on_phone_text
            else:
                return
            if c.turn.replies:
                c.turn.replies.pop()  # the request this message has answered already
            await handler(c, text)
            if c.s.step == step:
                return

    async def _navigate(self, c: Ctx, move: str) -> None:
        """«Назад», «в меню», «отмена» typed as text — on any step."""
        if move == "back":
            await self._on_back(c, "")
        elif move == "home":
            await self._show_menu(c)
        elif c.s.reschedule_of is not None:  # moving a booking: it stays as it was
            self._reset(c)
            c.say(texts.KEPT, self._main_menu(c))
        elif c.s.wizard and c.s.step in WIZARD_STEPS:
            self._reset(c)
            c.say(texts.FLOW_STOPPED, self._main_menu(c))
        elif c.s.step in ("idle", "payment") and await self._next_booking(c) is not None:
            await self._cancel_by_text(c, Wish())  # «отмена» with a booking: which one, or «да»
        else:
            await self._show_menu(c)

    async def _small_talk(self, c: Ctx, talk: str) -> None:
        """«Привет», «спасибо», «ок», «пока»: a polite reply, never «я помогаю только с…»."""
        if talk == "bye":
            self._reset(c)
            c.say(self.kb.common.goodbye.text, self._main_menu(c))
        elif c.s.step in WIZARD_STEPS or c.s.reschedule_of is not None:
            c.say(texts.GOING_ON)  # booking is under way: the same step
            await self._render_screen(c)
        elif talk == "greeting":
            await self._greet(c)
        elif talk == "thanks":
            self._reset(c)
            c.say(texts.WELCOME, self._main_menu(c))
        else:
            await self._show_menu(c)

    async def _on_input_text(self, c: Ctx, step: str, text: str, action: str | None) -> None:
        """Age, name, birth date, phone: a command or a question typed there is not the data."""
        if action == "book":  # «запишите меня» on the name step is not a name
            c.say(texts.GOING_ON)
            await self._render_screen(c)
            return
        if action is not None and action != "confirm":
            await self._command(c, action)
            return
        today = self._local(c.now).date()
        valid = {
            "age": lambda: parse_age_months(text) is not None,
            "name": lambda: find_full_name(text) is not None,
            "birth": lambda: parse_birth_date(text, today) is not None,
            "contact": lambda: find_phone(text) is not None,
        }[step]()
        if is_question(text):
            # «а зачем вам телефон? ладно, +7…»: the question is answered, the data is taken
            handled = await self._aside(c, text, data=valid)
            if handled and (not valid or c.s.step != step):
                return
        await self._on_step_text(c, step, text)

    async def _on_short_text(self, c: Ctx, step: str, text: str) -> bool:
        """«Не знаю», «ближайшее», «другое время», a bare «да» with nothing asked."""
        if step in START_STEPS and is_unsure(text):  # the topics as buttons
            menu = topics.start_menu(self.kb, c.s.area)
            if c.s.wizard and step == "topic" and c.s.menu == menu:
                c.say(texts.NONE_FITS)  # the same buttons again: what to do if none fits
            c.s.wizard = True
            self._menu_screen(c, menu)
            return True
        if step in TIME_STEPS:
            if wants_nearest(text):
                free = await self._free(c)
                if free is not None and free[1]:
                    await self._on_slot(c, free[1][0].id)
                    return True
            if wants_other_day(text) or (wants_other_time(text) and step != "times"):
                await self._other_day(c)
                return True
            if wants_other_time(text):  # the time grid: the other parts of this day
                if c.s.period is None:  # the grid already shows the whole day
                    await self._other_day(c)
                else:
                    c.s.period = None
                    await self._show_periods(c)
                return True
        if step in ("idle", "payment") and yes_no(text) is not None:
            await self._show_menu(c)
            return True
        return False

    async def _other_day(self, c: Ctx) -> None:
        wish = self._wish(c)
        if wish is not None and wish.day is not None:
            await self._on_wish(c, "day")
        else:
            await self._on_day(c, "other")

    async def _aside(
        self, c: Ctx, text: str, reading: TextReading | None = None, *, data: bool = False
    ) -> bool:
        """A question typed in the middle of booking: the answer, then the same step again.

        True — the text was a question and it is dealt with; False — it was not, and the
        step reads it as usual. ``reading`` — what route already said about the text.
        ``data`` — the message also holds the answer to the step (a name, a date, a phone):
        only the answer to the question is said, the step goes on by itself, and no other
        screen is opened.
        """
        known = await self._known(c)
        if reading is None:
            reading = await read_text(self._llm(c), text, known)
        if reading.unavailable:
            return False
        if reading.kind == "crisis":
            await self._crisis(c, text)
            return True
        if data and reading.kind in ("schedule_question", "menu_action"):
            return False
        if reading.kind == "schedule_question":
            await self._answer_schedule(c, self._read_wish(c, text), text)
            return True
        if reading.kind == "menu_action" and reading.action in NAV_ACTIONS:
            await self._navigate(c, reading.action)  # «хочу на стартовый экран»
            return True
        # «задать вопрос», as route reads a question typed in the middle of a booking: the
        # question itself is answered here, the booking is not dropped for the questions screen
        asks = reading.kind == "menu_action" and reading.action == "ask"
        asks = asks and c.s.step in WIZARD_STEPS
        command = reading.kind == "menu_action" and reading.action in COMMAND_ACTIONS - {"book"}
        if command and not asks:
            await self._command(c, reading.action)  # type: ignore[arg-type]
            return True
        if reading.kind == "clinic_question" or asks:
            info = await read_question(self._llm(c), text, known, on_partial=c.draft)
            c.say(info.answer or (texts.INFO_DOWN if info.unavailable else texts.ASIDE_UNKNOWN))
        elif reading.kind == "about_bot":
            c.say(texts.ABOUT_BOT)
        elif reading.kind == "medical_question":
            c.say(texts.MEDICAL_ASIDE)
        else:
            return False
        if not data:
            await self._render_screen(c)
        return True

    async def _answer_too(self, c: Ctx, text: str) -> None:
        """«Сколько стоит и запишите на завтра»: the answer first, then the request."""
        info = await read_question(self._llm(c), text, await self._known(c))
        c.say(info.answer or (texts.INFO_DOWN if info.unavailable else texts.ASIDE_UNKNOWN))

    async def _next_booking(self, c: Ctx) -> Booking | None:
        return await c.db.scalar(
            select(Booking)
            .where(
                Booking.user_id == c.user.id, Booking.status == "active", Booking.start_at > c.now
            )
            .order_by(Booking.start_at)
            .limit(1)
        )

    async def _confirm_by_text(self, c: Ctx) -> None:
        """«Подтверждаю», «да, приду»: the nearest booking is confirmed, as the button does."""
        booking = await self._next_booking(c)
        self._reset(c)
        if booking is None:
            c.say(texts.MY_EMPTY, self._main_menu(c))
            return
        booking.confirmed_at = c.now
        when = render.format_when(self._local(booking.start_at))
        actions = [
            Button(texts.MOVE, f"b:move:{booking.id}"),
            Button(texts.CANCEL, f"b:cancel:{booking.id}", "danger"),
        ]
        c.say(texts.CONFIRMED_WHEN.format(when=when), [actions, _home()])

    async def _on_step_text(self, c: Ctx, step: str, text: str) -> None:
        handlers: dict[str, Callable[[Ctx, str], Awaitable[None]]] = {
            "age": self._on_age,
            "question": self._on_answer_text,
            "audience": self._on_audience_text,
            "whom": self._on_whom_text,
            "name": self._on_name,
            "birth": self._on_birth,
            "contact": self._on_phone_text,
            "info": self._on_free_text,
            "admin_reply": self._on_admin_reply_text,
            "visit_question": self._on_visit_question,
            "feedback_text": self._on_feedback_text,
            "payment": self._on_payment_text,
        }
        if step in handlers:
            await handlers[step](c, text)
            if step in ("name", "birth"):
                await self._carry_on(c, text)  # «…, 12.12.1986, телефон +7…» in the same message
        elif step in TIME_STEPS:  # neither the code nor route understood: what can be typed
            c.say(texts.HINT_TIME)
            await self._render_screen(c)
        elif step in ("child_confirm", "consent", "patient", "second"):
            c.say(texts.HINT_BUTTONS)
            await self._repeat_buttons(c)
        elif step == "offer_admin":
            await self._on_offer_text(c, text)
        else:  # idle / complaint / topic / describe
            await self._on_free_text(c, text)

    async def _on_free_text(self, c: Ctx, text: str, reading: TextReading | None = None) -> None:
        """Any free text outside a scenario question: one route call decides what it is.

        Nothing here reaches the administrators: only buttons and scenario outcomes do.
        ``reading`` — what route has already said about the text.
        """
        asked = c.s.step == "info"
        at_start = c.s.wizard and c.s.step in START_STEPS  # step 1 of a booking: it goes on
        if reading is None:
            reading = await read_text(self._llm(c), text, await self._known(c))
        if reading.unavailable:
            if asked:
                c.s.handoff_reason, c.s.handoff_context = texts.QUESTION_REASON, text
                c.s.step = "offer_admin"
                c.say(texts.INFO_DOWN, self._ask_admin_buttons())
            else:  # no LLM: the word list narrows the topics
                area = topics.guess_area(text) or c.s.area
                words = topics.guess_candidates(text)
                advice = asks_advice(text) and bool(area or words)
                if advice:
                    self._advice(c)
                self._topics_for(c, area, words, texts.PICK_TOPIC, said=advice)
            return
        if reading.question and reading.kind in ("complaint", "menu_action"):
            await self._answer_too(c, text)  # the question in the message is not lost
        # «что делать, укусила собака?»: the complaint goes on, the advice is the doctor's
        advice = reading.kind == "complaint" and (reading.advice or asks_advice(text))
        injury = self._injury_in(text) if reading.kind == "medical_question" else None
        if reading.kind == "crisis":
            await self._crisis(c, text)
        elif reading.kind == "complaint" and reading.scenario_id:
            if await self._said_already(c, text, reading):
                return  # the same complaint right after its route or handoff
            area = reading.area or c.s.area  # «Опишите своими словами» after «Рука или нога»
            fits = self.kb.scenario(reading.scenario_id).fits(area)
            c.s.area = area if fits else reading.area
            if advice:
                self._advice(c, reading.scenario_id)
            await self._begin(c, reading.scenario_id, reading.facts, find_child_age(text))
        elif reading.kind == "complaint":
            area = reading.area or topics.guess_area(text) or c.s.area
            # «ударился, болит сильно» with the area known: the word list names the kinds
            words = topics.guess_candidates(text) if area else []
            unclear = reading.clarify or texts.TOPIC_UNCLEAR
            if advice:
                self._advice(c)
            self._topics_for(c, area, reading.candidates or words, unclear, said=advice)
        elif reading.kind == "menu_action" and reading.action in NAV_ACTIONS:
            await self._navigate(c, reading.action)  # «хочу на стартовый экран»
        elif reading.kind == "menu_action" and reading.action in COMMAND_ACTIONS:
            await self._command(c, reading.action, self._wish(c))
        elif reading.kind == "schedule_question":  # a wording the word list did not catch
            await self._answer_schedule(c, self._read_wish(c, text), text)
        elif at_start and injury is not None:
            # DeepSeek saw only the question, the words name the injury: the booking goes on
            await self._by_words(c, injury[0] or c.s.area, injury[1], advice=True)
        elif at_start and await self._aside(c, text, reading):
            pass  # a question on step 1: answered, the booking and the wish stay
        elif at_start and reading.kind in ("other_medical", "offtopic"):
            # not a complaint for the trauma centre: said so, the booking and the wish stay
            c.say(texts.NOT_OUR_PROFILE if reading.kind == "other_medical" else texts.OFFTOPIC)
            await self._render_screen(c)
        elif reading.kind == "medical_question":  # no advice and no diagnosis (rule 1)
            self._reset(c)
            data = "m:book"
            if injury is not None:  # «Записаться» leads to this injury, not to an empty screen
                c.s.area, c.s.hint, c.s.for_whom = injury[0], injury[1], c.for_whom  # type: ignore[assignment]
                data = "t:hint"
            c.say(texts.MEDICAL, [[Button(texts.MENU_BOOK, data, "primary")], _home()])
        elif reading.kind == "about_bot":
            await self._command(c, "about_bot")
        elif reading.kind == "feedback":  # to an administrator, only if the patient agrees
            self._reset(c)
            c.s.step = "offer_admin"
            c.s.handoff_reason, c.s.handoff_context = texts.FEEDBACK_REASON, text
            c.say(texts.FEEDBACK, self._ask_admin_buttons(texts.HANDOFF_YES))
        elif reading.kind == "other_department":
            self._reset(c)
            if phone := self.clinic.contacts.call_center_phone:
                c.say(texts.OTHER_DEPARTMENT.format(phone=phone), self._main_menu(c))
            else:  # TODO: the clinic phone (docs/clinic_requests.md, item 2)
                c.s.step = "offer_admin"
                c.s.handoff_reason, c.s.handoff_context = texts.OTHER_DEPARTMENT_REASON, text
                c.say(texts.OTHER_DEPARTMENT_NO_PHONE, self._ask_admin_buttons())
        elif reading.kind == "clinic_question":  # «У меня нет этой информации» only here
            await self._answer_question(c, text)
        elif reading.kind == "other_medical":
            self._reset(c)
            c.say(texts.NOT_OUR_PROFILE, self._main_menu(c))
        else:  # offtopic: the request is not carried out
            self._reset(c)
            c.say(texts.OFFTOPIC, self._main_menu(c))

    def _injury_in(self, text: str) -> tuple[str | None, list[str]] | None:
        """The injury a text names, by the word lists: (body area, scenarios) — or None."""
        area, words = topics.guess_area(text), topics.guess_candidates(text)
        return (area, words) if area or words else None

    def _advice(self, c: Ctx, scenario_id: str | None = None) -> None:
        """«Что делать?» next to a complaint: one message before the first question.

        First what the trauma centre does with this injury — only from the data: the
        scenario's script notice, or else its line of kb/info/uslugi.md, picked by the code
        and shown word for word. Then that advice is the doctor's, and 103 / 112. The bot adds
        nothing of its own (rule 1); an injury with no phrase in the data gets the second
        part only.
        """
        said = []
        if scenario_id is not None:
            said = [n.text for n in self.engine.notices(self.kb.scenario(scenario_id))]
        c.notices_said = bool(said)  # the notice is not repeated before the time is chosen
        if not said and scenario_id in self.care:
            said = [self.care[scenario_id]]
        c.say("\n\n".join([*said, texts.MEDICAL_ASIDE]))

    async def _by_words(
        self, c: Ctx, area: str | None, words: list[str], *, advice: bool = False
    ) -> None:
        """An injury known by the word lists only: its scenario if they name one, else topics."""
        fitting = [w for w in words if w in self.kb.scenarios and self.kb.scenario(w).fits(area)]
        only = fitting[0] if len(fitting) == 1 else None
        if advice:
            self._advice(c, only)
        if only is not None:
            c.s.area = area
            await self._begin(c, only, {}, None)
        else:
            self._topics_for(c, area, fitting, texts.PICK_TOPIC, said=advice)

    def _topics_for(
        self,
        c: Ctx,
        area: str | None,
        candidates: list[str],
        unclear: str,
        *,
        said: bool = False,
    ) -> None:
        """A complaint without a clear scenario: the narrowest fitting topic menu.

        ``unclear`` — what is missing, in words: DeepSeek's question or a line of ours. A
        vaguer text never leads back to a wider menu, the same menu is never shown twice
        without that line (``said`` — a line has been said already), and the reply to a
        description is never «опишите своими словами».
        """
        shown = c.s.menu if c.s.wizard and c.s.step == "topic" else None
        wish, for_whom = c.s.wish, c.for_whom or c.s.for_whom
        self._reset(c, booking=True)
        c.s.wizard, c.s.area, c.s.wish, c.s.for_whom = True, area, wish, for_whom  # type: ignore[assignment]
        menu = topics.best_menu(self.kb, area, candidates)
        if shown is not None and topics.stays(self.kb, shown, menu, area, candidates):
            menu = shown
        if menu is None:
            self._complaint_screen(c, unclear)
            return
        menus = self.kb.topics.menus
        if shown is not None and menus[menu].text == menus[shown].text and not said:
            c.say(unclear)  # the same question again: why
        self._menu_screen(c, menu)

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

    def _nav_row(self, back: bool = True) -> list[Button]:
        """The bottom row of every screen of a booking or a move: «Назад» and «Меню»."""
        home = Button(texts.MENU, "m:home")
        return [Button(texts.BACK, "w:back"), home] if back else [home]

    def _back_row(self) -> list[list[Button]]:
        return [self._nav_row()]

    async def _on_age(self, c: Ctx, text: str) -> None:
        months = parse_age_months(text)
        if months is None:
            c.say(texts.ASK_CHILD_AGE_AGAIN, self._back_row())
            return
        step = self.engine.set_patient(c.s.dialog, "child", months)
        if step is not None:
            await self._show(c, step)

    async def _on_audience_text(self, c: Ctx, text: str) -> None:
        """«Для кого запись?» answered in words. Two patients («меня и сына») with no word
        about who is first are asked again: the first one is never chosen silently."""
        who, lowered = c.for_whom, text.lower()
        if who is None and not c.more:  # one word is enough: «сын», «я»
            if any(w in lowered for w in ("реб", "сын", "доч", "дет")):
                who = "child"
            elif any(w in lowered for w in ("себ", "я ", "взросл")) or lowered == "я":
                who = "adult"
        if who is None:
            c.say(texts.PICK_BUTTON_SHORT)
            await self._repeat_buttons(c)
        else:
            await self._on_audience(c, "child" if who == "child" else "adult")

    async def _on_answer_text(self, c: Ctx, text: str) -> None:
        dialog = c.s.dialog
        if dialog.scenario_id is None or dialog.question_id is None:
            await self._on_free_text(c, text)
            return
        scenario = self.kb.scenario(dialog.scenario_id)
        question = scenario.question(dialog.question_id)
        if yes_no(text) is not None or is_unsure(text):
            reading = AnswerReading()  # «да», «не знаю» name no button: the question again
        else:
            known = await self._known(c)
            reading = await read_answer(self._llm(c), scenario, question, text, known)
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
        name = find_full_name(text)
        if name is None:
            c.s.name_asks += 1
            if c.s.name_asks < 2:
                c.say(texts.ASK_NAME_AGAIN, self._back_row())
            else:  # the same line again would not help: another one, and a way out
                rows = [[Button(texts.MENU_ADMIN, "m:admin")], self._nav_row()]
                c.say(texts.NAME_HELP, rows)
            return
        c.s.draft_name, c.s.name_asks = list(name), 0
        self._ask_birth(c)

    def _ask_birth(self, c: Ctx) -> None:
        """The birth date; above it — how the name was read: any word order is accepted."""
        c.s.step = "birth"
        name = " ".join(part for part in c.s.draft_name or [] if part)
        said = [texts.PATIENT_IS.format(name=name)] if name else []
        self._screen(c, "\n".join([*said, texts.ASK_BIRTH]))

    async def _on_birth(self, c: Ctx, text: str) -> None:
        birth = parse_birth_date(text, self._local(c.now).date())
        if birth is None or not c.s.draft_name:
            c.say(texts.ASK_BIRTH_AGAIN, self._back_row())
            return
        last, first, patronymic = c.s.draft_name
        months = age_in_months(birth, self._local(c.now).date())
        line, audience, age = self._age_by_birth(c, months)
        patient = Patient(
            user_id=c.user.id,
            audience=audience,
            last_name=last or "",
            first_name=first or "",
            patronymic=patronymic,
            birth_date=birth,
        )
        c.db.add(patient)
        await c.db.flush()
        c.s.patient_id, c.s.draft_name = patient.id, None
        if line is None:
            await self._identify(c)
            return
        c.say(line)  # the birth date says otherwise: the scenario again, with the right age
        await self._with_right_age(c, audience, age)

    def _age_by_birth(self, c: Ctx, months: int) -> tuple[str | None, str, int | None]:
        """The birth date against «для себя / для ребёнка» and the age said on step 2.

        (the line to say or None, the audience, the child's age in months). An adult under
        18, a child of 18 or older, an age that is off by more than a year, a baby under 6
        months: the bot says so and leads the scenario again — see ``_with_right_age``.
        """

        def words(age: int) -> str:
            return years_word(age // 12) if age >= 12 else format_age(age)

        d = c.s.dialog
        chosen, said = d.audience or "adult", d.age_months
        if months >= ADULT_FROM_MONTHS:
            line = texts.AGE_IS_ADULT.format(age=words(months)) if chosen == "child" else None
            return line, "adult", None
        if chosen == "adult":
            return texts.AGE_IS_CHILD.format(age=words(months)), "child", months
        if said is None:
            return None, "child", months
        baby = months < INFANT_UNDER_MONTHS <= said  # the 05-04 route depends on it
        if abs(said - months) > 12 or baby:
            line = texts.AGE_DIFFERS.format(age=words(months), said=words(said))
            return line, "child", months
        return None, "child", said

    async def _with_right_age(self, c: Ctx, audience: str, age: int | None) -> None:
        """The scenario again with the right age; the answers that do not depend on it stay.

        The same booking outcome — the booking goes on with the time already chosen (and
        the consent of a child's representative, if it is a child now). Another outcome —
        it is shown instead: nothing is booked on the wrong branch.
        """
        d = c.s.dialog
        before = (d.scenario_id, d.outcome_id)
        first = d.trail[0] if d.trail else d.scenario_id
        assert first is not None
        facts = {k: v for k, v in d.facts.items() if k != "audience"}
        state = DialogState()
        self.engine.set_patient(state, audience, age)  # type: ignore[arg-type]
        c.s.dialog, c.s.for_whom = state, None
        step = self.engine.start(state, first, facts)
        same = isinstance(step, Done) and (step.scenario.id, step.outcome_id) == before
        if same and step.kind == "book" and c.s.slot_id is not None:  # type: ignore[union-attr]
            await self._identify(c)
            return
        c.s.slot_id = c.s.day = c.s.period = c.s.day_from = None
        await self._show(c, step)

    async def _on_phone_text(self, c: Ctx, text: str) -> None:
        phone = find_phone(text)
        if phone is None:  # what is wrong, not the same request once more
            c.say(texts.ASK_CONTACT_AGAIN, ask_contact=True)
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
            "more": self._on_more,
            "way": self._on_way,
            "ws": self._on_wish,
            "ex": self._on_example,
            "sum": self._on_summary,
            "fb": self._on_answer_rating,
            "nd": self._on_unfinished,
            "sec": self._on_second,
            "mo": self._on_whom,
        }.get(head)
        if handler is None:
            c.say(texts.STALE, [_home()])
            return
        await handler(c, rest)

    async def _on_menu(self, c: Ctx, item: str) -> None:
        if item == "book":
            self._start_booking(c)
        elif item == "resume":
            await self._resume(c)
        elif item == "ask":
            self._reset(c)
            c.s.step = "info"
            c.say(texts.ASK_INFO, [_home()])
        elif item == "my":
            await self._my_bookings(c)
        elif item == "admin":  # keep the scenario answers: the administrator sees the context
            await self._handoff(c, texts.ASK_ADMIN_REASON)
        elif item == "more":  # «Записать ещё одного»
            self._reset(c)
            c.s.wizard, c.s.second_ok = True, True  # the first booking is not asked about
            self._whom_screen(c)
        else:  # «В главное меню»
            await self._show_menu(c)

    async def _on_topic(self, c: Ctx, value: str) -> None:
        if value == "hint":  # «Записаться» under «не могу давать рекомендации»: that injury
            if not (c.s.area or c.s.hint):
                self._start_booking(c)
                return
            c.for_whom = c.for_whom or c.s.for_whom
            await self._by_words(c, c.s.area, list(c.s.hint))
            return
        if value == "list":  # a button of an older message: the area's topics, or the groups
            c.s.wizard = True
            self._menu_screen(c, topics.start_menu(self.kb, c.s.area))
            return
        if value in self.kb.scenarios:  # a button from an older message
            await self._begin(c, value, {}, None)
            return
        item = self.kb.topics.item(value)
        if item is None:
            c.say(texts.STALE, [_home()])
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
            c.say(texts.STALE, [_home()])
            return
        step = self.engine.set_patient(c.s.dialog, value)  # type: ignore[arg-type]
        if step is not None:
            await self._show(c, step)

    async def _on_child_confirm(self, c: Ctx, value: str) -> None:
        if c.s.step != "child_confirm" or c.s.pending_scenario is None:
            c.s.step = "idle"
            c.say(texts.STALE, [_home()])
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
            c.say(texts.STALE, [_home()])
            return
        question_id, _, index = value.rpartition(":")
        if question_id and question_id != dialog.question_id:  # a button of an earlier question
            c.say(texts.STALE, [_home()])
            return
        buttons = self.kb.scenario(dialog.scenario_id).question(dialog.question_id).buttons()
        if not index.isdigit() or int(index) >= len(buttons):
            c.say(texts.STALE, [_home()])
            return
        await self._show(c, self.engine.answer(dialog, buttons[int(index)].facts))

    async def _on_day(self, c: Ctx, value: str) -> None:
        if c.s.step not in ("days", "periods", "times"):
            c.say(texts.STALE, [_home()])
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
            c.say(texts.STALE, [_home()])

    async def _on_period(self, c: Ctx, value: str) -> None:
        if c.s.step != "periods" or value not in slots_view.PERIOD_IDS:
            c.say(texts.STALE, [_home()])
            return
        c.s.period = value
        await self._show_times(c)

    async def _on_slot(self, c: Ctx, value: str) -> None:
        """A time from the grid, or «Ближайшее: …» from the day or period screen."""
        if c.s.step not in ("days", "periods", "times"):
            c.say(texts.STALE, [_home()])
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
            c.say(texts.STALE, [_home()])
            return
        if value != "yes":
            self._reset(c)
            c.say(texts.CONSENT_DECLINED, self._main_menu(c))
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
            c.say(texts.STALE, [_home()])
            return
        if value == "new":
            self._ask_name(c)
            return
        patient = await c.db.get(Patient, int(value)) if value.isdigit() else None
        if patient is None or patient.user_id != c.user.id:
            c.say(texts.STALE, [_home()])
            return
        c.s.patient_id = patient.id
        await self._identify(c)

    async def _on_payment(self, c: Ctx, value: str) -> None:
        booking = await c.db.get(Booking, c.s.booking_id) if c.s.booking_id else None
        if booking is not None and booking.user_id == c.user.id:
            booking.payment = "insurance" if value == "ins" else "self"
            if self._cards_on() and booking.admin_message_id:  # the card shows the payment
                text = await self._admin_booking(c, booking)
                c.turn.admin.append(AdminPost(text, edit_message=booking.admin_message_id))
        more = c.s.more  # a second patient was named at the start: now is the time
        self._reset(c)
        text = texts.NEXT_PATIENT if more else f"{texts.THANKS} {self.kb.common.more_help.text}"
        one_more = Button(texts.BOOK_MORE, "m:more", "primary" if more else None)
        c.say(text, [[one_more], *self._main_menu(c)])

    def _whom_screen(self, c: Ctx) -> None:
        """«Записать ещё одного» starts from «для кого»: the consent and the phone given
        before are not asked again."""
        c.s.step = "whom"
        buttons = [Button(texts.WHOM_ADULT, "mo:adult"), Button(texts.AUDIENCE_CHILD, "mo:child")]
        self._screen(c, texts.ASK_AUDIENCE, buttons)

    async def _on_whom(self, c: Ctx, value: str) -> None:
        if c.s.step != "whom" or value not in ("adult", "child"):
            c.say(texts.STALE, [_home()])
            return
        c.s.for_whom = value  # type: ignore[assignment]
        self._complaint_screen(c)

    async def _on_whom_text(self, c: Ctx, text: str) -> None:
        """«Для кого?» of one more booking in words — or with what happened at once."""
        who, lowered = c.for_whom, text.lower()
        if who is None:
            if any(w in lowered for w in ("реб", "сын", "доч", "дет")):
                who = "child"
            elif any(w in lowered for w in ("взросл", "муж", "жен", "мам", "пап", "себ")):
                who = "adult"
        if len(text.split()) > FEW_WORDS:  # «теперь сына, ногу подвернул»: the complaint too
            c.s.step, c.for_whom = "complaint", who
            await self._on_free_text(c, text)
        elif who is None:
            c.say(texts.HINT_BUTTONS)
            await self._render_screen(c)
        else:
            await self._on_whom(c, "child" if who == "child" else "adult")

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
        area, wish, for_whom = c.s.area, c.s.wish, c.for_whom or c.s.for_whom
        self._reset(c, booking=True)
        c.s.wizard, c.s.area, c.s.wish, c.s.for_whom = True, area, wish, for_whom  # type: ignore[assignment]
        c.s.notices_said = c.notices_said
        if for_whom and child_age is None:  # «запишите ребёнка», «меня укусила»: not asked again
            self.engine.set_patient(c.s.dialog, "child" if for_whom == "child" else "adult")
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
        """A scenario question; ``stuck`` — three unclear answers: the prompt says to pick a
        button («Меню» is on every screen, so no extra way out is added)."""
        c.s.step = "question"
        qid = c.s.dialog.question_id
        buttons = [Button(ch.label, f"q:{qid}:{i}") for i, ch in enumerate(prompt.choices)]
        text, more = self._shorten(prompt.text)
        self._screen(c, text, buttons, rows=more)

    def _shorten(self, text: str) -> tuple[str, list[list[Button]]]:
        """A long script phrase in two lines, with «Подробнее» for the whole of it."""
        text, ids = render.shorten(text, self.kb)
        return text, [[Button(texts.MORE, f"more:{'+'.join(ids)}")]] if ids else []

    async def _on_more(self, c: Ctx, phrase_id: str) -> None:
        parts = [render.full_phrase(self.kb, one) for one in phrase_id.split("+")]
        full = "\n\n".join(p for p in parts if p)  # one button for every long phrase above
        if not full:
            c.say(texts.STALE)
        elif c.s.step in WIZARD_STEPS:
            c.say(full)  # below, the screen stays as it is
        elif c.s.step == "offer_admin":
            c.say(full)
            await self._repeat_buttons(c)
        else:  # after a route: the way on is under the text, not somewhere above
            c.say(full, self._main_menu(c))

    def _ask_admin_buttons(self, label: str = texts.ASK_ADMIN_QUESTION) -> list[list[Button]]:
        return [[Button(label, "h:yes", "primary")], _home()]

    async def _command(self, c: Ctx, action: str, wish: Wish | None = None) -> None:
        """A menu command typed as text (app/flow/commands.py) or found by route.

        ``wish`` — the day, time or doctor named with it: «перенеси запись на завтра».
        """
        if action == "book":
            self._start_booking(c, wish)
        elif action == "my_bookings":
            await self._my_bookings(c, wish)
        elif action == "confirm":
            await self._confirm_by_text(c)
        elif action == "feedback":  # «хочу пожаловаться»: what happened is asked first
            self._reset(c)
            c.s.step = "feedback_text"
            c.say(texts.ASK_FEEDBACK, [_home()])
        elif action in MENU_ITEM:
            await self._on_menu(c, MENU_ITEM[action])
        elif action == "forget":
            await self._forget_ask(c)
        else:  # about_bot
            self._reset(c)
            c.say(
                texts.ABOUT_BOT,
                [
                    [Button(texts.MENU_ADMIN, "m:admin")],
                    [Button(texts.MENU_FORGET, "fg:ask")],
                    _home(),
                ],
            )

    async def _done(self, c: Ctx, done: Done) -> None:
        if done.outcome_id == FALLBACK_OUTCOME:  # a knowledge-base gap: no automatic handoff
            c.s.step = "idle"
            c.s.handoff_reason = done.outcome.handoff_reason
            self._final(c, texts.FLOW_BROKEN, [[Button(texts.MENU_ADMIN, "m:admin")], _home()])
            return
        local = self._local(c.now)
        if c.s.notices_said:  # said with the answer to «что делать?»: not twice
            done = replace(done, notices=())
        text, more = self._shorten(render.outcome_message(done, self.clinic, local))
        if done.kind != "book":  # the trauma centre does not book this: the wish is dropped
            c.s.wish, c.s.ended_at = None, c.now
        if done.kind == "book" and await self._too_many_bookings(c):
            return
        if done.kind == "book":
            summary = self._summary(c)  # the answers and the wish, with «Изменить ответы»
            parts = [p for p in (summary, text) if p]
            edit = [[Button(texts.SUMMARY_EDIT, "sum:edit")]] if summary else []
            if parts:  # the summary and the script phrases stay in the chat above the time
                c.say("\n\n".join(parts), [*edit, *more], edit=c.first())
            c.s.day = c.s.day_from = None
            if await self._second_visit(c, done):
                return  # the patient has a first visit ahead: move it or book once more
            await self._offer_time(c)
        elif done.kind == "handoff":
            if text:
                c.say(text, more, edit=c.first())
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
                    *more,
                    [Button(texts.HANDOFF_YES, "h:yes", "primary")],
                    [Button(texts.HANDOFF_NO, "h:no")],
                ],
            )
        else:
            c.s.step = "idle"
            self._final(
                c, f"{text}\n\n{self.kb.common.more_help.text}", [*more, *self._main_menu(c)]
            )

    def _summary(self, c: Ctx) -> str | None:
        """«Ваши ответы: вывих; для себя; бытовая травма; …» and the wish, before the time."""
        d = c.s.dialog
        if c.s.control_of is not None or not d.trail or d.scenario_id is None:
            return None
        parts = [self.kb.scenario(d.trail[0]).menu_label]
        if d.audience == "child":
            who = texts.SUMMARY_CHILD
            parts.append(f"{who}, {format_age(d.age_months)}" if d.age_months is not None else who)
        elif d.audience == "adult":
            parts.append(texts.SUMMARY_ADULT if c.s.for_whom == "adult" else texts.SUMMARY_SELF)
        for q in self.kb.scenario(d.scenario_id).questions:
            choice = _answer_choice(q, d.facts)
            if choice and all(f.name in d.facts for f in q.fields if f.default is None):
                # «Нет, не обращались» means nothing without its question: the bot's own words
                keys = [(name, str(value)) for name, value in choice.facts.items()]
                words = next(
                    (texts.SUMMARY_ANSWERS[k] for k in keys if k in texts.SUMMARY_ANSWERS), None
                )
                parts.append(words or choice.label)
        # «Бытовая травма» → «бытовая травма»; «МРТ подтвердило» stays as it is
        answers = "; ".join(p if p[1:2].isupper() else p[:1].lower() + p[1:] for p in parts)
        lines = [texts.SUMMARY.format(answers=answers)]
        if (wish := self._wish(c)) is not None:
            lines.append(render.wish_line(wish, self._local(c.now).date()))
        return "\n".join(lines)

    async def _on_summary(self, c: Ctx, value: str) -> None:
        """«Изменить ответы»: the scenario questions again; the topic and the patient stay."""
        d = c.s.dialog
        on_time = c.s.step in TIME_STEPS or c.s.step == "second"
        if value != "edit" or not d.trail or not on_time or c.s.reschedule_of:
            c.say(texts.STALE, [_home()])
            return
        first = d.trail[0]
        if c.s.for_whom is not None:  # taken from the text («меня укусила собака»): asked now
            c.s.for_whom, d.age_months = None, None
            d.facts.pop("audience", None)
        c.s.day = c.s.period = c.s.day_from = c.s.slot_id = None
        facts = topics.area_facts(self.kb, first, c.s.area)
        await self._show(c, self.engine.start(d, first, facts))

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

    async def _free(self, c: Ctx, *, wished: bool = True) -> tuple[BookingSpec, list[Slot]] | None:
        """Free slots the 04-09 rules allow; then, of those, what fits the patient's wish."""
        found = await self._booking_spec(c)
        if found is None:
            return None
        spec, _ = found
        slots = await all_slots(self.schedule, spec, now=c.now, config=self.clinic.schedule)
        wish = self._wish(c) if wished else None
        return spec, slots_view.matching(slots, wish, self.settings.tz)

    def _no_slots(self, c: Ctx) -> None:
        c.s.step = "idle"
        self._final(c, texts.NO_SLOTS, [[Button(texts.MENU_ADMIN, "m:admin")], _home()])

    async def _offer_time(self, c: Ctx) -> None:
        """First a day; a wish or a control visit opens straight on the right day."""
        wish = self._wish(c)
        if wish is not None:
            await self._open_wish(c, wish)
            return
        if c.s.anchor is not None:
            free = await self._free(c)
            days = slots_view.days_with_slots(free[1], self.settings.tz) if free else []
            day = slots_view.nearest_day(days, c.s.anchor)
            if day is not None:
                c.s.day, c.s.period = day, None
                await self._show_periods(c)
                return
        await self._show_days(c)

    async def _open_wish(self, c: Ctx, wish: Wish) -> None:
        """Step 4 with a wish: the wished day and time at once, or a plain «no» with ways out."""
        free = await self._free(c, wished=False)
        if free is None:
            await self._show_menu(c)
            return
        spec, allowed = free  # the 04-09 rules come first; the wish only narrows them
        tz = self.settings.tz
        fitting = slots_view.matching(allowed, wish, tz)
        c.s.day = c.s.period = c.s.day_from = None
        if wish.day is None:
            if fitting:
                await self._show_days(c)
            elif wish.has_time and slots_view.matching(allowed, wish.without_time(), tz):
                c.say(texts.WISH_TIME_NEVER.format(time=render.wish_time(wish)))
                c.s.wish = wish.without_time() if wish.doctor else None
                await self._show_days(c)
            else:
                await self._wish_unmet(c, wish, spec, allowed)
            return
        day_times = slots_view.on_day(fitting, wish.day, tz)
        if day_times:
            c.s.day = wish.day
            period = wish.period or (slots_view.period_of(wish.at).id if wish.at else None)
            if period and slots_view.in_period(day_times, period, tz):
                c.s.period = period
                await self._show_times(c)
            else:
                await self._show_periods(c)
            return
        loose = wish.without_time()
        if wish.has_time and slots_view.on_day(
            slots_view.matching(allowed, loose, tz), wish.day, tz
        ):
            day = render.sentence(render.wish_day(wish.day, self._local(c.now).date()))
            c.say(texts.WISH_TIME_GONE.format(day=day, time=render.wish_time(wish)))
            c.s.wish, c.s.day = loose, wish.day
            await self._show_periods(c)
            return
        await self._wish_unmet(c, wish, spec, allowed)

    async def _last_day(self, c: Ctx) -> date:
        """The last day the schedule is open for."""
        slots = await self.schedule.slots(c.now, c.now + timedelta(days=366))
        today = self._local(c.now).date()
        return max((slots_view.local(s, self.settings.tz).date() for s in slots), default=today)

    async def _wish_unmet(
        self, c: Ctx, wish: Wish, spec: BookingSpec, allowed: Sequence[Slot]
    ) -> None:
        """The wish cannot be met: said plainly, with the ways out as buttons."""
        tz, today = self.settings.tz, self._local(c.now).date()
        c.s.step = "days"
        short = short_name(wish.doctor) if wish.doctor else None
        rows: list[list[Button]] = []
        if wish.day is None:  # the doctor has no free time in the coming days at all
            text = texts.WISH_DOCTOR_NONE.format(doctor=short) if short else texts.NO_SLOTS
            if short and allowed:
                rows.append([Button(texts.WISH_ANOTHER_DOCTOR, "ws:doctor", "primary")])
            rows.append([Button(texts.MENU_ADMIN, "m:admin")])
            self._screen(c, text, rows=rows)
            return
        day = render.sentence(render.wish_day(wish.day, today))
        every = await self.schedule.slots(*slots_view.day_bounds(wish.day, tz))
        if not every:
            last = render.day_month(await self._last_day(c))
            text = texts.WISH_DAY_CLOSED.format(date=render.day_month(wish.day), last=last)
        elif short:
            on_duty = [
                s for s in every if s.doctor == wish.doctor and eligible(s, spec.slot_policy)
            ]
            template = texts.WISH_DOCTOR_FULL if on_duty else texts.WISH_DOCTOR_OFF[spec.visit]
            text = template.format(day=day, doctor=short)
        else:
            text = texts.WISH_DAY_FULL.format(day=day)
        other_day = wish.without("day")
        nearest = slots_view.matching(allowed, other_day, tz) or slots_view.matching(
            allowed, other_day.without_time(), tz
        )
        if nearest:
            when = slots_view.nearest_when(nearest[0], self._local(c.now), tz)
            label = texts.NEAREST.format(when=when)
            if wish.doctor:  # «Ярунин: сегодня, 13:00»
                surname = wish.doctor.split()[0]
                label = texts.WISH_NEAREST_DOCTOR.format(doctor=surname, when=when)
            rows.append([Button(label, f"s:{nearest[0].id}", "primary")])
        if short and slots_view.on_day(allowed, wish.day, tz):
            label = texts.WISH_OTHER_DOCTOR.format(day=render.day_label(wish.day, today))
            rows.append([Button(label, "ws:doctor")])
        rows.append([Button(texts.OTHER_DAY, "ws:day")])
        if short or wish.has_time:
            rows.append([Button(texts.WISH_CLEAR, "ws:clear")])
        self._screen(c, text, rows=rows)

    def _time_rows(self, c: Ctx) -> list[list[Button]]:
        """Under a day's times: «Другой день» for a wished day or a control visit, the wish off."""
        wish = self._wish(c)
        rows = []
        if wish is not None and wish.day is not None:
            rows.append([Button(texts.OTHER_DAY, "ws:day")])
        elif c.s.anchor is not None:
            rows.append([Button(texts.OTHER_DAY, "d:other")])
        if wish is not None:
            rows.append([Button(texts.WISH_CLEAR, "ws:clear")])
        return rows

    def _nearest(self, c: Ctx, slots: Sequence[Slot]) -> list[list[Button]]:
        """«Ближайшее: сегодня, 14:30» — the first time by the 04-09 rules, one tap."""
        if not slots:
            return []
        label = slots_view.nearest_label(slots[0], self._local(c.now), self.settings.tz)
        return [[Button(label, f"s:{slots[0].id}", "primary")]]

    def _opened_by_wish(self, c: Ctx) -> bool:
        """The day on screen was named by the patient: there was no day picker before it."""
        wish = self._wish(c)
        return wish is not None and wish.day is not None and wish.day == c.s.day

    def _nearest_that_day(self, c: Ctx, times: Sequence[Slot]) -> list[list[Button]]:
        """«Ближайшее» of a day the patient named: for a day ahead — from the morning.

        «На воскресенье, когда можно» is not about 00:00 of the night before it; for
        today the first free time is the nearest whatever the hour.
        """
        if c.s.day != self._local(c.now).date():
            tz = self.settings.tz
            by_day = [s for s in times if slots_view.local(s, tz).hour >= slots_view.MORNING_FROM]
            times = by_day or times
        return self._nearest(c, times)

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
        if self._wish(c) is not None:
            rows.append([Button(texts.WISH_CLEAR, "ws:clear")])
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

    def _day_header(
        self, c: Ctx, shown: list[Slot], slots: list[Slot], *, doctors: bool = True
    ) -> list[str]:
        """The day and, above the times only, the doctor on duty at the times shown."""
        tz = self.settings.tz
        assert c.s.day is not None
        lines = [render.format_date(c.s.day).capitalize()]
        if doctors:
            day_slots = [s for s in slots if slots_view.local(s, tz).date() == c.s.day]
            lines.append(slots_view.doctors_text(shown, day_slots, self.clinic.schedule, tz))
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
        if len(periods) == 1 or len(shown) <= FEW_TIMES:
            # one part of the day, or a few times in the whole day: straight to the times
            c.s.period = periods[0].id if len(periods) == 1 else None
            await self._show_times(c)
            return
        c.s.step, c.s.period = "periods", None
        lines = [*self._day_header(c, shown, slots, doctors=False), "", texts.PERIOD_PICK]
        buttons = [Button(p.label, f"pr:{p.id}") for p in periods]
        rows = self._time_rows(c)
        # «Ближайшее» belongs to the first screen of the choice: the day picker, or here for a
        # control visit or a day the patient named (no day picker before it)
        top = []
        if c.s.anchor is not None:
            top = self._nearest(c, [s for s in slots if s in shown])
        elif self._opened_by_wish(c):
            top = self._nearest_that_day(c, shown)
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
        top = []
        wish = self._wish(c)
        if wish is not None and wish.at is not None:  # «в 14:30»: the nearest time first
            near = slots_view.closest(shown, wish.at, tz)
            label = texts.WISH_NEAR_TIME.format(
                wanted=f"{wish.at:%H:%M}", time=f"{slots_view.local(near, tz):%H:%M}"
            )
            top.append([Button(label, f"s:{near.id}", "primary")])
        elif self._opened_by_wish(c) and len(shown) == len(day_times):
            top = self._nearest_that_day(c, shown)  # no part-of-the-day screen before this one
        rows = self._time_rows(c)
        self._screen(c, "\n".join(lines), buttons, top=top, rows=rows, columns=TIME_COLUMNS)

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
        text = f"{self.kb.common.identify.text}\n{texts.ASK_NAME}\n{texts.WHY_NAME}"
        self._screen(c, text)

    def _ask_contact(self, c: Ctx) -> None:
        """The phone, with why it is asked (the consent for a handoff has said it already)."""
        text = texts.ASK_CONTACT
        if c.s.resume != "handoff":
            text += "\n" + texts.WHY_PHONE
        c.say(text, ask_contact=True)

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
            self._ask_contact(c)
            return
        await self._book(c)

    async def _too_many_bookings(self, c: Ctx) -> bool:
        """BOOKING_LIMIT upcoming bookings for one Telegram user; a move is not a new one."""
        limit = self.settings.booking_limit
        if c.s.reschedule_of is not None or len(await self._upcoming(c)) < limit:
            return False
        self._reset(c)
        rows = [
            [Button(texts.MENU_MY, "m:my", "primary")],
            [Button(texts.MENU_ADMIN, "m:admin")],
            _home(),
        ]
        c.say(texts.TOO_MANY_BOOKINGS.format(limit=limit), rows, edit=c.first())
        return True

    async def _book(self, c: Ctx) -> None:
        if await self._too_many_bookings(c):  # e.g. a control visit, or booked meanwhile
            return
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
        if self._cards_on():
            text = await self._admin_booking(c, booking)
            c.turn.admin.append(AdminPost(text, booking_id=booking.id))
        c.say(self.kb.common.identify_thanks.text, remove_keyboard=True)
        c.say("", media=BOOKED, media_only=True)
        if photo := self.clinic.schedule.doctor_photos.get(slot.doctor):
            caption = f"{texts.CARD_DOCTOR}: {slot.doctor}"
            c.say(caption, media=Media("photo", photo), media_only=True)
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
        c.say(card.plain(), actions, card=card, pin=booking.id)
        if (place := render.venue(self.clinic)) is not None:
            c.say(place.address, venue=place)
        self._reset(c, booking=True)  # the second patient, if one was named, is offered next
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

    # --- bookings in the admin chat (BOOKINGS_TO_ADMIN_CHAT, for the test) --------------------

    def _cards_on(self) -> bool:
        return self.settings.bookings_to_admin_chat and bool(self.settings.admin_chat_id)

    def _topic(self, booking: Booking) -> str:
        scenario = self.kb.scenarios.get(booking.scenario_id)
        return f"{scenario.number} {scenario.title}" if scenario else booking.scenario_id

    def _patient_line(self, patient: Patient, today: date) -> str:
        months = age_in_months(patient.birth_date, today)
        age = format_age(months) if months < 36 else years_word(months // 12)  # months: babies
        who = f"ребёнок, {age}" if patient.audience == "child" else f"взрослый, {age}"
        return texts.PATIENT_LINE.format(
            name=patient.full_name, birth=f"{patient.birth_date:%d.%m.%Y}", who=who
        )

    async def _admin_booking(self, c: Ctx, booking: Booking) -> str:
        """The booking card for the admin chat: when, doctor, topic, patient, phone, payment."""
        patient = await c.db.get(Patient, booking.patient_id)
        today = self._local(c.now).date()
        telegram = c.user.display_name or ""
        if c.user.username:
            telegram += f" @{c.user.username}"
        return texts.ADMIN_BOOKING.format(
            number=booking.id,
            demo=texts.ADMIN_BOOKING_DEMO if booking.demo else "",
            when=render.format_day_time(self._local(booking.start_at)),
            doctor=booking.doctor,
            visit=texts.VISIT_KIND.get(booking.visit, booking.visit),
            topic=self._topic(booking),
            patient=self._patient_line(patient, today) if patient else "—",
            phone=c.user.phone or "—",
            payment=texts.PAYMENT_KIND.get(booking.payment, booking.payment),
            telegram=f"{telegram.strip() or '—'} (id {c.user.id})",
        )

    def _unpin(self, c: Ctx, booking: Booking) -> None:
        if booking.chat_message_id:
            c.turn.unpin.append(booking.chat_message_id)
            booking.chat_message_id = None

    def _booking_note(self, c: Ctx, booking: Booking, text: str) -> None:
        """A change to a booking, as a reply to its card in the admin chat."""
        if self._cards_on() and booking.admin_message_id:
            c.turn.admin.append(AdminPost(text, reply_to=booking.admin_message_id))

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

    async def _upcoming(self, c: Ctx) -> Sequence[Booking]:
        rows = await c.db.scalars(
            select(Booking)
            .where(
                Booking.user_id == c.user.id, Booking.status == "active", Booking.start_at > c.now
            )
            .order_by(Booking.start_at)
        )
        return rows.all()

    async def _my_bookings(self, c: Ctx, wish: Wish | None = None) -> None:
        """``wish`` — a day or a time named with it: «Перенести» opens it at once."""
        self._reset(c)
        bookings = await self._upcoming(c)
        if not bookings:
            c.say(texts.MY_EMPTY, self._main_menu(c))
            return
        title = texts.MY_ONE if len(bookings) == 1 else texts.MY_TITLE
        note = None  # the booking first, then what «Перенести» will do
        if wish is not None and not wish.empty:
            c.s.wish = wish
            words = render.wish_words(wish, self._local(c.now).date())
            note = texts.WISH_MOVE.format(wish=words)
        if len(bookings) > 1:  # one booking is one message: the title goes into it
            c.say(title)
        for b in bookings:
            patient = await c.db.get(Patient, b.patient_id)
            start = self._local(b.start_at)
            item = texts.MY_ITEM.format(
                date=render.format_date(start.date()),
                time=f"{start:%H:%M}",
                patient=patient.full_name if patient else "—",
                doctor=b.doctor,
            )
            if len(bookings) == 1:
                item = "\n".join([title, item, *(["", note] if note else [])])
            c.say(
                item,
                [
                    [
                        Button(texts.MOVE, f"b:move:{b.id}"),
                        Button(texts.CANCEL, f"b:cancel:{b.id}", "danger"),
                    ]
                ],
            )
        if note and len(bookings) > 1:
            c.say(note)

    async def _move_by_text(self, c: Ctx, wish: Wish) -> None:
        """«Перенеси на завтра», «отмени и запиши на воскресенье»: the new time at once.

        One booking — its move starts here; several — which one is asked first. The old
        time is held until a new one is chosen.
        """
        bookings = await self._upcoming(c)
        named = None if wish.empty else wish
        if len(bookings) == 1:
            await self._start_move(c, bookings[0], named)
            return
        self._reset(c)
        if not bookings:
            c.say(texts.MY_EMPTY, self._main_menu(c))
            return
        c.s.wish = named
        rows = []
        for b in bookings:
            patient = await c.db.get(Patient, b.patient_id)
            start = self._local(b.start_at)
            label = texts.MOVE_ITEM.format(
                day=f"{texts.WEEKDAYS_SHORT[start.weekday()]}, {start:%d.%m}",
                time=f"{start:%H:%M}",
                patient=short_name(patient.full_name) if patient else "—",
            )
            rows.append([Button(label[:LABEL_MAX], f"b:move:{b.id}")])
        c.say(texts.MOVE_WHICH, [*rows, _home()])

    async def _start_move(self, c: Ctx, booking: Booking, wish: Wish | None) -> None:
        """«Переношу запись с субботы, 14 ноября, 11:30», then the day and the time."""
        self._reset(c)
        c.s.reschedule_of, c.s.wish = booking.id, wish
        start = self._local(booking.start_at)
        c.say(
            texts.MOVING.format(
                weekday=texts.WEEKDAYS_FROM[start.weekday()],
                date=render.day_month(start.date()),
                time=f"{start:%H:%M}",
            )
        )
        await self._offer_time(c)

    async def _on_booking_action(self, c: Ctx, value: str) -> None:
        action, _, booking_ref = value.partition(":")
        if action == "askall":  # «Отменить все записи» under «Какую запись отменить?»
            await self._cancel_by_text(c, Wish(), everything=True)
            return
        if action == "dropall":
            await self._drop_all(c)
            return
        booking = await self._user_booking(c, booking_ref)
        if booking is None or booking.start_at <= c.now:
            c.say(texts.BOOKING_GONE, self._main_menu(c))
            return
        if action == "move":  # the card or the reminder stays; the day picker comes below
            await self._start_move(c, booking, self._wish(c))  # «…на завтра» named before
        elif action == "cancel":
            self._cancel_ask(c, booking)
        elif action == "drop":
            await self._drop(c, booking)
            self._reset(c)
            c.say(texts.CANCELLED, self._main_menu(c))
        else:  # «Нет, оставить», «Оставить как есть»
            c.s.cancel_ids = []  # «Отменить все записи?» is answered: its «да» is void
            if c.s.reschedule_of is not None:
                self._reset(c)
            c.say(texts.KEPT, self._main_menu(c))

    # --- cancelling in words: «отмени запись на воскресенье», «отмени все» -------------------

    def _cancel_ask(self, c: Ctx, booking: Booking) -> None:
        """«Отменить запись на … в …?» — nothing is cancelled without a «да»."""
        start = self._local(booking.start_at)
        text = texts.CANCEL_ASK.format(date=render.format_date(start.date()), time=f"{start:%H:%M}")
        c.say(
            text,
            [
                [Button(texts.CANCEL_YES, f"b:drop:{booking.id}", "danger")],
                [Button(texts.CANCEL_KEEP, f"b:keep:{booking.id}")],
            ],
        )

    async def _drop(self, c: Ctx, booking: Booking) -> None:
        """A cancellation: the slot is free, the reminders are off, the cards are updated."""
        await self.schedule.release(booking.slot_id)
        booking.status = "cancelled"
        await self._cancel_reminders(c, booking)
        self._booking_note(c, booking, texts.ADMIN_CANCELLED)
        self._unpin(c, booking)

    async def _booking_lines(self, c: Ctx, bookings: Sequence[Booking]) -> list[str]:
        lines = []
        for b in bookings:
            patient = await c.db.get(Patient, b.patient_id)
            start = self._local(b.start_at)
            lines.append(
                texts.MY_LINE.format(
                    date=render.format_date(start.date()),
                    time=f"{start:%H:%M}",
                    patient=patient.full_name if patient else "—",
                    doctor=b.doctor,
                )
            )
        return lines

    def _bookings_like(self, c: Ctx, bookings: Sequence[Booking], wish: Wish) -> list[Booking]:
        """The bookings a text names by the day, the doctor or the time."""

        def fits(b: Booking, day: date | None) -> bool:
            start = self._local(b.start_at)
            moment = start.time()
            if wish.doctor is not None and b.doctor != wish.doctor:
                return False
            if day is not None and start.date() != day:
                return False
            face = (moment.hour % 12, moment.minute)  # by the clock face: «в 9» is 9:00, 21:00
            if wish.at is not None and face != (wish.at.hour % 12, wish.at.minute):
                return False
            if wish.period is not None and slots_view.period_of(moment).id != wish.period:
                return False
            if wish.start is not None and moment < wish.start:
                return False
            return wish.end is None or moment <= wish.end

        found = [b for b in bookings if fits(b, wish.day)]
        today = self._local(c.now).date()
        if not found and wish.day == today + timedelta(days=7):
            found = [b for b in bookings if fits(b, today)]  # «в четверг», said on a Thursday
        return found

    async def _cancel_by_text(
        self, c: Ctx, wish: Wish, *, everything: bool = False, unclear: bool = False
    ) -> None:
        """A request to cancel: the booking it names, a choice, or all of them — with a «да».

        One booking fits — «Отменить запись на … в …?» at once; several — only they are shown;
        none — said so, and all are shown. The plain list of «Мои записи» is never the answer.
        ``unclear`` — the text names two days («на завтра и на воскресенье… только ту, что на
        воскресенье»): which booking is meant is asked, the bot offers none itself.
        """
        bookings = list(await self._upcoming(c))
        self._reset(c)
        if not bookings:
            c.say(texts.MY_EMPTY, self._main_menu(c))
            return
        if unclear and not everything and len(bookings) > 1:
            await self._cancel_choice(c, bookings, texts.CANCEL_WHICH, with_all=False)
            return
        named = not wish.empty
        found = self._bookings_like(c, bookings, wish) if named else bookings
        if not found:
            await self._cancel_choice(c, bookings, texts.CANCEL_NOT_FOUND, with_all=True)
        elif len(found) == 1:
            self._cancel_ask(c, found[0])
        elif everything:
            lines = await self._booking_lines(c, found)
            c.s.cancel_ids = [b.id for b in found]
            c.say(
                "\n".join([texts.MY_TITLE, *lines, "", texts.CANCEL_ALL_ASK]),
                [
                    [Button(texts.CANCEL_ALL_YES, "b:dropall", "danger")],
                    [Button(texts.CANCEL_KEEP, f"b:keep:{found[0].id}")],
                ],
            )
        else:
            await self._cancel_choice(c, found, texts.CANCEL_WHICH, with_all=not named)

    async def _cancel_choice(
        self, c: Ctx, bookings: Sequence[Booking], title: str, *, with_all: bool
    ) -> None:
        """«Какую запись отменить?»: one message, a button for each booking."""
        rows = []
        for b in bookings:
            start = self._local(b.start_at)
            day = f"{texts.WEEKDAYS_SHORT[start.weekday()].lower()}, {start:%d.%m}"
            label = texts.CANCEL_PICK.format(day=day, time=f"{start:%H:%M}")
            rows.append([Button(label, f"b:cancel:{b.id}")])
        if with_all and len(bookings) > 1:
            rows.append([Button(texts.CANCEL_ALL, "b:askall", "danger")])
        lines = await self._booking_lines(c, bookings)
        c.say("\n".join([title, *lines]), [*rows, _home()])

    async def _drop_all(self, c: Ctx) -> None:
        """«Да, отменить все»: the bookings the question was about, one confirmation."""
        ids = set(c.s.cancel_ids)
        bookings = [b for b in await self._upcoming(c) if b.id in ids]
        if not bookings:  # the question is gone (the session moved on): nothing is cancelled
            c.say(texts.STALE, [_home()])
            return
        for booking in bookings:
            await self._drop(c, booking)
        self._reset(c)
        c.say(texts.CANCELLED_ALL, self._main_menu(c))

    # --- a second first visit of the same patient -------------------------------------------

    async def _second_visit(self, c: Ctx, done: Done) -> bool:
        """The patient already has a first visit ahead: said before the time, with a choice.

        Who the patient is becomes known only on step 5, so the patient is told by «для себя»
        / «для ребёнка». A control visit after the survey and a move are not asked about.
        """
        spec = done.outcome.booking
        if c.s.second_ok or c.s.control_of is not None or c.s.reschedule_of is not None:
            return False
        if spec is None or spec.visit != "primary":
            return False
        audience = c.s.dialog.audience or "adult"
        for booking in await self._upcoming(c):
            patient = await c.db.get(Patient, booking.patient_id)
            if booking.visit == "primary" and patient and patient.audience == audience:
                c.s.second_of = booking.id
                await self._second_screen(c)
                return True
        return False

    async def _second_screen(self, c: Ctx) -> None:
        booking = await c.db.get(Booking, c.s.second_of) if c.s.second_of else None
        if booking is None or booking.status != "active" or booking.start_at <= c.now:
            c.s.second_of = None  # it is gone meanwhile: straight to the time
            await self._offer_time(c)
            return
        c.s.step = "second"
        patient = await c.db.get(Patient, booking.patient_id)
        start = self._local(booking.start_at)
        text = texts.ALREADY_BOOKED.format(
            date=render.format_date(start.date()),
            time=f"{start:%H:%M}",
            patient=patient.full_name if patient else "—",
        )
        rows = [
            [Button(texts.ALREADY_MOVE, "sec:move", "primary")],
            [Button(texts.ALREADY_AGAIN, "sec:again")],
        ]
        self._screen(c, text, rows=rows)

    async def _on_second(self, c: Ctx, value: str) -> None:
        """«Перенести ту запись» or «Записать ещё раз»."""
        booking = await c.db.get(Booking, c.s.second_of) if c.s.second_of else None
        if c.s.step != "second" or booking is None:
            c.say(texts.STALE, [_home()])
            return
        if value == "move":  # the day, the doctor and the time asked for go with it
            await self._start_move(c, booking, self._wish(c))
            return
        c.s.second_ok = True
        await self._offer_time(c)

    async def _reschedule(self, c: Ctx, slot: Slot) -> None:
        booking = await c.db.get(Booking, c.s.reschedule_of) if c.s.reschedule_of else None
        if booking is None or booking.status != "active":
            c.say(texts.BOOKING_GONE, self._main_menu(c))
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
        when = render.format_day_time(self._local(slot.start))
        self._booking_note(c, booking, texts.ADMIN_MOVED.format(when=when, doctor=slot.doctor))
        self._unpin(c, booking)  # the pinned card shows the old time
        self._reset(c)
        start = self._local(slot.start)
        text = texts.MOVED.format(
            date=render.format_date(start.date()), time=f"{start:%H:%M}", doctor=slot.doctor
        )
        if self.settings.is_demo_booking:
            text += "\n\n" + texts.DEMO_BOOKING
        c.say(text, self._main_menu(c))

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
            return Reply(f"{text}\n\n{docs}", actions, chat_id=chat_id, media=REMINDER)
        if kind == TWO_HOURS:
            center = self.clinic.contacts.trauma_center
            text = texts.REMINDER_TWO_HOURS.format(
                when=render.format_when(start), address=center.address, entrance=center.entrance
            )
            day = center.underground_parking_hours.contains(start.time())
            parking = common.parking_day.text if day else common.parking_night.text
            short, more = self._shorten(parking)
            way = ((Button(texts.HOW_TO_GET, f"way:{b.id}", "primary"),),)
            extra = tuple(tuple(row) for row in more)
            return Reply(
                f"{text}\n\n{short}", way + extra + actions, chat_id=chat_id, media=ENTRANCE
            )
        if kind == AFTER_VISIT and b.cycle >= 2:
            return Reply(
                texts.FEELING,
                (
                    (Button(texts.FEELING_OK, f"v:ok:{b.id}"),),
                    (Button(texts.AFTER_VISIT_QUESTION, f"v:q:{b.id}"),),
                ),
                chat_id=chat_id,
                media=FEEDBACK,
            )
        return Reply(
            texts.AFTER_VISIT,
            (
                (Button(texts.AFTER_VISIT_OK, f"v:ok:{b.id}"),),
                (Button(texts.AFTER_VISIT_QUESTION, f"v:q:{b.id}"),),
                (Button(texts.AFTER_VISIT_MISSED, f"v:missed:{b.id}"),),
            ),
            chat_id=chat_id,
            media=FEEDBACK,
        )

    async def _on_way(self, c: Ctx, value: str) -> None:
        """«Как пройти» under the 2-hour reminder: the map point, or the address and parking."""
        booking = await self._user_booking(c, value)
        center = self.clinic.contacts.trauma_center
        if (place := render.venue(self.clinic)) is not None:
            c.say(place.address, venue=place)
            return
        text = texts.WAY.format(address=center.address, entrance=center.entrance)
        if booking is not None:
            day = center.underground_parking_hours.contains(self._local(booking.start_at).time())
            common = self.kb.common
            text += "\n\n" + (common.parking_day.text if day else common.parking_night.text)
        c.say(text)

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
            c.say(texts.DEMO_NONE, self._main_menu(c))
            return
        c.turn.replies += (await self._fire(c.db, reminder, c.now)).replies

    async def _on_reminder(self, c: Ctx, value: str) -> None:
        _, _, ref = value.partition(":")
        booking = await self._user_booking(c, ref)
        if booking is None:
            c.say(texts.BOOKING_GONE)
            return
        booking.confirmed_at = c.now
        c.turn.reaction = "👍"
        c.say(texts.CONFIRMED, [_home()])

    async def _on_visit(self, c: Ctx, value: str) -> None:
        answer, _, ref = value.partition(":")
        booking = await self._user_booking(c, ref)
        if booking is None:
            c.say(texts.BOOKING_GONE)
            return
        if answer == "missed":  # no control visit is offered: a new booking instead
            booking.survey = "missed"
            self._booking_note(c, booking, texts.ADMIN_SURVEY["missed"])
            self._reset(c)
            c.say(texts.MISSED, [[Button(texts.MENU_BOOK, "m:book", "primary")], _home()])
            return
        booking.survey = "ok" if answer == "ok" else "question"
        survey = texts.ADMIN_FEELING if booking.cycle >= 2 else texts.ADMIN_SURVEY
        self._booking_note(c, booking, survey[booking.survey])
        if answer == "ok":
            if booking.cycle >= 2:
                self._rate_prompt(c, booking)
            else:
                c.say(texts.GLAD)
                self._control_prompt(c, booking)
        else:
            self._reset(c)
            c.s.step, c.s.visit_booking = "visit_question", booking.id
            c.say(texts.ASK_VISIT_QUESTION, [_home()])

    async def _on_feedback_text(self, c: Ctx, text: str) -> None:
        await self._handoff(c, texts.FEEDBACK_REASON, text)

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
            self._booking_note(c, booking, texts.ADMIN_RATING.format(score=score))
            if score == "5":
                c.turn.reaction = "❤"
        self._reset(c)
        c.say(f"{texts.RATED} {self.kb.common.more_help.text}", self._main_menu(c))

    # --- wishes: the doctor, the day and the time the patient asked for ---------------------

    def _read_wish(self, c: Ctx, text: str) -> Parsed:
        """The wish in a text, read by code before masking and before DeepSeek."""
        step = c.s.step
        about_time = step in TIME_STEPS or step == "second"  # the time is being chosen
        context = "time" if about_time else "free" if step in FREE_STEPS else "strict"
        local = self._local(c.now)  # a bare «в 7» is the nearest seven by the clinic's clock
        found = parse_wish(
            text,
            self.clinic.schedule.doctors,
            local.date(),
            context=context,  # type: ignore[arg-type]
            now=local.time(),
        )
        if not found.wish.empty:
            found.wish.noted = c.now
        return found

    def _start_booking(self, c: Ctx, wish: Wish | None = None) -> None:
        """«Записаться»: the wizard from «Что случилось?»; the wish waits for step 4."""
        self._reset(c)
        c.s.wizard = True
        c.s.wish = None if wish is None or wish.empty else wish
        c.s.for_whom = c.for_whom  # type: ignore[assignment]
        self._complaint_screen(c)

    async def _on_wish_text(self, c: Ctx, found: Parsed) -> bool:
        """A wish or a question about the schedule in a text. True — the text is dealt with.

        The wish is a filter over the schedule, not a way around the scenario: «Что
        случилось?», «Для кого запись?» and the scenario questions stay.
        """
        if found.question is not None:
            await self._answer_schedule(c, found)
            return True
        if found.wish.empty and not found.unknown_doctor:
            return False
        step = c.s.step
        wish = (self._wish(c) or Wish()).merge(found.wish)
        if step in TIME_STEPS:  # choosing the time: the wish is applied at once
            if found.unknown_doctor:
                self._no_such_doctor(c)
            if found.wish.day is None and c.s.day is not None:  # «вечером» on a day's screen
                wish = wish.merge(Wish(day=c.s.day))
            c.s.wish = None if wish.empty else wish
            await self._offer_time(c)
            return True
        under_way = step in WIZARD_STEPS and step not in START_STEPS
        if found.unknown_doctor:
            if found.pure and not under_way:
                self._reset(c)
                c.s.wish = None if wish.empty else wish
                self._no_such_doctor(c, choose=True)
                return True
            self._no_such_doctor(c)
        c.s.wish = None if wish.empty else wish
        if not found.pure:
            return False  # the rest of the message goes on: a complaint, an answer, a question
        if step in WIZARD_STEPS:  # the booking goes on: the same step, now with the wish
            if c.for_whom and step in START_STEPS:
                c.s.for_whom = c.for_whom  # type: ignore[assignment]
            await self._render_screen(c)
        else:
            self._start_booking(c, c.s.wish)
        return True

    def _no_such_doctor(self, c: Ctx, *, choose: bool = False) -> None:
        """«К Иванову»: no such doctor; the doctors from the data, each as a button."""
        doctors = self.clinic.schedule.doctors
        text = "\n".join([texts.NO_SUCH_DOCTOR, *(f"• {d}" for d in doctors)])
        rows = pack([Button(short_name(d), f"ws:doc:{i}") for i, d in enumerate(doctors)])
        if choose:
            rows += [[Button(texts.ANY_DOCTOR, "ws:book")], _home()]
        c.say(text, rows)

    async def _on_wish(self, c: Ctx, value: str) -> None:
        """Buttons about the wish: a doctor from the list, «Убрать пожелание», the ways out."""
        action, _, ref = value.partition(":")
        wish = self._wish(c)
        if action == "book":  # under an answer about the schedule
            self._start_booking(c, wish)
            return
        if action in ("doc", "when"):
            doctors = self.clinic.schedule.doctors
            if not ref.isdigit() or int(ref) >= len(doctors):
                c.say(texts.STALE, [_home()])
                return
            doctor = doctors[int(ref)]
        if action == "when":  # a doctor under «какие у вас врачи?»: when he sees patients
            text, asked = await self._doctor_days(c, Wish(doctor=doctor))
            await self._schedule_answer(c, text, asked, texts.BOOK_THIS_DOCTOR)
            return
        if action == "doc":
            wish = (wish or Wish()).merge(Wish(doctor=doctor, noted=c.now))
            if c.s.step in TIME_STEPS:
                c.s.wish = wish
                await self._offer_time(c)
            elif c.s.step in WIZARD_STEPS:
                c.s.wish = wish
                await self._render_screen(c)
            else:
                self._start_booking(c, wish)
            return
        if c.s.step not in TIME_STEPS:
            c.say(texts.STALE, [_home()])
            return
        if wish is not None and action == "doctor":  # the same day, the doctor on duty
            wish = wish.without("doctor")
        elif wish is not None and action == "day":  # «Другой день»: the doctor stays
            wish = wish.without("day")
        else:  # «Убрать пожелание»
            wish = None
        c.s.wish = None if wish is None or wish.empty else wish
        c.s.day = c.s.period = c.s.day_from = None
        await self._offer_time(c)

    # --- questions about the schedule: answered by code from the provider ------------------

    async def _answer_schedule(self, c: Ctx, found: Parsed, text: str = "") -> None:
        """«Кто принимает завтра?», «когда работает Ярунин?», «есть время сегодня вечером?».

        Under the answer — a booking button that carries what the schedule can give: the
        day, the doctor or the time asked about.
        """
        asked = found.wish
        question = found.question
        if question is None:  # found by route: a wording the word list did not catch
            about_doctors = "who" if asked.day else "doctors"
            by_words = about_doctors if names_doctors(text) else "free"
            question = "doctor" if asked.doctor else by_words  # type: ignore[assignment]
        if question == "doctors":
            await self._doctors(c)
            return
        if question == "who":
            text, wish = await self._who_is_on(c, asked)
            label = texts.BOOK_THIS_DAY if wish.day else texts.MENU_BOOK
        elif question == "doctor" and asked.doctor:
            text, wish = await self._doctor_days(c, asked)
            label = texts.BOOK_THIS_DOCTOR
        else:
            text, wish = await self._free_time(c, asked)
            label = texts.BOOK_THIS_TIME if wish.day or wish.has_time else texts.MENU_BOOK
        await self._schedule_answer(c, text, wish, label)

    async def _schedule_answer(self, c: Ctx, text: str, wish: Wish, label: str) -> None:
        if c.s.step in WIZARD_STEPS or c.s.reschedule_of is not None:
            c.say(text)  # booking is under way: the answer, then the same step again
            await self._render_screen(c)
            return
        self._reset(c)
        if not wish.empty:
            c.s.wish = wish.model_copy(update={"noted": c.now})
        c.say(text, [[Button(label, "ws:book", "primary")], _home()])

    async def _doctors(self, c: Ctx) -> None:
        """«Какие у вас врачи?»: everyone from the data, who is on duty now, each one's days."""
        doctors = self.clinic.schedule.doctors
        lines = [texts.DOCTORS_TITLE]
        for name in doctors:
            post = self.posts.get(name)
            lines.append(texts.DOCTORS_LINE.format(name=name, role=post) if post else f"• {name}")
        today = self._local(c.now).date()
        slots = await self.schedule.slots(*slots_view.day_bounds(today, self.settings.tz))
        on_duty = [s.doctor for s in slots if s.start <= c.now < s.end and not s.repeat_cabinet]
        if on_duty:
            names = ", ".join(dict.fromkeys(on_duty))
            lines += ["", texts.DOCTORS_NOW.format(doctors=names)]
        if c.s.step in WIZARD_STEPS or c.s.reschedule_of is not None:
            c.say("\n".join(lines))  # booking is under way: the answer, then the same step
            await self._render_screen(c)
            return
        self._reset(c)
        lines.append(texts.DOCTORS_HINT)
        each = pack([Button(short_name(d), f"ws:when:{i}") for i, d in enumerate(doctors)])
        book = Button(texts.MENU_BOOK, "m:book", "primary")
        c.say("\n".join(lines), [[book], *each, _home()])

    async def _shifts_from_now(self, c: Ctx, day: date) -> list[Slot]:
        """Every slot of the day, free or taken; a shift that is over by now is left out."""
        slots = await self.schedule.slots(*slots_view.day_bounds(day, self.settings.tz))
        ends: dict[tuple[str, bool], datetime] = {}
        for s in slots:
            key = (s.doctor, s.repeat_cabinet)
            ends[key] = max(ends.get(key, s.end), s.end)
        return [s for s in slots if ends[(s.doctor, s.repeat_cabinet)] > c.now]

    async def _who_is_on(self, c: Ctx, asked: Wish) -> tuple[str, Wish]:
        """(the doctors on duty that day, the wish for the booking button)."""
        today = self._local(c.now).date()
        day = asked.day or today
        slots = await self._shifts_from_now(c, day)
        if not slots:
            last = render.day_month(await self._last_day(c))
            return texts.SHIFTS_NONE.format(date=render.day_month(day), last=last), Wish()
        title = texts.SHIFTS_TITLE.format(day=render.sentence(render.wish_day(day, today)))
        lines = slots_view.shifts_text(slots, self.clinic.schedule, self.settings.tz)
        return "\n".join([title, *lines]), asked.merge(Wish(day=day))

    async def _doctor_days(self, c: Ctx, asked: Wish) -> tuple[str, Wish]:
        """(when the doctor sees patients, the wish for the booking button)."""
        assert asked.doctor is not None
        tz, today = self.settings.tz, self._local(c.now).date()
        last = await self._last_day(c)
        lines = []
        day = today
        while day <= last and len(lines) < DOCTOR_DAYS_SHOWN:
            spans = slots_view.doctor_spans(await self._shifts_from_now(c, day), asked.doctor, tz)
            if spans:
                label = render.day_label(day, today).lower()
                lines.append(texts.DOCTOR_DAY_LINE.format(day=label, spans=spans))
            day += timedelta(days=1)
        coming = [texts.DOCTOR_DAYS.format(doctor=asked.doctor), *lines]
        if not lines:
            coming = [texts.DOCTOR_NO_DAYS.format(doctor=asked.doctor)]
        if asked.day is None:
            return "\n".join(coming), asked
        name = render.sentence(render.wish_day(asked.day, today))
        short = short_name(asked.doctor)
        spans = slots_view.doctor_spans(await self._shifts_from_now(c, asked.day), asked.doctor, tz)
        if spans:
            return texts.DOCTOR_ON_DAY.format(day=name, doctor=short, spans=spans), asked
        off = texts.DOCTOR_OFF_DAY.format(day=name, doctor=short)
        return "\n".join([off, *coming]), Wish(doctor=asked.doctor)

    async def _free_time(self, c: Ctx, asked: Wish) -> tuple[str, Wish]:
        """(free time for a first visit, the wish for the booking button).

        The «nearest» rule of a first visit is used: a repeat visit has its own windows, so
        the exact time is shown after the scenario questions.
        """
        tz, today = self.settings.tz, self._local(c.now).date()
        spec = BookingSpec(
            visit="primary", service="", slot_policy="nearest", price_key="primary_visit"
        )
        allowed = await all_slots(self.schedule, spec, now=c.now, config=self.clinic.schedule)
        only_doctor = Wish(doctor=asked.doctor)
        of_doctor = slots_view.matching(allowed, only_doctor, tz)
        fitting = slots_view.matching(allowed, asked, tz)

        def with_doctor(text: str, shown: Sequence[Slot]) -> str:
            day_slots = slots_view.on_day(allowed, slots_view.local(shown[0], tz).date(), tz)
            doctors = slots_view.doctors_text(shown, day_slots, self.clinic.schedule, tz)
            return "\n".join([text, doctors, texts.FREE_NOTE])

        def nearest(slots: Sequence[Slot]) -> str:
            return slots_view.nearest_label(slots[0], self._local(c.now), tz) + "."

        if asked.day is None:
            if fitting:
                return with_doctor(nearest(fitting), fitting[:1]), asked
            if of_doctor:
                return with_doctor(nearest(of_doctor), of_doctor[:1]), only_doctor
            return texts.NO_SLOTS, Wish()
        when = render.sentence(render.wish_day(asked.day, today)) + ","
        if time_words := render.wish_time(asked):
            when += f" {time_words}"
        windows = self.clinic.schedule.repeat_windows
        shown = slots_view.times_of_day(fitting, asked.day, spec.slot_policy, windows, tz)
        if not shown:  # said plainly; the button does not carry a time that is not there
            text = texts.FREE_NONE.format(when=when)
            return (f"{text}\n{nearest(of_doctor)}" if of_doctor else text), only_doctor
        times = ", ".join(f"{slots_view.local(s, tz):%H:%M}" for s in shown[:FREE_TIMES_SHOWN])
        if len(shown) > FREE_TIMES_SHOWN:
            times = texts.FREE_MORE.format(times=times)
        return with_doctor(texts.FREE_ON.format(when=when, times=times), shown), asked

    # --- questions and administrators ------------------------------------------------------

    async def _answer_question(self, c: Ctx, text: str) -> None:
        """A question about the trauma centre, from kb/info; an administrator only by button."""
        known = await self._known(c)
        reading = await read_question(self._llm(c), text, known, on_partial=c.draft)
        self._reset(c)
        if reading.answer and not reading.offer_admin:
            c.s.handoff_reason, c.s.handoff_context = texts.QUESTION_REASON, text
            c.say(reading.answer, self._answer_buttons())
            return
        if not reading.answer and not reading.unavailable and self._is_request(c, text):
            c.say(
                texts.REQUEST_UNCLEAR, self._main_menu(c)
            )  # a request, not a question without data
            return
        c.s.step = "offer_admin"
        c.s.handoff_reason, c.s.handoff_context = texts.QUESTION_REASON, text
        if reading.answer:
            c.say(reading.answer, self._ask_admin_buttons())
        else:
            lead = texts.INFO_DOWN if reading.unavailable else texts.INFO_UNKNOWN
            c.say(lead, self._ask_admin_buttons())

    def _answer_buttons(self) -> list[list[Button]]:
        """Under an answer: «Помогло», or an administrator if it did not."""
        helped = Button(f"{texts.HELPED_ICON} {texts.HELPED}", "fb:yes", "success")
        return [[helped], [Button(texts.MENU_ADMIN, "fb:admin")], _home()]

    async def _on_answer_rating(self, c: Ctx, value: str) -> None:
        """Kept for /stats: whether the answer helped. No user and no text are stored."""
        helped = value == "yes"
        c.db.add(AnswerRating(helped=helped, source="answer", created_at=c.now))
        if helped:
            self._reset(c)
            c.say(texts.HELPED_THANKS, self._main_menu(c))
            return
        reason = c.s.handoff_reason or texts.QUESTION_REASON
        await self._handoff(c, reason, c.s.handoff_context)

    async def _on_unfinished(self, c: Ctx, _: str) -> None:
        """«Продолжить» under the reminder: the step the patient stopped at."""
        if c.s.step in WIZARD_STEPS:
            await self._render_screen(c)
        else:
            await self._show_menu(c)

    def _is_request(self, c: Ctx, text: str) -> bool:
        """«Запишите…», or a booking with a doctor, a day or a time: not a question."""
        local = self._local(c.now)
        found = parse_wish(text, self.clinic.schedule.doctors, local.date(), now=local.time())  # type: ignore[arg-type]
        return is_booking_order(text) or (found.booking and not found.wish.empty)

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
            c.say(texts.ADMIN_UNAVAILABLE, self._main_menu(c))
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
            c.say(f"{text}\n\n{self.kb.common.more_help.text}", self._main_menu(c))
            return
        if self._needs_consent(c):
            c.s.resume = "handoff"
            self._consent_prompt(c, "handoff")
            return
        if c.user.phone is None:
            c.s.resume, c.s.step = "handoff", "contact"
            self._ask_contact(c)
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
            await self._miss(c, c.s.step, misses.ADMIN, c.s.handoff_context or handoff.reason)
            c.turn.admin.append(AdminPost(body, handoff.id, reply_to=thread))
            template = texts.HANDOFF_ADDED
        text = render.handoff_message(self.clinic, now, handoff.id, why, template)
        self._handoff_done(c)
        c.say(f"{text}\n\n{self.kb.common.more_help.text}", self._main_menu(c))

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
        await self._miss(c, c.s.step, misses.ADMIN, c.s.handoff_context or reason)
        close = ((Button(texts.ADMIN_CLOSE, f"close:{handoff.id}", style="success"),),)
        c.turn.admin.append(
            AdminPost(await self._admin_text(c, handoff), handoff.id, buttons=close)
        )
        why = c.s.handoff_why
        text = render.handoff_message(self.clinic, self._local(c.now), handoff.id, why)
        self._handoff_done(c)
        c.say(
            f"{text}\n\n{self.kb.common.more_help.text}", self._main_menu(c), remove_keyboard=False
        )

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
            c.say(texts.STALE, [_home()])
            return
        self._reset(c)
        c.s.step, c.s.reply_handoff = "admin_reply", handoff.id
        c.say(texts.ASK_ADMIN_REPLY, [_home()])

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
        c.say(texts.ADMIN_REPLY_SENT, self._main_menu(c))

    # --- /forget: withdraw consent ---------------------------------------------------------

    async def _forget_ask(self, c: Ctx) -> None:
        self._reset(c)
        c.say(
            texts.FORGET_ASK,
            [[Button(texts.FORGET_YES, "fg:yes", "danger")], [Button(texts.FORGET_NO, "fg:no")]],
        )

    async def _on_forget(self, c: Ctx, value: str) -> None:
        if value == "ask":  # «Удалить мои данные» under «о боте»
            await self._forget_ask(c)
            return
        if value != "yes":
            c.say(texts.FORGET_KEPT, self._main_menu(c))
            return
        user_id = c.user.id
        bookings = (await c.db.scalars(select(Booking).where(Booking.user_id == user_id))).all()
        cards = [b.id for b in bookings if b.admin_message_id]
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
        items = []
        if handoff_ids:
            numbers = ", ".join(f"№ {n}" for n in handoff_ids)
            items.append(texts.FORGET_HANDOFFS.format(numbers=numbers))
        if cards:
            items.append(texts.FORGET_BOOKINGS.format(numbers=", ".join(f"№ {n}" for n in cards)))
        if self.settings.admin_chat_id and items:
            text = texts.FORGET_ADMIN.format(user=user_id, items="; ".join(items))
            c.turn.admin.append(AdminPost(text))
        c.say(texts.FORGOTTEN, self._main_menu(c))
