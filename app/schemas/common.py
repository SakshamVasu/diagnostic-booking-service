from decimal import Decimal
from math import ceil
from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

# Monetary amounts: exact decimals, positive, at most 2 decimal places. Serialised as
# strings (e.g. "750.00") so clients never lose precision to floating point.
Money = Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=2, examples=["750.00"])]


class InputModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class PatchModel(InputModel):
    """Base for partial updates: omitted fields are left untouched, explicit nulls rejected
    (except for optional fields listed in ``clearable_fields``, where null clears the value)."""

    clearable_fields: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            null_fields = sorted(
                key for key, value in data.items() if value is None and key not in cls.clearable_fields
            )
            if null_fields:
                raise ValueError(f"Fields cannot be null: {', '.join(null_fields)}")
        return data


class PaginationParams(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class Page[ItemT](BaseModel):
    items: list[ItemT]
    total: int
    page: int
    page_size: int
    pages: int

    @classmethod
    def build(cls, items: list[ItemT], total: int, params: PaginationParams) -> "Page[ItemT]":
        return cls(
            items=items,
            total=total,
            page=params.page,
            page_size=params.page_size,
            pages=ceil(total / params.page_size) if total else 0,
        )


class ErrorResponse(BaseModel):
    detail: str
