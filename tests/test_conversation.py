"""The whole patient path without Telegram (cases 2, 17, 18, 20 and the stage 3/4 decisions)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.bot import texts
from app.bot.conversation import Conversation
from app.config import ClinicData, Settings
from app.db.models import Booking, Handoff, Reminder
from app.db.session import Database
from app.flow.loader import KnowledgeBase
from app.schedule.mock import MockSchedule
from tests.chat_harness import PATIENT, Chat, Clock
from tests.conftest import NOW
from tests.fake_deepseek import FakeDeepSeek

ADMIN_CHAT = -1001234567890


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}")
    await database.init()
    yield database
    await database.close()


@pytest.fixture
def clock() -> Clock:
    return Clock(NOW)


def settings(**overrides: object) -> Settings:
    base = {"schedule_provider": "mock", "demo_mode": True, "admin_chat_id": ADMIN_CHAT}
    return Settings(_env_file=None, **(base | overrides))  # type: ignore[arg-type]


@pytest.fixture
def make_chat(kb: KnowledgeBase, clinic: ClinicData, db: Database, schedule: MockSchedule, clock):
    def _make(assistant=None, **overrides: object) -> Chat:
        conversation = Conversation(
            kb=kb,
            clinic=clinic,
            settings=settings(**overrides),
            db=db,
            schedule=schedule,
            assistant=assistant,
            clock=clock,
        )
        return Chat(conversation)

    return _make


async def book_dislocation(chat: Chat, *, slot: str = "Чт 12.11") -> None:
    """Menu → «Вывих» by buttons → slot → consent → name → birth date → contact."""
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press(texts.TOPIC_LIST)
    await chat.press("Вывих")
    await chat.press("Для себя")
    await chat.press("Бытовая травма")
    await chat.press("В течение 7 дней")
    await chat.press("Нет, не обращались")
    await chat.press(slot)
    await chat.press(texts.CONSENT_YES)
    await chat.say("петров иван сергеевич")
    await chat.say("12.03.1985")
    await chat.contact()


async def bookings(db: Database) -> list[Booking]:
    async with db() as s:
        return list((await s.scalars(select(Booking).order_by(Booking.id))).all())


async def test_case_17_full_demo_path(make_chat, db: Database, schedule: MockSchedule) -> None:
    chat = make_chat()
    await chat.start()
    assert chat.text.startswith("Добрый день! Травмпункт «УГМК-Здоровье», я виртуальный помощник")
    assert texts.DEMO_GREETING in chat.text
    assert chat.labels() == [texts.MENU_BOOK, texts.MENU_ASK, texts.MENU_MY, texts.MENU_ADMIN]

    await chat.press(texts.MENU_BOOK)
    await chat.say("вывихнул плечо")  # no LLM configured → topic menu
    assert texts.PICK_TOPIC in chat.text and "Вывих" in chat.labels()
    await chat.press("Вывих")
    assert chat.labels() == ["Для себя", "Для ребёнка (до 18 лет)"]
    await chat.press("Для себя")
    await chat.press("Бытовая травма")
    await chat.press("В течение 7 дней")
    await chat.press("Нет, не обращались")
    assert "вывих, врач попробует его вправить" in chat.text
    assert "Пожалуйста, подождите минуту" in chat.text
    slots = [label for label in chat.labels() if label.startswith("Чт 12.11")]
    assert slots and slots[0].startswith("Чт 12.11, 14:30 ·")  # not earlier than 13:00

    await chat.press(slots[0])
    assert texts.CONSENT in chat.text
    await chat.press(texts.CONSENT_YES)
    assert "фамилия, имя, отчество" in chat.text
    await chat.say("петров иван сергеевич")
    await chat.say("12.03.1985")
    assert chat.replies[-1].ask_contact
    await chat.contact()
    assert chat.replies[0].remove_keyboard
    assert "Иван Сергеевич записан на приём травматолога-ортопеда" in chat.text
    assert "ул. Шейнкмана, 113/2, вход через детское отделение" in chat.text
    assert "С собой возьмите паспорт. При первом обращении — ещё СНИЛС." in chat.text
    assert texts.DEMO_BOOKING in chat.text
    await chat.press(texts.PAYMENT_INSURANCE)

    first = (await bookings(db))[0]
    assert (first.payment, first.cycle, first.status) == ("insurance", 1, "active")
    assert first.slot_id not in {s.id for s in await schedule.free_slots(NOW, NOW + timedelta(1))}

    # notifications: 14:30 today → no «за 24 часа»; «за 2 часа», then «Как прошёл приём?»
    await chat.demo_next()
    assert chat.text.startswith("Скоро приём в травмпункте: в четверг, 12 ноября, в 14:30.")
    assert "завтра" not in chat.text and "Через 2 часа" not in chat.text
    assert "подземный паркинг" in chat.text
    assert chat.labels() == [texts.CONFIRM, texts.MOVE, texts.CANCEL]
    await chat.press(texts.CONFIRM)
    await chat.demo_next()
    assert chat.text == texts.AFTER_VISIT
    await chat.press(texts.AFTER_VISIT_OK)
    assert texts.OFFER_CONTROL in chat.text

    # control visit: 04-09 with «первично у нас» known → only «больничный?»
    await chat.press(texts.CONTROL_YES)
    assert chat.text == "Открывали больничный лист?"
    await chat.press("Нет")
    offered = [label for label in chat.labels() if label[:2] in texts.WEEKDAYS_SHORT]
    assert offered
    await chat.press(offered[0])  # patient, consent and phone are known: booked at once
    assert "записан на приём" in chat.text and texts.CONSENT not in chat.text
    await chat.press(texts.PAYMENT_SELF)

    control = (await bookings(db))[1]
    assert (control.cycle, control.parent_id, control.visit) == (2, first.id, "repeat")
    assert control.slot_policy == "repeat_window"
    seen = []
    for _ in range(3):  # «за 24 ч» / «за 2 ч» only if still ahead, then the survey
        await chat.demo_next()
        seen.append(chat.text.split(",")[0])
        if chat.text == texts.FEELING:
            break
    assert chat.text == texts.FEELING, seen
    await chat.press(texts.FEELING_OK)
    assert chat.labels() == ["1", "2", "3", "4", "5"]
    await chat.press("5")
    assert texts.RATED in chat.text
    assert (await bookings(db))[1].rating == 5
    await chat.demo_next()
    assert texts.DEMO_NONE in chat.text


async def test_day_before_reminder_lists_documents(make_chat, schedule: MockSchedule) -> None:
    chat = make_chat()
    await book_dislocation(chat)
    tomorrow = next(
        s
        for s in await schedule.free_slots(NOW + timedelta(days=1), NOW + timedelta(days=2))
        if not s.repeat_window and not s.repeat_cabinet
    )
    await chat.press(texts.PAYMENT_SELF)
    await chat.press(texts.MENU_MY)
    await chat.press(texts.MOVE)
    await chat.data(f"s:{tomorrow.id}")
    assert chat.text.startswith("Готово, запись перенесена на 13 ноября (пятница)")
    await chat.demo_next()
    assert chat.text.startswith("Напоминаем: в пятницу, 13 ноября, в ")
    assert "Пациент: Петров Иван Сергеевич" in chat.text
    assert "паспорт" in chat.text and "СНИЛС" in chat.text


async def test_case_18_reschedule_and_cancel_free_the_slot(
    make_chat, db: Database, schedule: MockSchedule
) -> None:
    chat = make_chat()
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    old = (await bookings(db))[0].slot_id

    await chat.press(texts.MENU_MY)
    assert "Петров Иван Сергеевич" in chat.text
    await chat.press(texts.MOVE)
    new_label = next(label for label in chat.labels() if label[:2] in texts.WEEKDAYS_SHORT)
    await chat.press(new_label)
    moved = (await bookings(db))[0]
    free = {s.id for s in await schedule.free_slots(NOW, NOW + timedelta(days=14))}
    assert moved.slot_id != old and old in free and moved.slot_id not in free

    await chat.press(texts.MENU_MY)
    await chat.press(texts.CANCEL)
    await chat.press(texts.CANCEL_YES)
    assert texts.CANCELLED in chat.text
    cancelled = (await bookings(db))[0]
    assert cancelled.status == "cancelled"
    assert cancelled.slot_id in {s.id for s in await schedule.free_slots(NOW, NOW + timedelta(14))}
    async with db() as s:
        left = (
            await s.scalars(select(Reminder).where(~Reminder.cancelled, Reminder.sent_at.is_(None)))
        ).all()
    assert left == []
    await chat.press(texts.MENU_MY)
    assert texts.MY_EMPTY in chat.text


async def test_case_2_handoff_asks_consent_and_contact_then_reply_goes_back(
    make_chat, db: Database
) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press(texts.TOPIC_LIST)
    await chat.press("Перелом")
    await chat.press("Для ребёнка (до 18 лет)")
    await chat.say("8 лет")
    await chat.press("Бытовая травма")
    await chat.press("Более 7 дней назад")
    assert texts.CONSENT_HANDOFF in chat.text  # consent before «Поделиться контактом»
    assert texts.CONSENT_ITEM_GUARDIAN in chat.text and texts.CONSENT_WITHDRAW in chat.text
    assert chat.admin == []
    await chat.press(texts.CONSENT_YES)
    assert chat.replies[-1].ask_contact
    await chat.contact("79123456789")
    assert "Передаю Ваше обращение администратору. С Вами свяжутся." in chat.text
    (post,) = chat.admin
    for part in (
        "Ребёнок: записать на ближайший плановый приём к травматологу-ортопеду",
        "Сценарий: 4.7",
        "Пациент: ребёнок, 8 лет",
        "• Когда была получена травма? — Более 7 дней назад",
        "Телефон: +79123456789",
        "@tester",
        "реплаем",
    ):
        assert part in post.text, part

    await chat.conversation.register_admin_message(555, post.handoff_id)
    answer = await chat.conversation.admin_reply(555, "Перезвоним сегодня до 18:00")
    assert answer is not None
    (reply,) = answer.replies
    assert reply.chat_id == PATIENT.chat_id
    assert reply.text == "Ответ администратора:\nПерезвоним сегодня до 18:00"
    chat.turn = answer
    await chat.press(texts.ADMIN_ANSWER_REPLY)
    await chat.say("Спасибо, жду звонка")
    assert chat.admin[-1].reply_to == 555
    assert chat.admin[-1].text == f"Пациент (обращение № {post.handoff_id}): Спасибо, жду звонка"
    assert await chat.conversation.admin_reply(999, "кому?") is None


async def test_handoff_outside_working_hours(make_chat, clock: Clock) -> None:
    clock.now = NOW.replace(hour=23)
    chat = make_chat()
    await chat.start()
    assert chat.text.startswith("Доброй ночи!")
    await chat.press(texts.MENU_ADMIN)
    await chat.press(texts.CONSENT_YES)
    await chat.contact()
    assert "С Вами свяжутся в рабочее время — с 08:00 до 20:00." in chat.text


async def test_no_consent_no_personal_data(make_chat, db: Database) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_ADMIN)
    await chat.press(texts.CONSENT_NO)
    assert texts.CONSENT_DECLINED in chat.text
    assert not any(r.ask_contact for r in chat.replies)
    async with db() as s:
        assert (await s.scalars(select(Handoff))).all() == []


async def test_contact_of_someone_else_is_refused(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_ADMIN)
    await chat.press(texts.CONSENT_YES)
    await chat.contact("+79990000000", owner=4242)
    assert texts.CONTACT_NOT_OWN in chat.text and chat.admin == []


async def test_case_20_emergency_interrupts_any_step(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.say("Кровь хлещет, не останавливается")
    assert chat.text == "При угрозе жизни звоните 103 или 112."
    assert texts.MENU_BOOK in chat.labels()


async def test_child_age_from_complaint_is_confirmed_with_buttons(make_chat, kb) -> None:
    fake = FakeDeepSeek(
        {
            "intent": "complaint",
            "scenario": "dislocation",
            "facts": {"assault": False, "injury_age": "within_7_days", "seen_doctor": "no"},
        }
    )
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("у сына 8 лет вывих, упал вчера дома, к врачу не ходили")
    assert chat.text == "Правильно понимаю: запись для ребёнка, 8 лет?"
    assert chat.labels() == ["Да, ребёнок 8 лет", "Нет"]
    await chat.press("Да, ребёнок 8 лет")
    assert "Пожалуйста, подождите минуту" in chat.text  # all answers known → straight to slots


async def test_child_age_rejected_asks_who(make_chat, kb) -> None:
    fake = FakeDeepSeek({"intent": "complaint", "scenario": "burns", "facts": {}})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("дочке 3 года, обожглась")
    await chat.press("Нет")
    assert chat.labels() == ["Для себя", "Для ребёнка (до 18 лет)"]


async def test_free_text_answer_falls_back_to_buttons(make_chat, kb) -> None:
    chat = make_chat(assistant=FakeDeepSeek(500, 500).assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press(texts.TOPIC_LIST)
    await chat.press("Ожог")
    await chat.press("Для себя")
    await chat.say("обожгла руку")
    assert chat.text.startswith(texts.PICK_BUTTON)
    assert chat.labels() == ["Лицо", "Другая часть тела"]


async def test_questions_are_answered_from_kb_info(make_chat, kb) -> None:
    answer = "Травмпункт работает круглосуточно по предварительной записи."
    fake = FakeDeepSeek({"found": True, "answer": answer}, {"found": False})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_ASK)
    await chat.say("Во сколько вы работаете?")
    assert chat.text.startswith(answer)
    await chat.press(texts.MENU_ASK)
    await chat.say("Делаете МРТ колена?")
    assert texts.INFO_UNKNOWN in chat.text
    await chat.press(texts.HANDOFF_YES)
    await chat.press(texts.CONSENT_YES)
    await chat.contact()
    assert "Сообщение пациента: Делаете МРТ колена?" in chat.admin[-1].text


async def test_returning_patient_is_picked_not_retyped(make_chat, db: Database) -> None:
    chat = make_chat()
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    await chat.press(texts.MENU_BOOK)
    await chat.press(texts.TOPIC_LIST)
    await chat.press("Перелом")
    await chat.press("Для себя")
    await chat.press("Бытовая травма")
    await chat.press("В течение 7 дней")
    await chat.press("Нет, не обращались")
    await chat.press(next(lbl for lbl in chat.labels() if lbl[:2] in texts.WEEKDAYS_SHORT))
    assert chat.labels() == ["Петров Иван, 12.03.1985", texts.PATIENT_NEW]
    await chat.press("Петров Иван, 12.03.1985")
    assert "Иван Сергеевич записан" in chat.text
    assert len(await bookings(db)) == 2


async def test_due_reminders_are_sent_once(make_chat, clock: Clock) -> None:
    chat = make_chat()
    await book_dislocation(chat)
    clock.now = NOW.replace(hour=12, minute=31)  # 2 h before 14:30
    turns = await chat.due()
    assert len(turns) == 1 and turns[0].replies[0].chat_id == PATIENT.chat_id
    assert await chat.due() == []


async def test_demo_next_only_in_demo_mode(make_chat) -> None:
    chat = make_chat(demo_mode=False)
    await chat.demo_next()
    assert chat.text == texts.DEMO_ONLY


async def test_without_admin_chat_the_bot_says_so(make_chat) -> None:
    chat = make_chat(admin_chat_id=None)
    await chat.start()
    await chat.press(texts.MENU_ADMIN)
    assert texts.ADMIN_UNAVAILABLE in chat.text


async def test_restart_keeps_booked_slots_taken(make_chat, kb, clinic, db, clock) -> None:
    chat = make_chat()
    await book_dislocation(chat)
    taken = (await bookings(db))[0].slot_id
    from app.schedule.generate import generate
    from tests.conftest import FIRST_DAY, TZ

    fresh = MockSchedule(generate(FIRST_DAY, 14, clinic.schedule), tz=TZ, slot_minutes=30)
    restarted = Conversation(
        kb=kb,
        clinic=clinic,
        settings=settings(),
        db=db,
        schedule=fresh,
        assistant=None,
        clock=clock,
    )
    await restarted.restore_schedule()
    assert taken not in {s.id for s in await fresh.free_slots(NOW, NOW + timedelta(days=1))}


async def test_admin_from_slot_choice_keeps_the_scenario_context(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press(texts.TOPIC_LIST)
    await chat.press("Ожог")
    await chat.press("Для себя")
    await chat.press("Другая часть тела")
    await chat.press("Кипяток, пар, огонь, солнце")
    await chat.press("Менее суток назад")
    await chat.press("Нет, не обращались")
    await chat.press(texts.MENU_ADMIN)
    await chat.press(texts.CONSENT_YES)
    await chat.say("+7 912 345 67 89")  # typed instead of the contact button
    assert chat.replies[0].remove_keyboard
    post = chat.admin[-1].text
    assert (
        "Сценарий: 4.6 «Ожоги»" in post
        and "• Как получили ожог? — Кипяток, пар, огонь, солнце" in post
    )


async def test_child_booking_asks_the_guardian_item_once(make_chat, db: Database) -> None:
    chat = make_chat()
    await book_dislocation(chat)  # adult: consent without the guardian item
    await chat.press(texts.PAYMENT_SELF)
    await chat.press(texts.MENU_BOOK)
    await chat.press(texts.TOPIC_LIST)
    await chat.press("Ожог")
    await chat.press("Для ребёнка (до 18 лет)")
    await chat.say("5 лет")
    await chat.press("Другая часть тела")
    await chat.press("Кипяток, пар, огонь, солнце")
    await chat.press("Менее суток назад")
    await chat.press("Нет, не обращались")
    await chat.press(next(lbl for lbl in chat.labels() if lbl[:2] in texts.WEEKDAYS_SHORT))
    assert texts.CONSENT_ITEM_GUARDIAN in chat.text  # adult consent is not enough for a child
    await chat.press(texts.CONSENT_YES)
    await chat.say("Петрова Анна Ивановна")
    await chat.say("01.02.2021")
    assert "Анна Ивановна записана" in chat.text  # phone already known
    await chat.press(texts.PAYMENT_SELF)
    await chat.press(texts.MENU_BOOK)
    await chat.press(texts.TOPIC_LIST)
    await chat.press("Ожог")
    await chat.press("Для ребёнка (до 18 лет)")
    await chat.say("5 лет")
    await chat.press("Другая часть тела")
    await chat.press("Кипяток, пар, огонь, солнце")
    await chat.press("Менее суток назад")
    await chat.press("Нет, не обращались")
    await chat.press(next(lbl for lbl in chat.labels() if lbl[:2] in texts.WEEKDAYS_SHORT))
    assert chat.labels() == ["Петрова Анна, 01.02.2021", texts.PATIENT_NEW]  # no consent again


async def test_consent_text_mentions_withdrawal(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_ADMIN)
    assert texts.CONSENT_WITHDRAW in chat.text and "/forget" in chat.text
    assert texts.CONSENT_ITEM_GUARDIAN not in chat.text


async def test_forget_deletes_data_after_confirmation(
    make_chat, db: Database, schedule: MockSchedule
) -> None:
    from app.db.models import Patient, User

    chat = make_chat()
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    await chat.press(texts.MENU_ADMIN)  # one handoff to the admin chat
    slot = (await bookings(db))[0].slot_id

    chat._take(await chat.conversation.forget(chat.user))
    assert chat.text == texts.FORGET_ASK
    await chat.press(texts.FORGET_NO)
    assert texts.FORGET_KEPT in chat.text and len(await bookings(db)) == 1

    chat._take(await chat.conversation.forget(chat.user))
    await chat.press(texts.FORGET_YES)
    assert texts.FORGOTTEN in chat.text
    assert "отозвал согласие" in chat.admin[-1].text and "№ 1" in chat.admin[-1].text
    async with db() as s:
        for model in (User, Patient, Booking, Reminder, Handoff):
            assert (await s.scalars(select(model))).all() == [], model
    assert slot in {s.id for s in await schedule.free_slots(NOW, NOW + timedelta(days=1))}

    await chat.start()  # a new start: consent is asked again
    await chat.press(texts.MENU_ADMIN)
    assert texts.CONSENT_HANDOFF in chat.text
