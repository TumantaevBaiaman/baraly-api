"""Вход админа и модератора по почте и паролю."""

from uuid import uuid4

import pytest
from fastapi import status
from httpx import AsyncClient

from tests.integration.conftest import SignInUser, bearer
from tests.integration.test_tours_flow import API

PASSWORD = "strong-pass-1"


async def create_staff(
    client: AsyncClient, admin: dict[str, str], role: str = "moderator", **extra: str
) -> str:
    email = f"{uuid4().hex}@example.com"
    response = await client.post(
        f"{API}/admin/staff",
        json={"email": email, "role": role, "password": PASSWORD} | extra,
        headers=admin,
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return email


async def login(client: AsyncClient, email: str, password: str = PASSWORD) -> int:
    response = await client.post(f"{API}/auth/login", json={"email": email, "password": password})
    return response.status_code


async def test_staff_sign_in_with_password(
    db_client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    email = await create_staff(db_client, admin_headers, role="admin", first_name="Islam")

    assert await login(db_client, email.upper()) == status.HTTP_200_OK
    assert await login(db_client, email, "wrong-pass-1") == status.HTTP_401_UNAUTHORIZED
    assert await login(db_client, f"{uuid4().hex}@example.com") == status.HTTP_401_UNAUTHORIZED


async def test_user_without_password_cannot_use_password_login(
    db_client: AsyncClient, admin_headers: dict[str, str], sign_in_user: SignInUser
) -> None:
    user_headers = bearer(await sign_in_user())
    user = (await db_client.get(f"{API}/users/me", headers=user_headers)).json()
    await create_staff(db_client, admin_headers)

    assert await login(db_client, user["email"]) == status.HTTP_401_UNAUTHORIZED


async def test_five_wrong_passwords_lock_the_account(
    db_client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    email = await create_staff(db_client, admin_headers)

    for _ in range(5):
        assert await login(db_client, email, "wrong-pass-1") == status.HTTP_401_UNAUTHORIZED
    response = await db_client.post(
        f"{API}/auth/login", json={"email": email, "password": PASSWORD}
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert "try again later" in response.json()["detail"]


async def test_admin_resets_password_and_unlocks(
    db_client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    email = await create_staff(db_client, admin_headers)
    for _ in range(5):
        await login(db_client, email, "wrong-pass-1")
    users = await db_client.get(f"{API}/admin/users", params={"q": email}, headers=admin_headers)
    user_id = users.json()["items"][0]["id"]

    reset = await db_client.put(
        f"{API}/admin/users/{user_id}/password",
        json={"password": "new-strong-pass-2"},
        headers=admin_headers,
    )

    assert reset.status_code == status.HTTP_204_NO_CONTENT
    assert await login(db_client, email, "new-strong-pass-2") == status.HTTP_200_OK


async def test_staff_changes_own_password_and_old_sessions_end(
    db_client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    email = await create_staff(db_client, admin_headers)
    tokens = (
        await db_client.post(f"{API}/auth/login", json={"email": email, "password": PASSWORD})
    ).json()

    wrong = await db_client.post(
        f"{API}/users/me/password",
        json={"current_password": "nope-nope-1", "new_password": "another-pass-3"},
        headers=bearer(tokens),
    )
    changed = await db_client.post(
        f"{API}/users/me/password",
        json={"current_password": PASSWORD, "new_password": "another-pass-3"},
        headers=bearer(tokens),
    )

    assert wrong.status_code == status.HTTP_401_UNAUTHORIZED
    assert changed.status_code == status.HTTP_204_NO_CONTENT
    refresh = await db_client.post(
        f"{API}/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refresh.status_code == status.HTTP_401_UNAUTHORIZED
    assert await login(db_client, email, "another-pass-3") == status.HTTP_200_OK


async def test_demoted_staff_loses_password_login(
    db_client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    email = await create_staff(db_client, admin_headers)
    users = await db_client.get(f"{API}/admin/users", params={"q": email}, headers=admin_headers)
    user_id = users.json()["items"][0]["id"]

    await db_client.patch(
        f"{API}/admin/users/{user_id}/role", json={"role": "user"}, headers=admin_headers
    )

    assert await login(db_client, email) == status.HTTP_401_UNAUTHORIZED


@pytest.mark.parametrize("password", ["short1", "onlyletterslong", "1234567890123"])
async def test_weak_passwords_are_rejected(
    db_client: AsyncClient, admin_headers: dict[str, str], password: str
) -> None:
    response = await db_client.post(
        f"{API}/admin/staff",
        json={"email": f"{uuid4().hex}@example.com", "role": "moderator", "password": password},
        headers=admin_headers,
    )

    assert response.status_code in {
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
    }


async def test_only_admin_creates_staff(db_client: AsyncClient, sign_in_user: SignInUser) -> None:
    headers = bearer(await sign_in_user())

    response = await db_client.post(
        f"{API}/admin/staff",
        json={"email": "x@example.com", "role": "admin", "password": PASSWORD},
        headers=headers,
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN


async def test_company_members_sign_in_with_password(
    db_client: AsyncClient, admin_headers: dict[str, str], sign_in_user: SignInUser
) -> None:
    owner_email = f"{uuid4().hex}@example.com"
    company = await db_client.post(
        f"{API}/admin/tour-operators",
        json={"name": "Password Co", "owner_email": owner_email, "owner_password": PASSWORD},
        headers=admin_headers,
    )
    assert company.status_code == status.HTTP_201_CREATED, company.text
    company_id = company.json()["id"]

    owner_tokens = await db_client.post(
        f"{API}/auth/login", json={"email": owner_email, "password": PASSWORD}
    )
    assert owner_tokens.status_code == status.HTTP_200_OK
    owner = bearer(owner_tokens.json())
    mine = await db_client.get(f"{API}/users/me/companies", headers=owner)
    assert mine.json()[0]["role"] == "owner"

    manager_email = f"{uuid4().hex}@example.com"
    created = await db_client.post(
        f"{API}/company/{company_id}/members",
        json={"email": manager_email, "role": "manager", "password": PASSWORD, "first_name": "Bek"},
        headers=owner,
    )
    assert created.json()["status"] == "created"
    assert await login(db_client, manager_email) == status.HTTP_200_OK

    # На существующий чужой аккаунт владелец пароль поставить не может.
    traveler = bearer(await sign_in_user())
    traveler_email = (await db_client.get(f"{API}/users/me", headers=traveler)).json()["email"]
    takeover = await db_client.post(
        f"{API}/company/{company_id}/members",
        json={"email": traveler_email, "role": "manager", "password": "evil-pass-123"},
        headers=owner,
    )
    assert takeover.status_code == status.HTTP_409_CONFLICT
    assert await login(db_client, traveler_email, "evil-pass-123") == status.HTTP_401_UNAUTHORIZED
    added = await db_client.post(
        f"{API}/company/{company_id}/members",
        json={"email": traveler_email, "role": "manager"},
        headers=owner,
    )
    assert added.json()["status"] == "member"


async def test_admin_resets_company_member_password(
    db_client: AsyncClient, admin_headers: dict[str, str], sign_in_user: SignInUser
) -> None:
    owner_email = f"{uuid4().hex}@example.com"
    await db_client.post(
        f"{API}/admin/tour-operators",
        json={"name": "Reset Co", "owner_email": owner_email, "owner_password": PASSWORD},
        headers=admin_headers,
    )
    users = await db_client.get(
        f"{API}/admin/users", params={"q": owner_email}, headers=admin_headers
    )
    owner_id = users.json()["items"][0]["id"]

    reset = await db_client.put(
        f"{API}/admin/users/{owner_id}/password",
        json={"password": "fresh-pass-77"},
        headers=admin_headers,
    )
    assert reset.status_code == status.HTTP_204_NO_CONTENT
    assert await login(db_client, owner_email, "fresh-pass-77") == status.HTTP_200_OK

    traveler = bearer(await sign_in_user())
    traveler_id = (await db_client.get(f"{API}/users/me", headers=traveler)).json()["id"]
    refused = await db_client.put(
        f"{API}/admin/users/{traveler_id}/password",
        json={"password": "fresh-pass-77"},
        headers=admin_headers,
    )
    assert refused.status_code == status.HTTP_400_BAD_REQUEST
