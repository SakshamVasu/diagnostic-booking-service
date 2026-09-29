from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import MONEY, Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.test import DiagnosticTest


class DiagnosticCentre(TimestampMixin, Base):
    __tablename__ = "diagnostic_centres"
    __table_args__ = (
        UniqueConstraint("name", "location", name="uq_diagnostic_centres_name_location"),
        Index("ix_diagnostic_centres_location", "location"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    location: Mapped[str] = mapped_column(String(200), nullable=False)

    offerings: Mapped[list["CentreTest"]] = relationship(
        back_populates="centre", cascade="all, delete-orphan", passive_deletes=True
    )


class CentreTest(TimestampMixin, Base):
    """Association between a centre and a test it offers, carrying the centre-specific price."""

    __tablename__ = "centre_tests"
    __table_args__ = (
        UniqueConstraint("centre_id", "test_id", name="uq_centre_tests_centre_id_test_id"),
        CheckConstraint("price > 0", name="price_positive"),
        Index("ix_centre_tests_test_id", "test_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    centre_id: Mapped[int] = mapped_column(ForeignKey("diagnostic_centres.id", ondelete="CASCADE"), nullable=False)
    test_id: Mapped[int] = mapped_column(ForeignKey("diagnostic_tests.id", ondelete="CASCADE"), nullable=False)
    price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)

    centre: Mapped[DiagnosticCentre] = relationship(back_populates="offerings")
    test: Mapped["DiagnosticTest"] = relationship(back_populates="offerings")

    @property
    def centre_name(self) -> str:
        return self.centre.name

    @property
    def test_name(self) -> str:
        return self.test.name
