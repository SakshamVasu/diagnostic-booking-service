from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.db.utils import commit_or_conflict, contains_pattern, paginate
from app.models.booking import Booking
from app.models.test import DiagnosticTest
from app.schemas.common import PaginationParams
from app.schemas.test import DiagnosticTestCreate, DiagnosticTestUpdate

DUPLICATE_TEST_MESSAGE = "A test with this name already exists"
TEST_IN_USE_MESSAGE = "Test has bookings and cannot be deleted"


def get_test(db: Session, test_id: int) -> DiagnosticTest:
    test = db.get(DiagnosticTest, test_id)
    if test is None:
        raise NotFoundError("Test not found")
    return test


def list_tests(db: Session, params: PaginationParams, search: str | None = None) -> tuple[list[DiagnosticTest], int]:
    stmt = select(DiagnosticTest).order_by(DiagnosticTest.id)
    if search:
        pattern = contains_pattern(search)
        stmt = stmt.where(
            or_(
                DiagnosticTest.name.ilike(pattern, escape="\\"),
                DiagnosticTest.description.ilike(pattern, escape="\\"),
            )
        )
    return paginate(db, stmt, params)


def create_test(db: Session, data: DiagnosticTestCreate) -> DiagnosticTest:
    test = DiagnosticTest(**data.model_dump())
    db.add(test)
    commit_or_conflict(db, DUPLICATE_TEST_MESSAGE)
    return test


def update_test(db: Session, test_id: int, data: DiagnosticTestUpdate) -> DiagnosticTest:
    test = get_test(db, test_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(test, field, value)
    commit_or_conflict(db, DUPLICATE_TEST_MESSAGE)
    return test


def delete_test(db: Session, test_id: int) -> None:
    test = get_test(db, test_id)
    if db.scalar(select(exists().where(Booking.test_id == test_id))):
        raise ConflictError(TEST_IN_USE_MESSAGE)
    db.delete(test)  # centre offerings are removed by ON DELETE CASCADE
    commit_or_conflict(db, TEST_IN_USE_MESSAGE)
