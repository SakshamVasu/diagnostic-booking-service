from datetime import datetime
from enum import Enum as PyEnum
from typing import Any, ClassVar

from sqlalchemy import DateTime, Enum, MetaData, Numeric, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Money is always stored as exact NUMERIC(10, 2), never floating point.
MONEY = Numeric(10, 2)

# Deterministic constraint names make Alembic migrations reproducible and let the
# services recognise specific constraint violations by name.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # Fetch server-generated values (ids, timestamps) with RETURNING on INSERT/UPDATE.
    __mapper_args__: ClassVar[dict[str, Any]] = {"eager_defaults": True}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


def str_enum(enum_cls: type[PyEnum], name: str) -> Enum:
    """Store an enum as VARCHAR + CHECK constraint (simpler to migrate than native PG enums)."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=20,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )
