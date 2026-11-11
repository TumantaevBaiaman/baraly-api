from datetime import UTC, datetime
from uuid import UUID

from app.application.dto import Page, PageParams, UserFilters
from app.application.exceptions import AuthenticationError
from app.application.ports.auth import PasswordHasher, RefreshTokenRepository, TokenService
from app.application.ports.company import MembershipRepository
from app.application.ports.storage import ImageStorage
from app.application.ports.unit_of_work import UnitOfWork
from app.application.ports.users import UserRepository
from app.application.use_cases.images import store_image, user_images_prefix
from app.domain.exceptions import ConflictError, DomainError, NotFoundError
from app.domain.user import ProfileUpdate, User, UserRole, normalize_email, validate_password


class GetCurrentUser:
    def __init__(self, users: UserRepository, tokens: TokenService) -> None:
        self._users = users
        self._tokens = tokens

    async def execute(self, access_token: str) -> User:
        user_id = self._tokens.read_access_token(access_token)
        user = await self._users.get_by_id(user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("Invalid access token")
        return user


class UpdateProfile:
    def __init__(self, users: UserRepository, unit_of_work: UnitOfWork) -> None:
        self._users = users
        self._unit_of_work = unit_of_work

    async def execute(self, user: User, changes: ProfileUpdate) -> User:
        user.update_profile(changes, datetime.now(UTC))
        await self._users.save(user)
        await self._unit_of_work.commit()
        return user


class ChangeAvatar:
    """Свой аватар заменяет фото из Google; без него в профиле нет фото."""

    def __init__(
        self,
        users: UserRepository,
        unit_of_work: UnitOfWork,
        storage: ImageStorage,
        max_bytes: int,
    ) -> None:
        self._users = users
        self._unit_of_work = unit_of_work
        self._storage = storage
        self._max_bytes = max_bytes

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    async def upload(self, user: User, data: bytes) -> User:
        prefix = f"{user_images_prefix(user.id)}avatars/"
        url = await store_image(self._storage, self._max_bytes, prefix, data)
        return await self._save(user, url)

    async def remove(self, user: User) -> User:
        return await self._save(user, None)

    async def _save(self, user: User, avatar_url: str | None) -> User:
        user.change_avatar(avatar_url, datetime.now(UTC))
        await self._users.save(user)
        await self._unit_of_work.commit()
        return user


class ManageUsers:
    """Админ назначает роли и пароли сотрудников для входа в админку."""

    def __init__(
        self,
        users: UserRepository,
        unit_of_work: UnitOfWork,
        hasher: PasswordHasher,
        refresh_tokens: RefreshTokenRepository,
        memberships: MembershipRepository,
    ) -> None:
        self._users = users
        self._unit_of_work = unit_of_work
        self._hasher = hasher
        self._refresh_tokens = refresh_tokens
        self._memberships = memberships

    async def search(self, filters: UserFilters, page: PageParams) -> Page[User]:
        return await self._users.search(filters, page)

    async def get(self, user_id: UUID) -> User:
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise NotFoundError("User not found")
        return user

    async def set_role(self, actor: User, user_id: UUID, role: UserRole) -> User:
        if actor.id == user_id:
            raise ConflictError("You cannot change your own role")
        user = await self.get(user_id)
        await self._users.set_role(user.id, role)
        if role is UserRole.USER and user.is_staff:
            # Бывший сотрудник теряет пароль от админки и все открытые сессии.
            await self._users.set_password_hash(user.id, None)
            await self._refresh_tokens.revoke_all_for_user(user.id, datetime.now(UTC))
        await self._unit_of_work.commit()
        return await self.get(user_id)

    async def set_password(self, actor: User, user_id: UUID, password: str) -> User:
        user = await self.get(user_id)
        if not user.is_staff and not await self._memberships.list_for_user(user.id):
            raise DomainError("Passwords are only for platform staff and company members")
        validate_password(password)
        await self._users.set_password_hash(user.id, self._hasher.hash(password))
        if actor.id != user.id:
            await self._refresh_tokens.revoke_all_for_user(user.id, datetime.now(UTC))
        await self._unit_of_work.commit()
        return user

    async def create_staff(
        self,
        actor: User | None,
        *,
        email: str,
        role: UserRole,
        password: str,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> User:
        """Создаёт сотрудника или делает сотрудником существующего пользователя.
        actor=None используется командой создания первого админа."""
        if role is UserRole.USER:
            raise DomainError("Staff role must be admin or moderator")
        validate_password(password)
        email = normalize_email(email)
        user = await self._users.get_by_email(email)
        if user is None:
            user = User.register(
                email=email,
                role=role,
                first_name=first_name,
                last_name=last_name,
                now=datetime.now(UTC),
            )
            await self._users.add(user)
        else:
            if actor is not None and actor.id == user.id:
                raise ConflictError("You cannot change your own role")
            await self._users.set_role(user.id, role)
        await self._users.set_password_hash(user.id, self._hasher.hash(password))
        await self._unit_of_work.commit()
        return await self.get(user.id)


class ChangeOwnPassword:
    """Сотрудник меняет свой пароль; остальные его сессии закрываются."""

    def __init__(
        self,
        users: UserRepository,
        hasher: PasswordHasher,
        refresh_tokens: RefreshTokenRepository,
        unit_of_work: UnitOfWork,
    ) -> None:
        self._users = users
        self._hasher = hasher
        self._refresh_tokens = refresh_tokens
        self._unit_of_work = unit_of_work

    async def execute(self, user: User, current_password: str, new_password: str) -> None:
        credentials = await self._users.get_credentials(user.email)
        if (
            credentials is None
            or credentials.password_hash is None
            or not self._hasher.verify(credentials.password_hash, current_password)
        ):
            raise AuthenticationError("Current password is incorrect")
        validate_password(new_password)
        await self._users.set_password_hash(user.id, self._hasher.hash(new_password))
        await self._refresh_tokens.revoke_all_for_user(user.id, datetime.now(UTC))
        await self._unit_of_work.commit()
