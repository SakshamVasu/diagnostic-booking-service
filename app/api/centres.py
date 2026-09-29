from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from app.api.common import ADMIN_ONLY, CONFLICT, NOT_FOUND, Pagination
from app.dependencies.auth import AdminUser, DbSession
from app.models.centre import CentreTest, DiagnosticCentre
from app.schemas.booking import BookedTimes
from app.schemas.centre import (
    CentreCreate,
    CentreRead,
    CentreTestCreate,
    CentreTestRead,
    CentreTestUpdate,
    CentreUpdate,
)
from app.schemas.common import Page
from app.services import booking_service, centre_service

router = APIRouter(prefix="/centres", tags=["Centres"])

CentreId = Annotated[int, Path(gt=0, description="Centre id")]
TestId = Annotated[int, Path(gt=0, description="Diagnostic test id")]


@router.post(
    "/",
    response_model=CentreRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a diagnostic centre (admin)",
    responses={**ADMIN_ONLY, **CONFLICT},
)
def create_centre(data: CentreCreate, db: DbSession, _: AdminUser) -> DiagnosticCentre:
    return centre_service.create_centre(db, data)


@router.get("/", response_model=Page[CentreRead], summary="List diagnostic centres")
def list_centres(
    db: DbSession,
    pagination: Pagination,
    location: Annotated[str | None, Query(max_length=200, description="Case-insensitive location match")] = None,
    search: Annotated[str | None, Query(max_length=200, description="Case-insensitive name match")] = None,
) -> Page[CentreRead]:
    items, total = centre_service.list_centres(db, pagination, location=location, search=search)
    return Page[CentreRead].build([CentreRead.model_validate(c) for c in items], total, pagination)


@router.get("/{centre_id}", response_model=CentreRead, summary="Get a centre", responses=NOT_FOUND)
def get_centre(centre_id: CentreId, db: DbSession) -> DiagnosticCentre:
    return centre_service.get_centre(db, centre_id)


@router.patch(
    "/{centre_id}",
    response_model=CentreRead,
    summary="Update a centre (admin)",
    responses={**ADMIN_ONLY, **NOT_FOUND, **CONFLICT},
)
def update_centre(centre_id: CentreId, data: CentreUpdate, db: DbSession, _: AdminUser) -> DiagnosticCentre:
    return centre_service.update_centre(db, centre_id, data)


@router.delete(
    "/{centre_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a centre (admin). Refused if the centre has bookings.",
    responses={**ADMIN_ONLY, **NOT_FOUND, **CONFLICT},
)
def delete_centre(centre_id: CentreId, db: DbSession, _: AdminUser) -> None:
    centre_service.delete_centre(db, centre_id)


@router.get(
    "/{centre_id}/tests",
    response_model=Page[CentreTestRead],
    summary="List the tests a centre offers, with centre-specific prices",
    responses=NOT_FOUND,
)
def list_centre_tests(centre_id: CentreId, db: DbSession, pagination: Pagination) -> Page[CentreTestRead]:
    items, total = centre_service.list_centre_offerings(db, centre_id, pagination)
    return Page[CentreTestRead].build([CentreTestRead.model_validate(o) for o in items], total, pagination)


@router.get(
    "/{centre_id}/tests/{test_id}/booked-times",
    response_model=BookedTimes,
    summary="Times already booked for a test at this centre",
    description=(
        "Returns the appointment times held by active bookings in `[start, end)` (at most 31 days), "
        "so clients can avoid offering them. No patient details are included."
    ),
    responses=NOT_FOUND,
)
def booked_times(
    centre_id: CentreId,
    test_id: TestId,
    start: Annotated[datetime, Query(description="Window start (ISO-8601)")],
    end: Annotated[datetime, Query(description="Window end, exclusive (ISO-8601)")],
    db: DbSession,
) -> BookedTimes:
    times = booking_service.booked_times(db, centre_id, test_id, start, end)
    return BookedTimes(centre_id=centre_id, test_id=test_id, start=start, end=end, booked=times)


@router.post(
    "/{centre_id}/tests",
    response_model=CentreTestRead,
    status_code=status.HTTP_201_CREATED,
    summary="Offer a test at a centre with a centre-specific price (admin)",
    responses={**ADMIN_ONLY, **NOT_FOUND, **CONFLICT},
)
def add_centre_test(centre_id: CentreId, data: CentreTestCreate, db: DbSession, _: AdminUser) -> CentreTest:
    return centre_service.add_offering(db, centre_id, data)


@router.patch(
    "/{centre_id}/tests/{test_id}",
    response_model=CentreTestRead,
    summary="Change a centre's price for a test (admin)",
    responses={**ADMIN_ONLY, **NOT_FOUND},
)
def update_centre_test(
    centre_id: CentreId, test_id: TestId, data: CentreTestUpdate, db: DbSession, _: AdminUser
) -> CentreTest:
    return centre_service.update_offering_price(db, centre_id, test_id, data.price)


@router.delete(
    "/{centre_id}/tests/{test_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Stop offering a test at a centre (admin)",
    responses={**ADMIN_ONLY, **NOT_FOUND},
)
def remove_centre_test(centre_id: CentreId, test_id: TestId, db: DbSession, _: AdminUser) -> None:
    centre_service.remove_offering(db, centre_id, test_id)
