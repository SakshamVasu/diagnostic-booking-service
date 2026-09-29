from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.booking import BookingStatus
from app.models.user import UserRole
from app.models.webhook import WebhookOutcome
from app.schemas.common import PatchModel


class AdminUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    role: UserRole
    is_active: bool
    booking_count: int
    created_at: datetime


class AdminUserUpdate(PatchModel):
    role: UserRole | None = None
    is_active: bool | None = Field(default=None, description="Deactivated users can't sign in or use their tokens.")


class WebhookEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: str
    event_type: str
    provider_payment_id: str
    booking_id: int
    reported_status: str
    reported_amount: Decimal
    payload: dict[str, Any]
    processed: bool
    outcome: WebhookOutcome | None
    outcome_detail: str | None
    received_at: datetime
    processed_at: datetime | None


class CentreStats(BaseModel):
    centre_id: int
    centre_name: str
    location: str
    bookings: int
    revenue: Decimal


class TestStats(BaseModel):
    test_id: int
    test_name: str
    bookings: int


class AdminStats(BaseModel):
    days: int = Field(description="Bookings and payments created in the last `days` days are counted.")
    since: datetime
    bookings_total: int
    bookings_by_status: dict[BookingStatus, int]
    upcoming_confirmed: int = Field(description="CONFIRMED appointments still ahead (regardless of `days`).")
    revenue: Decimal = Field(description="Successful payments that have not been refunded.")
    refunded: Decimal
    payments_succeeded: int
    payments_failed: int
    payments_pending: int
    payment_success_rate: float | None = Field(description="Share of settled payments that succeeded (0-1).")
    patients: int
    centres: list[CentreStats]
    top_tests: list[TestStats]


class MaintenanceResult(BaseModel):
    expired_bookings: int
    reconciled_payments: int
    reconcile_errors: list[str]
