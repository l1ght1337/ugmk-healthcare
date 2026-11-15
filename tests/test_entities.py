"""The spans of a reply as Telegram entities: offsets in UTF-16 units, the plain fallback.

An emoji is two units of UTF-16: the bold after «📝 » starts at 3, not at 2. The entities
are built by aiogram.utils.formatting; here the piece cut out by offset and length is
checked against what must be bold, italic, a quote or a link — for every template with an
emoji and for a line with a patient's name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from app.bot import markup, texts
from app.bot.conversation import WELCOME, Button, Reply, Turn
from app.bot.telegram import Delivery, entities
from tests.test_media import _Conv, _store


def reply(marked: str, **kw: Any) -> Reply:
    text, spans = markup.render(marked, quoted=texts.QUOTED)
    return Reply(text, spans=spans, **kw)


def cut(text: str, entity: Any) -> str:
    """The piece of ``text`` an entity covers, by its offset and length in UTF-16 units."""
    units = text.encode("utf-16-le")
    return units[entity.offset * 2 : (entity.offset + entity.length) * 2].decode("utf-16-le")


def pieces(r: Reply) -> list[tuple[str, str]]:
    return [(e.type, cut(r.text, e)) for e in entities(r) or []]


GREETING = (
    "**Добрый день!** Травмпункт «УГМК-Здоровье», я виртуальный помощник.\n\n"
    "📝 **Запишу в травмпункт за минуту** — 5 коротких шагов.\n"
    "💬 **Отвечу на вопросы о травмпункте**: адрес, цены, парковка, документы.\n\n"
    "Напишите своими словами, например _подвернул ногу_ или _запишите к Ярунину на завтра_, — "
    "или нажмите кнопку.\n\n"
    f"> {texts.BOT_DEMO_NOTE}"
)


def test_the_greeting_with_emoji_is_cut_right() -> None:
    assert pieces(reply(GREETING)) == [
        ("bold", "Добрый день!"),
        ("bold", "Запишу в травмпункт за минуту"),
        ("bold", "Отвечу на вопросы о травмпункте"),
        ("italic", "подвернул ногу"),
        ("italic", "запишите к Ярунину на завтра"),
        ("blockquote", texts.BOT_DEMO_NOTE),
    ]
    r = reply(GREETING)
    plain = r.text
    (after_emoji,) = [e for e in entities(r) if cut(plain, e).startswith("Запишу")]
    assert after_emoji.offset == len(plain[: plain.index("Запишу")].encode("utf-16-le")) // 2
    assert after_emoji.offset == plain.index("Запишу") + 1  # «📝» is two units


@pytest.mark.parametrize(
    "name",
    ["Иванов Иван Иванович", "Иванов_Иван **Иванович** 😀", "[Петров](http://x.ru) Пётр 🦴🦴",
     "> Сидоров _Сидор_"],
)  # fmt: skip
def test_a_line_with_a_patients_name(name: str) -> None:
    safe = markup.esc(name)
    r = reply(f"📝 Запись для ребёнка: {safe}, 5 лет\n\n**Пациент: {safe} — верно?**")
    assert r.text == f"📝 Запись для ребёнка: {name}, 5 лет\n\nПациент: {name} — верно?"
    assert pieces(r) == [("bold", f"Пациент: {name} — верно?")]  # the name is bold, not a mark


# what the patient reads: not the bare mark and not the lines for the administrators
AMBULANCE_TEMPLATES = [
    name
    for name, value in vars(texts).items()
    if isinstance(value, str) and "⚠️" in value and name.isupper()
    if not name.startswith("MARK_") and "ADMIN_MARK" not in name and name != "CRISIS_ADMIN"
]


@pytest.mark.parametrize("name", AMBULANCE_TEMPLATES)
def test_every_template_with_the_ambulance_sign(name: str) -> None:
    value = getattr(texts, name)
    r = reply(value.format(start="08:00", phone="8-800-2000-122") if "{" in value else value)
    lines = [line.removeprefix("⚠️ ") for line in r.text.split("\n") if line.startswith("⚠️")]
    assert lines and [piece for kind, piece in pieces(r) if kind == "bold"] == lines
    for line in r.text.split("\n"):  # one line — one sentence about 103 or 112, behind the sign
        calls = any(n in line.replace(".", " ").split() for n in ("103", "112"))
        assert line.startswith("⚠️") == calls, line


def test_the_templates_with_the_sign_are_found() -> None:
    assert {"DANGER_FIRST", "DANGER_NO_BOOKING", "MEDICAL", "MEDICAL_ASIDE", "CRISIS"} <= set(
        AMBULANCE_TEMPLATES
    )


def test_a_link_and_an_address_mark() -> None:
    url = "https://yandex.ru/maps/?text=%D0%95%D0%BA%D0%B0%D1%82"
    r = reply(
        f"**Обратиться нужно:**\n📍 Центр борьбы со СПИДом, {markup.link('ул. Ясная, 46', url)}"
    )
    found = entities(r)
    assert [(e.type, cut(r.text, e)) for e in found] == [
        ("bold", "Обратиться нужно:"),
        ("text_link", "ул. Ясная, 46"),
    ]
    assert found[1].url == url


def test_a_reply_without_marks_has_no_entities() -> None:
    assert entities(Reply("Спасибо!")) is None


# --- the delivery: with the entities, and plain when Telegram refuses them ---------------------


@dataclass
class _Sent:
    message_id: int = 1
    animation: Any = None
    photo: list = field(default_factory=list)


class _Bot:
    def __init__(self, refuse_entities: bool = False) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.refuse = refuse_entities

    async def send_message(self, chat_id: int, text: str, **kw: Any) -> _Sent:
        self.calls.append(("message", text, kw.get("entities")))
        if self.refuse and kw.get("entities"):
            raise TelegramBadRequest(
                SendMessage(chat_id=chat_id, text=text), "can't parse entities"
            )
        return _Sent()

    async def send_animation(self, chat_id: int, animation: Any, caption: Any = None, **kw: Any):
        self.calls.append(("animation", caption, kw.get("caption_entities")))
        return _Sent(animation=type("A", (), {"file_id": "ANIM"})())


class _Pressed:
    """A message with buttons that was pressed: what edit_text got."""

    def __init__(self, text: str, found: list | None = None) -> None:
        self.text, self.entities = text, found
        self.chat = type("C", (), {"id": 1})()
        self.message_id = 7
        self.edits: list[tuple[str, Any]] = []

    async def edit_text(self, text: str, **kw: Any) -> None:
        self.edits.append((text, kw.get("entities")))


async def test_a_message_goes_with_its_entities() -> None:
    bot = _Bot()
    r = reply(GREETING, buttons=((Button("Записаться", "m:book"),),))
    await Delivery(bot, _Conv(), None)(Turn([r]), 1)  # type: ignore[arg-type]
    ((kind, text, found),) = bot.calls
    assert (kind, text) == ("message", r.text) and [e.type for e in found][:2] == ["bold", "bold"]


async def test_refused_entities_send_the_same_text_plain() -> None:
    bot = _Bot(refuse_entities=True)
    r = reply(GREETING)
    await Delivery(bot, _Conv(), None)(Turn([r]), 1)  # type: ignore[arg-type]
    assert [(text, bool(found)) for _, text, found in bot.calls] == [
        (r.text, True),
        (r.text, False),
    ]


async def test_the_next_screen_replaces_the_pressed_one_with_entities() -> None:
    pressed = _Pressed("Шаг 2 из 5")
    screen = reply("Шаг 3 из 5 · Уточняющие вопросы\n\n**Как получена травма?**", edit=True)
    await Delivery(_Bot(), _Conv(), None)(Turn([screen]), 1, pressed=pressed)  # type: ignore[arg-type]
    ((text, found),) = pressed.edits
    assert text == screen.text and [(e.type, cut(text, e)) for e in found] == [
        ("bold", "Как получена травма?")
    ]


async def test_what_was_chosen_keeps_the_entities_of_the_message() -> None:
    screen = reply("Шаг 3 из 5 · Уточняющие вопросы\n\n**Как получена травма?**")
    pressed = _Pressed(screen.text, entities(screen))
    await Delivery(_Bot(), _Conv(), None)(
        Turn([Reply("Спасибо!")]), 1, pressed=pressed, label="Нет"
    )  # type: ignore[arg-type]
    ((text, found),) = pressed.edits
    assert text == f"{screen.text}\n\n— Нет" and found == entities(screen)


async def test_the_caption_of_the_animation_has_the_entities(tmp_path) -> None:
    bot = _Bot()
    media = _store(tmp_path, files=("welcome.mp4",))
    r = reply(GREETING, media=WELCOME)
    await Delivery(bot, _Conv(), None, media=media)(Turn([r]), 1)  # type: ignore[arg-type]
    ((kind, caption, found),) = bot.calls
    assert (kind, caption) == ("animation", r.text)
    assert (found[0].type, cut(caption, found[0])) == ("bold", "Добрый день!")


async def test_a_text_too_long_for_a_caption_follows_the_animation(tmp_path) -> None:
    bot = _Bot()
    media = _store(tmp_path, files=("welcome.mp4",))
    long = reply(GREETING + "\n\n" + "очень длинный текст " * 60, media=WELCOME)
    await Delivery(bot, _Conv(), None, media=media)(Turn([long]), 1)  # type: ignore[arg-type]
    first, second = bot.calls
    assert first[:2] == ("animation", None)  # the animation alone, then the whole text
    assert second[:2] == ("message", long.text) and second[2]
