import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TypedDict
from uuid import UUID, uuid4

from app.domain.exceptions import DomainError

MAX_NAME_LENGTH = 100
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128
_PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")
_PHONE_SEPARATORS_RE = re.compile(r"[\s()-]")


def validate_password(password: str) -> None:
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise DomainError(
            f"Password must be {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} characters long"
        )
    if password.isdigit() or password.isalpha():
        raise DomainError("Password must contain letters and digits or symbols")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def normalize_name(name: str | None) -> str | None:
    cleaned = name.strip() if name else None
    if not cleaned:
        return None
    if len(cleaned) > MAX_NAME_LENGTH:
        raise DomainError(f"Name must be at most {MAX_NAME_LENGTH} characters")
    return cleaned


def normalize_phone(phone: str | None) -> str | None:
    """Приводит телефон к формату E.164: +996502800202."""
    if not phone or not phone.strip():
        return None
    cleaned = _PHONE_SEPARATORS_RE.sub("", phone)
    if not _PHONE_RE.fullmatch(cleaned):
        raise DomainError("Phone must be in international format, e.g. +996502800202")
    return cleaned


class UserRole(StrEnum):
    USER = "user"
    # Модератор видит все туры, одобряет и скрывает их, но не управляет платформой.
    MODERATOR = "moderator"
    ADMIN = "admin"


class ProfileUpdate(TypedDict, total=False):
    first_name: str | None
    last_name: str | None
    phone: str | None


@dataclass(kw_only=True)
class User:
    email: str
    created_at: datetime
    updated_at: datetime
    id: UUID = field(default_factory=uuid4)
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    avatar_url: str | None = None
    is_active: bool = True
    role: UserRole = UserRole.USER

    @property
    def is_admin(self) -> bool:
        return self.role is UserRole.ADMIN

    @property
    def is_staff(self) -> bool:
        return self.role in (UserRole.ADMIN, UserRole.MODERATOR)

    @classmethod
    def register(
        cls,
        *,
        email: str,
        now: datetime,
        first_name: str | None = None,
        last_name: str | None = None,
        avatar_url: str | None = None,
        role: UserRole = UserRole.USER,
    ) -> "User":
        return cls(
            email=normalize_email(email),
            first_name=normalize_name(first_name),
            last_name=normalize_name(last_name),
            avatar_url=avatar_url,
            role=role,
            created_at=now,
            updated_at=now,
        )

    def update_profile(self, changes: ProfileUpdate, now: datetime) -> None:
        if "first_name" in changes:
            self.first_name = normalize_name(changes["first_name"])
        if "last_name" in changes:
            self.last_name = normalize_name(changes["last_name"])
        if "phone" in changes:
            self.phone = normalize_phone(changes["phone"])
        self.updated_at = now

    def change_avatar(self, avatar_url: str | None, now: datetime) -> None:
        self.avatar_url = avatar_url
        self.updated_at = now
