from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4


class AuthProvider(StrEnum):
    GOOGLE = "google"
    APPLE = "apple"


@dataclass(frozen=True, kw_only=True)
class ExternalIdentity:
    """Данные пользователя из проверенного токена Google или Apple."""

    provider: AuthProvider
    subject: str
    email: str | None = None
    email_verified: bool = False
    first_name: str | None = None
    last_name: str | None = None
    avatar_url: str | None = None


@dataclass(kw_only=True)
class RefreshToken:
    user_id: UUID
    token_hash: str
    expires_at: datetime
    created_at: datetime
    id: UUID = field(default_factory=uuid4)
    revoked_at: datetime | None = None

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    def revoke(self, now: datetime) -> None:
        self.revoked_at = now
