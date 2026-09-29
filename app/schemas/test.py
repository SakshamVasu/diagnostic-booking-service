from datetime import datetime
from decimal import Decimal
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from app.models.test import SampleType
from app.schemas.common import InputModel, Money, PatchModel

FastingHours = Field(default=None, ge=0, le=72, description="Hours of fasting required; 0 means none.")
TurnaroundHours = Field(default=None, ge=1, le=720, description="Typical hours until the report is ready.")
PreparationText = Field(default=None, max_length=2000, description="What to do before the test.")


class DiagnosticTestCreate(InputModel):
    name: str = Field(min_length=1, max_length=200, examples=["Complete Blood Count (CBC)"])
    description: str | None = Field(default=None, max_length=2000, examples=["Measures blood cell counts"])
    base_price: Money = Field(description="Reference/list price; centres may charge a different price.")
    sample_type: SampleType | None = None
    fasting_hours: int | None = FastingHours
    preparation_instructions: str | None = PreparationText
    report_turnaround_hours: int | None = TurnaroundHours


class DiagnosticTestUpdate(PatchModel):
    clearable_fields: ClassVar[frozenset[str]] = frozenset(
        {"sample_type", "fasting_hours", "preparation_instructions", "report_turnaround_hours"}
    )

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    base_price: Money | None = None
    sample_type: SampleType | None = None
    fasting_hours: int | None = FastingHours
    preparation_instructions: str | None = PreparationText
    report_turnaround_hours: int | None = TurnaroundHours


class DiagnosticTestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    base_price: Decimal
    sample_type: SampleType | None
    fasting_hours: int | None
    preparation_instructions: str | None
    report_turnaround_hours: int | None
    created_at: datetime
    updated_at: datetime
