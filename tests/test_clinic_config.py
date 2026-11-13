from datetime import time

import pytest
from pydantic import ValidationError

from app.config import Emergency, Prices, WorkingHours, load_clinic_data


def test_clinic_yaml_files_load() -> None:
    data = load_clinic_data()
    assert data.contacts.trauma_center.address == "ул. Шейнкмана, 113/2"
    assert data.clinic.rabies_vaccine_available is False
    assert data.clinic.emergency.reply == "При угрозе жизни звоните 103 или 112."


def test_prices_are_demo_until_the_clinic_gives_them() -> None:
    data = load_clinic_data()
    assert data.prices.demo is True
    assert data.price_text("primary_visit") == "2000 рублей (демо-цена)"
    assert data.price_text("no_such_service") is None
    real = data.model_copy(
        update={"prices": Prices(demo=False, primary_visit=1800, repeat_visit=None)}
    )
    assert real.price_text("primary_visit") == "1800 рублей"
    assert real.price_text("repeat_visit") is None  # «стоимость уточнит администратор»


def test_clinic_answers_of_13_november() -> None:
    data = load_clinic_data()
    assert data.clinic.control_visit_after_days == 10
    assert data.contacts.ambulance_line_phone is None  # TODO from the clinic
    assert data.contacts.crisis_line_phone == "8-800-2000-122"
    assert "спрыгну с крыши" in data.clinic.crisis.phrases
    assert "спрыгнул с крыши" not in data.clinic.crisis.phrases  # an injury, not a crisis


def test_emergency_triggers_must_be_phrases() -> None:
    with pytest.raises(ValidationError, match="phrases, not single words"):
        Emergency(reply="x", phrases=("кровь не останавливается", "кровь"))


def test_working_hours_overnight() -> None:
    day = WorkingHours(start=time(8), end=time(20))
    assert day.contains(time(8)) and not day.contains(time(20)) and not day.contains(time(2))
    night = WorkingHours(start=time(20), end=time(8))
    assert night.contains(time(23)) and night.contains(time(2)) and not night.contains(time(12))
