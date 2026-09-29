"""Idempotent processing of payment-provider webhooks.

Everything for one event happens in a single database transaction:

1. *Claim* the event: ``INSERT ... ON CONFLICT (event_id) DO NOTHING``. The UNIQUE
   constraint on ``event_id`` guarantees only one transaction can ever insert a given
   event. If an identical request is being processed concurrently, PostgreSQL blocks
   our insert until that transaction finishes: if it committed we get "conflict" and
   treat the event as a duplicate; if it rolled back our insert succeeds and we process
   the event ourselves. Duplicates therefore never re-run business logic.
2. Lock the booking and payment rows (``SELECT ... FOR UPDATE``, always booking first,
   the same order used by payment creation and cancellation, so no deadlocks).
3. Validate (payment exists, belongs to the booking, amount matches) and apply the
   state transitions inside a SAVEPOINT.
4. Record the outcome on the event row and commit.

If anything fails unexpectedly the whole transaction rolls back, the event is *not*
recorded, and a provider retry will process it from scratch.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.exceptions import AppError, BadRequestError, NotFoundError
from app.models.booking import Booking
from app.models.payment import Payment, PaymentStatus
from app.models.webhook import WebhookEvent, WebhookOutcome
from app.schemas.webhook import PaymentWebhookPayload, WebhookAck
from app.services.payment_service import apply_payment_result

logger = logging.getLogger(__name__)


def process_payment_webhook(db: Session, payload: PaymentWebhookPayload) -> WebhookAck:
    event = claim_event(db, payload)
    if event is None:
        return _duplicate_ack(db, payload.event_id)

    try:
        with db.begin_nested():  # rejected events roll back any partial state change
            outcome, detail = _apply_event(db, payload)
    except AppError as exc:
        _record_outcome(event, WebhookOutcome.REJECTED, exc.detail)
        db.commit()  # keep the audit record of the rejected event
        logger.warning("Rejected webhook event %s: %s", payload.event_id, exc.detail)
        raise

    _record_outcome(event, outcome, detail)
    db.commit()
    logger.info("Processed webhook event %s: %s", payload.event_id, outcome.value)
    return WebhookAck(event_id=event.event_id, outcome=outcome, duplicate=False, detail=detail)


def claim_event(db: Session, payload: PaymentWebhookPayload) -> WebhookEvent | None:
    """Insert the event row, or return None if this event_id was already claimed."""
    stmt = (
        insert(WebhookEvent)
        .values(
            event_id=payload.event_id,
            event_type=payload.event_type,
            provider_payment_id=payload.payment_id,
            booking_id=payload.booking_id,
            reported_status=payload.status,
            reported_amount=payload.amount,
            payload=payload.model_dump(mode="json"),
            processed=False,
        )
        .on_conflict_do_nothing(index_elements=[WebhookEvent.event_id])
        .returning(WebhookEvent.id)
    )
    event_pk = db.scalar(stmt)
    return None if event_pk is None else db.get(WebhookEvent, event_pk)


def _duplicate_ack(db: Session, event_id: str) -> WebhookAck:
    existing = db.scalars(select(WebhookEvent).where(WebhookEvent.event_id == event_id)).one()
    logger.info("Ignoring duplicate webhook event %s", event_id)
    return WebhookAck(
        event_id=event_id,
        outcome=existing.outcome or WebhookOutcome.REJECTED,
        duplicate=True,
        detail="Event already processed",
    )


def _apply_event(db: Session, payload: PaymentWebhookPayload) -> tuple[WebhookOutcome, str]:
    booking = db.scalar(select(Booking).where(Booking.id == payload.booking_id).with_for_update())
    if booking is None:
        raise NotFoundError("Booking not found")
    payment = db.scalar(select(Payment).where(Payment.provider_payment_id == payload.payment_id).with_for_update())
    if payment is None:
        raise NotFoundError("Payment not found")
    if payment.booking_id != booking.id:
        raise BadRequestError("Payment does not belong to the given booking")
    if payload.amount != payment.amount:
        logger.warning(
            "Webhook amount mismatch for payment %s: expected %s, received %s (event %s)",
            payment.provider_payment_id,
            payment.amount,
            payload.amount,
            payload.event_id,
        )
        raise BadRequestError("Amount does not match the payment amount")

    reported_status = PaymentStatus(payload.status)
    if payment.status == reported_status:
        # e.g. the provider re-sends a SUCCESS under a new event id: nothing to change.
        return WebhookOutcome.NO_OP, f"Payment already {payment.status.value}"

    apply_payment_result(payment, booking, reported_status)  # raises on invalid transitions
    return WebhookOutcome.APPLIED, f"Payment {payment.status.value}; booking {booking.status.value}"


def _record_outcome(event: WebhookEvent, outcome: WebhookOutcome, detail: str) -> None:
    event.processed = True
    event.outcome = outcome
    event.outcome_detail = detail
    event.processed_at = datetime.now(UTC)
