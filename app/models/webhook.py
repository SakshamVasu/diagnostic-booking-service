from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, String, Text, false, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import MONEY, Base, str_enum


class WebhookOutcome(StrEnum):
    APPLIED = "APPLIED"  # the event changed payment/booking state
    NO_OP = "NO_OP"  # valid, but state already reflected it (e.g. SUCCESS for a paid payment)
    REJECTED = "REJECTED"  # invalid (unknown payment, amount mismatch, illegal transition, ...)


class WebhookEvent(Base):
    """Ledger of every signed webhook event received. The UNIQUE constraint on ``event_id``
    is what makes processing idempotent: an event can be claimed exactly once."""

    __tablename__ = "webhook_events"
    __table_args__ = (Index("ix_webhook_events_provider_payment_id", "provider_payment_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # Raw values as reported by the provider. booking_id is intentionally not a foreign
    # key: rejected events may reference bookings that do not exist.
    provider_payment_id: Mapped[str] = mapped_column(String(100), nullable=False)
    booking_id: Mapped[int] = mapped_column(nullable=False)
    reported_status: Mapped[str] = mapped_column(String(20), nullable=False)
    reported_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    outcome: Mapped[WebhookOutcome | None] = mapped_column(str_enum(WebhookOutcome, "webhook_outcome"))
    outcome_detail: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
