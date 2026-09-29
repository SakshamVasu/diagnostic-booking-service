import random
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import PaymentSimulationMode
from app.core.exceptions import AppError
from app.db.database import SessionLocal
from app.models.booking import Booking
from app.models.payment import Payment, PaymentStatus
from app.models.user import User
from app.services import payment_service
from app.services.payment_provider import SimulatedPaymentProvider
from tests.helpers import Headers

SetMode = Callable[[PaymentSimulationMode], None]


def _pay(client: TestClient, booking_id: int, headers: Headers, **extra: Any) -> Any:
    return client.post("/payments/", json={"booking_id": booking_id, **extra}, headers=headers)


def _payment_count(db: Session, booking_id: int) -> int:
    return db.scalar(select(func.count()).select_from(Payment).where(Payment.booking_id == booking_id)) or 0


def test_successful_payment_confirms_booking(
    client: TestClient, user_headers: Headers, create_booking: Callable[..., dict], set_payment_mode: SetMode
) -> None:
    set_payment_mode(PaymentSimulationMode.SUCCESS)
    booking = create_booking()

    response = _pay(client, booking["id"], user_headers)

    assert response.status_code == 201
    payment = response.json()
    assert payment["status"] == "SUCCESS"
    assert payment["booking_status"] == "CONFIRMED"
    assert payment["provider"] == "simulated"
    assert payment["provider_payment_id"].startswith("pay_")
    assert client.get(f"/bookings/{booking['id']}", headers=user_headers).json()["status"] == "CONFIRMED"


def test_failed_payment_fails_booking(
    client: TestClient, user_headers: Headers, create_booking: Callable[..., dict], set_payment_mode: SetMode
) -> None:
    set_payment_mode(PaymentSimulationMode.FAILURE)
    booking = create_booking()

    response = _pay(client, booking["id"], user_headers)

    assert response.status_code == 201
    assert response.json()["status"] == "FAILED"
    assert response.json()["booking_status"] == "FAILED"
    assert client.get(f"/bookings/{booking['id']}", headers=user_headers).json()["status"] == "FAILED"


def test_pending_payment_leaves_booking_pending(pending_payment: dict[str, Any]) -> None:
    assert pending_payment["status"] == "PENDING"
    assert pending_payment["booking_status"] == "PENDING"


def test_payment_amount_comes_from_booking_not_client(
    client: TestClient, user_headers: Headers, create_booking: Callable[..., dict], db: Session
) -> None:
    booking = create_booking()

    response = _pay(client, booking["id"], user_headers, amount="1.00")

    assert response.status_code == 201
    assert response.json()["amount"] == booking["amount"] == "750.00"
    stored = db.scalar(select(Payment).where(Payment.booking_id == booking["id"]))
    assert stored is not None and stored.amount == Decimal("750.00")


def test_payment_for_nonexistent_booking(client: TestClient, user_headers: Headers) -> None:
    response = _pay(client, 999_999, user_headers)
    assert response.status_code == 404
    assert response.json() == {"detail": "Booking not found"}


@pytest.mark.parametrize("booking_id", [0, -5, "abc", None])
def test_payment_with_invalid_booking_id(client: TestClient, user_headers: Headers, booking_id: object) -> None:
    assert _pay(client, booking_id, user_headers).status_code == 422  # type: ignore[arg-type]


def test_user_cannot_pay_for_another_users_booking(
    client: TestClient, other_user_headers: Headers, create_booking: Callable[..., dict], db: Session
) -> None:
    booking = create_booking()

    response = _pay(client, booking["id"], other_user_headers)

    assert response.status_code == 404
    assert _payment_count(db, booking["id"]) == 0


def test_admin_cannot_pay_for_another_users_booking(
    client: TestClient, admin_headers: Headers, create_booking: Callable[..., dict]
) -> None:
    booking = create_booking()
    assert _pay(client, booking["id"], admin_headers).status_code == 404


def test_payment_requires_authentication(client: TestClient, create_booking: Callable[..., dict]) -> None:
    booking = create_booking()
    assert client.post("/payments/", json={"booking_id": booking["id"]}).status_code == 401


def test_duplicate_payment_after_success_is_rejected(
    client: TestClient, user_headers: Headers, create_booking: Callable[..., dict], db: Session
) -> None:
    booking = create_booking()
    assert _pay(client, booking["id"], user_headers).status_code == 201

    response = _pay(client, booking["id"], user_headers)

    assert response.status_code == 409
    assert response.json() == {"detail": "Booking is already paid"}
    assert _payment_count(db, booking["id"]) == 1


def test_duplicate_payment_while_pending_is_rejected(
    client: TestClient, user_headers: Headers, pending_payment: dict[str, Any], db: Session
) -> None:
    response = _pay(client, pending_payment["booking_id"], user_headers)

    assert response.status_code == 409
    assert response.json() == {"detail": payment_service.PAYMENT_IN_PROGRESS_MESSAGE}
    assert _payment_count(db, pending_payment["booking_id"]) == 1


@pytest.mark.parametrize("setup", ["failed", "cancelled"])
def test_non_pending_booking_is_not_payable(
    client: TestClient,
    user_headers: Headers,
    create_booking: Callable[..., dict],
    set_payment_mode: SetMode,
    setup: str,
) -> None:
    booking = create_booking()
    if setup == "failed":
        set_payment_mode(PaymentSimulationMode.FAILURE)
        _pay(client, booking["id"], user_headers)
        set_payment_mode(PaymentSimulationMode.SUCCESS)
    else:
        client.patch(f"/bookings/{booking['id']}/cancel", headers=user_headers)

    response = _pay(client, booking["id"], user_headers)

    assert response.status_code == 409
    assert response.json() == {"detail": f"Booking is {setup.upper()} and cannot be paid"}


def test_booking_whose_appointment_has_passed_is_not_payable(
    client: TestClient, user_headers: Headers, create_booking: Callable[..., dict], db: Session
) -> None:
    booking = create_booking()
    stored = db.get(Booking, booking["id"])
    assert stored is not None
    stored.appointment_datetime = datetime.now(UTC) - timedelta(hours=1)
    db.commit()

    response = _pay(client, booking["id"], user_headers)

    assert response.status_code == 409
    assert response.json() == {"detail": "Booking appointment time has passed"}


def test_concurrent_payment_attempts_charge_only_once(
    create_booking: Callable[..., dict], user: tuple[User, Headers], db: Session
) -> None:
    booking = create_booking()
    provider = SimulatedPaymentProvider(PaymentSimulationMode.SUCCESS)
    attempts = 5
    barrier = threading.Barrier(attempts)
    outcomes: list[str] = []

    def attempt() -> None:
        with SessionLocal() as session:
            barrier.wait()
            try:
                payment_service.create_payment(session, user[0], booking["id"], provider)
                outcomes.append("paid")
            except AppError as exc:
                outcomes.append(str(exc.status_code))

    threads = [threading.Thread(target=attempt) for _ in range(attempts)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["409"] * (attempts - 1) + ["paid"]
    assert _payment_count(db, booking["id"]) == 1


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (PaymentSimulationMode.SUCCESS, PaymentStatus.SUCCESS),
        (PaymentSimulationMode.FAILURE, PaymentStatus.FAILED),
        (PaymentSimulationMode.PENDING, PaymentStatus.PENDING),
    ],
)
def test_simulated_provider_fixed_modes(mode: PaymentSimulationMode, expected: PaymentStatus) -> None:
    result = SimulatedPaymentProvider(mode).charge(Decimal("10.00"), "booking_1")
    assert result.status == expected
    assert result.provider_payment_id.startswith("pay_")


def test_simulated_provider_random_mode_is_reproducible_with_seed() -> None:
    def outcomes(seed: int) -> list[PaymentStatus]:
        provider = SimulatedPaymentProvider(PaymentSimulationMode.RANDOM, 0.5, rng=random.Random(seed))  # noqa: S311
        return [provider.charge(Decimal("1.00"), "ref").status for _ in range(50)]

    assert outcomes(42) == outcomes(42)
    assert {PaymentStatus.SUCCESS, PaymentStatus.FAILED} == set(outcomes(42))


def test_provider_payment_ids_are_unique() -> None:
    provider = SimulatedPaymentProvider(PaymentSimulationMode.SUCCESS)
    ids = {provider.charge(Decimal("1.00"), "ref").provider_payment_id for _ in range(100)}
    assert len(ids) == 100
