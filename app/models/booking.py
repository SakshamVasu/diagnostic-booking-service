from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.core.exceptions import InvalidStateTransitionError
from app.db.base import MONEY, Base, TimestampMixin, str_enum
from app.models.payment import PaymentStatus

if TYPE_CHECKING:
    from app.models.centre import DiagnosticCentre
    from app.models.payment import Payment
    from app.models.test import DiagnosticTest
    from app.models.user import User


class BookingStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"  # unpaid past the hold window; the slot was released
    COMPLETED = "COMPLETED"  # the patient attended the appointment
    NO_SHOW = "NO_SHOW"  # the patient missed the appointment


# Statuses that hold a centre/test/time slot (mirrors the partial unique index below).
ACTIVE_BOOKING_STATUSES = frozenset({BookingStatus.PENDING, BookingStatus.CONFIRMED})

# The single source of truth for the booking lifecycle. Anything not listed is rejected.
BOOKING_TRANSITIONS: dict[BookingStatus, frozenset[BookingStatus]] = {
    BookingStatus.PENDING: frozenset(
        {BookingStatus.CONFIRMED, BookingStatus.FAILED, BookingStatus.CANCELLED, BookingStatus.EXPIRED}
    ),
    # Cancelling a CONFIRMED booking refunds it; attendance is recorded by an admin afterwards.
    BookingStatus.CONFIRMED: frozenset({BookingStatus.CANCELLED, BookingStatus.COMPLETED, BookingStatus.NO_SHOW}),
    BookingStatus.FAILED: frozenset(),
    BookingStatus.CANCELLED: frozenset(),
    BookingStatus.EXPIRED: frozenset(),
    BookingStatus.COMPLETED: frozenset(),
    BookingStatus.NO_SHOW: frozenset(),
}


class Booking(TimestampMixin, Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        Index("ix_bookings_user_id_created_at", "user_id", "created_at"),
        Index("ix_bookings_status", "status"),
        # A centre/test/time slot can be held by at most one active booking.
        # Enforced in the database so concurrent requests cannot double-book.
        Index(
            "uq_bookings_active_slot",
            "centre_id",
            "test_id",
            "appointment_datetime",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'CONFIRMED')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    test_id: Mapped[int] = mapped_column(ForeignKey("diagnostic_tests.id", ondelete="RESTRICT"), nullable=False)
    centre_id: Mapped[int] = mapped_column(ForeignKey("diagnostic_centres.id", ondelete="RESTRICT"), nullable=False)
    appointment_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Snapshot of the centre-specific price at booking time; later price changes don't affect it.
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    status: Mapped[BookingStatus] = mapped_column(
        str_enum(BookingStatus, "booking_status"),
        nullable=False,
        default=BookingStatus.PENDING,
        server_default=BookingStatus.PENDING.value,
    )

    user: Mapped["User"] = relationship(back_populates="bookings")
    test: Mapped["DiagnosticTest"] = relationship()
    centre: Mapped["DiagnosticCentre"] = relationship()
    payments: Mapped[list["Payment"]] = relationship(back_populates="booking")

    @property
    def patient_name(self) -> str:
        return self.user.name

    @property
    def patient_email(self) -> str:
        return self.user.email

    @property
    def test_name(self) -> str:
        return self.test.name

    @property
    def centre_name(self) -> str:
        return self.centre.name

    @property
    def centre_location(self) -> str:
        return self.centre.location

    @property
    def payment_in_progress(self) -> bool:
        """True while a payment awaits the provider's final result."""
        return any(payment.status == PaymentStatus.PENDING for payment in self.payments)

    @property
    def hold_expires_at(self) -> datetime | None:
        """When an unpaid PENDING booking releases its slot (None if it never does)."""
        hold = get_settings().booking_hold_minutes
        if self.status != BookingStatus.PENDING or hold == 0 or self.payment_in_progress:
            return None
        return self.created_at + timedelta(minutes=hold)

    def hold_expired(self, now: datetime) -> bool:
        expires_at = self.hold_expires_at
        return expires_at is not None and expires_at <= now

    @property
    def changeable_until(self) -> datetime:
        """Last moment the patient can cancel or reschedule this booking themselves."""
        return self.appointment_datetime - timedelta(hours=get_settings().cancellation_window_hours)

    def transition_to(self, new_status: BookingStatus) -> None:
        if new_status not in BOOKING_TRANSITIONS[self.status]:
            raise InvalidStateTransitionError(
                f"Booking cannot transition from {self.status.value} to {new_status.value}"
            )
        self.status = new_status
