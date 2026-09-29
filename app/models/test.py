from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import MONEY, Base, TimestampMixin, str_enum

if TYPE_CHECKING:
    from app.models.centre import CentreTest


class SampleType(StrEnum):
    BLOOD = "BLOOD"
    URINE = "URINE"
    STOOL = "STOOL"
    SWAB = "SWAB"
    IMAGING = "IMAGING"  # X-ray, ultrasound, scans: no sample is taken
    OTHER = "OTHER"


class DiagnosticTest(TimestampMixin, Base):
    __tablename__ = "diagnostic_tests"
    __table_args__ = (
        CheckConstraint("base_price > 0", name="base_price_positive"),
        CheckConstraint("fasting_hours IS NULL OR fasting_hours BETWEEN 0 AND 72", name="fasting_hours_range"),
        CheckConstraint(
            "report_turnaround_hours IS NULL OR report_turnaround_hours BETWEEN 1 AND 720",
            name="report_turnaround_hours_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)

    # What the patient needs to know before and after the visit. All optional.
    sample_type: Mapped[SampleType | None] = mapped_column(str_enum(SampleType, "sample_type"))
    fasting_hours: Mapped[int | None] = mapped_column(Integer)  # 0 = no fasting needed
    preparation_instructions: Mapped[str | None] = mapped_column(Text)
    report_turnaround_hours: Mapped[int | None] = mapped_column(Integer)

    offerings: Mapped[list["CentreTest"]] = relationship(
        back_populates="test", cascade="all, delete-orphan", passive_deletes=True
    )
