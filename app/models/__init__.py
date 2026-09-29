"""Import every model so SQLAlchemy's registry and Alembic autogenerate see all tables."""

from app.models.booking import ACTIVE_BOOKING_STATUSES, Booking, BookingStatus
from app.models.centre import CentreTest, DiagnosticCentre
from app.models.payment import Payment, PaymentStatus
from app.models.test import DiagnosticTest, SampleType
from app.models.user import User, UserRole
from app.models.webhook import WebhookEvent, WebhookOutcome

__all__ = [
    "ACTIVE_BOOKING_STATUSES",
    "Booking",
    "BookingStatus",
    "CentreTest",
    "DiagnosticCentre",
    "DiagnosticTest",
    "Payment",
    "PaymentStatus",
    "SampleType",
    "User",
    "UserRole",
    "WebhookEvent",
    "WebhookOutcome",
]
