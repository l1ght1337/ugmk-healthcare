"""The patient simulation of 14.11 (docs/simulation_report.md), fixes 1–4.

1. A full name inside a sentence, a name with the birth date (and the phone) in one message;
   after the second name that is not read — another line and «Позвать администратора».
2. A time and «согласен» inside a sentence press the button.
3. «Записать мужа… после моего приёма» and a question about the visit are not «Мои записи»;
   «я уже записан, новую не надо» leaves the booking under way.
4. A question together with the data on the name, birth date and phone steps does not throw
   the patient out of the booking.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.bot import texts
from app.db.models import Patient, User
from app.flow.commands import find_command, gives_consent, refuses_new_booking
from app.flow.identity import find_full_name, find_phone
from app.flow.loader import KnowledgeBase
from tests import intent_cases as m
from tests.chat_harness import Chat
from tests.fake_deepseek import FakeDeepSeek
from tests.test_conversation import book_dislocation, bookings
from tests.test_intents import session, to_step

ELDER = (
    "Громова Нина Васильевна, родилась 2 февраля 1952 года. Телефон мой +79001110002. "
    "Согласна я, конечно, на обработку, записывайте, пожалуйста, милая."
)


async def user(chat: Chat) -> User:
    async with chat.conversation.db() as s:
        return await s.get(User, chat.user.id)


async def patients(chat: Chat) -> list[Patient]:
    from sqlalchemy import select

    async with chat.conversation.db() as s:
        return list((await s.scalars(select(Patient).order_by(Patient.id))).all())


# --- 1. the full name inside a sentence ---------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "name"),
    [
        ("Громова Нина Васильевна. Это я, значит, сама и есть, записываюсь я к вам, милая.",
         ("Громова", "Нина", "Васильевна")),
        ("Я же написал: Савельев Григорий Аркадьевич. Больше мне добавить нечего.",
         ("Савельев", "Григорий", "Аркадьевич")),
        ("САВЕЛЬЕВ ГРИГОРИЙ АРКАДЬЕВИЧ. Пишу заглавными, может так дойдёт.",
         ("Савельев", "Григорий", "Аркадьевич")),
        ("Савельев Григорий Аркадьевич. Три слова: Савельев, Григорий, Аркадьевич. Что вам ещё "
         "непонятно?", ("Савельев", "Григорий", "Аркадьевич")),
        ("Громова Нина Васильевна — вот мои фамилия, имя и отчество, полностью.",
         ("Громова", "Нина", "Васильевна")),
        ("меня зовут иванов пётр сергеевич", ("Иванов", "Пётр", "Сергеевич")),
        ("а зачем вам мои ФИО? ладно, Юдина Марина Олеговна", ("Юдина", "Марина", "Олеговна")),
        ("Дроздов Павел Сергеевич, дата рождения 12.12.1986", ("Дроздов", "Павел", "Сергеевич")),
        ("Пациент — Мамедов Али Вели оглы, записывайте", ("Мамедов", "Али", "Вели оглы")),
        ("петров иван сергеевич", ("Петров", "Иван", "Сергеевич")),  # the whole text, as before
        ("Ли Вэй", ("Ли", "Вэй", None)),  # two words with nothing else are still a name
    ],
)  # fmt: skip
def test_a_full_name_is_found_inside_a_sentence(said: str, name: tuple) -> None:
    assert find_full_name(said) == name


@pytest.mark.parametrize(
    "said",
    [
        "я же всё написала",
        "ну что ещё надо",
        "ладно, записывайте уже меня наконец",
        "Я Захарова Любовь Тимофеевна, а муж Захаров Игорь Семёнович",  # two people: which one?
        "а зачем вам отчество?",
    ],
)
def test_what_is_not_a_full_name(said: str) -> None:
    assert find_full_name(said) is None


async def test_a_name_inside_a_sentence_is_taken_on_the_name_step(make_chat) -> None:
    chat = make_chat()
    await to_step(chat, m.NAME)
    await chat.say("Громова Нина Васильевна. Это я, значит, сама и есть, записываюсь я к вам.")
    assert texts.PATIENT_IS.format(name="Громова Нина Васильевна") in chat.text
    assert texts.ASK_BIRTH in chat.text and (await session(chat))["step"] == "birth"


@pytest.mark.parametrize(
    ("said", "born"),
    [
        ("Дроздов Павел Сергеевич, дата рождения 12.12.1986", date(1986, 12, 12)),
        ("Захарова Любовь Тимофеевна, 18 ноября 1979 года рождения. Это я, первая запись моя.",
         date(1979, 11, 18)),
        ("Агапов Михаил Степанович, 04.07.1980.", date(1980, 7, 4)),
    ],
)  # fmt: skip
async def test_a_name_with_the_birth_date_answers_both_steps(make_chat, said, born) -> None:
    chat = make_chat()
    await to_step(chat, m.NAME)
    await chat.say(said)
    (ask,) = chat.replies  # the phone is asked at once: no «Дата рождения пациента» in between
    assert ask.ask_contact and texts.ASK_BIRTH not in chat.text
    (patient,) = await patients(chat)
    assert patient.birth_date == born and (await session(chat))["step"] == "contact"


async def test_name_birth_date_and_phone_in_one_message_make_the_booking(make_chat) -> None:
    chat = make_chat()
    await to_step(chat, m.NAME)
    await chat.say("Громова Нина Васильевна, родилась 2 февраля 1952 года. Телефон +79001110002")
    assert "Нина Васильевна записана" in chat.text
    assert len(await bookings(chat.conversation.db)) == 1
    assert (await user(chat)).phone == "+79001110002"


async def test_two_people_in_the_name_are_asked_again(make_chat) -> None:
    chat = make_chat()
    await to_step(chat, m.NAME)
    await chat.say("Я Захарова Любовь Тимофеевна, а муж Захаров Игорь Семёнович")
    assert chat.text == texts.ASK_NAME_AGAIN and (await session(chat))["step"] == "name"


async def test_the_second_unread_name_gets_another_line_and_the_administrator(make_chat) -> None:
    chat = make_chat()
    await to_step(chat, m.NAME)
    await chat.say("я же всё написала")
    assert chat.text == texts.ASK_NAME_AGAIN and texts.MENU_ADMIN not in chat.labels()
    await chat.say("ну что ещё надо")
    assert chat.text == texts.NAME_HELP
    assert chat.labels() == [texts.MENU_ADMIN, texts.BACK, texts.MENU]
    await chat.say("милая, я не понимаю")  # and again: the way out stays on screen
    assert chat.text == texts.NAME_HELP and texts.MENU_ADMIN in chat.labels()
    await chat.say("Громова Нина Васильевна")
    assert (await session(chat))["step"] == "birth"
    await chat.press(texts.BACK)  # the name again: the count starts anew
    await chat.say("я же всё написала")
    assert chat.text == texts.ASK_NAME_AGAIN


# --- 2. a time and «согласен» inside a sentence -------------------------------------------------


async def test_a_time_in_a_sentence_presses_it_though_two_buttons_show_it(make_chat) -> None:
    chat = make_chat()
    await to_step(chat, m.TIMES)
    wanted = chat.times()[0].label  # a free time of tomorrow
    await chat.say("другой день")
    await chat.say(f"завтра в {wanted}")  # the wish: «Ближе всего к 11:00: 11:00» above the grid
    assert texts.WISH_NEAR_TIME.format(wanted=wanted, time=wanted) in chat.labels()
    assert [b.label for b in chat.times()].count(wanted) == 1
    slot = next(b.data for b in chat.times() if b.label == wanted).removeprefix("s:")
    await chat.say(f"Вот спасибо, родная. Записывайте меня, пожалуйста, на {wanted}.")
    state = await session(chat)
    assert (state["step"], state["slot_id"]) == ("consent", slot)
    assert texts.SAME_STEP not in chat.text


@pytest.mark.parametrize(
    "said",
    [
        "Давайте на ближайшее, сегодня, если можно, в {time}.",
        "Половина второго меня устроит. Запишите, пожалуйста, на {time}.",
        "Я же написал: сегодня в {time}. Запишите меня, пожалуйста, на это время.",
        "Ну вот, теперь видно {time}. Мне подходит ближайшее, сегодня в {time}.",
    ],
)
async def test_the_time_of_the_nearest_button_presses_it(make_chat, said: str) -> None:
    chat = make_chat()
    await to_step(chat, m.DAYS)
    nearest = chat.nearest()  # «Ближайшее: сегодня, 13:30»
    await chat.say(said.format(time=nearest.label[-5:]))
    state = await session(chat)
    assert (state["step"], state["slot_id"]) == ("consent", nearest.data.removeprefix("s:"))


@pytest.mark.parametrize(
    "said",
    [
        "только не на {time}",
        "{time} не подходит, давайте другое",
        "{time} или {other}",
        "а {time} точно свободно?",
        "сегодня в {time}",  # the grid shows tomorrow: another day is not this button
    ],
)
async def test_a_time_that_is_not_chosen_presses_nothing(make_chat, said: str) -> None:
    chat = make_chat()
    await to_step(chat, m.TIMES)
    first, *more = (b.label for b in chat.times())
    await chat.say(said.format(time=first, other=more[0] if more else "03:15"))
    state = await session(chat)
    assert state["step"] in ("days", "periods", "times") and state["slot_id"] is None, said


@pytest.mark.parametrize(
    "said",
    [
        "согласна, записывайте",
        "Согласна я, конечно, на обработку, записывайте, пожалуйста, милая.",
        "На обработку данных согласен.",
        "Да что ж такое. Я согласен на обработку данных, согласен. Всё вам написал словами.",
        "даю согласие на обработку",
        "Я кнопок не нажимаю. С обработкой данных согласен.",  # «не» is in another sentence
    ],
)
async def test_consent_inside_a_sentence_is_consent(make_chat, said: str) -> None:
    assert gives_consent(said)
    chat = make_chat()
    await to_step(chat, m.CONSENT)
    await chat.say(said)
    assert (await session(chat))["step"] == "name" and (await user(chat)).consent_at is not None


@pytest.mark.parametrize(
    "said",
    [
        "я не согласен на это",
        "нет, не согласна пока",
        "согласен, если данные никуда не уйдут",
        "согласна, но только на запись",
        "а с чем я согласен?",
        "согласен ли я? не знаю",
        "на что я соглашаюсь",
        "против обработки, отказываюсь",
        "согласие даю только при условии, что удалите потом",
        "записывайте",  # no word of consent at all
    ],
)
async def test_a_refusal_a_condition_or_a_question_is_not_consent(make_chat, said: str) -> None:
    assert not gives_consent(said)
    chat = make_chat()
    await to_step(chat, m.CONSENT)
    await chat.say(said)
    state = await session(chat)
    assert state["step"] == "consent" and state["wizard"], said  # the same screen
    assert (await user(chat)).consent_at is None
    assert texts.CONSENT_YES in chat.labels() and texts.CONSENT_DECLINED not in chat.text


async def test_the_exact_label_still_declines(make_chat) -> None:
    chat = make_chat()
    await to_step(chat, m.CONSENT)
    await chat.say("Не согласен")  # the label of the button typed as it is
    assert chat.text == texts.CONSENT_DECLINED


async def test_consent_name_birth_date_and_phone_in_one_message(make_chat) -> None:
    chat = make_chat()
    await to_step(chat, m.CONSENT)
    await chat.say(ELDER)
    assert "Нина Васильевна записана" in chat.text
    assert (await user(chat)).consent_at is not None
    (patient,) = await patients(chat)
    assert (patient.last_name, patient.birth_date) == ("Громова", date(1952, 2, 2))
    assert len(await bookings(chat.conversation.db)) == 1


# --- 3. «записать мужа», a question about the visit, «я уже записан» ----------------------------

HUSBAND = (
    "Да, хочу записать мужа — Захаров Игорь Семёнович, 05.05.1977, тоже подвернул ногу. Можно "
    "его на сегодня, желательно после моего приёма, чтобы вместе приехать."
)
HUSBAND_AGAIN = (
    "Спасибо, что оставили мою запись. Но подскажите, пожалуйста, как мне записать мужа? Мне "
    "нужен второй приём для Захарова Игоря Семёновича, 05.05.1977. Он тоже подвернул ногу."
)
SHOULDER = (
    "Спасибо, записали. Только напомните, плечо тоже посмотрят на приёме? Я из-за него рукой "
    "двигать не могу."
)


@pytest.mark.parametrize("said", [HUSBAND, HUSBAND_AGAIN, SHOULDER, "запишите ещё сына к вам"])
def test_booking_someone_else_or_asking_about_the_visit_is_not_my_bookings(said: str) -> None:
    assert find_command(said) != "my_bookings"


@pytest.mark.parametrize(
    "said",
    [
        "моя запись",
        "напомни про мои записи",
        "напомните, когда у меня приём",
        "напомните время приёма",
        "когда у меня приём?",
        "на когда я записан",
        "перенесите мою запись на завтра",
    ],
)
def test_requests_about_my_bookings_stay(said: str) -> None:
    assert find_command(said) == "my_bookings"


async def test_booking_the_husband_after_my_own_visit_starts_a_booking(make_chat) -> None:
    chat = make_chat()
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    await chat.say(HUSBAND)
    state = await session(chat)
    assert texts.MY_ONE not in chat.text and state["wizard"] and state["for_whom"] == "adult"
    assert state["step"] in ("topic", "complaint", "question"), state["step"]


@pytest.mark.parametrize(
    "said",
    [
        "Да не надо меня заново записывать, я уже записан на 12 ноября в 13:30!",
        "Мне не нужна новая запись, я уже записан",
        "Да не надо мне ничего заново оформлять! Я уже записан.",
        "я уже записана, вторую запись не надо",
    ],
)
async def test_i_am_booked_already_leaves_the_new_booking(make_chat, said: str) -> None:
    assert refuses_new_booking(said)
    chat = make_chat()
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    await chat.press(texts.MENU_BOOK)
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")  # a second booking under way: «Для кого запись?»
    assert (await session(chat))["wizard"]
    await chat.say(said)
    state = await session(chat)
    assert (state["step"], state["wizard"]) == ("idle", False)
    first, mine = chat.replies
    assert first.text == texts.NO_NEW_BOOKING and mine.text.startswith(texts.MY_ONE)
    assert chat.labels() == [texts.MOVE, texts.CANCEL]
    assert len(await bookings(chat.conversation.db)) == 1


@pytest.mark.parametrize(
    "said", ["я ещё не записан", "запишите меня заново", "не надо на завтра", "я уже был у врача"]
)
def test_what_is_not_a_refusal_of_a_new_booking(said: str) -> None:
    assert not refuses_new_booking(said)


async def test_without_a_booking_the_words_change_nothing(make_chat) -> None:
    chat = make_chat()
    await to_step(chat, m.QUESTION)
    await chat.say("я уже записан, новую не надо")
    assert texts.NO_NEW_BOOKING not in chat.text and (await session(chat))["wizard"]


# --- 4. a question together with the data -------------------------------------------------------


async def test_a_question_with_the_name_is_answered_and_the_name_is_taken(
    make_chat, kb: KnowledgeBase
) -> None:
    fake = FakeDeepSeek({"type": "about_bot"})
    chat = make_chat(assistant=fake.assistant(kb))
    await to_step(chat, m.NAME)
    await chat.say("а зачем вам мои ФИО? ладно, Юдина Марина Олеговна")
    answer, screen = chat.replies
    assert answer.text == texts.ABOUT_BOT
    assert texts.PATIENT_IS.format(name="Юдина Марина Олеговна") in screen.text
    assert (await session(chat))["step"] == "birth"


async def test_a_question_with_the_birth_date(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek({"type": "about_bot"})
    chat = make_chat(assistant=fake.assistant(kb))
    await to_step(chat, m.BIRTH)
    await chat.say("а зачем вам дата рождения? ладно, 25.04.1995")
    answer, ask = chat.replies
    assert answer.text == texts.ABOUT_BOT and ask.ask_contact


async def test_a_question_with_the_phone_does_not_open_the_questions_screen(
    make_chat, kb: KnowledgeBase
) -> None:
    fake = FakeDeepSeek({"type": "menu_action", "action": "ask"})  # as DeepSeek read it
    chat = make_chat(assistant=fake.assistant(kb))
    await to_step(chat, m.PHONE)
    await chat.say("а зачем вам телефон? ладно, +79001110010")
    assert texts.ASK_INFO not in chat.text and "записан" in chat.text
    assert len(await bookings(chat.conversation.db)) == 1
    assert (await user(chat)).phone == "+79001110010"


async def test_a_question_alone_on_the_name_step_keeps_the_booking(
    make_chat, kb: KnowledgeBase
) -> None:
    fake = FakeDeepSeek(
        {"type": "menu_action", "action": "ask"}, {"found": True, "answer": "Для записи."}
    )
    chat = make_chat(assistant=fake.assistant(kb))
    await to_step(chat, m.NAME)
    await chat.say("а зачем вам телефон?")
    state = await session(chat)
    assert (state["step"], state["wizard"]) == ("name", True)
    answer, screen = chat.replies
    assert answer.text == "Для записи." and texts.ASK_NAME in screen.text


@pytest.mark.parametrize(
    ("said", "phone"),
    [
        ("а зачем вам телефон? ладно, +79001110010", "+79001110010"),
        ("мой номер +7 900 111-00-10, звоните после 18", "+79001110010"),
        ("Телефон мой 8 (900) 111-00-02, родилась 02.02.1952", "+79001110002"),
        ("89001110002", "+79001110002"),
        ("9001110002", "+79001110002"),
    ],
)
def test_a_phone_is_found_inside_a_sentence(said: str, phone: str) -> None:
    assert find_phone(said) == phone


@pytest.mark.parametrize("said", ["родилась 02.02.1952", "12345", "в 13:30, кабинет 2"])
def test_what_is_not_a_phone(said: str) -> None:
    assert find_phone(said) is None


async def test_a_phone_among_other_numbers_is_taken(make_chat) -> None:
    chat = make_chat()
    await to_step(chat, m.PHONE)
    await chat.say("мой номер +7 900 111-00-10, звоните после 18")
    assert (await user(chat)).phone == "+79001110010"
    assert len(await bookings(chat.conversation.db)) == 1
