"""Block 3: media from assets/, the pinned card, reactions, «Подробнее», «Как пройти», icons."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.bot import texts
from app.bot.conversation import (
    BOOKED,
    ENTRANCE,
    FEEDBACK,
    REMINDER,
    WELCOME,
    Button,
    Media,
    Reply,
    Turn,
)
from app.bot.layout import width
from app.bot.telegram import Delivery, MediaStore
from app.db.models import Booking
from app.flow.loader import KnowledgeBase
from tests.chat_harness import Chat
from tests.conftest import NOW
from tests.test_conversation import book_dislocation

# --- what the conversation asks for --------------------------------------------------------


async def test_welcome_animation_only_at_the_first_start(make_chat) -> None:
    chat = make_chat()
    await chat.start()
    assert chat.replies[0].media == WELCOME
    await chat.start()
    assert chat.replies[0].media is None


async def _booking(chat: Chat) -> Booking:
    async with chat.conversation.db() as s:
        return (await s.scalars(select(Booking).order_by(Booking.id))).all()[-1]


async def test_booked_animation_and_a_pinned_card(make_chat) -> None:
    chat = make_chat()
    await book_dislocation(chat)
    booked = next(r for r in chat.replies if r.media == BOOKED)
    assert booked.media_only and booked.text == ""
    card = next(r for r in chat.replies if r.card is not None)
    booking = await _booking(chat)
    assert card.pin == booking.id
    assert not any(r.media and r.media.kind == "photo" for r in chat.replies)  # no photos yet


async def test_doctor_photo_once_the_config_has_it(make_chat, clinic) -> None:
    chat = make_chat()
    photos = dict.fromkeys(clinic.schedule.doctors, "doctors/photo.jpg")
    schedule = clinic.schedule.model_copy(update={"doctor_photos": photos})
    chat.conversation.clinic = clinic.model_copy(update={"schedule": schedule})
    await book_dislocation(chat)
    photo = next(r for r in chat.replies if r.media and r.media.kind == "photo")
    booking = await _booking(chat)
    assert photo.media == Media("photo", "doctors/photo.jpg") and photo.media_only
    assert photo.text == f"Врач: {booking.doctor}"


async def test_cancel_and_move_unpin_the_card(make_chat) -> None:
    chat = make_chat()
    await book_dislocation(chat)
    booking = await _booking(chat)
    await chat.conversation.register_pinned_card(booking.id, 4242)
    await chat.press(texts.PAYMENT_SELF)
    await chat.press(texts.MENU_MY)
    await chat.press(texts.MOVE)
    await chat.pick_slot("Завтра")
    assert chat.turn.unpin == [4242]  # the pinned card showed the old time
    await chat.press(texts.MENU_MY)
    await chat.press(texts.CANCEL)
    await chat.press(texts.CANCEL_YES)
    assert chat.turn.unpin == []  # nothing pinned any more


async def test_cancel_unpins(make_chat) -> None:
    chat = make_chat()
    await book_dislocation(chat)
    booking = await _booking(chat)
    await chat.conversation.register_pinned_card(booking.id, 4343)
    await chat.press(texts.PAYMENT_SELF)
    await chat.press(texts.MENU_MY)
    await chat.press(texts.CANCEL)
    await chat.press(texts.CANCEL_YES)
    assert chat.turn.unpin == [4343]


async def test_reminders_carry_their_media_and_way(make_chat, schedule) -> None:
    from datetime import timedelta

    chat = make_chat()
    await book_dislocation(chat)
    await chat.press(texts.PAYMENT_SELF)
    tomorrow = next(
        s
        for s in await schedule.free_slots(NOW + timedelta(days=1, hours=1), NOW + timedelta(2))
        if not s.repeat_window and not s.repeat_cabinet
    )
    await chat.press(texts.MENU_MY)
    await chat.press(texts.MOVE)
    await chat.pick_day("Завтра")
    await chat.data(f"s:{tomorrow.id}")
    await chat.demo_next()  # 24 hours before
    assert chat.replies[0].media == REMINDER
    await chat.demo_next()  # 2 hours before
    reminder = chat.replies[0]
    assert reminder.media == ENTRANCE
    assert chat.labels()[0] == texts.HOW_TO_GET and texts.MORE in chat.labels()
    assert "бесплатный подземный паркинг" in reminder.text  # the short parking phrase
    await chat.press(texts.HOW_TO_GET)
    assert chat.text.startswith("ул. Шейнкмана, 113/2, вход через детское отделение.")
    assert "Парковка для посетителей клиники бесплатная" in chat.text  # the full text
    await chat.demo_next()
    assert chat.replies[0].media == FEEDBACK and chat.text == texts.AFTER_VISIT


async def test_way_sends_the_map_point_when_configured(make_chat, clinic) -> None:
    from app.config import Location

    chat = make_chat()
    await book_dislocation(chat)
    booking = await _booking(chat)
    center = clinic.contacts.trauma_center.model_copy(
        update={"location": Location(latitude=56.1, longitude=60.1)}
    )
    contacts = clinic.contacts.model_copy(update={"trauma_center": center})
    chat.conversation.clinic = clinic.model_copy(update={"contacts": contacts})
    await chat.data(f"way:{booking.id}")
    assert chat.replies[0].venue is not None


async def test_reactions_on_confirm_and_on_five(make_chat) -> None:
    chat = make_chat()
    await book_dislocation(chat)
    booking = await _booking(chat)
    await chat.press(texts.PAYMENT_SELF)
    await chat.data(f"r:ok:{booking.id}")
    assert chat.turn.reaction == "👍"
    await chat.data(f"rate:4:{booking.id}")
    assert chat.turn.reaction is None
    await chat.data(f"rate:5:{booking.id}")
    assert chat.turn.reaction == "❤"


async def test_long_question_in_short_with_more(make_chat, kb: KnowledgeBase) -> None:
    chat = make_chat()
    await chat.start()
    await chat.press(texts.MENU_BOOK)
    await chat.press(texts.TOPIC_LIST)
    await chat.press("Рана, ожог, укус")
    await chat.press("Порез, рана")
    for label in ("Для себя", "Руки, ноги, тело", "Да, двигается", "Менее суток назад"):
        await chat.press(label)
    assert "В травмпункте делают только хирургический шов, не косметический." in chat.text
    assert texts.MORE in chat.labels()
    await chat.press(texts.MORE)
    full = kb.scenario("cut_sutures").question("suture_type").ask
    assert chat.text == full and not chat.replies[0].edit  # the screen stays


def test_short_phrases_are_two_lines_and_safety_phrases_stay_whole(kb: KnowledgeBase) -> None:
    shorts = kb.shorts.phrases
    assert len(shorts) >= 15
    assert all(len(p.short) <= 90 for p in shorts)
    fulls = " ".join(p.full for p in shorts)
    assert "вызывайте скорую" not in fulls and "нарушение дыхания" not in fulls


def test_icons_on_the_main_menu_and_topics(kb: KnowledgeBase) -> None:
    from app.bot.conversation import _menu

    labels = [b.label for row in _menu() for b in row]
    assert labels[0] == f"📝 {texts.MENU_BOOK}" and all(" " in label for label in labels)
    for menu in kb.topics.menus.values():
        for item in menu.items:
            assert item.icon and item.caption.startswith(item.icon), item.id
            assert width(item.caption) <= 40, item.caption
    assert width("🦴 Рука или нога") == 16


# --- Telegram: media store and calls ------------------------------------------------------


@dataclass
class _Sent:
    message_id: int
    animation: Any = None
    photo: list = field(default_factory=list)


class _Bot:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def send_animation(self, chat_id: int, animation: Any, caption: Any = None, **kw: Any):
        self.calls.append(("animation", type(animation).__name__, caption))
        return _Sent(10, animation=type("A", (), {"file_id": "ANIM_ID"})())

    async def send_photo(self, chat_id: int, photo: Any, caption: Any = None, **kw: Any):
        self.calls.append(("photo", type(photo).__name__, caption))
        return _Sent(11, photo=[type("P", (), {"file_id": "PHOTO_ID"})()])

    async def send_message(self, chat_id: int, text: str, **kw: Any):
        self.calls.append(("message", text))
        return _Sent(12)

    async def pin_chat_message(self, **kw: Any) -> None:
        self.calls.append(("pin", kw["message_id"], kw["disable_notification"]))

    async def unpin_chat_message(self, **kw: Any) -> None:
        self.calls.append(("unpin", kw["message_id"]))

    async def set_message_reaction(self, **kw: Any) -> None:
        self.calls.append(("reaction", kw["reaction"][0].emoji))


class _Conv:
    def __init__(self) -> None:
        self.pinned: list = []

    async def register_pinned_card(self, booking_id: int, message_id: int) -> None:
        self.pinned.append((booking_id, message_id))


def _store(tmp_path: Path, *, files: tuple[str, ...] = (), enabled: bool = True) -> MediaStore:
    assets = tmp_path / "assets"
    assets.mkdir(exist_ok=True)
    for name in files:
        (assets / name).write_bytes(b"x")
    return MediaStore(assets, tmp_path / "media_ids.json", enabled=enabled)


async def test_media_is_uploaded_once_then_sent_by_file_id(tmp_path: Path) -> None:
    bot = _Bot()
    delivery = Delivery(bot, _Conv(), None, media=_store(tmp_path, files=("welcome.mp4",)))  # type: ignore[arg-type]
    hello = Reply("Добрый день!", ((Button("Записаться", "m:book"),),), media=WELCOME)
    await delivery(Turn([hello]), 1)
    await delivery(Turn([hello]), 1)
    assert bot.calls == [
        ("animation", "FSInputFile", "Добрый день!"),
        ("animation", "str", "Добрый день!"),  # the remembered file_id
    ]
    again = MediaStore(tmp_path / "assets", tmp_path / "media_ids.json")  # after a restart
    assert again.file("welcome.mp4") == "ANIM_ID"


async def test_missing_file_or_disabled_media_send_the_text(tmp_path: Path) -> None:
    bot = _Bot()
    delivery = Delivery(bot, _Conv(), None, media=_store(tmp_path))  # type: ignore[arg-type]
    await delivery(Turn([Reply("Добрый день!", media=WELCOME)]), 1)
    await delivery(Turn([Reply("", media=BOOKED, media_only=True)]), 1)  # nothing at all
    off = Delivery(
        bot, _Conv(), None, media=_store(tmp_path, files=("feedback.png",), enabled=False)
    )  # type: ignore[arg-type]
    await off(Turn([Reply(texts.AFTER_VISIT, media=FEEDBACK)]), 1)
    assert bot.calls == [("message", "Добрый день!"), ("message", texts.AFTER_VISIT)]


async def test_long_caption_goes_as_text_after_the_media(tmp_path: Path) -> None:
    bot = _Bot()
    delivery = Delivery(bot, _Conv(), None, media=_store(tmp_path, files=("entrance.jpg",)))  # type: ignore[arg-type]
    await delivery(Turn([Reply("я" * 1500, media=ENTRANCE)]), 1)
    assert bot.calls == [("photo", "FSInputFile", None), ("message", "я" * 1500)]


async def test_pin_unpin_and_reaction(tmp_path: Path) -> None:
    from tests.test_wizard import _Pressed

    bot, conv = _Bot(), _Conv()
    delivery = Delivery(bot, conv, None, media=_store(tmp_path))  # type: ignore[arg-type]
    await delivery(Turn([Reply("Карточка", pin=5)]), 1)
    assert bot.calls == [("message", "Карточка"), ("pin", 12, True)] and conv.pinned == [(5, 12)]
    bot.calls.clear()
    pressed = _Pressed("Напоминаем…")
    await delivery(
        Turn([Reply("Спасибо, ждём Вас!")], reaction="👍", unpin=[12]), 1, pressed=pressed
    )
    assert ("unpin", 12) in bot.calls and ("reaction", "👍") in bot.calls
