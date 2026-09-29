from enum import StrEnum
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_SECRET_LENGTH = 32


class PaymentSimulationMode(StrEnum):
    """How the simulated payment provider decides the outcome of a charge."""

    SUCCESS = "success"  # every charge succeeds
    FAILURE = "failure"  # every charge fails
    RANDOM = "random"  # succeeds with probability PAYMENT_SUCCESS_RATE
    PENDING = "pending"  # charge stays PENDING until a webhook reports the result


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Diagnostic Booking Service"
    database_url: str
    db_echo: bool = False

    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=60, gt=0)

    webhook_secret: str

    payment_simulation_mode: PaymentSimulationMode = PaymentSimulationMode.SUCCESS
    payment_success_rate: float = Field(default=0.8, ge=0.0, le=1.0)

    # How long an unpaid PENDING booking holds its slot before it expires (0 = never).
    booking_hold_minutes: int = Field(default=30, ge=0)
    # A PENDING payment the provider still hasn't settled after this long is treated as abandoned.
    payment_pending_timeout_minutes: int = Field(default=15, gt=0)
    # Patients may cancel (with refund) or reschedule a CONFIRMED booking up to this many hours
    # before the appointment; after that only an admin can.
    cancellation_window_hours: int = Field(default=24, ge=0)

    log_level: str = "INFO"

    @field_validator("jwt_secret_key", "webhook_secret")
    @classmethod
    def secret_must_be_strong(cls, value: str) -> str:
        if len(value) < MIN_SECRET_LENGTH:
            raise ValueError(f"must be at least {MIN_SECRET_LENGTH} characters long")
        return value

    @field_validator("jwt_algorithm")
    @classmethod
    def algorithm_must_be_hmac(cls, value: str) -> str:
        if value not in {"HS256", "HS384", "HS512"}:
            raise ValueError("only HMAC algorithms (HS256/HS384/HS512) are supported")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
