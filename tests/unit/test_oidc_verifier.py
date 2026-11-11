import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from jwt.algorithms import RSAAlgorithm

from app.application.exceptions import AuthenticationError, ExternalServiceError
from app.domain.auth import AuthProvider
from app.infrastructure.auth.oidc import GOOGLE_ISSUERS, JwksClient, OidcIdentityVerifier

CLIENT_ID = "web-client-id"
KEY_ID = "key-1"
JWKS_URL = "https://provider.test/keys"


@pytest.fixture(scope="module")
def private_key() -> RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
async def http_client(private_key: RSAPrivateKey) -> AsyncIterator[httpx.AsyncClient]:
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk |= {"kid": KEY_ID, "use": "sig", "alg": "RS256"}
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"keys": [jwk]}))
    async with httpx.AsyncClient(transport=transport) as client:
        yield client


def make_verifier(
    client: httpx.AsyncClient, audiences: tuple[str, ...] = (CLIENT_ID,)
) -> OidcIdentityVerifier:
    return OidcIdentityVerifier(
        AuthProvider.GOOGLE, JwksClient(client, JWKS_URL), GOOGLE_ISSUERS, audiences
    )


def make_token(key: Any, *, kid: str | None = KEY_ID, **overrides: Any) -> str:
    now = datetime.now(UTC)
    claims = {
        "iss": GOOGLE_ISSUERS[0],
        "aud": CLIENT_ID,
        "sub": "subject-1",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "email": "traveler@example.com",
        "email_verified": True,
        "given_name": "Aigul",
        "family_name": "Asanova",
        "picture": "https://example.com/a.png",
    } | overrides
    headers = {"kid": kid} if kid else {}
    return jwt.encode(claims, key, algorithm="RS256", headers=headers)


async def test_valid_token_returns_identity(
    http_client: httpx.AsyncClient, private_key: RSAPrivateKey
) -> None:
    identity = await make_verifier(http_client).verify(make_token(private_key))

    assert identity.provider is AuthProvider.GOOGLE
    assert identity.subject == "subject-1"
    assert identity.email_verified is True
    assert identity.first_name == "Aigul"


async def test_apple_string_email_verified_is_accepted(
    http_client: httpx.AsyncClient, private_key: RSAPrivateKey
) -> None:
    token = make_token(private_key, email_verified="true")

    assert (await make_verifier(http_client).verify(token)).email_verified is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "another-app"},
        {"iss": "https://evil.test"},
        {"exp": datetime(2020, 1, 1, tzinfo=UTC)},
    ],
    ids=["wrong-audience", "wrong-issuer", "expired"],
)
async def test_rejects_invalid_claims(
    http_client: httpx.AsyncClient, private_key: RSAPrivateKey, overrides: dict[str, Any]
) -> None:
    with pytest.raises(AuthenticationError):
        await make_verifier(http_client).verify(make_token(private_key, **overrides))


async def test_rejects_token_signed_by_another_key(http_client: httpx.AsyncClient) -> None:
    attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    with pytest.raises(AuthenticationError):
        await make_verifier(http_client).verify(make_token(attacker_key))


async def test_rejects_unknown_key_id(
    http_client: httpx.AsyncClient, private_key: RSAPrivateKey
) -> None:
    with pytest.raises(AuthenticationError):
        await make_verifier(http_client).verify(make_token(private_key, kid="unknown"))


async def test_rejects_unsigned_token(http_client: httpx.AsyncClient) -> None:
    token = jwt.encode(
        {"iss": GOOGLE_ISSUERS[0], "aud": CLIENT_ID, "sub": "subject-1"},
        key="",
        algorithm="none",
        headers={"kid": KEY_ID},
    )

    with pytest.raises(AuthenticationError):
        await make_verifier(http_client).verify(token)


async def test_rejects_garbage(http_client: httpx.AsyncClient) -> None:
    with pytest.raises(AuthenticationError):
        await make_verifier(http_client).verify("not-a-jwt")


async def test_rejects_all_when_client_ids_not_configured(
    http_client: httpx.AsyncClient, private_key: RSAPrivateKey
) -> None:
    with pytest.raises(AuthenticationError):
        await make_verifier(http_client, audiences=()).verify(make_token(private_key))


async def test_provider_outage_is_reported_as_unavailable(private_key: RSAPrivateKey) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ExternalServiceError):
            await make_verifier(client).verify(make_token(private_key))
