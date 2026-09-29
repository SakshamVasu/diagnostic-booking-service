from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.db.database import get_db
from app.models.user import User
from app.services import auth_service

DbSession = Annotated[Session, Depends(get_db)]

# auto_error=False so a missing header yields our own consistent 401 (FastAPI's default is 403).
_bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token from POST /auth/login")


def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError("Not authenticated")
    return auth_service.get_user_from_token(db, credentials.credentials)


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> User:
    if not user.is_admin:
        raise ForbiddenError("Admin privileges required")
    return user


AdminUser = Annotated[User, Depends(require_admin)]
