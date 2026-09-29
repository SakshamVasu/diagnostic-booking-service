import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import PaymentSimulationMode
from app.core.exceptions import AppError, InvalidStateTransitionError
from app.db.database import SessionLocal
from app.models.booking import BOOKING_TRANSITIONS, Booking, BookingStatus
from app.models.payment import Payment, PaymentStatus
from app.models.user import User
from app.schemas.booking import BookingCreate
from app.services import booking_service
from tests.helpers import Headers, future_datetime


def _payment_status(db: Session, booking_id: int) -> PaymentStatus:
    db.expire_all()
    payment = db.scalar(select(Payment).where(Payment.booking_id == booking_id))
    assert payment is not None
    return payment.status


def _booking_body(catalogue: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    body = {
        "test_id": catalogue["test"]["id"],
        "centre_id": catalogue["centre"]["id"],
        "appointment_datetime": future_datetime(),
    }
    body.update(overrides)
    return body


# --- Creation ---------------------------------------------------------------------


def test_create_booking_success(client: TestClient, catalogue: dict[str, Any], user: tuple[User, Headers]) -> None:
    user_obj, headers = user

    response = client.post("/bookings/", json=_booking_body(catalogue), headers=headers)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["user_id"] == user_obj.id
    assert body["test_id"] == catalogue["test"]["id"]
    assert body["centre_id"] == catalogue["centre"]["id"]


def test_amount_is_calculated_server_side_from_centre_price(
    client: TestClient, catalogue: dict[str, Any], user: tuple[User, Headers]
) -> None:
    user_obj, headers = user
    tampered = _booking_body(catalogue, amount="1.00", status="CONFIRMED", user_id=999)

    response = client.post("/bookings/", json=tampered, headers=headers)

    assert response.status_code == 201
    body = response.json()
    assert body["amount"] == "750.00"  # centre price, not the 500.00 base price or client value
    assert body["status"] == "PENDING"
    assert body["user_id"] == user_obj.id


def test_booking_uses_price_of_selected_centre(
    client: TestClient,
    user_headers: Headers,
    create_centre: Callable[..., dict[str, Any]],
    create_test: Callable[..., dict[str, Any]],
    offer_test: Callable[..., dict[str, Any]],
) -> None:
    test = create_test()
    centre_a, centre_b = create_centre(name="A"), create_centre(name="B")
    offer_test(centre_a["id"], test["id"], price="500.00")
    offer_test(centre_b["id"], test["id"], price="650.00")

    booking_b = client.post(
        "/bookings/",
        json={"test_id": test["id"], "centre_id": centre_b["id"], "appointment_datetime": future_datetime()},
        headers=user_headers,
    ).json()

    assert booking_b["amount"] == "650.00"


def test_later_price_change_does_not_alter_existing_booking(
    client: TestClient, admin_headers: Headers, user_headers: Headers, create_booking: Callable[..., dict]
) -> None:
    booking = create_booking()
    client.patch(
        f"/centres/{booking['centre_id']}/tests/{booking['test_id']}", json={"price": "999.00"}, headers=admin_headers
    )

    assert client.get(f"/bookings/{booking['id']}", headers=user_headers).json()["amount"] == "750.00"


def test_naive_datetime_is_treated_as_utc_and_offsets_are_normalised(
    client: TestClient, catalogue: dict[str, Any], user_headers: Headers
) -> None:
    day = (datetime.now(UTC) + timedelta(days=3)).date().isoformat()

    naive = client.post(
        "/bookings/", json=_booking_body(catalogue, appointment_datetime=f"{day}T10:30:00"), headers=user_headers
    ).json()
    offset = client.post(
        "/bookings/", json=_booking_body(catalogue, appointment_datetime=f"{day}T16:30:00+05:30"), headers=user_headers
    )

    assert datetime.fromisoformat(naive["appointment_datetime"]) == datetime.fromisoformat(f"{day}T10:30:00+00:00")
    # 16:30 IST is 11:00 UTC: a different slot, so no conflict.
    assert offset.status_code == 201
    assert datetime.fromisoformat(offset.json()["appointment_datetime"]).utcoffset() == timedelta(0)


def test_booking_invalid_test(client: TestClient, catalogue: dict[str, Any], user_headers: Headers) -> None:
    response = client.post("/bookings/", json=_booking_body(catalogue, test_id=999_999), headers=user_headers)
    assert response.status_code == 404
    assert response.json() == {"detail": "Test not found"}


def test_booking_invalid_centre(client: TestClient, catalogue: dict[str, Any], user_headers: Headers) -> None:
    response = client.post("/bookings/", json=_booking_body(catalogue, centre_id=999_999), headers=user_headers)
    assert response.status_code == 404
    assert response.json() == {"detail": "Centre not found"}


def test_booking_test_not_offered_by_centre(
    client: TestClient,
    catalogue: dict[str, Any],
    user_headers: Headers,
    create_test: Callable[..., dict[str, Any]],
) -> None:
    other_test = create_test(name="MRI")

    response = client.post("/bookings/", json=_booking_body(catalogue, test_id=other_test["id"]), headers=user_headers)

    assert response.status_code == 400
    assert response.json() == {"detail": "The selected centre does not offer the selected test"}


@pytest.mark.parametrize(
    "appointment",
    [
        (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        (datetime.now(UTC) - timedelta(days=30)).isoformat(),
        (datetime.now(UTC) + timedelta(days=400)).isoformat(),
    ],
    ids=["just-passed", "last-month", "beyond-booking-horizon"],
)
def test_booking_rejects_past_or_far_future_appointment(
    client: TestClient, catalogue: dict[str, Any], user_headers: Headers, appointment: str
) -> None:
    response = client.post(
        "/bookings/", json=_booking_body(catalogue, appointment_datetime=appointment), headers=user_headers
    )
    assert response.status_code == 400


@pytest.mark.parametrize("appointment", ["tomorrow", "2026-13-45T10:00:00", "", None, 12345])
def test_booking_rejects_malformed_datetime(
    client: TestClient, catalogue: dict[str, Any], user_headers: Headers, appointment: object
) -> None:
    response = client.post(
        "/bookings/", json=_booking_body(catalogue, appointment_datetime=appointment), headers=user_headers
    )
    assert response.status_code == 422


@pytest.mark.parametrize("field", ["test_id", "centre_id"])
@pytest.mark.parametrize("value", [0, -1, "abc", None])
def test_booking_rejects_invalid_ids(
    client: TestClient, catalogue: dict[str, Any], user_headers: Headers, field: str, value: object
) -> None:
    response = client.post("/bookings/", json=_booking_body(catalogue, **{field: value}), headers=user_headers)
    assert response.status_code == 422


def test_booking_requires_authentication(client: TestClient, catalogue: dict[str, Any]) -> None:
    assert client.post("/bookings/", json=_booking_body(catalogue)).status_code == 401


def test_same_slot_cannot_be_double_booked(
    client: TestClient, catalogue: dict[str, Any], user_headers: Headers, other_user_headers: Headers
) -> None:
    body = _booking_body(catalogue)
    assert client.post("/bookings/", json=body, headers=user_headers).status_code == 201

    response = client.post("/bookings/", json=body, headers=other_user_headers)

    assert response.status_code == 409
    assert response.json() == {"detail": booking_service.SLOT_TAKEN_MESSAGE}


def test_cancelled_booking_releases_its_slot(
    client: TestClient, catalogue: dict[str, Any], user_headers: Headers, other_user_headers: Headers
) -> None:
    body = _booking_body(catalogue)
    booking = client.post("/bookings/", json=body, headers=user_headers).json()
    client.patch(f"/bookings/{booking['id']}/cancel", headers=user_headers)

    assert client.post("/bookings/", json=body, headers=other_user_headers).status_code == 201


def test_concurrent_bookings_for_same_slot_only_one_succeeds(
    catalogue: dict[str, Any], make_user: Callable[..., tuple[User, Headers]]
) -> None:
    users = [make_user()[0] for _ in range(5)]
    data = BookingCreate(**_booking_body(catalogue))
    barrier = threading.Barrier(len(users))
    outcomes: list[str] = []

    def attempt(user: User) -> None:
        with SessionLocal() as session:
            barrier.wait()
            try:
                booking_service.create_booking(session, user, data)
                outcomes.append("created")
            except AppError as exc:
                outcomes.append(str(exc.status_code))

    threads = [threading.Thread(target=attempt, args=(u,)) for u in users]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["409", "409", "409", "409", "created"]


# --- Reading & authorization --------------------------------------------------------


def test_get_own_booking(client: TestClient, user_headers: Headers, create_booking: Callable[..., dict]) -> None:
    booking = create_booking()
    response = client.get(f"/bookings/{booking['id']}", headers=user_headers)
    assert response.status_code == 200
    assert response.json() == booking


@pytest.mark.parametrize(("booking_id", "status"), [(999_999, 404), (0, 422), ("abc", 422)])
def test_invalid_booking_id(client: TestClient, user_headers: Headers, booking_id: object, status: int) -> None:
    assert client.get(f"/bookings/{booking_id}", headers=user_headers).status_code == status
    assert client.patch(f"/bookings/{booking_id}/cancel", headers=user_headers).status_code == status


def test_user_cannot_access_another_users_booking(
    client: TestClient, other_user_headers: Headers, create_booking: Callable[..., dict]
) -> None:
    booking = create_booking()

    read = client.get(f"/bookings/{booking['id']}", headers=other_user_headers)
    cancel = client.patch(f"/bookings/{booking['id']}/cancel", headers=other_user_headers)

    # 404 rather than 403, so another user's booking ids cannot even be confirmed to exist.
    assert read.status_code == 404
    assert read.json() == {"detail": "Booking not found"}
    assert cancel.status_code == 404


def test_list_bookings_returns_only_own_bookings(
    client: TestClient, user_headers: Headers, other_user_headers: Headers, create_booking: Callable[..., dict]
) -> None:
    mine = [create_booking(days=1), create_booking(days=2)]
    create_booking(headers=other_user_headers, days=3)

    response = client.get("/bookings/", headers=user_headers)

    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert {b["id"] for b in response.json()["items"]} == {b["id"] for b in mine}


def test_admin_can_view_all_bookings(
    client: TestClient, admin_headers: Headers, other_user_headers: Headers, create_booking: Callable[..., dict]
) -> None:
    create_booking(days=1)
    other = create_booking(headers=other_user_headers, days=2)

    assert client.get("/bookings/", headers=admin_headers).json()["total"] == 2
    assert client.get(f"/bookings/{other['id']}", headers=admin_headers).status_code == 200


def test_list_bookings_filter_by_status_and_paginate(
    client: TestClient, user_headers: Headers, create_booking: Callable[..., dict]
) -> None:
    bookings = [create_booking(days=d) for d in (1, 2, 3)]
    client.patch(f"/bookings/{bookings[0]['id']}/cancel", headers=user_headers)

    pending = client.get("/bookings/?status=PENDING", headers=user_headers).json()
    cancelled = client.get("/bookings/?status=CANCELLED", headers=user_headers).json()
    page = client.get("/bookings/?page=1&page_size=2", headers=user_headers).json()

    assert pending["total"] == 2
    assert [b["id"] for b in cancelled["items"]] == [bookings[0]["id"]]
    assert len(page["items"]) == 2 and page["pages"] == 2
    assert client.get("/bookings/?status=BOGUS", headers=user_headers).status_code == 422


# --- Cancellation & state machine ---------------------------------------------------


def test_cancel_pending_booking(client: TestClient, user_headers: Headers, create_booking: Callable[..., dict]) -> None:
    booking = create_booking()

    response = client.patch(f"/bookings/{booking['id']}/cancel", headers=user_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"


def test_cancelling_twice_is_an_invalid_transition(
    client: TestClient, user_headers: Headers, create_booking: Callable[..., dict]
) -> None:
    booking = create_booking()
    client.patch(f"/bookings/{booking['id']}/cancel", headers=user_headers)

    response = client.patch(f"/bookings/{booking['id']}/cancel", headers=user_headers)

    assert response.status_code == 409
    assert response.json() == {"detail": "Booking cannot transition from CANCELLED to CANCELLED"}


def _confirmed_booking_soon(client: TestClient, catalogue: dict[str, Any], headers: Headers) -> dict[str, Any]:
    """A paid booking two hours from now: inside the default 24-hour cancellation window."""
    soon = (datetime.now(UTC) + timedelta(hours=2)).replace(second=0, microsecond=0).isoformat()
    booking = client.post("/bookings/", json=_booking_body(catalogue, appointment_datetime=soon), headers=headers)
    assert booking.status_code == 201, booking.text
    client.post("/payments/", json={"booking_id": booking.json()["id"]}, headers=headers)
    return booking.json()


def test_patient_can_cancel_confirmed_booking_before_window_with_refund(
    client: TestClient, user_headers: Headers, create_booking: Callable[..., dict], db: Session
) -> None:
    booking = create_booking(days=7)
    client.post("/payments/", json={"booking_id": booking["id"]}, headers=user_headers)

    response = client.patch(f"/bookings/{booking['id']}/cancel", headers=user_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"
    assert _payment_status(db, booking["id"]) == PaymentStatus.REFUNDED


def test_patient_cannot_cancel_confirmed_booking_inside_window(
    client: TestClient, user_headers: Headers, catalogue: dict[str, Any], db: Session
) -> None:
    booking = _confirmed_booking_soon(client, catalogue, user_headers)

    response = client.patch(f"/bookings/{booking['id']}/cancel", headers=user_headers)

    assert response.status_code == 403
    assert response.json() == {
        "detail": "Confirmed bookings can only be cancelled online up to 24 hours before the appointment. "
        "Please contact the centre."
    }
    assert client.get(f"/bookings/{booking['id']}", headers=user_headers).json()["status"] == "CONFIRMED"
    assert _payment_status(db, booking["id"]) == PaymentStatus.SUCCESS


def test_admin_can_cancel_confirmed_booking_inside_window(
    client: TestClient, user_headers: Headers, admin_headers: Headers, catalogue: dict[str, Any], db: Session
) -> None:
    booking = _confirmed_booking_soon(client, catalogue, user_headers)

    response = client.patch(f"/bookings/{booking['id']}/cancel", headers=admin_headers)

    assert response.status_code == 200
    assert _payment_status(db, booking["id"]) == PaymentStatus.REFUNDED


def test_admin_can_cancel_confirmed_booking_and_refund_payment(
    client: TestClient,
    admin_headers: Headers,
    user_headers: Headers,
    other_user_headers: Headers,
    create_booking: Callable[..., dict],
    db: Session,
) -> None:
    booking = create_booking()
    client.post("/payments/", json={"booking_id": booking["id"]}, headers=user_headers)

    response = client.patch(f"/bookings/{booking['id']}/cancel", headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"
    assert _payment_status(db, booking["id"]) == PaymentStatus.REFUNDED
    # The patient sees the cancellation, and the slot is free for someone else.
    assert client.get(f"/bookings/{booking['id']}", headers=user_headers).json()["status"] == "CANCELLED"
    rebook = client.post(
        "/bookings/",
        json={
            "test_id": booking["test_id"],
            "centre_id": booking["centre_id"],
            "appointment_datetime": booking["appointment_datetime"],
        },
        headers=other_user_headers,
    )
    assert rebook.status_code == 201


def test_refunded_booking_cannot_be_paid_again(
    client: TestClient, admin_headers: Headers, user_headers: Headers, create_booking: Callable[..., dict]
) -> None:
    booking = create_booking()
    client.post("/payments/", json={"booking_id": booking["id"]}, headers=user_headers)
    client.patch(f"/bookings/{booking['id']}/cancel", headers=admin_headers)

    response = client.post("/payments/", json={"booking_id": booking["id"]}, headers=user_headers)

    assert response.status_code == 409


def test_admin_cannot_cancel_booking_with_payment_in_progress(
    client: TestClient, admin_headers: Headers, pending_payment: dict[str, Any]
) -> None:
    response = client.patch(f"/bookings/{pending_payment['booking_id']}/cancel", headers=admin_headers)
    assert response.status_code == 409


@pytest.mark.parametrize("final_status", ["FAILED", "CANCELLED"])
def test_admin_cannot_cancel_failed_or_cancelled_booking(
    client: TestClient,
    admin_headers: Headers,
    user_headers: Headers,
    create_booking: Callable[..., dict],
    set_payment_mode: Callable[[PaymentSimulationMode], None],
    final_status: str,
) -> None:
    booking = create_booking()
    if final_status == "FAILED":
        set_payment_mode(PaymentSimulationMode.FAILURE)
        client.post("/payments/", json={"booking_id": booking["id"]}, headers=user_headers)
    else:
        client.patch(f"/bookings/{booking['id']}/cancel", headers=user_headers)

    response = client.patch(f"/bookings/{booking['id']}/cancel", headers=admin_headers)

    assert response.status_code == 409
    assert response.json() == {"detail": f"Booking cannot transition from {final_status} to CANCELLED"}


def test_bookings_include_patient_details(
    client: TestClient, admin_headers: Headers, user: tuple[User, Headers], create_booking: Callable[..., dict]
) -> None:
    patient, _ = user
    create_booking()

    item = client.get("/bookings/", headers=admin_headers).json()["items"][0]

    assert item["patient_name"] == patient.name
    assert item["patient_email"] == patient.email


def test_booking_with_payment_in_progress_cannot_be_cancelled(
    client: TestClient, user_headers: Headers, pending_payment: dict[str, Any]
) -> None:
    response = client.patch(f"/bookings/{pending_payment['booking_id']}/cancel", headers=user_headers)
    assert response.status_code == 409
    assert response.json() == {"detail": "Booking has a payment in progress and cannot be cancelled"}


def test_admin_can_cancel_any_booking(
    client: TestClient, admin_headers: Headers, create_booking: Callable[..., dict]
) -> None:
    booking = create_booking()
    response = client.patch(f"/bookings/{booking['id']}/cancel", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"


def test_booking_status_cannot_be_set_by_client(
    client: TestClient, user_headers: Headers, create_booking: Callable[..., dict]
) -> None:
    booking = create_booking()
    response = client.patch(f"/bookings/{booking['id']}", json={"status": "CONFIRMED"}, headers=user_headers)
    assert response.status_code == 405


ALL_TRANSITIONS = [(src, dst) for src in BookingStatus for dst in BookingStatus]


@pytest.mark.parametrize(("source", "target"), ALL_TRANSITIONS, ids=[f"{s}->{t}" for s, t in ALL_TRANSITIONS])
def test_booking_state_machine(source: BookingStatus, target: BookingStatus) -> None:
    booking = Booking(status=source)
    allowed = {
        (BookingStatus.PENDING, BookingStatus.CONFIRMED),
        (BookingStatus.PENDING, BookingStatus.FAILED),
        (BookingStatus.PENDING, BookingStatus.CANCELLED),
        (BookingStatus.PENDING, BookingStatus.EXPIRED),
        (BookingStatus.CONFIRMED, BookingStatus.CANCELLED),  # with a refund; window enforced by the service
        (BookingStatus.CONFIRMED, BookingStatus.COMPLETED),
        (BookingStatus.CONFIRMED, BookingStatus.NO_SHOW),
    }
    if (source, target) in allowed:
        booking.transition_to(target)
        assert booking.status == target
    else:
        with pytest.raises(InvalidStateTransitionError):
            booking.transition_to(target)
        assert booking.status == source
    assert set(BOOKING_TRANSITIONS) == set(BookingStatus)
