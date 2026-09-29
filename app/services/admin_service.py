"""Admin-only reads and actions: reporting, user management and the webhook ledger."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import distinct, func, or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.db.utils import contains_pattern, paginate
from app.models.booking import Booking, BookingStatus
from app.models.centre import DiagnosticCentre
from app.models.payment import Payment, PaymentStatus
from app.models.test import DiagnosticTest
from app.models.user import User, UserRole
from app.models.webhook import WebhookEvent, WebhookOutcome
from app.schemas.admin import AdminStats, AdminUserRead, AdminUserUpdate, CentreStats, TestStats
from app.schemas.common import PaginationParams

TOP_TESTS_LIMIT = 5
CENTS = Decimal("0.01")


def _money(value: Decimal | int | None) -> Decimal:
    return Decimal(value or 0).quantize(CENTS)


# --- Reporting -------------------------------------------------------------------------------


def stats(db: Session, days: int) -> AdminStats:
    since = datetime.now(UTC) - timedelta(days=days)

    by_status = dict(
        db.execute(
            select(Booking.status, func.count()).where(Booking.created_at >= since).group_by(Booking.status)
        ).all()
    )
    payments = dict(
        db.execute(
            select(Payment.status, func.count()).where(Payment.created_at >= since).group_by(Payment.status)
        ).all()
    )
    amounts = dict(
        db.execute(
            select(Payment.status, func.coalesce(func.sum(Payment.amount), 0))
            .where(Payment.created_at >= since, Payment.status.in_([PaymentStatus.SUCCESS, PaymentStatus.REFUNDED]))
            .group_by(Payment.status)
        ).all()
    )
    # A refunded payment had succeeded first, so it counts towards the success rate.
    succeeded = payments.get(PaymentStatus.SUCCESS, 0) + payments.get(PaymentStatus.REFUNDED, 0)
    failed = payments.get(PaymentStatus.FAILED, 0)

    upcoming = db.scalar(
        select(func.count()).where(
            Booking.status == BookingStatus.CONFIRMED, Booking.appointment_datetime >= func.now()
        )
    )
    patients = db.scalar(select(func.count()).select_from(User).where(User.role == UserRole.USER))

    successful_revenue = func.coalesce(
        func.sum(Payment.amount).filter(Payment.status == PaymentStatus.SUCCESS), Decimal("0")
    )
    centre_rows = db.execute(
        select(
            DiagnosticCentre.id,
            DiagnosticCentre.name,
            DiagnosticCentre.location,
            func.count(distinct(Booking.id)),
            successful_revenue,
        )
        .join(Booking, (Booking.centre_id == DiagnosticCentre.id) & (Booking.created_at >= since), isouter=True)
        .join(Payment, Payment.booking_id == Booking.id, isouter=True)
        .group_by(DiagnosticCentre.id)
        .order_by(successful_revenue.desc(), func.count(distinct(Booking.id)).desc(), DiagnosticCentre.id)
    ).all()
    test_rows = db.execute(
        select(DiagnosticTest.id, DiagnosticTest.name, func.count(Booking.id))
        .join(Booking, Booking.test_id == DiagnosticTest.id)
        .where(Booking.created_at >= since)
        .group_by(DiagnosticTest.id)
        .order_by(func.count(Booking.id).desc(), DiagnosticTest.id)
        .limit(TOP_TESTS_LIMIT)
    ).all()

    return AdminStats(
        days=days,
        since=since,
        bookings_total=sum(by_status.values()),
        bookings_by_status={status: by_status.get(status, 0) for status in BookingStatus},
        upcoming_confirmed=upcoming or 0,
        revenue=_money(amounts.get(PaymentStatus.SUCCESS)),
        refunded=_money(amounts.get(PaymentStatus.REFUNDED)),
        payments_succeeded=succeeded,
        payments_failed=failed,
        payments_pending=payments.get(PaymentStatus.PENDING, 0),
        payment_success_rate=round(succeeded / (succeeded + failed), 4) if succeeded + failed else None,
        patients=patients or 0,
        centres=[
            CentreStats(centre_id=cid, centre_name=name, location=loc, bookings=count, revenue=_money(revenue))
            for cid, name, loc, count, revenue in centre_rows
        ],
        top_tests=[TestStats(test_id=tid, test_name=name, bookings=count) for tid, name, count in test_rows],
    )


# --- Users -----------------------------------------------------------------------------------


def _booking_counts(db: Session, user_ids: list[int]) -> dict[int, int]:
    if not user_ids:
        return {}
    rows = db.execute(
        select(Booking.user_id, func.count()).where(Booking.user_id.in_(user_ids)).group_by(Booking.user_id)
    ).all()
    return dict(rows)


def _to_read(user: User, booking_count: int) -> AdminUserRead:
    return AdminUserRead(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        booking_count=booking_count,
        created_at=user.created_at,
    )


def list_users(
    db: Session, params: PaginationParams, search: str | None = None, role: UserRole | None = None
) -> tuple[list[AdminUserRead], int]:
    stmt = select(User).order_by(User.created_at.desc(), User.id.desc())
    if search:
        pattern = contains_pattern(search)
        stmt = stmt.where(or_(User.name.ilike(pattern, escape="\\"), User.email.ilike(pattern, escape="\\")))
    if role is not None:
        stmt = stmt.where(User.role == role)
    users, total = paginate(db, stmt, params)
    counts = _booking_counts(db, [user.id for user in users])
    return [_to_read(user, counts.get(user.id, 0)) for user in users], total


def update_user(db: Session, admin: User, user_id: int, data: AdminUserUpdate) -> AdminUserRead:
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found")
    changes = data.model_dump(exclude_unset=True)
    if user.id == admin.id and (changes.get("role") == UserRole.USER or changes.get("is_active") is False):
        # Guards against an admin locking everyone out by accident.
        raise ConflictError("You can't remove your own admin role or deactivate your own account")
    for field, value in changes.items():
        setattr(user, field, value)
    db.commit()
    return _to_read(user, _booking_counts(db, [user.id]).get(user.id, 0))


# --- Webhook ledger --------------------------------------------------------------------------


def list_webhook_events(
    db: Session, params: PaginationParams, outcome: WebhookOutcome | None = None, search: str | None = None
) -> tuple[list[WebhookEvent], int]:
    stmt = select(WebhookEvent).order_by(WebhookEvent.received_at.desc(), WebhookEvent.id.desc())
    if outcome is not None:
        stmt = stmt.where(WebhookEvent.outcome == outcome)
    if search:
        pattern = contains_pattern(search)
        stmt = stmt.where(
            or_(
                WebhookEvent.event_id.ilike(pattern, escape="\\"),
                WebhookEvent.provider_payment_id.ilike(pattern, escape="\\"),
            )
        )
    return paginate(db, stmt, params)
