from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, UnauthorizedError
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
    verify_password_against_dummy,
)
from app.models.user import User, UserRole
from app.schemas.auth import SignupRequest

DUPLICATE_EMAIL_MESSAGE = "An account with this email already exists"
INVALID_CREDENTIALS_MESSAGE = "Incorrect email or password"


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


def signup(db: Session, data: SignupRequest) -> User:
    if get_user_by_email(db, data.email) is not None:
        raise ConflictError(DUPLICATE_EMAIL_MESSAGE)

    user = User(
        name=data.name,
        email=data.email,
        password_hash=hash_password(data.password),
        role=UserRole.USER,  # admins are never created through public signup
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:  # concurrent signup with the same email
        db.rollback()
        raise ConflictError(DUPLICATE_EMAIL_MESSAGE) from exc
    return user


def login(db: Session, email: str, password: str) -> str:
    user = get_user_by_email(db, email)
    if user is None:
        verify_password_against_dummy(password)
        raise UnauthorizedError(INVALID_CREDENTIALS_MESSAGE)
    if not verify_password(password, user.password_hash) or not user.is_active:
        raise UnauthorizedError(INVALID_CREDENTIALS_MESSAGE)
    return create_access_token(user.id)


def get_user_from_token(db: Session, token: str) -> User:
    user = db.get(User, decode_access_token(token))
    if user is None or not user.is_active:
        raise UnauthorizedError()
    return user
