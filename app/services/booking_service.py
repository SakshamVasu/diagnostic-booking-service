from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, and_, exists, func, not_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.exceptions import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.db.utils import paginate, violated_constraint
from app.models.booking import ACTIVE_BOOKING_STATUSES, Booking, BookingStatus
from app.models.centre import CentreTest
from app.models.payment import Payment, PaymentStatus
from app.models.user import User
from app.schemas.booking import BookingCreate
from app.schemas.common import PaginationParams
from app.services import centre_service, diagnostic_test_service
from app.services.payment_provider import PaymentProvider

MAX_BOOKING_HORIZON = timedelta(days=365)
MAX_BOOKED_TIMES_WINDOW = timedelta(days=31)
SLOT_TAKEN_MESSAGE = "This time slot is already booked for this test at this centre"
BOOKING_NOT_FOUND_MESSAGE = "Booking not found"
PAYMENT_IN_PROGRESS_CANCEL_MESSAGE = "Booking has a payment in progress and cannot be cancelled"


def to_utc(value: datetime) -> datetime:
    """Naive datetimes are interpreted as UTC; aware ones are converted to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _validate_appointment_time(appointment: datetime, now: datetime) -> None:
    if appointment <= now:
        raise BadRequestError("Appointment must be in the future")
    if appointment > now + MAX_BOOKING_HORIZON:
        raise BadRequestError(f"Appointment cannot be more than {MAX_BOOKING_HORIZON.days} days in the future")


# --- Unpaid-hold expiry ----------------------------------------------------------------


def _stale_hold_condition() -> ColumnElement[bool] | None:
    """SQL condition matching PENDING bookings whose unpaid hold has run out.

    A booking with a payment still in progress is never stale: the provider may yet
    capture the money. Measured against the database clock, like ``created_at``.
    """
    hold = get_settings().booking_hold_minutes
    if hold == 0:
        return None
    payment_in_progress = exists().where(Payment.booking_id == Booking.id, Payment.status == PaymentStatus.PENDING)
    return and_(
        Booking.status == BookingStatus.PENDING,
        Booking.created_at <= func.now() - timedelta(minutes=hold),
        not_(payment_in_progress),
    )


def expire_stale_bookings(
    db: Session, *, centre_id: int | None = None, test_id: int | None = None, appointment: datetime | None = None
) -> int:
    """Move unpaid PENDING bookings past their hold to EXPIRED, releasing their slots.

    Optionally scoped to one slot. Does not commit: the caller owns the transaction.
    Concurrent callers serialise on the row locks taken by UPDATE, and a row that another
    transaction has meanwhile paid or cancelled no longer matches and is skipped.
    """
    stale = _stale_hold_condition()
    if stale is None:
        return 0
    stmt = update(Booking).where(stale).values(status=BookingStatus.EXPIRED, updated_at=func.now())
    if centre_id is not None:
        stmt = stmt.where(Booking.centre_id == centre_id)
    if test_id is not None:
        stmt = stmt.where(Booking.test_id == test_id)
    if appointment is not None:
        stmt = stmt.where(Booking.appointment_datetime == appointment)
    result = db.execute(stmt.execution_options(synchronize_session=False))
    return result.rowcount or 0


def expire_if_hold_elapsed(db: Session, booking: Booking) -> None:
    """For a row-locked booking: expire it (and commit) if its unpaid hold has run out."""
    if booking.hold_expired(datetime.now(UTC)) and not has_pending_payment(db, booking.id):
        booking.transition_to(BookingStatus.EXPIRED)
        db.commit()
        raise ConflictError("This booking's reservation has expired because it wasn't paid in time. Please book again.")


# --- Creating and reading ----------------------------------------------------------------


def create_booking(db: Session, user: User, data: BookingCreate) -> Booking:
    appointment = to_utc(data.appointment_datetime)
    _validate_appointment_time(appointment, datetime.now(UTC))

    centre_service.get_centre(db, data.centre_id)
    diagnostic_test_service.get_test(db, data.test_id)
    offering = db.scalar(
        select(CentreTest).where(CentreTest.centre_id == data.centre_id, CentreTest.test_id == data.test_id)
    )
    if offering is None:
        raise BadRequestError("The selected centre does not offer the selected test")

    # An abandoned unpaid hold on this exact slot must not block a new patient.
    expire_stale_bookings(db, centre_id=data.centre_id, test_id=data.test_id, appointment=appointment)

    booking = Booking(
        user_id=user.id,
        test_id=data.test_id,
        centre_id=data.centre_id,
        appointment_datetime=appointment,
        amount=offering.price,  # price is always determined server-side
        status=BookingStatus.PENDING,
    )
    db.add(booking)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if violated_constraint(exc) == "uq_bookings_active_slot":
            raise ConflictError(SLOT_TAKEN_MESSAGE) from exc
        raise
    return booking


def list_bookings(
    db: Session, user: User, params: PaginationParams, status: BookingStatus | None = None
) -> tuple[list[Booking], int]:
    stmt = (
        select(Booking)
        .options(
            selectinload(Booking.user),
            selectinload(Booking.test),
            selectinload(Booking.centre),
            selectinload(Booking.payments),
        )
        .order_by(Booking.created_at.desc(), Booking.id.desc())
    )
    if not user.is_admin:
        stmt = stmt.where(Booking.user_id == user.id)
    if status is not None:
        stmt = stmt.where(Booking.status == status)
    return paginate(db, stmt, params)


def get_booking(
    db: Session, user: User, booking_id: int, *, allow_admin: bool = True, for_update: bool = False
) -> Booking:
    """Fetch a booking the user may access.

    Bookings owned by someone else are reported as "not found" (never 403) so that
    booking ids cannot be probed for existence (IDOR protection). ``for_update`` takes a
    row lock that serialises concurrent state changes on the booking.
    """
    stmt = select(Booking).where(Booking.id == booking_id)
    if not (allow_admin and user.is_admin):
        stmt = stmt.where(Booking.user_id == user.id)
    if for_update:
        stmt = stmt.with_for_update()
    booking = db.scalar(stmt)
    if booking is None:
        raise NotFoundError(BOOKING_NOT_FOUND_MESSAGE)
    return booking


def has_pending_payment(db: Session, booking_id: int) -> bool:
    return bool(
        db.scalar(select(exists().where(Payment.booking_id == booking_id, Payment.status == PaymentStatus.PENDING)))
    )


def booked_times(db: Session, centre_id: int, test_id: int, start: datetime, end: datetime) -> list[datetime]:
    """Appointment times already held for this centre and test in ``[start, end)``.

    Read-only: unpaid holds that have run out are reported as free (they are expired for
    real by the next booking of that slot, or by the maintenance job).
    """
    start, end = to_utc(start), to_utc(end)
    if end <= start:
        raise BadRequestError("end must be after start")
    if end - start > MAX_BOOKED_TIMES_WINDOW:
        raise BadRequestError(f"The window can be at most {MAX_BOOKED_TIMES_WINDOW.days} days")
    centre_service.get_offering(db, centre_id, test_id)

    stmt = select(Booking.appointment_datetime).where(
        Booking.centre_id == centre_id,
        Booking.test_id == test_id,
        Booking.status.in_(ACTIVE_BOOKING_STATUSES),
        Booking.appointment_datetime >= start,
        Booking.appointment_datetime < end,
    )
    stale = _stale_hold_condition()
    if stale is not None:
        stmt = stmt.where(not_(stale))
    return list(db.scalars(stmt.order_by(Booking.appointment_datetime)))


# --- Changing bookings ---------------------------------------------------------------------


def _ensure_patient_may_change(user: User, booking: Booking, action: str) -> None:
    """Patients may change a CONFIRMED booking only until the cancellation window opens."""
    if user.is_admin or booking.status != BookingStatus.CONFIRMED:
        return
    if datetime.now(UTC) >= booking.changeable_until:
        hours = get_settings().cancellation_window_hours
        raise ForbiddenError(
            f"Confirmed bookings can only be {action} online up to {hours} hours before the appointment. "
            "Please contact the centre."
        )


def cancel_booking(db: Session, user: User, booking_id: int, provider: PaymentProvider) -> Booking:
    """Cancel a booking.

    Anyone may cancel their PENDING bookings. A CONFIRMED booking is cancelled with a full
    refund, by an admin at any time or by the patient until the cancellation window opens.
    """
    booking = get_booking(db, user, booking_id, for_update=True)
    # A payment awaiting its provider result could still succeed; cancelling now would
    # leave a captured payment against a cancelled booking.
    if booking.status == BookingStatus.PENDING and has_pending_payment(db, booking.id):
        raise ConflictError(PAYMENT_IN_PROGRESS_CANCEL_MESSAGE)
    if booking.status == BookingStatus.CONFIRMED:
        _ensure_patient_may_change(user, booking, "cancelled")
        _refund_successful_payment(db, booking, provider)
    booking.transition_to(BookingStatus.CANCELLED)
    db.commit()
    return booking


def _refund_successful_payment(db: Session, booking: Booking, provider: PaymentProvider) -> None:
    # Row lock order (booking, then payment) matches payment creation and webhooks.
    payment = db.scalar(
        select(Payment)
        .where(Payment.booking_id == booking.id, Payment.status == PaymentStatus.SUCCESS)
        .with_for_update()
    )
    if payment is None:  # a CONFIRMED booking always has a successful payment
        raise ConflictError("No successful payment found to refund for this booking")
    provider.refund(payment.provider_payment_id, payment.amount)
    payment.transition_to(PaymentStatus.REFUNDED)


def reschedule_booking(db: Session, user: User, booking_id: int, new_time: datetime) -> Booking:
    """Move a PENDING or CONFIRMED booking to another time at the same centre, for the same test.

    The price paid (or due) is kept. The partial unique index still guarantees the new slot
    can't be double-booked, even under concurrent requests.
    """
    booking = get_booking(db, user, booking_id, for_update=True)
    if booking.status not in ACTIVE_BOOKING_STATUSES:
        raise ConflictError(f"A {booking.status.value} booking cannot be rescheduled")
    if booking.status == BookingStatus.PENDING:
        if has_pending_payment(db, booking.id):
            raise ConflictError("Booking has a payment in progress and cannot be rescheduled")
        expire_if_hold_elapsed(db, booking)
    _ensure_patient_may_change(user, booking, "rescheduled")

    appointment = to_utc(new_time)
    _validate_appointment_time(appointment, datetime.now(UTC))
    if appointment == booking.appointment_datetime:
        return booking

    expire_stale_bookings(db, centre_id=booking.centre_id, test_id=booking.test_id, appointment=appointment)
    booking.appointment_datetime = appointment
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if violated_constraint(exc) == "uq_bookings_active_slot":
            raise ConflictError(SLOT_TAKEN_MESSAGE) from exc
        raise
    return booking


def record_attendance(db: Session, user: User, booking_id: int, status: BookingStatus) -> Booking:
    """Admin: mark a CONFIRMED booking whose appointment time has passed as COMPLETED or NO_SHOW."""
    booking = get_booking(db, user, booking_id, for_update=True)
    if booking.status == BookingStatus.CONFIRMED and booking.appointment_datetime > datetime.now(UTC):
        raise ConflictError("Attendance can only be recorded once the appointment time has passed")
    booking.transition_to(status)  # only CONFIRMED -> COMPLETED / NO_SHOW is allowed
    db.commit()
    return booking
