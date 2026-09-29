from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.api.common import ADMIN_ONLY, CONFLICT, NOT_FOUND, Pagination
from app.dependencies.auth import AdminUser, DbSession
from app.dependencies.payments import PaymentProviderDep
from app.models.payment import Payment
from app.models.user import UserRole
from app.models.webhook import WebhookOutcome
from app.schemas.admin import AdminStats, AdminUserRead, AdminUserUpdate, MaintenanceResult, WebhookEventRead
from app.schemas.common import ErrorResponse, Page
from app.schemas.payment import PaymentRead
from app.services import admin_service, payment_service

router = APIRouter(prefix="/admin", tags=["Admin"], responses=ADMIN_ONLY)

UserId = Annotated[int, Path(gt=0, description="User id")]
PaymentId = Annotated[int, Path(gt=0, description="Payment id")]


@router.get("/stats", response_model=AdminStats, summary="Bookings, revenue and payment health (admin)")
def get_stats(
    db: DbSession,
    _: AdminUser,
    days: Annotated[int, Query(ge=1, le=365, description="Reporting period in days")] = 30,
) -> AdminStats:
    return admin_service.stats(db, days)


@router.get("/users", response_model=Page[AdminUserRead], summary="List user accounts (admin)")
def list_users(
    db: DbSession,
    _: AdminUser,
    pagination: Pagination,
    search: Annotated[str | None, Query(max_length=200, description="Match on name or email")] = None,
    role: Annotated[UserRole | None, Query(description="Filter by role")] = None,
) -> Page[AdminUserRead]:
    items, total = admin_service.list_users(db, pagination, search=search, role=role)
    return Page[AdminUserRead].build(items, total, pagination)


@router.patch(
    "/users/{user_id}",
    response_model=AdminUserRead,
    summary="Activate/deactivate a user or change their role (admin)",
    responses={**NOT_FOUND, **CONFLICT},
)
def update_user(user_id: UserId, data: AdminUserUpdate, db: DbSession, admin: AdminUser) -> AdminUserRead:
    return admin_service.update_user(db, admin, user_id, data)


@router.get(
    "/webhook-events",
    response_model=Page[WebhookEventRead],
    summary="Browse the ledger of received payment webhooks (admin)",
)
def list_webhook_events(
    db: DbSession,
    _: AdminUser,
    pagination: Pagination,
    outcome: Annotated[WebhookOutcome | None, Query(description="Filter by outcome, e.g. REJECTED")] = None,
    search: Annotated[str | None, Query(max_length=200, description="Match on event or payment id")] = None,
) -> Page[WebhookEventRead]:
    items, total = admin_service.list_webhook_events(db, pagination, outcome=outcome, search=search)
    return Page[WebhookEventRead].build([WebhookEventRead.model_validate(e) for e in items], total, pagination)


@router.post(
    "/payments/{payment_id}/reconcile",
    response_model=PaymentRead,
    summary="Settle a payment stuck in PENDING (admin)",
    description=(
        "Asks the provider for the charge's status and applies it. If the provider still reports it "
        "pending after PAYMENT_PENDING_TIMEOUT_MINUTES, the payment is marked FAILED (abandoned)."
    ),
    responses={**NOT_FOUND, 409: {"model": ErrorResponse, "description": "Not pending, or not yet timed out"}},
)
def reconcile_payment(payment_id: PaymentId, db: DbSession, _: AdminUser, provider: PaymentProviderDep) -> Payment:
    return payment_service.reconcile_payment(db, payment_id, provider)


@router.post(
    "/maintenance/run",
    response_model=MaintenanceResult,
    summary="Expire lapsed unpaid bookings and settle stuck payments now (admin)",
    description="The same job the `maintenance` Compose service runs every minute.",
)
def run_maintenance(db: DbSession, _: AdminUser, provider: PaymentProviderDep) -> MaintenanceResult:
    report = payment_service.run_maintenance(db, provider)
    return MaintenanceResult(**vars(report))
