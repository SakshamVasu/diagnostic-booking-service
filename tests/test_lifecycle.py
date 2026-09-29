"""Unpaid-hold expiry, rescheduling, attendance, booked times and payment history."""

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import PaymentSimulationMode
from app.core.exceptions import AppError
from app.db.database import SessionLocal
from app.models.booking import Booking, BookingStatus
from app.models.user import User
from app.schemas.booking import BookingCreate
from app.schemas.common import PaginationParams
from app.services import booking_service
from tests.helpers import Headers, future_datetime


def _age_booking(db: Session, booking_id: int, minutes: int) -> None:
    """Pretend the booking was created ``minutes`` ago (the hold is measured from created_at)."""
    db.execute(
        update(Booking).where(Booking.id == booking_id).values(created_at=func.now() - timedelta(minutes=minutes))
    )
    db.commit()


def _status(db: Session, booking_id: int) -> BookingStatus:
    db.expire_all()
    return db.scalar(select(Booking.status).where(Booking.id == booking_id))


def _slot(booking: dict[str, Any]) -> dict[str, Any]:
    return {
        "test_id": booking["test_id"],
        "centre_id": booking["centre_id"],
        "appointment_datetime": booking["appointment_datetime"],
    }


# --- Booking details in responses ------------------------------------------------------


def test_booking_includes_names_and_deadlines(
    client: TestClient, user_headers: Headers, catalogue: dict[str, Any], create_booking: Callable[..., dict]
) -> None:
    booking = create_booking(days=7)

    assert booking["test_name"] == catalogue["test"]["name"]
    assert booking["centre_name"] == catalogue["centre"]["name"]
    assert booking["centre_location"] == catalogue["centre"]["location"]
    created = datetime.fromisoformat(booking["created_at"])
    assert datetime.fromisoformat(booking["hold_expires_at"]) == created + timedelta(minutes=30)
    appointment = datetime.fromisoformat(booking["appointment_datetime"])
    assert datetime.fromisoformat(booking["changeable_until"]) == appointment - timedelta(hours=24)

    client.post("/payments/", json={"booking_id": booking["id"]}, headers=user_headers)
    paid = client.get(f"/bookings/{booking['id']}", headers=user_headers).json()
    assert paid["hold_expires_at"] is None  # only unpaid bookings have a hold


# --- Unpaid-hold expiry ----------------------------------------------------------------


def test_stale_unpaid_booking_is_expired_when_someone_books_the_slot(
    client: TestClient,
    user_headers: Headers,
    other_user_headers: Headers,
    create_booking: Callable[..., dict],
    db: Session,
) -> None:
    booking = create_booking()
    assert client.post("/bookings/", json=_slot(booking), headers=other_user_headers).status_code == 409

    _age_booking(db, booking["id"], minutes=31)
    response = client.post("/bookings/", json=_slot(booking), headers=other_user_headers)

    assert response.status_code == 201
    assert _status(db, booking["id"]) == BookingStatus.EXPIRED
    assert client.get(f"/bookings/{booking['id']}", headers=user_headers).json()["status"] == "EXPIRED"


def test_fresh_unpaid_booking_still_holds_its_slot(
    client: TestClient, other_user_headers: Headers, create_booking: Callable[..., dict], db: Session
) -> None:
    booking = create_booking()
    _age_booking(db, booking["id"], minutes=29)

    assert client.post("/bookings/", json=_slot(booking), headers=other_user_headers).status_code == 409
    assert _status(db, booking["id"]) == BookingStatus.PENDING


def test_paying_after_the_hold_expired_is_refused(
    client: TestClient, user_headers: Headers, create_booking: Callable[..., dict], db: Session
) -> None:
    booking = create_booking()
    _age_booking(db, booking["id"], minutes=45)

    response = client.post("/payments/", json={"booking_id": booking["id"]}, headers=user_headers)

    assert response.status_code == 409
    assert "expired" in response.json()["detail"]
    assert _status(db, booking["id"]) == BookingStatus.EXPIRED
    assert client.get(f"/bookings/{booking['id']}/payments", headers=user_headers).json() == []


def test_booking_with_payment_in_progress_reports_no_hold(
    client: TestClient, user_headers: Headers, pending_payment: dict[str, Any]
) -> None:
    booking = client.get(f"/bookings/{pending_payment['booking_id']}", headers=user_headers).json()
    assert booking["payment_in_progress"] is True
    assert booking["hold_expires_at"] is None
    listed = client.get("/bookings/", headers=user_headers).json()["items"][0]
    assert listed["payment_in_progress"] is True


def test_booking_with_payment_in_progress_never_expires(
    client: TestClient, other_user_headers: Headers, pending_payment: dict[str, Any], db: Session
) -> None:
    booking_id = pending_payment["booking_id"]
    _age_booking(db, booking_id, minutes=120)
    booking = db.get(Booking, booking_id)
    assert booking is not None
    slot = {
        "test_id": booking.test_id,
        "centre_id": booking.centre_id,
        "appointment_datetime": booking.appointment_datetime.isoformat(),
    }

    assert client.post("/bookings/", json=slot, headers=other_user_headers).status_code == 409
    assert _status(db, booking_id) == BookingStatus.PENDING


def test_maintenance_expires_every_stale_booking(
    client: TestClient, admin_headers: Headers, create_booking: Callable[..., dict], db: Session
) -> None:
    stale = [create_booking(hour=9), create_booking(hour=11)]
    fresh = create_booking(hour=13)
    for booking in stale:
        _age_booking(db, booking["id"], minutes=60)

    response = client.post("/admin/maintenance/run", headers=admin_headers)

    assert response.status_code == 200
    assert response.json() == {"expired_bookings": 2, "reconciled_payments": 0, "reconcile_errors": []}
    assert [_status(db, b["id"]) for b in stale] == [BookingStatus.EXPIRED] * 2
    assert _status(db, fresh["id"]) == BookingStatus.PENDING


def test_expiry_can_be_disabled(
    client: TestClient,
    other_user_headers: Headers,
    create_booking: Callable[..., dict],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "booking_hold_minutes", 0)
    booking = create_booking()
    _age_booking(db, booking["id"], minutes=24 * 60)

    assert client.post("/bookings/", json=_slot(booking), headers=other_user_headers).status_code == 409
    assert _status(db, booking["id"]) == BookingStatus.PENDING


def test_concurrent_bookings_of_an_expired_slot_only_one_succeeds(
    catalogue: dict[str, Any], create_booking: Callable[..., dict], make_user: Callable[..., Any], db: Session
) -> None:
    stale = create_booking()
    _age_booking(db, stale["id"], minutes=60)
    users = [make_user()[0] for _ in range(5)]
    barrier = threading.Barrier(len(users))
    results: list[str] = []

    def attempt(user_id: int) -> None:
        with SessionLocal() as session:
            user = session.get(User, user_id)
            barrier.wait()
            try:
                booking_service.create_booking(session, user, BookingCreate(**_slot(stale)))
                results.append("ok")
            except AppError as exc:
                results.append(str(exc.status_code))

    threads = [threading.Thread(target=attempt, args=(user.id,)) for user in users]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == ["409"] * 4 + ["ok"]
    assert _status(db, stale["id"]) == BookingStatus.EXPIRED


# --- Rescheduling ------------------------------------------------------------------------


def test_patient_can_reschedule_pending_booking(
    client: TestClient, user_headers: Headers, create_booking: Callable[..., dict]
) -> None:
    booking = create_booking(days=7, hour=10)
    new_time = future_datetime(days=8, hour=15)

    response = client.patch(
        f"/bookings/{booking['id']}/reschedule", json={"appointment_datetime": new_time}, headers=user_headers
    )

    assert response.status_code == 200
    body = response.json()
    assert datetime.fromisoformat(body["appointment_datetime"]) == datetime.fromisoformat(new_time)
    assert body["status"] == "PENDING"
    assert body["amount"] == booking["amount"]


def test_rescheduling_confirmed_booking_keeps_payment_and_frees_old_slot(
    client: TestClient,
    user_headers: Headers,
    other_user_headers: Headers,
    create_booking: Callable[..., dict],
) -> None:
    booking = create_booking(days=7, hour=10)
    client.post("/payments/", json={"booking_id": booking["id"]}, headers=user_headers)

    response = client.patch(
        f"/bookings/{booking['id']}/reschedule",
        json={"appointment_datetime": future_datetime(days=9, hour=12)},
        headers=user_headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "CONFIRMED"
    payments = client.get(f"/bookings/{booking['id']}/payments", headers=user_headers).json()
    assert [p["status"] for p in payments] == ["SUCCESS"]
    assert client.post("/bookings/", json=_slot(booking), headers=other_user_headers).status_code == 201


def test_reschedule_to_a_taken_slot_conflicts(
    client: TestClient, user_headers: Headers, create_booking: Callable[..., dict]
) -> None:
    first = create_booking(hour=10)
    second = create_booking(hour=12)

    response = client.patch(
        f"/bookings/{second['id']}/reschedule",
        json={"appointment_datetime": first["appointment_datetime"]},
        headers=user_headers,
    )

    assert response.status_code == 409
    assert response.json() == {"detail": booking_service.SLOT_TAKEN_MESSAGE}


def test_reschedule_rejects_past_time(
    client: TestClient, user_headers: Headers, create_booking: Callable[..., dict]
) -> None:
    booking = create_booking()
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    response = client.patch(
        f"/bookings/{booking['id']}/reschedule", json={"appointment_datetime": past}, headers=user_headers
    )

    assert response.status_code == 400


def test_patient_cannot_reschedule_confirmed_booking_inside_window(
    client: TestClient, user_headers: Headers, admin_headers: Headers, catalogue: dict[str, Any]
) -> None:
    soon = (datetime.now(UTC) + timedelta(hours=3)).replace(second=0, microsecond=0).isoformat()
    booking = client.post(
        "/bookings/",
        json={"test_id": catalogue["test"]["id"], "centre_id": catalogue["centre"]["id"], "appointment_datetime": soon},
        headers=user_headers,
    ).json()
    client.post("/payments/", json={"booking_id": booking["id"]}, headers=user_headers)
    body = {"appointment_datetime": future_datetime(days=5)}

    assert client.patch(f"/bookings/{booking['id']}/reschedule", json=body, headers=user_headers).status_code == 403
    assert client.patch(f"/bookings/{booking['id']}/reschedule", json=body, headers=admin_headers).status_code == 200


def test_cannot_reschedule_inactive_or_paying_booking(
    client: TestClient, user_headers: Headers, create_booking: Callable[..., dict], pending_payment: dict[str, Any]
) -> None:
    body = {"appointment_datetime": future_datetime(days=10)}
    in_progress = client.patch(f"/bookings/{pending_payment['booking_id']}/reschedule", json=body, headers=user_headers)
    cancelled = create_booking(hour=15)
    client.patch(f"/bookings/{cancelled['id']}/cancel", headers=user_headers)

    assert in_progress.status_code == 409
    assert client.patch(f"/bookings/{cancelled['id']}/reschedule", json=body, headers=user_headers).status_code == 409


def test_cannot_reschedule_another_users_booking(
    client: TestClient, other_user_headers: Headers, create_booking: Callable[..., dict]
) -> None:
    booking = create_booking()
    response = client.patch(
        f"/bookings/{booking['id']}/reschedule",
        json={"appointment_datetime": future_datetime(days=9)},
        headers=other_user_headers,
    )
    assert response.status_code == 404


# --- Attendance ---------------------------------------------------------------------------


def _confirmed_in_the_past(
    client: TestClient, user_headers: Headers, create_booking: Callable[..., dict], db: Session
) -> dict[str, Any]:
    booking = create_booking()
    client.post("/payments/", json={"booking_id": booking["id"]}, headers=user_headers)
    db.execute(
        update(Booking).where(Booking.id == booking["id"]).values(appointment_datetime=func.now() - timedelta(hours=2))
    )
    db.commit()
    return booking


@pytest.mark.parametrize("outcome", ["COMPLETED", "NO_SHOW"])
def test_admin_records_attendance_after_the_appointment(
    client: TestClient,
    admin_headers: Headers,
    user_headers: Headers,
    create_booking: Callable[..., dict],
    db: Session,
    outcome: str,
) -> None:
    booking = _confirmed_in_the_past(client, user_headers, create_booking, db)

    response = client.patch(f"/bookings/{booking['id']}/attendance", json={"status": outcome}, headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["status"] == outcome


def test_attendance_cannot_be_recorded_before_the_appointment(
    client: TestClient, admin_headers: Headers, user_headers: Headers, create_booking: Callable[..., dict]
) -> None:
    booking = create_booking()
    client.post("/payments/", json={"booking_id": booking["id"]}, headers=user_headers)

    response = client.patch(
        f"/bookings/{booking['id']}/attendance", json={"status": "COMPLETED"}, headers=admin_headers
    )

    assert response.status_code == 409


def test_attendance_requires_confirmed_booking_and_admin(
    client: TestClient, admin_headers: Headers, user_headers: Headers, create_booking: Callable[..., dict]
) -> None:
    booking = create_booking()
    body = {"status": "COMPLETED"}

    assert client.patch(f"/bookings/{booking['id']}/attendance", json=body, headers=user_headers).status_code == 403
    assert client.patch(f"/bookings/{booking['id']}/attendance", json=body, headers=admin_headers).status_code == 409
    bad = client.patch(f"/bookings/{booking['id']}/attendance", json={"status": "CANCELLED"}, headers=admin_headers)
    assert bad.status_code == 422


def test_completed_booking_cannot_be_cancelled(
    client: TestClient,
    admin_headers: Headers,
    user_headers: Headers,
    create_booking: Callable[..., dict],
    db: Session,
) -> None:
    booking = _confirmed_in_the_past(client, user_headers, create_booking, db)
    client.patch(f"/bookings/{booking['id']}/attendance", json={"status": "COMPLETED"}, headers=admin_headers)

    assert client.patch(f"/bookings/{booking['id']}/cancel", headers=admin_headers).status_code == 409


# --- Booked times -------------------------------------------------------------------------


def test_booked_times_lists_active_bookings_only(
    client: TestClient,
    user_headers: Headers,
    catalogue: dict[str, Any],
    create_booking: Callable[..., dict],
    db: Session,
) -> None:
    held = create_booking(days=3, hour=9)
    paid = create_booking(days=3, hour=10)
    client.post("/payments/", json={"booking_id": paid["id"]}, headers=user_headers)
    cancelled = create_booking(days=3, hour=11)
    client.patch(f"/bookings/{cancelled['id']}/cancel", headers=user_headers)
    lapsed = create_booking(days=3, hour=12)
    _age_booking(db, lapsed["id"], minutes=90)
    create_booking(days=5, hour=9)  # outside the window

    day = datetime.fromisoformat(held["appointment_datetime"]).replace(hour=0, minute=0)
    response = client.get(
        f"/centres/{catalogue['centre']['id']}/tests/{catalogue['test']['id']}/booked-times",
        params={"start": day.isoformat(), "end": (day + timedelta(days=1)).isoformat()},
    )

    assert response.status_code == 200
    booked = [datetime.fromisoformat(value) for value in response.json()["booked"]]
    assert booked == [
        datetime.fromisoformat(held["appointment_datetime"]),
        datetime.fromisoformat(paid["appointment_datetime"]),
    ]
    assert "patient" not in response.text


def test_booked_times_validates_window_and_offering(
    client: TestClient, catalogue: dict[str, Any], create_test: Callable[..., dict]
) -> None:
    base = f"/centres/{catalogue['centre']['id']}/tests"
    start = datetime.now(UTC)
    params = {"start": start.isoformat(), "end": (start + timedelta(days=1)).isoformat()}
    other_test = create_test(name="Not offered here")

    assert (
        client.get(
            f"{base}/{catalogue['test']['id']}/booked-times", params={"start": params["end"], "end": params["start"]}
        ).status_code
        == 400
    )
    too_long = {"start": params["start"], "end": (start + timedelta(days=40)).isoformat()}
    assert client.get(f"{base}/{catalogue['test']['id']}/booked-times", params=too_long).status_code == 400
    assert client.get(f"{base}/{other_test['id']}/booked-times", params=params).status_code == 404


# --- Payment history ------------------------------------------------------------------------


def test_payment_history_shows_every_attempt_to_owner_and_admin(
    client: TestClient,
    user_headers: Headers,
    admin_headers: Headers,
    other_user_headers: Headers,
    create_booking: Callable[..., dict],
) -> None:
    booking = create_booking()
    client.post("/payments/", json={"booking_id": booking["id"]}, headers=user_headers)
    client.patch(f"/bookings/{booking['id']}/cancel", headers=admin_headers)

    own = client.get(f"/bookings/{booking['id']}/payments", headers=user_headers)

    assert own.status_code == 200
    assert [(p["status"], p["amount"], p["booking_status"]) for p in own.json()] == [
        ("REFUNDED", booking["amount"], "CANCELLED")
    ]
    assert client.get(f"/bookings/{booking['id']}/payments", headers=admin_headers).json() == own.json()
    assert client.get(f"/bookings/{booking['id']}/payments", headers=other_user_headers).status_code == 404


def test_failed_payment_appears_in_history(
    client: TestClient,
    user_headers: Headers,
    create_booking: Callable[..., dict],
    set_payment_mode: Callable[[PaymentSimulationMode], None],
) -> None:
    booking = create_booking()
    set_payment_mode(PaymentSimulationMode.FAILURE)
    client.post("/payments/", json={"booking_id": booking["id"]}, headers=user_headers)

    history = client.get(f"/bookings/{booking['id']}/payments", headers=user_headers).json()

    assert [p["status"] for p in history] == ["FAILED"]


def test_list_bookings_loads_names_without_errors(
    user_headers: Headers, create_booking: Callable[..., dict], db: Session, user: tuple[Any, Headers]
) -> None:
    create_booking(hour=9)
    create_booking(hour=10)
    items, total = booking_service.list_bookings(db, user[0], PaginationParams(page=1, page_size=10))
    assert total == 2
    assert {item.test_name for item in items} == {"Complete Blood Count"}
