from fastapi import APIRouter, status

from app.core.config import get_settings
from app.dependencies.auth import CurrentUser, DbSession
from app.models.user import User
from app.schemas.auth import LoginRequest, SignupRequest, TokenResponse, UserRead
from app.schemas.common import ErrorResponse
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/signup",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
    responses={409: {"model": ErrorResponse, "description": "Email already registered"}},
)
def signup(data: SignupRequest, db: DbSession) -> User:
    return auth_service.signup(db, data)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Exchange email and password for a JWT access token",
    responses={401: {"model": ErrorResponse, "description": "Invalid credentials"}},
)
def login(data: LoginRequest, db: DbSession) -> TokenResponse:
    token = auth_service.login(db, data.email, data.password)
    return TokenResponse(access_token=token, expires_in=get_settings().access_token_expire_minutes * 60)


@router.get("/me", response_model=UserRead, summary="Get the authenticated user's profile")
def me(user: CurrentUser) -> User:
    return user
