"""Build patient messages from engine steps. Pure functions, no aiogram.

Texts come from the scenarios (script phrases), config (address, prices, hours) and
app/bot/texts.py (the bot's own phrases). Nothing here invents data: an unknown price
becomes «уточнит администратор».
"""

from __future__ import annotations

from datetime import date, datetime, time

from app.bot import texts
from app.config import ClinicData
from app.flow.engine import AskPatient, AskQuestion, Done, Place
from app.flow.loader import KnowledgeBase
from app.flow.schema import Audience, Scenario
from app.schedule.base import Slot

MALE_PATRONYMIC = ("вич", "ич", "оглы", "улы", "уулу")
FEMALE_PATRONYMIC = ("вна", "чна", "кызы", "гызы")


def format_date(day: date) -> str:
    return f"{day.day} {texts.MONTHS_GENITIVE[day.month - 1]} ({texts.WEEKDAYS[day.weekday()]})"


def format_time(moment: time | datetime) -> str:
    return f"{moment:%H:%M}"


def booked_word(patronymic: str | None) -> str:
    """«записан» / «записана» by the patronymic; «записан(а)» if it does not tell."""
    word = (patronymic or "").strip().lower()
    if word.endswith(FEMALE_PATRONYMIC):
        return texts.BOOKED["female"]
    if word.endswith(MALE_PATRONYMIC):
        return texts.BOOKED["male"]
    return texts.BOOKED[None]


def patient_display(first_name: str, patronymic: str | None) -> str:
    """Script: «(Имя или Имя Отчество) записан…»."""
    return f"{first_name} {patronymic}".strip() if patronymic else first_name


def price_text(clinic: ClinicData, key: str) -> str:
    return clinic.price_text(key) or texts.PRICE_UNKNOWN


def ask_patient(step: AskPatient) -> str:
    return texts.ASK_AUDIENCE if step.need == "audience" else texts.ASK_CHILD_AGE


def ask_question(step: AskQuestion) -> str:
    """Script phrase; on a repeat ask the LLM rephrases it (stage 3), until then «Уточните»."""
    if step.attempt > 1:
        return texts.CLARIFY.format(ask=step.question.ask)
    return step.question.ask


def place_line(place: Place) -> str:
    fac = place.facility
    text = f"{fac.name}, {fac.address}" if fac.address else fac.name
    if place.label:
        text = f"{place.label}: {text}"
    notes = [n for n in (fac.note, place.note) if n]
    if notes:
        text += f" ({'; '.join(notes)})"
    return f"• {text}"


def handoff_message(clinic: ClinicData, now: datetime) -> str:
    hours = clinic.contacts.admin_working_hours
    when = ""
    if not hours.contains(now.time()):
        start, end = format_time(hours.start), format_time(hours.end)
        when = texts.HANDOFF_LATER.format(start=start, end=end)
    return texts.HANDOFF.format(when=when)


def outcome_message(done: Done, clinic: ClinicData, now: datetime) -> str:
    """What the patient reads when the scenario ends (before slots, for a booking)."""
    parts: list[str] = []
    if done.kind == "emergency":
        parts.append(clinic.clinic.emergency.reply)
    parts.extend(done.outcome.say)
    if done.places:
        lines = [place_line(p) for p in done.places]
        if done.kind == "emergency":
            lines.insert(0, texts.EMERGENCY_ALSO)
        elif not done.outcome.say:
            lines.insert(0, texts.ROUTE_LEAD)
        if parts and parts[-1].endswith(":"):
            parts[-1] = "\n".join([parts[-1], *lines])
        else:
            parts.append("\n".join(lines))
    if done.kind == "handoff":
        parts.append(handoff_message(clinic, now))
    if done.offer_admin:
        parts.append(texts.OFFER_ADMIN)
    parts.extend(n.text for n in done.notices)
    return "\n\n".join(parts)


def emergency_message(clinic: ClinicData) -> str:
    return clinic.clinic.emergency.reply


def documents(kb: KnowledgeBase, audience: Audience) -> str:
    """Step 6 of the booking scripts: what to bring."""
    c = kb.common
    if audience == "child":
        return f"{c.bring_child.text} {c.bring_child_first_visit.text}"
    return f"{c.bring_adult.text} {c.bring_adult_first_visit.text}"


def needs_night_parking(slot: Slot, clinic: ClinicData) -> bool:
    return not clinic.contacts.trauma_center.underground_parking_hours.contains(slot.start.time())


def booking_summary(
    scenario: Scenario,
    slot: Slot,
    *,
    first_name: str,
    patronymic: str | None,
    price_key: str,
    clinic: ClinicData,
) -> str:
    """Step 5 of the booking scripts, filled from the schedule and config."""
    assert scenario.summary is not None
    return scenario.summary.format(
        patient=patient_display(first_name, patronymic),
        booked=booked_word(patronymic),
        date=format_date(slot.start.date()),
        time=format_time(slot.start),
        price=price_text(clinic, price_key),
        address=clinic.contacts.trauma_center.address,
    )


def booking_confirmation(
    kb: KnowledgeBase,
    done: Done,
    slot: Slot,
    *,
    first_name: str,
    patronymic: str | None,
    audience: Audience,
    clinic: ClinicData,
    demo: bool,
) -> str:
    """Summary + doctor + documents (+ night parking, + demo notice)."""
    assert done.outcome.booking is not None
    parts = [
        booking_summary(
            done.scenario,
            slot,
            first_name=first_name,
            patronymic=patronymic,
            price_key=done.outcome.booking.price_key,
            clinic=clinic,
        ),
        f"Врач: {slot.doctor}.",
        documents(kb, audience),
    ]
    if needs_night_parking(slot, clinic):
        parts.append(kb.common.parking_after_20.text)
    if demo:
        parts.append(texts.DEMO_BOOKING)
    return "\n\n".join(parts)
