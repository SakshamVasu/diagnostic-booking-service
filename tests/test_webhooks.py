"""Payment webhook: authentication, validation, state transitions and, above all, idempotency."""

import json
import logging
import threading
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.db.database import SessionLocal
from app.main import app
from app.models.booking import Booking, BookingStatus
from app.models.payment import Payment, PaymentStatus
from app.models.webhook import WebhookEvent, WebhookOutcome
from app.schemas.webhook import PaymentWebhookPayload, WebhookAck
from app.services import webhook_service
from tests.helpers import WEBHOOK_URL, Headers, send_webhook, signed_headers, webhook_payload


def _count(db: Session, model: type, *where: Any) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*where)) or 0


def _state(db: Session, payment: dict[str, Any]) -> tuple[PaymentStatus, BookingStatus]:
    db.expire_all()
    stored_payment = db.scalar(select(Payment).where(Payment.provider_payment_id == payment["provider_payment_id"]))
    stored_booking = db.get(Booking, payment["booking_id"])
    assert stored_payment is not None and stored_booking is not None
    return stored_payment.status, stored_booking.status


def _event(db: Session, event_id: str) -> WebhookEvent:
    db.expire_all()
    event = db.scalar(select(WebhookEvent).where(WebhookEvent.event_id == event_id))
    assert event is not None
    return event


# --- Happy paths ------------------------------------------------------------------


def test_success_webhook_confirms_booking(client: TestClient, pending_payment: dict[str, Any], db: Session) -> None:
    response = send_webhook(client, webhook_payload(pending_payment, status="SUCCESS"))

    assert response.status_code == 200
    assert response.json() == {
        "event_id": "evt_1",
        "outcome": "APPLIED",
        "duplicate": False,
        "detail": "Payment SUCCESS; booking CONFIRMED",
    }
    assert _state(db, pending_payment) == (PaymentStatus.SUCCESS, BookingStatus.CONFIRMED)
    event = _event(db, "evt_1")
    assert event.processed is True
    assert event.processed_at is not None
    assert event.event_type == "payment.success"
    assert event.outcome == WebhookOutcome.APPLIED


def test_failed_webhook_fails_booking(client: TestClient, pending_payment: dict[str, Any], db: Session) -> None:
    response = send_webhook(client, webhook_payload(pending_payment, status="FAILED"))

    assert response.status_code == 200
    assert response.json()["outcome"] == "APPLIED"
    assert _state(db, pending_payment) == (PaymentStatus.FAILED, BookingStatus.FAILED)
    assert _event(db, "evt_1").event_type == "payment.failed"


def test_amount_accepts_equivalent_decimal_representations(client: TestClient, pending_payment: dict[str, Any]) -> None:
    response = send_webhook(client, webhook_payload(pending_payment, amount=750))  # booking amount is "750.00"
    assert response.status_code == 200
    assert response.json()["outcome"] == "APPLIED"


# --- Idempotency -------------------------------------------------------------------


@pytest.mark.parametrize("deliveries", [2, 10])
def test_redelivered_webhook_is_processed_once(
    client: TestClient, pending_payment: dict[str, Any], db: Session, deliveries: int
) -> None:
    payload = webhook_payload(pending_payment)

    responses = [send_webhook(client, payload) for _ in range(deliveries)]

    assert all(r.status_code == 200 for r in responses)
    assert responses[0].json()["duplicate"] is False
    assert all(r.json()["duplicate"] is True for r in responses[1:])
    assert all(r.json()["outcome"] == "APPLIED" for r in responses)
    assert _count(db, WebhookEvent) == 1
    assert _count(db, Payment, Payment.booking_id == pending_payment["booking_id"]) == 1
    assert _count(db, Booking) == 1
    assert _state(db, pending_payment) == (PaymentStatus.SUCCESS, BookingStatus.CONFIRMED)


def test_redelivered_event_is_not_reprocessed_even_if_payload_changes(
    client: TestClient, pending_payment: dict[str, Any], db: Session
) -> None:
    send_webhook(client, webhook_payload(pending_payment, event_id="evt_same", status="SUCCESS"))

    replay = send_webhook(client, webhook_payload(pending_payment, event_id="evt_same", status="FAILED"))

    assert replay.status_code == 200
    assert replay.json()["duplicate"] is True
    assert _state(db, pending_payment) == (PaymentStatus.SUCCESS, BookingStatus.CONFIRMED)


def test_new_event_reporting_already_applied_status_is_a_no_op(
    client: TestClient, pending_payment: dict[str, Any], db: Session
) -> None:
    send_webhook(client, webhook_payload(pending_payment, event_id="evt_1"))

    response = send_webhook(client, webhook_payload(pending_payment, event_id="evt_2"))

    assert response.status_code == 200
    assert response.json()["outcome"] == "NO_OP"
    assert response.json()["duplicate"] is False
    assert _count(db, WebhookEvent) == 2
    assert _state(db, pending_payment) == (PaymentStatus.SUCCESS, BookingStatus.CONFIRMED)


def test_webhook_for_synchronously_completed_payment_is_a_no_op(
    client: TestClient, user_headers: Headers, create_booking: Callable[..., dict], db: Session
) -> None:
    booking = create_booking()
    payment = client.post("/payments/", json={"booking_id": booking["id"]}, headers=user_headers).json()
    assert payment["status"] == "SUCCESS"

    response = send_webhook(client, webhook_payload(payment))

    assert response.json()["outcome"] == "NO_OP"
    assert _state(db, payment) == (PaymentStatus.SUCCESS, BookingStatus.CONFIRMED)


def test_concurrent_duplicate_webhooks_are_processed_exactly_once(pending_payment: dict[str, Any], db: Session) -> None:
    payload = PaymentWebhookPayload(**webhook_payload(pending_payment, event_id="evt_race"))
    workers = 10
    barrier = threading.Barrier(workers)
    acks: list[WebhookAck] = []
    errors: list[BaseException] = []

    def deliver() -> None:
        with SessionLocal() as session:
            barrier.wait()
            try:
                acks.append(webhook_service.process_payment_webhook(session, payload))
            except BaseException as exc:
                errors.append(exc)

    threads = [threading.Thread(target=deliver) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert sum(not ack.duplicate for ack in acks) == 1
    assert sum(ack.duplicate for ack in acks) == workers - 1
    assert {ack.outcome for ack in acks} == {WebhookOutcome.APPLIED}
    assert _count(db, WebhookEvent) == 1
    assert _count(db, Payment) == 1
    assert _state(db, pending_payment) == (PaymentStatus.SUCCESS, BookingStatus.CONFIRMED)


def test_duplicate_waits_for_in_flight_event_then_is_ignored(pending_payment: dict[str, Any], db: Session) -> None:
    """Deterministic interleaving: transaction A has claimed the event but not committed.
    Transaction B, delivering the same event, must block on the unique index and then
    treat the event as a duplicate once A commits, never processing it a second time."""
    payload = PaymentWebhookPayload(**webhook_payload(pending_payment, event_id="evt_inflight"))
    first = SessionLocal()
    second_result: list[WebhookAck] = []

    def deliver_second() -> None:
        with SessionLocal() as second:
            second_result.append(webhook_service.process_payment_webhook(second, payload))

    try:
        assert webhook_service.claim_event(first, payload) is not None  # A holds the claim

        thread = threading.Thread(target=deliver_second)
        thread.start()
        thread.join(timeout=1.0)
        assert thread.is_alive(), "second delivery should block while the first is uncommitted"

        first.commit()  # A finishes
        thread.join(timeout=10)
        assert not thread.is_alive()
    finally:
        first.close()

    assert second_result[0].duplicate is True
    assert _count(db, WebhookEvent) == 1
    # B did not apply the event; only A (which here only claimed it) could have.
    assert _state(db, pending_payment) == (PaymentStatus.PENDING, BookingStatus.PENDING)


def test_competing_success_and_failure_events_leave_consistent_state(
    pending_payment: dict[str, Any], db: Session
) -> None:
    payloads = [
        PaymentWebhookPayload(**webhook_payload(pending_payment, event_id="evt_ok", status="SUCCESS")),
        PaymentWebhookPayload(**webhook_payload(pending_payment, event_id="evt_fail", status="FAILED")),
    ]
    barrier = threading.Barrier(len(payloads))
    outcomes: list[str] = []

    def deliver(payload: PaymentWebhookPayload) -> None:
        with SessionLocal() as session:
            barrier.wait()
            try:
                outcomes.append(webhook_service.process_payment_webhook(session, payload).outcome.value)
            except AppError as exc:
                outcomes.append(str(exc.status_code))

    threads = [threading.Thread(target=deliver, args=(p,)) for p in payloads]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # Row locks serialise the two events: the first wins, the second is an invalid transition.
    assert sorted(outcomes) == ["409", "APPLIED"]
    payment_status, booking_status = _state(db, pending_payment)
    assert (payment_status, booking_status) in {
        (PaymentStatus.SUCCESS, BookingStatus.CONFIRMED),
        (PaymentStatus.FAILED, BookingStatus.FAILED),
    }


def test_event_is_not_recorded_when_processing_crashes_so_retry_succeeds(
    pending_payment: dict[str, Any], db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = webhook_payload(pending_payment, event_id="evt_crash")

    def explode(*_: Any) -> None:
        raise RuntimeError("database went away")

    monkeypatch.setattr(webhook_service, "apply_payment_result", explode)
    with TestClient(app, raise_server_exceptions=False) as crashing_client:
        crashed = send_webhook(crashing_client, payload)
    assert crashed.status_code == 500
    assert crashed.json() == {"detail": "Internal server error"}  # no internals leaked
    assert _count(db, WebhookEvent) == 0
    assert _state(db, pending_payment) == (PaymentStatus.PENDING, BookingStatus.PENDING)

    monkeypatch.undo()
    with TestClient(app) as healthy_client:
        retried = send_webhook(healthy_client, payload)

    assert retried.status_code == 200
    assert retried.json() == {**retried.json(), "outcome": "APPLIED", "duplicate": False}
    assert _state(db, pending_payment) == (PaymentStatus.SUCCESS, BookingStatus.CONFIRMED)


# --- Validation & rejection ----------------------------------------------------------


def test_amount_mismatch_is_rejected_and_logged(
    client: TestClient, pending_payment: dict[str, Any], db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="app.services.webhook_service"):
        response = send_webhook(client, webhook_payload(pending_payment, event_id="evt_cheap", amount="1.00"))

    assert response.status_code == 400
    assert response.json() == {"detail": "Amount does not match the payment amount"}
    assert _state(db, pending_payment) == (PaymentStatus.PENDING, BookingStatus.PENDING)
    event = _event(db, "evt_cheap")
    assert event.outcome == WebhookOutcome.REJECTED
    assert event.outcome_detail == "Amount does not match the payment amount"
    assert "amount mismatch" in caplog.text.lower()


def test_rejected_event_redelivery_is_acknowledged_without_reprocessing(
    client: TestClient, pending_payment: dict[str, Any], db: Session
) -> None:
    payload = webhook_payload(pending_payment, event_id="evt_bad", amount="1.00")
    send_webhook(client, payload)

    replay = send_webhook(client, payload)

    assert replay.status_code == 200
    assert replay.json()["duplicate"] is True
    assert replay.json()["outcome"] == "REJECTED"
    assert _count(db, WebhookEvent) == 1
    assert _state(db, pending_payment) == (PaymentStatus.PENDING, BookingStatus.PENDING)


def test_unknown_payment_id_is_rejected(client: TestClient, pending_payment: dict[str, Any], db: Session) -> None:
    response = send_webhook(client, webhook_payload(pending_payment, event_id="evt_x", payment_id="pay_unknown"))

    assert response.status_code == 404
    assert response.json() == {"detail": "Payment not found"}
    assert _event(db, "evt_x").outcome == WebhookOutcome.REJECTED
    assert _state(db, pending_payment) == (PaymentStatus.PENDING, BookingStatus.PENDING)


def test_unknown_booking_id_is_rejected(client: TestClient, pending_payment: dict[str, Any], db: Session) -> None:
    response = send_webhook(client, webhook_payload(pending_payment, booking_id=999_999))

    assert response.status_code == 404
    assert response.json() == {"detail": "Booking not found"}
    assert _state(db, pending_payment) == (PaymentStatus.PENDING, BookingStatus.PENDING)


def test_payment_belonging_to_a_different_booking_is_rejected(
    client: TestClient, pending_payment: dict[str, Any], create_booking: Callable[..., dict], db: Session
) -> None:
    other_booking = create_booking(days=9)

    response = send_webhook(client, webhook_payload(pending_payment, booking_id=other_booking["id"]))

    assert response.status_code == 400
    assert response.json() == {"detail": "Payment does not belong to the given booking"}
    assert _state(db, pending_payment) == (PaymentStatus.PENDING, BookingStatus.PENDING)


@pytest.mark.parametrize(
    ("first", "second"),
    [("SUCCESS", "FAILED"), ("FAILED", "SUCCESS")],
    ids=["success-then-failed", "failed-then-success"],
)
def test_invalid_payment_state_transition_is_rejected(
    client: TestClient, pending_payment: dict[str, Any], db: Session, first: str, second: str
) -> None:
    send_webhook(client, webhook_payload(pending_payment, event_id="evt_first", status=first))
    state_after_first = _state(db, pending_payment)

    response = send_webhook(client, webhook_payload(pending_payment, event_id="evt_second", status=second))

    assert response.status_code == 409
    assert response.json() == {"detail": f"Payment cannot transition from {first} to {second}"}
    assert _state(db, pending_payment) == state_after_first
    assert _event(db, "evt_second").outcome == WebhookOutcome.REJECTED


def test_success_for_cancelled_booking_is_rejected_without_partial_update(
    client: TestClient, pending_payment: dict[str, Any], db: Session
) -> None:
    # Cancellation is blocked while a payment is pending, so force the inconsistent state
    # directly to prove the webhook still cannot resurrect a cancelled booking.
    booking = db.get(Booking, pending_payment["booking_id"])
    assert booking is not None
    booking.status = BookingStatus.CANCELLED
    db.commit()

    response = send_webhook(client, webhook_payload(pending_payment))

    assert response.status_code == 409
    assert response.json() == {"detail": "Booking cannot transition from CANCELLED to CONFIRMED"}
    # The payment transition that happened first was rolled back with the savepoint.
    assert _state(db, pending_payment) == (PaymentStatus.PENDING, BookingStatus.CANCELLED)


def test_webhook_cannot_reconfirm_refunded_payment(
    client: TestClient, admin_headers: Headers, pending_payment: dict[str, Any], db: Session
) -> None:
    send_webhook(client, webhook_payload(pending_payment, event_id="evt_paid"))
    client.patch(f"/bookings/{pending_payment['booking_id']}/cancel", headers=admin_headers)

    response = send_webhook(client, webhook_payload(pending_payment, event_id="evt_late_success"))

    assert response.status_code == 409
    assert response.json() == {"detail": "Payment cannot transition from REFUNDED to SUCCESS"}
    assert _state(db, pending_payment) == (PaymentStatus.REFUNDED, BookingStatus.CANCELLED)


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "REFUNDED"},
        {"amount": -750},
        {"amount": 0},
        {"amount": "750.001"},
        {"event_id": ""},
        {"event_id": "evt with spaces"},
        {"booking_id": 0},
        {"payment_id": ""},
    ],
)
def test_malformed_webhook_payload_is_rejected(
    client: TestClient, pending_payment: dict[str, Any], db: Session, overrides: dict[str, Any]
) -> None:
    response = send_webhook(client, webhook_payload(pending_payment, **overrides))

    assert response.status_code == 422
    assert _count(db, WebhookEvent) == 0


# --- Authentication ------------------------------------------------------------------


def test_webhook_without_signature_is_rejected(
    client: TestClient, pending_payment: dict[str, Any], db: Session
) -> None:
    response = client.post(WEBHOOK_URL, json=webhook_payload(pending_payment))

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid webhook signature"}
    assert _count(db, WebhookEvent) == 0
    assert _state(db, pending_payment) == (PaymentStatus.PENDING, BookingStatus.PENDING)


def test_webhook_with_forged_or_mismatched_signature_is_rejected(
    client: TestClient, pending_payment: dict[str, Any], db: Session
) -> None:
    genuine = json.dumps(webhook_payload(pending_payment, amount="750.00")).encode()
    tampered = json.dumps(webhook_payload(pending_payment, event_id="evt_tampered")).encode()

    forged = client.post(
        WEBHOOK_URL, content=genuine, headers={"Content-Type": "application/json", "X-Webhook-Signature": "0" * 64}
    )
    replayed_signature = client.post(WEBHOOK_URL, content=tampered, headers=signed_headers(genuine))

    assert forged.status_code == 401
    assert replayed_signature.status_code == 401
    assert _count(db, WebhookEvent) == 0
