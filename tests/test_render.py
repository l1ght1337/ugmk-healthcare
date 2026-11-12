"""Patient messages built from engine steps (no Telegram)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.bot import render
from app.config import ClinicData
from app.flow.engine import Place
from app.flow.loader import KnowledgeBase
from app.schedule.base import Slot
from tests.conftest import NOW, TZ


@pytest.mark.parametrize(
    ("patronymic", "word"),
    [
        ("Петрович", "записан"),
        ("Кузьмич", "записан"),
        ("Ивановна", "записана"),
        ("Ильинична", "записана"),
        (None, "записан(а)"),
        ("", "записан(а)"),
    ],
)
def test_booked_word(patronymic: str | None, word: str) -> None:
    assert render.booked_word(patronymic) == word


def test_format_date() -> None:
    assert render.format_date(date(2026, 11, 13)) == "13 ноября (пятница)"


def test_handoff_mentions_working_hours_only_outside_them(clinic: ClinicData) -> None:
    assert render.handoff_message(clinic, NOW) == (
        "Передаю Ваше обращение администратору. С Вами свяжутся."
    )
    late = render.handoff_message(clinic, NOW.replace(hour=23))
    assert late == (
        "Передаю Ваше обращение администратору. С Вами свяжутся в рабочее время — с 08:00 до 20:00."
    )


def test_place_line(kb: KnowledgeBase) -> None:
    line = render.place_line(Place(kb.facilities["gkb14"], label="Женщины", note="кроме ЧМТ"))
    assert line == (
        "• Женщины: Городская клиническая больница № 14, пер. Суворовский, 5В "
        "(без КТ и МРТ; кроме ЧМТ)"
    )
    assert render.place_line(Place(kb.facilities["municipal_trauma_center"])) == (
        "• Любой муниципальный травмпункт"
    )


def test_route_lists_places_under_the_script_phrase(run, clinic: ClinicData) -> None:
    w = run("genital_trauma", {}, audience="child", age_months=5 * 12)
    text = render.outcome_message(w.step, clinic, NOW)
    assert text.startswith("В травмпункте у нас принимает только травматолог-ортопед.")
    assert "обратиться:\n• Мальчики до 18 лет: ДГКБ № 9, приёмный покой, ул. Решетская, 51" in text
    assert "Девочки до 18 лет: Областная детская клиническая больница № 1" in text


def test_emergency_outcome_text(run, clinic: ClinicData) -> None:
    w = run("animal_bite", {"danger_signs": True})
    text = render.outcome_message(w.step, clinic, NOW)
    assert text.startswith("При угрозе жизни звоните 103 или 112.")
    assert "Также можно обратиться:\n• Токсикологическое отделение ГКБ № 14" in text


def _slot(hour: int) -> Slot:
    start = datetime(2026, 11, 13, hour, 0, tzinfo=TZ)
    return Slot(
        "s",
        start,
        start + timedelta(minutes=30),
        "Гусев Антон Викторович",
        "Травматолог 1",
        False,
        False,
    )


@pytest.mark.parametrize(("hour", "night"), [(12, False), (19, False), (20, True), (3, True)])
def test_confirmation(run, kb: KnowledgeBase, clinic: ClinicData, hour: int, night: bool) -> None:
    w = run(
        "burns",
        {
            "burn_location": "body",
            "burn_cause": "thermal",
            "injury_age": "within_24h",
            "seen_doctor": "no",
        },
        audience="child",
        age_months=6 * 12,
    )
    text = render.booking_confirmation(
        kb,
        w.step,
        _slot(hour),
        first_name="Анна",
        patronymic="Сергеевна",
        audience="child",
        clinic=clinic,
        demo=False,
    )
    assert (
        "Анна Сергеевна записана на приём травматолога-ортопеда в травмпункт "
        f"на 13 ноября (пятница) в {hour:02d}:00"
    ) in text
    assert "Врач: Гусев Антон Викторович." in text
    assert "паспорт взрослого, сопровождающего ребёнка" in text
    assert "свидетельство о рождении ребёнка (с 14 лет — паспорт) и СНИЛС ребёнка" in text
    assert ("главные ворота" in text) is night
    assert "демо" not in text


def test_route_without_script_phrase_gets_a_lead(run, clinic: ClinicData) -> None:
    w = run("cut_sutures", {"wound_location": "face_neck_head"}, audience="child", age_months=108)
    assert not w.step.outcome.say
    text = render.outcome_message(w.step, clinic, NOW)
    assert text.startswith(
        "С такой травмой Вам нужно обратиться:\n• ДГКБ № 9, приёмный покой, ул. Решетская, 51"
    )


@pytest.mark.parametrize("audience", ["adult", "child"])
def test_open_fracture_mentions_ambulance(run, clinic: ClinicData, audience: str) -> None:
    w = run(
        "fracture",
        {"assault": False, "open_or_displaced": True},
        audience=audience,
        age_months=10 * 12,
    )
    text = render.outcome_message(w.step, clinic, NOW)
    assert text.startswith(
        "Если кость видна в ране или кровотечение не останавливается, вызывайте скорую: "
        "103 или 112. С такой травмой Вам нужно обратиться:\n• "
    )


@pytest.mark.parametrize(
    ("day", "text"),
    [
        (13, "в пятницу, 13 ноября, в 10:30"),
        (17, "во вторник, 17 ноября, в 10:30"),
        (18, "в среду, 18 ноября, в 10:30"),
    ],
)
def test_format_when(day: int, text: str) -> None:
    assert render.format_when(datetime(2026, 11, day, 10, 30, tzinfo=TZ)) == text
