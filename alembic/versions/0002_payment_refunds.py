"""allow REFUNDED payment status

Admins can cancel CONFIRMED bookings, which refunds the booking's successful payment.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-29 18:30:00

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_payments_payment_status"


def upgrade() -> None:
    op.drop_constraint(op.f(CONSTRAINT), "payments", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "payments", "status IN ('PENDING', 'SUCCESS', 'FAILED', 'REFUNDED')")


def downgrade() -> None:
    # Fails if REFUNDED payments exist: they have no representation in the old schema.
    op.drop_constraint(op.f(CONSTRAINT), "payments", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "payments", "status IN ('PENDING', 'SUCCESS', 'FAILED')")
