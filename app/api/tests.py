from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from app.api.common import ADMIN_ONLY, CONFLICT, NOT_FOUND, Pagination
from app.dependencies.auth import AdminUser, DbSession
from app.models.test import DiagnosticTest
from app.schemas.centre import CentreTestRead
from app.schemas.common import Page
from app.schemas.test import DiagnosticTestCreate, DiagnosticTestRead, DiagnosticTestUpdate
from app.services import centre_service, diagnostic_test_service

router = APIRouter(prefix="/tests", tags=["Tests"])

TestId = Annotated[int, Path(gt=0, description="Diagnostic test id")]


@router.post(
    "/",
    response_model=DiagnosticTestRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a diagnostic test (admin)",
    responses={**ADMIN_ONLY, **CONFLICT},
)
def create_test(data: DiagnosticTestCreate, db: DbSession, _: AdminUser) -> DiagnosticTest:
    return diagnostic_test_service.create_test(db, data)


@router.get("/", response_model=Page[DiagnosticTestRead], summary="List diagnostic tests")
def list_tests(
    db: DbSession,
    pagination: Pagination,
    search: Annotated[
        str | None, Query(max_length=200, description="Case-insensitive match on name or description")
    ] = None,
) -> Page[DiagnosticTestRead]:
    items, total = diagnostic_test_service.list_tests(db, pagination, search=search)
    return Page[DiagnosticTestRead].build([DiagnosticTestRead.model_validate(t) for t in items], total, pagination)


@router.get("/{test_id}", response_model=DiagnosticTestRead, summary="Get a diagnostic test", responses=NOT_FOUND)
def get_test(test_id: TestId, db: DbSession) -> DiagnosticTest:
    return diagnostic_test_service.get_test(db, test_id)


@router.get(
    "/{test_id}/centres",
    response_model=Page[CentreTestRead],
    summary="List centres offering this test with their prices (cheapest first)",
    responses=NOT_FOUND,
)
def list_test_centres(test_id: TestId, db: DbSession, pagination: Pagination) -> Page[CentreTestRead]:
    items, total = centre_service.list_test_offerings(db, test_id, pagination)
    return Page[CentreTestRead].build([CentreTestRead.model_validate(o) for o in items], total, pagination)


@router.patch(
    "/{test_id}",
    response_model=DiagnosticTestRead,
    summary="Update a diagnostic test (admin)",
    responses={**ADMIN_ONLY, **NOT_FOUND, **CONFLICT},
)
def update_test(test_id: TestId, data: DiagnosticTestUpdate, db: DbSession, _: AdminUser) -> DiagnosticTest:
    return diagnostic_test_service.update_test(db, test_id, data)


@router.delete(
    "/{test_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a diagnostic test (admin). Refused if the test has bookings.",
    responses={**ADMIN_ONLY, **NOT_FOUND, **CONFLICT},
)
def delete_test(test_id: TestId, db: DbSession, _: AdminUser) -> None:
    diagnostic_test_service.delete_test(db, test_id)
