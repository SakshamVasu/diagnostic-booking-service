from decimal import Decimal

from sqlalchemy import Select, exists, select
from sqlalchemy.orm import Session, joinedload

from app.core.exceptions import ConflictError, NotFoundError
from app.db.utils import commit_or_conflict, contains_pattern, paginate
from app.models.booking import Booking
from app.models.centre import CentreTest, DiagnosticCentre
from app.schemas.centre import CentreCreate, CentreTestCreate, CentreUpdate
from app.schemas.common import PaginationParams
from app.services import diagnostic_test_service

DUPLICATE_CENTRE_MESSAGE = "A centre with this name already exists at this location"
CENTRE_IN_USE_MESSAGE = "Centre has bookings and cannot be deleted"


def get_centre(db: Session, centre_id: int) -> DiagnosticCentre:
    centre = db.get(DiagnosticCentre, centre_id)
    if centre is None:
        raise NotFoundError("Centre not found")
    return centre


def list_centres(
    db: Session, params: PaginationParams, location: str | None = None, search: str | None = None
) -> tuple[list[DiagnosticCentre], int]:
    stmt = select(DiagnosticCentre).order_by(DiagnosticCentre.id)
    if location:
        stmt = stmt.where(DiagnosticCentre.location.ilike(contains_pattern(location), escape="\\"))
    if search:
        stmt = stmt.where(DiagnosticCentre.name.ilike(contains_pattern(search), escape="\\"))
    return paginate(db, stmt, params)


def create_centre(db: Session, data: CentreCreate) -> DiagnosticCentre:
    centre = DiagnosticCentre(**data.model_dump())
    db.add(centre)
    commit_or_conflict(db, DUPLICATE_CENTRE_MESSAGE)
    return centre


def update_centre(db: Session, centre_id: int, data: CentreUpdate) -> DiagnosticCentre:
    centre = get_centre(db, centre_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(centre, field, value)
    commit_or_conflict(db, DUPLICATE_CENTRE_MESSAGE)
    return centre


def delete_centre(db: Session, centre_id: int) -> None:
    centre = get_centre(db, centre_id)
    if db.scalar(select(exists().where(Booking.centre_id == centre_id))):
        raise ConflictError(CENTRE_IN_USE_MESSAGE)
    db.delete(centre)  # its test offerings are removed by ON DELETE CASCADE
    commit_or_conflict(db, CENTRE_IN_USE_MESSAGE)


# --- Tests offered by a centre (centre_tests association) ---


def _offering_query() -> Select[tuple[CentreTest]]:
    return select(CentreTest).options(joinedload(CentreTest.centre), joinedload(CentreTest.test))


def get_offering(db: Session, centre_id: int, test_id: int) -> CentreTest:
    offering = db.scalar(_offering_query().where(CentreTest.centre_id == centre_id, CentreTest.test_id == test_id))
    if offering is None:
        raise NotFoundError("This centre does not offer this test")
    return offering


def list_centre_offerings(db: Session, centre_id: int, params: PaginationParams) -> tuple[list[CentreTest], int]:
    get_centre(db, centre_id)
    stmt = _offering_query().where(CentreTest.centre_id == centre_id).order_by(CentreTest.test_id)
    return paginate(db, stmt, params)


def list_test_offerings(db: Session, test_id: int, params: PaginationParams) -> tuple[list[CentreTest], int]:
    diagnostic_test_service.get_test(db, test_id)
    stmt = _offering_query().where(CentreTest.test_id == test_id).order_by(CentreTest.price, CentreTest.centre_id)
    return paginate(db, stmt, params)


def add_offering(db: Session, centre_id: int, data: CentreTestCreate) -> CentreTest:
    centre = get_centre(db, centre_id)
    test = diagnostic_test_service.get_test(db, data.test_id)
    offering = CentreTest(centre=centre, test=test, price=data.price if data.price is not None else test.base_price)
    db.add(offering)
    commit_or_conflict(db, "This centre already offers this test")
    return offering


def update_offering_price(db: Session, centre_id: int, test_id: int, price: Decimal) -> CentreTest:
    offering = get_offering(db, centre_id, test_id)
    offering.price = price  # existing bookings keep the price they were created with
    db.commit()
    return offering


def remove_offering(db: Session, centre_id: int, test_id: int) -> None:
    db.delete(get_offering(db, centre_id, test_id))
    db.commit()
