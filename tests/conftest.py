"""Test fixtures.

Tests run against a real PostgreSQL database (the concurrency and partial-index
behaviour under test cannot be reproduced with SQLite). The schema is created with the
real Alembic migrations, and every table is truncated after each test.

Point TEST_DATABASE_URL at a disposable database; it defaults to the one created by
``docker compose`` on localhost (see README).
"""

import os
from collections.abc import Callable, Iterator
from typing import Any

# Configure the app for tests *before* anything imports app settings.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/diagnostic_booking_test"
)
os.environ["JWT_SECRET_KEY"] = "test-jwt-secret-key-that-is-long-enough-0123456789"
os.environ["WEBHOOK_SECRET"] = "test-webhook-secret-that-is-long-enough-0123456789"
os.environ["PAYMENT_SIMULATION_MODE"] = "success"

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import PaymentSimulationMode
from app.core.security import create_access_token, hash_password
from app.db.base import Base
from app.db.database import SessionLocal, engine
from app.dependencies.payments import get_payment_provider
from app.main import app
from app.models.user import User, UserRole
from app.services.payment_provider import SimulatedPaymentProvider
from tests.helpers import Headers, future_datetime

DEFAULT_PASSWORD = "SecurePass123"


@pytest.fixture(scope="session", autouse=True)
def _migrated_database() -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"].replace("%", "%%"))
    command.upgrade(config, "head")


@pytest.fixture(autouse=True)
def _clean_tables() -> Iterator[None]:
    yield
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def set_payment_mode() -> Callable[[PaymentSimulationMode], None]:
    """Make the simulated provider deterministic for a test."""

    def _set(mode: PaymentSimulationMode) -> None:
        app.dependency_overrides[get_payment_provider] = lambda: SimulatedPaymentProvider(mode)

    return _set


# --- Users -------------------------------------------------------------------------


@pytest.fixture(scope="session")
def default_password_hash() -> str:
    # bcrypt is deliberately slow; hash the shared fixture password only once.
    return hash_password(DEFAULT_PASSWORD)


@pytest.fixture
def make_user(db: Session, default_password_hash: str) -> Callable[..., tuple[User, Headers]]:
    counter = 0

    def _make(role: UserRole = UserRole.USER, email: str | None = None) -> tuple[User, Headers]:
        nonlocal counter
        counter += 1
        user = User(
            name=f"User {counter}",
            email=email or f"user{counter}-{role.value.lower()}@example.com",
            password_hash=default_password_hash,
            role=role,
        )
        db.add(user)
        db.commit()
        return user, {"Authorization": f"Bearer {create_access_token(user.id)}"}

    return _make


@pytest.fixture
def admin_headers(make_user: Callable[..., tuple[User, Headers]]) -> Headers:
    return make_user(UserRole.ADMIN)[1]


@pytest.fixture
def user(make_user: Callable[..., tuple[User, Headers]]) -> tuple[User, Headers]:
    return make_user()


@pytest.fixture
def user_headers(user: tuple[User, Headers]) -> Headers:
    return user[1]


@pytest.fixture
def other_user_headers(make_user: Callable[..., tuple[User, Headers]]) -> Headers:
    return make_user()[1]


# --- Catalogue ---------------------------------------------------------------------


@pytest.fixture
def create_centre(client: TestClient, admin_headers: Headers) -> Callable[..., dict[str, Any]]:
    def _create(name: str = "Apollo Diagnostics", location: str = "Delhi") -> dict[str, Any]:
        response = client.post("/centres/", json={"name": name, "location": location}, headers=admin_headers)
        assert response.status_code == 201, response.text
        return response.json()

    return _create


@pytest.fixture
def create_test(client: TestClient, admin_headers: Headers) -> Callable[..., dict[str, Any]]:
    def _create(
        name: str = "Complete Blood Count", base_price: str = "500.00", description: str | None = None
    ) -> dict[str, Any]:
        response = client.post(
            "/tests/",
            json={"name": name, "description": description, "base_price": base_price},
            headers=admin_headers,
        )
        assert response.status_code == 201, response.text
        return response.json()

    return _create


@pytest.fixture
def offer_test(client: TestClient, admin_headers: Headers) -> Callable[..., dict[str, Any]]:
    def _offer(centre_id: int, test_id: int, price: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"test_id": test_id}
        if price is not None:
            body["price"] = price
        response = client.post(f"/centres/{centre_id}/tests", json=body, headers=admin_headers)
        assert response.status_code == 201, response.text
        return response.json()

    return _offer


@pytest.fixture
def catalogue(
    create_centre: Callable[..., dict[str, Any]],
    create_test: Callable[..., dict[str, Any]],
    offer_test: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """A centre offering a test at a centre-specific price of 750.00 (base price 500.00)."""
    centre = create_centre()
    test = create_test()
    offer_test(centre["id"], test["id"], price="750.00")
    return {"centre": centre, "test": test, "price": "750.00"}


# --- Bookings / payments -----------------------------------------------------------


@pytest.fixture
def create_booking(client: TestClient, catalogue: dict[str, Any], user_headers: Headers) -> Callable[..., dict]:
    def _create(headers: Headers | None = None, days: int = 7, hour: int = 10) -> dict[str, Any]:
        response = client.post(
            "/bookings/",
            json={
                "test_id": catalogue["test"]["id"],
                "centre_id": catalogue["centre"]["id"],
                "appointment_datetime": future_datetime(days=days, hour=hour),
            },
            headers=headers or user_headers,
        )
        assert response.status_code == 201, response.text
        return response.json()

    return _create


@pytest.fixture
def pending_payment(
    client: TestClient,
    create_booking: Callable[..., dict[str, Any]],
    user_headers: Headers,
    set_payment_mode: Callable[[PaymentSimulationMode], None],
) -> dict[str, Any]:
    """A booking with a PENDING payment, awaiting the provider's webhook."""
    booking = create_booking()
    set_payment_mode(PaymentSimulationMode.PENDING)
    response = client.post("/payments/", json={"booking_id": booking["id"]}, headers=user_headers)
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "PENDING"
    return response.json()
