from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from app.schemas.common import InputModel, Money, PatchModel


class CentreCreate(InputModel):
    name: str = Field(min_length=1, max_length=200, examples=["Apollo Diagnostics"])
    location: str = Field(min_length=1, max_length=200, examples=["Delhi"])


class CentreUpdate(PatchModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    location: str | None = Field(default=None, min_length=1, max_length=200)


class CentreRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    location: str
    created_at: datetime
    updated_at: datetime


class CentreTestCreate(InputModel):
    test_id: PositiveInt
    price: Money | None = Field(
        default=None, description="Centre-specific price. Defaults to the test's base price if omitted."
    )


class CentreTestUpdate(InputModel):
    price: Money


class CentreTestRead(BaseModel):
    """A test offered at a centre, with that centre's price."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    centre_id: int
    centre_name: str
    test_id: int
    test_name: str
    price: Decimal
    created_at: datetime
    updated_at: datetime
