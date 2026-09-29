from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError
from app.schemas.common import PaginationParams


def violated_constraint(exc: IntegrityError) -> str | None:
    """Name of the constraint behind an IntegrityError (psycopg exposes it via ``diag``)."""
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None)


def paginate(db: Session, stmt: Select[Any], params: PaginationParams) -> tuple[list[Any], int]:
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    items = list(db.scalars(stmt.limit(params.page_size).offset(params.offset)))
    return items, total


def contains_pattern(term: str) -> str:
    """ILIKE pattern matching ``term`` literally anywhere (escapes LIKE wildcards)."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def commit_or_conflict(db: Session, message: str) -> None:
    """Commit, translating a unique/foreign-key violation into a 409 with ``message``."""
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(message) from exc
