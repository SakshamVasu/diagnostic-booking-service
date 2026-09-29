"""Password hashing, JWT handling and webhook signature verification."""

import hashlib
import hmac
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError

BCRYPT_MAX_PASSWORD_BYTES = 72  # bcrypt ignores anything beyond 72 bytes


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    password_bytes = password.encode("utf-8")
    if len(password_bytes) > BCRYPT_MAX_PASSWORD_BYTES:
        return False
    return bcrypt.checkpw(password_bytes, password_hash.encode("utf-8"))


# A precomputed hash used to spend the same bcrypt time when a login email does not
# exist, so response timing does not reveal which emails are registered.
_DUMMY_PASSWORD_HASH = hash_password("dummy-password-for-timing")


def verify_password_against_dummy(password: str) -> None:
    verify_password(password, _DUMMY_PASSWORD_HASH)


def create_access_token(user_id: int) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> int:
    """Validate the token and return the user id it was issued for."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("Token has expired") from exc
    except jwt.PyJWTError as exc:
        raise UnauthorizedError() from exc

    if payload.get("type") != "access":
        raise UnauthorizedError()
    try:
        return int(payload["sub"])
    except (TypeError, ValueError) as exc:
        raise UnauthorizedError() from exc


def compute_webhook_signature(body: bytes) -> str:
    secret = get_settings().webhook_secret.encode("utf-8")
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


def verify_webhook_signature(body: bytes, signature: str | None) -> None:
    if not signature or not hmac.compare_digest(compute_webhook_signature(body), signature):
        raise UnauthorizedError("Invalid webhook signature")
