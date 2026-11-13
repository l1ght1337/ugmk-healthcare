"""Tables of the test bot. Times are stored in UTC (CLAUDE.md), shown in clinic time.

Personal data (names, birth dates, phones) stays here and in the administrators' chat;
it is never sent to the LLM.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, String, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class UTCDateTime(TypeDecorator[datetime]):
    """Aware datetimes in, aware UTC datetimes out (SQLite keeps no zone)."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime given; use an aware one")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None else None


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: UTCDateTime, dict[str, Any]: JSON}


class User(Base):
    """A Telegram user (the applicant: the patient or a parent)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Telegram user id
    chat_id: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[str | None] = mapped_column(String(64))
    display_name: Mapped[str | None] = mapped_column(String(128))
    phone: Mapped[str | None] = mapped_column(String(32))
    consent_at: Mapped[datetime | None]
    guardian_at: Mapped[datetime | None]  # «я законный представитель ребёнка»
    session: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    audience: Mapped[str] = mapped_column(String(8))  # adult / child
    last_name: Mapped[str] = mapped_column(String(64))
    first_name: Mapped[str] = mapped_column(String(64))
    patronymic: Mapped[str | None] = mapped_column(String(64))
    birth_date: Mapped[date]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    @property
    def short_name(self) -> str:
        return f"{self.last_name} {self.first_name}"

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.last_name, self.first_name, self.patronymic) if p)


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    scenario_id: Mapped[str] = mapped_column(String(32))
    outcome_id: Mapped[str] = mapped_column(String(64))
    visit: Mapped[str] = mapped_column(String(8))  # primary / repeat
    service: Mapped[str] = mapped_column(String(128))
    slot_policy: Mapped[str] = mapped_column(String(32))
    price_key: Mapped[str] = mapped_column(String(32))
    slot_id: Mapped[str] = mapped_column(String(64))
    start_at: Mapped[datetime]
    end_at: Mapped[datetime]
    doctor: Mapped[str] = mapped_column(String(128))
    cabinet: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active / cancelled
    cycle: Mapped[int] = mapped_column(default=1)  # 1 — first visit, 2 — control visit
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("bookings.id"))
    payment: Mapped[str | None] = mapped_column(String(16))  # self / insurance
    confirmed_at: Mapped[datetime | None]
    survey: Mapped[str | None] = mapped_column(String(16))  # ok / question
    rating: Mapped[int | None]
    demo: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Reminder(Base):
    """Source of truth for notifications; the scheduler polls it, so restarts lose nothing."""

    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # day_before / two_hours / after_visit
    due_at: Mapped[datetime] = mapped_column(index=True)
    sent_at: Mapped[datetime | None]
    cancelled: Mapped[bool] = mapped_column(default=False)


class Handoff(Base):
    """A request passed to the administrators' chat.

    One open request per user: new requests are added to it as replies in the admin chat.
    It closes with «Закрыть обращение» or after a day without activity. ``kind`` «crisis»
    is a note about a risk to life, not a request: never open, not counted in the limit.
    """

    __tablename__ = "handoffs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    reason: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # Nullable for the test-stage schema evolution (old rows: a closed request).
    kind: Mapped[str | None] = mapped_column(String(16), default="request")  # request / crisis
    status: Mapped[str | None] = mapped_column(String(16), default="open")  # open / closed
    updated_at: Mapped[datetime | None] = mapped_column(default=utcnow)
    closed_at: Mapped[datetime | None]
    closed_by: Mapped[str | None] = mapped_column(String(128))
    last_note: Mapped[str | None] = mapped_column(String(512))  # what was sent last (no repeats)


class AdminMessage(Base):
    """A bot message in the admin chat; an administrator's reply to it goes to the patient."""

    __tablename__ = "admin_messages"

    message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    handoff_id: Mapped[int] = mapped_column(ForeignKey("handoffs.id"))


class AccessRequest(Base):
    """A user outside ALLOWED_USER_IDS; an administrator may let them in with «Разрешить»."""

    __tablename__ = "access_requests"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[str | None] = mapped_column(String(64))
    display_name: Mapped[str | None] = mapped_column(String(128))
    requested_at: Mapped[datetime] = mapped_column(default=utcnow)
    approved_at: Mapped[datetime | None]
    approved_by: Mapped[str | None] = mapped_column(String(128))
