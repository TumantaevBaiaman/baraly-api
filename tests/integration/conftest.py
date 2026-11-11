"""Полный сценарий через HTTP и настоящий PostgreSQL. Изменения откатываются после теста."""

from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import uuid4

import pytest
from fastapi import FastAPI, status
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.domain.auth import AuthProvider, ExternalIdentity
from app.domain.user import UserRole
from app.infrastructure.db.models import UserModel
from app.infrastructure.db.session import build_database_url
from app.main import create_app
from app.presentation.api.dependencies import get_identity_verifiers


class TokenAsIdentityVerifier:
    """Вместо проверки подписи берёт из id_token идентификатор и email: "subject|email"."""

    def __init__(self, provider: AuthProvider, default_email: str) -> None:
        self._provider = provider
        self._default_email = default_email

    async def verify(self, id_token: str) -> ExternalIdentity:
        subject, _, email = id_token.partition("|")
        return ExternalIdentity(
            provider=self._provider,
            subject=subject,
            email=email or self._default_email,
            email_verified=True,
        )


@pytest.fixture
async def db_connection() -> AsyncIterator[AsyncConnection]:
    try:
        database_url = build_database_url(Settings())
    except ValidationError:
        pytest.skip("Нет настроек БД в .env")

    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        connection = await engine.connect()
    except (OSError, SQLAlchemyError):
        await engine.dispose()
        pytest.skip("PostgreSQL недоступен")

    transaction = await connection.begin()
    yield connection
    await transaction.rollback()
    await connection.close()
    await engine.dispose()


@pytest.fixture
async def db_app(settings: Settings, db_connection: AsyncConnection) -> AsyncIterator[FastAPI]:
    app = create_app(settings)
    await app.state.engine.dispose()
    app.state.session_factory = async_sessionmaker(
        bind=db_connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    email = f"{uuid4().hex}@example.com"
    app.dependency_overrides[get_identity_verifiers] = lambda: {
        provider: TokenAsIdentityVerifier(provider, email) for provider in AuthProvider
    }
    yield app


@pytest.fixture
async def db_client(db_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=db_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def sign_in(client: AsyncClient, provider: str, **body: str) -> dict[str, str]:
    response = await client.post(f"/api/v1/auth/{provider}", json=body)
    assert response.status_code == status.HTTP_200_OK, response.text
    tokens: dict[str, str] = response.json()
    return tokens


SignInUser = Callable[[], Awaitable[dict[str, str]]]


@pytest.fixture
def sign_in_user(db_client: AsyncClient) -> SignInUser:
    async def _sign_in() -> dict[str, str]:
        email = f"{uuid4().hex}@example.com"
        return await sign_in(db_client, "google", id_token=f"{uuid4().hex}|{email}")

    return _sign_in


@pytest.fixture
async def admin_headers(
    db_client: AsyncClient, db_connection: AsyncConnection, sign_in_user: SignInUser
) -> dict[str, str]:
    headers = bearer(await sign_in_user())
    me = await db_client.get("/api/v1/users/me", headers=headers)
    await db_connection.execute(
        update(UserModel).where(UserModel.id == me.json()["id"]).values(role=UserRole.ADMIN)
    )
    return headers
