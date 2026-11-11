"""Роли платформы, проверка компаний, модерация туров и прошедшие туры."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi import status
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncConnection

from app.infrastructure.db.models import TourDepartureModel
from tests.integration.conftest import SignInUser, bearer, sign_in
from tests.integration.test_tours_flow import API, Catalog, departure, tour_body


@pytest.fixture
def catalog(db_client: AsyncClient, admin_headers: dict[str, str]) -> Catalog:
    return Catalog(db_client, admin_headers)


def new_email() -> str:
    return f"{uuid4().hex}@example.com"


async def login(client: AsyncClient, email: str) -> dict[str, str]:
    return bearer(await sign_in(client, "google", id_token=f"{uuid4().hex}|{email}"))


@pytest.fixture
async def moderator(db_client: AsyncClient, catalog: Catalog) -> dict[str, str]:
    """Модератор создан админом и входит в админку по паролю."""
    email = new_email()
    created = await db_client.post(
        f"{API}/admin/staff",
        json={"email": email, "role": "moderator", "password": "moderator-pass-1"},
        headers=catalog.headers,
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    tokens = await db_client.post(
        f"{API}/auth/login", json={"email": email, "password": "moderator-pass-1"}
    )
    assert tokens.status_code == status.HTTP_200_OK, tokens.text
    headers = bearer(tokens.json())
    me = (await db_client.get(f"{API}/users/me", headers=headers)).json()
    assert me["role"] == "moderator"
    return headers


class UnverifiedCompany:
    def __init__(self, client: AsyncClient, operator_id: str, owner: dict[str, str]) -> None:
        self.client = client
        self.id = operator_id
        self.owner = owner

    async def create_tour(self, **overrides: Any) -> dict[str, Any]:
        body = tour_body(self.id, **overrides)
        body.pop("operator_id")
        response = await self.client.post(
            f"{API}/company/{self.id}/tours", json=body, headers=self.owner
        )
        assert response.status_code == status.HTTP_201_CREATED, response.text
        result: dict[str, Any] = response.json()
        return result


@pytest.fixture
async def company(db_client: AsyncClient, catalog: Catalog) -> UnverifiedCompany:
    email = new_email()
    created = await catalog.post("/tour-operators", {"name": "New operator", "owner_email": email})
    return UnverifiedCompany(db_client, created["id"], await login(db_client, email))


async def public_status(client: AsyncClient, tour_id: str) -> int:
    return (await client.get(f"{API}/tours/{tour_id}")).status_code


async def test_unverified_company_tours_wait_for_moderation(
    db_client: AsyncClient, company: UnverifiedCompany, moderator: dict[str, str]
) -> None:
    tour = await company.create_tour()
    assert tour["moderation_status"] == "pending"
    assert await public_status(db_client, tour["id"]) == status.HTTP_404_NOT_FOUND

    queue = await db_client.get(
        f"{API}/admin/tours",
        params={"moderation_status": "pending", "operator_id": company.id},
        headers=moderator,
    )
    assert [item["id"] for item in queue.json()["items"]] == [tour["id"]]

    approved = await db_client.patch(
        f"{API}/admin/tours/{tour['id']}/approval",
        json={"status": "approved"},
        headers=moderator,
    )
    assert approved.json()["moderation_status"] == "approved"
    assert approved.json()["moderated_at"] is not None
    assert await public_status(db_client, tour["id"]) == status.HTTP_200_OK


async def test_rejection_needs_reason_and_edit_returns_to_queue(
    db_client: AsyncClient, company: UnverifiedCompany, moderator: dict[str, str]
) -> None:
    tour = await company.create_tour()
    url = f"{API}/admin/tours/{tour['id']}/approval"

    no_reason = await db_client.patch(url, json={"status": "rejected"}, headers=moderator)
    assert no_reason.status_code == status.HTTP_400_BAD_REQUEST

    rejected = await db_client.patch(
        url, json={"status": "rejected", "comment": "Нет фото программы"}, headers=moderator
    )
    assert rejected.json()["moderation_status"] == "rejected"

    seen_by_company = await db_client.get(
        f"{API}/company/{company.id}/tours/{tour['id']}", headers=company.owner
    )
    assert seen_by_company.json()["moderation_comment"] == "Нет фото программы"

    body = tour_body(company.id, title="Fixed")
    body.pop("operator_id")
    edited = await db_client.put(
        f"{API}/company/{company.id}/tours/{tour['id']}", json=body, headers=company.owner
    )
    assert edited.json()["moderation_status"] == "pending"
    assert edited.json()["moderation_comment"] is None


async def test_verified_company_publishes_immediately(
    db_client: AsyncClient, company: UnverifiedCompany, catalog: Catalog
) -> None:
    verified = await db_client.patch(
        f"{API}/admin/tour-operators/{company.id}/verification",
        json={"is_verified": True, "note": "Лицензия туроператора проверена"},
        headers=catalog.headers,
    )
    assert verified.json()["is_verified"] is True
    assert verified.json()["verification_note"] == "Лицензия туроператора проверена"
    assert verified.json()["verified_at"] is not None

    tour = await company.create_tour()
    assert tour["moderation_status"] == "approved"
    assert await public_status(db_client, tour["id"]) == status.HTTP_200_OK

    public_company = (await db_client.get(f"{API}/tour-operators/{company.id}")).json()
    assert public_company["is_verified"] is True

    filtered = await db_client.get(
        f"{API}/admin/tour-operators",
        params={"is_verified": False, "q": "New operator", "limit": 100},
        headers=catalog.headers,
    )
    assert company.id not in {item["id"] for item in filtered.json()["items"]}

    revoked = await db_client.patch(
        f"{API}/admin/tour-operators/{company.id}/verification",
        json={"is_verified": False, "note": "ignored"},
        headers=catalog.headers,
    )
    assert revoked.json()["verification_note"] is None
    next_tour = await company.create_tour()
    assert next_tour["moderation_status"] == "pending"


async def test_moderator_permissions(
    db_client: AsyncClient,
    moderator: dict[str, str],
    company: UnverifiedCompany,
    sign_in_user: SignInUser,
) -> None:
    tour = await company.create_tour()

    hidden = await db_client.patch(
        f"{API}/admin/tours/{tour['id']}/moderation",
        json={"is_blocked": True, "reason": "Жалобы"},
        headers=moderator,
    )
    assert hidden.json()["is_blocked"] is True

    for method, path, body in (
        ("GET", "/admin/tour-operators", None),
        ("GET", "/admin/users", None),
        ("GET", "/admin/reviews", None),
        ("DELETE", f"/admin/tours/{tour['id']}", None),
        ("POST", "/admin/tags", {"slug": "x", "name": "X"}),
    ):
        response = await db_client.request(method, f"{API}{path}", json=body, headers=moderator)
        assert response.status_code == status.HTTP_403_FORBIDDEN, path

    company_area = await db_client.get(f"{API}/company/{company.id}/tours", headers=moderator)
    assert company_area.status_code == status.HTTP_403_FORBIDDEN

    regular = bearer(await sign_in_user())
    queue = await db_client.get(f"{API}/admin/tours", headers=regular)
    assert queue.status_code == status.HTTP_403_FORBIDDEN


async def test_admin_manages_roles(
    db_client: AsyncClient, catalog: Catalog, sign_in_user: SignInUser
) -> None:
    user_headers = bearer(await sign_in_user())
    user = (await db_client.get(f"{API}/users/me", headers=user_headers)).json()

    promoted = await db_client.patch(
        f"{API}/admin/users/{user['id']}/role", json={"role": "moderator"}, headers=catalog.headers
    )
    assert promoted.json()["role"] == "moderator"
    listed = await db_client.get(
        f"{API}/admin/users",
        params={"role": "moderator", "q": user["email"]},
        headers=catalog.headers,
    )
    assert [item["id"] for item in listed.json()["items"]] == [user["id"]]

    admin = (await db_client.get(f"{API}/users/me", headers=catalog.headers)).json()
    self_change = await db_client.patch(
        f"{API}/admin/users/{admin['id']}/role", json={"role": "user"}, headers=catalog.headers
    )
    assert self_change.status_code == status.HTTP_409_CONFLICT

    demoted = await db_client.patch(
        f"{API}/admin/users/{user['id']}/role", json={"role": "user"}, headers=catalog.headers
    )
    assert demoted.json()["role"] == "user"
    lost = await db_client.get(f"{API}/admin/tours", headers=user_headers)
    assert lost.status_code == status.HTTP_403_FORBIDDEN


async def test_past_tours_leave_the_catalog(
    db_client: AsyncClient, catalog: Catalog, db_connection: AsyncConnection
) -> None:
    operator = (await catalog.operator())["id"]
    finished = await catalog.tour(operator, departures=[departure(5)])
    upcoming = await catalog.tour(operator, departures=[departure(5), departure(30)])
    on_request = await catalog.tour(operator, schedule_note="Any day")
    past = datetime.now(UTC) - timedelta(days=10)
    for tour in (finished, upcoming):
        first_departure = tour["departures"][0]["id"]
        await db_connection.execute(
            update(TourDepartureModel)
            .where(TourDepartureModel.id == first_departure)
            .values(start_at=past, end_at=past + timedelta(days=3))
        )

    listed = await db_client.get(f"{API}/tour-operators/{operator}/tours")
    assert {item["id"] for item in listed.json()["items"]} == {upcoming["id"], on_request["id"]}

    detail = await db_client.get(f"{API}/tours/{finished['id']}")
    assert detail.status_code == status.HTTP_200_OK
    assert detail.json()["is_expired"] is True
    assert detail.json()["next_departure"] is None

    expired_only = await db_client.get(
        f"{API}/admin/tours",
        params={"operator_id": operator, "is_expired": True},
        headers=catalog.headers,
    )
    assert [item["id"] for item in expired_only.json()["items"]] == [finished["id"]]
