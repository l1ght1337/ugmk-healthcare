"""The «угроза жизни» state of a conversation (14.11.2026, the customer's decision).

Runs of the simulation: `bleeding` («кровь не останавливается») was booked in runs 1 and 2
and in run 3 read «При угрозе жизни звоните 103 или 112» six times.

- A phrase of config/clinic.yaml ``emergency.phrases`` switches the state on — and so does
  the «скорая» outcome of a scenario (04-11). While it is on, no booking starts or goes on:
  not by «записывайте», not by an old button. The screen has «Позвать администратора» and
  «Меню», no «Записаться в травмпункт».
- The answers are templates picked by the words of the question; the same line is never
  said twice in a row.
- Where the scripts have addresses (an open fracture, 04-07; a bite with the signs, 04-11)
  «куда ехать?» names them.
- Out: «Меню», or words that the sign has passed (``emergency.over``). A booking made after
  that goes to the administrators with a line about the phrase.
- «Позвать администратора»: outside the administrators' hours the bot says so at once; the
  request carries «⚠️ угроза жизни: …».

The clinic clock is Thursday, 12 November 2026, 12:00.
"""

from __future__ import annotations

from datetime import timedelta
from itertools import pairwise

import pytest

from app.bot import texts
from app.config import ClinicData
from app.flow.commands import danger_question
from app.flow.loader import KnowledgeBase
from tests.fake_deepseek import FakeDeepSeek
from tests.test_conversation import book_dislocation, bookings
from tests.test_sim_findings_2 import MENU, session

BLEEDING = "срочно! порезал руку сильно, кровь не останавливается, что делать??"
STATE = [texts.MENU_ADMIN, texts.MENU]  # the buttons while the state is on
AMBULANCE = "103 или 112"


async def danger(chat) -> dict | None:
    return (await session(chat))["talk"]["danger"]


async def on(chat, said: str = BLEEDING) -> None:
    await chat.start()
    await chat.say(said)
    assert chat.text == texts.DANGER_FIRST and chat.labels() == STATE
    assert await danger(chat) is not None


# --- the words of the question -----------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "kind"),
    [
        ("да не 103, мне в травмпункт надо! кровь хлещет, рука в крови вся", "insists"),
        ("да не буду я звонить, мне записаться надо!", "insists"),
        ("записывайте меня уже", "insists"),
        ("скажите куда ехать сейчас!", "where"),
        ("скажите хотя бы куда ехать, в какую больницу, у меня рука не останавливается", "where"),
        ("я записаться хочу на приём, дайте мне запись или скажите куда ехать!", "where"),
        ("да не буду я звонить, мне записаться надо! сколько ждать?", "how_long"),
        ("а скорая когда приедет?", "how_long"),
        ("вызвал скорую", "called"),
        ("позвонила в 103, едут", "called"),
        ("спасибо", "called"),
        ("кровь не останавливается!!", "other"),
        ("всё, хватит. толку от вас ноль. поеду сам в больницу", "other"),
    ],
)
def test_the_kind_of_the_question(said: str, kind: str) -> None:
    assert danger_question(said) == kind


# --- on, and nothing is booked ----------------------------------------------------------------


@pytest.mark.parametrize(
    "said", [BLEEDING, "ребёнок не дышит", "упал, кость торчит из ноги", "муж потерял сознание"]
)
async def test_a_phrase_switches_the_state_on(make_chat, kb: KnowledgeBase, said: str) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await on(chat, said)
    assert texts.MENU_BOOK not in chat.labels() and fake.requests == []


async def test_it_interrupts_a_booking_and_the_booking_is_not_resumed(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")
    await chat.press(texts.AUDIENCE_SELF)
    old = chat.button("Бытовая травма").data
    await chat.say("кровь хлещет, не останавливается")
    assert chat.text == texts.DANGER_FIRST and chat.labels() == STATE
    state = await session(chat)
    assert (state["step"], state["wizard"], state["paused"]) == ("idle", False, None)
    await chat.data(old)  # a button of the step that was on screen
    assert chat.text == texts.DANGER_NO_BOOKING and chat.labels() == STATE
    assert (await session(chat))["step"] == "idle"


@pytest.mark.parametrize("data", ["m:book", "m:resume", "m:more", "t:g_limb", "ex:ankle", "m:ask"])
async def test_old_buttons_do_not_start_a_booking(make_chat, data: str) -> None:
    chat = make_chat()
    await on(chat)
    await chat.data(data)
    assert chat.text == texts.DANGER_NO_BOOKING and chat.labels() == STATE
    state = await session(chat)
    assert (state["step"], state["wizard"]) == ("idle", False)


async def test_the_book_command_does_not_start_a_booking(make_chat) -> None:
    chat = make_chat()
    await on(chat)
    chat._take(await chat.conversation.command(chat.user, "book"))
    assert chat.text == texts.DANGER_NO_BOOKING and chat.labels() == STATE


async def test_words_do_not_start_a_booking(make_chat, kb: KnowledgeBase) -> None:
    fake = FakeDeepSeek()
    chat = make_chat(assistant=fake.assistant(kb))
    await on(chat)
    said = []
    for text in (
        "да не 103, мне в травмпункт надо! кровь хлещет, рука в крови вся",
        "стекло разбилось, порезал предплечье глубоко, кровь не останавливается!!",
        "да не буду я звонить, мне записаться надо! сколько ждать?",
        "скажите куда ехать сейчас!",
        "скажите хотя бы куда ехать, в какую больницу",
        "записывайте меня уже, сколько можно по кругу!",
        "дальше давайте, записывайте меня",
        "я третий раз вам пишу: порез глубокий, кровь не останавливается",
    ):
        await chat.say(text)
        (only,) = chat.replies
        said.append(only.text)
        assert chat.labels() == STATE and "Шаг" not in only.text
    assert said[0] == texts.DANGER_NO_BOOKING
    assert said[1] == texts.DANGER_OTHER
    assert said[2] == texts.DANGER_HOW_LONG
    assert said[3] == texts.DANGER_WHERE
    assert said[4] != texts.DANGER_WHERE  # the same line is not said twice: the next one
    assert all(a != b for a, b in pairwise([texts.DANGER_FIRST, *said]))
    assert all(AMBULANCE in line or "103" in line for line in said)
    assert fake.requests == [] and await bookings(chat.conversation.db) == []


async def test_the_ambulance_was_called(make_chat) -> None:
    chat = make_chat()
    await on(chat)
    await chat.say("вызвал скорую, спасибо")
    assert chat.text == texts.DANGER_CALLED and chat.labels() == STATE


async def test_the_clinics_own_line_is_added_to_every_answer(make_chat, clinic: ClinicData) -> None:
    phone = "+7 (343) 000-00-00"
    line = texts.AMBULANCE_LINE.format(phone=phone)
    chat = make_chat()
    contacts = clinic.contacts.model_copy(update={"ambulance_line_phone": phone})
    chat.conversation.clinic = clinic.model_copy(update={"contacts": contacts})
    await chat.start()
    await chat.say(BLEEDING)
    assert chat.text == f"{texts.DANGER_FIRST} {line}"
    await chat.say("запишите меня")
    assert chat.text == f"{texts.DANGER_NO_BOOKING} {line}"


# --- where the scripts have addresses -------------------------------------------------------


async def test_an_open_fracture_has_the_addresses_of_its_script(make_chat) -> None:
    chat = make_chat()
    await on(chat, "упал с лестницы, кость торчит из ноги")
    await chat.say("куда ехать-то?")
    said = chat.text
    for part in (texts.ROUTE_GO, texts.DANGER_ADULTS, "Городская травматологическая больница № 36",
                 texts.DANGER_CHILDREN, "ДГКБ № 9", texts.DANGER_WHERE_KNOWN):  # fmt: skip
        assert part in said, part
    assert texts.DANGER_WHERE not in said  # «адреса нет» would be false here


async def test_the_addresses_for_the_one_the_talk_is_about(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.say("у сына кость торчит из руки")
    await chat.say("в какую больницу ехать?")
    assert "ДГКБ № 9" in chat.text and "№ 36" not in chat.text
    assert texts.DANGER_ADULTS not in chat.text


async def test_the_emergency_outcome_of_a_scenario_switches_it_on(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press("Рана, ожог, укус")
    await chat.press("Укус животного")
    await chat.press(texts.AUDIENCE_SELF)
    await chat.press("Да, есть")
    assert "При угрозе жизни звоните 103 или 112" in chat.text  # the script's outcome, as it was
    assert "Токсикологическое отделение ГКБ № 14" in chat.text and chat.labels() == STATE
    assert await danger(chat) is not None
    await chat.say("запишите меня всё-таки")
    assert chat.text == texts.DANGER_NO_BOOKING
    await chat.say("а куда ехать?")
    assert "Токсикологическое отделение ГКБ № 14, ул. 22-го Партсъезда, 15В" in chat.text
    assert texts.DANGER_WHERE_KNOWN in chat.text


# --- out ------------------------------------------------------------------------------------


@pytest.mark.parametrize("out", ["button", "меню", "command", "start"])
async def test_the_menu_switches_it_off(make_chat, out: str) -> None:
    chat = make_chat()
    await on(chat)
    if out == "button":
        await chat.press(texts.MENU)
    elif out == "command":
        chat._take(await chat.conversation.menu(chat.user))
    elif out == "start":
        await chat.start()  # /start is a way out as well, and the phrase is not forgotten
    else:
        await chat.say(out)
    assert chat.labels()[-4:] == MENU and await danger(chat) is None
    assert (await session(chat))["talk"]["danger_said"] == "кровь не останавливается"


@pytest.mark.parametrize(
    "said", ["всё, кровь остановилась", "уже не течёт", "ложная тревога, всё нормально"]
)
async def test_words_that_the_sign_has_passed_switch_it_off(make_chat, said: str) -> None:
    chat = make_chat()
    await on(chat)
    await chat.say(said)
    assert chat.labels() == MENU and await danger(chat) is None


async def test_a_booking_after_it_goes_to_the_administrators_with_the_phrase(make_chat) -> None:
    chat = make_chat()
    await on(chat)
    await chat.press(texts.MENU)
    posted = len(chat.admin)
    await book_dislocation(chat)  # with /start inside: the phrase said is not forgotten by it
    (booking,) = await bookings(chat.conversation.db)
    notes = [p.text for p in chat.admin[posted:]]
    assert any("фраза из списка угрозы жизни: «кровь не останавливается»" in n for n in notes)
    assert any(f"№ {booking.id}" in n for n in notes)


async def test_without_the_state_a_booking_has_no_such_line(make_chat) -> None:
    chat = make_chat()
    await book_dislocation(chat)
    assert not any("угрозы жизни" in p.text for p in chat.admin)


# --- «Позвать администратора» -------------------------------------------------------------------


async def test_in_working_hours_the_request_is_marked(make_chat) -> None:
    chat = make_chat()
    await on(chat)
    await chat.press(texts.MENU_ADMIN)
    assert texts.DANGER_ADMIN_LATER.split("{")[0] not in chat.text  # 12:00: they are there
    await chat.press(texts.CONSENT_YES)
    await chat.contact()
    (post,) = chat.admin
    assert post.text.startswith("⚠️ угроза жизни: кровь не останавливается\n")
    assert "обращение № 1" in chat.text and chat.labels() == STATE
    assert await danger(chat) is not None  # still on: the request does not book either


async def test_outside_the_hours_the_bot_says_so_at_once(make_chat, clock) -> None:
    clock.now += timedelta(hours=11)  # 23:00
    chat = make_chat()
    await on(chat)
    await chat.press(texts.MENU_ADMIN)
    first = chat.replies[0]
    assert (
        first.text
        == "Администратор ответит только после 08:00.\n⚠️ Не ждите его — звоните 103 или 112."
    )
    assert texts.CONSENT_YES in chat.labels()  # said before the consent and the phone
    await chat.press(texts.CONSENT_YES)
    await chat.contact()
    assert chat.admin[-1].text.startswith("⚠️ угроза жизни: кровь не останавливается\n")


async def test_asking_for_an_administrator_in_words(make_chat) -> None:
    chat = make_chat()
    await on(chat)
    await chat.say("да передавайте уже хоть кому-нибудь, позовите администратора")
    assert texts.CONSENT_YES in chat.labels()
    await chat.say("согласен")  # the consent and the phone are read as usual
    assert chat.replies[-1].ask_contact


# --- what stays as it was ---------------------------------------------------------------------


async def test_other_templates_keep_the_old_line(make_chat, kb: KnowledgeBase, clinic) -> None:
    assert clinic.clinic.emergency.reply == "При угрозе жизни звоните 103 или 112."
    assert texts.MEDICAL.endswith("При угрозе жизни звоните 103 или 112.")


def test_the_config_names_the_scripts_with_addresses(kb: KnowledgeBase, clinic) -> None:
    emergency = clinic.clinic.emergency
    assert emergency.over and emergency.routes
    for route in emergency.routes:
        assert set(route.phrases) <= set(emergency.phrases)
        scenario = kb.scenario(route.scenario)
        for outcome in (route.adult, route.child):
            assert scenario.outcomes[outcome].destinations


# --- the words switch off only the state of their own kind (14.11.2026) ------------------------
# After a loss of consciousness or a stop of breathing nothing the patient writes switches the
# state off: «Меню» or /start only. «Кровь остановилась» does not switch off «не дышит».


@pytest.mark.parametrize(
    ("on_by", "said"),
    [
        ("ребёнок не дышит", "уже дышит, всё нормально"),
        ("ребёнок не дышит", "задышал"),
        ("ребёнок не дышит", "кровь остановилась"),
        ("ребёнок не дышит", "ложная тревога"),
        ("муж потерял сознание", "пришёл в себя"),
        ("муж потерял сознание", "ложная тревога, он пришёл в себя"),
        ("задыхается, трудно дышать", "ложная тревога"),
    ],
)
async def test_after_consciousness_or_breathing_words_do_not_switch_it_off(
    make_chat, on_by: str, said: str
) -> None:
    chat = make_chat()
    await on(chat, on_by)
    await chat.say(said)
    assert chat.labels() == STATE and await danger(chat) is not None
    assert texts.MENU_BOOK not in chat.labels()
    await chat.press(texts.MENU)  # the way out
    assert chat.labels() == MENU and await danger(chat) is None
    assert (await session(chat))["talk"]["danger_said"]  # the line for the administrators stays


async def test_the_bleeding_words_do_not_switch_off_another_phrase_said_later(make_chat) -> None:
    chat = make_chat()
    await on(chat)  # «кровь не останавливается»
    await chat.say("он потерял сознание!")
    await chat.say("кровь остановилась")
    assert chat.labels() == STATE and await danger(chat) is not None


@pytest.mark.parametrize(
    ("on_by", "said", "off"),
    [
        ("у него судороги", "ложная тревога", False),  # words work after bleeding only
        ("у него судороги", "кровь остановилась", False),
        ("ударило током", "ложная тревога", False),
        ("кровь хлещет", "ложная тревога", True),
        ("кровь хлещет", "кровотечение остановилось", True),
        ("кровь хлещет", "пришёл в себя", False),  # no longer a word of the list
    ],
)
async def test_which_words_switch_which_state_off(
    make_chat, on_by: str, said: str, off: bool
) -> None:
    chat = make_chat()
    await on(chat, on_by)
    await chat.say(said)
    assert (await danger(chat) is None) is off


async def test_the_outcome_of_a_scenario_is_not_switched_off_by_words(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press("Рана, ожог, укус")
    await chat.press("Укус животного")
    await chat.press(texts.AUDIENCE_SELF)
    await chat.press("Да, есть")  # the signs of 04-11: pressure, breathing, consciousness
    await chat.say("ложная тревога")
    assert chat.labels() == STATE and await danger(chat) is not None


def test_the_exit_words_of_the_config(clinic) -> None:
    emergency = clinic.clinic.emergency
    words = {w for group in emergency.over for w in group.phrases}
    assert "ложная тревога" in words and "кровь остановилась" in words
    for gone in ("пришел в себя", "пришла в себя", "уже дышит", "задышал", "задышала"):
        assert gone not in words
    (bleeding,) = emergency.over  # the words work after the phrases about bleeding only
    assert bleeding.after and set(bleeding.after) <= set(emergency.phrases)
    assert all("кров" in phrase for phrase in bleeding.after)
    assert "ложная тревога" in bleeding.phrases
