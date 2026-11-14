"""Task 2, beyond the table: what follows a phrase, and the edges of the fixes."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.bot import texts
from app.db.models import Handoff, Patient, User
from app.flow.loader import KnowledgeBase
from tests import intent_cases as m
from tests.chat_harness import Chat, to_topic
from tests.fake_deepseek import FakeDeepSeek
from tests.test_intents import to_step


async def session(chat: Chat) -> dict:
    async with chat.conversation.db() as s:
        return dict((await s.get(User, chat.user.id)).session or {})


# --- who the visit is for ----------------------------------------------------------------


async def test_child_named_in_the_request_is_not_asked_again(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("запишите ребёнка")
    assert texts.FOR_WHOM["child"] in chat.text
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")
    assert texts.ASK_AUDIENCE not in chat.text  # «Для кого запись?» is skipped
    assert texts.ASK_CHILD_AGE in chat.text  # the age decides the 05-04 route, so it is asked
    await chat.say("8 лет")
    assert "Как получена травма" in chat.text
    await chat.press("Бытовая травма")
    await chat.press("В течение 7 дней")
    await chat.press("Нет, не обращались")
    await chat.pick_slot()
    assert texts.CONSENT_ITEM_GUARDIAN in chat.text  # the consent of a child's representative


async def test_adult_named_in_the_request_goes_straight_to_the_questions(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("можно записать маму?")
    assert texts.FOR_WHOM["adult"] in chat.text
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")
    assert "Как получена травма" in chat.text and texts.ASK_AUDIENCE not in chat.text


async def test_infant_named_in_the_request_is_still_routed(make_chat, kb: KnowledgeBase) -> None:
    """«Запишите ребёнка» does not go around the 05-04 rule: under 6 months — the hospital."""
    chat = make_chat()
    await chat.start()
    await chat.say("запишите сына")
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")
    await chat.say("4 месяца")
    assert "Областная детская клиническая больница № 1" in chat.text and not chat.days()


async def test_menu_button_forgets_who_the_visit_was_for(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("запишите ребёнка")
    await chat.conversation.menu(chat.user)
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    assert texts.FOR_WHOM["child"] not in chat.text
    assert (await session(chat)).get("for_whom") is None


async def test_walking_with_a_child_is_not_a_child_patient(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek({"type": "complaint", "area": "limb", "scenario": "fracture"})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("гуляла с сыном, упала и сломала руку")
    assert texts.ASK_AUDIENCE in chat.text  # it is asked: the text does not say


# --- navigation and data steps ------------------------------------------------------------


async def test_back_from_the_phone_step_returns_to_the_birth_date(make_chat) -> None:
    chat = make_chat()
    await to_step(chat, m.PHONE)
    await chat.say("назад")
    assert texts.ASK_BIRTH in chat.text and (await session(chat))["step"] == "birth"
    await chat.say("12.03.1985")
    assert chat.replies[-1].ask_contact  # and forward again


async def test_a_request_on_the_name_step_is_not_a_name(make_chat, db) -> None:
    chat = make_chat()
    await to_step(chat, m.NAME)
    for phrase in ("запишите меня", "позови администратора", "спасибо", "что взять с собой"):
        await chat.say(phrase)
        async with db() as s:
            assert (await s.scalars(select(Patient))).all() == [], phrase
    assert (await session(chat)).get("draft_name") is None


async def test_cancel_while_moving_a_booking_keeps_it(make_chat) -> None:
    from tests.test_conversation import book_dislocation, bookings

    chat = make_chat()
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    await chat.say("хочу перенести")  # one booking: its move starts, the day picker
    assert chat.text.startswith("Переношу запись с четверга, 12 ноября") and chat.days()
    (before,) = await bookings(chat.conversation.db)
    await chat.say("отмена")
    assert chat.text == texts.KEPT and texts.MENU_BOOK in chat.labels()
    (after,) = await bookings(chat.conversation.db)
    assert (after.status, after.slot_id) == ("active", before.slot_id)


async def test_typed_label_works_without_deepseek(make_chat) -> None:
    """The whole booking by typed words, with no LLM at all."""
    chat = make_chat()  # assistant=None
    await chat.start()
    await chat.say("записаться")
    await chat.say("рука или нога")
    await chat.say("похоже на вывих")
    await chat.say("для себя")
    await chat.say("бытовая травма")
    await chat.say("в течение 7 дней")
    await chat.say("нет")
    assert chat.days()
    await chat.say("завтра")
    await chat.say("день")
    await chat.say(chat.times()[0].label)
    await chat.say("согласен")
    await chat.say("петров иван сергеевич")
    await chat.say("12.03.1985")
    await chat.say("+7 912 345-67-89")
    assert "Иван Сергеевич записан на приём" in chat.text


# --- questions in the middle of booking ---------------------------------------------------


async def test_question_about_data_on_the_consent_step(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek({"type": "about_bot"})
    chat = make_chat(assistant=fake.assistant(kb))
    await to_step(chat, m.CONSENT)
    await chat.say("а зачем вам мои данные?")
    answer, screen = chat.replies
    assert answer.text == texts.ABOUT_BOT
    assert texts.CONSENT in screen.text and texts.CONSENT_YES in chat.labels()


async def test_medical_question_on_a_scenario_step_gets_no_advice(make_chat, kb) -> None:
    fake = FakeDeepSeek({"type": "medical_question"})
    chat = make_chat(assistant=fake.assistant(kb))
    await to_step(chat, m.QUESTION)
    await chat.say("а лёд прикладывать можно?")
    answer, screen = chat.replies
    assert answer.text == texts.MEDICAL_ASIDE and "Как получена травма" in screen.text


async def test_question_like_answer_is_still_an_answer(make_chat, kb: KnowledgeBase) -> None:
    """«А если упал на тренировке?» is not about the clinic: the scenario reads it."""
    fake = FakeDeepSeek(
        {"type": "complaint", "area": "limb"}, {"facts": {"assault": False}, "unclear": False}
    )
    chat = make_chat(assistant=fake.assistant(kb))
    await to_step(chat, m.QUESTION)
    await chat.say("а если упал на тренировке?")
    assert "Когда была получена травма?" in chat.text
    assert len(fake.requests) == 2  # route said «not a question», extract took the answer


async def test_question_without_data_inside_booking_keeps_the_step(make_chat, kb) -> None:
    fake = FakeDeepSeek({"type": "clinic_question"}, {"found": False})
    chat = make_chat(assistant=fake.assistant(kb))
    await to_step(chat, m.DAYS)
    await chat.say("а кофе у вас есть?")
    answer, screen = chat.replies
    assert answer.text == texts.ASIDE_UNKNOWN and chat.days()
    assert texts.INFO_UNKNOWN not in chat.text and "Шаг 4 из 5" in screen.text


# --- two parts, feedback, the safety net --------------------------------------------------


async def test_question_part_without_an_answer_is_said_so(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek(
        {"type": "menu_action", "action": "book", "question": True}, {"found": False}
    )
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("у вас есть МРТ? и запишите на завтра")
    note, screen = chat.replies
    assert note.text == texts.ASIDE_UNKNOWN
    assert texts.ASK_COMPLAINT in screen.text and "Пожелание: завтра" in screen.text


async def test_complaint_text_goes_to_the_admins_after_it_is_written(make_chat, db) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("хочу пожаловаться")
    assert chat.text == texts.ASK_FEEDBACK and chat.labels() == [texts.MENU_HOME]
    await chat.say("ждал приёма час, отмените мою запись")  # the message, not a command
    await chat.press(texts.CONSENT_YES)
    await chat.contact()
    post = chat.admin[-1].text
    assert texts.FEEDBACK_REASON in post and "ждал приёма час, отмените мою запись" in post
    async with db() as s:
        assert len((await s.scalars(select(Handoff))).all()) == 1


async def test_request_taken_for_a_question_gets_the_menu(make_chat, kb: KnowledgeBase) -> None:
    """The safety net: whatever DeepSeek says, «запишите…» is not answered with «нет данных»."""
    fake = FakeDeepSeek({"type": "clinic_question"}, {"found": False})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("запишите нас с мужем обоих, если получится")
    assert chat.text == texts.REQUEST_UNCLEAR and texts.INFO_UNKNOWN not in chat.text
    assert chat.labels()[0] == texts.MENU_BOOK


async def test_real_question_without_data_still_says_so(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek({"type": "clinic_question"}, {"found": False})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("Я опаздываю на приём, что делать?")
    assert chat.text == texts.INFO_UNKNOWN and chat.labels()[0] == texts.ASK_ADMIN_QUESTION


# --- not a text ---------------------------------------------------------------------------


async def test_photo_or_voice_gets_a_way_on(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    chat._take(await chat.conversation.other(chat.user))
    assert chat.text == texts.ONLY_TEXT and texts.MENU_BOOK in chat.labels()
    await to_topic(chat, "Вывих")
    chat._take(await chat.conversation.other(chat.user))
    note, screen = chat.replies
    assert note.text == texts.ONLY_TEXT and texts.ASK_AUDIENCE in screen.text


async def test_stale_button_leads_to_the_menu(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.data("q:when:0")  # a button of a question that is not on screen
    assert chat.text == texts.STALE and chat.labels() == [texts.MENU_HOME]


# --- the same screen is never shown twice with no word why (13.11, item 2) -----------------

LIMB_TOPICS = "темы «Рука или нога»"
FALL_TOPICS = "«На что больше похоже?»"
OTHER_TOPICS = "меню «Другое»"
DESCRIBE = "«Опишите своими словами»"
START_SCREENS = (m.COMPLAINT, LIMB_TOPICS, FALL_TOPICS, OTHER_TOPICS, DESCRIBE)
SCREENS = (*m.WIZARD, *START_SCREENS[1:])
DESCRIPTIONS = (
    "ударился болит сильно",
    "болит рука",
    "упал вчера, до сих пор болит",
    "не могу наступить на ногу",
)
PHRASES = (*sorted({i.phrase for i in m.INTENTS if not i.phrase.startswith("<")}), *DESCRIPTIONS)
NOTHING = {"type": "complaint", "area": None, "scenario": None}
ASKS_BACK = {**NOTHING, "clarify": "Подскажите, пожалуйста, что именно болит?"}
# what DeepSeek may make of a text it did not understand; () — it does not answer at all
READINGS = {
    "жалоба без темы": (NOTHING,),
    "жалоба и встречный вопрос": (ASKS_BACK,),
    "не по теме": ({"type": "offtopic"},),
    "не наш профиль": ({"type": "other_medical"},),
    "вопрос без ответа в справке": ({"type": "clinic_question"}, {"found": False}),
    "DeepSeek не отвечает": (),
}
COMPLAINT_READINGS = ("жалоба без темы", "жалоба и встречный вопрос", "DeepSeek не отвечает")


async def to_screen(chat: Chat, screen: str) -> None:
    if screen not in START_SCREENS[1:]:
        await to_step(chat, screen)
        return
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    if screen in (LIMB_TOPICS, FALL_TOPICS):
        await chat.press("Рука или нога")
    if screen == FALL_TOPICS:  # as after «подвернул ногу»
        chat._take(await chat.conversation.button(chat.user, "ex:ankle"))
    if screen in (OTHER_TOPICS, DESCRIBE):
        await chat.press("Другое")
    if screen == DESCRIBE:
        await chat.press("Опишите своими словами")


async def put_back(chat: Chat, saved: dict) -> None:
    async with chat.conversation.db() as s:
        (await s.get(User, chat.user.id)).session = saved
        await s.commit()


@pytest.mark.parametrize("reading", READINGS)
@pytest.mark.parametrize("screen", SCREENS)
async def test_same_screen_is_never_repeated_without_a_line(
    make_chat, kb: KnowledgeBase, screen: str, reading: str
) -> None:
    """Every phrase on every booking screen, whatever DeepSeek makes of it."""
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await to_screen(chat, screen)
    shown, saved = chat.replies[-1], await session(chat)
    assert saved["step"] in ("complaint", "topic", "describe") or screen in m.WIZARD
    for phrase in PHRASES:
        await put_back(chat, saved)
        fake.replies = list(READINGS[reading])
        await chat.say(phrase)
        where = f"«{phrase}» на шаге {screen}, {reading}:\n{chat.text}"
        assert chat.replies and chat.replies[-1].text.strip(), where
        repeated = [r for r in chat.replies if (r.text, r.buttons) == (shown.text, shown.buttons)]
        assert len(repeated) <= 1, where
        if repeated:  # the same screen again: a line before it says why
            assert chat.replies[0] is not repeated[0] and chat.replies[0].text.strip(), where


@pytest.mark.parametrize("reading", READINGS)
@pytest.mark.parametrize("screen", START_SCREENS)
async def test_description_on_step_1_is_not_answered_with_describe_it(
    make_chat, kb: KnowledgeBase, screen: str, reading: str
) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("запишите к Ярунину на завтра")  # the wish in the header of step 1
    for label in {
        m.COMPLAINT: (),
        LIMB_TOPICS: ("Рука или нога",),
        FALL_TOPICS: ("Рука или нога", "упал"),
        OTHER_TOPICS: ("Другое",),
        DESCRIBE: ("Другое", "Опишите своими словами"),
    }[screen]:
        await (chat.say(label) if label.islower() else chat.press(label))
    saved = await session(chat)
    saved_wish = saved["wish"]
    for phrase in DESCRIPTIONS:
        await put_back(chat, saved)
        fake.replies = list(READINGS[reading])
        await chat.say(phrase)
        where = f"«{phrase}» на шаге {screen}, {reading}:\n{chat.text}"
        now = await session(chat)
        assert now["wizard"] and now["step"] in ("complaint", "topic", "describe"), where
        assert now["wish"] and now["wish"]["doctor"] == saved_wish["doctor"], where
        assert "Пожелание: к Ярунину А. А., завтра" in chat.text and chat.buttons(), where
        if reading in COMPLAINT_READINGS:  # read as a complaint: never «опишите своими словами»
            assert "своими словами" not in chat.text.lower(), where


# --- the menu and «назад» in the patient's own words, on every step (14.11, item 7) ----------

MENU_PHRASES = (
    "открой меню",
    "покажи меню",
    "первоначальное меню открой",
    "начальное меню",
    "верни меня в меню",
    "выйди в меню",
    "давай заново",
    "хочу сначала",
)
BACK_PHRASES = ("вернись назад", "давай назад", "можно назад?")
EVERY_STEP = (m.MENU, *SCREENS, m.PAYMENT, m.ASK)


@pytest.mark.parametrize("screen", EVERY_STEP)
async def test_menu_and_back_in_words_work_on_every_step(
    make_chat, kb: KnowledgeBase, screen: str
) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await to_screen(chat, screen)
    shown, saved = chat.replies[-1], await session(chat)
    for phrase in MENU_PHRASES:
        await put_back(chat, saved)
        await chat.say(phrase)
        where = f"«{phrase}» на шаге {screen}:\n{chat.text}"
        assert fake.requests == [], where  # read by code
        assert (await session(chat))["step"] == "idle", where
        assert chat.text == kb.common.more_help.text and texts.MENU_BOOK in chat.labels(), where
    for phrase in BACK_PHRASES:
        await put_back(chat, saved)
        await chat.say(phrase)
        where = f"«{phrase}» на шаге {screen}:\n{chat.text}"
        assert fake.requests == [], where
        now = await session(chat)
        moved = (chat.replies[-1].text, chat.replies[-1].buttons) != (shown.text, shown.buttons)
        assert now["step"] == "idle" or moved, where  # the step before, or the menu from the first
        assert texts.HINT_TIME not in chat.text and texts.HINT_BUTTONS not in chat.text, where


@pytest.mark.parametrize("action", ["home", "back"])
@pytest.mark.parametrize("screen", [m.DAYS, m.PERIODS, m.TIMES, m.CONSENT])
async def test_route_leads_to_the_menu_or_back_from_steps_with_buttons(
    make_chat, kb: KnowledgeBase, screen: str, action: str
) -> None:
    """A wording the word list does not know: route says «home» or «back»."""
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await to_screen(chat, screen)
    shown = chat.replies[-1]
    fake.replies = [{"type": "menu_action", "action": action}]
    await chat.say("мне бы на самый первый экран попасть" if action == "home" else "не туда нажал")
    assert len(fake.requests) == 1
    if action == "home":
        assert (await session(chat))["step"] == "idle" and texts.MENU_BOOK in chat.labels()
        assert chat.labels()[0] == texts.RESUME  # the booking is kept
    else:
        assert (chat.replies[-1].text, chat.replies[-1].buttons) != (shown.text, shown.buttons)


# --- a request to cancel never gets the plain list of bookings again (14.11, item 1) ---------

CANCEL_PHRASES = (
    "отмени запись",
    "отмените мою запись",
    "хочу отменить приём",
    "отмена",
    "отмени все",
    "отмени всё",
    "отмените все записи",
    "отмени запись на сегодня",
    "отмени запись на воскресенье",
    "отмени запись к Кузнецову",
    "отмени запись на 15 ноября в 21:00",
)
CANCEL_ANSWERS = (
    "Отменить запись на ",
    texts.CANCEL_WHICH,
    texts.CANCEL_ALL_ASK,
    texts.CANCEL_NOT_FOUND,  # no such booking: said so, with a button for each one
)


@pytest.mark.parametrize("days", [(), ("Завтра",), ("Завтра", "Вс")], ids=["1", "2", "3"])
async def test_a_request_to_cancel_never_repeats_the_list(make_chat, days: tuple[str, ...]) -> None:
    from sqlalchemy import func

    from app.db.models import Booking
    from tests.test_manual_findings_3 import booked

    chat = await booked(make_chat, *days)
    for phrase in CANCEL_PHRASES:
        await chat.say("мои записи")
        listed = [(r.text, r.buttons) for r in chat.replies]
        assert texts.MOVE in chat.labels()
        for attempt in (1, 2):  # the same request twice: a question each time, never the list
            await chat.say(phrase)
            where = f"«{phrase}», записей {len(days) + 1}, раз {attempt}:\n{chat.text}"
            got = [(r.text, r.buttons) for r in chat.replies]
            assert not any(item in listed for item in got), where
            assert texts.MOVE not in chat.labels(), where
            assert any(a in chat.text for a in CANCEL_ANSWERS), where
            assert chat.buttons(), where
        async with chat.conversation.db() as s:  # nothing is cancelled without a «да»
            count = await s.scalar(
                select(func.count()).select_from(Booking).where(Booking.status == "active")
            )
        assert count == len(days) + 1, phrase
