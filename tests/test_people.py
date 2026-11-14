"""Several patients in one message (app/flow/people.py): who, how old, what happened to each."""

from __future__ import annotations

import pytest

from app.flow.people import Person, find_people, merge

KIDS = (
    "Здравствуйте, дети сегодня с горки упали, Маша 7 лет руку ушибла, Ваня 10 лет ногу "
    "подвернул. Надо обоих записать к врачу"
)


def told(text: str) -> list[tuple[str, str | None, int | None, str]]:
    return [(p.name, p.audience, p.age_months, p.complaint) for p in find_people(text)]


@pytest.mark.parametrize(
    ("said", "people"),
    [
        (KIDS, [("Маша", "child", 84, "руку ушибла"), ("Ваня", "child", 120, "ногу подвернул")]),
        ("У Маши ушиб руки, у Вани нога подвернулась, когда падали. Переломов вроде нет",
         [("Маша", None, None, "ушиб руки"), ("Ваня", None, None, "нога подвернулась")]),
        ("Маше 7 лет, она первая. Ване 10 лет, его потом запишем",
         [("Маша", "child", 84, ""), ("Ваня", "child", 120, "")]),
        ("У меня запястье — похоже на перелом. У сына колено — ушиб.",
         [("", "self", None, "запястье, похоже на перелом"),
          ("сын", "child", None, "колено, ушиб")]),
        ("Упали с сыном на катке. Мне надо записать обоих: у меня запястье болит, похоже "
         "перелом, у сына колено ушиб.",
         [("сын", "child", None, "колено ушиб"),
          ("", "self", None, "запястье болит, похоже перелом")]),
        ("Сначала для себя, потом сына надо будет записать. Мне 44, запястье правое болит, на "
         "катке упал.",
         [("", "self", None, "запястье правое болит, на катке упал"), ("сын", "child", None, "")]),
        ("Сыну 12 лет, Егор. Сначала меня запишите, потом его.",
         [("сын", "child", 144, ""), ("", "self", None, "")]),
        ("Для себя и для мужа. Сначала можно меня записать, потом его.",
         [("", "self", None, ""), ("муж", "adult", None, "")]),
        ("Мы с мужем в походе подвернули ногу — и я, и он.",
         [("", "self", None, "в походе подвернули ногу"),
          ("муж", "adult", None, "в походе подвернули ногу")]),
        ("меня и сына", [("", "self", None, ""), ("сын", "child", None, "")]),
        ("запишите меня с ребёнком", [("", "self", None, ""), ("ребёнок", "child", None, "")]),
    ],
)  # fmt: skip
def test_the_patients_a_message_names(said: str, people: list) -> None:
    assert told(said) == people


@pytest.mark.parametrize(
    "said",
    [
        "запишите к Андрею Александровичу на завтра",  # a doctor: no age, no injury
        "двоих детей надо записать",  # nobody is named
        "болит рука, упал вчера",
        "Громова Нина Васильевна",
    ],
)
def test_nobody_is_named(said: str) -> None:
    assert find_people(said) == []


@pytest.mark.parametrize(
    "said",
    [
        "запишите сына",
        "меня укусила собака",
        "гуляла с сыном, он упал",
        "сыну 7 лет",
        "у меня сын упал с велосипеда",
    ],
)
def test_one_patient_is_one(said: str) -> None:
    assert len(find_people(said)) == 1


@pytest.mark.parametrize(
    ("name", "whom"),
    [("Ваня", "Ваню"), ("Маша", "Машу"), ("Егор", "Егора"), ("Андрей", "Андрея"),
     ("Игорь", "Игоря"), ("Любовь", "Любовь"), ("Пётр", "Петра"), ("сын", "сына"),
     ("дочь", "дочь"), ("муж", "мужа"), ("мама", "маму"), ("", "меня")],
)  # fmt: skip
def test_whom_in_the_accusative(name: str, whom: str) -> None:
    assert Person(name=name).whom == whom


def test_a_later_message_adds_the_age_and_what_happened() -> None:
    waiting = [Person(name="сын", audience="child")]
    merge(waiting, find_people("Сыну 12 лет, Егор"))
    assert (waiting[0].age_months, waiting[0].complaint) == (144, "")
    merge(waiting, find_people("у сына колено ушиб, а Пете 5 лет"))  # a new name is not added
    assert [(p.name, p.complaint) for p in waiting] == [("сын", "колено ушиб")]
