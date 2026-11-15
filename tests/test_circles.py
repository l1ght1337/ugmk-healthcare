"""The same answer or screen is not sent a third time in a row (the customer, 15.11.2026).

Run 7: frostbite — the screen «Что случилось с рукой или ногой?» eleven times (the injury is
not in the scripts, and «Другое» → «опишите своими словами» → the same screen again); tooth —
the same pair of lines to every repeat; questions — «Я виртуальный помощник…» three times.

The third time in a row the same answer or screen is due, one line is sent instead: «Похоже,
я не могу ответить на этот вопрос. Передать его администратору?» with buttons — and, after a
route, with «Не ждите звонка…». A new answer to a new question on the same step is not a
circle; the template about urgency is said however many times it is asked.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

from app.bot import texts
from app.flow.loader import KnowledgeBase
from tests.fake_deepseek import FakeDeepSeek
from tests.test_after_route import infant_route
from tests.test_conversation import bookings
from tests.test_intents import session

STUCK = "Похоже, я не могу ответить на этот вопрос. Передать его администратору?"
NO_WAIT = "⚠️ Не ждите звонка, если становится хуже: 103 или 112."
UNCLEAR = {"type": "complaint", "area": "limb", "scenario": None, "candidates": [], "facts": {},
           "clarify": "Подскажите, пожалуйста, что именно случилось с пальцами?"}  # fmt: skip
FROSTBITE = [
    "Обморозил пальцы на руке. Вчера на рыбалке было холодно, пальцы побелели и болят.",
    "Это отморожение. Пальцы побелели, болят, вчера на рыбалке было холодно.",
    "Да я уже писал несколько раз: отморожение пальцев на руке, вчера на рыбалке.",
    "Отморожение пальцев. Хочу записаться к врачу.",
]
SCREEN = "Что случилось с рукой или ногой?"


def said(chat) -> list[str]:
    return [reply.text for reply in chat.replies]


async def test_the_same_screen_is_not_shown_a_third_time(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek(*[UNCLEAR] * 6)
    chat = make_chat(assistant=fake.assistant(kb), admin_chat_id=-100)
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press("Рука или нога")
    await chat.say(FROSTBITE[0])
    await chat.say(FROSTBITE[1])
    assert SCREEN in chat.replies[-1].text  # twice: with a line that says why
    await chat.say(FROSTBITE[2])
    assert said(chat) == [STUCK]  # the third time: one line instead of the screen
    assert chat.labels() == [texts.ASK_ADMIN_QUESTION, texts.MENU_HOME]
    await chat.say(FROSTBITE[3])
    assert said(chat) == [STUCK]  # and not the screen again while nothing new is said
    await chat.press(texts.ASK_ADMIN_QUESTION)
    await chat.press(texts.CONSENT_YES)
    await chat.contact()
    assert "обращение № 1" in chat.text
    assert FROSTBITE[3] in chat.admin[-1].text  # what he could not get an answer to


async def test_other_and_describe_do_not_start_the_circle_anew(
    make_chat, kb: KnowledgeBase
) -> None:
    """frostbite: «Другое» → «опишите своими словами» → the description → the same screen."""
    fake = FakeDeepSeek(*[UNCLEAR] * 6)
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press("Рука или нога")
    shown = 0
    for text in FROSTBITE[:3]:
        await chat.say(text)
        shown += SCREEN in chat.text
        if "Другое" in chat.labels():
            await chat.press("Другое")
    assert shown == 2 and (STUCK in said(chat) or SCREEN not in chat.text)


async def test_the_same_answer_is_not_said_a_third_time(make_chat, kb: KnowledgeBase) -> None:
    """tooth: «Куда с этим обращаться…», then «На этот вопрос ответит администратор» to
    every repeat."""
    chat = make_chat(assistant=FakeDeepSeek().assistant(kb))
    await chat.start()
    await chat.say("Выбил зуб, принимаете с таким?")
    assert chat.replies[0].text.startswith(texts.NO_ROUTE_DATA)
    for again in ("Так вы принимаете с выбитым зубом или нет?", "Зуб выбит. Запишете?"):
        await chat.say(again)
        assert chat.replies[0].text == texts.ADMIN_WILL_ANSWER
    await chat.say("Ну хоть скажите, примут с выбитым зубом или нет?")
    assert said(chat) == [STUCK]


async def test_a_new_answer_to_a_new_question_on_a_step_is_not_a_circle(
    make_chat, kb: KnowledgeBase
) -> None:
    answers = [{"found": True, "answer": text} for text in ("Первый.", "Второй.", "Третий.")]
    replies = [reply for answer in answers for reply in ({"type": "clinic_question"}, answer)]
    chat = make_chat(assistant=FakeDeepSeek(*replies).assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press("Рука или нога")
    for n, asked in enumerate(("а где вы находитесь?", "а парковка есть?", "а сколько стоит?")):
        await chat.say(asked)
        assert chat.replies[0].text == answers[n]["answer"]
        assert SCREEN in chat.replies[-1].text  # the step, under each answer
    assert (await session(chat))["step"] == "topic"


async def test_the_same_answer_over_new_screens_is_dropped_the_third_time(
    make_chat, kb: KnowledgeBase
) -> None:
    """questions: «а зачем вам ФИО?», «а зачем дата рождения?»… — «Я виртуальный помощник…»
    three times, each over the next step."""
    about = {"type": "about_bot"}
    chat = make_chat(assistant=FakeDeepSeek(*[about] * 6).assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    for label in ("Рука или нога", "Похоже на вывих", texts.AUDIENCE_SELF, "Бытовая травма",
                  "В течение 7 дней", "Нет, не обращались"):  # fmt: skip
        await chat.press(label)
    await chat.pick_slot("Завтра")
    await chat.say("а зачем вам мои данные? вы же бот")
    assert chat.replies[0].text == texts.ABOUT_BOT
    await chat.press(texts.CONSENT_YES)
    await chat.say("а вы точно бот? ладно, Юдина Марина Олеговна")
    assert chat.replies[0].text == texts.ABOUT_BOT
    await chat.say("а вы кто вообще? ладно, 25.04.1995")
    assert chat.replies[0].text == texts.ABOUT_BOT  # twice in a row
    await chat.say("а зачем вам ещё и телефон, вы же бот? ладно, +79001110010")
    assert texts.ABOUT_BOT not in said(chat)  # the third time it is not said
    assert STUCK not in said(chat)  # … and what is new stays: the booking is made
    assert "Запись в травмпункт" in chat.text
    assert len(await bookings(chat.conversation.db)) == 1


async def test_after_a_route_the_line_says_not_to_wait_for_the_call(
    make_chat, kb: KnowledgeBase
) -> None:
    clinic = {"type": "clinic_question"}
    fake = FakeDeepSeek(*[clinic] * 5)
    chat = make_chat(assistant=fake.assistant(kb))
    await infant_route(chat)
    asked = ("а сколько у вас стоит приём?", "а парковка у вас есть?", "а оплата картой есть?")
    for question in asked:
        await chat.say(question)
        assert texts.ADMIN_WILL_ANSWER in chat.replies[0].text
    assert len(fake.requests) == 3  # what each message is; the reference is never asked
    await chat.say("а до скольки вы работаете?")
    assert said(chat) == [f"{STUCK}\n{NO_WAIT}"]


async def test_the_template_about_urgency_is_said_every_time(make_chat, kb: KnowledgeBase) -> None:
    chat = make_chat(assistant=FakeDeepSeek().assistant(kb))
    await infant_route(chat)
    for _ in range(4):
        await chat.say("скорую вызывать?")
        assert chat.replies[0].text.startswith("Оценить состояние я не могу.")
        assert STUCK not in chat.text


async def test_a_button_in_between_is_not_a_circle(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    for _ in range(4):  # the same question, the same answer — but the patient moves himself
        await chat.say("мои записи")
        assert texts.MY_EMPTY in chat.text
        await chat.press(texts.MENU_MY)
    assert STUCK not in chat.text
