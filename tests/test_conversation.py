"""The whole patient path without Telegram (cases 2, 17, 18, 20 and the stage 3/4 decisions)."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.bot import texts
from app.bot.conversation import Conversation
from app.db.models import Booking, Handoff, Reminder
from app.db.session import Database
from app.schedule.mock import MockSchedule
from tests.chat_harness import PATIENT, Chat, Clock, make_settings, to_topic
from tests.conftest import NOW
from tests.fake_deepseek import FakeDeepSeek


async def to_dislocation(chat: Chat) -> None:
    """Menu → «Выбрать из списка» → «Рука или нога» → «Похоже на вывих»."""
    await to_topic(chat, "Вывих")


async def book_dislocation(chat: Chat, *, day: str = "Сегодня") -> None:
    """A dislocation by buttons → day → time → consent → name → birth date → contact."""
    await chat.start()
    await to_dislocation(chat)
    await chat.press("Для себя")
    await chat.press("Бытовая травма")
    await chat.press("В течение 7 дней")
    await chat.press("Нет, не обращались")
    await chat.pick_slot(day)
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
    await chat.say("вывихнул плечо")  # no LLM: the word list → arm, «упал, подвернул» topics
    assert (
        chat.text.startswith("Шаг 1 из 5 · Что случилось") and "На что больше похоже?" in chat.text
    )
    assert chat.labels()[:2] == ["Похоже на вывих", "Похоже на перелом"]
    await chat.press("Похоже на вывих")
    assert chat.labels() == ["Для себя", "Для ребёнка (до 18 лет)", texts.BACK]
    await chat.press("Для себя")
    await chat.press("Бытовая травма")
    await chat.press("В течение 7 дней")
    await chat.press("Нет, не обращались")
    info, days = chat.replies
    assert "вывих, врач попробует его вправить" in info.text and info.edit and not info.screen
    assert days.text.startswith("Шаг 4 из 5 · День и время") and days.screen and not days.edit
    assert chat.days()[0].label == "Сегодня, 12.11"
    assert chat.labels()[0].startswith("Ближайшее: сегодня, 1")  # first, one tap to book
    await chat.pick_day("Сегодня")
    assert "12 ноября (четверг)" in chat.text and "смены:" in chat.text
    assert texts.PERIOD_PICK in chat.text
    assert [b.label for b in chat.periods()] == ["День 12–18", "Вечер 18–23", "Ночь 23–8"]
    await chat.pick_period("13:00")
    times = [b.label for b in chat.times()]
    assert times and times[0] >= "13:00"  # not earlier than an hour from now (12:00)
    later = next(t for t in times if t >= "14:00")  # «за 2 часа» still ahead

    await chat.pick_time(later)
    assert texts.CONSENT in chat.text and chat.replies[0].edit
    await chat.press(texts.CONSENT_YES)
    assert "фамилия, имя, отчество" in chat.text
    await chat.say("петров иван сергеевич")
    await chat.say("12.03.1985")
    assert chat.replies[-1].ask_contact
    await chat.contact()
    thanks, card, payment = chat.replies
    assert thanks.remove_keyboard and card.card is not None
    for part in (
        "Запись в травмпункт",
        "Иван Сергеевич записан на приём травматолога-ортопеда в травмпункт.",
        f"Дата и время: четверг, 12 ноября, {later}",
        "Врач: ",
        "Адрес: ул. Шейнкмана, 113/2, вход через детское отделение",
        "Стоимость консультации 2000 рублей (демо-цена)",
        "Что взять с собой:\n• Паспорт.\n• При первом обращении — ещё СНИЛС.",
        texts.DEMO_BOOKING,
    ):
        assert part in card.text, part
    assert [b.label for row in card.buttons for b in row] == [texts.MOVE, texts.CANCEL]
    assert payment.text == "Оплачивать планируете самостоятельно или по страховке?"
    await chat.press(texts.PAYMENT_INSURANCE)

    first = (await bookings(db))[0]
    assert (first.payment, first.cycle, first.status) == ("insurance", 1, "active")
    assert first.slot_id not in {s.id for s in await schedule.free_slots(NOW, NOW + timedelta(1))}

    # notifications: 14:30 today → no «за 24 часа»; «за 2 часа», then «Как прошёл приём?»
    await chat.demo_next()
    assert chat.text.startswith(
        f"Скоро приём в травмпункте: в четверг, 12 ноября, в {later[10:15]}"
    )
    assert "завтра" not in chat.text and "Через 2 часа" not in chat.text
    assert "подземный паркинг" in chat.text
    assert chat.labels() == [texts.CONFIRM, texts.MOVE, texts.CANCEL]
    await chat.press(texts.CONFIRM)
    await chat.demo_next()
    assert chat.text == texts.AFTER_VISIT
    await chat.press(texts.AFTER_VISIT_OK)
    assert (
        "Контрольный приём обычно назначают примерно через 10 дней, точный срок определяет врач"
    ) in chat.text

    # control visit: 04-09 with «первично у нас» known → only «больничный?»
    await chat.press(texts.CONTROL_YES)
    assert chat.text == "Открывали больничный лист?"
    await chat.press("Нет")  # straight to the time grid of the day about 10 days later
    assert chat.text.startswith("Предлагаю время около 22 ноября (воскресенье).")
    assert "22 ноября (воскресенье)" in chat.text and texts.OTHER_DAY in chat.labels()
    assert chat.periods() and not chat.days()
    assert chat.labels()[0].startswith("Ближайшее: вс, 22.11, ")
    await chat.press(texts.OTHER_DAY)
    assert chat.days()[0].label == "Чт, 19.11"  # a few days before the target
    await chat.pick_day("Вс, 22.11")
    await chat.pick_time()  # patient, consent and phone are known: booked at once
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
        for s in await schedule.free_slots(NOW + timedelta(days=1, hours=1), NOW + timedelta(2))
        if not s.repeat_window and not s.repeat_cabinet
    )
    await chat.press(texts.PAYMENT_SELF)
    await chat.press(texts.MENU_MY)
    await chat.press(texts.MOVE)
    assert texts.MOVE_PICK in chat.text and "Шаг" not in chat.text
    await chat.pick_day("Завтра")
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
    await chat.pick_slot("Завтра")
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
    await to_topic(chat, "Перелом")
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
    assert (
        "С такой ситуацией ребёнка записывают на плановый приём к травматологу-ортопеду, а не в "
        "травмпункт. Я передал обращение № 1 администратору — он свяжется с Вами по "
        "телефону, который Вы указали."
    ) in chat.text
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
    assert "свяжется с Вами в рабочее время (с 08:00 до 20:00) по телефону" in chat.text


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
    assert chat.text.endswith("\n\nПравильно понимаю: запись для ребёнка, 8 лет?")
    assert chat.labels() == ["Да, ребёнок 8 лет", "Нет", texts.BACK]
    await chat.press("Да, ребёнок 8 лет")
    assert texts.DAY_PICK in chat.text  # all answers known → straight to the day picker


async def test_child_age_rejected_asks_who(make_chat, kb) -> None:
    fake = FakeDeepSeek({"intent": "complaint", "scenario": "burns", "facts": {}})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("дочке 3 года, обожглась")
    await chat.press("Нет")
    assert chat.labels() == ["Для себя", "Для ребёнка (до 18 лет)", texts.BACK]


async def test_free_text_answer_falls_back_to_buttons(make_chat, kb) -> None:
    chat = make_chat(assistant=FakeDeepSeek(500, 500).assistant(kb))
    await chat.start()
    await to_topic(chat, "Ожог")
    await chat.press("Для себя")
    await chat.say("обожгла руку")
    assert texts.PICK_BUTTON in chat.text
    assert chat.labels() == ["Лицо", "Другая часть тела", texts.BACK]


async def test_questions_are_answered_from_kb_info(make_chat, kb) -> None:
    answer = "Травмпункт работает круглосуточно по предварительной записи."
    question = {"type": "clinic_question"}
    fake = FakeDeepSeek(question, {"found": True, "answer": answer}, question, {"found": False})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_ASK)
    await chat.say("Во сколько вы работаете?")
    assert chat.text.startswith(answer)
    await chat.press(texts.MENU_ASK)
    await chat.say("Делаете МРТ колена?")
    assert chat.text == texts.INFO_UNKNOWN and chat.admin == []  # only by the button
    assert chat.labels() == [texts.ASK_ADMIN_QUESTION, texts.MENU_HOME]
    await chat.press(texts.ASK_ADMIN_QUESTION)
    await chat.press(texts.CONSENT_YES)
    await chat.contact()
    assert "Сообщение пациента: Делаете МРТ колена?" in chat.admin[-1].text


async def test_returning_patient_is_picked_not_retyped(make_chat, db: Database) -> None:
    chat = make_chat()
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    await to_topic(chat, "Перелом")
    await chat.press("Для себя")
    await chat.press("Бытовая травма")
    await chat.press("В течение 7 дней")
    await chat.press("Нет, не обращались")
    await chat.pick_slot()
    assert chat.labels() == ["Петров Иван, 12.03.1985", texts.PATIENT_NEW, texts.BACK]
    await chat.press("Петров Иван, 12.03.1985")
    assert "Иван Сергеевич записан" in chat.text
    assert len(await bookings(db)) == 2


async def test_due_reminders_are_sent_once(make_chat, clock: Clock, db: Database) -> None:
    chat = make_chat()
    await book_dislocation(chat)
    clock.now = (await bookings(db))[0].end_at + timedelta(hours=3)  # «Как прошёл приём?»
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
        settings=make_settings(),
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
    await to_topic(chat, "Ожог")
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
    await to_topic(chat, "Ожог")
    await chat.press("Для ребёнка (до 18 лет)")
    await chat.say("5 лет")
    await chat.press("Другая часть тела")
    await chat.press("Кипяток, пар, огонь, солнце")
    await chat.press("Менее суток назад")
    await chat.press("Нет, не обращались")
    await chat.pick_slot()
    assert texts.CONSENT_ITEM_GUARDIAN in chat.text  # adult consent is not enough for a child
    await chat.press(texts.CONSENT_YES)
    await chat.say("Петрова Анна Ивановна")
    await chat.say("01.02.2021")
    assert "Анна Ивановна записана" in chat.text  # phone already known
    await chat.press(texts.PAYMENT_SELF)
    await to_topic(chat, "Ожог")
    await chat.press("Для ребёнка (до 18 лет)")
    await chat.say("5 лет")
    await chat.press("Другая часть тела")
    await chat.press("Кипяток, пар, огонь, солнце")
    await chat.press("Менее суток назад")
    await chat.press("Нет, не обращались")
    await chat.pick_slot()
    assert chat.labels() == ["Петрова Анна, 01.02.2021", texts.PATIENT_NEW, texts.BACK]


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
