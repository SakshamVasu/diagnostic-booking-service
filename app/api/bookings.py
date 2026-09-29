from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from app.api.common import NOT_FOUND, Pagination
from app.dependencies.auth import AdminUser, CurrentUser, DbSession
from app.dependencies.payments import PaymentProviderDep
from app.models.booking import Booking, BookingStatus
from app.models.payment import Payment
from app.schemas.booking import AttendanceUpdate, BookingCreate, BookingRead, BookingReschedule
from app.schemas.common import ErrorResponse, Page
from app.schemas.payment import PaymentRead
from app.services import booking_service, payment_service

router = APIRouter(prefix="/bookings", tags=["Bookings"])

BookingId = Annotated[int, Path(gt=0, description="Booking id")]


@router.post(
    "/",
    response_model=BookingRead,
    status_code=status.HTTP_201_CREATED,
    summary="Book a diagnostic test at a centre",
    description="The amount is taken from the centre's price for the test; the booking starts as PENDING.",
    responses={
        400: {"model": ErrorResponse, "description": "Past appointment or test not offered by centre"},
        404: {"model": ErrorResponse, "description": "Centre or test not found"},
        409: {"model": ErrorResponse, "description": "Slot already booked"},
    },
)
def create_booking(data: BookingCreate, db: DbSession, user: CurrentUser) -> Booking:
    return booking_service.create_booking(db, user, data)


@router.get(
    "/",
    response_model=Page[BookingRead],
    summary="List bookings",
    description="Users see only their own bookings; admins see all bookings.",
)
def list_bookings(
    db: DbSession,
    user: CurrentUser,
    pagination: Pagination,
    status_filter: Annotated[BookingStatus | None, Query(alias="status", description="Filter by status")] = None,
) -> Page[BookingRead]:
    items, total = booking_service.list_bookings(db, user, pagination, status=status_filter)
    return Page[BookingRead].build([BookingRead.model_validate(b) for b in items], total, pagination)


@router.get("/{booking_id}", response_model=BookingRead, summary="Get one of your bookings", responses=NOT_FOUND)
def get_booking(booking_id: BookingId, db: DbSession, user: CurrentUser) -> Booking:
    return booking_service.get_booking(db, user, booking_id)


@router.patch(
    "/{booking_id}/cancel",
    response_model=BookingRead,
    summary="Cancel a booking",
    description=(
        "Patients can cancel their own PENDING bookings, and their CONFIRMED bookings up to "
        "CANCELLATION_WINDOW_HOURS before the appointment. Admins can cancel any PENDING or CONFIRMED "
        "booking at any time. Cancelling a CONFIRMED booking refunds its payment (payment status REFUNDED)."
    ),
    responses={
        **NOT_FOUND,
        403: {"model": ErrorResponse, "description": "Too close to the appointment for the patient to cancel"},
        409: {"model": ErrorResponse, "description": "Booking cannot be cancelled"},
    },
)
def cancel_booking(booking_id: BookingId, db: DbSession, user: CurrentUser, provider: PaymentProviderDep) -> Booking:
    return booking_service.cancel_booking(db, user, booking_id, provider)


@router.patch(
    "/{booking_id}/reschedule",
    response_model=BookingRead,
    summary="Move a booking to another time",
    description=(
        "Moves a PENDING or CONFIRMED booking to another time at the same centre, for the same test, "
        "keeping its price and payment. Patients can reschedule CONFIRMED bookings up to "
        "CANCELLATION_WINDOW_HOURS before the appointment; admins at any time."
    ),
    responses={
        **NOT_FOUND,
        400: {"model": ErrorResponse, "description": "New time is in the past or too far ahead"},
        403: {"model": ErrorResponse, "description": "Too close to the appointment for the patient to change it"},
        409: {"model": ErrorResponse, "description": "Slot taken, booking not active, or payment in progress"},
    },
)
def reschedule_booking(booking_id: BookingId, data: BookingReschedule, db: DbSession, user: CurrentUser) -> Booking:
    return booking_service.reschedule_booking(db, user, booking_id, data.appointment_datetime)


@router.patch(
    "/{booking_id}/attendance",
    response_model=BookingRead,
    summary="Record whether the patient attended (admin)",
    description="Marks a CONFIRMED booking whose appointment time has passed as COMPLETED or NO_SHOW.",
    responses={**NOT_FOUND, 409: {"model": ErrorResponse, "description": "Not confirmed, or still in the future"}},
)
def record_attendance(booking_id: BookingId, data: AttendanceUpdate, db: DbSession, admin: AdminUser) -> Booking:
    return booking_service.record_attendance(db, admin, booking_id, BookingStatus(data.status))


@router.get(
    "/{booking_id}/payments",
    response_model=list[PaymentRead],
    summary="Payment attempts for a booking",
    description="Every payment for the booking, oldest first, including refunds. Owner or admin only.",
    responses=NOT_FOUND,
)
def list_booking_payments(booking_id: BookingId, db: DbSession, user: CurrentUser) -> list[Payment]:
    return payment_service.list_booking_payments(db, user, booking_id)
