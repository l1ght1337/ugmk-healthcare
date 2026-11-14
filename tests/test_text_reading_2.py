"""Texts read by code, after the customer's check of 453f8ef (14.11): items 1, 2, 4, 5, 7.

No LLM and no Telegram here. The clinic clock of the tests is Thursday, 12 November 2026.
"""

from __future__ import annotations

from datetime import date, time

import pytest

from app.config import ClinicData
from app.flow.commands import asks_advice, find_audience, find_navigation
from app.flow.wishes import parse

TODAY = date(2026, 11, 12)  # Thursday
YARUNIN = "Ярунин Андрей Александрович"


@pytest.fixture(scope="module")
def doctors(clinic: ClinicData) -> tuple[str, ...]:
    return clinic.schedule.doctors


# --- 4. an hour with no «утра / вечера» on another day ---------------------------------------


@pytest.mark.parametrize(
    ("text", "at"),
    [
        ("завтра в 3", time(15)),  # people say so about the afternoon
        ("на завтра в 5", time(17)),
        ("в субботу в 2", time(14)),
        ("завтра в 1", time(13)),
        ("завтра в 6", time(18)),
        ("завтра в 3:30", time(15, 30)),
        ("завтра в 7", time(7)),  # 7–11: as it is said
        ("завтра в 11", time(11)),
        ("завтра в 3 ночи", time(3)),  # said which one
        ("завтра в 5 утра", time(5)),
        ("завтра в 3 часа дня", time(15)),
        ("завтра в 15", time(15)),
        ("в пятницу утром в 6", time(6)),  # «утром» decides
    ],
)
def test_bare_hour_on_another_day(doctors, text: str, at: time) -> None:
    for now in (None, time(4), time(10), time(22)):  # the clock of today does not matter
        assert parse(text, doctors, TODAY, now=now).wish.at == at, (text, now)


def test_bare_window_on_another_day(doctors) -> None:
    assert parse("завтра после 6", doctors, TODAY, now=time(10)).wish.start == time(18)
    assert parse("завтра до 5", doctors, TODAY, now=time(10)).wish.end == time(17)
    wish = parse("в субботу с 2 до 5", doctors, TODAY, now=time(10)).wish
    assert (wish.start, wish.end) == (time(14), time(17))
    wish = parse("завтра с 9 до 11", doctors, TODAY, now=time(10)).wish
    assert (wish.start, wish.end) == (time(9), time(11))
    assert parse("завтра после 7", doctors, TODAY).wish.start == time(7)


def test_today_keeps_the_nearest_hour_ahead(doctors) -> None:
    assert parse("можно сегодня в 3?", doctors, TODAY, now=time(1, 30)).wish.at == time(3)
    assert parse("можно сегодня в 3?", doctors, TODAY, now=time(10)).wish.at == time(15)
    assert parse("можно в 3?", doctors, TODAY, now=time(1, 30)).wish.at == time(3)


# --- 5. «только не на завтра» ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "day", "period"),
    [
        ("Запишите к Ярунину, только не на завтра", None, None),
        ("запишите к Ярунину, только не завтра", None, None),
        ("к Ярунину, кроме субботы", None, None),
        ("запишите к Ярунину, но не в субботу", None, None),
        ("запишите к Ярунину, только не утром", None, None),
        ("к Ярунину, только не вечером и не ночью", None, None),
        ("к Ярунину не в субботу, а в воскресенье", date(2026, 11, 15), None),
        ("к Ярунину не завтра, а послезавтра вечером", date(2026, 11, 14), "evening"),
        ("к Ярунину, завтра не могу, давайте в пятницу", date(2026, 11, 13), None),
        ("к Ярунину, в субботу не получится, лучше в воскресенье", date(2026, 11, 15), None),
        ("к Ярунину на завтра, только не утром", date(2026, 11, 13), None),
    ],
)
def test_a_refused_day_or_time_is_no_wish(doctors, text: str, day, period) -> None:
    wish = parse(text, doctors, TODAY).wish
    assert wish.doctor == YARUNIN, text
    assert (wish.day, wish.period) == (day, period), text
    assert not (wish.at or wish.start or wish.end), text


def test_a_refused_time_is_no_wish(doctors) -> None:
    wish = parse("запишите на завтра, только не в 8", doctors, TODAY).wish
    assert wish.day == date(2026, 11, 13) and not wish.has_time
    assert parse("только не завтра", doctors, TODAY).wish.empty


# --- «не раньше 5», «не позже 12» are bounds, not refusals (14.11) ----------------------------


@pytest.mark.parametrize(
    ("text", "now", "start", "end"),
    [
        ("можно не раньше 5?", time(10), time(17), None),  # as «после 5»: the nearest ahead
        ("можно не раньше 5?", time(4), time(5), None),
        ("завтра не раньше 5", time(10), time(17), None),  # another day: 1–6 is the afternoon
        ("завтра не раньше 7", time(10), time(7), None),
        ("не ранее 9 утра", time(10), time(9), None),
        ("не раньше чем в 10", time(8), time(10), None),
        ("только не раньше 18", time(8), time(18), None),
        ("не позже 12 завтра", time(10), None, time(12)),
        ("завтра не позже 3", time(10), None, time(15)),
        ("не позднее 18:30", time(10), None, time(18, 30)),
        ("запишите к Ярунину на завтра не позже чем в 11", time(10), None, time(11)),
    ],
)
def test_bounds_with_not(doctors, text: str, now: time, start, end) -> None:
    wish = parse(text, doctors, TODAY, now=now).wish
    assert (wish.start, wish.end) == (start, end), text
    assert wish.at is None and wish.period is None


def test_bounds_with_not_keep_the_day(doctors) -> None:
    wish = parse("не позже 12 завтра", doctors, TODAY).wish
    assert (wish.day, wish.end) == (date(2026, 11, 13), time(12))
    wish = parse("в субботу не раньше 10 и не позже 14", doctors, TODAY).wish
    assert (wish.day, wish.start, wish.end) == (date(2026, 11, 14), time(10), time(14))
    assert parse("не раньше 5 дней назад упал", doctors, TODAY).wish.empty  # a number of days


# --- two bounds in one message are one window (14.11) --------------------------------------------


@pytest.mark.parametrize(
    ("text", "now", "start", "end"),
    [
        ("не раньше 10 и не позже 14", time(10), time(10), time(14)),  # was «с 22:00»
        ("не раньше 10 и не позже 14", time(22), time(10), time(14)),
        ("не раньше 10 и не позже 14", None, time(10), time(14)),
        ("не позже 14 и не раньше 10", time(10), time(10), time(14)),  # in any order
        ("завтра не раньше 10 и не позже 2", time(10), time(10), time(14)),  # the start is earlier
        ("не раньше 4 дня и не позже 6", time(10), time(16), time(18)),
        ("после 18 и до 20", time(10), time(18), time(20)),
        ("после 9 утра, но не позже 12", time(10), time(9), time(12)),
        # both without «утра / вечера»: the rule of «с 3 до 6»
        ("завтра после 3 и до 6", time(10), time(15), time(18)),
        ("после 3 и до 6", time(10), time(15), time(18)),
        ("после 3 и до 6", time(2), time(3), time(6)),
        ("завтра не раньше 9 и не позже 11", time(10), time(9), time(11)),
    ],
)
def test_two_bounds_are_one_window(doctors, text: str, now, start: time, end: time) -> None:
    wish = parse(text, doctors, TODAY, now=now).wish
    assert (wish.start, wish.end) == (start, end), (text, now)
    assert wish.at is None and wish.period is None


def test_two_bounds_read_as_from_to(doctors) -> None:
    for now in (time(4), time(10), time(16)):
        joined = parse("завтра не раньше 10 и не позже 14", doctors, TODAY, now=now).wish
        plain = parse("завтра с 10 до 14", doctors, TODAY, now=now).wish
        assert (joined.day, joined.start, joined.end) == (plain.day, plain.start, plain.end)


def test_bounds_that_make_no_window_keep_the_first(doctors) -> None:
    wish = parse("не раньше 18 и не позже 12", doctors, TODAY, now=time(10)).wish
    assert (wish.start, wish.end) == (time(18), None)


# --- 2. «меня» is a booking for oneself -------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "что делать укусила собака меня",
        "меня укусила собака",
        "меня вчера укусила собака",
        "я упал",
        "я вчера упала с лестницы",
        "у меня болит рука",
        "у меня перелом",
        "мне нужно снять гипс",
        "мне нужна перевязка",
        "мне больно наступать на ногу",
        "я сломал руку, сколько стоит приём?",
        "порезал себе палец",
        "запишите меня",  # «меня» is the patient here too (14.11)
        "запиши меня к Ярунину на завтра",
        "Здравствуйте, запишите меня, пожалуйста",
        "можно записать меня на завтра?",
    ],
)
def test_a_complaint_about_oneself(text: str) -> None:
    assert find_audience(text) == "self", text


@pytest.mark.parametrize(
    ("text", "who"),
    [
        ("меня и сына покусала собака", None),  # two patients: the bot asks
        ("я и дочка упали с горки", None),
        ("мы упали", None),
        ("нас покусала собака", None),
        ("я гуляла с сыном, упала", None),  # as before: it does not say who is hurt
        ("запишите меня с ребёнком", None),
        ("запишите меня и сына", None),
        ("запишите нас", None),
        ("упал вчера", None),
        ("запишите брата", None),
        ("сын упал, я не знаю что делать", "child"),
        ("ребёнка укусила собака, мне что делать?", "child"),
        ("у мамы перелом, я её привезу", "adult"),
        ("запишите ребёнка", "child"),
        ("дочку укусила собака", "child"),
    ],
)
def test_who_else_the_visit_is_for(text: str, who: str | None) -> None:
    assert find_audience(text) == who, text


# --- 7. the menu and «назад» asked for in words -----------------------------------------------

HOME = (
    "меню",
    "в меню",
    "главное меню",
    "открой меню",
    "покажи меню",
    "первоначальное меню открой",
    "начальное меню",
    "верни в меню",
    "верни меня в меню",
    "выйди в меню",
    "Открой, пожалуйста, меню",
    "давай заново",
    "хочу сначала",
    "начать заново",
    "заново",
)
BACK = ("назад", "вернись назад", "давай назад", "шаг назад", "верни назад", "можно назад?")


@pytest.mark.parametrize("text", HOME)
def test_menu_asked_in_words(text: str) -> None:
    assert find_navigation(text) == "home", text


@pytest.mark.parametrize("text", BACK)
def test_back_asked_in_words(text: str) -> None:
    assert find_navigation(text) == "back", text


@pytest.mark.parametrize(
    "text",
    [
        "в меню нет кнопки отмены записи, как отменить?",
        "что есть в меню вашего кафе?",
        "а меню ресторана рядом есть?",
        "назад упал на спину",
        "вернулся назад домой и упал",
        "заново наложили гипс",
        "сначала болела рука, потом нога",
    ],
)
def test_a_word_inside_a_sentence_is_not_navigation(text: str) -> None:
    assert find_navigation(text) is None, text


# --- 1. a medical question next to a complaint: the word list --------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "что делать укусила собака меня",
        "упал на руку, что мне теперь делать?",
        "порезал палец, чем обработать?",
        "упал на руку, она опухла — это перелом?",
        "подвернул ногу, это растяжение или перелом?",
        "обжёг руку, можно ли мазать кремом?",
        "наложили гипс, можно ли его мочить",
        "ударился коленом, нужно ли прикладывать лёд?",
        "укусила собака, надо ли делать прививку?",
        "сломал палец, какое обезболивающее выпить?",
        "подвернул ногу, как лечить?",
    ],
)
def test_asks_for_advice(text: str) -> None:
    assert asks_advice(text), text


@pytest.mark.parametrize(
    "text",
    [
        "сломал руку, можно ли записаться на сегодня?",
        "сломал руку, сколько стоит приём?",
        "порезал палец, нужно ли брать паспорт?",
        "упал, можно ли к вам без записи?",
        "подвернул ногу, запишите на завтра",
        "что взять с собой?",
        "меня укусила собака",
    ],
)
def test_not_a_request_for_advice(text: str) -> None:
    assert not asks_advice(text), text
