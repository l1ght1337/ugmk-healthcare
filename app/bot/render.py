"""Build patient messages from engine steps. Pure functions, no aiogram.

Texts come from the scenarios (script phrases), config (address, prices, hours) and
app/bot/texts.py (the bot's own phrases). Nothing here invents data: an unknown price
becomes «уточнит администратор».
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.bot import texts
from app.config import ClinicData
from app.flow.engine import AskPatient, AskQuestion, Done, Place
from app.flow.loader import KnowledgeBase
from app.flow.schema import Audience, Choice, Scenario
from app.schedule.base import Slot

MALE_PATRONYMIC = ("вич", "ич", "оглы", "улы", "уулу")
FEMALE_PATRONYMIC = ("вна", "чна", "кызы", "гызы")


def format_date(day: date) -> str:
    return f"{day.day} {texts.MONTHS_GENITIVE[day.month - 1]} ({texts.WEEKDAYS[day.weekday()]})"


def format_when(moment: datetime) -> str:
    """«в пятницу, 13 ноября, в 14:30» (clinic time expected)."""
    day = f"{moment.day} {texts.MONTHS_GENITIVE[moment.month - 1]}"
    return f"{texts.WEEKDAYS_ON[moment.weekday()]}, {day}, в {moment:%H:%M}"


def format_time(moment: time | datetime) -> str:
    return f"{moment:%H:%M}"


def format_day_time(moment: datetime) -> str:
    """«пятница, 13 ноября, 14:30» for the booking card."""
    day = f"{moment.day} {texts.MONTHS_GENITIVE[moment.month - 1]}"
    return f"{texts.WEEKDAYS[moment.weekday()]}, {day}, {moment:%H:%M}"


def day_label(day: date, today: date) -> str:
    """Day button: «Сегодня, 12.11», «Завтра, 13.11», «Сб, 14.11» (≤ 18 characters)."""
    if day == today:
        return f"{texts.TODAY}, {day:%d.%m}"
    if day == today + timedelta(days=1):
        return f"{texts.TOMORROW}, {day:%d.%m}"
    return f"{texts.WEEKDAYS_SHORT[day.weekday()]}, {day:%d.%m}"


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


@dataclass(frozen=True)
class Prompt:
    """A message with buttons; a pressed button sets ``choice.facts`` without the LLM."""

    text: str
    choices: tuple[Choice, ...] = ()


AUDIENCE_CHOICES = (
    Choice(label=texts.AUDIENCE_SELF, facts={"audience": "adult"}),
    Choice(label=texts.AUDIENCE_CHILD, facts={"audience": "child"}),
)


def ask_patient(step: AskPatient) -> Prompt:
    """«Для кого запись?» is asked with buttons; the age is typed (see app/flow/age.py)."""
    if step.need == "audience":
        return Prompt(texts.ASK_AUDIENCE, AUDIENCE_CHOICES)
    return Prompt(texts.ASK_CHILD_AGE)


def ask_question(step: AskQuestion, clarify: str | None = None, *, pick: bool = False) -> Prompt:
    """Script phrase with its buttons.

    ``clarify`` is the LLM's rephrasing after an unclear answer; ``pick`` means the LLM
    was unavailable, so the patient is asked to press a button.
    """
    if pick:
        text = f"{texts.PICK_BUTTON}\n{step.question.ask}"
    elif clarify:
        text = clarify
    elif step.attempt > 1:
        text = texts.CLARIFY.format(ask=step.question.ask)
    else:
        text = step.question.ask
    return Prompt(text, step.question.buttons())


def place_line(place: Place) -> str:
    fac = place.facility
    text = f"{fac.name}, {fac.address}" if fac.address else fac.name
    if place.label:
        text = f"{place.label}: {text}"
    notes = [n for n in (fac.note, place.note) if n]
    if notes:
        text += f" ({'; '.join(notes)})"
    return f"• {text}"


def handoff_when(clinic: ClinicData, now: datetime) -> str:
    """« в рабочее время — с 08:00 до 20:00» outside the administrators' hours, else empty."""
    hours = clinic.contacts.admin_working_hours
    if hours.contains(now.time()):
        return ""
    return texts.HANDOFF_LATER.format(start=format_time(hours.start), end=format_time(hours.end))


def handoff_message(clinic: ClinicData, now: datetime) -> str:
    return texts.HANDOFF.format(when=handoff_when(clinic, now))


def outcome_message(
    done: Done, clinic: ClinicData, now: datetime, *, include_handoff: bool = True
) -> str:
    """What the patient reads when the scenario ends (before slots, for a booking).

    ``include_handoff=False`` leaves out «Передаю Ваше обращение…»: the bot says it only
    once the request has really gone to the administrators (after consent and contact).
    """
    parts: list[str] = []
    if done.kind == "emergency":
        parts.append(emergency_message(clinic))
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
    if done.kind == "handoff" and include_handoff:
        parts.append(handoff_message(clinic, now))
    if done.offer_admin:
        parts.append(texts.OFFER_ADMIN)
    parts.extend(n.text for n in done.notices)
    return "\n\n".join(parts)


def emergency_message(clinic: ClinicData) -> str:
    """«При угрозе жизни звоните 103 или 112», plus the clinic's own line once it is known."""
    reply = clinic.clinic.emergency.reply
    line = clinic.contacts.ambulance_line_phone
    return f"{reply} {texts.AMBULANCE_LINE.format(phone=line)}" if line else reply


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


# --- booking card (part C) ----------------------------------------------------------------

SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[А-ЯЁ{])")


def summary_parts(template: str) -> dict[str, str]:
    """Sentences of the script's step-5 summary: «booked», «price», «payment», «address».

    «Благодарю Вас за ожидание! {patient} {booked} на приём … на {date} в {time}. Стоимость
    консультации {price}, … оплачиваются дополнительно. Оплачивать планируете …? Адрес …».
    """
    parts: dict[str, str] = {}
    for sentence in SENTENCE.split(template.strip()):
        if "{booked}" in sentence:
            parts["booked"] = sentence
        elif sentence.startswith("Стоимость"):
            parts["price"] = sentence
        elif sentence.startswith("Оплачивать"):
            parts["payment"] = sentence
        elif sentence.startswith("Адрес"):
            parts["address"] = sentence
    return parts


@dataclass(frozen=True)
class Card:
    """A structured message; the Telegram layer sends it as a rich message if it can."""

    title: str
    lead: str
    fields: tuple[tuple[str, str], ...]
    paragraphs: tuple[str, ...]
    list_title: str
    items: tuple[str, ...]
    notes: tuple[str, ...] = ()

    def plain(self) -> str:
        """The same content as plain text: the fallback for clients without rich messages."""
        lines = [self.title, "", self.lead, ""]
        lines += [f"{name}: {value}" for name, value in self.fields]
        lines += ["", *self.paragraphs]
        lines += ["", f"{self.list_title}:"]
        lines += [f"• {item}" for item in self.items]
        text = "\n".join(lines)
        if self.notes:
            text += "\n\n" + "\n\n".join(self.notes)
        return text


@dataclass(frozen=True)
class Venue:
    latitude: float
    longitude: float
    title: str
    address: str


def _bring_items(kb: KnowledgeBase, audience: Audience) -> tuple[str, ...]:
    c = kb.common
    phrases = (
        (c.bring_child.text, c.bring_child_first_visit.text)
        if audience == "child"
        else (c.bring_adult.text, c.bring_adult_first_visit.text)
    )
    items = []
    for phrase in phrases:
        item = phrase.removeprefix("С собой возьмите ").strip()
        items.append(item[:1].upper() + item[1:])
    return tuple(items)


def booking_card(
    kb: KnowledgeBase,
    done: Done,
    slot: Slot,
    *,
    first_name: str,
    patronymic: str | None,
    audience: Audience,
    clinic: ClinicData,
    demo: bool,
) -> Card:
    """Step 5 of the scripts as a card: when, doctor, address, price, what to bring."""
    assert done.outcome.booking is not None and done.scenario.summary is not None
    parts = summary_parts(done.scenario.summary)
    lead = (
        parts["booked"]
        .split(" на {date}")[0]
        .format(patient=patient_display(first_name, patronymic), booked=booked_word(patronymic))
    )
    price = parts["price"].format(price=price_text(clinic, done.outcome.booking.price_key))
    center = clinic.contacts.trauma_center
    notes = []
    if needs_night_parking(slot, clinic):
        notes.append(kb.common.parking_after_20.text)
    if demo:
        notes.append(texts.DEMO_BOOKING)
    return Card(
        title=texts.CARD_TITLE,
        lead=f"{lead}.",
        fields=(
            (texts.CARD_WHEN, format_day_time(slot.start)),
            (texts.CARD_DOCTOR, slot.doctor),
            (texts.CARD_ADDRESS, f"{center.address}, {center.entrance}"),
        ),
        paragraphs=(price,),
        list_title=texts.CARD_BRING,
        items=_bring_items(kb, audience),
        notes=tuple(notes),
    )


def payment_question(done: Done) -> str:
    """«Оплачивать планируете самостоятельно или по страховке?» from the script."""
    assert done.scenario.summary is not None
    return summary_parts(done.scenario.summary).get("payment", texts.PAYMENT_QUESTION)


def venue(clinic: ClinicData) -> Venue | None:
    center = clinic.contacts.trauma_center
    if center.location is None:  # TODO: the map point is not confirmed yet
        return None
    return Venue(
        center.location.latitude,
        center.location.longitude,
        texts.VENUE_TITLE,
        f"{center.address}, {center.entrance}",
    )
