from typing import Annotated

from fastapi import Depends, Header, Request

from app.core.config import get_settings
from app.core.security import verify_webhook_signature
from app.services.payment_provider import PaymentProvider, SimulatedPaymentProvider


def get_payment_provider() -> PaymentProvider:
    settings = get_settings()
    return SimulatedPaymentProvider(settings.payment_simulation_mode, settings.payment_success_rate)


PaymentProviderDep = Annotated[PaymentProvider, Depends(get_payment_provider)]


async def verify_webhook_request(
    request: Request,
    x_webhook_signature: Annotated[
        str | None,
        Header(description="Hex HMAC-SHA256 of the raw request body, keyed with WEBHOOK_SECRET"),
    ] = None,
) -> None:
    """Authenticate the webhook sender before the event is looked at or recorded."""
    verify_webhook_signature(await request.body(), x_webhook_signature)
