"""The fixes after the walk through every screen on a phone (the customer's list of 13.11)."""

from __future__ import annotations

from app.bot import texts
from app.bot.conversation import Reply
from app.bot.telegram import markup
from app.flow.commands import normalize
from app.flow.loader import KnowledgeBase
from tests.chat_harness import Chat, to_topic
from tests.conftest import TZ

GROUPS = [
    "Рука или нога", "Рана, ожог, укус", "Голова, лицо, глаза, уши", "Шея, спина, грудь, живот",
    "Уже лечусь у вас", "Ребёнок до 6 месяцев", "Другое",
]  # fmt: skip


async def to_days(chat: Chat) -> None:
    await to_topic(chat, "Вывих")
    await chat.press("Для себя")
    await chat.press("Бытовая травма")
    await chat.press("В течение 7 дней")
    await chat.press("Нет, не обращались")


# 1. the topic groups at once ------------------------------------------------------------------


async def test_groups_are_on_the_first_screen_under_the_invitation(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    assert "Шаг 1 из 5" in chat.text and texts.ASK_COMPLAINT in chat.text
    assert "Опишите своими словами" in chat.text  # the invitation stays above the buttons
    assert chat.labels() == [*GROUPS, texts.BACK]
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")  # three taps from the menu to the scenario
    assert texts.ASK_AUDIENCE in chat.text


async def test_back_from_a_group_returns_to_the_first_screen(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press("Рука или нога")
    await chat.press(texts.BACK)
    assert texts.ASK_COMPLAINT in chat.text and chat.labels() == [*GROUPS, texts.BACK]


# 2. few free times: the grid at once ----------------------------------------------------------


async def test_few_times_in_a_day_skip_the_part_of_the_day(make_chat, schedule) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("запишите к Кузнецову")
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")
    await chat.press("Для себя")
    await chat.press("Бытовая травма")
    await chat.press("В течение 7 дней")
    await chat.press("Нет, не обращались")
    await chat.pick_day("Сб")  # he is on duty from 20:00: evening and night, a few times
    times = [b.label for b in chat.times()]
    assert 1 <= len(times) <= 8 and not chat.periods()
    assert times[0] >= "20:00" and times[-1] >= "23:00"  # both parts of the day in one grid
    assert "Врач смены: Кузнецов Николай Александрович (с 20:00)" in chat.text
    await chat.say("другое время")  # nothing else that day: the days
    assert chat.days()


async def test_many_times_in_a_day_ask_the_part_of_the_day(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await to_days(chat)
    await chat.pick_day("Завтра")
    assert len(chat.periods()) == 4 and not chat.times()


# 5. one booking — one message -----------------------------------------------------------------


async def test_one_booking_is_one_message(make_chat) -> None:
    from tests.test_conversation import book_dislocation

    chat = make_chat()
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    await chat.say("мои записи")
    (only,) = chat.replies
    assert only.text.startswith("Ваша запись:\n12 ноября (четверг) в 1")
    assert "Петров Иван Сергеевич. Врач: Кузнецов Николай Александрович." in only.text
    assert chat.labels() == [texts.MOVE, texts.CANCEL]
    await chat.say("перенеси запись на завтра")
    (only,) = chat.replies
    assert only.text.startswith(
        "Ваша запись:\nПри переносе сразу покажу время по пожеланию: завтра"
    )


async def test_several_bookings_keep_the_title_and_a_message_each(make_chat) -> None:
    from tests.test_conversation import book_dislocation

    chat = make_chat()
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    await to_days(chat)
    await chat.pick_slot("Завтра")
    await chat.press("Петров Иван")
    await chat.press(texts.PAYMENT_SELF)
    await chat.say("мои записи")
    title, first, second = chat.replies
    assert title.text == texts.MY_TITLE and not title.buttons
    assert first.buttons and second.buttons


# 6, 15. the name and the phone ----------------------------------------------------------------


async def test_name_step_is_the_script_phrase_and_a_short_start(make_chat, kb) -> None:
    chat = make_chat()
    await chat.start()
    await to_days(chat)
    await chat.pick_slot()
    await chat.press(texts.CONSENT_YES)
    body = chat.text.split("\n\n", 1)[1]
    assert body == f"{kb.common.identify.text}\nНачнём с ФИО.\n{texts.WHY_NAME}"
    await chat.say("петров иван сергеевич")
    await chat.say("12.03.1985")
    assert chat.text == (
        "Поделитесь, пожалуйста, номером телефона — нажмите кнопку «Поделиться контактом» внизу. "
        "Или напишите номер.\n" + texts.WHY_PHONE
    )


def test_contact_keyboard_has_back() -> None:
    keyboard = markup(Reply("телефон", ask_contact=True)).keyboard
    assert [[b.text for b in row] for row in keyboard] == [[texts.CONTACT_BUTTON], [texts.BACK]]
    assert keyboard[0][0].request_contact and not keyboard[1][0].request_contact


# 7, 9. the demo note is one line --------------------------------------------------------------


async def test_demo_notes_are_one_line(make_chat) -> None:
    from tests.test_conversation import book_dislocation

    chat = make_chat()
    await chat.start()
    assert chat.text.endswith(texts.BOT_DEMO_NOTE) and "не попадают" not in chat.text
    assert "«запишите к Ярунину на завтра»" in chat.text  # the one example that is not a button
    assert "подвернул ногу" not in chat.text and "где парковаться" not in chat.text
    await book_dislocation(chat)
    card = next(r for r in chat.replies if r.card is not None)
    assert card.text.endswith("Тестовая запись: в расписание клиники не попадает.")
    assert "\n" not in texts.DEMO_BOOKING and len(texts.DEMO_BOOKING) < 60
    assert "Стоимость консультации 2000 рублей (демо-цена), все дополнительные" in card.text


# 10. the doctor only above the times ----------------------------------------------------------


async def test_doctor_is_named_only_on_the_time_screen(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await to_days(chat)
    await chat.pick_day("Завтра")
    assert "13 ноября (пятница)" in chat.text and texts.PERIOD_PICK in chat.text
    assert "смены" not in chat.text and "Ярунин" not in chat.text
    await chat.pick_period("13:00")
    assert "Врач смены: Ярунин Андрей Александрович (08:00–20:00)" in chat.text


async def test_wish_line_stays_on_every_screen_of_the_time_step(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("запишите к Ярунину")
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")
    await chat.press("Для себя")
    await chat.press("Бытовая травма")
    await chat.press("В течение 7 дней")
    await chat.press("Нет, не обращались")
    line = "Пожелание: к Ярунину А. А."
    assert line in chat.replies[-1].text and chat.days()  # the days
    await chat.pick_day("Завтра")
    assert line in chat.text
    if chat.periods():  # the parts of the day, when there are many times
        await chat.pick_period()
        assert line in chat.text
    assert chat.times() and "Врач смены: Ярунин Андрей Александрович" in chat.text


# 11. answers that are unclear without the question --------------------------------------------


async def test_summary_words_the_yes_and_no_answers(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await to_days(chat)
    assert chat.replies[0].text.startswith(
        "Ваши ответы: вывих; для себя; бытовая травма; в течение 7 дней; к врачу не обращались."
    )
    await chat.start()
    await to_topic(chat, "Повторный приём")
    await chat.press("Для себя")
    await chat.press("Да, в вашем травмпункте")
    await chat.press("Нет")
    assert chat.replies[0].text == (
        "Ваши ответы: повторный приём; для себя; первично лечились в вашем травмпункте; "
        "больничный не открыт."
    )


def test_every_yes_or_no_button_has_its_words_for_the_summary(kb: KnowledgeBase) -> None:
    """«Да», «нет», «нет, не обращались» mean nothing without the question above them."""
    missing = []
    for scenario in kb.scenarios.values():
        for question in scenario.questions:
            for choice in question.buttons():
                if normalize(choice.label).split()[:1] not in (["да"], ["нет"]):
                    continue
                keys = [(name, str(value)) for name, value in choice.facts.items()]
                if not any(key in texts.SUMMARY_ANSWERS for key in keys):
                    missing.append((scenario.number, choice.label, keys))
    assert missing == []
    for words in texts.SUMMARY_ANSWERS.values():
        assert words[0].islower() and not words.endswith(".")


# 13. labels that fit --------------------------------------------------------------------------


def test_long_labels_are_shorter(kb: KnowledgeBase) -> None:
    fracture = [c.label for c in kb.scenario("fracture").question("how").buttons()]
    assert "Открытый или со смещением" in fracture
    sick = kb.scenario("sick_leave").fields()["help_provided"]
    assert [o.label for o in sick.options if o.value == "sick_leave_open_here"] == [
        "Больничный открыт у вас"
    ]
    for scenario in kb.scenarios.values():
        for question in scenario.questions:
            for choice in question.buttons():
                assert len(choice.label) <= 30, choice.label


async def test_nearest_time_of_the_wished_doctor_names_him(make_chat, clock) -> None:
    from datetime import datetime

    clock.now = datetime(2026, 11, 13, 12, 0, tzinfo=TZ)  # tomorrow he is not on duty
    chat = make_chat()
    await chat.start()
    await chat.say("запиши к Ярунину на завтра")
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")
    await chat.press("Для себя")
    await chat.press("Бытовая травма")
    await chat.press("В течение 7 дней")
    await chat.press("Нет, не обращались")
    first = chat.replies[-1].buttons[0][0].label
    assert first.startswith("Ярунин: сегодня, 1") and len(first) <= 30
