from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_settings = get_settings()

engine = create_engine(_settings.database_url, echo=_settings.db_echo, pool_pre_ping=True)

# expire_on_commit=False lets services return ORM objects after committing; server-side
# column values (timestamps) are fetched eagerly via RETURNING (see Base mapper args).
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """Request-scoped session. Services own commit boundaries; anything uncommitted is
    rolled back when the request ends (including on error)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()
