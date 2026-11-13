"""Booking wishes read by code: the doctor, the day and the time of day (task 1).

No LLM and no Telegram here: plain text in, a ``Wish`` out. The clinic clock of the tests
is Thursday, 12 November 2026.
"""

from __future__ import annotations

from datetime import date, time

import pytest

from app.config import ClinicData
from app.flow.wishes import Wish, dative, mark_doctors, parse, short_name

TODAY = date(2026, 11, 12)  # Thursday
KUZNETSOV = "Кузнецов Николай Александрович"
YARUNIN = "Ярунин Андрей Александрович"
BILALOV = "Билалов Руслан Фярисович"
DEMENKOV = "Деменков Денис Сергеевич"


@pytest.fixture(scope="module")
def doctors(clinic: ClinicData) -> tuple[str, ...]:
    return clinic.schedule.doctors


def day(n: int, month: int = 11) -> date:
    return date(2026, month, n)


# --- the doctor ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "doctor"),
    [
        ("Запиши меня к ярунину на завтра", YARUNIN),
        ("запишите к Кузнецову", KUZNETSOV),
        ("к доктору КУЗНЕЦОВУ Н.А.", KUZNETSOV),
        ("хочу к Билалову Р. Ф. в субботу утром", BILALOV),
        ("можно на приём к врачу Деменкову?", DEMENKOV),
        ("у Деменкова есть время завтра?", DEMENKOV),
        ("Кузнецов завтра работает?", KUZNETSOV),
        ("когда принимает Ярунин", YARUNIN),
        ("запишите к Андрею Александровичу", YARUNIN),
        ("запиши к кузницову", KUZNETSOV),  # a typo, one letter
        ("хочу к Деменькову", DEMENKOV),
        ("ярунин", YARUNIN),
        ("запишите к Андрею Ярунину", YARUNIN),
        ("ярунин свободен завтра?", YARUNIN),
        ("к Кузнецову Н. А. можно?", KUZNETSOV),
    ],
)
def test_doctor_in_any_case_and_register(doctors, text: str, doctor: str) -> None:
    found = parse(text, doctors, TODAY)
    assert found.wish.doctor == doctor, text
    assert not found.unknown_doctor


@pytest.mark.parametrize(
    "text",
    [
        "меня зовут Кузнецов Иван Петрович, болит рука",
        "Кузнецов Иван, 1985 года рождения",
        "я Кузнецова Мария, хочу записаться",
        "запишите меня, фамилия Билалов",
        "кузнечик укусил",
        "у Кузнецова Ивана Петровича перелом, запишите его",
        "Билалов Тимур упал с велосипеда",
    ],
)
def test_patient_surname_is_not_a_doctor(doctors, text: str) -> None:
    found = parse(text, doctors, TODAY)
    assert found.wish.doctor is None, text
    assert not found.unknown_doctor


@pytest.mark.parametrize(
    "text",
    ["запиши к Иванову", "запишите меня к доктору Петровой", "хочу к Сидоровскому на завтра"],
)
def test_unknown_surname_is_noticed(doctors, text: str) -> None:
    found = parse(text, doctors, TODAY)
    assert found.unknown_doctor and found.wish.doctor is None, text


@pytest.mark.parametrize(
    "text",
    [
        "запишите к детскому травматологу",
        "к вам можно завтра?",
        "запишите к врачу",
        "кстати, к слову, сколько стоит приём",
        "записаться к ужину успею?",
        "хочу к вам в травмпункт",
        "запишите сына к травматологу",
    ],
)
def test_ordinary_words_are_not_surnames(doctors, text: str) -> None:
    found = parse(text, doctors, TODAY)
    assert not found.unknown_doctor and found.wish.doctor is None, text


def test_names_for_the_patient(doctors) -> None:
    assert short_name(YARUNIN) == "Ярунин А. А."
    assert dative(YARUNIN) == "к Ярунину А. А."
    assert dative(KUZNETSOV) == "к Кузнецову Н. А."
    assert dative("Иванова Мария Петровна") == "к Ивановой М. П."
    assert dative("Чайковский Пётр Ильич") == "к Чайковскому П. И."
    assert dative("Шевченко Тарас Григорьевич") == "к врачу Шевченко Т. Г."


# --- the day -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "wanted"),
    [
        ("запишите на сегодня", day(12)),
        ("запишите на завтра", day(13)),
        ("можно послезавтра?", day(14)),
        ("хочу в пятницу", day(13)),
        ("в субботу можно прийти?", day(14)),
        ("запишите на понедельник", day(16)),
        ("во вторник есть время?", day(17)),
        ("запишите в среду", day(18)),
        ("запишите в четверг", day(19)),  # today is Thursday: the next one
        ("в следующую пятницу", day(20)),
        ("в следующий понедельник", day(16)),
        ("на воскресенье", day(15)),
        ("запишите на 15 ноября", day(15)),
        ("запишите на 15.11", day(15)),
        ("на 15.11.2026", day(15)),
        ("хочу 20 числа", day(20)),
        ("запишите на 17-е", day(17)),
        ("на 3 декабря", day(3, 12)),
        ("через 2 дня можно?", day(14)),
        ("запишите через неделю", day(19)),
        ("на выходных можно?", day(14)),
        ("Кузнецов завтра работает?", day(13)),  # «в» of the surname is not a preposition
        ("запишите к Билалов завтра", day(13)),
    ],
)
def test_day(doctors, text: str, wanted: date) -> None:
    assert parse(text, doctors, TODAY).wish.day == wanted, text


@pytest.mark.parametrize(
    "text",
    [
        "упал вчера",
        "упал 10 ноября, болит нога",
        "в субботу упал с велосипеда",
        "сегодня подвернул ногу",
        "сегодня утром ударился рукой",
        "болит с понедельника",
        "рука болит, сегодня хуже",
        "ребёнку 3 года, упал",
        "порезал палец 2 дня назад",
        "добрый день, сломал руку",
        "доброе утро! добрый вечер!",
    ],
)
def test_the_day_of_the_injury_is_not_a_wish(doctors, text: str) -> None:
    wish = parse(text, doctors, TODAY).wish
    assert wish.empty, (text, wish)


@pytest.mark.parametrize(
    ("text", "wanted"),
    [
        ("упал в субботу, запишите на завтра", day(13)),
        ("сегодня упал, можно сегодня прийти?", day(12)),
        ("вчера вечером подвернул ногу и хочу завтра к врачу", day(13)),
        ("подвернул ногу сегодня утром запишите на пятницу", day(13)),
    ],
)
def test_injury_day_and_wish_day_in_one_message(doctors, text: str, wanted: date) -> None:
    assert parse(text, doctors, TODAY).wish.day == wanted, text


# --- the time ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "period"),
    [
        ("завтра утром", "morning"),
        ("запишите с утра", "morning"),
        ("на завтра днём", "day"),
        ("в пятницу после обеда", "day"),
        ("завтра в обед", "day"),
        ("хочу на завтра вечером", "evening"),
        ("запишите на вечер", "evening"),
        ("можно после работы?", "evening"),
        ("сегодня ночью можно?", "night"),
    ],
)
def test_part_of_the_day(doctors, text: str, period: str) -> None:
    assert parse(text, doctors, TODAY).wish.period == period, text


@pytest.mark.parametrize(
    ("text", "at"),
    [
        ("запишите на завтра в 14:30", time(14, 30)),
        ("завтра в 9 утра", time(9)),
        ("сегодня в 7 вечера", time(19)),
        ("запишите к 10 часам", time(10)),
        ("завтра в 14.30", time(14, 30)),
        ("запишите на 15:00", time(15)),
        ("можно в 3 часа дня?", time(15)),
        ("можно в 3?", time(15)),
        ("завтра вечером в 7", time(19)),
        ("запишите на 2 декабря в 10", time(10)),
    ],
)
def test_exact_time(doctors, text: str, at: time) -> None:
    assert parse(text, doctors, TODAY).wish.at == at, text


@pytest.mark.parametrize(
    ("text", "start", "end"),
    [
        ("завтра после 18", time(18), None),
        ("запишите на завтра после 18:30", time(18, 30), None),
        ("можно до 12?", None, time(12)),
        ("после 18 можно?", time(18), None),
        ("после шести вечера", None, None),  # words for numbers are not read
        ("в пятницу с 10 до 12", time(10), time(12)),
    ],
)
def test_time_window(doctors, text: str, start: time | None, end: time | None) -> None:
    wish = parse(text, doctors, TODAY).wish
    assert (wish.start, wish.end) == (start, end), text


@pytest.mark.parametrize(
    "text",
    [
        "упал с 2 метров",
        "ребёнку 7 лет",
        "болит уже 3 дня",
        "после 18 лет можно без родителей?",
        "вчера вечером упал",
        "упал утром на тренировке",
        "до 7 дней травма",
        "в 3 раза больнее",
        "в 3 месяца ребёнок упал с дивана",
        "мне 30 лет",
        "ул. Шейнкмана, 113/2",
    ],
)
def test_numbers_and_event_time_are_not_a_time_wish(doctors, text: str) -> None:
    wish = parse(text, doctors, TODAY).wish
    assert wish.empty, (text, wish)


# --- the whole message ---------------------------------------------------------------------


def test_the_ten_phrases(doctors) -> None:
    one = parse("Запиши меня к ярунину на завтра", doctors, TODAY)
    assert (one.wish.doctor, one.wish.day) == (YARUNIN, day(13)) and one.booking and one.pure

    two = parse("запишите к Кузнецову", doctors, TODAY)
    assert (two.wish.doctor, two.wish.day) == (KUZNETSOV, None) and two.booking and two.pure

    three = parse("хочу на завтра вечером", doctors, TODAY)
    assert (three.wish.day, three.wish.period) == (day(13), "evening") and three.pure

    four = parse("подвернул ногу, можно к Билалову в субботу утром?", doctors, TODAY)
    assert (four.wish.doctor, four.wish.day, four.wish.period) == (BILALOV, day(14), "morning")
    assert not four.pure  # the complaint goes on to the scenario

    six = parse("запиши к Иванову", doctors, TODAY)
    assert six.unknown_doctor and six.booking and six.pure

    seven = parse("в пятницу после обеда", doctors, TODAY, context="time")
    assert (seven.wish.day, seven.wish.period) == (day(13), "day") and seven.pure

    eight = parse("перенеси запись на завтра", doctors, TODAY)
    assert eight.wish.day == day(13)

    nine = parse("кто завтра принимает?", doctors, TODAY)
    assert nine.question == "who" and nine.wish.day == day(13) and not nine.booking

    ten = parse("Ударился головой, запишите к Ярунину", doctors, TODAY)
    assert ten.wish.doctor == YARUNIN and not ten.pure


@pytest.mark.parametrize(
    ("text", "question"),
    [
        ("кто завтра принимает?", "who"),
        ("кто принимает завтра", "who"),
        ("какой врач сегодня дежурит?", "who"),
        ("кто из врачей работает в субботу?", "who"),
        ("кто сегодня в смене", "who"),
        ("кто сейчас принимает?", "who"),
        ("когда работает Ярунин?", "doctor"),
        ("Кузнецов завтра работает?", "doctor"),
        ("в какие дни принимает Билалов", "doctor"),
        ("график Деменкова", "doctor"),
        ("есть время сегодня вечером?", "free"),
        ("на завтра есть свободное время?", "free"),
        ("когда ближайшее свободное время?", "free"),
        ("у Деменкова есть окошко завтра?", "free"),
        ("когда можно записаться?", "free"),
    ],
)
def test_schedule_questions(doctors, text: str, question: str) -> None:
    found = parse(text, doctors, TODAY)
    assert found.question == question and not found.booking, text


@pytest.mark.parametrize(
    "text",
    [
        "запишите к Ярунину, он завтра работает?",  # a request: the wizard shows his time
        "когда у меня приём",  # «Мои записи»
        "Скажите, когда у вас приём",
        "кто меня примет с переломом?",
        "сколько стоит приём?",
        "есть ли у вас рентген?",
        "кто такой Ярунин?",  # about the doctor: the справка answers
        "какие врачи у вас работают?",
        "Можно записаться именно к Кузнецову?",  # a request with a wish
        "во сколько вы работаете?",
    ],
)
def test_not_schedule_questions(doctors, text: str) -> None:
    assert parse(text, doctors, TODAY).question is None, text


@pytest.mark.parametrize(
    "text",
    ["вчера", "сегодня утром", "в пятницу", "три дня назад, в понедельник", "утром упал"],
)
def test_an_answer_to_a_scenario_question_is_not_a_wish(doctors, text: str) -> None:
    """«Когда была получена травма?» — «в пятницу»: the injury day, not a wish."""
    assert parse(text, doctors, TODAY, context="strict").wish.empty, text


def test_strict_context_takes_an_explicit_request(doctors) -> None:
    found = parse("вчера, запишите к Кузнецову на завтра", doctors, TODAY, context="strict")
    assert (found.wish.doctor, found.wish.day) == (KUZNETSOV, day(13))
    assert not found.pure  # «вчера» is still the answer to the question


def test_time_context_reads_bare_words(doctors) -> None:
    assert parse("сегодня", doctors, TODAY, context="time").wish.day == day(12)
    assert parse("вечером", doctors, TODAY, context="time").wish.period == "evening"
    assert parse("Билалов", doctors, TODAY, context="time").wish.doctor == BILALOV


def test_a_question_about_booking_to_a_doctor_is_a_request(doctors) -> None:
    found = parse("Можно записаться именно к Кузнецову?", doctors, TODAY)
    assert found.wish.doctor == KUZNETSOV and found.booking and found.pure


def test_two_doctors_the_first_named_is_the_wish(doctors) -> None:
    found = parse("запишите к Ярунину или Кузнецову", doctors, TODAY)
    assert found.wish.doctor == YARUNIN and found.pure
    marked = mark_doctors("запишите к Ярунину или Кузнецову", doctors)
    assert marked == "запишите к [ВРАЧ] или [ВРАЧ]"


def test_merge_keeps_what_was_not_said_again() -> None:
    first = Wish(doctor=YARUNIN, day=day(13))
    merged = first.merge(Wish(period="evening"))
    assert (merged.doctor, merged.day, merged.period) == (YARUNIN, day(13), "evening")
    other_time = merged.merge(Wish(start=time(18)))
    assert (other_time.period, other_time.start) == (None, time(18))  # one time wish at once
    assert Wish().empty and not merged.empty


# --- what DeepSeek sees --------------------------------------------------------------------


def test_doctor_becomes_a_neutral_label_for_the_llm(doctors) -> None:
    from app.llm.masking import mask

    marked = mark_doctors("Запиши меня к Ярунину на завтра", doctors)
    assert marked == "Запиши меня к [ВРАЧ] на завтра"
    assert mask(marked, today=TODAY) == "Запиши меня к [ВРАЧ] на завтра"
    assert "[ВРАЧ]" in mark_doctors("подвернул ногу, можно к билалову Р. Ф. в субботу?", doctors)
    assert "Билалов" not in mark_doctors("можно к Билалову Р. Ф. в субботу?", doctors)
    # the patient's own surname is personal data, not a doctor
    own = mask(mark_doctors("меня зовут Кузнецов Иван Петрович", doctors), today=TODAY)
    assert "[ВРАЧ]" not in own and "Кузнецов" not in own
