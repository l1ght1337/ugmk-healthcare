from datetime import time

import pytest
from pydantic import ValidationError

from app.config import Emergency, WorkingHours, load_clinic_data


def test_clinic_yaml_files_load() -> None:
    data = load_clinic_data()
    assert data.contacts.trauma_center.address == "ул. Шейнкмана, 113/2"
    assert data.clinic.rabies_vaccine_available is False
    assert data.clinic.emergency.reply == "При угрозе жизни звоните 103 или 112."


def test_unknown_price_is_not_invented() -> None:
    data = load_clinic_data()
    assert data.price_text("consultation") is None or data.price_text("consultation").endswith(
        "рублей"
    )
    assert data.price_text("no_such_service") is None


def test_emergency_triggers_must_be_phrases() -> None:
    with pytest.raises(ValidationError, match="phrases, not single words"):
        Emergency(reply="x", phrases=("кровь не останавливается", "кровь"))


def test_working_hours_overnight() -> None:
    day = WorkingHours(start=time(8), end=time(20))
    assert day.contains(time(8)) and not day.contains(time(20)) and not day.contains(time(2))
    night = WorkingHours(start=time(20), end=time(8))
    assert night.contains(time(23)) and night.contains(time(2)) and not night.contains(time(12))
