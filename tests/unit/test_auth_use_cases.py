from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.application.dto import Page, PageParams, UserFilters
from app.application.exceptions import AuthenticationError
from app.application.use_cases.auth import RefreshSession, SignInWithProvider, SignOut
from app.domain.auth import AuthProvider, ExternalIdentity, RefreshToken
from app.domain.user import User, UserRole
from app.infrastructure.auth.tokens import JwtTokenService

SECRET = "test-secret-test-secret-test-secret"


class FakeVerifier:
    def __init__(self, identity: ExternalIdentity) -> None:
        self.identity = identity

    async def verify(self, id_token: str) -> ExternalIdentity:
        return self.identity


class FakeUsers:
    def __init__(self) -> None:
        self.users: dict[UUID, User] = {}
        self.identities: dict[tuple[AuthProvider, str], UUID] = {}

    async def get_by_id(self, user_id: UUID) -> User | None:
        return self.users.get(user_id)

    async def get_by_email(self, email: str) -> User | None:
        return next((u for u in self.users.values() if u.email == email), None)

    async def get_by_identity(self, provider: AuthProvider, subject: str) -> User | None:
        user_id = self.identities.get((provider, subject))
        return self.users[user_id] if user_id else None

    async def add(self, user: User) -> None:
        self.users[user.id] = user

    async def add_identity(self, user_id: UUID, provider: AuthProvider, subject: str) -> None:
        self.identities[(provider, subject)] = user_id

    async def save(self, user: User) -> None:
        self.users[user.id] = user

    async def set_role(self, user_id: UUID, role: UserRole) -> None:
        self.users[user_id].role = role

    async def get_credentials(self, email: str) -> None:
        return None

    async def set_password_hash(self, user_id: UUID, password_hash: str | None) -> None: ...

    async def record_failed_login(
        self, user_id: UUID, failed_count: int, locked_until: datetime | None
    ) -> None: ...

    async def reset_failed_logins(self, user_id: UUID) -> None: ...

    async def search(self, filters: UserFilters, page: PageParams) -> Page[User]:
        items = list(self.users.values())
        return Page(items=items, total=len(items), limit=page.limit, offset=page.offset)


class FakeRefreshTokens:
    def __init__(self) -> None:
        self.tokens: dict[str, RefreshToken] = {}

    async def add(self, token: RefreshToken) -> None:
        self.tokens[token.token_hash] = token

    async def get_by_hash_for_update(self, token_hash: str) -> RefreshToken | None:
        return self.tokens.get(token_hash)

    async def save(self, token: RefreshToken) -> None:
        self.tokens[token.token_hash] = token

    async def revoke_all_for_user(self, user_id: UUID, now: datetime) -> None:
        for token in self.tokens.values():
            if token.user_id == user_id and not token.is_revoked:
                token.revoke(now)


class FakeInvitations:
    def __init__(self) -> None:
        self.claimed: list[tuple[UUID, str]] = []

    async def claim(self, user_id: UUID, email: str, now: datetime) -> None:
        self.claimed.append((user_id, email))


class FakeUnitOfWork:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class Env:
    def __init__(self) -> None:
        self.users = FakeUsers()
        self.refresh_tokens = FakeRefreshTokens()
        self.unit_of_work = FakeUnitOfWork()
        self.invitations = FakeInvitations()
        self.tokens = JwtTokenService(SECRET, timedelta(minutes=5), timedelta(days=1))

    def sign_in(self, identity: ExternalIdentity) -> SignInWithProvider:
        return SignInWithProvider(
            {identity.provider: FakeVerifier(identity)},
            self.users,
            self.refresh_tokens,
            self.tokens,
            self.unit_of_work,
            self.invitations,  # type: ignore[arg-type]
        )

    def refresh(self) -> RefreshSession:
        return RefreshSession(self.users, self.refresh_tokens, self.tokens, self.unit_of_work)

    def sign_out(self) -> SignOut:
        return SignOut(self.refresh_tokens, self.tokens, self.unit_of_work)


def google_identity(**overrides: object) -> ExternalIdentity:
    values: dict[str, object] = {
        "provider": AuthProvider.GOOGLE,
        "subject": "google-1",
        "email": "Traveler@Example.com",
        "email_verified": True,
        "first_name": "Aigul",
        "last_name": "Asanova",
    }
    return ExternalIdentity(**(values | overrides))  # type: ignore[arg-type]


def apple_identity(**overrides: object) -> ExternalIdentity:
    values: dict[str, object] = {
        "provider": AuthProvider.APPLE,
        "subject": "apple-1",
        "email": "traveler@example.com",
        "email_verified": True,
    }
    return ExternalIdentity(**(values | overrides))  # type: ignore[arg-type]


@pytest.fixture
def env() -> Env:
    return Env()


async def test_first_sign_in_creates_user(env: Env) -> None:
    await env.sign_in(google_identity()).execute(AuthProvider.GOOGLE, "token")

    user = next(iter(env.users.users.values()))
    assert user.email == "traveler@example.com"
    assert user.first_name == "Aigul"
    assert env.unit_of_work.commits == 1


async def test_repeat_sign_in_reuses_user(env: Env) -> None:
    await env.sign_in(google_identity()).execute(AuthProvider.GOOGLE, "token")
    await env.sign_in(google_identity()).execute(AuthProvider.GOOGLE, "token")

    assert len(env.users.users) == 1


async def test_second_provider_with_same_verified_email_links_account(env: Env) -> None:
    await env.sign_in(google_identity()).execute(AuthProvider.GOOGLE, "token")
    await env.sign_in(apple_identity()).execute(AuthProvider.APPLE, "token")

    assert len(env.users.users) == 1
    assert len(env.users.identities) == 2


async def test_apple_names_come_from_client(env: Env) -> None:
    await env.sign_in(apple_identity()).execute(AuthProvider.APPLE, "token", "Aigul", "Asanova")

    user = next(iter(env.users.users.values()))
    assert (user.first_name, user.last_name) == ("Aigul", "Asanova")


@pytest.mark.parametrize(
    "overrides", [{"email_verified": False}, {"email": None}], ids=["unverified", "missing"]
)
async def test_new_identity_requires_verified_email(env: Env, overrides: dict[str, object]) -> None:
    with pytest.raises(AuthenticationError):
        await env.sign_in(google_identity(**overrides)).execute(AuthProvider.GOOGLE, "token")

    assert env.users.users == {}


async def test_unverified_email_cannot_take_over_existing_account(env: Env) -> None:
    await env.sign_in(google_identity()).execute(AuthProvider.GOOGLE, "token")

    with pytest.raises(AuthenticationError):
        await env.sign_in(apple_identity(email_verified=False)).execute(AuthProvider.APPLE, "token")

    assert len(env.users.identities) == 1


async def test_disabled_user_cannot_sign_in(env: Env) -> None:
    await env.sign_in(google_identity()).execute(AuthProvider.GOOGLE, "token")
    next(iter(env.users.users.values())).is_active = False

    with pytest.raises(AuthenticationError):
        await env.sign_in(google_identity()).execute(AuthProvider.GOOGLE, "token")


async def test_refresh_rotates_token(env: Env) -> None:
    pair = await env.sign_in(google_identity()).execute(AuthProvider.GOOGLE, "token")

    new_pair = await env.refresh().execute(pair.refresh_token)

    assert new_pair.refresh_token != pair.refresh_token
    assert env.refresh_tokens.tokens[env.tokens.hash_refresh_token(pair.refresh_token)].is_revoked


async def test_reusing_refresh_token_revokes_all_sessions(env: Env) -> None:
    pair = await env.sign_in(google_identity()).execute(AuthProvider.GOOGLE, "token")
    new_pair = await env.refresh().execute(pair.refresh_token)

    with pytest.raises(AuthenticationError):
        await env.refresh().execute(pair.refresh_token)
    with pytest.raises(AuthenticationError):
        await env.refresh().execute(new_pair.refresh_token)


async def test_expired_refresh_token_is_rejected(env: Env) -> None:
    pair = await env.sign_in(google_identity()).execute(AuthProvider.GOOGLE, "token")
    stored = env.refresh_tokens.tokens[env.tokens.hash_refresh_token(pair.refresh_token)]
    stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    with pytest.raises(AuthenticationError):
        await env.refresh().execute(pair.refresh_token)


async def test_unknown_refresh_token_is_rejected(env: Env) -> None:
    with pytest.raises(AuthenticationError):
        await env.refresh().execute("unknown")


async def test_sign_out_revokes_token_and_ignores_unknown(env: Env) -> None:
    pair = await env.sign_in(google_identity()).execute(AuthProvider.GOOGLE, "token")

    await env.sign_out().execute(pair.refresh_token)
    await env.sign_out().execute("unknown")

    assert env.refresh_tokens.tokens[env.tokens.hash_refresh_token(pair.refresh_token)].is_revoked


async def test_sign_in_claims_company_invitations(env: Env) -> None:
    await env.sign_in(google_identity()).execute(AuthProvider.GOOGLE, "token")

    user = next(iter(env.users.users.values()))
    assert env.invitations.claimed == [(user.id, "traveler@example.com")]
