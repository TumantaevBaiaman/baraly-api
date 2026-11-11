"""Полный сценарий через HTTP и настоящий PostgreSQL. Изменения откатываются после теста."""

from uuid import uuid4

import pytest
from fastapi import status
from httpx import AsyncClient

from tests.integration.conftest import bearer, sign_in


async def test_profile_flow(db_client: AsyncClient) -> None:
    tokens = await sign_in(
        db_client, "apple", id_token=uuid4().hex, first_name="Aigul", last_name="Asanova"
    )

    me = await db_client.get("/api/v1/users/me", headers=bearer(tokens))
    assert me.status_code == status.HTTP_200_OK
    assert me.json()["first_name"] == "Aigul"
    assert me.json()["phone"] is None

    updated = await db_client.patch(
        "/api/v1/users/me", headers=bearer(tokens), json={"phone": "+996 502800202"}
    )
    assert updated.status_code == status.HTTP_200_OK
    assert updated.json()["phone"] == "+996502800202"
    assert updated.json()["first_name"] == "Aigul"

    me = await db_client.get("/api/v1/users/me", headers=bearer(tokens))
    assert me.json()["phone"] == "+996502800202"


async def test_invalid_phone_and_email_change_are_rejected(db_client: AsyncClient) -> None:
    tokens = await sign_in(db_client, "google", id_token=uuid4().hex)

    invalid_phone = await db_client.patch(
        "/api/v1/users/me", headers=bearer(tokens), json={"phone": "12345"}
    )
    change_email = await db_client.patch(
        "/api/v1/users/me", headers=bearer(tokens), json={"email": "new@example.com"}
    )

    assert invalid_phone.status_code == status.HTTP_400_BAD_REQUEST
    assert change_email.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_both_providers_share_account_by_verified_email(db_client: AsyncClient) -> None:
    google = await sign_in(db_client, "google", id_token=uuid4().hex)
    apple = await sign_in(db_client, "apple", id_token=uuid4().hex)

    google_me = await db_client.get("/api/v1/users/me", headers=bearer(google))
    apple_me = await db_client.get("/api/v1/users/me", headers=bearer(apple))

    assert google_me.json()["id"] == apple_me.json()["id"]


async def test_refresh_reuse_and_logout(db_client: AsyncClient) -> None:
    subject = uuid4().hex
    first = await sign_in(db_client, "google", id_token=subject)

    rotated = await db_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert rotated.status_code == status.HTTP_200_OK
    second = rotated.json()

    reused = await db_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert reused.status_code == status.HTTP_401_UNAUTHORIZED

    after_reuse = await db_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]}
    )
    assert after_reuse.status_code == status.HTTP_401_UNAUTHORIZED

    third = await sign_in(db_client, "google", id_token=subject)
    logout = await db_client.post(
        "/api/v1/auth/logout", json={"refresh_token": third["refresh_token"]}
    )
    assert logout.status_code == status.HTTP_204_NO_CONTENT
    after_logout = await db_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": third["refresh_token"]}
    )
    assert after_logout.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.parametrize("token", [None, "garbage"], ids=["missing", "invalid"])
async def test_me_requires_valid_access_token(db_client: AsyncClient, token: str | None) -> None:
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    response = await db_client.get("/api/v1/users/me", headers=headers)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
