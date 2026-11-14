"""Five safeguards (14.11, part 3): the birth date against «для себя / для ребёнка», more
emergency phrases, a handler for errors, «Не был на приёме», limits.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.bot import render, texts
from app.bot.conversation import UserRef
from app.config import PROJECT_ROOT, ClinicData, Emergency
from app.db.models import Booking, Patient, User
from app.flow.loader import KnowledgeBase
from tests import intent_cases as m
from tests.chat_harness import Chat
from tests.conftest import NOW
from tests.fake_deepseek import FakeDeepSeek
from tests.test_conversation import book_dislocation, bookings
from tests.test_intents import to_step
from tests.test_manual_findings_3 import book_again, booked
from tests.test_wish_flow import through_dislocation

GUARDIAN = texts.CONSENT_ITEM_GUARDIAN


async def session(chat: Chat) -> dict:
    async with chat.conversation.db() as s:
        return dict((await s.get(User, chat.user.id)).session or {})


async def patients(chat: Chat) -> list[Patient]:
    from sqlalchemy import select

    async with chat.conversation.db() as s:
        return list((await s.scalars(select(Patient).order_by(Patient.id))).all())


async def to_birth(chat: Chat, audience: str, age: str | None = None) -> None:
    """A dislocation up to «Дата рождения пациента»: the time is chosen, the name is given."""
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")
    await chat.press(audience)
    if age:
        await chat.say(age)
    for label in ("Бытовая травма", "В течение 7 дней", "Нет, не обращались"):
        await chat.press(label)
    await chat.pick_slot("Завтра")
    await chat.press(texts.CONSENT_YES)
    await chat.say("Белов Артём Игоревич")
    assert texts.ASK_BIRTH in chat.text


# --- 1. the birth date against «для себя / для ребёнка» ------------------------------------------


async def test_adult_chosen_but_the_patient_is_sixteen(make_chat) -> None:
    chat = make_chat()
    await to_birth(chat, texts.AUDIENCE_SELF)
    await chat.say("09.09.2010")
    line, screen = chat.replies
    assert line.text == texts.AGE_IS_CHILD.format(age="16 лет") and not line.buttons
    assert GUARDIAN in screen.text  # the same booking, now as a child's: the guardian agrees
    assert await bookings(chat.conversation.db) == []  # nothing is booked on the adult branch
    state = await session(chat)
    assert state["dialog"]["facts"]["audience"] == "child"
    assert state["dialog"]["age_months"] == 194 and state["dialog"]["outcome_id"] == "book_primary"
    (patient,) = await patients(chat)
    assert (patient.audience, patient.last_name) == ("child", "Белов")
    await chat.press(texts.CONSENT_YES)
    await chat.contact()
    (booking,) = await bookings(chat.conversation.db)
    assert booking.patient_id == patient.id and "Артём Игоревич записан" in chat.text


async def test_child_chosen_but_the_patient_is_an_adult(make_chat) -> None:
    chat = make_chat()
    await to_birth(chat, texts.AUDIENCE_CHILD, "8 лет")
    await chat.say("12.03.1985")
    line, ask = chat.replies
    assert line.text == texts.AGE_IS_ADULT.format(age="41 год")
    assert ask.ask_contact  # the answers stay, the booking goes on as an adult's
    state = await session(chat)
    assert state["dialog"]["facts"]["audience"] == "adult" and state["dialog"]["age_months"] is None
    assert [p.audience for p in await patients(chat)] == ["adult"]
    await chat.contact()
    assert len(await bookings(chat.conversation.db)) == 1


async def test_the_age_said_differs_from_the_birth_date_by_more_than_a_year(make_chat) -> None:
    chat = make_chat()
    await to_birth(chat, texts.AUDIENCE_CHILD, "8 лет")
    await chat.say("12.05.2023")
    line, ask = chat.replies
    assert line.text == texts.AGE_DIFFERS.format(age="3 года", said="8 лет")
    assert ask.ask_contact and (await session(chat))["dialog"]["age_months"] == 42


async def test_a_baby_under_six_months_by_the_birth_date_is_routed(make_chat) -> None:
    chat = make_chat()
    await to_birth(chat, texts.AUDIENCE_CHILD, "1 год")
    await chat.say("10.07.2026")
    line, route = chat.replies
    assert line.text == texts.AGE_DIFFERS.format(age="4 месяца", said="1 год")
    assert "Областная детская клиническая больница № 1" in route.text
    assert await bookings(chat.conversation.db) == [] and not chat.days()
    assert (await session(chat))["dialog"]["scenario_id"] == "infant_under_6m"
    assert texts.MENU_BOOK in chat.labels()


@pytest.mark.parametrize(
    ("audience", "age", "born"),
    [
        (texts.AUDIENCE_SELF, None, "12.03.1985"),
        (texts.AUDIENCE_CHILD, "8 лет", "20.02.2018"),
        (texts.AUDIENCE_CHILD, "8 лет", "30.12.2017"),  # almost nine: within a year
        (texts.AUDIENCE_CHILD, "17 лет", "01.12.2008"),  # almost eighteen is still a child
    ],
)
async def test_a_birth_date_that_fits_changes_nothing(make_chat, audience, age, born) -> None:
    chat = make_chat()
    await to_birth(chat, audience, age)
    await chat.say(born)
    (ask,) = chat.replies
    assert ask.ask_contact


# --- 2. emergency phrases -----------------------------------------------------------------------

NEW_PHRASES = (
    "не дышит",
    "задыхается",
    "судороги",
    "синеет",
    "кость торчит",
    "не чувствую ногу",
    "не чувствую руку",
    "ударило током",
)
SAID = (
    "ребёнок не дышит",
    "муж задыхается, что делать",
    "у дочки судороги!",
    "он синеет",
    "упал, кость торчит из ноги",
    "после падения не чувствую ногу",
    "не чувствую руку совсем",
    "меня ударило током",
    "сын перестал дышать",
    "посинели губы",
    "нет пульса",
)
STEPS = (m.MENU, m.COMPLAINT, m.AUDIENCE, m.AGE, m.QUESTION, m.DAYS, m.PERIODS, m.TIMES,
         m.CONSENT, m.NAME, m.BIRTH, m.PHONE, m.PAYMENT, m.ASK)  # fmt: skip


def test_new_emergency_phrases_are_in_the_config(clinic: ClinicData) -> None:
    phrases = clinic.clinic.emergency.phrases
    assert set(NEW_PHRASES) <= set(phrases)
    requests = (PROJECT_ROOT / "docs" / "clinic_requests.md").read_text(encoding="utf-8")
    for phrase in phrases:  # the whole list is with the clinic for agreement
        assert f"«{phrase}»" in requests, phrase


def test_an_everyday_word_cannot_be_an_emergency_trigger() -> None:
    for word in ("кровь", "боль", "упал"):
        with pytest.raises(ValidationError):
            Emergency(reply="103", phrases=("не дышит", word))
    Emergency(reply="103", phrases=("задыхается", "судороги", "синеет"))


@pytest.mark.parametrize("step", STEPS)
async def test_emergency_phrases_work_on_every_step(
    make_chat, kb: KnowledgeBase, clinic: ClinicData, step: str
) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await to_step(chat, step)
    saved = await session(chat)
    for said in SAID:
        async with chat.conversation.db() as s:
            (await s.get(User, chat.user.id)).session = saved
            await s.commit()
        await chat.say(said)
        assert chat.text == render.emergency_message(clinic), (said, step)
        assert fake.requests == [] and texts.MENU_BOOK in chat.labels(), (said, step)


async def test_a_bruised_toe_is_not_an_emergency(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek({"type": "complaint", "area": "limb", "scenario": None})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("ушибла ногу, палец посинел и опух")
    assert "103" not in chat.text and (await session(chat))["wizard"]


# --- 3. an error inside a handler ---------------------------------------------------------------


class _Bot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, object]] = []

    async def send_message(self, chat_id: int, text: str, reply_markup=None, **kw):  # type: ignore[no-untyped-def]
        self.sent.append((chat_id, text, reply_markup))


def _failed(exc: Exception, chat_id: int = 1001):  # type: ignore[no-untyped-def]
    message = SimpleNamespace(chat=SimpleNamespace(id=chat_id))
    update = SimpleNamespace(message=message, callback_query=None)
    return SimpleNamespace(update=update, exception=exc)


def _raise(exc: Exception) -> Exception:
    try:
        raise exc
    except Exception as caught:
        return caught


async def test_an_error_tells_the_patient_and_notes_the_admins_once(tmp_path) -> None:
    from app.bot.telegram import Failures

    now = [0.0]
    failures = Failures(admin_chat_id=-100, clock=lambda: now[0])
    bot = _Bot()
    secret = "Иванов Иван +79123456789"
    await failures.handle(_failed(_raise(KeyError(secret))), bot)
    patient, note = bot.sent
    assert patient[0] == 1001 and patient[1] == texts.FAILED
    labels = [b.text for row in patient[2].inline_keyboard for b in row]
    assert labels == [texts.MENU, texts.MENU_ADMIN]
    assert note[0] == -100 and note[1].startswith("Ошибка в боте: KeyError")
    assert "test_safeguards.py" in note[1] and secret not in note[1] and "1001" not in note[1]

    now[0] += 300  # the same error five minutes later: the patient is told, the group is not
    await failures.handle(_failed(_raise(KeyError("другое"))), bot)
    assert [chat for chat, *_ in bot.sent[2:]] == [1001]
    await failures.handle(_failed(_raise(ValueError("x"))), bot)  # another kind of error: a note
    assert [chat for chat, *_ in bot.sent[3:]] == [1001, -100]
    now[0] += 601
    await failures.handle(_failed(_raise(KeyError("ещё"))), bot)  # ten minutes passed: a note again
    assert [chat for chat, *_ in bot.sent[5:]] == [1001, -100]


async def test_an_error_in_the_admin_group_is_not_answered_there_as_to_a_patient() -> None:
    from app.bot.telegram import Failures

    failures = Failures(admin_chat_id=-100, clock=lambda: 0.0)
    bot = _Bot()
    await failures.handle(_failed(_raise(RuntimeError("boom")), chat_id=-100), bot)
    assert [(chat, text.split(":")[0]) for chat, text, _ in bot.sent] == [(-100, "Ошибка в боте")]


def test_dispatcher_has_the_error_handler(kb, clinic, schedule, tmp_path) -> None:
    from app.bot.access import Access
    from app.bot.conversation import Conversation
    from app.bot.telegram import Failures, build_dispatcher
    from app.db.session import Database
    from tests.chat_harness import make_settings

    settings = make_settings()
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}")
    conversation = Conversation(
        kb=kb, clinic=clinic, settings=settings, db=db, schedule=schedule, assistant=None
    )

    async def delivery(*args, **kwargs):  # type: ignore[no-untyped-def]
        return None

    dp = build_dispatcher(settings, conversation, delivery, Access(settings, db))
    (handler,) = dp.errors.handlers
    assert isinstance(handler.callback.__self__, Failures) and handler.awaitable


# --- 4. «Не был на приёме» ----------------------------------------------------------------------


async def to_survey(chat: Chat) -> Booking:
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    (booking,) = await bookings(chat.conversation.db)
    await chat.conversation.register_booking_card(booking.id, 700)
    for _ in range(4):
        await chat.demo_next()
        if chat.text == texts.AFTER_VISIT:
            break
    assert chat.text == texts.AFTER_VISIT
    return booking


async def test_missed_visit_offers_a_new_booking_and_no_control_visit(make_chat) -> None:
    chat = make_chat(bookings_to_admin_chat=True)
    await to_survey(chat)
    assert chat.labels() == [
        texts.AFTER_VISIT_OK,
        texts.AFTER_VISIT_QUESTION,
        texts.AFTER_VISIT_MISSED,
    ]
    posted = len(chat.admin)
    await chat.press(texts.AFTER_VISIT_MISSED)
    assert chat.text == texts.MISSED and chat.labels() == [texts.MENU_BOOK, texts.MENU_HOME]
    assert "Контрольный приём" not in chat.text
    (booking,) = await bookings(chat.conversation.db)
    assert booking.survey == "missed"
    (note,) = chat.admin[posted:]  # a mark under the booking's card in the group
    assert (note.text, note.reply_to) == (texts.ADMIN_SURVEY["missed"], 700)
    await chat.demo_next()  # nothing else is waiting: no control visit, no rating
    assert texts.CONTROL_YES not in chat.labels() and "Оцените" not in chat.text
    await chat.press(texts.MENU_BOOK)  # «Записать Вас заново?»: a new booking from step 1
    assert "С чем обращаетесь?" in chat.text and (await session(chat))["wizard"]


async def test_the_visit_that_went_well_is_as_before(make_chat) -> None:
    chat = make_chat()
    await to_survey(chat)
    await chat.press(texts.AFTER_VISIT_OK)
    assert texts.CONTROL_YES in chat.labels()


# --- 5. limits ----------------------------------------------------------------------------------


async def test_no_more_than_three_bookings_ahead(make_chat) -> None:
    chat = await booked(make_chat, "Завтра", "Вс")
    await chat.press(texts.MENU_BOOK)
    await through_dislocation(chat)
    assert chat.replies[-1].text == texts.TOO_MANY_BOOKINGS.format(limit=3)
    assert chat.labels()[-3:] == [texts.MENU_MY, texts.MENU_ADMIN, texts.MENU_HOME]
    assert not chat.days() and (await session(chat))["step"] == "idle"
    assert len([b for b in await bookings(chat.conversation.db) if b.status == "active"]) == 3
    await chat.press(texts.MENU_MY)  # the way out: cancel or move one of them
    assert chat.text.startswith(texts.MY_TITLE)
    await chat.say("перенеси запись на субботу")  # moving is not a new booking
    assert chat.text == texts.MOVE_WHICH


async def test_the_booking_limit_is_a_setting(make_chat) -> None:
    chat = await booked(make_chat, "Завтра", "Вс", booking_limit=4)
    await book_again(chat, "Сб")
    assert len(await bookings(chat.conversation.db)) == 4
    await chat.press(texts.MENU_BOOK)
    await through_dislocation(chat)
    assert chat.replies[-1].text == texts.TOO_MANY_BOOKINGS.format(limit=4)


async def test_a_cancelled_booking_frees_a_place(make_chat) -> None:
    chat = await booked(make_chat, "Завтра", "Вс")
    await chat.say("отмени запись на воскресенье")
    await chat.press(texts.CANCEL_YES)
    await book_again(chat, "Сб")
    assert len([b for b in await bookings(chat.conversation.db) if b.status == "active"]) == 3


async def test_deepseek_calls_per_minute_are_limited_for_one_user(make_chat, kb, clock) -> None:
    fake = FakeDeepSeek(*[{"type": "offtopic"}] * 10)
    chat = make_chat(assistant=fake.assistant(kb), llm_per_minute=3)
    other = Chat(
        chat.conversation, UserRef(id=2002, chat_id=2002, username=None, display_name=None)
    )
    await chat.start()
    for n in range(3):
        await chat.say(f"расскажи анекдот {n}")
        assert chat.text == texts.OFFTOPIC
    await chat.say("расскажи анекдот ещё")  # the fourth within a minute: DeepSeek is not called
    assert len(fake.requests) == 3
    assert chat.replies[0].text == texts.TOO_MANY_MESSAGES and chat.buttons()
    await other.start()
    await other.say("расскажи анекдот")  # another patient is served
    assert other.text == texts.OFFTOPIC and len(fake.requests) == 4
    clock.now = NOW + timedelta(seconds=61)
    await chat.say("расскажи анекдот через минуту")
    assert chat.replies[0].text == texts.OFFTOPIC and len(fake.requests) == 5


async def test_buttons_work_while_the_limit_is_on(make_chat, kb) -> None:
    fake = FakeDeepSeek(*[{"type": "offtopic"}] * 5)
    chat = make_chat(assistant=fake.assistant(kb), llm_per_minute=1)
    await chat.start()
    await chat.say("расскажи анекдот")
    await chat.say("болит рука")  # over the limit: the word list, as when DeepSeek is down
    assert chat.replies[0].text == texts.TOO_MANY_MESSAGES
    assert "Что случилось с рукой или ногой?" in chat.text and len(fake.requests) == 1
    await chat.press("Упал, ударился, подвернул")
    assert "На что больше похоже?" in chat.text
