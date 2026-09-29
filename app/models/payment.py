from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.exceptions import InvalidStateTransitionError
from app.db.base import MONEY, Base, TimestampMixin, str_enum

if TYPE_CHECKING:
    from app.models.booking import Booking, BookingStatus


class PaymentStatus(StrEnum):
    PENDING = "PENDING"  # submitted to the provider, final result not known yet
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"  # a successful payment returned because the booking was cancelled


PAYMENT_TRANSITIONS: dict[PaymentStatus, frozenset[PaymentStatus]] = {
    PaymentStatus.PENDING: frozenset({PaymentStatus.SUCCESS, PaymentStatus.FAILED}),
    PaymentStatus.SUCCESS: frozenset({PaymentStatus.REFUNDED}),
    PaymentStatus.FAILED: frozenset(),
    PaymentStatus.REFUNDED: frozenset(),
}


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        Index("ix_payments_booking_id", "booking_id"),
        # At most one in-flight or successful payment per booking: a booking can never be
        # charged twice, even under concurrent requests.
        Index(
            "uq_payments_active_booking",
            "booking_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'SUCCESS')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id", ondelete="RESTRICT"), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_payment_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(str_enum(PaymentStatus, "payment_status"), nullable=False)

    booking: Mapped["Booking"] = relationship(back_populates="payments")

    @property
    def booking_status(self) -> "BookingStatus":
        return self.booking.status

    def transition_to(self, new_status: PaymentStatus) -> None:
        if new_status not in PAYMENT_TRANSITIONS[self.status]:
            raise InvalidStateTransitionError(
                f"Payment cannot transition from {self.status.value} to {new_status.value}"
            )
        self.status = new_status
