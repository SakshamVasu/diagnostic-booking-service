"""Admin reporting, user management, the webhook ledger and payment reconciliation."""

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import PaymentSimulationMode
from app.models.booking import Booking
from app.models.payment import Payment, PaymentStatus
from app.models.user import User, UserRole
from tests.helpers import Headers, send_webhook, webhook_payload

ADMIN_GETS = ["/admin/stats", "/admin/users", "/admin/webhook-events"]


@pytest.mark.parametrize("path", ADMIN_GETS)
def test_admin_endpoints_require_admin(client: TestClient, user_headers: Headers, path: str) -> None:
    assert client.get(path).status_code == 401
    assert client.get(path, headers=user_headers).status_code == 403


def test_admin_actions_require_admin(client: TestClient, user_headers: Headers) -> None:
    assert client.post("/admin/maintenance/run", headers=user_headers).status_code == 403
    assert client.post("/admin/payments/1/reconcile", headers=user_headers).status_code == 403
    assert client.patch("/admin/users/1", json={"is_active": False}, headers=user_headers).status_code == 403


# --- Stats -----------------------------------------------------------------------------------


def test_stats_summarise_bookings_revenue_and_payments(
    client: TestClient,
    admin_headers: Headers,
    user_headers: Headers,
    catalogue: dict[str, Any],
    create_booking: Callable[..., dict],
    set_payment_mode: Callable[[PaymentSimulationMode], None],
) -> None:
    paid = create_booking(hour=9)
    client.post("/payments/", json={"booking_id": paid["id"]}, headers=user_headers)
    refunded = create_booking(hour=10)
    client.post("/payments/", json={"booking_id": refunded["id"]}, headers=user_headers)
    client.patch(f"/bookings/{refunded['id']}/cancel", headers=admin_headers)
    failed = create_booking(hour=11)
    set_payment_mode(PaymentSimulationMode.FAILURE)
    client.post("/payments/", json={"booking_id": failed["id"]}, headers=user_headers)
    create_booking(hour=12)  # unpaid

    response = client.get("/admin/stats", headers=admin_headers)

    assert response.status_code == 200
    stats = response.json()
    assert stats["bookings_total"] == 4
    assert stats["bookings_by_status"] == {
        "PENDING": 1,
        "CONFIRMED": 1,
        "FAILED": 1,
        "CANCELLED": 1,
        "EXPIRED": 0,
        "COMPLETED": 0,
        "NO_SHOW": 0,
    }
    assert stats["revenue"] == "750.00"
    assert stats["refunded"] == "750.00"
    assert (stats["payments_succeeded"], stats["payments_failed"], stats["payments_pending"]) == (2, 1, 0)
    assert stats["payment_success_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert stats["upcoming_confirmed"] == 1
    assert stats["patients"] == 1
    assert stats["centres"] == [
        {
            "centre_id": catalogue["centre"]["id"],
            "centre_name": catalogue["centre"]["name"],
            "location": catalogue["centre"]["location"],
            "bookings": 4,
            "revenue": "750.00",
        }
    ]
    assert stats["top_tests"] == [
        {"test_id": catalogue["test"]["id"], "test_name": catalogue["test"]["name"], "bookings": 4}
    ]


def test_stats_on_empty_system(client: TestClient, admin_headers: Headers) -> None:
    stats = client.get("/admin/stats?days=7", headers=admin_headers).json()
    assert stats["days"] == 7
    assert stats["bookings_total"] == 0
    assert stats["revenue"] == "0.00"
    assert stats["payment_success_rate"] is None
    assert client.get("/admin/stats?days=0", headers=admin_headers).status_code == 422


# --- Users -----------------------------------------------------------------------------------


def test_list_users_with_search_and_booking_counts(
    client: TestClient, admin_headers: Headers, user: tuple[User, Headers], create_booking: Callable[..., dict]
) -> None:
    patient, _ = user
    create_booking()

    page = client.get("/admin/users", params={"search": patient.email.upper()}, headers=admin_headers).json()

    assert page["total"] == 1
    assert page["items"][0]["email"] == patient.email
    assert page["items"][0]["booking_count"] == 1
    assert page["items"][0]["is_active"] is True
    assert "password_hash" not in page["items"][0]
    admins = client.get("/admin/users", params={"role": "ADMIN"}, headers=admin_headers).json()
    assert {item["role"] for item in admins["items"]} == {"ADMIN"}


def test_deactivated_user_loses_access_and_can_be_reactivated(
    client: TestClient, admin_headers: Headers, user: tuple[User, Headers]
) -> None:
    patient, headers = user

    response = client.patch(f"/admin/users/{patient.id}", json={"is_active": False}, headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["is_active"] is False
    assert client.get("/auth/me", headers=headers).status_code == 401
    client.patch(f"/admin/users/{patient.id}", json={"is_active": True}, headers=admin_headers)
    assert client.get("/auth/me", headers=headers).status_code == 200


def test_admin_can_promote_a_user(client: TestClient, admin_headers: Headers, user: tuple[User, Headers]) -> None:
    patient, headers = user

    client.patch(f"/admin/users/{patient.id}", json={"role": "ADMIN"}, headers=admin_headers)

    assert client.get("/admin/stats", headers=headers).status_code == 200


def test_admin_cannot_lock_themselves_out(client: TestClient, make_user: Callable[..., tuple[User, Headers]]) -> None:
    admin, headers = make_user(UserRole.ADMIN)

    for change in ({"is_active": False}, {"role": "USER"}):
        response = client.patch(f"/admin/users/{admin.id}", json=change, headers=headers)
        assert response.status_code == 409
    assert client.get("/admin/stats", headers=headers).status_code == 200


def test_update_unknown_user_and_invalid_body(client: TestClient, admin_headers: Headers) -> None:
    assert client.patch("/admin/users/9999", json={"is_active": False}, headers=admin_headers).status_code == 404
    assert client.patch("/admin/users/1", json={"role": None}, headers=admin_headers).status_code == 422
    assert client.patch("/admin/users/1", json={"role": "ROOT"}, headers=admin_headers).status_code == 422


# --- Webhook ledger --------------------------------------------------------------------------


def test_webhook_ledger_lists_events_and_filters_rejections(
    client: TestClient, admin_headers: Headers, pending_payment: dict[str, Any]
) -> None:
    send_webhook(client, webhook_payload(pending_payment, event_id="evt_bad", amount="1.00"))
    send_webhook(client, webhook_payload(pending_payment, event_id="evt_good"))

    all_events = client.get("/admin/webhook-events", headers=admin_headers).json()
    rejected = client.get("/admin/webhook-events", params={"outcome": "REJECTED"}, headers=admin_headers).json()
    searched = client.get("/admin/webhook-events", params={"search": "good"}, headers=admin_headers).json()

    assert all_events["total"] == 2
    assert [e["event_id"] for e in rejected["items"]] == ["evt_bad"]
    assert rejected["items"][0]["outcome_detail"] == "Amount does not match the payment amount"
    assert rejected["items"][0]["payload"]["amount"] == "1.00"
    assert [e["outcome"] for e in searched["items"]] == ["APPLIED"]


# --- Reconciliation --------------------------------------------------------------------------


def _age_payment(db: Session, payment_id: int, minutes: int) -> None:
    db.execute(
        update(Payment).where(Payment.id == payment_id).values(created_at=func.now() - timedelta(minutes=minutes))
    )
    db.commit()


def _statuses(db: Session, payment_id: int) -> tuple[str, str]:
    db.expire_all()
    payment = db.get(Payment, payment_id)
    assert payment is not None
    booking = db.scalar(select(Booking).where(Booking.id == payment.booking_id))
    assert booking is not None
    return payment.status.value, booking.status.value


def test_reconcile_applies_the_providers_final_status(
    client: TestClient,
    admin_headers: Headers,
    pending_payment: dict[str, Any],
    set_payment_mode: Callable[[PaymentSimulationMode], None],
    db: Session,
) -> None:
    set_payment_mode(PaymentSimulationMode.SUCCESS)  # the provider has now settled the charge

    response = client.post(f"/admin/payments/{pending_payment['id']}/reconcile", headers=admin_headers)

    assert response.status_code == 200
    assert (response.json()["status"], response.json()["booking_status"]) == ("SUCCESS", "CONFIRMED")
    assert _statuses(db, pending_payment["id"]) == ("SUCCESS", "CONFIRMED")


def test_reconcile_refuses_a_recent_unresolved_payment(
    client: TestClient, admin_headers: Headers, pending_payment: dict[str, Any], db: Session
) -> None:
    response = client.post(f"/admin/payments/{pending_payment['id']}/reconcile", headers=admin_headers)

    assert response.status_code == 409
    assert _statuses(db, pending_payment["id"]) == ("PENDING", "PENDING")


def test_reconcile_marks_a_timed_out_payment_failed(
    client: TestClient, admin_headers: Headers, pending_payment: dict[str, Any], db: Session
) -> None:
    _age_payment(db, pending_payment["id"], minutes=20)

    response = client.post(f"/admin/payments/{pending_payment['id']}/reconcile", headers=admin_headers)

    assert response.status_code == 200
    assert _statuses(db, pending_payment["id"]) == ("FAILED", "FAILED")


def test_late_success_webhook_after_abandonment_is_rejected_and_logged(
    client: TestClient, admin_headers: Headers, pending_payment: dict[str, Any], db: Session
) -> None:
    _age_payment(db, pending_payment["id"], minutes=20)
    client.post(f"/admin/payments/{pending_payment['id']}/reconcile", headers=admin_headers)

    late = send_webhook(client, webhook_payload(pending_payment, event_id="evt_late"))

    assert late.status_code == 409
    rejected = client.get("/admin/webhook-events", params={"outcome": "REJECTED"}, headers=admin_headers).json()
    assert [e["event_id"] for e in rejected["items"]] == ["evt_late"]


def test_reconcile_settled_or_unknown_payment(
    client: TestClient, admin_headers: Headers, user_headers: Headers, create_booking: Callable[..., dict]
) -> None:
    booking = create_booking()
    payment = client.post("/payments/", json={"booking_id": booking["id"]}, headers=user_headers).json()

    assert client.post(f"/admin/payments/{payment['id']}/reconcile", headers=admin_headers).status_code == 409
    assert client.post("/admin/payments/9999/reconcile", headers=admin_headers).status_code == 404


def test_maintenance_reconciles_only_timed_out_payments(
    client: TestClient,
    admin_headers: Headers,
    user_headers: Headers,
    create_booking: Callable[..., dict],
    set_payment_mode: Callable[[PaymentSimulationMode], None],
    db: Session,
) -> None:
    set_payment_mode(PaymentSimulationMode.PENDING)
    old = client.post("/payments/", json={"booking_id": create_booking(hour=9)["id"]}, headers=user_headers).json()
    new = client.post("/payments/", json={"booking_id": create_booking(hour=10)["id"]}, headers=user_headers).json()
    _age_payment(db, old["id"], minutes=30)

    report = client.post("/admin/maintenance/run", headers=admin_headers).json()

    assert report == {"expired_bookings": 0, "reconciled_payments": 1, "reconcile_errors": []}
    assert _statuses(db, old["id"]) == ("FAILED", "FAILED")
    assert _statuses(db, new["id"]) == ("PENDING", "PENDING")
    assert db.scalar(select(func.count()).select_from(Payment).where(Payment.status == PaymentStatus.PENDING)) == 1
