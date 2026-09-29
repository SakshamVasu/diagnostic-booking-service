from fastapi import APIRouter, Depends

from app.dependencies.auth import DbSession
from app.dependencies.payments import verify_webhook_request
from app.schemas.common import ErrorResponse
from app.schemas.webhook import PaymentWebhookPayload, WebhookAck
from app.services import webhook_service

router = APIRouter(prefix="/payments", tags=["Webhooks"])


@router.post(
    "/webhook/",
    response_model=WebhookAck,
    summary="Receive a payment status event from the (simulated) payment provider",
    description=(
        "Authenticated with an HMAC-SHA256 signature of the raw body in `X-Webhook-Signature`. "
        "Idempotent on `event_id`: redelivering an event returns 200 with `duplicate: true` and "
        "never re-applies it."
    ),
    dependencies=[Depends(verify_webhook_request)],
    responses={
        400: {"model": ErrorResponse, "description": "Amount mismatch or payment/booking mismatch"},
        401: {"model": ErrorResponse, "description": "Missing or invalid signature"},
        404: {"model": ErrorResponse, "description": "Unknown payment or booking"},
        409: {"model": ErrorResponse, "description": "Invalid state transition"},
    },
)
def payment_webhook(payload: PaymentWebhookPayload, db: DbSession) -> WebhookAck:
    return webhook_service.process_payment_webhook(db, payload)
