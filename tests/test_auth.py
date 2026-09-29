from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.user import User, UserRole

SIGNUP = {"name": "John Doe", "email": "john@example.com", "password": "securepassword"}


def test_signup_success_returns_safe_user_fields(client: TestClient, db: Session) -> None:
    response = client.post("/auth/signup", json=SIGNUP)

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "john@example.com"
    assert body["name"] == "John Doe"
    assert body["role"] == "USER"
    assert "password" not in body and "password_hash" not in body

    stored = db.scalar(select(User).where(User.email == "john@example.com"))
    assert stored is not None
    assert stored.password_hash != SIGNUP["password"]
    assert stored.password_hash.startswith("$2")  # bcrypt


def test_signup_cannot_self_assign_admin_role(client: TestClient) -> None:
    response = client.post("/auth/signup", json={**SIGNUP, "role": "ADMIN"})
    assert response.status_code == 201
    assert response.json()["role"] == UserRole.USER


def test_signup_normalises_email_case(client: TestClient) -> None:
    response = client.post("/auth/signup", json={**SIGNUP, "email": "  John@Example.COM "})
    assert response.status_code == 201
    assert response.json()["email"] == "john@example.com"


def test_duplicate_signup_is_rejected_case_insensitively(client: TestClient) -> None:
    assert client.post("/auth/signup", json=SIGNUP).status_code == 201

    response = client.post("/auth/signup", json={**SIGNUP, "email": "JOHN@example.com"})

    assert response.status_code == 409
    assert response.json() == {"detail": "An account with this email already exists"}


@pytest.mark.parametrize("email", ["not-an-email", "john@", "@example.com", ""])
def test_signup_invalid_email(client: TestClient, email: str) -> None:
    response = client.post("/auth/signup", json={**SIGNUP, "email": email})
    assert response.status_code == 422


@pytest.mark.parametrize(
    "password",
    ["short", "        ", "password", "12345678", "x" * 73],
    ids=["too-short", "blank", "common", "common-digits", "over-72-bytes"],
)
def test_signup_weak_or_invalid_password(client: TestClient, password: str) -> None:
    response = client.post("/auth/signup", json={**SIGNUP, "password": password})
    assert response.status_code == 422


def test_validation_errors_do_not_echo_submitted_password(client: TestClient) -> None:
    response = client.post("/auth/signup", json={**SIGNUP, "password": "password"})
    assert response.status_code == 422
    assert "password" in str(response.json()["detail"][0]["loc"])
    assert '"input"' not in response.text


def test_signup_missing_fields(client: TestClient) -> None:
    response = client.post("/auth/signup", json={"email": "john@example.com"})
    assert response.status_code == 422


def test_login_success_returns_valid_jwt(client: TestClient) -> None:
    user_id = client.post("/auth/signup", json=SIGNUP).json()["id"]

    response = client.post("/auth/login", json={"email": "John@Example.com", "password": SIGNUP["password"]})

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    settings = get_settings()
    claims = jwt.decode(body["access_token"], settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    assert claims["sub"] == str(user_id)


@pytest.mark.parametrize(
    ("email", "password"),
    [("john@example.com", "wrong-password"), ("nobody@example.com", "securepassword")],
    ids=["wrong-password", "unknown-email"],
)
def test_login_invalid_credentials(client: TestClient, email: str, password: str) -> None:
    client.post("/auth/signup", json=SIGNUP)

    response = client.post("/auth/login", json={"email": email, "password": password})

    assert response.status_code == 401
    # Same message for both cases so emails cannot be enumerated.
    assert response.json() == {"detail": "Incorrect email or password"}


def test_protected_endpoint_with_token_returns_current_user(client: TestClient) -> None:
    client.post("/auth/signup", json=SIGNUP)
    token = client.post("/auth/login", json=SIGNUP).json()["access_token"]

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["email"] == SIGNUP["email"]


def test_protected_endpoint_without_token(client: TestClient) -> None:
    response = client.get("/bookings/")
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "authorization",
    ["Bearer not-a-jwt", "Bearer ", "Basic dXNlcjpwYXNz", "Bearer eyJhbGciOiJIUzI1NiJ9.e30.invalid"],
)
def test_protected_endpoint_with_invalid_token(client: TestClient, authorization: str) -> None:
    response = client.get("/bookings/", headers={"Authorization": authorization})
    assert response.status_code == 401


def _token(user_id: int, secret: str, **overrides: object) -> str:
    now = datetime.now(UTC)
    claims = {"sub": str(user_id), "iat": now, "exp": now + timedelta(minutes=5), "type": "access", **overrides}
    return jwt.encode(claims, secret, algorithm="HS256")


def test_token_signed_with_wrong_secret_is_rejected(client: TestClient) -> None:
    user_id = client.post("/auth/signup", json=SIGNUP).json()["id"]
    token = _token(user_id, "some-other-secret-that-is-at-least-32-chars-long")

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


def test_expired_token_is_rejected(client: TestClient) -> None:
    user_id = client.post("/auth/signup", json=SIGNUP).json()["id"]
    token = _token(user_id, get_settings().jwt_secret_key, exp=datetime.now(UTC) - timedelta(seconds=1))

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Token has expired"}


def test_token_for_deleted_or_unknown_user_is_rejected(client: TestClient) -> None:
    token = _token(999_999, get_settings().jwt_secret_key)
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_token_for_deactivated_user_is_rejected(client: TestClient, db: Session) -> None:
    user_id = client.post("/auth/signup", json=SIGNUP).json()["id"]
    token = client.post("/auth/login", json=SIGNUP).json()["access_token"]
    user = db.get(User, user_id)
    assert user is not None
    user.is_active = False
    db.commit()

    assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert client.post("/auth/login", json=SIGNUP).status_code == 401
