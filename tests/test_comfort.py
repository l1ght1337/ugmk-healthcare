"""Task 3: the greeting with examples, the summary before the time choice, «зачем данные»,
the booking card with icons, «Помогло» under answers, the return of an unfinished booking."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from app.bot import texts
from app.db.models import AnswerRating, User
from app.flow.loader import KnowledgeBase
from tests.chat_harness import Chat, to_topic
from tests.conftest import NOW, TZ
from tests.fake_deepseek import FakeDeepSeek

EXAMPLES = ["Подвернул ногу", "Нужна перевязка", "Где парковаться?"]
MENU = [texts.MENU_BOOK, texts.MENU_ASK, texts.MENU_MY, texts.MENU_ADMIN]


async def session(chat: Chat) -> dict:
    async with chat.conversation.db() as s:
        return dict((await s.get(User, chat.user.id)).session or {})


def labels_of(reply) -> list[str]:  # type: ignore[no-untyped-def]
    return [b.label for row in reply.buttons for b in row]


# --- 1. the greeting ---------------------------------------------------------------------


async def test_greeting_has_examples_in_text_and_as_buttons(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    assert chat.text.startswith("Добрый день! Травмпункт «УГМК-Здоровье», я виртуальный помощник")
    assert "Запишу в травмпункт за минуту — 5 коротких шагов" in chat.text
    assert "«запишите к Кузнецову Н. А. на завтра»" in chat.text  # the doctor is from the data
    assert texts.DEMO_GREETING in chat.text
    assert chat.labels() == [*EXAMPLES, *MENU]


async def test_menu_after_the_greeting_has_no_examples(make_chat, kb: KnowledgeBase) -> None:
    chat = make_chat()
    await chat.start()
    chat._take(await chat.conversation.menu(chat.user))
    assert chat.text == kb.common.more_help.text and chat.labels() == MENU


async def test_example_twisted_ankle_opens_the_limb_topics(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.press("Подвернул ногу")
    assert fake.requests == []  # an example is a button: no DeepSeek
    assert "Шаг 1 из 5" in chat.text and "На что больше похоже?" in chat.text
    assert chat.labels()[:3] == [
        "Похоже на вывих",
        "Похоже на перелом",
        "Растяжение или разрыв связок",
    ]


async def test_example_dressing_starts_its_scenario(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press("Нужна перевязка")
    assert texts.ASK_AUDIENCE in chat.text
    assert (await session(chat))["dialog"]["scenario_id"] == "dressing"


async def test_example_parking_answers_from_the_scripts(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.press("Где парковаться?")
    assert fake.requests == []
    assert "С 8:00 до 20:00" in chat.text and "После 20:00" in chat.text
    assert chat.labels()[-3:] == [texts.HELPED, texts.MENU_ADMIN, texts.MENU_HOME]
    assert chat.labels().count(texts.MORE) == 1  # one button for both long phrases
    await chat.press(texts.MORE)
    assert "талон после посещения врача нужно обнулить" in chat.text  # by day
    assert "выделены места для пациентов травмпункта в ночное время" in chat.text  # after 20:00


# --- 2. the summary before the time choice -------------------------------------------------


async def to_time(chat: Chat) -> None:
    await chat.press(texts.TOPIC_LIST)
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")
    await chat.press("Для себя")
    await chat.press("Бытовая травма")
    await chat.press("В течение 7 дней")
    await chat.press("Нет, не обращались")


async def test_summary_of_answers_with_the_wish(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("запишите к Кузнецову")
    await to_time(chat)
    summary, screen = chat.replies
    assert summary.text == (
        "Ваши ответы: вывих; для себя; бытовая травма; в течение 7 дней; нет, не обращались.\n"
        "Пожелание: к Кузнецову Н. А.\n\n"
        "Можно записаться к травматологу-ортопеду: врач попробует вправить вывих."
    )
    assert labels_of(summary) == [texts.SUMMARY_EDIT, texts.MORE]
    assert "Шаг 4 из 5" in screen.text and chat.days()


async def test_summary_for_a_child_names_the_age(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await to_topic(chat, "Перелом")
    await chat.press("Для ребёнка (до 18 лет)")
    await chat.say("8 лет")
    await chat.press("Бытовая травма")
    await chat.press("В течение 7 дней")
    await chat.press("Нет, не обращались")
    assert chat.replies[0].text == (
        "Ваши ответы: перелом; для ребёнка, 8 лет; бытовая травма; в течение 7 дней; нет, не "
        "обращались."
    )
    assert labels_of(chat.replies[0]) == [texts.SUMMARY_EDIT]


async def test_edit_goes_back_to_the_first_question_and_keeps_the_patient(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await to_time(chat)
    await chat.press(texts.SUMMARY_EDIT)
    assert "Шаг 3 из 5" in chat.text and "Как получена травма" in chat.text
    state = await session(chat)
    assert state["dialog"]["scenario_id"] == "dislocation"
    assert state["dialog"]["facts"] == {"audience": "adult"}  # «Для кого» is not asked again
    await chat.press("Бытовая травма")
    await chat.press("Более 7 дней назад")  # another answer: another outcome
    assert chat.text and not chat.days()


async def test_route_outcome_has_no_summary(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press(texts.TOPIC_LIST)
    await chat.press("Голова, лицо, глаза, уши")
    await chat.press("Ударился головой")
    await chat.press("Для себя")
    assert "Ваши ответы" not in chat.text and texts.SUMMARY_EDIT not in chat.labels()


# --- 3. why the data is asked ----------------------------------------------------------------


async def test_why_the_name_and_the_phone_are_asked(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await to_time(chat)
    await chat.pick_slot()
    await chat.press(texts.CONSENT_YES)
    assert texts.ASK_NAME in chat.text and texts.WHY_NAME in chat.text
    await chat.say("петров иван сергеевич")
    await chat.say("12.03.1985")
    assert chat.replies[-1].ask_contact
    assert texts.WHY_PHONE in chat.text and texts.CONTACT_BUTTON in chat.text


# --- 4. the booking card ----------------------------------------------------------------------


async def test_card_is_lines_with_icons(make_chat) -> None:
    from tests.test_conversation import book_dislocation

    chat = make_chat()
    await book_dislocation(chat)
    card = next(r for r in chat.replies if r.card is not None)
    lines = card.text.split("\n")
    assert lines[0] == "✅ Запись в травмпункт"
    assert "📅 Дата: четверг, 12 ноября" in lines
    assert any(line.startswith("🕒 Время: 1") for line in lines)
    assert any(line.startswith("🩺 Врач: Кузнецов Николай Александрович") for line in lines)
    assert "📍 Адрес: ул. Шейнкмана, 113/2, вход через детское отделение" in lines
    assert any(
        line.startswith("💳 Стоимость консультации 2000 рублей (демо-цена)") for line in lines
    )
    assert "🧾 Что взять с собой:" in lines and "• Паспорт." in lines
    assert [name for _, name, _ in card.card.fields] == ["Дата", "Время", "Врач", "Адрес"]


# --- 5. «Помогло» under an answer ------------------------------------------------------------


async def ratings(chat: Chat) -> list[AnswerRating]:
    async with chat.conversation.db() as s:
        return list((await s.scalars(select(AnswerRating).order_by(AnswerRating.id))).all())


async def test_answer_can_be_marked_as_helpful(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek({"type": "clinic_question"}, {"found": True, "answer": "Круглосуточно."})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("во сколько вы работаете?")
    assert chat.text == "Круглосуточно."
    assert chat.labels() == [texts.HELPED, texts.MENU_ADMIN, texts.MENU_HOME]
    await chat.press(texts.HELPED)
    assert chat.text == texts.HELPED_THANKS and chat.labels() == MENU
    (row,) = await ratings(chat)
    assert (row.helped, row.source) == (True, "answer")
    assert not hasattr(row, "user_id")  # no personal data: nothing to delete on /forget


async def test_admin_after_an_answer_gets_the_question(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek({"type": "clinic_question"}, {"found": True, "answer": "Круглосуточно."})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("во сколько вы работаете в праздники?")
    await chat.press(texts.MENU_ADMIN)
    await chat.press(texts.CONSENT_YES)
    await chat.contact()
    post = chat.admin[-1].text
    assert texts.QUESTION_REASON in post and "во сколько вы работаете в праздники?" in post
    (row,) = await ratings(chat)
    assert (row.helped, row.source) == (False, "answer")


async def test_question_after_an_answer_is_read_as_usual(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek(
        {"type": "clinic_question"}, {"found": True, "answer": "Круглосуточно."},
        {"type": "clinic_question"}, {"found": True, "answer": "ул. Шейнкмана, 113/2."},
    )  # fmt: skip
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("во сколько вы работаете?")
    await chat.say("а адрес какой?")
    assert chat.text == "ул. Шейнкмана, 113/2." and await ratings(chat) == []


# --- 6. the return of an unfinished booking -------------------------------------------------


async def nudges(chat: Chat) -> list:
    return [chat._take(t) for t in await chat.conversation.unfinished()]


async def test_one_reminder_fifteen_minutes_after_the_patient_went_silent(make_chat, clock) -> None:
    chat = make_chat()
    await chat.start()
    await to_topic(chat, "Вывих")
    await chat.press("Для себя")
    clock.now = NOW + timedelta(minutes=14)
    assert await nudges(chat) == []
    clock.now = NOW + timedelta(minutes=16)
    (turn,) = await nudges(chat)
    (reply,) = turn.replies
    assert reply.text == texts.UNFINISHED and reply.chat_id == chat.user.chat_id
    assert labels_of(reply) == [texts.UNFINISHED_GO, texts.UNFINISHED_AGAIN]
    clock.now = NOW + timedelta(minutes=40)
    assert await nudges(chat) == []  # one reminder, not more

    await chat.press(texts.UNFINISHED_GO)
    assert "Как получена травма" in chat.text and "Шаг 3 из 5" in chat.text  # the same step
    clock.now = NOW + timedelta(minutes=70)
    assert await nudges(chat) == []  # still one for this booking


async def test_start_again_from_the_reminder(make_chat, clock) -> None:
    chat = make_chat()
    await chat.start()
    await to_topic(chat, "Вывих")
    clock.now = NOW + timedelta(minutes=16)
    await nudges(chat)
    await chat.press(texts.UNFINISHED_AGAIN)
    assert texts.ASK_COMPLAINT in chat.text
    assert (await session(chat))["dialog"]["scenario_id"] is None


async def test_no_reminder_at_night(make_chat, clock) -> None:
    clock.now = datetime(2026, 11, 12, 21, 50, tzinfo=TZ)
    chat = make_chat()
    await chat.start()
    await to_topic(chat, "Вывих")
    clock.now = datetime(2026, 11, 12, 22, 6, tzinfo=TZ)  # quiet hours from 22:00
    assert await nudges(chat) == []
    clock.now = datetime(2026, 11, 13, 8, 5, tzinfo=TZ)  # and not in the morning either
    assert await nudges(chat) == []


async def test_no_reminder_without_an_unfinished_booking(make_chat, clock, kb) -> None:
    from tests.test_conversation import book_dislocation

    chat = make_chat()
    await chat.start()  # the menu only
    clock.now = NOW + timedelta(minutes=16)
    assert await nudges(chat) == []
    await chat.press(texts.MENU_ASK)  # a question is not a booking
    clock.now = NOW + timedelta(minutes=40)
    assert await nudges(chat) == []
    await book_dislocation(chat)  # booked: nothing is unfinished
    clock.now = NOW + timedelta(minutes=80)
    assert await nudges(chat) == []


async def test_handoff_consent_is_not_an_unfinished_booking(make_chat, clock) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_ADMIN)  # consent for the administrators' call
    clock.now = NOW + timedelta(minutes=16)
    assert await nudges(chat) == []
