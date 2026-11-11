from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from app.application.use_cases.auth import TokenPair
from app.domain.auth import AuthProvider
from app.domain.user import MAX_NAME_LENGTH, MAX_PASSWORD_LENGTH
from app.presentation.api.dependencies import (
    PasswordSignInDep,
    RefreshSessionDep,
    SignInDep,
    SignOutDep,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class GoogleSignInRequest(BaseModel):
    id_token: str = Field(min_length=1, max_length=8192)


class AppleSignInRequest(BaseModel):
    id_token: str = Field(min_length=1, max_length=8192)
    first_name: str | None = Field(
        default=None,
        max_length=MAX_NAME_LENGTH,
        description="Apple сообщает имя только клиенту и только при первом входе.",
    )
    last_name: str | None = Field(default=None, max_length=MAX_NAME_LENGTH)


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=512)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int

    @classmethod
    def from_pair(cls, pair: TokenPair) -> "TokenResponse":
        return cls(
            access_token=pair.access_token,
            refresh_token=pair.refresh_token,
            expires_in=pair.expires_in,
        )


@router.post("/google")
async def sign_in_with_google(body: GoogleSignInRequest, use_case: SignInDep) -> TokenResponse:
    pair = await use_case.execute(AuthProvider.GOOGLE, body.id_token)
    return TokenResponse.from_pair(pair)


@router.post("/apple")
async def sign_in_with_apple(body: AppleSignInRequest, use_case: SignInDep) -> TokenResponse:
    pair = await use_case.execute(
        AuthProvider.APPLE, body.id_token, body.first_name, body.last_name
    )
    return TokenResponse.from_pair(pair)


@router.post("/refresh")
async def refresh(body: RefreshTokenRequest, use_case: RefreshSessionDep) -> TokenResponse:
    return TokenResponse.from_pair(await use_case.execute(body.refresh_token))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshTokenRequest, use_case: SignOutDep) -> None:
    await use_case.execute(body.refresh_token)


class PasswordSignInRequest(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


@router.post("/login")
async def sign_in_with_password(
    body: PasswordSignInRequest, use_case: PasswordSignInDep
) -> TokenResponse:
    """Вход по почте и паролю для админов, модераторов и сотрудников компаний.

    Пароль задаёт администратор или владелец компании при создании аккаунта."""
    return TokenResponse.from_pair(await use_case.execute(body.email, body.password))
