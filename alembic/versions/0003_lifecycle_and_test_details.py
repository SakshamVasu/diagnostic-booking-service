"""booking lifecycle states and test preparation details

- Bookings gain EXPIRED (unpaid past the hold window), COMPLETED and NO_SHOW (attendance).
- Tests gain sample type, fasting hours, preparation instructions and report turnaround.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-30 10:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BOOKING_STATUS_CHECK = "ck_bookings_booking_status"
OLD_BOOKING_STATUSES = "'PENDING', 'CONFIRMED', 'FAILED', 'CANCELLED'"
NEW_BOOKING_STATUSES = f"{OLD_BOOKING_STATUSES}, 'EXPIRED', 'COMPLETED', 'NO_SHOW'"
SAMPLE_TYPES = "'BLOOD', 'URINE', 'STOOL', 'SWAB', 'IMAGING', 'OTHER'"


def upgrade() -> None:
    op.drop_constraint(op.f(BOOKING_STATUS_CHECK), "bookings", type_="check")
    op.create_check_constraint(op.f(BOOKING_STATUS_CHECK), "bookings", f"status IN ({NEW_BOOKING_STATUSES})")

    op.add_column("diagnostic_tests", sa.Column("sample_type", sa.String(length=20), nullable=True))
    op.add_column("diagnostic_tests", sa.Column("fasting_hours", sa.Integer(), nullable=True))
    op.add_column("diagnostic_tests", sa.Column("preparation_instructions", sa.Text(), nullable=True))
    op.add_column("diagnostic_tests", sa.Column("report_turnaround_hours", sa.Integer(), nullable=True))
    op.create_check_constraint(
        op.f("ck_diagnostic_tests_sample_type"), "diagnostic_tests", f"sample_type IN ({SAMPLE_TYPES})"
    )
    op.create_check_constraint(
        op.f("ck_diagnostic_tests_fasting_hours_range"),
        "diagnostic_tests",
        "fasting_hours IS NULL OR fasting_hours BETWEEN 0 AND 72",
    )
    op.create_check_constraint(
        op.f("ck_diagnostic_tests_report_turnaround_hours_range"),
        "diagnostic_tests",
        "report_turnaround_hours IS NULL OR report_turnaround_hours BETWEEN 1 AND 720",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_diagnostic_tests_report_turnaround_hours_range"), "diagnostic_tests", type_="check")
    op.drop_constraint(op.f("ck_diagnostic_tests_fasting_hours_range"), "diagnostic_tests", type_="check")
    op.drop_constraint(op.f("ck_diagnostic_tests_sample_type"), "diagnostic_tests", type_="check")
    op.drop_column("diagnostic_tests", "report_turnaround_hours")
    op.drop_column("diagnostic_tests", "preparation_instructions")
    op.drop_column("diagnostic_tests", "fasting_hours")
    op.drop_column("diagnostic_tests", "sample_type")

    # Fails if bookings use the new statuses: they have no representation in the old schema.
    op.drop_constraint(op.f(BOOKING_STATUS_CHECK), "bookings", type_="check")
    op.create_check_constraint(op.f(BOOKING_STATUS_CHECK), "bookings", f"status IN ({OLD_BOOKING_STATUSES})")
