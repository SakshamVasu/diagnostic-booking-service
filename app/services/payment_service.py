import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.db.utils import violated_constraint
from app.models.booking import Booking, BookingStatus
from app.models.payment import Payment, PaymentStatus
from app.models.user import User
from app.services import booking_service
from app.services.payment_provider import PaymentProvider

logger = logging.getLogger(__name__)

PAYMENT_IN_PROGRESS_MESSAGE = "A payment for this booking is already in progress"

_BOOKING_STATUS_FOR_PAYMENT = {
    PaymentStatus.SUCCESS: BookingStatus.CONFIRMED,
    PaymentStatus.FAILED: BookingStatus.FAILED,
}


def apply_payment_result(payment: Payment, booking: Booking, result: PaymentStatus) -> None:
    """Move a PENDING payment to its final status and the booking along with it.

    Both transitions are validated by their state machines; an invalid one raises
    ``InvalidStateTransitionError`` and the caller's transaction must be rolled back.
    """
    if result == PaymentStatus.PENDING:
        return
    payment.transition_to(result)
    booking.transition_to(_BOOKING_STATUS_FOR_PAYMENT[result])


def _ensure_payable(db: Session, booking: Booking) -> None:
    if booking.status == BookingStatus.CONFIRMED:
        raise ConflictError("Booking is already paid")
    if booking.status != BookingStatus.PENDING:
        raise ConflictError(f"Booking is {booking.status.value} and cannot be paid")
    if booking.appointment_datetime <= datetime.now(UTC):
        raise ConflictError("Booking appointment time has passed")
    if booking_service.has_pending_payment(db, booking.id):
        raise ConflictError(PAYMENT_IN_PROGRESS_MESSAGE)
    booking_service.expire_if_hold_elapsed(db, booking)


def create_payment(db: Session, user: User, booking_id: int, provider: PaymentProvider) -> Payment:
    # Only the owner may pay (admins included), and the row lock serialises concurrent
    # payment attempts for the same booking: the second one sees the first's result.
    booking = booking_service.get_booking(db, user, booking_id, allow_admin=False, for_update=True)
    _ensure_payable(db, booking)

    # The charged amount always comes from the booking, never from the client.
    result = provider.charge(booking.amount, reference=f"booking_{booking.id}")
    payment = Payment(
        booking=booking,
        provider=provider.name,
        provider_payment_id=result.provider_payment_id,
        amount=booking.amount,
        status=PaymentStatus.PENDING,
    )
    apply_payment_result(payment, booking, result.status)
    db.add(payment)
    try:
        db.commit()
    except IntegrityError as exc:
        # Backstop for the partial unique index: never two PENDING/SUCCESS payments per booking.
        db.rollback()
        if violated_constraint(exc) == "uq_payments_active_booking":
            raise ConflictError(PAYMENT_IN_PROGRESS_MESSAGE) from exc
        raise
    return payment


def list_booking_payments(db: Session, user: User, booking_id: int) -> list[Payment]:
    """Every payment attempt for a booking the user may access (the owner, or an admin)."""
    booking = booking_service.get_booking(db, user, booking_id)
    return list(db.scalars(select(Payment).where(Payment.booking_id == booking.id).order_by(Payment.id)))


# --- Reconciliation of payments stuck in PENDING --------------------------------------------


def reconcile_payment(db: Session, payment_id: int, provider: PaymentProvider) -> Payment:
    """Settle a PENDING payment whose webhook never arrived.

    Asks the provider for the charge's status and applies a final result. If the provider
    still reports it as pending after ``PAYMENT_PENDING_TIMEOUT_MINUTES``, the charge is
    treated as abandoned and marked FAILED, which releases the booking. Should the provider
    later report SUCCESS after all, that webhook is rejected (FAILED is final) and shows up
    in the admin webhook log for manual follow-up.
    """
    booking_id = db.scalar(select(Payment.booking_id).where(Payment.id == payment_id))
    if booking_id is None:
        raise NotFoundError("Payment not found")
    # Row lock order (booking, then payment) matches payment creation, cancellation and webhooks.
    booking = db.scalar(select(Booking).where(Booking.id == booking_id).with_for_update())
    payment = db.scalar(
        select(Payment).where(Payment.id == payment_id).with_for_update().execution_options(populate_existing=True)
    )
    if booking is None or payment is None:  # deleted in between; the FK makes this unlikely
        raise NotFoundError("Payment not found")
    if payment.status != PaymentStatus.PENDING:
        raise ConflictError(f"Payment is already {payment.status.value}; nothing to reconcile")

    reported = provider.get_status(payment.provider_payment_id)
    if reported != PaymentStatus.PENDING:
        apply_payment_result(payment, booking, reported)
        logger.info("Reconciled payment %s from provider: %s", payment.provider_payment_id, reported.value)
    else:
        timeout = timedelta(minutes=get_settings().payment_pending_timeout_minutes)
        if payment.created_at + timeout > datetime.now(UTC):
            raise ConflictError(
                "The provider hasn't settled this payment yet. It can be marked as failed once it has "
                f"been pending for {get_settings().payment_pending_timeout_minutes} minutes."
            )
        apply_payment_result(payment, booking, PaymentStatus.FAILED)
        logger.warning("Payment %s abandoned after timeout; marked FAILED", payment.provider_payment_id)
    db.commit()
    return payment


@dataclass
class MaintenanceReport:
    expired_bookings: int = 0
    reconciled_payments: int = 0
    reconcile_errors: list[str] = field(default_factory=list)


def run_maintenance(db: Session, provider: PaymentProvider) -> MaintenanceReport:
    """Expire lapsed unpaid holds and settle payments stuck in PENDING past the timeout.

    Safe to run repeatedly and concurrently with API traffic (every change goes through the
    same row locks and state machines as the API). Intended for a scheduler or the admin UI.
    """
    report = MaintenanceReport()
    report.expired_bookings = booking_service.expire_stale_bookings(db)
    db.commit()

    timeout = timedelta(minutes=get_settings().payment_pending_timeout_minutes)
    stuck = db.scalars(
        select(Payment.id).where(Payment.status == PaymentStatus.PENDING, Payment.created_at <= func.now() - timeout)
    ).all()
    for payment_id in stuck:
        try:
            reconcile_payment(db, payment_id, provider)
            report.reconciled_payments += 1
        except AppError as exc:  # e.g. settled by a webhook in the meantime
            db.rollback()
            report.reconcile_errors.append(f"payment {payment_id}: {exc.detail}")
    return report
