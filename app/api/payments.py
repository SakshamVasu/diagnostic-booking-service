from fastapi import APIRouter, status

from app.dependencies.auth import CurrentUser, DbSession
from app.dependencies.payments import PaymentProviderDep
from app.models.payment import Payment
from app.schemas.common import ErrorResponse
from app.schemas.payment import PaymentCreate, PaymentRead
from app.services import payment_service

router = APIRouter(prefix="/payments", tags=["Payments"])


@router.post(
    "/",
    response_model=PaymentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Pay for one of your bookings (simulated provider)",
    description=(
        "Charges the booking's amount through the simulated provider. The resulting payment is "
        "SUCCESS (booking CONFIRMED), FAILED (booking FAILED) or, in `pending` simulation mode, "
        "PENDING until a webhook reports the final result."
    ),
    responses={
        404: {"model": ErrorResponse, "description": "Booking not found"},
        409: {"model": ErrorResponse, "description": "Booking is not payable"},
    },
)
def create_payment(data: PaymentCreate, db: DbSession, user: CurrentUser, provider: PaymentProviderDep) -> Payment:
    return payment_service.create_payment(db, user, data.booking_id, provider)
