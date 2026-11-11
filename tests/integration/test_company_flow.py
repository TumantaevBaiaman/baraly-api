from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI, status
from httpx import AsyncClient

from app.presentation.api.dependencies import get_image_storage
from tests.integration.conftest import bearer, sign_in
from tests.integration.test_tours_flow import API, Catalog, tour_body

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class FakeImageStorage:
    def __init__(self) -> None:
        self.saved: dict[str, tuple[bytes, str]] = {}

    async def save(self, key: str, data: bytes, content_type: str) -> str:
        self.saved[key] = (data, content_type)
        return self.public_url(key)

    def public_url(self, key: str) -> str:
        return f"https://cdn.test/{key}"


@pytest.fixture
def storage(db_app: FastAPI) -> FakeImageStorage:
    fake = FakeImageStorage()
    db_app.dependency_overrides[get_image_storage] = lambda: fake
    return fake


@pytest.fixture
def catalog(db_client: AsyncClient, admin_headers: dict[str, str]) -> Catalog:
    return Catalog(db_client, admin_headers)


async def login(client: AsyncClient, email: str) -> dict[str, str]:
    return bearer(await sign_in(client, "google", id_token=f"{uuid4().hex}|{email}"))


def new_email() -> str:
    return f"{uuid4().hex}@example.com"


class Company:
    """Компания с владельцем и менеджером, заведённая через админку."""

    def __init__(self, client: AsyncClient, operator: dict[str, Any]) -> None:
        self.client = client
        self.id = operator["id"]
        self.base = f"{API}/company/{self.id}"
        self.owner: dict[str, str] = {}
        self.manager: dict[str, str] = {}

    async def call(self, method: str, path: str, headers: dict[str, str], **kwargs: Any) -> Any:
        return await self.client.request(method, f"{self.base}{path}", headers=headers, **kwargs)


@pytest.fixture
async def company(db_client: AsyncClient, catalog: Catalog) -> Company:
    owner_email = new_email()
    created = await catalog.post(
        "/tour-operators", {"name": "Ak-sai travel", "owner_email": owner_email}
    )
    # Проверенная компания публикует без модерации; модерацию проверяет test_moderation_flow.
    verified = await db_client.patch(
        f"{API}/admin/tour-operators/{created['id']}/verification",
        json={"is_verified": True, "note": "Документы проверены"},
        headers=catalog.headers,
    )
    assert verified.status_code == status.HTTP_200_OK, verified.text
    result = Company(db_client, created)
    result.owner = await login(db_client, owner_email)

    manager_email = new_email()
    result.manager = await login(db_client, manager_email)
    invited = await result.call(
        "POST", "/members", result.owner, json={"email": manager_email, "role": "manager"}
    )
    assert invited.status_code == status.HTTP_201_CREATED, invited.text
    assert invited.json()["status"] == "member"
    return result


async def test_owner_invited_before_registration_gets_access_on_first_login(
    db_client: AsyncClient, company: Company
) -> None:
    companies = await db_client.get(f"{API}/users/me/companies", headers=company.owner)

    assert companies.status_code == status.HTTP_200_OK
    assert companies.json() == [
        {"operator_id": company.id, "name": "Ak-sai travel", "logo_url": None, "role": "owner"}
    ]


async def test_pending_invitation_is_listed_cancelled_and_claimed(
    db_client: AsyncClient, company: Company
) -> None:
    email = new_email()
    invited = await company.call(
        "POST", "/members", company.owner, json={"email": email.upper(), "role": "manager"}
    )
    assert invited.json() == {"status": "invited", "email": email, "role": "manager"}
    duplicate = await company.call(
        "POST", "/members", company.owner, json={"email": email, "role": "owner"}
    )
    assert duplicate.status_code == status.HTTP_409_CONFLICT

    invitations = (await company.call("GET", "/invitations", company.owner)).json()
    assert [item["email"] for item in invitations] == [email]

    cancelled = await company.call("DELETE", f"/invitations/{invitations[0]['id']}", company.owner)
    assert cancelled.status_code == status.HTTP_204_NO_CONTENT
    stranger = await login(db_client, email)
    no_access = await company.call("GET", "", stranger)
    assert no_access.status_code == status.HTTP_403_FORBIDDEN


async def test_roles_limit_actions(db_client: AsyncClient, company: Company) -> None:
    profile = {"name": "Renamed"}

    manager_profile = await company.call("PUT", "", company.manager, json=profile)
    manager_invite = await company.call(
        "POST", "/members", company.manager, json={"email": new_email(), "role": "owner"}
    )
    manager_view = await company.call("GET", "/members", company.manager)
    owner_profile = await company.call("PUT", "", company.owner, json=profile)

    assert manager_profile.status_code == status.HTTP_403_FORBIDDEN
    assert manager_invite.status_code == status.HTTP_403_FORBIDDEN
    assert manager_view.status_code == status.HTTP_200_OK
    assert {item["role"] for item in manager_view.json()} == {"owner", "manager"}
    assert owner_profile.status_code == status.HTTP_200_OK
    assert owner_profile.json()["name"] == "Renamed"


async def test_outsider_has_no_access(db_client: AsyncClient, company: Company) -> None:
    outsider = await login(db_client, new_email())

    for method, path in (("GET", ""), ("GET", "/tours"), ("GET", "/members")):
        response = await company.call(method, path, outsider)
        assert response.status_code == status.HTTP_403_FORBIDDEN, path

    anonymous = await db_client.get(company.base)
    assert anonymous.status_code == status.HTTP_401_UNAUTHORIZED


async def test_company_manages_only_its_own_tours(
    db_client: AsyncClient, company: Company, catalog: Catalog
) -> None:
    body = tour_body(str(uuid4()))
    body.pop("operator_id")

    created = await company.call("POST", "/tours", company.manager, json=body)
    assert created.status_code == status.HTTP_201_CREATED, created.text
    tour = created.json()
    assert tour["operator"]["id"] == company.id

    other_operator = (await catalog.operator("Other"))["id"]
    foreign = await catalog.tour(other_operator)
    for method, kwargs in (("GET", {}), ("PUT", {"json": body}), ("DELETE", {})):
        response = await company.call(method, f"/tours/{foreign['id']}", company.owner, **kwargs)
        assert response.status_code == status.HTTP_404_NOT_FOUND, method

    smuggled = await company.call(
        "POST", "/tours", company.owner, json=body | {"operator_id": other_operator}
    )
    assert smuggled.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    listed = await company.call("GET", "/tours", company.owner)
    assert [item["id"] for item in listed.json()["items"]] == [tour["id"]]

    updated = await company.call(
        "PUT", f"/tours/{tour['id']}", company.manager, json=body | {"title": "Renamed"}
    )
    assert updated.json()["title"] == "Renamed"
    deleted = await company.call("DELETE", f"/tours/{tour['id']}", company.manager)
    assert deleted.status_code == status.HTTP_204_NO_CONTENT


async def test_admin_acts_inside_any_company(
    db_client: AsyncClient, company: Company, catalog: Catalog
) -> None:
    body = tour_body(company.id)
    body.pop("operator_id")

    created = await company.call("POST", "/tours", catalog.headers, json=body)
    members = await company.call("GET", "/members", catalog.headers)

    assert created.status_code == status.HTTP_201_CREATED
    assert members.status_code == status.HTTP_200_OK


async def test_company_keeps_at_least_one_owner(db_client: AsyncClient, company: Company) -> None:
    members = (await company.call("GET", "/members", company.owner)).json()
    owner_id = next(item["user_id"] for item in members if item["role"] == "owner")
    manager_id = next(item["user_id"] for item in members if item["role"] == "manager")

    demote = await company.call(
        "PATCH", f"/members/{owner_id}", company.owner, json={"role": "manager"}
    )
    remove = await company.call("DELETE", f"/members/{owner_id}", company.owner)
    assert demote.status_code == status.HTTP_409_CONFLICT
    assert remove.status_code == status.HTTP_409_CONFLICT

    promote = await company.call(
        "PATCH", f"/members/{manager_id}", company.owner, json={"role": "owner"}
    )
    assert promote.status_code == status.HTTP_204_NO_CONTENT
    leave = await company.call("DELETE", f"/members/{owner_id}", company.owner)
    assert leave.status_code == status.HTTP_204_NO_CONTENT
    lost_access = await company.call("GET", "", company.owner)
    assert lost_access.status_code == status.HTTP_403_FORBIDDEN


async def test_image_upload(company: Company, storage: FakeImageStorage) -> None:
    uploaded = await company.call(
        "POST", "/images", company.manager, files={"file": ("photo.bin", PNG, "text/plain")}
    )
    assert uploaded.status_code == status.HTTP_201_CREATED, uploaded.text
    key = next(iter(storage.saved))
    assert key.startswith(f"operators/{company.id}/") and key.endswith(".png")
    assert storage.saved[key][1] == "image/png"
    assert uploaded.json()["url"] == f"https://cdn.test/{key}"

    for content in (b"<script>alert(1)</script>", b""):
        rejected = await company.call(
            "POST", "/images", company.manager, files={"file": ("x.png", content, "image/png")}
        )
        assert rejected.status_code == status.HTTP_400_BAD_REQUEST


async def test_image_size_limit(
    db_app: FastAPI, company: Company, storage: FakeImageStorage
) -> None:
    db_app.state.max_image_bytes = len(PNG) - 1

    too_big = await company.call(
        "POST", "/images", company.manager, files={"file": ("big.png", PNG, "image/png")}
    )

    assert too_big.status_code == status.HTTP_400_BAD_REQUEST
    assert storage.saved == {}


async def test_admin_blocks_tour_and_company_cannot_undo(
    db_client: AsyncClient, company: Company, catalog: Catalog
) -> None:
    body = tour_body(company.id)
    body.pop("operator_id")
    tour = (await company.call("POST", "/tours", company.owner, json=body)).json()

    blocked = await db_client.patch(
        f"{API}/admin/tours/{tour['id']}/moderation",
        json={"is_blocked": True, "reason": "Фото не соответствует туру"},
        headers=catalog.headers,
    )
    assert blocked.json()["is_blocked"] is True
    assert (await db_client.get(f"{API}/tours/{tour['id']}")).status_code == 404

    republished = await company.call(
        "PUT", f"/tours/{tour['id']}", company.owner, json=body | {"is_published": True}
    )
    assert republished.json()["is_blocked"] is True
    assert republished.json()["blocked_reason"] == "Фото не соответствует туру"
    assert (await db_client.get(f"{API}/tours/{tour['id']}")).status_code == 404

    admin_filter = await db_client.get(
        f"{API}/admin/tours",
        params={"operator_id": company.id, "is_blocked": True},
        headers=catalog.headers,
    )
    assert [item["id"] for item in admin_filter.json()["items"]] == [tour["id"]]

    await db_client.patch(
        f"{API}/admin/tours/{tour['id']}/moderation",
        json={"is_blocked": False, "reason": "ignored"},
        headers=catalog.headers,
    )
    restored = await db_client.get(f"{API}/tours/{tour['id']}")
    assert restored.status_code == status.HTTP_200_OK
    assert restored.json()["blocked_reason"] is None


async def test_deactivated_company_disappears(
    db_client: AsyncClient, company: Company, catalog: Catalog
) -> None:
    body = tour_body(company.id)
    body.pop("operator_id")
    tour = (await company.call("POST", "/tours", company.owner, json=body)).json()

    response = await db_client.patch(
        f"{API}/admin/tour-operators/{company.id}/status",
        json={"is_active": False},
        headers=catalog.headers,
    )
    assert response.json()["is_active"] is False

    assert (await db_client.get(f"{API}/tours/{tour['id']}")).status_code == 404
    assert (await db_client.get(f"{API}/tour-operators/{company.id}")).status_code == 404
    workspace = await company.call("GET", "/tours", company.owner)
    assert workspace.status_code == status.HTTP_403_FORBIDDEN
    admin_view = await company.call("GET", "/tours", catalog.headers)
    assert admin_view.status_code == status.HTTP_200_OK


async def test_admin_company_list_filters_and_counts(
    db_client: AsyncClient, company: Company, catalog: Catalog
) -> None:
    body = tour_body(company.id)
    body.pop("operator_id")
    await company.call("POST", "/tours", company.owner, json=body)
    await company.call("POST", "/tours", company.owner, json=body | {"is_published": False})
    marker = uuid4().hex
    inactive = await catalog.operator(f"{marker} inactive")
    await db_client.patch(
        f"{API}/admin/tour-operators/{inactive['id']}/status",
        json={"is_active": False},
        headers=catalog.headers,
    )

    detail = (
        await db_client.get(f"{API}/admin/tour-operators/{company.id}", headers=catalog.headers)
    ).json()
    assert (detail["tours_count"], detail["published_tours_count"]) == (2, 1)
    assert detail["members_count"] == 2

    by_name = await db_client.get(
        f"{API}/admin/tour-operators",
        params={"q": marker, "is_active": False},
        headers=catalog.headers,
    )
    assert [item["id"] for item in by_name.json()["items"]] == [inactive["id"]]
    public = await db_client.get(f"{API}/tour-operators", params={"limit": 100})
    assert inactive["id"] not in {item["id"] for item in public.json()["items"]}


async def test_company_reviews_and_moderation(
    db_client: AsyncClient, company: Company, catalog: Catalog
) -> None:
    body = tour_body(company.id)
    body.pop("operator_id")
    tour = (await company.call("POST", "/tours", company.owner, json=body)).json()
    traveler = await login(db_client, new_email())

    company_review = await db_client.put(
        f"{API}/tour-operators/{company.id}/reviews/me",
        json={"rating": 2, "text": "Late pickup"},
        headers=traveler,
    )
    assert company_review.status_code == status.HTTP_200_OK, company_review.text
    assert company_review.json()["kind"] == "company"
    await db_client.put(
        f"{API}/tours/{tour['id']}/reviews/me",
        json={"rating": 4, "text": "Great lake"},
        headers=traveler,
    )

    operator = (await db_client.get(f"{API}/tour-operators/{company.id}")).json()
    assert (operator["rating"], operator["review_count"]) == (3.0, 2)
    only_company = await db_client.get(
        f"{API}/tour-operators/{company.id}/reviews", params={"kind": "company"}
    )
    assert [item["kind"] for item in only_company.json()["items"]] == ["company"]

    review_id = company_review.json()["id"]
    hidden = await db_client.patch(
        f"{API}/admin/reviews/{review_id}", json={"is_hidden": True}, headers=catalog.headers
    )
    assert hidden.json()["is_hidden"] is True
    operator = (await db_client.get(f"{API}/tour-operators/{company.id}")).json()
    assert (operator["rating"], operator["review_count"]) == (4.0, 1)
    public = await db_client.get(f"{API}/tour-operators/{company.id}/reviews")
    assert review_id not in {item["id"] for item in public.json()["items"]}

    in_cabinet = await company.call("GET", "/reviews", company.manager, params={"is_hidden": True})
    assert [item["id"] for item in in_cabinet.json()["items"]] == [review_id]

    admin_filter = await db_client.get(
        f"{API}/admin/reviews",
        params={"operator_id": company.id, "rating_max": 3, "q": "pickup"},
        headers=catalog.headers,
    )
    assert [item["id"] for item in admin_filter.json()["items"]] == [review_id]

    deleted = await db_client.delete(f"{API}/admin/reviews/{review_id}", headers=catalog.headers)
    assert deleted.status_code == status.HTTP_204_NO_CONTENT
    mine = await db_client.get(f"{API}/users/me/reviews", headers=traveler)
    assert [item["kind"] for item in mine.json()["items"]] == ["tour"]


async def test_legal_details_are_not_public(
    db_client: AsyncClient, company: Company, catalog: Catalog
) -> None:
    profile = {"name": "Ak-sai travel", "legal_name": "ОсОО Ак-Сай", "license_number": "L-123"}
    saved = await company.call("PUT", "", company.owner, json=profile)
    assert saved.json()["license_number"] == "L-123"

    public = (await db_client.get(f"{API}/tour-operators/{company.id}")).json()
    assert "license_number" not in public and "legal_name" not in public
    admin = await db_client.get(f"{API}/admin/tour-operators/{company.id}", headers=catalog.headers)
    assert admin.json()["legal_name"] == "ОсОО Ак-Сай"
