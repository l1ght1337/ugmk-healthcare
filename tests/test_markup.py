"""The markup of the bot's messages: bold, italic, a quote and a link over plain text.

The bot's templates use `**жирный**`, `_курсив_`, `> цитата` and `[текст](ссылка)`; the
text a patient reads (``Reply.text``) has none of it, the markup goes beside it as spans and
becomes Telegram entities. What came from the patient is escaped and stays as it was typed.
"""

from __future__ import annotations

import pytest

from app.bot import markup
from app.bot.markup import Span, ambulance, esc, parse, plain, render


def cut(text: str, span: Span) -> str:
    return text[span.start : span.end]


def kinds(marked: str) -> list[tuple[str, str]]:
    text, spans = parse(marked)
    return [(s.kind, cut(text, s)) for s in spans]


def test_bold_italic_quote_and_link() -> None:
    marked = (
        "**Добрый день!** Травмпункт «УГМК-Здоровье».\n\n"
        "📝 **Запишу в травмпункт за минуту** — 5 коротких шагов.\n"
        "Напишите, например _подвернул ногу_ или _запишите к Ярунину на завтра_, — или кнопку.\n\n"
        "> Тестовый режим: записи демонстрационные.\n"
        "📍 Центр борьбы со СПИДом, [ул. Ясная, 46](https://yandex.ru/maps/?text=x)"
    )
    text, spans = parse(marked)
    assert [(s.kind, cut(text, s)) for s in spans] == [
        ("bold", "Добрый день!"),
        ("bold", "Запишу в травмпункт за минуту"),
        ("italic", "подвернул ногу"),
        ("italic", "запишите к Ярунину на завтра"),
        ("quote", "Тестовый режим: записи демонстрационные."),
        ("link", "ул. Ясная, 46"),
    ]
    assert spans[-1].url == "https://yandex.ru/maps/?text=x"
    for marker in ("**", "_", "> ", "](", "["):
        assert marker not in text, marker
    assert text == plain(marked)


def test_quote_lines_in_a_row_are_one_quote() -> None:
    text, spans = parse("Вопрос?\n> первая строка\n> вторая строка\nдальше")
    assert [(s.kind, cut(text, s)) for s in spans] == [("quote", "первая строка\nвторая строка")]


@pytest.mark.parametrize(
    "name",
    ["Иванов_Иван **Иванович**", "[Петров](http://x.ru) Пётр", "> Сидоров", "a\\b _c_ * d",
     "Анна-Мария _Луиза_"],
)  # fmt: skip
def test_what_came_from_the_patient_is_escaped(name: str) -> None:
    text, spans = parse(f"**Пациент:** {esc(name)} — верно?\n{esc(name)}")
    assert text == f"Пациент: {name} — верно?\n{name}"
    assert [(s.kind, cut(text, s)) for s in spans] == [("bold", "Пациент:")]


@pytest.mark.parametrize(
    "marked",
    ["цена 2*2 **и всё", "file_name_x и snake_case", "не_курсив", "[не ссылка] (и не это)",
     "5 > 3", "один _ подчёрк", "**"],
)  # fmt: skip
def test_markers_that_do_not_close_stay_as_text(marked: str) -> None:
    assert parse(marked) == (marked, ())


def test_bold_may_hold_a_link_and_italic() -> None:
    text, spans = parse("**Обратитесь: [ул. Ясная, 46](https://y.ru) и _сразу_**")
    assert text == "Обратитесь: ул. Ясная, 46 и сразу"
    assert {(s.kind, cut(text, s)) for s in spans} == {
        ("bold", text), ("link", "ул. Ясная, 46"), ("italic", "сразу")
    }  # fmt: skip


# --- the lines about 103 and 112 -----------------------------------------------------------

ASIDE = "Я не могу давать медицинские рекомендации — это решит врач на приёме."
CALL = "При угрозе жизни звоните 103 или 112."


def test_a_sentence_about_the_ambulance_is_a_line_of_its_own() -> None:
    assert ambulance(f"{ASIDE} {CALL}") == f"{ASIDE}\n⚠️ {CALL}"
    assert ambulance(CALL) == f"⚠️ {CALL}"
    three = "Я бот и не записываю. Номера скорой: 103 или 112. Могу передать сообщение."
    assert ambulance(three) == (
        "Я бот и не записываю.\n⚠️ Номера скорой: 103 или 112.\nМогу передать сообщение."
    )
    first = "Сколько ждать скорую, скажет диспетчер 103. Запись я не оформляю."
    assert (
        ambulance(first) == "⚠️ Сколько ждать скорую, скажет диспетчер 103.\nЗапись я не оформляю."
    )


@pytest.mark.parametrize(
    "text",
    [f"{ASIDE}\n⚠️ {CALL}", "📍 Больница, ул. Ленина, 112", "Приём стоит 2000 рублей.",
     "Шаг 1 из 5\n\nЧто случилось?"],
)  # fmt: skip
def test_the_ambulance_rule_changes_nothing_else_and_only_once(text: str) -> None:
    assert ambulance(text) == text and ambulance(ambulance(text)) == ambulance(text)


def test_an_ambulance_line_is_bold_a_note_is_a_quote_an_example_is_italic() -> None:
    note = "Тестовый режим: записи демонстрационные."
    text, spans = render(
        f"{ASIDE} {CALL}\n\nМожно написать, например «завтра вечером», либо «назад».\n\n{note}",
        quoted=(note,),
    )
    assert text.splitlines()[1] == f"⚠️ {CALL}"
    assert [(s.kind, cut(text, s)) for s in spans] == [
        ("bold", CALL),
        ("italic", "завтра вечером"),
        ("italic", "назад"),
        ("quote", note),
    ]


def test_render_is_plain_text_when_there_is_nothing_to_mark() -> None:
    assert render("Спасибо! Могу я ещё чем-нибудь Вам помочь?") == (
        "Спасибо! Могу я ещё чем-нибудь Вам помочь?", ()
    )  # fmt: skip


def test_the_helpers_wrap_and_escape() -> None:
    assert markup.bold("Вопрос?") == "**Вопрос?**"
    assert parse(markup.bold(esc("a**b")))[0] == "a**b"
    assert kinds(markup.link("ул. Ясная, 46", "https://y.ru/?text=a%20b")) == [
        ("link", "ул. Ясная, 46")
    ]
    assert kinds(markup.quote("первая\nвторая")) == [("quote", "первая\nвторая")]
