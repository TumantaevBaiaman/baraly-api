from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.auth import ExternalIdentity, RefreshToken
from app.domain.user import User


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...

    def verify(self, password_hash: str, password: str) -> bool: ...


@dataclass(frozen=True, kw_only=True)
class StaffCredentials:
    user: User
    password_hash: str | None
    failed_login_count: int
    locked_until: datetime | None


class IdentityVerifier(Protocol):
    async def verify(self, id_token: str) -> ExternalIdentity: ...


@dataclass(frozen=True, kw_only=True)
class NewRefreshToken:
    value: str
    token_hash: str
    expires_at: datetime


class TokenService(Protocol):
    @property
    def access_token_ttl_seconds(self) -> int: ...

    def issue_access_token(self, user_id: UUID, now: datetime) -> str: ...

    def read_access_token(self, token: str) -> UUID: ...

    def new_refresh_token(self, now: datetime) -> NewRefreshToken: ...

    def hash_refresh_token(self, value: str) -> str: ...


class RefreshTokenRepository(Protocol):
    async def add(self, token: RefreshToken) -> None: ...

    async def get_by_hash_for_update(self, token_hash: str) -> RefreshToken | None: ...

    async def save(self, token: RefreshToken) -> None: ...

    async def revoke_all_for_user(self, user_id: UUID, now: datetime) -> None: ...
