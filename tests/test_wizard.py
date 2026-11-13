"""Part C: topics by context, the one-message wizard, day → time, colours, the card, drafts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from app.bot import texts
from app.bot.conversation import Button, Reply, Turn
from app.bot.layout import SHORT, pack
from app.bot.telegram import Delivery, Drafts, bot_commands, bot_descriptions, rich_card
from app.config import Settings
from app.flow import topics
from app.flow.loader import KnowledgeBase
from app.flow.schema import BUTTON_MAX
from tests.chat_harness import Chat
from tests.fake_deepseek import FakeDeepSeek

LIMB_TOPICS = {
    "Рука или нога", "Упал, ударился, подвернул", "Похоже на вывих", "Похоже на перелом",
    "Растяжение или разрыв связок", "Гипс, ортез, скотчкаст", "Наложить гипс", "Снять гипс",
    "Ортез", "Скотчкаст", "Повторная пункция", "Удаление спиц",
}  # fmt: skip
HEAD_TOPICS = {"Голова, лицо, глаза, уши", "Ударился головой", "Травма глаза", "Ухо, горло, нос"}
TORSO_TOPICS = {"Удар в грудь или живот", "Потянул шею или спину", "Травма половых органов"}


async def every_topic_label(chat: Chat) -> set[str]:
    """Press through every topic submenu reachable from the screen, collecting labels."""
    seen: set[str] = set()
    todo = [chat.turn]
    visited: set[str] = set()
    while todo:
        chat.turn = todo.pop()
        for button in chat.buttons():
            seen.add(button.label)
            item = topics_item(chat, button.data)
            if button.data == "t:list" or (item is not None and item.menu is not None):
                if button.data in visited:
                    continue
                visited.add(button.data)
                saved = chat.turn
                todo.append(await chat.data(button.data))
                chat.turn = saved
    return seen


def topics_item(chat: Chat, data: str):  # type: ignore[no-untyped-def]
    if not data.startswith("t:"):
        return None
    return chat.conversation.kb.topics.item(data[2:])


def route(area: str | None, *candidates: str, scenario: str | None = None) -> dict[str, Any]:
    return {"type": "complaint", "area": area, "scenario": scenario, "candidates": list(candidates)}


# --- topics by context -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "reply"),
    [
        ("болит рука", route("limb", "dislocation", "fracture", "cut_sutures")),
        ("подвернул ногу", route("limb", "ligament_rupture", "dislocation", "fracture")),
        ("обжёг палец", route("limb", "burns", "cut_sutures")),
    ],
)
@pytest.mark.parametrize("llm", [True, False])
async def test_limb_complaints_show_no_head_or_torso_topics(
    make_chat, kb: KnowledgeBase, text: str, reply: dict, llm: bool
) -> None:
    assistant = FakeDeepSeek(reply).assistant(kb) if llm else None  # False: the word list
    chat = make_chat(assistant=assistant)
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.say(text)
    labels = await every_topic_label(chat)
    assert labels & LIMB_TOPICS, labels
    assert not labels & (HEAD_TOPICS | TORSO_TOPICS), labels


@pytest.mark.parametrize("llm", [True, False])
async def test_head_complaint_shows_no_limb_topics(make_chat, kb: KnowledgeBase, llm: bool) -> None:
    assistant = FakeDeepSeek(route("head", "head_trauma")).assistant(kb) if llm else None
    chat = make_chat(assistant=assistant)
    await chat.start()
    await chat.say("ударился головой")
    labels = await every_topic_label(chat)
    assert "Ударился головой" in labels
    assert not labels & LIMB_TOPICS, labels


async def test_list_button_keeps_to_the_area(make_chat, kb: KnowledgeBase) -> None:
    chat = make_chat(assistant=FakeDeepSeek(route("limb"), {"type": "complaint"}).assistant(kb))
    await chat.start()
    await chat.say("болит рука")
    await chat.press("Другое")
    assert chat.labels() == ["Опишите своими словами", "Позвать администратора", texts.BACK]
    await chat.press("Опишите своими словами")
    assert texts.DESCRIBE in chat.text
    await chat.say("сам не понимаю")  # still unclear: the arm is remembered
    assert "Что случилось с рукой или ногой?" in chat.text
    await chat.data("t:list")  # «Выбрать из списка» keeps to the arm too
    assert "Что случилось с рукой или ногой?" in chat.text
    assert not set(chat.labels()) & (HEAD_TOPICS | TORSO_TOPICS)


GROUPS = [
    "Рука или нога", "Рана, ожог, укус", "Голова, лицо, глаза, уши", "Шея, спина, грудь, живот",
    "Уже лечусь у вас", "Ребёнок до 6 месяцев", "Другое",
]  # fmt: skip


async def test_topic_buttons_are_about_what_happened(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press(texts.TOPIC_LIST)  # no complaint yet: the groups
    assert chat.labels() == [*GROUPS, texts.BACK]
    await chat.press("Рука или нога")  # from the groups: every topic one press away
    assert chat.labels() == [
        "Похоже на вывих", "Похоже на перелом", "Растяжение или разрыв связок",
        "Наложить гипс", "Снять гипс", "Ортез", "Скотчкаст", "Другое", texts.BACK,
    ]  # fmt: skip
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.say("болит рука")  # after a complaint: what happened, then the details
    assert chat.labels() == [
        "Упал, ударился, подвернул", "Порез, рана", "Ожог", "Укус животного",
        "Гипс, ортез, скотчкаст", "Уже лечусь у вас", "Другое", texts.BACK,
    ]  # fmt: skip
    await chat.press("Упал, ударился, подвернул")
    assert chat.labels() == [
        "Похоже на вывих", "Похоже на перелом", "Растяжение или разрыв связок", "Не знаю",
        texts.BACK,
    ]  # fmt: skip
    await chat.press(texts.BACK)
    await chat.press("Уже лечусь у вас")
    assert chat.labels() == [
        "Повторный приём", "Перевязка", "Больничный", "Повторная пункция", "Удаление спиц",
        texts.BACK,
    ]  # fmt: skip


async def test_unsure_goes_the_dislocation_way_for_now(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.say("подвернул ногу")  # no LLM: the word list → «На что больше похоже?»
    await chat.press("Не знаю")
    await chat.press("Для себя")
    assert "Как получена травма?" in chat.text
    assert chat.conversation.kb.topics.item("f_unsure").scenario == "dislocation"


async def test_a_burnt_finger_is_not_asked_about_the_face(make_chat, kb: KnowledgeBase) -> None:
    reply = route("limb", "burns", scenario="burns")
    chat = make_chat(assistant=FakeDeepSeek(reply).assistant(kb))
    await chat.start()
    await chat.say("обжёг палец")
    await chat.press("Для себя")
    assert "Как получили ожог?" in chat.text and "Лицо" not in chat.labels()


def test_word_list_finds_the_area() -> None:
    assert topics.guess_area("болит рука") == "limb"
    assert topics.guess_area("подвернул ногу") == "limb"
    assert topics.guess_area("обжёг палец") == "limb"
    assert topics.guess_area("ударился головой") == "head"
    assert topics.guess_area("потянул спину") == "neck_back"
    assert topics.guess_area("шея болит") == "neck_back"
    assert topics.guess_area("ударился грудью") == "chest_abdomen"
    assert topics.guess_area("грудничок упал с дивана") is None  # a baby, not the chest
    assert topics.guess_area("ударился головой и болит колено") is None  # two areas


def test_candidates_open_the_narrowest_menu(kb: KnowledgeBase) -> None:
    assert topics.best_menu(kb, "limb", ["ligament_rupture", "dislocation"]) == "fall"
    assert topics.best_menu(kb, "limb", ["dislocation", "burns"]) == "limb"
    assert topics.best_menu(kb, "limb", ["head_trauma"]) == "limb"  # does not fit the area
    assert topics.best_menu(kb, None, ["cut_sutures", "animal_bite"]) == "wounds"
    assert topics.best_menu(kb, None, []) is None


def test_every_scenario_has_body_areas(kb: KnowledgeBase) -> None:
    for sc in kb.scenarios.values():
        assert sc.body_areas, sc.id
    assert kb.scenario("dislocation").body_areas == ("limb",)
    assert kb.scenario("head_trauma").body_areas == ("head",)
    assert kb.scenario("neurology").body_areas == ("neck_back",)
    assert kb.scenario("chest_abdomen").body_areas == ("chest_abdomen",)
    assert kb.scenario("genital_trauma").body_areas == ("genital",)


# --- one message, «Шаг N из M», «Назад» ---------------------------------------------------


async def test_buttons_replace_the_message_with_the_next_step(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    (screen,) = chat.replies
    assert screen.edit and screen.screen
    assert screen.text.startswith("Шаг 1 из 5 · Что случилось")
    await to_topic_after_book(chat)
    assert chat.replies[0].text.startswith("Шаг 2 из 5 · Для кого запись")
    await chat.press("Для себя")
    assert chat.replies[0].text.startswith("Шаг 3 из 5 · Уточняющие вопросы")


async def to_topic_after_book(chat: Chat) -> None:
    await chat.press(texts.TOPIC_LIST)
    await chat.press("Рука или нога")
    await chat.press("Похоже на вывих")


async def test_back_returns_the_previous_screen_and_answer(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await to_topic_after_book(chat)
    await chat.press("Для себя")
    asked = chat.text
    await chat.press("Бытовая травма")
    assert "Когда была получена травма?" in chat.text
    await chat.press(texts.BACK)
    assert chat.text == asked and chat.replies[0].edit
    await chat.press("Нападение, драка, сбила машина")  # another answer after «Назад»
    assert "Любой муниципальный травмпункт" in chat.text
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press(texts.BACK)  # the first screen: back to the main menu
    assert texts.MENU_BOOK in chat.labels()


async def test_back_from_the_time_grid_to_the_days(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await to_topic_after_book(chat)
    for label in ("Для себя", "Бытовая травма", "В течение 7 дней", "Нет, не обращались"):
        await chat.press(label)
    days = chat.replies[-1].text
    await chat.pick_day("Завтра")
    periods = chat.text
    assert chat.periods()
    await chat.pick_period("08:00")
    assert chat.times()
    await chat.press(texts.BACK)
    assert chat.text == periods
    await chat.press(texts.BACK)
    assert chat.text == days


# --- day, then times ------------------------------------------------------------------------


async def test_days_two_in_a_row_times_four_in_a_row(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await to_topic_after_book(chat)
    for label in ("Для себя", "Бытовая травма", "В течение 7 дней", "Нет, не обращались"):
        await chat.press(label)
    screen = chat.replies[-1]
    day_rows = [row for row in screen.buttons if row[0].data.startswith("d:2")]
    assert day_rows and all(len(row) <= 2 for row in day_rows)
    assert [b.label for b in day_rows[0]] == ["Сегодня, 12.11", "Завтра, 13.11"]
    assert texts.LATER in chat.labels() and texts.MENU_ADMIN in chat.labels()
    await chat.press(texts.LATER)
    assert texts.EARLIER in chat.labels()
    await chat.pick_day()
    await chat.pick_period()
    screen = chat.replies[-1]
    time_rows = [row for row in screen.buttons if row[0].data.startswith("s:")]
    assert time_rows and all(len(row) <= 4 for row in time_rows)
    labels = [b.label for row in time_rows for b in row]
    assert labels == sorted(labels) and len(labels) == len(set(labels))
    assert all(len(label) == 5 and label[2] == ":" for label in labels)
    assert "Врач смены:" in screen.text or "Врачи смены:" in screen.text


async def to_days(chat: Chat) -> None:
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await to_topic_after_book(chat)
    for label in ("Для себя", "Бытовая травма", "В течение 7 дней", "Нет, не обращались"):
        await chat.press(label)


async def test_parts_of_the_day_only_with_free_time(make_chat, schedule) -> None:
    chat = make_chat()
    await to_days(chat)
    await chat.pick_day("Завтра")
    labels = [b.label for b in chat.periods()]
    assert labels == ["Утро 8–12", "День 12–18", "Вечер 18–23", "Ночь 23–8"]
    assert [len(row) for row in chat.replies[-1].buttons][:2] == [2, 2]  # two in a row
    assert not any(b.label.startswith("Ближайшее") for b in chat.buttons())
    await chat.pick_period("23:00")
    night = [b.label for b in chat.times()]
    assert night and all(t >= "23:00" or t < "08:00" for t in night)
    assert night[0] < "08:00"  # the calendar day: its early hours first, then 23:00–24:00
    await chat.press(texts.BACK)
    await chat.pick_period("08:00")
    assert all("08:00" <= b.label < "12:00" for b in chat.times())


async def test_nearest_time_is_offered_first_and_books_in_one_tap(make_chat, db) -> None:
    from sqlalchemy import select

    from app.db.models import Booking

    chat = make_chat()
    await to_days(chat)
    nearest = chat.nearest()
    assert chat.labels()[0] == nearest.label and nearest.style == "primary"
    assert nearest.label.startswith("Ближайшее: сегодня, ")
    await chat.data(nearest.data)
    assert texts.CONSENT in chat.text  # straight to the patient's data
    await chat.press(texts.CONSENT_YES)
    await chat.say("Петров Иван Сергеевич")
    await chat.say("12.03.1985")
    await chat.contact()
    async with db() as s:
        (booking,) = (await s.scalars(select(Booking))).all()
    assert f"{booking.start_at.astimezone(chat.conversation.settings.tz):%H:%M}" in nearest.label


async def test_one_part_of_the_day_goes_straight_to_times(make_chat, clock) -> None:
    from tests.conftest import NOW

    clock.now = NOW.replace(hour=22, minute=0)  # +1 h to travel: only the night is left
    chat = make_chat()
    await to_days(chat)
    await chat.pick_day("Сегодня")
    assert not chat.periods() and chat.times()
    assert all(b.label >= "23:00" for b in chat.times())


def test_nearest_label_names_the_day() -> None:
    from datetime import date, datetime, timedelta

    from app.bot.slots_view import nearest_label
    from app.schedule.base import Slot
    from tests.conftest import TZ

    def at(day: int, hour: int) -> Slot:
        start = datetime(2026, 11, day, hour, 30, tzinfo=TZ)
        return Slot("x", start, start + timedelta(minutes=30), "Д", "К", False, False)

    today = date(2026, 11, 12)
    assert nearest_label(at(12, 14), today, TZ) == "Ближайшее: сегодня, 14:30"
    assert nearest_label(at(13, 9), today, TZ) == "Ближайшее: завтра, 09:30"
    assert nearest_label(at(14, 9), today, TZ) == "Ближайшее: сб, 14.11, 09:30"


# --- labels that fit a phone ---------------------------------------------------------------


def test_pack_puts_long_labels_alone() -> None:
    buttons = [
        Button(label, "x")
        for label in ("Короткая", "Ещё одна", "Очень длинная подпись кнопки", "Раз")
    ]
    rows = pack(buttons)
    assert [[b.label for b in row] for row in rows] == [
        ["Короткая", "Ещё одна"], ["Очень длинная подпись кнопки"], ["Раз"],
    ]  # fmt: skip


def _all_labels(kb: KnowledgeBase) -> list[str]:
    labels = [i.label for m in kb.topics.menus.values() for i in m.items]
    labels += [c.label for sc in kb.scenarios.values() for q in sc.questions for c in q.buttons()]
    labels += [v for k, v in vars(texts).items() if k.isupper() and isinstance(v, str)
               and len(v) <= BUTTON_MAX and "{" not in v]  # fmt: skip
    return labels


async def test_no_two_column_row_has_a_long_label(make_chat, kb: KnowledgeBase) -> None:
    assert all(len(label) <= BUTTON_MAX for label in _all_labels(kb))
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    turns = []
    await chat.press(texts.TOPIC_LIST)
    turns.append(chat.turn)
    for group in GROUPS[:5]:
        await chat.press(group)
        turns.append(chat.turn)
        await chat.press(texts.BACK)
    for complaint in ("болит рука", "подвернул ногу", "шея болит", "ударился головой"):
        await chat.start()
        await chat.press(texts.MENU_BOOK)
        await chat.say(complaint)
        turns.append(chat.turn)
    for turn in turns:
        for reply in turn.replies:
            for row in reply.buttons:
                if len(row) > 1 and not row[0].data.startswith(("s:", "d:")):
                    assert all(len(b.label) <= SHORT for b in row), [b.label for b in row]


# --- colours --------------------------------------------------------------------------------


async def test_main_action_confirm_and_cancel_colours(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    assert chat.button(texts.MENU_BOOK).style == "primary"
    await chat.press(texts.MENU_ADMIN)
    assert chat.button(texts.CONSENT_YES).style == "success"
    assert chat.button(texts.CONSENT_NO).style is None
    chat._take(await chat.conversation.forget(chat.user))
    assert chat.button(texts.FORGET_YES).style == "danger"


# --- the card -------------------------------------------------------------------------------


async def test_card_is_also_a_valid_rich_message(make_chat) -> None:
    from tests.test_conversation import book_dislocation

    chat = make_chat()
    await book_dislocation(chat)
    card = next(r for r in chat.replies if r.card is not None)
    styles = {b.label: b.style for row in card.buttons for b in row}
    assert styles == {texts.MOVE: None, texts.CANCEL: "danger"}
    message = rich_card(card.card)
    dumped = message.model_dump(exclude_none=True)
    kinds = [block["type"] for block in dumped["blocks"]]
    assert kinds[:2] == ["heading", "paragraph"] and "list" in kinds and "footer" in kinds
    assert not any(r.venue for r in chat.replies)  # no map point in config yet (TODO)


def test_venue_once_the_map_point_is_known(clinic) -> None:
    from app.bot import render
    from app.config import Location

    center = clinic.contacts.trauma_center.model_copy(
        update={"location": Location(latitude=56.8, longitude=60.6)}
    )
    contacts = clinic.contacts.model_copy(update={"trauma_center": center})
    venue = render.venue(clinic.model_copy(update={"contacts": contacts}))
    assert venue is not None and venue.address.startswith("ул. Шейнкмана, 113/2")
    assert render.venue(clinic) is None


async def test_booking_sends_the_map_point_when_configured(make_chat, clinic) -> None:
    from app.config import Location
    from tests.test_conversation import book_dislocation

    center = clinic.contacts.trauma_center.model_copy(
        update={"location": Location(latitude=56.123456, longitude=60.123456)}
    )
    chat = make_chat()
    contacts = clinic.contacts.model_copy(update={"trauma_center": center})
    chat.conversation.clinic = clinic.model_copy(update={"contacts": contacts})
    await book_dislocation(chat)
    kinds = ["card" if r.card else "venue" if r.venue else "text" for r in chat.replies]
    assert kinds == ["text", "card", "venue", "text"]  # thanks, card, map, payment question
    venue = chat.replies[2].venue
    assert (venue.latitude, venue.longitude, venue.title) == (
        56.123456, 60.123456, "Травмпункт «УГМК-Здоровье»",
    )  # fmt: skip

    bot = _Bot()
    await Delivery(bot, None, None)(Turn([chat.replies[2]]), 1)  # type: ignore[arg-type]
    assert bot.calls == [
        ("venue", {"latitude": 56.123456, "longitude": 60.123456,
                   "title": "Травмпункт «УГМК-Здоровье»",
                   "address": "ул. Шейнкмана, 113/2, вход через детское отделение"})
    ]  # fmt: skip


# --- Telegram delivery with fallbacks --------------------------------------------------------


@dataclass
class _Sent:
    message_id: int


@dataclass
class _Chat:
    id: int


@dataclass
class _Pressed:
    text: str | None
    chat: _Chat = field(default_factory=lambda: _Chat(1))
    message_id: int = 7
    edits: list[tuple[str, Any]] = field(default_factory=list)

    async def edit_text(self, text: str, reply_markup: Any = None) -> None:
        self.edits.append((text, reply_markup))

    async def edit_reply_markup(self, reply_markup: Any = None) -> None:
        self.edits.append(("<markup>", reply_markup))


class _Bot:
    def __init__(self, *, refuse_rich: bool = False, refuse_style: bool = False) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.refuse_rich, self.refuse_style = refuse_rich, refuse_style
        self.next_id = 100

    def _id(self) -> _Sent:
        self.next_id += 1
        return _Sent(self.next_id)

    async def send_message(self, chat_id: int, text: str, reply_markup: Any = None, **_: Any):
        styled = reply_markup is not None and any(
            getattr(b, "style", None)
            for row in getattr(reply_markup, "inline_keyboard", [])
            for b in row
        )
        if styled and self.refuse_style:
            raise TelegramBadRequest(SendMessage(chat_id=chat_id, text=text), "bad style")
        self.calls.append(("message", text))
        return self._id()

    async def send_rich_message(self, chat_id: int, rich_message: Any, reply_markup: Any = None):
        if self.refuse_rich:
            raise TelegramBadRequest(SendMessage(chat_id=chat_id, text="x"), "no rich")
        self.calls.append(("rich", rich_message))
        return self._id()

    async def send_venue(self, chat_id: int, **kw: Any):
        self.calls.append(("venue", kw))
        return self._id()

    async def edit_message_reply_markup(self, **kw: Any) -> None:
        self.calls.append(("strip", kw["message_id"]))

    async def send_message_draft(self, **kw: Any) -> None:
        self.calls.append(("draft", kw["text"]))


def _card() -> Any:
    from app.bot.render import Card

    return Card(
        "Запись", "Иван записан.", (("Врач", "Кузнецов"),), ("Стоимость…",), "Взять", ("Паспорт.",)
    )


async def test_delivery_replaces_the_pressed_message_and_strips_old_screens() -> None:
    bot = _Bot()
    delivery = Delivery(bot, None, None)  # type: ignore[arg-type]
    pressed = _Pressed("Шаг 1")
    screen = Reply("Шаг 2", ((Button("Да", "q:x:0"),),), edit=True, screen=True)
    await delivery(Turn([screen]), 1, pressed=pressed)
    assert pressed.edits[0][0] == "Шаг 2" and bot.calls == []
    await delivery(Turn([Reply("Шаг 3", ((Button("Б", "b"),),), screen=True)]), 1)
    assert bot.calls == [("strip", 7), ("message", "Шаг 3")]  # the old screen lost its buttons


async def test_delivery_falls_back_to_text_and_plain_buttons() -> None:
    bot = _Bot(refuse_rich=True, refuse_style=True)
    delivery = Delivery(bot, None, None)  # type: ignore[arg-type]
    card = Reply(_card().plain(), ((Button("Отменить", "b:cancel:1", "danger"),),), card=_card())
    await delivery(Turn([card]), 1)
    assert bot.calls == [("message", card.text)]  # no rich message, no colours — still sent
    plain = Delivery(_Bot(), None, None, rich_messages=False)  # type: ignore[arg-type]
    await plain(Turn([card]), 1)
    assert plain.bot.calls == [("message", card.text)]  # type: ignore[attr-defined]


async def test_rich_card_is_sent_when_allowed() -> None:
    bot = _Bot()
    await Delivery(bot, None, None)(Turn([Reply("x", card=_card())]), 1)  # type: ignore[arg-type]
    assert bot.calls[0][0] == "rich"


async def test_drafts_switch_off_after_a_refusal() -> None:
    class Refusing(_Bot):
        async def send_message_draft(self, **kw: Any) -> None:
            raise TelegramBadRequest(SendMessage(chat_id=1, text="x"), "no drafts")

    drafts = Drafts(Refusing())  # type: ignore[arg-type]
    sink = drafts.sink(1)
    assert sink is not None
    await sink("Травмпункт работает круглосуточно")
    assert drafts.enabled is False and drafts.sink(1) is None


# --- streaming the answer -------------------------------------------------------------------


async def test_answer_is_streamed_then_sent_whole(make_chat, kb: KnowledgeBase) -> None:
    answer = (
        "Травмпункт работает круглосуточно по предварительной записи, адрес — ул. Шейнкмана, 113/2."
    )
    fake = FakeDeepSeek({"type": "clinic_question"}, {"found": True, "answer": answer})
    chat = make_chat(assistant=fake.assistant(kb))
    drafts: list[str] = []

    async def sink(text: str) -> None:
        drafts.append(text)

    await chat.start()
    await chat.press(texts.MENU_ASK)
    chat._take(await chat.conversation.text(chat.user, "Как вы работаете?", draft=sink))
    assert drafts and drafts[-1] == answer and all(answer.startswith(d) for d in drafts)
    assert chat.text.startswith(answer)
    assert fake.requests[-1]["stream"] is True and fake.usage[-1].prompt_tokens == 5000


async def test_a_number_not_in_kb_info_stops_the_stream(kb: KnowledgeBase) -> None:
    answer = "Приём стоит 987 рублей, приходите."
    fake = FakeDeepSeek({"found": True, "answer": answer})
    drafts: list[str] = []

    async def sink(text: str) -> None:
        drafts.append(text)

    result = await fake.assistant(kb).answer("сколько стоит?", on_partial=sink)
    assert result.answer is None  # the whole answer is checked again
    assert drafts and not any("987" in d for d in drafts)


# --- bot profile ----------------------------------------------------------------------------


def test_bot_profile(clinic) -> None:
    settings = Settings(_env_file=None, schedule_provider="mock", demo_mode=True)
    description, short = bot_descriptions(settings, clinic)
    assert len(description) <= 512 and len(short) <= 120
    assert "ул. Шейнкмана, 113/2" in description and "103 или 112" in description
    assert short.startswith(texts.BOT_DEMO_NOTE)
    names = [c.command for c in bot_commands(settings)]
    assert names == ["start", "book", "my", "ask", "admin", "menu", "forget", "demo_next"]
    no_demo = Settings(_env_file=None, demo_mode=False)
    assert "demo_next" not in [c.command for c in bot_commands(no_demo)]


# --- finer areas and the groups (follow-up 2–3) ---------------------------------------------

NECK_TOPICS = [
    "Потянул шею или спину", "Порез, рана", "Ожог", "Укус животного", "Уже лечусь у вас",
    "Другое",
]  # fmt: skip


@pytest.mark.parametrize("llm", [True, False])
async def test_a_sore_neck_shows_only_neck_and_back_topics(
    make_chat, kb: KnowledgeBase, llm: bool
) -> None:
    reply = route("neck_back", "neurology")
    chat = make_chat(assistant=FakeDeepSeek(reply).assistant(kb) if llm else None)
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.say("шея болит")
    assert chat.labels() == [*NECK_TOPICS, texts.BACK]
    labels = await every_topic_label(chat)
    assert "Травма половых органов" not in labels and "Удар в грудь или живот" not in labels


@pytest.mark.parametrize(
    ("text", "area", "first"),
    [("ударился грудью", "chest_abdomen", "Удар в грудь или живот"),
     ("травма мошонки", "genital", "Травма половых органов")],
)  # fmt: skip
async def test_chest_and_genital_areas_have_their_own_topics(
    make_chat, text: str, area: str, first: str
) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.say(text)
    assert chat.labels()[0] == first
    assert "Потянул шею или спину" not in chat.labels()


def _presses_to_each_scenario(kb: KnowledgeBase) -> dict[str, int]:
    """Fewest presses from the groups menu, never through «Другое» or its actions."""
    found: dict[str, int] = {}
    for group in topics.visible(kb, kb.topics.root, None):
        if group.menu == "other":
            continue
        if group.scenario:
            found[group.scenario] = min(found.get(group.scenario, 9), 1)
            continue
        for item in topics.visible(kb, group.menu, group.area):
            if item.scenario:
                found[item.scenario] = min(found.get(item.scenario, 9), 2)
    return found


def test_every_scenario_is_two_presses_from_the_groups(kb: KnowledgeBase) -> None:
    presses = _presses_to_each_scenario(kb)
    assert set(presses) == set(kb.scenarios), set(kb.scenarios) - set(presses)
    assert max(presses.values()) <= 2
    assert "Шея, спина, грудь, живот" in [i.label for i in kb.topics.menus["root"].items]


async def test_two_presses_really_start_each_scenario(make_chat, kb: KnowledgeBase) -> None:
    chat = make_chat()
    started: set[str] = set()
    for group in topics.visible(kb, kb.topics.root, None):
        if group.menu == "other":
            continue
        items = [group] if group.scenario else topics.visible(kb, group.menu, group.area)
        for item in items:
            if not item.scenario:
                continue
            await chat.start()
            await chat.press(texts.MENU_BOOK)
            await chat.press(texts.TOPIC_LIST)
            await chat.press(group.label)
            if item is not group:
                await chat.press(item.label)
            session = await _session(chat)  # the scenario started (who it is for, or its outcome)
            assert session["dialog"]["scenario_id"] or session["pending_scenario"], item.label
            started.add(item.scenario)
    assert started == set(kb.scenarios)


async def _session(chat: Chat) -> dict:
    from app.db.models import User

    async with chat.conversation.db() as s:
        user = await s.get(User, chat.user.id)
        return user.session
