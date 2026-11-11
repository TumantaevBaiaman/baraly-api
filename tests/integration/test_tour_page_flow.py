"""Страница тура: программа с фото, длительность, календарь выездов и отзывы с фото."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI, status
from httpx import AsyncClient

from app.presentation.api.dependencies import get_image_storage
from tests.integration.conftest import SignInUser, bearer
from tests.integration.test_company_flow import PNG, FakeImageStorage
from tests.integration.test_tours_flow import API, Catalog, departure, tour_body

pytestmark = pytest.mark.usefixtures("storage")


@pytest.fixture
def storage(db_app: FastAPI) -> FakeImageStorage:
    fake = FakeImageStorage()
    db_app.dependency_overrides[get_image_storage] = lambda: fake
    return fake


@pytest.fixture
def catalog(db_client: AsyncClient, admin_headers: dict[str, str]) -> Catalog:
    return Catalog(db_client, admin_headers)


def departure_on(days_ahead: int, price: float | None = None) -> dict[str, Any]:
    body = departure(days_ahead)
    if price is not None:
        body["price"] = price
    return body


async def test_program_days_have_photos_and_tour_shows_duration(
    db_client: AsyncClient, catalog: Catalog
) -> None:
    operator = (await catalog.operator())["id"]
    created = await catalog.tour(
        operator,
        duration_days=8,
        duration_nights=7,
        short_description="Четыре острова, всё включено",
        days=[
            {
                "day_number": 1,
                "title": "Bishkek - Son-Kol",
                "image_urls": ["https://img.test/a.jpg", "https://img.test/b.jpg"],
            },
            {"day_number": 2, "title": "Son-Kol - Kel-Suu"},
        ],
    )

    tour = (await db_client.get(f"{API}/tours/{created['id']}")).json()
    card = (await db_client.get(f"{API}/tour-operators/{operator}/tours")).json()["items"][0]

    assert (tour["duration_days"], tour["duration_nights"]) == (8, 7)
    assert tour["short_description"] == "Четыре острова, всё включено"
    assert tour["days"][0]["image_urls"] == ["https://img.test/a.jpg", "https://img.test/b.jpg"]
    assert tour["days"][1]["image_urls"] == []
    assert card["duration_nights"] == 7


@pytest.mark.parametrize("nights", [9, -1])
async def test_nights_cannot_exceed_days(
    db_client: AsyncClient, catalog: Catalog, nights: int
) -> None:
    operator = (await catalog.operator())["id"]
    response = await db_client.post(
        f"{API}/admin/tours",
        json=tour_body(operator, duration_days=8, duration_nights=nights),
        headers=catalog.headers,
    )

    assert response.status_code in {
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
    }


async def test_calendar_lists_future_departures_with_prices(
    db_client: AsyncClient, catalog: Catalog
) -> None:
    operator = (await catalog.operator())["id"]
    tour = await catalog.tour(
        operator,
        price=300,
        departures=[departure_on(10), departure_on(40, price=450), departure_on(400)],
    )

    default = await db_client.get(f"{API}/tours/{tour['id']}/departures")
    assert default.status_code == status.HTTP_200_OK
    prices = [item["price"] for item in default.json()]
    assert prices == [300, 450]

    soon = (datetime.now(UTC) + timedelta(days=20)).date().isoformat()
    window = await db_client.get(f"{API}/tours/{tour['id']}/departures", params={"date_to": soon})
    assert [item["price"] for item in window.json()] == [300]

    card = (await db_client.get(f"{API}/tours/{tour['id']}")).json()
    assert card["next_departure"]["price"] == 300
    assert [item["price"] for item in card["departures"]] == [300, 450, 300]

    inverted = await db_client.get(
        f"{API}/tours/{tour['id']}/departures",
        params={"date_from": soon, "date_to": datetime.now(UTC).date().isoformat()},
    )
    assert inverted.status_code == status.HTTP_400_BAD_REQUEST


async def test_calendar_hidden_for_draft(db_client: AsyncClient, catalog: Catalog) -> None:
    operator = (await catalog.operator())["id"]
    draft = await catalog.tour(operator, is_published=False, departures=[departure_on(5)])

    response = await db_client.get(f"{API}/tours/{draft['id']}/departures")

    assert response.status_code == status.HTTP_404_NOT_FOUND


async def test_review_photos(
    db_client: AsyncClient,
    catalog: Catalog,
    sign_in_user: SignInUser,
    storage: FakeImageStorage,
) -> None:
    operator = (await catalog.operator())["id"]
    tour = (await catalog.tour(operator))["id"]
    traveler = bearer(await sign_in_user())
    other = bearer(await sign_in_user())

    uploaded = await db_client.post(
        f"{API}/users/me/images", files={"file": ("trip.png", PNG, "image/png")}, headers=traveler
    )
    assert uploaded.status_code == status.HTTP_201_CREATED, uploaded.text
    photo = uploaded.json()["url"]
    foreign = (
        await db_client.post(
            f"{API}/users/me/images", files={"file": ("x.png", PNG, "image/png")}, headers=other
        )
    ).json()["url"]

    review = await db_client.put(
        f"{API}/tours/{tour}/reviews/me",
        json={"rating": 5, "text": "Great", "photo_urls": [photo]},
        headers=traveler,
    )
    assert review.status_code == status.HTTP_200_OK, review.text
    assert review.json()["photo_urls"] == [photo]

    for urls in ([foreign], ["https://evil.test/pixel.png"]):
        rejected = await db_client.put(
            f"{API}/tours/{tour}/reviews/me",
            json={"rating": 5, "text": "Great", "photo_urls": urls},
            headers=traveler,
        )
        assert rejected.status_code == status.HTTP_400_BAD_REQUEST

    company_review = await db_client.put(
        f"{API}/tour-operators/{operator}/reviews/me",
        json={"rating": 5, "text": "Nice", "photo_urls": [photo]},
        headers=traveler,
    )
    assert company_review.status_code == status.HTTP_400_BAD_REQUEST

    listed = await db_client.get(f"{API}/tours/{tour}/reviews")
    assert listed.json()["items"][0]["photo_urls"] == [photo]
    photos = await db_client.get(f"{API}/tours/{tour}/reviews/photos")
    assert photos.json()["total"] == 1
    assert photos.json()["items"][0]["url"] == photo

    cleared = await db_client.put(
        f"{API}/tours/{tour}/reviews/me",
        json={"rating": 4, "text": "Good"},
        headers=traveler,
    )
    assert cleared.json()["photo_urls"] == []
    assert (await db_client.get(f"{API}/tours/{tour}/reviews/photos")).json()["total"] == 0


async def test_user_image_upload_requires_sign_in(db_client: AsyncClient) -> None:
    response = await db_client.post(
        f"{API}/users/me/images", files={"file": ("trip.png", PNG, "image/png")}
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


async def test_user_sets_and_removes_avatar(
    db_client: AsyncClient, sign_in_user: SignInUser, storage: FakeImageStorage
) -> None:
    headers = bearer(await sign_in_user())

    uploaded = await db_client.post(
        f"{API}/users/me/avatar", files={"file": ("me.png", PNG, "image/png")}, headers=headers
    )
    assert uploaded.status_code == status.HTTP_200_OK, uploaded.text
    avatar = uploaded.json()["avatar_url"]
    assert "/avatars/" in avatar and avatar.endswith(".png")
    me = (await db_client.get(f"{API}/users/me", headers=headers)).json()
    assert me["avatar_url"] == avatar

    rejected = await db_client.post(
        f"{API}/users/me/avatar",
        files={"file": ("me.png", b"not an image", "image/png")},
        headers=headers,
    )
    assert rejected.status_code == status.HTTP_400_BAD_REQUEST
    assert (await db_client.get(f"{API}/users/me", headers=headers)).json()["avatar_url"] == avatar

    removed = await db_client.delete(f"{API}/users/me/avatar", headers=headers)
    assert removed.json()["avatar_url"] is None
