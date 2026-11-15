"""After a route the same injury is not booked (rule 5), 14.11.2026.

Run 3 of the simulation: `needle` got the route to the AIDS centre and nine turns later a
booking in the trauma centre — a question («где рану обработать?») opened step 1, the
patient pressed «Порез, рана» and went all the way. `infant` (4 months): step 1 opened
several times after the route.

- A question about the injury after its route gets the route once more, not step 1.
- A booking started after a route is asked once: «Это та же травма, о которой Вы писали,
  или другая?» «Та же» — the route and «Передать вопрос администратору»; «Другая» — the
  booking goes on.
- A child under 6 months: any complaint about this child is the 05-04 route.
- «Ехать или ждать звонка?», «а туда как, просто приехать?» — templates, no DeepSeek.
- The route is remembered through a question in between; /start or a long silence starts a
  new conversation.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

from datetime import timedelta
from itertools import pairwise

import pytest

from app.bot import texts
from app.flow.commands import asks_about_there, asks_to_be_taken, asks_where_to_go
from app.flow.loader import KnowledgeBase
from app.llm.guards import grounded
from tests.fake_deepseek import FakeDeepSeek
from tests.test_conversation import bookings
from tests.test_sim_findings_2 import session

AIDS = "📍 Центр борьбы со СПИДом, ул. Ясная, 46"
ODKB = "📍 Областная детская клиническая больница № 1, ул. Серафимы Дерябиной, 32"
NOT_HERE = f"{texts.NOT_BOOKED_HERE}\n{AIDS}\n\n{texts.QUESTIONS_TO_ADMIN}"
# «меня примут?», «вы меня запишете?»: a yes-or-no question gets «Нет» first (15.11.2026)
NO_HERE = f"{texts.NOT_BOOKED_NO}\n{AIDS}\n\n{texts.QUESTIONS_TO_ADMIN}"
ABOUT_US = f"{texts.ROUTE_GO}\n{AIDS}\n\n{texts.ADMIN_WILL_ANSWER}"
NO_WAIT = (
    f"Ждать звонка администратора для этого не нужно.\nОбратиться нужно:\n{AIDS}\n\n"
    "Как там принимают, у меня данных нет — уточните в самом учреждении.\n"
    "⚠️ При угрозе жизни звоните 103 или 112."
)
GO = (
    f"Обратиться нужно:\n{AIDS}\n\n"
    "Как там принимают, у меня данных нет — уточните в самом учреждении.\n"
    "⚠️ При угрозе жизни звоните 103 или 112."
)
ASK_ADMIN = [texts.ASK_ADMIN_QUESTION, texts.MENU_HOME]


def complaint(scenario: str | None, **more: object) -> dict:
    found = [scenario] if scenario else []
    return {"type": "complaint", "area": "limb", "scenario": scenario, "candidates": found,
            "facts": {}, "clarify": None, **more}  # fmt: skip


CUT = complaint("cut_sutures")
UNCLEAR = complaint(None, question=True, clarify="Что случилось с рукой или ногой?")
MEDICAL = {"type": "medical_question"}


async def needle_route(chat) -> None:
    """«Укол чужой иглой» by buttons: the route to the AIDS centre."""
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press("Рана, ожог, укус")
    await chat.press("Укол чужой иглой")
    assert AIDS in chat.text and (await session(chat))["step"] == "idle"


async def infant_route(chat) -> None:
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press("Ребёнок до 6 месяцев")
    assert ODKB in chat.text


async def to_cut(chat) -> None:
    """«Записаться» → «Другая» (after a route it is asked first) → «Рана, ожог, укус» →
    «Порез, рана»."""
    await chat.press(texts.MENU_BOOK)
    if texts.OTHER_INJURY in chat.labels():
        await chat.press(texts.OTHER_INJURY)
    assert texts.ASK_COMPLAINT in chat.text  # step 1 opens: the patient started it himself
    await chat.press("Рана, ожог, укус")
    await chat.press("Порез, рана")


# --- a question after the route -----------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "reading"),
    [
        # no injury and no body part named: the talk is about the injury of the route
        ("А в травмпункт меня всё-таки примут?", CUT),
        ("Так вы меня всё-таки не запишете? Я же написала, что случилось.", UNCLEAR),
        ("А что мне теперь делать-то?", MEDICAL),
        # the words of the route's own scenario: the same injury, whatever else is named
        ("А в травмпункт меня всё-таки примут? У меня же рана на стопе, там же игла была", CUT),
    ],
)
async def test_a_question_after_the_route_gets_the_route_not_step_one(
    make_chat, kb: KnowledgeBase, said: str, reading: dict
) -> None:
    fake = FakeDeepSeek(reading, {"found": False})
    chat = make_chat(assistant=fake.assistant(kb))
    await needle_route(chat)
    await chat.say(said)
    asked_to_be_taken = reading != MEDICAL  # «меня примут?»: «Нет, …» is its answer
    assert chat.replies[-1].text == (NO_HERE if asked_to_be_taken else NOT_HERE)
    assert chat.labels() == ASK_ADMIN
    for wrong in ("Шаг 1 из 5", "Шаг 2 из 5", texts.MENU_BOOK, "Записать Вас?"):
        assert wrong not in chat.text and wrong not in chat.labels()
    assert (await session(chat))["step"] == "offer_admin"


# --- an injury or a body part named after the route: the same one or another? --------------
# «А с вывихом пальца можно записаться?» must not get the route of the needle once more.

SAME_SCREEN = [texts.SAME_INJURY, texts.OTHER_INJURY, texts.MENU]


@pytest.mark.parametrize(
    ("said", "reading"),
    [
        ("а с вывихом пальца можно записаться?", complaint("dislocation")),
        ("а с вывихом пальца можно записаться?", UNCLEAR),
        ("А в травмпункт меня всё-таки примут? У меня же рана на стопе, это травма ноги.", CUT),
        ("А рану мне хоть обработают?", UNCLEAR),
        ("А рану мне хоть обработают? Чем её пока мазать?", MEDICAL),
        ("ещё плечо болит, упал вчера", UNCLEAR),
    ],
)
async def test_an_injury_named_after_the_route_is_asked_same_or_other(
    make_chat, kb: KnowledgeBase, said: str, reading: dict
) -> None:
    chat = make_chat(assistant=FakeDeepSeek(reading, {"found": False}).assistant(kb))
    await needle_route(chat)
    await chat.say(said)
    screen = chat.replies[-1]
    assert screen.text.endswith(texts.SAME_OR_OTHER) and chat.labels() == SAME_SCREEN
    assert AIDS not in screen.text and (await session(chat))["step"] == "same"


async def test_another_injury_named_in_a_question_is_booked(make_chat, kb: KnowledgeBase) -> None:
    chat = make_chat(assistant=FakeDeepSeek(UNCLEAR).assistant(kb))
    await needle_route(chat)
    await chat.say("а с вывихом пальца можно записаться?")
    await chat.press(texts.OTHER_INJURY)
    assert texts.ASK_AUDIENCE in chat.text  # «вывих» by the word list: the scenario goes on
    assert (await session(chat))["dialog"]["scenario_id"] == "dislocation"


async def test_the_same_injury_is_not_asked_about_on_every_message(
    make_chat, kb: KnowledgeBase
) -> None:
    fake = FakeDeepSeek(UNCLEAR, UNCLEAR, {"found": False}, MEDICAL)
    chat = make_chat(assistant=fake.assistant(kb))
    await needle_route(chat)
    await chat.say("А рану мне хоть обработают?")
    await chat.press(texts.SAME_INJURY)
    assert chat.replies[-1].text == NOT_HERE
    await chat.say("Но рана же на ноге, это травма ноги!")
    assert texts.SAME_OR_OTHER not in chat.text and AIDS in chat.text  # the route, not asked
    await chat.say("А чем рану пока мазать?")
    assert texts.SAME_OR_OTHER not in chat.text and "Шаг" not in chat.text


async def test_the_route_is_remembered_through_a_question_in_between(
    make_chat, kb: KnowledgeBase
) -> None:
    fake = FakeDeepSeek(
        {"type": "clinic_question"}, {"found": True, "answer": "Круглосуточно."}, CUT
    )
    chat = make_chat(assistant=fake.assistant(kb))
    await needle_route(chat)
    await chat.say("а вы круглосуточно работаете?")
    assert chat.replies[0].text == ABOUT_US  # not our hours: the route and the administrator
    await chat.say("А в травмпункт меня всё-таки примут?")
    assert chat.replies[-1].text == NO_HERE


# --- a booking started after the route ----------------------------------------------------


async def test_a_booking_after_the_route_is_asked_same_or_other(make_chat) -> None:
    chat = make_chat()
    await needle_route(chat)
    await chat.press(texts.MENU_BOOK)  # asked before step 1 (15.11.2026), not after the topic
    screen = chat.replies[-1]
    assert screen.text.endswith(texts.SAME_OR_OTHER) and "Шаг 1 из 5" in screen.text
    assert chat.labels() == [texts.SAME_INJURY, texts.OTHER_INJURY, texts.MENU]
    assert (await session(chat))["step"] == "same"
    await chat.press(texts.SAME_INJURY)
    assert chat.replies[-1].text == NOT_HERE and chat.labels() == ASK_ADMIN
    assert await bookings(chat.conversation.db) == []


async def test_another_injury_is_booked_and_the_question_is_not_asked_twice(make_chat) -> None:
    chat = make_chat()
    await needle_route(chat)
    await to_cut(chat)  # «Другая» is said on the way
    assert texts.ASK_AUDIENCE in chat.text and texts.SAME_OR_OTHER not in chat.text
    await chat.press(texts.MENU)
    await to_cut(chat)  # the same conversation: the answer was «другая»
    assert texts.ASK_AUDIENCE in chat.text and texts.SAME_OR_OTHER not in chat.text


async def test_the_same_injury_is_asked_about_again_on_the_next_try(make_chat) -> None:
    chat = make_chat()
    await needle_route(chat)
    await chat.press(texts.MENU_BOOK)
    await chat.press(texts.SAME_INJURY)
    await chat.press(texts.MENU_HOME)
    await chat.press(texts.MENU_BOOK)
    assert chat.replies[-1].text.endswith(texts.SAME_OR_OTHER)


@pytest.mark.parametrize("typed", ["та же", "другая", "да та же самая", "нет, другая травма"])
async def test_the_answer_may_be_typed(make_chat, typed: str) -> None:
    chat = make_chat()
    await needle_route(chat)
    await chat.press(texts.MENU_BOOK)
    await chat.say(typed)
    if "друг" in typed:
        assert texts.ASK_COMPLAINT in chat.text  # another injury: step 1
    else:
        assert chat.replies[-1].text == NOT_HERE


async def test_a_complaint_in_words_after_the_route_is_asked_too(
    make_chat, kb: KnowledgeBase
) -> None:
    chat = make_chat(assistant=FakeDeepSeek(CUT).assistant(kb))
    await needle_route(chat)
    await chat.say("я вчера ещё порезал палец ножом на кухне")
    assert chat.replies[-1].text.endswith(texts.SAME_OR_OTHER)
    await chat.press(texts.OTHER_INJURY)
    assert "Шаг 3 из 5" in chat.text  # «я порезал»: for himself, the scenario's first question


async def test_a_new_conversation_forgets_the_route(make_chat, clock) -> None:
    chat = make_chat()
    await needle_route(chat)
    await chat.start()  # /start
    await to_cut(chat)
    assert texts.SAME_OR_OTHER not in chat.text and texts.ASK_AUDIENCE in chat.text
    chat = make_chat()
    await needle_route(chat)
    clock.now += timedelta(hours=7)  # a long silence
    await to_cut(chat)
    assert texts.SAME_OR_OTHER not in chat.text


# --- a child under 6 months ---------------------------------------------------------------

INFANT_AGAIN = f"{texts.NOT_BOOKED_HERE}\n{ODKB}\n\n{texts.QUESTIONS_TO_ADMIN}"


@pytest.mark.parametrize(
    ("said", "reading"),
    [
        (
            "Головой вроде ударился, наверное. Он плакал сильно, сейчас успокоился",
            complaint("head_trauma"),
        ),
        ("он ещё и ручку ушиб, она опухла", complaint("fracture")),
        ("упал с дивана, плачет", UNCLEAR),
    ],
)
async def test_any_complaint_about_the_infant_is_the_infant_route(
    make_chat, kb: KnowledgeBase, said: str, reading: dict
) -> None:
    chat = make_chat(assistant=FakeDeepSeek(reading, {"found": False}).assistant(kb))
    await infant_route(chat)
    await chat.say(said)
    assert chat.replies[-1].text == INFANT_AGAIN
    for wrong in ("Шаг 1 из 5", "Шаг 2 из 5", texts.SAME_OR_OTHER, "Травму головы"):
        assert wrong not in chat.text


async def test_a_complaint_about_someone_else_is_not_the_infants(
    make_chat, kb: KnowledgeBase
) -> None:
    chat = make_chat(assistant=FakeDeepSeek(complaint("dislocation")).assistant(kb))
    await infant_route(chat)
    await chat.say("а я сама вчера упала на льду, плечо вылетело")
    assert INFANT_AGAIN not in chat.text and texts.SAME_OR_OTHER not in chat.text
    assert "Шаг 3 из 5" in chat.text  # her own injury: the scenario goes on


# --- «ехать или ждать звонка?», «а туда как?» ---------------------------------------------

GO_OR_WAIT = (
    "Мне сейчас идти в травмпункт или ждать звонка администратора?",
    "Мне ждать или всё-таки ехать на Ясную? Я не могу ждать",
    "Так мне что, до утра ждать? Скажите хоть, ехать сейчас на Ясную или нет",
    "Так мне скорую вызывать, что ли? Скажите хоть, 103 звонить или на Ясную ехать самим",
    "Мне 103 звонить сейчас или нет? Я боюсь до утра ждать",
    "Так куда нам ехать-то?",
    "Ну хоть рану обработать мне где? Я не понимаю, куда мне сейчас со стопой идти.",
)


@pytest.mark.parametrize("said", GO_OR_WAIT)
def test_words_that_ask_where_to_go(said: str) -> None:
    assert asks_where_to_go(said)


@pytest.mark.parametrize(
    "said", ["а сколько стоит приём?", "буду ждать звонка", "я поеду завтра", "куда вставать?"]
)
def test_words_that_do_not_ask_where_to_go(said: str) -> None:
    assert not asks_where_to_go(said)


async def handed(chat) -> None:
    await chat.press(texts.MENU_ADMIN)
    await chat.press(texts.CONSENT_YES)
    await chat.contact()
    assert "обращение № 1" in chat.text


async def test_go_or_wait_after_the_route_is_a_template(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await needle_route(chat)
    await handed(chat)
    said = []
    for text in GO_OR_WAIT[:6]:  # the last one names the wound: asked «та же или другая?»
        await chat.say(text)
        (only,) = chat.replies
        said.append(only.text)
        assert "Шаг" not in only.text
    assert said[0] == NO_WAIT  # the call is with the administrators: not to be waited for
    # never the same line twice in a row — but for the template about urgency: «скорую
    # вызывать?» and «103 звонить или нет?» get it however many times they are asked
    urgent = texts.URGENCY.split(".")[0]
    assert all(a != b or urgent in a for a, b in pairwise(said))
    assert sum(urgent in line for line in said) == 2
    assert all(AIDS in line for line in said)
    assert fake.requests == []  # the words decide, DeepSeek is not asked
    assert len(chat.admin) == 1  # nothing was added to the request


async def test_go_or_wait_without_a_request_does_not_speak_of_the_call(make_chat) -> None:
    chat = make_chat()
    await needle_route(chat)
    await chat.say("Так куда мне ехать-то?")
    assert chat.text == GO and chat.labels() == ASK_ADMIN


THERE = (
    "А в эту больницу как, просто приехать? Или записываться надо?",
    "А туда как, просто приехать?",
    "А в центр этот как попасть, там записи нужны?",
    "там круглосуточно принимают?",
    "на Ясной принимают без записи?",
)


@pytest.mark.parametrize("said", THERE)
def test_words_about_the_place_of_the_route(said: str) -> None:
    assert asks_about_there(said, ("Центр борьбы со СПИДом, ул. Ясная, 46",))


@pytest.mark.parametrize(
    "said", ["а к вам как попасть?", "там болит, где ударился", "туда, где ушиб, лёд можно?"]
)
def test_words_not_about_the_place_of_the_route(said: str) -> None:
    assert not asks_about_there(said, ("Центр борьбы со СПИДом, ул. Ясная, 46",))


async def test_how_to_get_there_is_about_the_routes_place(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await infant_route(chat)
    await chat.say("А это точно? А в эту больницу как, просто приехать? Или записываться надо?")
    assert chat.text == f"{texts.ROUTE_THERE}\n{ODKB}" and chat.labels() == ASK_ADMIN
    assert "Шейнкмана" not in chat.text and fake.requests == []


async def test_a_question_about_our_centre_is_not_answered_about_it(
    make_chat, kb: KnowledgeBase
) -> None:
    """Until 15.11 the reference answered «Круглосуточно.» here; a patient sent elsewhere
    read it as «come to us» (tests/test_route_not_here.py)."""
    fake = FakeDeepSeek({"type": "clinic_question"}, {"found": True, "answer": "Круглосуточно."})
    chat = make_chat(assistant=fake.assistant(kb))
    await needle_route(chat)
    await chat.say("а вы сами круглосуточно работаете?")
    assert chat.replies[0].text == ABOUT_US


# --- DeepSeek's answer does not advise ----------------------------------------------------

INFO = "Травмпункт работает круглосуточно. При угрозе жизни звоните 103 или 112. Нужна запись."


@pytest.mark.parametrize(
    "answer",
    [
        "При угрозе жизни звоните 103 или 112. Травмы головы в травмпункте не принимают, "
        "поэтому при опасениях за ребёнка лучше не ждать до утра и вызвать скорую.",
        "Рекомендую приложить холод и не нагружать ногу.",
        "Советую сразу ехать в больницу.",
        "Не ждите утра, вызывайте скорую.",
    ],
)
def test_an_answer_with_advice_is_not_grounded(answer: str) -> None:
    assert not grounded(answer, INFO)


@pytest.mark.parametrize(
    "answer",
    ["Травмпункт работает круглосуточно, нужна запись.", "При угрозе жизни звоните 103 или 112."],
)
def test_an_answer_from_the_reference_is_grounded(answer: str) -> None:
    assert grounded(answer, INFO)


def test_advice_that_the_reference_gives_itself_is_allowed() -> None:
    reference = INFO + " При открытом переломе рекомендуем сразу вызвать скорую."
    assert grounded("При открытом переломе рекомендуем сразу вызвать скорую.", reference)


# --- after the partial run of 14.11 (9 dialogs) ---------------------------------------------


async def test_asking_for_an_administrator_after_the_route_is_not_a_template(make_chat) -> None:
    chat = make_chat()
    await needle_route(chat)
    await chat.say("А туда можно сегодня прийти? Или записаться нужно?")
    assert chat.text == f"{texts.ROUTE_THERE}\n{AIDS}"
    await chat.say(
        "А вы можете передать вопрос администратору? Пусть подскажут, можно ли сегодня "
        "прийти в этот центр или сначала записываться"
    )
    assert texts.CONSENT_YES in chat.labels() and texts.ROUTE_GO not in chat.text


@pytest.mark.parametrize(
    "said",
    ["А как понять, угроза жизни или нет? Он же не скажет. Может, вы всё-таки нас посмотрите, "
     "я не знаю что делать, боюсь везти не туда",
     "как понять, что это серьёзно?", "боюсь ехать не туда"],
)  # fmt: skip
def test_not_there_and_how_to_tell_are_not_about_the_place(said: str) -> None:
    assert not asks_about_there(said, ("Центр борьбы со СПИДом, ул. Ясная, 46",))


async def test_advice_asked_after_the_route_gets_no_advice_and_the_route(
    make_chat, kb: KnowledgeBase
) -> None:
    fake = FakeDeepSeek(MEDICAL, {"found": False})
    chat = make_chat(assistant=fake.assistant(kb))
    await infant_route(chat)
    await chat.say(
        "А как понять, угроза жизни или нет? Он же не скажет. Может, вы всё-таки нас "
        "посмотрите, я не знаю что делать, боюсь везти не туда"
    )
    aside, route = chat.replies
    assert aside.text == texts.MEDICAL_ASIDE and route.text == INFANT_AGAIN


async def test_thanks_twice_in_a_row_is_a_goodbye(make_chat, kb: KnowledgeBase) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("Хорошо, буду ждать. Спасибо вам большое")
    assert chat.text == texts.WELCOME
    await chat.say("Нет, наверное больше нечего. Спасибо, буду ждать звонка")
    assert chat.text == kb.common.goodbye.text


# --- the partial runs of section 5 (genital) ----------------------------------------------------
# «А тут что, нельзя записаться? Я думал в травмпункт» after the route opened step 1, and the
# same question there got «Мы на этом же шаге» until «Похоже, я Вас не понимаю».

CAN_I_BOOK = (
    "А тут что, нельзя записаться? Я думал в травмпункт. Мне бы сегодня, срочно надо",
    "Так а в травмпункт у вас нельзя что ли записаться? Мне бы сегодня, дело срочное",
    "Мне объясните, в травмпункт ваш можно записаться или нет? Мне сегодня надо",
    "Мне не записаться что ли к вам? Скажите прямо, в травмпункт ваш можно или нет",
    "А в травмпункт меня всё-таки примут?",
)


@pytest.mark.parametrize("said", CAN_I_BOOK)
def test_words_that_ask_to_be_taken(said: str) -> None:
    assert asks_to_be_taken(said)


@pytest.mark.parametrize(
    "said", ["запишите меня на сегодня", "хочу записаться", "а сколько стоит приём?"]
)
def test_an_order_to_book_is_not_such_a_question(said: str) -> None:
    assert not asks_to_be_taken(said)


@pytest.mark.parametrize("started", [False, True])
async def test_can_i_book_here_after_the_route_is_the_route(
    make_chat, kb: KnowledgeBase, started: bool
) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await needle_route(chat)
    if started:
        await chat.press(texts.MENU_BOOK)  # «та же или другая?»: the question is a question
    said = []
    for text in CAN_I_BOOK[:3]:
        await chat.say(text)
        said.append(chat.replies[-1].text)
        assert AIDS in chat.text and texts.SAME_STEP not in chat.text
        assert "Шаг 1 из 5" not in chat.text and texts.STUCK not in chat.text
    assert said[0] == NO_HERE and all(a != b for a, b in pairwise(said))
    assert fake.requests == []


async def test_an_order_to_book_after_the_route_asks_same_or_other(make_chat) -> None:
    chat = make_chat()
    await needle_route(chat)
    await chat.say("запишите меня на сегодня")
    assert chat.replies[-1].text.endswith(texts.SAME_OR_OTHER)  # before step 1 (15.11.2026)
    await chat.press(texts.OTHER_INJURY)
    assert texts.ASK_COMPLAINT in chat.text
