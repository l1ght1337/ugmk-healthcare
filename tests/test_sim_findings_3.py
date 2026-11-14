"""Findings of the partial simulation run after the third round of fixes (14.11.2026).

- two_injuries: «посмотрели обе травмы» from one patient was read as two patients («Запишу по
  очереди…», then «Теперь запишем второго пациента?»).
- needle: «меня там примут без записи?» was read as a request to book — «Не совсем понял Вашу
  просьбу» three times in a row; the phrase did not count as «не понял».
- tick, two_injuries: a long goodbye with a word about the injury started a booking again.
- teen16: on the screen of a patient of 15–17 «буду ждать звонка» got «Продолжаем запись», and
  «родитель не рядом, значит жду звонка, да?» got «Мы на этом же шаге».
- questions: «а сколько стоит приём? и на ближайшее можно?» on the day step got «Не понял
  сообщение» twice.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

from datetime import date, time
from itertools import pairwise

import pytest

from app.bot import texts
from app.config import ClinicData
from app.flow.commands import (
    asks_when_admin,
    find_small_talk,
    keeps_booking,
    names_several,
    waits_for_call,
)
from app.flow.loader import KnowledgeBase
from app.flow.wishes import parse as parse_wish
from tests.fake_deepseek import FakeDeepSeek
from tests.test_safeguards import to_birth
from tests.test_sim_findings_2 import MENU, handed, session
from tests.test_wish_flow import through_dislocation

TODAY = date(2026, 11, 12)
DOCTORS = ("Кузнецов Николай Александрович", "Ярунин Андрей Александрович")
TWO_INJURIES = (
    "Здравствуйте! Упал с велосипеда: порезал ногу и, кажется, вывихнул плечо. "
    "Можно записаться, чтобы посмотрели обе травмы?"
)
BYE_TICK = (
    "Ладно, спасибо хоть за это. Пойду сама тогда разбираться, а то с вами до вечера "
    "просижу с клещом. До свидания"
)
BYE_ALONE = (
    "Второго пациента нет, я один. Просто напомню на приёме: порез на ноге и плечо. "
    "Спасибо, до свидания."
)

# --- one patient with two injuries -----------------------------------------------------------


@pytest.mark.parametrize(
    "said",
    [TWO_INJURIES, "у меня обе раны кровят", "оба пореза глубокие", "болят оба места",
     "обе записи оставьте", "обе травмы у меня", "оба ушиба на ноге", "обе ладони обжёг"],
)  # fmt: skip
def test_both_injuries_are_one_patient(said: str) -> None:
    assert not names_several(said)


@pytest.mark.parametrize(
    "said",
    ["запишите нас обоих", "мы с мужем оба упали", "обе дочки ушиблись", "двоих детей",
     "меня и сына"],
)  # fmt: skip
def test_both_people_are_still_two_patients(said: str) -> None:
    assert names_several(said)


async def test_two_injuries_of_one_patient_are_not_booked_by_turns(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say(TWO_INJURIES)
    assert texts.ONE_BY_ONE not in chat.text and texts.WHO_FIRST not in chat.text
    assert (await session(chat))["more"] is False


# --- «без записи» is not a request to book -----------------------------------------------------


@pytest.mark.parametrize(
    "said",
    ["меня там примут без записи?", "Меня там вечером без записи примут?",
     "можно прийти без записи завтра?"],
)  # fmt: skip
def test_without_a_booking_is_not_a_booking_word(said: str) -> None:
    assert not parse_wish(said, DOCTORS, TODAY, now=time(12, 0)).booking


def test_a_booking_word_is_still_a_request() -> None:
    assert parse_wish("запишите на завтра без очереди", DOCTORS, TODAY, now=time(12, 0)).booking


async def test_a_question_about_coming_without_a_booking_is_not_a_request(
    make_chat, kb: KnowledgeBase
) -> None:
    fake = FakeDeepSeek({"type": "clinic_question"}, {"found": False})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say(
        "Центр на Ясной круглосуточный? Если я поеду сейчас вечером, меня там примут без записи?"
    )
    assert chat.text == texts.INFO_UNKNOWN and texts.REQUEST_UNCLEAR not in chat.text
    assert texts.ASK_ADMIN_QUESTION in chat.labels()


async def test_an_unclear_request_is_not_said_three_times(make_chat, kb: KnowledgeBase) -> None:
    replies = [{"type": "clinic_question"}, {"found": False}] * 3
    chat = make_chat(assistant=FakeDeepSeek(*replies).assistant(kb))
    await chat.start()
    for _ in range(2):
        await chat.say("запишите нас с мужем обоих, если получится")
        assert chat.text == texts.REQUEST_UNCLEAR
    await chat.say("запишите нас с мужем обоих, если получится")
    assert chat.text == texts.STUCK and texts.MENU_ADMIN in chat.labels()


# --- a long goodbye -----------------------------------------------------------------------------


@pytest.mark.parametrize("said", [BYE_TICK, BYE_ALONE])
def test_a_long_goodbye_is_a_goodbye(said: str) -> None:
    assert find_small_talk(said) == "bye"


@pytest.mark.parametrize(
    "said",
    [
        # something is asked for, or the message does not end with the goodbye
        "Сын упал с качели, рука опухла, хотим приехать сегодня вечером, подскажите как это "
        "сделать побыстрее, спасибо большое заранее, до свидания",
        "Спасибо, до свидания. Хотя нет, подождите, я ещё хотела узнать про парковку возле "
        "вашего травмпункта, там вообще есть места для машин или лучше приехать на такси",
        "Спасибо, а в центре на Ясной меня точно примут сегодня вечером без записи или лучше "
        "ехать туда завтра с утра? До свидания",
    ],
)
def test_a_long_message_with_something_else_is_not_a_goodbye(said: str) -> None:
    assert find_small_talk(said) is None


@pytest.mark.parametrize("said", [BYE_TICK, BYE_ALONE])
async def test_a_long_goodbye_does_not_start_a_booking(
    make_chat, kb: KnowledgeBase, said: str
) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say(said)
    assert chat.text == kb.common.goodbye.text and chat.labels() == MENU
    assert (await session(chat))["step"] == "idle" and fake.requests == []


# --- the screen of a patient of 15–17 ----------------------------------------------------------


@pytest.mark.parametrize(
    "said",
    ["ладно, буду ждать звонка. если что родителям скажу",
     "не, родитель не рядом. значит жду звонка, да?",
     "ну так родитель не рядом, я один. звонка ждать или че?", "да жду звонка, че непонятно",
     "родителей нет рядом", "подожду звонка"],
)  # fmt: skip
def test_words_of_waiting_for_the_call(said: str) -> None:
    assert waits_for_call(said)


@pytest.mark.parametrize(
    "said", ["а че нельзя просто записаться? мне 16, сам могу", "мама рядом", "не хочу ждать"]
)
def test_words_that_do_not_wait_for_the_call(said: str) -> None:
    assert not waits_for_call(said)


async def to_teen(chat) -> None:
    await to_birth(chat, texts.AUDIENCE_SELF)
    await chat.say("09.09.2010")
    await chat.contact()
    assert (await session(chat))["step"] == "teen"


async def test_a_teen_who_waits_for_the_call_is_told_so(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await to_teen(chat)
    await chat.say("ладно, буду ждать звонка. если что родителям скажу")
    (first,) = chat.replies  # one line: the screen is not drawn once more
    assert first.text.startswith(f"{texts.TEEN_WAIT} Ваше обращение № 1 уже у администратора")
    assert chat.labels() == [texts.TEEN_PARENT, texts.MENU]
    await chat.say("не, родитель не рядом. значит жду звонка, да?")
    (second,) = chat.replies
    assert second.text.startswith(f"{texts.TEEN_WAIT} Администратору передано")
    assert second.text != first.text and chat.labels() == [texts.TEEN_PARENT, texts.MENU]
    for reply in (first, second):
        for wrong in (texts.GOING_ON, texts.SAME_STEP, "Вам 16 лет"):
            assert wrong not in reply.text
    assert (await session(chat))["step"] == "teen" and fake.requests == []
    assert len(chat.admin) == 1  # nothing new went to the administrators
    await chat.press(texts.TEEN_PARENT)  # the parent can still take the booking over
    assert texts.CONSENT_YES in chat.labels()


async def test_any_other_text_of_a_teen_is_one_line_with_the_buttons(make_chat) -> None:
    chat = make_chat()
    await to_teen(chat)
    await chat.say("а че нельзя просто записаться? мне 16, сам могу")
    (only,) = chat.replies
    assert only.text == texts.TEEN_AGAIN and chat.labels() == [texts.TEEN_PARENT, texts.MENU]


# --- a question on a step answered by a button --------------------------------------------------


@pytest.mark.parametrize(
    "route",
    [{"type": "menu_action", "action": "book", "question": True},
     {"type": "menu_action", "action": "book"},
     {"type": "offtopic"}],
)  # fmt: skip
async def test_a_question_on_the_day_step_is_answered(
    make_chat, kb: KnowledgeBase, clinic: ClinicData, route: dict
) -> None:
    price = f"Первичный приём — {clinic.price_text('primary_visit')}."  # the sum from config
    fake = FakeDeepSeek(route, {"found": True, "answer": price})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await through_dislocation(chat)
    await chat.say("а сколько стоит приём? и на ближайшее можно?")
    answer, screen = chat.replies
    assert answer.text == price and texts.HINT_TIME not in chat.text
    assert texts.DAY_PICK in screen.text and chat.labels()[0].startswith("Ближайшее")


async def test_a_question_without_an_answer_on_the_day_step_is_said_so(
    make_chat, kb: KnowledgeBase
) -> None:
    fake = FakeDeepSeek({"type": "menu_action", "action": "book"}, {"found": False})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await through_dislocation(chat)
    await chat.say("а сколько стоит МРТ колена? и на ближайшее можно?")
    note, screen = chat.replies
    assert note.text == texts.ASIDE_UNKNOWN and texts.DAY_PICK in screen.text


async def test_a_text_that_is_not_a_question_on_the_day_step_gets_the_hint(
    make_chat, kb: KnowledgeBase
) -> None:
    fake = FakeDeepSeek({"type": "offtopic"})
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await through_dislocation(chat)
    await chat.say("абракадабра какая-то")
    assert chat.replies[0].text == texts.HINT_TIME and len(fake.requests) == 1


# --- after run 3 (not checked by a live run) ---------------------------------------------------
# teen16: four different questions on the screen of a patient of 15–17 got the same line four
# times. price_xray: «Нет, спасибо, больше ничего. Буду ждать звонка администратора, надеюсь…»
# (17 words) was added to the request twice. cancel_by_day: «значит запись на 13 ноября
# осталась. Спасибо, больше ничего не нужно» showed the booking once more.

STAYED = "Отлично, значит запись на 13 ноября осталась. Спасибо, больше ничего не нужно."
WAITS_LONG = (
    "Нет, спасибо, больше ничего. Буду ждать звонка администратора, надеюсь, он мне всё про "
    "рентген и оплату объяснит."
)


@pytest.mark.parametrize(
    "said", ["а долго ждать администратора? я хочу сегодня, нога реально болит",
             "ну так а когда он ответит? мне бы сегодня, нога опухла уже",
             "когда ответит администратор?", "долго ли ждать звонка?"],
)  # fmt: skip
def test_how_long_to_wait_for_the_administrator(said: str) -> None:
    assert asks_when_admin(said)


@pytest.mark.parametrize(
    "said", ["долго ждать в очереди?", "когда ответит врач на приёме?", "долго заживает перелом?"]
)
def test_not_about_the_administrators_call(said: str) -> None:
    assert not asks_when_admin(said)


async def test_a_teen_never_reads_the_same_line_twice_in_a_row(
    make_chat, kb: KnowledgeBase
) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await to_teen(chat)
    said: list[str] = []
    for text in (
        "а сам я не могу записаться? нога болит, хочу сегодня попасть",
        "а долго ждать администратора? я хочу сегодня, нога реально болит",
        "ну так а когда он ответит? мне бы сегодня, нога опухла уже",
        "ладно, а если мама со мной придёт или она сама запишет, сегодня получится?",
        "ну а так-то че делать",
        "ну а так-то че делать то",
    ):
        await chat.say(text)
        (only,) = chat.replies  # one line each time, with the buttons of the screen
        assert chat.labels() == [texts.TEEN_PARENT, texts.MENU]
        assert "Вам 16 лет" not in only.text and only.text != texts.STUCK
        said.append(only.text)
    hours = "Администраторы отвечают с 08:00 до 20:00"
    assert said[0] == texts.TEEN_AGAIN
    assert said[1].startswith(hours) and not said[2].startswith(hours)  # not the hours twice
    assert said[3] == texts.TEEN_PARENT_CAN  # «мама сама запишет»: the parent can
    assert all(a != b for a, b in pairwise(said))
    assert (await session(chat))["step"] == "teen"
    assert len(chat.admin) == 1  # nothing was added to the request


def test_the_teen_screen_is_made_of_the_same_sentences() -> None:
    assert texts.TEEN.endswith(texts.TEEN_PARENT_CAN)


@pytest.mark.parametrize("said", [STAYED, "запись на пятницу осталась, спасибо"])
def test_the_booking_has_stayed(said: str) -> None:
    assert keeps_booking(said)


@pytest.mark.parametrize(
    "said", ["Спасибо, а запись на 13 ноября в 11:00 осталась?", "осталась боль в ноге"]
)
def test_a_question_whether_it_stayed_is_not_a_statement(said: str) -> None:
    assert not keeps_booking(said)


def test_a_long_thanks_that_waits_for_the_call_is_small_talk() -> None:
    assert find_small_talk(WAITS_LONG) == "thanks"


async def test_waiting_for_the_call_in_a_long_message_adds_nothing_to_the_request(
    make_chat, kb: KnowledgeBase
) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await handed(chat)
    posted = len(chat.admin)
    await chat.say(WAITS_LONG)
    assert chat.text == texts.WELCOME and chat.labels() == MENU
    assert len(chat.admin) == posted and fake.requests == []
