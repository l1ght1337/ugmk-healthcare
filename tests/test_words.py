"""Child age stated in a complaint, Russian number agreement, identification parsing."""

from __future__ import annotations

from datetime import date

import pytest

from app.flow.age import find_child_age
from app.flow.identity import age_in_months, parse_birth_date, parse_full_name, parse_phone
from app.flow.words import format_age, plural, relative_day

TODAY = date(2026, 11, 12)


@pytest.mark.parametrize(
    ("text", "months"),
    [
        ("у сына 8 лет вывих", 96),
        ("Сыну восемь лет, упал с качели", 96),
        ("дочке 4 месяца, упала с кровати", 4),
        ("8-летний сын сломал руку", 96),
        ("4-месячный малыш упал", 4),
        ("ребёнок (3 года) обжёгся", 36),
        ("Мальчик 12 лет подвернул ногу на тренировке", 144),
        ("внучке 2 года 3 месяца", 27),
        ("ребенку 1,5 года", 18),
        # no child age stated
        ("мне 30 лет, вывих", None),
        ("сын упал 3 дня назад", None),
        ("у ребенка вывих", None),
        ("сыну 25 лет", None),  # an adult
    ],
)
def test_find_child_age(text: str, months: int | None) -> None:
    assert find_child_age(text) == months


@pytest.mark.parametrize(
    ("n", "word"), [(1, "день"), (2, "дня"), (5, "дней"), (11, "дней"), (21, "день"), (22, "дня")]
)
def test_plural(n: int, word: str) -> None:
    assert plural(n, "день", "дня", "дней") == word


@pytest.mark.parametrize(
    ("months", "text"),
    [
        (96, "8 лет"),
        (12, "1 год"),
        (24, "2 года"),
        (4, "4 месяца"),
        (18, "1 год 6 месяцев"),
        (0, "до 1 месяца"),
    ],
)
def test_format_age(months: int, text: str) -> None:
    assert format_age(months) == text


@pytest.mark.parametrize(
    ("delta", "text"),
    [
        (0, "сегодня"),
        (1, "вчера"),
        (-1, "завтра"),
        (2, "2 дня назад"),
        (-3, "через 3 дня"),
        (90, "3 месяца назад"),
        (800, "2 года назад"),
    ],
)
def test_relative_day(delta: int, text: str) -> None:
    assert relative_day(delta) == text


def test_identification_parsing() -> None:
    assert parse_full_name("иванов иван иванович") == ("Иванов", "Иван", "Иванович")
    assert parse_full_name("Петрова-Водкина Анна") == ("Петрова-Водкина", "Анна", None)
    assert parse_full_name("Иван") is None
    assert parse_birth_date("12.03.2015", TODAY) == date(2015, 3, 12)
    assert parse_birth_date("12 марта 2015", TODAY) == date(2015, 3, 12)
    assert parse_birth_date("31.02.2015", TODAY) is None
    assert parse_birth_date("12.03.2030", TODAY) is None
    assert parse_phone("8 (912) 345-67-89") == "+79123456789"
    assert parse_phone("12345") is None
    assert age_in_months(date(2018, 11, 13), TODAY) == 95
