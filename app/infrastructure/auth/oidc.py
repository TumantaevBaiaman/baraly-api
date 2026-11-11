import asyncio
import logging
import time
from collections.abc import Sequence

import httpx
import jwt
from jwt import PyJWK, PyJWKSet

from app.application.exceptions import AuthenticationError, ExternalServiceError
from app.application.ports.auth import IdentityVerifier
from app.config import Settings
from app.domain.auth import AuthProvider, ExternalIdentity

logger = logging.getLogger(__name__)

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
APPLE_ISSUERS = ("https://appleid.apple.com",)

_JWKS_TTL_SECONDS = 3600
_JWKS_MIN_REFRESH_SECONDS = 60
_JWKS_TIMEOUT_SECONDS = 5


class JwksClient:
    """Кэширует публичные ключи провайдера и обновляет их при смене ключа."""

    def __init__(self, http_client: httpx.AsyncClient, url: str) -> None:
        self._http_client = http_client
        self._url = url
        self._keys: dict[str, PyJWK] = {}
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def get_key(self, key_id: str) -> PyJWK | None:
        if self._should_refresh(key_id):
            async with self._lock:
                if self._should_refresh(key_id):
                    await self._refresh()
        return self._keys.get(key_id)

    def _should_refresh(self, key_id: str) -> bool:
        if not self._keys:
            return True
        age = time.monotonic() - self._fetched_at
        if age >= _JWKS_TTL_SECONDS:
            return True
        # Ограничение не даёт поддельными kid заставить нас постоянно ходить к провайдеру.
        return key_id not in self._keys and age >= _JWKS_MIN_REFRESH_SECONDS

    async def _refresh(self) -> None:
        self._fetched_at = time.monotonic()
        try:
            response = await self._http_client.get(self._url, timeout=_JWKS_TIMEOUT_SECONDS)
            response.raise_for_status()
            key_set = PyJWKSet.from_dict(response.json())
        except (httpx.HTTPError, ValueError, jwt.PyJWTError) as exc:
            logger.warning("Failed to fetch JWKS from %s", self._url, exc_info=True)
            if not self._keys:
                raise ExternalServiceError("Identity provider is unavailable") from exc
            return
        self._keys = {key.key_id: key for key in key_set.keys if key.key_id}


class OidcIdentityVerifier:
    def __init__(
        self,
        provider: AuthProvider,
        jwks: JwksClient,
        issuers: Sequence[str],
        audiences: Sequence[str],
    ) -> None:
        self._provider = provider
        self._jwks = jwks
        self._issuers = list(issuers)
        self._audiences = list(audiences)

    async def verify(self, id_token: str) -> ExternalIdentity:
        if not self._audiences:
            logger.error("Client IDs for %s are not configured", self._provider.value)
            raise AuthenticationError("Sign-in with this provider is not available")

        try:
            key_id = jwt.get_unverified_header(id_token).get("kid")
            key = await self._jwks.get_key(key_id) if key_id else None
            if key is None:
                raise AuthenticationError("Invalid identity token")
            claims = jwt.decode(
                id_token,
                key.key,
                algorithms=["RS256"],
                audience=self._audiences,
                issuer=self._issuers,
                options={"require": ["sub", "iss", "aud", "exp", "iat"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError("Invalid identity token") from exc

        return ExternalIdentity(
            provider=self._provider,
            subject=claims["sub"],
            email=claims.get("email"),
            # Apple передаёт это поле строкой "true", Google булевым значением.
            email_verified=claims.get("email_verified") in (True, "true"),
            first_name=claims.get("given_name"),
            last_name=claims.get("family_name"),
            avatar_url=claims.get("picture"),
        )


def build_identity_verifiers(
    settings: Settings, http_client: httpx.AsyncClient
) -> dict[AuthProvider, IdentityVerifier]:
    return {
        AuthProvider.GOOGLE: OidcIdentityVerifier(
            AuthProvider.GOOGLE,
            JwksClient(http_client, GOOGLE_JWKS_URL),
            GOOGLE_ISSUERS,
            settings.google_client_ids,
        ),
        AuthProvider.APPLE: OidcIdentityVerifier(
            AuthProvider.APPLE,
            JwksClient(http_client, APPLE_JWKS_URL),
            APPLE_ISSUERS,
            settings.apple_client_ids,
        ),
    }
