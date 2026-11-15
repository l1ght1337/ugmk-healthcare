"""Two children named with commas, and what is said about both of them at once.

From the fifth simulation run (two_kids, 25 turns, one of the two booked): «Маша, 7 лет, руку
ушибла, и Ваня, 10 лет, ногу подвернул» — the name, the age and the injury stood in three
parts of the sentence, and the bot found nobody: it asked DeepSeek about both, then asked each
child «для кого», the age and the three questions of step 3, and showed «Кого записываем?»
with the sister on it for the brother.

Now the names, the ages and the injuries are read across the commas; what the message says
about all of them («сегодня с горки упали», «к врачу не обращались») is kept and answers the
questions of each child — the bot shows «Ваши ответы: …» with «Изменить ответы» instead of
asking; the second child is not offered the first one's name. The first child is booked in 9
turns, the second in 6 — when DeepSeek's reading of «руку ушибла» opens the whole menu of
«Рука или нога», as in that run; in 8 and 5 when it opens «На что больше похоже?» at once.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import httpx2
import pytest

from app.bot import texts
from app.db.models import Patient
from app.flow.loader import KnowledgeBase
from app.flow.people import find_people, shared
from tests.chat_harness import Chat
from tests.fake_deepseek import FakeDeepSeek
from tests.test_conversation import bookings
from tests.test_intents import session
from tests.test_two_named import KIDS

FIRST = (
    "Здравствуйте! Мы сегодня с горки упали, двое детей. Маша, 7 лет, руку ушибла, и Ваня, "
    "10 лет, ногу подвернул. К врачу ещё не обращались. Надо обоих записать."
)
SECOND = (
    "У Маши ушиб руки, а у Вани нога подвернулась, вроде ушиб тоже. Перелома не видно, но "
    "ходит плохо."
)
TOLD = ["Мы сегодня с горки упали", "К врачу ещё не обращались"]
WHO_FIRST = f"{texts.ONE_BY_ONE} {texts.WHO_FIRST}"
UNCLEAR = {
    "type": "complaint",
    "area": "limb",
    "scenario": None,
    "facts": {},
    "candidates": [],
    "clarify": None,
}
WIDE = {**UNCLEAR, "candidates": ["dislocation", "dressing"]}  # the menu of «Рука или нога»
FALL = "Упал, ударился, подвернул"
ANSWERED = {
    "facts": {"assault": False, "injury_age": "within_7_days", "seen_doctor": "no"},
    "unclear": False,
    "clarify": None,
    "crisis": False,
}
RUN_1 = (  # the first message of the live run after the change
    "Здравствуйте! У меня двое детей сегодня с горки упали, обоих надо к врачу. Маша 7 лет руку "
    "ушибла, Ваня 10 лет ногу подвернул. Запишите обоих, пожалуйста"
)
COUPLE = "Добрый день. Мы с мужем в походе подвернули ногу, оба. Хотим прийти завтра утром."
ME_FIRST = (
    "Давайте сначала меня, Любовь Тимофеевна Захарова, 18.11.1979 года рождения. А потом мужа, "
    "Захарова Игоря Семёновича, 05.05.1977 года рождения."
)


def own_field(asked: dict[str, Any]) -> dict[str, Any]:
    """DeepSeek as it answered in the live run: the field of the question asked, no other."""
    facts = {k: v for k, v in ANSWERED["facts"].items() if k in asked["fields"]}
    return {**ANSWERED, "facts": facts}


SUMMARY = (
    "Ваши ответы: не знаю, на что похоже; для ребёнка, {age}; бытовая травма; в течение 7 "
    "дней; к врачу не обращались."
)


class ByTask(FakeDeepSeek):
    """DeepSeek that answers by the task of the request: «route» or «extract»."""

    def __init__(self, **by_task: Any) -> None:
        super().__init__()
        self.by_task = by_task

    async def _handle(self, request: httpx2.Request) -> httpx2.Response:
        asked = sent(json.loads(request.content))
        reply = self.by_task[asked["task"]]
        self.replies.insert(0, reply(asked) if callable(reply) else reply)
        return await super()._handle(request)

    def texts_of(self, task: str) -> list[str]:
        return [s["text"] for s in map(sent, self.requests) if s["task"] == task]


def sent(request: dict[str, Any]) -> dict[str, Any]:
    return json.loads(request["messages"][1]["content"])


class Counted:
    """A chat that counts what the patient does: a message, a button, the contact."""

    def __init__(self, chat: Chat) -> None:
        self.chat, self.turns = chat, 0

    async def say(self, text: str) -> None:
        self.turns += 1
        await self.chat.say(text)

    async def press(self, label: str) -> None:
        self.turns += 1
        await self.chat.press(label)

    async def contact(self) -> None:
        self.turns += 1
        await self.chat.contact()


# --- the names, the ages and the injuries across the commas -----------------------------------


def people(text: str) -> list[tuple[str, str | None, int | None, str]]:
    return [(p.name, p.audience, p.age_months, p.complaint) for p in find_people(text)]


def test_a_name_an_age_and_an_injury_between_commas() -> None:
    assert people(FIRST) == [
        ("Маша", "child", 84, "руку ушибла"),
        ("Ваня", "child", 120, "ногу подвернул"),
    ]


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("Сын, 12 лет, упал с велосипеда", [("сын", "child", 144, "упал с велосипеда")]),
        ("Маша, руку ушибла, а Ваня, ногу подвернул",
         [("Маша", None, None, "руку ушибла"), ("Ваня", None, None, "ногу подвернул")]),
        ("Дочка Маша, 7 лет, и сын, ему 10, оба упали",
         [("дочка", "child", 84, ""), ("сын", "child", 120, "")]),
        ("Олег, 40 лет, ногу подвернул", [("Олег", "adult", 480, "ногу подвернул")]),
        # the age stands right after the name, not after what happened
        ("У сына болит нога, 2 месяца", [("сын", "child", None, "болит нога")]),
        # a doctor, a greeting, a name with nothing about it
        ("Запишите к Андрею Александровичу, болит нога", []),
        ("Запишите к Андрею, 7 лет ребёнку", [("ребёнок", "child", 84, "")]),
        ("Маша, Ваня, здравствуйте", []),
    ],
)  # fmt: skip
def test_who_is_named_in_parts_of_a_sentence(text: str, found: list) -> None:
    assert people(text) == found


def test_my_two_children_are_not_me() -> None:
    assert people(RUN_1) == [
        ("Маша", "child", 84, "руку ушибла"),
        ("Ваня", "child", 120, "ногу подвернул"),
    ]
    # how and when is said with the count of the children; «обоих надо к врачу» says nothing
    assert shared(RUN_1) == ["У меня двое детей сегодня с горки упали"]
    assert people("У меня запястье болит, у сына колено")[0][:2] == ("", "self")


def test_a_request_to_book_is_not_about_the_injury() -> None:
    assert shared("Хотели бы записаться к вам на сегодня или на завтра, если можно.") == []
    assert shared("Упали сегодня на катке, я и сын 12 лет. Нужно записать обоих.") == [
        "Упали сегодня на катке"
    ]


def test_what_is_said_about_all_of_them() -> None:
    assert shared(FIRST) == TOLD  # not the greeting, not «двое детей», not «надо обоих записать»
    assert shared(KIDS) == ["дети сегодня с горки упали"]
    assert shared(SECOND) == ["Перелома не видно, но ходит плохо"]
    assert shared("У меня запястье — похоже на перелом. У сына колено — ушиб.") == []


# --- the dialog of the fifth run ---------------------------------------------------------------


async def test_the_first_message_names_both_with_their_ages(make_chat, kb: KnowledgeBase) -> None:
    fake = ByTask(route=UNCLEAR, extract=ANSWERED)
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say(FIRST)
    (screen,) = chat.replies
    assert screen.text == WHO_FIRST and fake.requests == []
    assert chat.labels() == ["Машу, 7 лет", "Ваню, 10 лет", texts.MENU]
    assert (await session(chat))["shared"] == TOLD


async def test_more_about_both_on_who_is_first_is_not_a_choice(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say(FIRST)
    await chat.say(SECOND)
    said, screen = chat.replies  # the same question: nobody is chosen silently
    assert said.text == texts.SAME_STEP
    assert screen.text == WHO_FIRST and "Машу, 7 лет" in chat.labels()
    state = await session(chat)
    assert state["step"] == "first" and state["person"] is None
    assert [(p["name"], p["age_months"]) for p in state["people"]] == [("Маша", 84), ("Ваня", 120)]
    assert "ушиб руки" in state["people"][0]["complaint"]
    assert state["shared"] == [*TOLD, "Перелома не видно, но ходит плохо"]


async def test_the_names_may_come_in_the_second_message(make_chat, kb: KnowledgeBase) -> None:
    fake = ByTask(route=UNCLEAR, extract=ANSWERED)
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say("Мы сегодня с горки упали, двое детей. К врачу ещё не обращались.")
    assert texts.ONE_BY_ONE in chat.text
    await chat.say("Маша, 7 лет, руку ушибла, и Ваня, 10 лет, ногу подвернул.")
    assert chat.text == WHO_FIRST and chat.labels()[:2] == ["Машу, 7 лет", "Ваню, 10 лет"]
    assert (await session(chat))["shared"] == TOLD  # what the first message said is kept


@pytest.mark.parametrize(
    ("route", "menus", "first", "second"),
    [(WIDE, [FALL, "Не знаю"], 9, 6), (UNCLEAR, ["Не знаю"], 8, 5)],
    ids=["the menu of the limb, as in the run", "what it looks like at once"],
)
async def test_both_children_are_booked_in_nine_and_six_turns(
    make_chat, kb: KnowledgeBase, route: dict, menus: list[str], first: int, second: int
) -> None:
    fake = ByTask(route=route, extract=ANSWERED)
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    one = Counted(chat)

    await one.say(FIRST)
    await one.press("Машу, 7 лет")
    # her age stands over the step with «Изменить»: neither «для кого», nor «сколько лет»
    assert "📝 Записываю Машу, 7 лет: руку ушибла" in chat.text
    assert texts.PERSON_EDIT in chat.labels() and fake.texts_of("route") == ["руку ушибла"]
    for label in menus:
        await one.press(label)
    # what was said about both answers the three questions: the answers, not the questions
    assert (
        fake.texts_of("extract")
        == ["руку ушибла. Мы сегодня с горки упали. К врачу ещё не обращались"] * 3
    )  # «Как получена травма?», «Когда?», «Обращались ли к врачу?»: a call for each
    summary, days = chat.replies
    assert summary.text.startswith(SUMMARY.format(age="7 лет"))
    assert texts.SUMMARY_EDIT in [b.label for row in summary.buttons for b in row]
    assert "Выберите, пожалуйста, день приёма" in days.text
    assert texts.ASK_AUDIENCE not in chat.text and "Как получена травма" not in chat.text
    await one.press(chat.nearest().label)
    await one.press(texts.CONSENT_YES)
    assert "📝 Записываю Машу, 7 лет" in chat.text and texts.ASK_NAME in chat.text
    await one.say("Воронина Мария Кирилловна, 03.03.2019")
    await one.contact()
    assert "Мария Кирилловна записана" in chat.text
    await one.press(texts.PAYMENT_SELF)
    assert chat.text == texts.NEXT_PATIENT and "Записать Ваню" in chat.labels()
    assert one.turns == first <= 9

    two = Counted(chat)
    await two.press("Записать Ваню")
    assert "📝 Записываю Ваню, 10 лет: ногу подвернул" in chat.text
    for label in menus:
        await two.press(label)
    assert (
        fake.texts_of("extract")[3:]
        == ["ногу подвернул. Мы сегодня с горки упали. К врачу ещё не обращались"] * 3
    )
    assert chat.replies[0].text.startswith(SUMMARY.format(age="10 лет"))
    await two.press(chat.nearest().label)
    # «Кого записываем?» with the sister on it is not shown: the bot knows it is Ваня
    assert texts.PATIENT_PICK not in chat.text and "Воронина" not in " ".join(chat.labels())
    assert "📝 Записываю Ваню, 10 лет" in chat.text and texts.ASK_NAME in chat.text
    await two.say("Воронин Иван Кириллович, 15.05.2015")
    assert "Иван Кириллович записан" in chat.text
    await two.press(texts.PAYMENT_SELF)
    assert two.turns == second <= 6
    assert len(await bookings(chat.conversation.db)) == 2
    assert "Маш" not in fake.sent() and "Ван" not in fake.sent()  # the names stay here


async def test_change_the_answers_asks_the_questions(make_chat, kb: KnowledgeBase) -> None:
    fake = ByTask(route=UNCLEAR, extract=ANSWERED)
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say(FIRST)
    await chat.press("Ваню, 10 лет")
    await chat.press("Похоже на перелом")
    assert chat.replies[0].text.startswith("Ваши ответы: ")
    read = len(fake.texts_of("extract"))
    await chat.press(texts.SUMMARY_EDIT)
    assert len(fake.texts_of("extract")) == read  # read once; now the patient answers himself
    assert (await session(chat))["step"] in ("audience", "question")


async def test_an_answer_that_was_not_said_is_asked(make_chat, kb: KnowledgeBase) -> None:
    partly = {**ANSWERED, "facts": {"assault": False, "injury_age": "within_7_days"}}
    fake = ByTask(route=UNCLEAR, extract=partly)
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say(FIRST.replace(" К врачу ещё не обращались.", ""))
    await chat.press("Машу, 7 лет")
    await chat.press("Не знаю")
    assert "Обращались ли к врачу по данному случаю?" in chat.text  # the only question left
    assert "Как получена травма" not in chat.text


async def test_each_question_is_read_by_its_own_call(make_chat, kb: KnowledgeBase) -> None:
    """In the live run DeepSeek returned the field of the question asked and no other: «Как
    получена травма?» was answered, «Когда?» and «Обращались ли к врачу?» were asked."""
    fake = ByTask(route=UNCLEAR, extract=own_field)
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say(FIRST)
    await chat.press("Машу, 7 лет")
    await chat.press("Не знаю")
    assert chat.replies[0].text.startswith(SUMMARY.format(age="7 лет"))
    assert [s["question"] for s in map(sent, fake.requests) if s["task"] == "extract"] == [
        "how",
        "when",
        "seen_doctor",
    ]


async def test_the_first_message_of_the_live_run(make_chat, kb: KnowledgeBase) -> None:
    fake = ByTask(route=UNCLEAR, extract=own_field)
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say(RUN_1)
    assert chat.labels() == ["Машу, 7 лет", "Ваню, 10 лет", texts.MENU]  # no «Меня»
    await chat.press("Машу, 7 лет")
    await chat.press("Не знаю")
    await chat.press(chat.nearest().label)
    await chat.press(texts.CONSENT_YES)
    await chat.say("Воронина Мария Кирилловна, 03.03.2019")
    await chat.contact()
    await chat.press(texts.PAYMENT_SELF)
    assert "Записать Ваню" in chat.labels()  # one waits: by name, not «Записать ещё одного»


async def test_a_wish_for_the_visit_is_not_when_it_happened(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say(COUPLE)
    assert chat.labels()[:2] == ["Меня", "Мужа"]
    assert (await session(chat))["shared"] == []  # «завтра утром» is the wish, kept as a wish


async def test_the_order_said_on_who_is_first_is_a_choice(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say(COUPLE)
    await chat.say(ME_FIRST)
    state = await session(chat)
    assert state["person"]["name"] == "" and state["step"] != "first"
    assert [p["name"] for p in state["people"]] == ["муж"]
    assert texts.ONE_BY_ONE not in chat.text  # said with the question, not once more
    assert "Записываю Вас" in chat.text


async def test_without_deepseek_the_questions_are_asked_as_before(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say(FIRST)
    await chat.press("Машу, 7 лет")
    await chat.press("Похоже на вывих")
    assert "Как получена травма" in chat.text
    assert (await session(chat))["dialog"]["age_months"] == 84


async def test_a_patient_saved_before_is_still_offered(make_chat, kb: KnowledgeBase) -> None:
    """Only the child booked a minute ago is left out: Ваня saved last week is on the screen."""
    fake = ByTask(route=UNCLEAR, extract=ANSWERED)
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    async with chat.conversation.db() as db:
        db.add(
            Patient(
                user_id=chat.user.id,
                audience="child",
                last_name="Воронин",
                first_name="Иван",
                patronymic="Кириллович",
                birth_date=date(2015, 5, 15),
            )
        )
        await db.commit()
    await chat.say(FIRST)
    await chat.press("Машу, 7 лет")
    await chat.press("Не знаю")
    await chat.press(chat.nearest().label)
    await chat.press(texts.CONSENT_YES)
    assert texts.PATIENT_PICK in chat.text  # her brother is saved: she is «Другой пациент»
    await chat.press(texts.PATIENT_NEW)
    await chat.say("Воронина Мария Кирилловна, 03.03.2019")
    await chat.contact()
    await chat.press(texts.PAYMENT_SELF)
    await chat.press("Записать Ваню")
    await chat.press("Не знаю")
    await chat.press(chat.nearest().label)
    assert texts.PATIENT_PICK in chat.text
    assert [label for label in chat.labels() if "Ворон" in label] == ["Воронин Иван, 15.05.2015"]
