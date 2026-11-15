"""A simple patient is booked: the time in words, no screen taken away, «передайте» typed.

Run 8, text_only: the patient does not press buttons («принципиально») and wrote «тринадцать
тридцать, к Кузнецову» three times; the bot did not read the time, replaced the screen of the
time with «Похоже, я не могу ответить на этот вопрос. Передать его администратору?», did not
take «Да, передайте администратору» typed in answer — and the dialog ended with «Запись не
оформлена». In run 6 the same patient was booked in seven turns.

- A time said in words is read by the code and checked against the buttons of the screen
  and the free time of the day chosen.
- On the steps of a booking that line does not stand in for the screen: the third answer
  that is not understood gets the same screen with «Не могу разобрать ответ. Нажмите кнопку
  под сообщением или позовите администратора» and the button «Позвать администратора».
- «Передайте администратору», «да, передайте», «пусть администратор запишет» typed are the
  button, on any step.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

import pytest

from app.bot import texts
from app.flow.commands import find_command
from app.flow.spoken import digits
from tests.test_conversation import bookings
from tests.test_intents import session

CANNOT_READ = "Не могу разобрать ответ. Нажмите кнопку под сообщением или позовите администратора."
STUCK_ASK = "Похоже, я не могу ответить на этот вопрос. Передать его администратору?"


@pytest.mark.parametrize(
    ("said", "read"),
    [
        ("тринадцать тридцать", "13:30"),
        ("Запишите к Кузнецову, на тринадцать тридцать", "Запишите к Кузнецову, на 13:30"),
        ("в тринадцать часов", "в 13 часов"),
        ("в два", "в 2"),
        ("давайте в три, если можно", "давайте в 3, если можно"),
        ("в час дня", "в 1 дня"),
        ("к шести вечера", "к 6 вечера"),
        ("после пяти", "после 5"),
        ("полвторого", "13:30"),
        ("в полвторого", "в 13:30"),
        ("половина третьего", "14:30"),
        ("полдевятого", "8:30"),
        ("двадцать один ноль ноль", "21:00"),
        ("девять пятнадцать утра", "9:15 утра"),
        ("в четырнадцать ноль ноль", "в 14:00"),
        # not a time: an age, a count, an hour as a span
        ("сыну три года", "сыну три года"),
        ("упал два раза", "упал два раза"),
        ("болит уже час", "болит уже час"),
        ("жду два часа", "жду два часа"),
        ("у меня двое детей", "у меня двое детей"),
    ],
)  # fmt: skip
def test_a_time_said_in_words(said: str, read: str) -> None:
    assert digits(said) == read


async def to_the_time(chat, *, day: str = "Сегодня") -> None:
    """A dislocation by buttons up to the grid of the time of a day."""
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    for label in ("Рука или нога", "Похоже на вывих", texts.AUDIENCE_SELF, "Бытовая травма",
                  "В течение 7 дней", "Нет, не обращались"):  # fmt: skip
        await chat.press(label)
    await chat.pick_day(day)
    if chat.periods():
        await chat.pick_period()
    assert "13:30" in chat.labels() and (await session(chat))["step"] == "times"


@pytest.mark.parametrize(
    "said",
    [
        "Вот и хорошо. Пишу словами: тринадцать тридцать, к Кузнецову.",  # run 8, word for word
        "Я же написал русским языком: тринадцать тридцать, сегодня, к Кузнецову.",
        "запишите на тринадцать тридцать",
        "полвторого",
        "давайте в полвторого",
    ],
)
async def test_a_time_in_words_presses_its_button(make_chat, said: str) -> None:
    chat = make_chat()
    await to_the_time(chat)
    await chat.say(said)
    state = await session(chat)
    assert state["step"] == "consent", chat.text  # the time is chosen: the next step
    await chat.press(texts.CONSENT_YES)
    await chat.say("Савельев Григорий Аркадьевич")
    await chat.say("22.02.1969")
    await chat.contact("+79001110016")
    (booking,) = await bookings(chat.conversation.db)
    assert f"{booking.start_at.astimezone(chat.conversation.settings.tz):%H:%M}" == "13:30"


@pytest.mark.parametrize(("said", "near"), [("в час дня", "13:00"), ("в два", "14:00")])
async def test_an_hour_in_words_is_a_wish_for_that_hour(make_chat, said: str, near: str) -> None:
    chat = make_chat()
    await to_the_time(chat)
    await chat.say(said)
    state = await session(chat)
    assert state["wish"]["at"] == f"{near}:00", chat.text
    assert "Не понял сообщение" not in chat.text


async def test_the_third_answer_not_understood_keeps_the_screen(make_chat) -> None:
    chat = make_chat()
    await to_the_time(chat)
    screen = chat.replies[-1]
    for _ in range(2):
        await chat.say("ну запишите уже как-нибудь по-человечески")
        assert chat.replies[-1].text == screen.text
    for _ in range(3):  # the third, and every one after it
        await chat.say("ну запишите уже как-нибудь по-человечески")
        line, shown = chat.replies
        assert line.text == CANNOT_READ
        assert shown.text == screen.text  # the same screen, with its buttons
        assert set(b.label for row in screen.buttons for b in row) <= set(chat.labels())
        assert chat.labels()[-1] == texts.MENU_ADMIN
        assert STUCK_ASK not in chat.text and texts.STUCK not in chat.text
    assert (await session(chat))["step"] == "times"
    await chat.press("13:30")  # the buttons of the step work
    assert (await session(chat))["step"] == "consent"


HAND_IT = [
    "Передайте администратору, пусть запишут. Мне на сегодня к Кузнецову надо.",
    "Да, передайте администратору.",
    "да, передайте",
    "пусть администратор запишет",
    "Передайте вопрос администратору, я уже три раза просил.",
]


@pytest.mark.parametrize("said", HAND_IT)
def test_pass_it_to_the_administrator_is_the_button(said: str) -> None:
    assert find_command(said) == "admin"


@pytest.mark.parametrize(
    "said", ["не надо ничего передавать администратору", "передайте привет доктору", "да, приду"]
)
def test_what_does_not_ask_for_the_administrator(said: str) -> None:
    assert find_command(said) != "admin"


@pytest.mark.parametrize("said", HAND_IT)
async def test_typed_on_a_step_of_a_booking_it_goes_to_the_administrator(
    make_chat, said: str
) -> None:
    chat = make_chat(admin_chat_id=-100)
    await to_the_time(chat)
    await chat.say(said)
    state = await session(chat)
    assert state["resume"] == "handoff" and state["step"] == "consent", chat.text
    assert "Чтобы администратор мог с Вами связаться" in chat.text
