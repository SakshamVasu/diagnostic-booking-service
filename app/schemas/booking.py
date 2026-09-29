from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, field_validator

from app.models.booking import BookingStatus
from app.schemas.common import InputModel


def reject_numeric_timestamps(value: Any) -> Any:
    # Pydantic would otherwise accept numbers as Unix timestamps (e.g. 12345 -> 1970).
    if isinstance(value, int | float):
        raise ValueError("must be an ISO-8601 date-time string")
    return value


class BookingCreate(InputModel):
    """Only the booking's *what/where/when*. Owner, amount and status are always derived
    server-side; any such fields sent by the client are ignored."""

    test_id: PositiveInt = Field(examples=[1])
    centre_id: PositiveInt = Field(examples=[2])
    appointment_datetime: datetime = Field(
        description="ISO-8601 date-time. Values without a timezone are interpreted as UTC.",
        examples=["2026-10-05T10:30:00Z"],
    )

    reject_numeric = field_validator("appointment_datetime", mode="before")(reject_numeric_timestamps)


class BookingReschedule(InputModel):
    appointment_datetime: datetime = Field(
        description="The new ISO-8601 date-time. Values without a timezone are interpreted as UTC.",
        examples=["2026-10-06T09:00:00Z"],
    )

    reject_numeric = field_validator("appointment_datetime", mode="before")(reject_numeric_timestamps)


class AttendanceUpdate(InputModel):
    status: Literal["COMPLETED", "NO_SHOW"] = Field(
        description="Whether the patient attended (COMPLETED) or missed (NO_SHOW) the appointment."
    )


class BookingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    patient_name: str
    patient_email: str
    test_id: int
    test_name: str
    centre_id: int
    centre_name: str
    centre_location: str
    appointment_datetime: datetime
    amount: Decimal
    status: BookingStatus
    payment_in_progress: bool = Field(description="A payment is awaiting the provider's final result.")
    hold_expires_at: datetime | None = Field(
        description="For unpaid PENDING bookings: when the slot is released if still unpaid."
    )
    changeable_until: datetime = Field(
        description="Last moment the patient can cancel or reschedule this booking themselves."
    )
    created_at: datetime
    updated_at: datetime


class BookedTimes(BaseModel):
    """Appointment times already held at a centre for a test, within the requested window."""

    centre_id: int
    test_id: int
    start: datetime
    end: datetime
    booked: list[datetime]
