from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from app.models.booking import BookingStatus
from app.models.payment import PaymentStatus
from app.schemas.common import InputModel


class PaymentCreate(InputModel):
    """The amount is never accepted from the client: it is taken from the booking."""

    booking_id: PositiveInt = Field(examples=[123])


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    booking_id: int
    provider: str
    provider_payment_id: str
    amount: Decimal
    status: PaymentStatus
    booking_status: BookingStatus
    created_at: datetime
    updated_at: datetime
