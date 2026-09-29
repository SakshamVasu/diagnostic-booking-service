from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, PositiveInt

from app.models.webhook import WebhookOutcome


class PaymentWebhookPayload(BaseModel):
    event_id: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9_\-.:]+$", examples=["evt_123456"])
    payment_id: str = Field(min_length=1, max_length=100, description="The provider payment id", examples=["pay_123"])
    booking_id: PositiveInt = Field(examples=[123])
    status: Literal["SUCCESS", "FAILED"]
    amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2, examples=[750])

    @property
    def event_type(self) -> str:
        return f"payment.{self.status.lower()}"


class WebhookAck(BaseModel):
    event_id: str
    outcome: WebhookOutcome
    duplicate: bool = Field(description="True when this event_id had already been processed")
    detail: str | None = None
