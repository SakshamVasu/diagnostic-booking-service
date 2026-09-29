"""Plain helper functions shared by the tests."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from app.core.security import compute_webhook_signature

WEBHOOK_URL = "/payments/webhook/"

Headers = dict[str, str]


def future_datetime(days: int = 7, hour: int = 10, minute: int = 30) -> str:
    moment = (datetime.now(UTC) + timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return moment.isoformat()


def webhook_payload(
    payment: dict[str, Any], event_id: str = "evt_1", status: str = "SUCCESS", **overrides: Any
) -> dict:
    payload = {
        "event_id": event_id,
        "payment_id": payment["provider_payment_id"],
        "booking_id": payment["booking_id"],
        "status": status,
        "amount": payment["amount"],
    }
    payload.update(overrides)
    return payload


def signed_headers(body: bytes) -> Headers:
    return {"Content-Type": "application/json", "X-Webhook-Signature": compute_webhook_signature(body)}


def send_webhook(client: TestClient, payload: dict[str, Any]) -> Response:
    body = json.dumps(payload).encode()
    return client.post(WEBHOOK_URL, content=body, headers=signed_headers(body))
