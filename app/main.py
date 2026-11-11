import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings
from app.infrastructure.auth.oidc import build_identity_verifiers
from app.infrastructure.auth.passwords import Argon2PasswordHasher
from app.infrastructure.auth.tokens import JwtTokenService
from app.infrastructure.db.session import create_engine, create_session_factory
from app.infrastructure.payments.fake import FakePaymentGateway
from app.infrastructure.storage.s3 import S3ImageStorage, create_minio_client
from app.presentation.api.error_handlers import register_error_handlers
from app.presentation.api.health import router as health_router
from app.presentation.api.v1.bookings import fake_payments_router
from app.presentation.api.v1.router import router as api_v1_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await app.state.http_client.aclose()
    await app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    app = FastAPI(
        title=settings.app_name,
        lifespan=lifespan,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )

    # Движок подключается к БД лениво, при первом запросе.
    app.state.engine = create_engine(settings)
    app.state.session_factory = create_session_factory(app.state.engine)
    app.state.http_client = httpx.AsyncClient()
    app.state.identity_verifiers = build_identity_verifiers(settings, app.state.http_client)
    app.state.image_storage = S3ImageStorage(
        create_minio_client(settings), settings.s3_bucket, settings.s3_public_url
    )
    app.state.max_image_bytes = settings.max_image_size_mb * 1024 * 1024
    app.state.booking_hold = timedelta(minutes=settings.booking_hold_minutes)
    app.state.payment_gateway = FakePaymentGateway(settings.public_api_url)
    app.state.password_hasher = Argon2PasswordHasher()
    # Хеш-заглушка для выравнивания времени ответа при неизвестном email.
    app.state.dummy_password_hash = app.state.password_hasher.hash("dummy-password-1")
    app.state.token_service = JwtTokenService(
        secret=settings.jwt_secret.get_secret_value(),
        access_ttl=timedelta(minutes=settings.access_token_ttl_minutes),
        refresh_ttl=timedelta(days=settings.refresh_token_ttl_days),
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(api_v1_router)
    if settings.payment_provider == "fake":
        app.include_router(fake_payments_router, prefix="/api/v1")
    return app
