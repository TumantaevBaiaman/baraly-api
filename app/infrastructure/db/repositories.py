from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dto import Page, PageParams, UserFilters
from app.application.ports.auth import StaffCredentials
from app.domain.auth import AuthProvider, RefreshToken
from app.domain.user import User, UserRole
from app.infrastructure.db.errors import conflict_on_integrity_error
from app.infrastructure.db.models import AuthIdentityModel, RefreshTokenModel, UserModel


def _to_user(model: UserModel) -> User:
    return User(
        id=model.id,
        email=model.email,
        first_name=model.first_name,
        last_name=model.last_name,
        phone=model.phone,
        avatar_url=model.avatar_url,
        is_active=model.is_active,
        role=model.role,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _to_refresh_token(model: RefreshTokenModel) -> RefreshToken:
    return RefreshToken(
        id=model.id,
        user_id=model.user_id,
        token_hash=model.token_hash,
        expires_at=model.expires_at,
        revoked_at=model.revoked_at,
        created_at=model.created_at,
    )


async def _flush(session: AsyncSession) -> None:
    with conflict_on_integrity_error("Account already exists, please try again"):
        await session.flush()


class SqlAlchemyUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: UUID) -> User | None:
        model = await self._session.get(UserModel, user_id)
        return _to_user(model) if model else None

    async def get_by_email(self, email: str) -> User | None:
        model = await self._session.scalar(select(UserModel).where(UserModel.email == email))
        return _to_user(model) if model else None

    async def get_by_identity(self, provider: AuthProvider, subject: str) -> User | None:
        model = await self._session.scalar(
            select(UserModel)
            .join(AuthIdentityModel, AuthIdentityModel.user_id == UserModel.id)
            .where(AuthIdentityModel.provider == provider, AuthIdentityModel.subject == subject)
        )
        return _to_user(model) if model else None

    async def add(self, user: User) -> None:
        self._session.add(
            UserModel(
                id=user.id,
                email=user.email,
                first_name=user.first_name,
                last_name=user.last_name,
                phone=user.phone,
                avatar_url=user.avatar_url,
                is_active=user.is_active,
                role=user.role,
                created_at=user.created_at,
                updated_at=user.updated_at,
            )
        )
        await _flush(self._session)

    async def add_identity(self, user_id: UUID, provider: AuthProvider, subject: str) -> None:
        self._session.add(
            AuthIdentityModel(
                provider=provider,
                subject=subject,
                user_id=user_id,
                created_at=datetime.now(UTC),
            )
        )
        await _flush(self._session)

    async def set_role(self, user_id: UUID, role: UserRole) -> None:
        await self._session.execute(
            update(UserModel).where(UserModel.id == user_id).values(role=role)
        )

    async def search(self, filters: UserFilters, page: PageParams) -> Page[User]:
        conditions: list[Any] = []
        if filters.query:
            pattern = filters.query
            conditions.append(
                or_(
                    UserModel.email.icontains(pattern, autoescape=True),
                    UserModel.first_name.icontains(pattern, autoescape=True),
                    UserModel.last_name.icontains(pattern, autoescape=True),
                )
            )
        if filters.role:
            conditions.append(UserModel.role == filters.role)
        total = await self._session.scalar(
            select(func.count()).select_from(UserModel).where(*conditions)
        )
        models = await self._session.scalars(
            select(UserModel)
            .where(*conditions)
            .order_by(UserModel.created_at.desc(), UserModel.id)
            .limit(page.limit)
            .offset(page.offset)
        )
        return Page(
            items=[_to_user(model) for model in models],
            total=total or 0,
            limit=page.limit,
            offset=page.offset,
        )

    async def get_credentials(self, email: str) -> StaffCredentials | None:
        model = await self._session.scalar(select(UserModel).where(UserModel.email == email))
        if model is None:
            return None
        return StaffCredentials(
            user=_to_user(model),
            password_hash=model.password_hash,
            failed_login_count=model.failed_login_count,
            locked_until=model.locked_until,
        )

    async def set_password_hash(self, user_id: UUID, password_hash: str | None) -> None:
        await self._session.execute(
            update(UserModel)
            .where(UserModel.id == user_id)
            .values(password_hash=password_hash, failed_login_count=0, locked_until=None)
        )

    async def record_failed_login(
        self, user_id: UUID, failed_count: int, locked_until: datetime | None
    ) -> None:
        await self._session.execute(
            update(UserModel)
            .where(UserModel.id == user_id)
            .values(failed_login_count=failed_count, locked_until=locked_until)
        )

    async def reset_failed_logins(self, user_id: UUID) -> None:
        await self._session.execute(
            update(UserModel)
            .where(UserModel.id == user_id)
            .values(failed_login_count=0, locked_until=None)
        )

    async def save(self, user: User) -> None:
        await self._session.execute(
            update(UserModel)
            .where(UserModel.id == user.id)
            .values(
                first_name=user.first_name,
                last_name=user.last_name,
                phone=user.phone,
                avatar_url=user.avatar_url,
                updated_at=user.updated_at,
            )
            .execution_options(synchronize_session=False)
        )


class SqlAlchemyRefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, token: RefreshToken) -> None:
        self._session.add(
            RefreshTokenModel(
                id=token.id,
                user_id=token.user_id,
                token_hash=token.token_hash,
                expires_at=token.expires_at,
                revoked_at=token.revoked_at,
                created_at=token.created_at,
            )
        )
        await _flush(self._session)

    async def get_by_hash_for_update(self, token_hash: str) -> RefreshToken | None:
        # Блокировка не даёт двум параллельным запросам обменять один токен дважды.
        model = await self._session.scalar(
            select(RefreshTokenModel)
            .where(RefreshTokenModel.token_hash == token_hash)
            .with_for_update()
        )
        return _to_refresh_token(model) if model else None

    async def save(self, token: RefreshToken) -> None:
        await self._session.execute(
            update(RefreshTokenModel)
            .where(RefreshTokenModel.id == token.id)
            .values(revoked_at=token.revoked_at)
            .execution_options(synchronize_session=False)
        )

    async def revoke_all_for_user(self, user_id: UUID, now: datetime) -> None:
        await self._session.execute(
            update(RefreshTokenModel)
            .where(RefreshTokenModel.user_id == user_id, RefreshTokenModel.revoked_at.is_(None))
            .values(revoked_at=now)
            .execution_options(synchronize_session=False)
        )


class SqlAlchemyUnitOfWork:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()
