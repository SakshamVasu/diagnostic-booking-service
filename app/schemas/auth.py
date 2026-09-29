from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.security import BCRYPT_MAX_PASSWORD_BYTES
from app.models.user import UserRole

MIN_PASSWORD_LENGTH = 8

# Small deny-list of the most common passwords (NIST SP 800-63B recommends length plus a
# blocklist rather than composition rules).
COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "password123",
        "12345678",
        "123456789",
        "1234567890",
        "qwerty123",
        "qwertyuiop",
        "iloveyou",
        "11111111",
        "00000000",
        "abcdefgh",
        "abc12345",
        "letmein1",
        "welcome1",
        "admin123",
    }
)


def _normalise_email(value: str) -> str:
    return value.strip().lower()


class SignupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100, examples=["John Doe"])
    email: EmailStr = Field(examples=["john@example.com"])
    # Not stripped/normalised: passwords are used exactly as typed.
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, examples=["SecurePass123"])

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name cannot be blank")
        return value

    @field_validator("email")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        return _normalise_email(value)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if len(value.encode("utf-8")) > BCRYPT_MAX_PASSWORD_BYTES:
            raise ValueError(f"Password must be at most {BCRYPT_MAX_PASSWORD_BYTES} bytes")
        if not value.strip():
            raise ValueError("Password cannot be blank")
        if value.lower() in COMMON_PASSWORDS:
            raise ValueError("Password is too common")
        return value


class LoginRequest(BaseModel):
    email: EmailStr = Field(examples=["john@example.com"])
    password: str = Field(min_length=1, examples=["SecurePass123"])

    @field_validator("email")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        return _normalise_email(value)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - token type, not a secret
    expires_in: int = Field(description="Token lifetime in seconds")


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: EmailStr
    role: UserRole
    created_at: datetime
