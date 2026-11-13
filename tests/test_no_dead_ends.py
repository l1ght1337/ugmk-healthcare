"""Task 2, beyond the table: what follows a phrase, and the edges of the fixes."""

from __future__ import annotations

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
    await chat.press(texts.TOPIC_LIST)
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
    await chat.press(texts.TOPIC_LIST)
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")
    assert "Как получена травма" in chat.text and texts.ASK_AUDIENCE not in chat.text


async def test_infant_named_in_the_request_is_still_routed(make_chat, kb: KnowledgeBase) -> None:
    """«Запишите ребёнка» does not go around the 05-04 rule: under 6 months — the hospital."""
    chat = make_chat()
    await chat.start()
    await chat.say("запишите сына")
    await chat.press(texts.TOPIC_LIST)
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
    await chat.say("хочу перенести")
    await chat.press(texts.MOVE)
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
    await chat.say("выбрать из списка")
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
