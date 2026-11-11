from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.application.exceptions import AuthenticationError
from app.application.ports.auth import (
    IdentityVerifier,
    PasswordHasher,
    RefreshTokenRepository,
    TokenService,
)
from app.application.ports.company import InvitationRepository
from app.application.ports.unit_of_work import UnitOfWork
from app.application.ports.users import UserRepository
from app.domain.auth import AuthProvider, ExternalIdentity, RefreshToken
from app.domain.user import User, normalize_email


@dataclass(frozen=True, kw_only=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


async def _issue_tokens(
    user: User,
    now: datetime,
    tokens: TokenService,
    refresh_tokens: RefreshTokenRepository,
) -> TokenPair:
    new_refresh = tokens.new_refresh_token(now)
    await refresh_tokens.add(
        RefreshToken(
            user_id=user.id,
            token_hash=new_refresh.token_hash,
            expires_at=new_refresh.expires_at,
            created_at=now,
        )
    )
    return TokenPair(
        access_token=tokens.issue_access_token(user.id, now),
        refresh_token=new_refresh.value,
        expires_in=tokens.access_token_ttl_seconds,
    )


async def _finish_sign_in(
    user: User,
    now: datetime,
    *,
    users: UserRepository,
    invitations: InvitationRepository,
    tokens: TokenService,
    refresh_tokens: RefreshTokenRepository,
    unit_of_work: UnitOfWork,
) -> TokenPair:
    if not user.is_active:
        raise AuthenticationError("Account is disabled")
    # Email подтверждён, поэтому приглашения в компании можно принять.
    await invitations.claim(user.id, user.email, now)
    pair = await _issue_tokens(user, now, tokens, refresh_tokens)
    await unit_of_work.commit()
    return pair


class SignInWithProvider:
    def __init__(
        self,
        verifiers: Mapping[AuthProvider, IdentityVerifier],
        users: UserRepository,
        refresh_tokens: RefreshTokenRepository,
        tokens: TokenService,
        unit_of_work: UnitOfWork,
        invitations: InvitationRepository,
    ) -> None:
        self._verifiers = verifiers
        self._users = users
        self._refresh_tokens = refresh_tokens
        self._tokens = tokens
        self._unit_of_work = unit_of_work
        self._invitations = invitations

    async def execute(
        self,
        provider: AuthProvider,
        id_token: str,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> TokenPair:
        """first_name и last_name передаёт клиент: Apple не кладёт имя в id_token."""
        identity = await self._verifiers[provider].verify(id_token)
        now = datetime.now(UTC)

        user = await self._users.get_by_identity(identity.provider, identity.subject)
        if user is None:
            user = await self._attach_identity(identity, first_name, last_name, now)
        return await _finish_sign_in(
            user,
            now,
            users=self._users,
            invitations=self._invitations,
            tokens=self._tokens,
            refresh_tokens=self._refresh_tokens,
            unit_of_work=self._unit_of_work,
        )

    async def _attach_identity(
        self,
        identity: ExternalIdentity,
        first_name: str | None,
        last_name: str | None,
        now: datetime,
    ) -> User:
        # Связывать аккаунты по email безопасно только с подтверждённым адресом.
        if not identity.email or not identity.email_verified:
            raise AuthenticationError("Provider did not return a verified email")

        user = await self._users.get_by_email(normalize_email(identity.email))
        if user is None:
            user = User.register(
                email=identity.email,
                first_name=identity.first_name or first_name,
                last_name=identity.last_name or last_name,
                avatar_url=identity.avatar_url,
                now=now,
            )
            await self._users.add(user)
        await self._users.add_identity(user.id, identity.provider, identity.subject)
        return user


class RefreshSession:
    def __init__(
        self,
        users: UserRepository,
        refresh_tokens: RefreshTokenRepository,
        tokens: TokenService,
        unit_of_work: UnitOfWork,
    ) -> None:
        self._users = users
        self._refresh_tokens = refresh_tokens
        self._tokens = tokens
        self._unit_of_work = unit_of_work

    async def execute(self, refresh_token: str) -> TokenPair:
        now = datetime.now(UTC)
        token_hash = self._tokens.hash_refresh_token(refresh_token)
        current = await self._refresh_tokens.get_by_hash_for_update(token_hash)
        if current is None:
            raise AuthenticationError("Invalid refresh token")

        if current.is_revoked:
            # Повторное использование: токен мог быть украден, закрываем все сессии.
            await self._refresh_tokens.revoke_all_for_user(current.user_id, now)
            await self._unit_of_work.commit()
            raise AuthenticationError("Invalid refresh token")
        if current.is_expired(now):
            raise AuthenticationError("Invalid refresh token")

        user = await self._users.get_by_id(current.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("Invalid refresh token")

        current.revoke(now)
        await self._refresh_tokens.save(current)
        pair = await _issue_tokens(user, now, self._tokens, self._refresh_tokens)
        await self._unit_of_work.commit()
        return pair


class SignOut:
    def __init__(
        self,
        refresh_tokens: RefreshTokenRepository,
        tokens: TokenService,
        unit_of_work: UnitOfWork,
    ) -> None:
        self._refresh_tokens = refresh_tokens
        self._tokens = tokens
        self._unit_of_work = unit_of_work

    async def execute(self, refresh_token: str) -> None:
        token_hash = self._tokens.hash_refresh_token(refresh_token)
        current = await self._refresh_tokens.get_by_hash_for_update(token_hash)
        if current is None or current.is_revoked:
            return
        current.revoke(datetime.now(UTC))
        await self._refresh_tokens.save(current)
        await self._unit_of_work.commit()


MAX_FAILED_LOGINS = 5
LOGIN_LOCK = timedelta(minutes=15)
_INVALID_CREDENTIALS = "Invalid email or password"


class PasswordSignIn:
    """Вход по email и паролю: сотрудники платформы и сотрудники компаний.

    Пять неверных паролей подряд блокируют вход на 15 минут. Ошибка одна и та же
    для неизвестного email и неверного пароля, чтобы нельзя было перебирать адреса."""

    def __init__(
        self,
        users: UserRepository,
        hasher: PasswordHasher,
        refresh_tokens: RefreshTokenRepository,
        tokens: TokenService,
        unit_of_work: UnitOfWork,
        dummy_hash: str,
    ) -> None:
        self._users = users
        self._hasher = hasher
        self._refresh_tokens = refresh_tokens
        self._tokens = tokens
        self._unit_of_work = unit_of_work
        self._dummy_hash = dummy_hash

    async def execute(self, email: str, password: str) -> TokenPair:
        now = datetime.now(UTC)
        credentials = await self._users.get_credentials(normalize_email(email))
        if credentials is None or credentials.password_hash is None:
            # Проверка заглушки выравнивает время ответа для несуществующих аккаунтов.
            self._hasher.verify(self._dummy_hash, password)
            raise AuthenticationError(_INVALID_CREDENTIALS)
        user = credentials.user
        if credentials.locked_until is not None and credentials.locked_until > now:
            raise AuthenticationError("Too many failed attempts; try again later")
        if not self._hasher.verify(credentials.password_hash, password):
            failed = credentials.failed_login_count + 1
            locked_until = now + LOGIN_LOCK if failed >= MAX_FAILED_LOGINS else None
            await self._users.record_failed_login(
                user.id, 0 if locked_until else failed, locked_until
            )
            await self._unit_of_work.commit()
            raise AuthenticationError(_INVALID_CREDENTIALS)
        if not user.is_active:
            raise AuthenticationError("Account is disabled")
        await self._users.reset_failed_logins(user.id)
        pair = await _issue_tokens(user, now, self._tokens, self._refresh_tokens)
        await self._unit_of_work.commit()
        return pair
