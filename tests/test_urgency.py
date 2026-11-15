"""«Скорую вызывать?», «это срочно?» get the same answer from the code, wherever it is asked.

Full run 6. `infant`: a child of 4 months after a fall, «вялый и срыгивает»; on «мне скорую
вызывать или в эту больницу ехать самой?» the line about 103 and 112 was gone, then the bot
took the phone and promised a call of an administrator. `head_temple`: «это срочно, если
тошнит до сих пор?» got «С этим вопросом не в травмпункт» and «У меня нет этой информации».
`burn_face`: a burn of a child's face became «травма головы», the patient was told «Ожоги мы
принимаем… записать можно в этом чате», and «вы меня запишете?» made two patients of one.

The answer to a question about urgency is a template: «Оценить состояние я не могу. Если
сомневаетесь или становится хуже — звоните 103 или 112.» — with the address of the route
under it, if one was given. The words of the question stand in config/clinic.yaml.

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

import pytest

from app.bot import texts
from app.config import ClinicData
from app.flow.commands import asks_urgency
from app.flow.loader import KnowledgeBase
from app.flow.people import find_people
from tests.fake_deepseek import FakeDeepSeek
from tests.test_intents import session

CANNOT = "Оценить состояние я не могу."
CALL = "⚠️ Если сомневаетесь или становится хуже — звоните 103 или 112."
NO_WAIT = "⚠️ Не ждите звонка, если становится хуже: 103 или 112."
INFANT = "здравствуйте! сын упал с дивана, ему 4 месяца, плакал сильно, сейчас вроде успокоился"
ODKB = "Областная детская клиническая больница № 1"
ASKS = [  # the patients of run 6, word for word
    "а как туда попасть? нам скорую надо вызывать или самим ехать? он маленький совсем, я боюсь",
    "а если он вялый стал и срыгивает? это же плохо да? я не понимаю, мне скорую вызывать или в "
    "эту больницу ехать самой? подскажите хоть примерно",
    "а сейчас сколько времени? если уже вечер, я не дождусь звонка... мне что, скорую вызывать? "
    "он вялый совсем, я боюсь",
    "Скорую вызывать?",
    "ехать самой или скорую?",
    "это срочно?",
    "это опасно?",
    "И насколько это срочно, если тошнит до сих пор?",
    # the customer, 15.11: more words, and no question mark is needed about the ambulance
    "это серьёзно?",
    "а это серьезно, как думаете?",
    "серьёзно ли это",
    "опасно ли это для ребёнка",
    "срочно ли нам ехать",
    "скорую надо или самим ехать",
    "скорую вызывать или сами доедем",
    "нужна скорая или нет",
    "муж уже едет к вам, это опасно?",  # «едет» is not about an ambulance here
    # run 7, ent: «103» and «112» in a question ask about urgency too
    "Тогда последнее: если бусина сама не выйдет и дышать станет тяжелее — сразу 103, верно?",
    "звонить 112?",
    "это опасно? я тогда скорую вызову",  # the question stays a question
]
NOT_ASKED = [
    "поняла, спасибо. вызову скорую тогда, раз вялый",  # she has decided: not a question
    "скорую уже вызвали",
    "мне срочно нужна справка",
    "запишите срочно на сегодня",
    # the ambulance has been called or has been there: nothing to weigh
    "скорую вызвали, надо ли нам ехать самим?",
    "скорая уже едет, что нужно взять?",
    "скорая приезжала, сказали идти в травмпункт, надо записаться",
    "скорая увезла мужа, нужно ли отменить запись?",
    "это серьёзно меняет дело",  # no question in it
    # run 7, tick: what the patient is going to do is not a question about urgency
    "Я спрашиваю простое: можно приехать в травмпункт без записи или нет? Если не знаете — "
    "так и скажите, я в скорую позвоню",
    "Всё, хватит. Я поняла, что от вас толку нет, позвоню в скорую сама.",
    "ладно, вызову скорую",
    "позвоню 103, если хуже станет",
]


@pytest.mark.parametrize("said", ASKS)
def test_a_question_about_urgency(clinic: ClinicData, said: str) -> None:
    emergency = clinic.clinic.emergency
    assert asks_urgency(said, emergency.asks, emergency.not_weighed)


@pytest.mark.parametrize("said", NOT_ASKED)
def test_what_is_not_a_question_about_urgency(clinic: ClinicData, said: str) -> None:
    emergency = clinic.clinic.emergency
    assert not asks_urgency(said, emergency.asks, emergency.not_weighed)


def test_the_words_of_the_customer_are_in_the_data(clinic: ClinicData) -> None:
    emergency = clinic.clinic.emergency
    assert {"это серьезно", "серьезно ли", "опасно ли", "срочно ли"} <= set(emergency.asks)
    assert {"вызвали", "едет", "приезжала", "увезла"} <= set(emergency.ambulance_was)
    assert {"103", "112"} <= set(emergency.asks)
    assert {"позвоню", "вызову"} <= set(emergency.ambulance_will)


async def routed_infant(make_chat, kb: KnowledgeBase, *replies):
    fake = FakeDeepSeek(*replies)
    chat = make_chat(assistant=fake.assistant(kb), admin_chat_id=-100)
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press("Ребёнок до 6 месяцев")
    assert ODKB in chat.text
    return chat, fake


@pytest.mark.parametrize("said", ASKS[:3])
async def test_after_a_route_the_answer_has_the_line_and_the_address_every_time(
    make_chat, kb: KnowledgeBase, said: str
) -> None:
    chat, fake = await routed_infant(make_chat, kb)
    for _ in range(3):  # however many times it is asked
        await chat.say(said)
        lines = chat.text.split("\n")
        assert lines[:2] == [CANNOT, CALL], chat.text
        assert ODKB in chat.text and "Обратиться нужно:" in chat.text
    assert fake.requests == []  # the code answers, not DeepSeek


async def test_the_answer_in_the_middle_of_a_booking_keeps_the_step(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")
    await chat.say("а это опасно?")
    first, screen = chat.replies
    assert first.text == f"{CANNOT}\n{CALL}" and "Для кого запись" in screen.text
    assert (await session(chat))["step"] == "audience"


async def test_a_request_after_a_route_does_not_say_to_wait_for_the_call(
    make_chat, kb: KnowledgeBase
) -> None:
    chat, _ = await routed_infant(make_chat, kb)
    await chat.press(texts.MENU_ADMIN)  # the request is made, as the patient asks
    await chat.press(texts.CONSENT_YES)
    await chat.contact()
    assert "Я передал обращение № 1 администратору" in chat.text
    assert NO_WAIT in chat.text.split("\n")
    await chat.say("спасибо... а долго ждать? я боюсь не дождусь звонка")
    assert "Администраторы отвечают с 08:00 до 20:00" in chat.text
    assert NO_WAIT in chat.text.split("\n")


async def test_a_request_with_no_route_has_no_such_line(make_chat) -> None:
    chat = make_chat(admin_chat_id=-100)
    await chat.start()
    await chat.press(texts.MENU_ADMIN)
    await chat.press(texts.CONSENT_YES)
    await chat.contact()
    assert "Не ждите звонка" not in chat.text


HEAD = "Я ударилась виском об угол шкафа, примерно сорок минут назад. На месте удара шишка, тошнит."
THERE = [
    "Спасибо, а в 23-ю больницу можно как-то записаться или просто приехать? Просто голова "
    "побаливает и тошнит до сих пор.",
    "Я понимаю, что вы травму головы не принимаете. Но подскажите хотя бы: в 23-ю больницу "
    "просто приехать или записываться надо?",
    "а как туда попасть?",
]


@pytest.mark.parametrize("said", THERE)
async def test_a_question_about_the_hospital_of_the_route(
    make_chat, kb: KnowledgeBase, said: str
) -> None:
    other = {"type": "other_medical"}
    chat = make_chat(assistant=FakeDeepSeek(other, other).assistant(kb))
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press("Голова, лицо, глаза, уши")
    await chat.press("Ударился головой")
    if texts.AUDIENCE_SELF in chat.labels():
        await chat.press(texts.AUDIENCE_SELF)
    assert "Травму головы не принимаем" in chat.text, chat.text
    await chat.say(said)
    assert texts.ROUTE_THERE in chat.text and "больница № 23" in chat.text
    assert texts.NOT_OUR_PROFILE not in chat.text and "У меня нет этой информации" not in chat.text


@pytest.mark.parametrize(
    "said",
    [
        "Сейчас только утро, а ребёнку всё хуже, лицо красное. Я не могу просто ждать звонка. "
        "Скажите, нам ехать в приёмный покой сейчас или вы меня запишете на приём?",
        "Так вы меня запишете или нет? Я спрашиваю про приём! Мне с ребёнком к вам ехать или "
        "сразу в приёмный покой на Решетскую?",
        "меня примут с ребёнком?",
    ],
)
def test_will_you_book_me_is_not_a_second_patient(said: str) -> None:
    assert [p.name for p in find_people(said)] in (["ребёнок"], [])


def test_book_me_with_the_child_still_names_two() -> None:
    assert [p.name for p in find_people("запишите меня с ребёнком")] == ["", "ребёнок"]
    assert [p.name for p in find_people("меня и сына")] == ["", "сын"]


BURN = "дочка 3 года опрокинула на лицо горячий чай, кожа красная"
FACTS = {"burn_location": "face", "burn_cause": "thermal", "injury_age": "within_24h"}
FACE = {"type": "complaint", "area": "head", "scenario": "burns", "candidates": ["burns"],
        "facts": FACTS}  # fmt: skip
HEAD_AGAIN = {"type": "complaint", "area": "head", "scenario": "head_trauma",
              "candidates": ["head_trauma"], "facts": {}}  # fmt: skip


async def routed_burn(make_chat, kb: KnowledgeBase, *more):
    chat = make_chat(assistant=FakeDeepSeek(FACE, *more).assistant(kb), admin_chat_id=-100)
    await chat.start()
    await chat.say(BURN)
    await chat.press("Да, ребёнок 3 года")
    assert "С ожогами лица нужно обратиться в приёмный покой" in chat.text, chat.text
    return chat


@pytest.mark.parametrize(
    "said",
    [
        "Так мне записаться к вам или в приёмный покой ехать? Я запуталась совсем",
        "Так вы меня запишете или нет? Мне с ребёнком к вам ехать или сразу в приёмный покой?",
        "Так вы нас не запишете, да? Только в приёмный покой ехать? Скажите прямо",
    ],
)
async def test_after_the_route_of_a_burn_the_answer_is_that_route(
    make_chat, kb: KnowledgeBase, said: str
) -> None:
    chat = await routed_burn(make_chat, kb, {"type": "menu_action", "action": "admin"})
    await chat.say(said)
    assert "ДГКБ № 9" in chat.text and "Чтобы администратор мог" not in chat.text
    assert texts.WHO_FIRST not in chat.text and "Ожоги мы принимаем" not in chat.text


async def test_the_burn_does_not_become_a_head_injury(make_chat, kb: KnowledgeBase) -> None:
    chat = await routed_burn(make_chat, kb, HEAD_AGAIN)
    await chat.say("у неё лицо красное, три года ей, вы нас примете?")
    assert "Травму головы не принимаем" not in chat.text
    assert "ДГКБ № 9" in chat.text  # the route that was given, with its reason


async def test_after_the_route_of_a_burn_the_call_is_not_waited_for_either(
    make_chat, kb: KnowledgeBase
) -> None:
    """The customer, 15.11: the line stands after any route — wherever the bot refused to
    book and gave an address — not only after the routes of section 5."""
    chat = await routed_burn(make_chat, kb)
    await chat.press(texts.MENU_ADMIN)
    await chat.press(texts.CONSENT_YES)
    await chat.contact()
    assert "Я передал обращение № 1 администратору" in chat.text
    assert NO_WAIT in chat.text.split("\n")
    await chat.say("а долго ждать звонка?")
    assert "Администраторы отвечают с 08:00 до 20:00" in chat.text
    assert NO_WAIT in chat.text.split("\n")


# --- the template does not stand in for the route (run 7, needle) ---------------------------------

NEEDLE = (
    "Здравствуйте! Я сегодня во дворе наступила на иглу от шприца, проколола стопу. Это опасно? "
    "Мне нужно к вам приехать?"
)
AIDS = "📍 Центр борьбы со СПИДом, ул. Ясная, 46"


async def test_a_complaint_with_a_question_about_urgency_gets_its_route_at_once(
    make_chat, kb: KnowledgeBase
) -> None:
    """Run 7: the message got «Оценить состояние я не могу» and the menu; the route came on
    the third turn. With a complaint in the message the template is the first line, then the
    message goes its usual way."""
    needle = {"type": "complaint", "area": "limb", "scenario": "needle_injury",
              "candidates": ["needle_injury"], "facts": {}, "advice": True}  # fmt: skip
    chat = make_chat(assistant=FakeDeepSeek(needle).assistant(kb))
    await chat.start()
    await chat.say(NEEDLE)
    assert chat.replies[0].text == f"{CANNOT}\n{CALL}"
    assert AIDS in chat.replies[-1].text  # the route, in the same turn
    said = "\n".join(reply.text for reply in chat.replies)
    assert said.count("103 или 112") == 1  # said once: not «не могу давать рекомендации» too


async def test_a_complaint_that_is_booked_goes_on_to_its_step(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("Подвернул ногу, это опасно? Можно к вам сегодня?")
    assert chat.replies[0].text == f"{CANNOT}\n{CALL}"
    state = await session(chat)
    assert state["wizard"] and "Шаг 1 из 5" in chat.replies[-1].text


async def test_what_the_patient_is_going_to_do_is_not_answered_with_the_template(
    make_chat, kb: KnowledgeBase
) -> None:
    """Run 7, tick: «можно приехать без записи или нет? …я в скорую позвоню» got the template
    instead of the answer."""
    answer = {"found": True, "answer": "Приём по предварительной записи."}
    fake = FakeDeepSeek({"type": "clinic_question"}, answer)
    chat = make_chat(assistant=fake.assistant(kb))
    await chat.start()
    await chat.say(NOT_ASKED[-4])
    assert chat.replies[0].text == "Приём по предварительной записи."
    assert CANNOT not in chat.text


CHEST = (
    "Здравствуйте, я утром поскользнулась в ванной и упала грудью на край ванны. Болят рёбра "
    "справа, особенно когда двигаюсь или кашляю. Дышу вроде свободно, крови нет. Хочу "
    "записаться к вам на приём."
)


async def test_when_inside_a_complaint_is_not_a_question_about_the_schedule(
    make_chat, kb: KnowledgeBase
) -> None:
    """chest, runs 5–7 (found by the rule «маршрут пришёл не в ответ на первое сообщение с
    жалобой»): «…болят рёбра, особенно когда двигаюсь или кашляю» was answered «Ближайшее:
    сегодня, 13:30» — the word «когда» was read as a question about free time, and the route
    came five turns later. A complaint goes its usual way."""
    chest = {"type": "complaint", "area": "chest", "scenario": "chest_abdomen",
             "candidates": ["chest_abdomen"], "facts": {}}  # fmt: skip
    chat = make_chat(assistant=FakeDeepSeek(chest).assistant(kb))
    await chat.start()
    await chat.say(CHEST)
    assert "Ближайшее:" not in chat.text
    state = await session(chat)
    assert state["wizard"] and state["dialog"]["scenario_id"] == "chest_abdomen", chat.text


async def test_a_question_about_the_schedule_with_no_complaint_is_answered(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("когда можно прийти?")
    assert "Ближайшее:" in chat.text
