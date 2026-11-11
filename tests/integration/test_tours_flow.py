from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi import status
from httpx import AsyncClient

from tests.integration.conftest import SignInUser, bearer

API = "/api/v1"


class Catalog:
    """Заводит данные через настоящие админские запросы."""

    def __init__(self, client: AsyncClient, headers: dict[str, str]) -> None:
        self.client = client
        self.headers = headers

    async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        response = await self.client.post(f"{API}/admin{path}", json=body, headers=self.headers)
        assert response.status_code == status.HTTP_201_CREATED, response.text
        result: dict[str, Any] = response.json()
        return result

    async def operator(self, name: str = "Operator") -> dict[str, Any]:
        return await self.post("/tour-operators", {"name": name, "phone": "+996502800202"})

    async def location(self, name: str | None = None) -> dict[str, Any]:
        return await self.post(
            "/locations", {"name": name or uuid4().hex, "latitude": 41.8, "longitude": 75.1}
        )

    async def tour(self, operator_id: str, **overrides: Any) -> dict[str, Any]:
        return await self.post("/tours", tour_body(operator_id, **overrides))


def tour_body(operator_id: str, **overrides: Any) -> dict[str, Any]:
    return {
        "operator_id": operator_id,
        "title": f"Tour {uuid4().hex}",
        "description": "Mountains",
        "price": 300,
        "duration_days": 4,
        "activity": "moderate",
        "comfort": "high",
        "min_age": 10,
        "max_age": 75,
        "prepayment_percent": 34,
        "is_published": True,
    } | overrides


def departure(days_ahead: int, capacity: int = 10) -> dict[str, Any]:
    start = datetime.now(UTC) + timedelta(days=days_ahead)
    return {
        "start_at": start.isoformat(),
        "end_at": (start + timedelta(days=3)).isoformat(),
        "capacity": capacity,
    }


@pytest.fixture
def catalog(db_client: AsyncClient, admin_headers: dict[str, str]) -> Catalog:
    return Catalog(db_client, admin_headers)


async def search(client: AsyncClient, **params: Any) -> dict[str, Any]:
    response = await client.get(f"{API}/tours", params=params)
    assert response.status_code == status.HTTP_200_OK, response.text
    result: dict[str, Any] = response.json()
    return result


async def found_ids(client: AsyncClient, operator_id: str, **params: Any) -> set[str]:
    response = await client.get(f"{API}/tour-operators/{operator_id}/tours", params=params)
    assert response.status_code == status.HTTP_200_OK, response.text
    return {item["id"] for item in response.json()["items"]}


async def test_full_tour_detail_round_trip(db_client: AsyncClient, catalog: Catalog) -> None:
    operator = await catalog.operator("Ak-sai travel")
    start = await catalog.location()
    day_location = await catalog.location()

    created = await catalog.tour(
        operator["id"],
        title="Son-Kul",
        tag_slugs=["trekking", "ethno"],
        language_codes=["en", "ru"],
        image_urls=["https://img.test/1.jpg", "https://img.test/2.jpg"],
        included=["Transfer", "Guide"],
        excluded=["Drinks"],
        start_location_id=start["id"],
        finish_location_id=start["id"],
        free_cancellation_hours=24,
        accommodation="Yurts",
        days=[
            {
                "day_number": 2,
                "title": "Son-Kol - Kel-Suu",
                "location_id": day_location["id"],
                "meals": ["breakfast", "dinner"],
                "landmarks": ["Pass", "Lake"],
            },
            {"day_number": 1, "title": "Bishkek - Son-Kol", "meals": ["lunch"]},
        ],
        departures=[departure(30), departure(10)],
    )

    response = await db_client.get(f"{API}/tours/{created['id']}")
    assert response.status_code == status.HTTP_200_OK
    tour = response.json()

    assert tour["operator"]["name"] == "Ak-sai travel"
    assert {tag["slug"] for tag in tour["tags"]} == {"trekking", "ethno"}
    assert {item["code"] for item in tour["languages"]} == {"en", "ru"}
    assert tour["cover_image_url"] == "https://img.test/1.jpg"
    assert tour["image_urls"] == ["https://img.test/1.jpg", "https://img.test/2.jpg"]
    assert tour["included"] == ["Transfer", "Guide"]
    assert tour["excluded"] == ["Drinks"]
    assert tour["start_location"]["id"] == start["id"]
    assert [day["day_number"] for day in tour["days"]] == [1, 2]
    assert tour["days"][1]["location"]["id"] == day_location["id"]
    assert set(tour["days"][1]["meals"]) == {"breakfast", "dinner"}
    assert tour["days"][1]["landmarks"] == ["Pass", "Lake"]
    assert tour["price"] == 300
    assert tour["currency"] == "USD"
    assert tour["rating"] is None
    assert tour["review_count"] == 0
    assert tour["next_departure"]["id"] == tour["departures"][0]["id"]


async def test_filters(db_client: AsyncClient, catalog: Catalog) -> None:
    operator = (await catalog.operator())["id"]
    location_name = f"Issyk-Kul {uuid4().hex}"
    location = await catalog.location(location_name)

    cheap = await catalog.tour(
        operator,
        title="Cheap unique-word",
        price=100,
        duration_days=2,
        activity="easy",
        min_age=18,
        max_age=39,
        tag_slugs=["water"],
        language_codes=["ko"],
        departures=[departure(5, capacity=2)],
    )
    costly = await catalog.tour(
        operator,
        price=500,
        duration_days=9,
        activity="extreme",
        min_age=50,
        max_age=70,
        tag_slugs=["trekking"],
        language_codes=["tr"],
        start_location_id=location["id"],
        departures=[departure(60, capacity=20)],
    )
    flexible = await catalog.tour(
        operator, price=300, duration_days=1, schedule_note="Any day", activity="for_all"
    )
    cheap_id, costly_id, flexible_id = cheap["id"], costly["id"], flexible["id"]

    async def ids(**params: Any) -> set[str]:
        return await found_ids(db_client, operator, **params)

    assert await ids() == {cheap_id, costly_id, flexible_id}
    assert await ids(price_min=200, price_max=400) == {flexible_id}
    assert await ids(duration_min=3) == {costly_id}
    assert await ids(duration_max=1) == {flexible_id}
    assert await ids(tags=["water", "trekking"]) == {cheap_id, costly_id}
    assert await ids(languages=["ko"]) == {cheap_id}
    assert await ids(activity=["extreme", "for_all"]) == {costly_id, flexible_id}
    assert await ids(age_from=60) >= {costly_id}
    assert cheap_id not in await ids(age_from=60)
    assert await ids(q="unique-word") == {cheap_id}
    assert await ids(location=location_name[:12]) == {costly_id}

    near_end = (datetime.now(UTC) + timedelta(days=20)).date().isoformat()
    assert await ids(date_to=near_end) == {cheap_id, flexible_id}
    assert await ids(persons=5) == {costly_id, flexible_id}

    by_price = await db_client.get(
        f"{API}/tour-operators/{operator}/tours", params={"sort": "price_desc", "limit": 1}
    )
    assert by_price.json()["total"] == 3
    assert [item["id"] for item in by_price.json()["items"]] == [costly_id]


async def test_like_wildcards_are_literal(db_client: AsyncClient, catalog: Catalog) -> None:
    operator = (await catalog.operator())["id"]
    await catalog.tour(operator)

    assert await found_ids(db_client, operator, q="%") == set()


async def test_drafts_are_hidden_from_public(db_client: AsyncClient, catalog: Catalog) -> None:
    operator = (await catalog.operator())["id"]
    draft = await catalog.tour(operator, is_published=False)

    public = await db_client.get(f"{API}/tours/{draft['id']}")
    admin = await db_client.get(f"{API}/admin/tours/{draft['id']}", headers=catalog.headers)
    admin_list = await db_client.get(
        f"{API}/admin/tours", params={"operator_id": operator}, headers=catalog.headers
    )

    assert public.status_code == status.HTTP_404_NOT_FOUND
    assert await found_ids(db_client, operator) == set()
    assert admin.status_code == status.HTTP_200_OK
    assert [item["id"] for item in admin_list.json()["items"]] == [draft["id"]]


async def test_admin_endpoints_require_admin(
    db_client: AsyncClient, sign_in_user: SignInUser
) -> None:
    body = {"name": "Operator"}

    anonymous = await db_client.post(f"{API}/admin/tour-operators", json=body)
    regular = await db_client.post(
        f"{API}/admin/tour-operators", json=body, headers=bearer(await sign_in_user())
    )

    assert anonymous.status_code == status.HTTP_401_UNAUTHORIZED
    assert regular.status_code == status.HTTP_403_FORBIDDEN


async def test_update_replaces_content_and_keeps_departure_ids(
    db_client: AsyncClient, catalog: Catalog
) -> None:
    operator = (await catalog.operator())["id"]
    created = await catalog.tour(
        operator,
        tag_slugs=["water"],
        days=[{"day_number": 1, "title": "Old", "landmarks": ["Old"]}],
        departures=[departure(10), departure(20)],
    )
    kept, removed = created["departures"]
    body = tour_body(
        operator,
        title="New title",
        tag_slugs=["ethno"],
        days=[{"day_number": 1, "title": "New"}],
        departures=[
            {
                "id": kept["id"],
                "start_at": kept["start_at"],
                "end_at": kept["end_at"],
                "capacity": 99,
            },
            departure(40),
        ],
    )

    response = await db_client.put(
        f"{API}/admin/tours/{created['id']}", json=body, headers=catalog.headers
    )

    assert response.status_code == status.HTTP_200_OK, response.text
    updated = response.json()
    assert updated["title"] == "New title"
    assert [tag["slug"] for tag in updated["tags"]] == ["ethno"]
    assert updated["days"][0]["title"] == "New"
    assert updated["days"][0]["landmarks"] == []
    departures = {item["id"]: item for item in updated["departures"]}
    assert len(departures) == 2
    assert departures[kept["id"]]["capacity"] == 99
    assert removed["id"] not in departures


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"min_age": 80, "max_age": 10}, status.HTTP_400_BAD_REQUEST),
        ({"tag_slugs": ["no-such-tag"]}, status.HTTP_409_CONFLICT),
        ({"language_codes": ["xx"]}, status.HTTP_409_CONFLICT),
        ({"tag_slugs": ["water", "water"]}, status.HTTP_409_CONFLICT),
        ({"price": -5}, status.HTTP_422_UNPROCESSABLE_CONTENT),
    ],
    ids=["inverted-ages", "unknown-tag", "unknown-language", "duplicate-tag", "negative-price"],
)
async def test_invalid_tour_is_rejected(
    db_client: AsyncClient, catalog: Catalog, overrides: dict[str, Any], expected: int
) -> None:
    operator = (await catalog.operator())["id"]

    response = await db_client.post(
        f"{API}/admin/tours", json=tour_body(operator, **overrides), headers=catalog.headers
    )

    assert response.status_code == expected, response.text


async def test_unknown_operator_is_rejected(db_client: AsyncClient, catalog: Catalog) -> None:
    response = await db_client.post(
        f"{API}/admin/tours", json=tour_body(str(uuid4())), headers=catalog.headers
    )

    assert response.status_code == status.HTTP_409_CONFLICT


async def test_reference_data_cannot_be_deleted_while_used(
    db_client: AsyncClient, catalog: Catalog
) -> None:
    operator = (await catalog.operator())["id"]
    location = await catalog.location()
    tag = await catalog.post("/tags", {"slug": uuid4().hex[:10], "name": "Rafting"})
    tour = await catalog.tour(operator, tag_slugs=[tag["slug"]], start_location_id=location["id"])

    for path in (
        f"/tour-operators/{operator}",
        f"/locations/{location['id']}",
        f"/tags/{tag['slug']}",
    ):
        blocked = await db_client.delete(f"{API}/admin{path}", headers=catalog.headers)
        assert blocked.status_code == status.HTTP_409_CONFLICT, path

    deleted = await db_client.delete(f"{API}/admin/tours/{tour['id']}", headers=catalog.headers)
    assert deleted.status_code == status.HTTP_204_NO_CONTENT
    assert (await db_client.get(f"{API}/tours/{tour['id']}")).status_code == 404

    for path in (
        f"/tour-operators/{operator}",
        f"/locations/{location['id']}",
        f"/tags/{tag['slug']}",
    ):
        allowed = await db_client.delete(f"{API}/admin{path}", headers=catalog.headers)
        assert allowed.status_code == status.HTTP_204_NO_CONTENT, path


async def test_reviews_drive_ratings(
    db_client: AsyncClient, catalog: Catalog, sign_in_user: SignInUser
) -> None:
    operator = (await catalog.operator())["id"]
    first_tour = (await catalog.tour(operator))["id"]
    second_tour = (await catalog.tour(operator))["id"]
    alice = bearer(await sign_in_user())
    bob = bearer(await sign_in_user())

    async def review(tour_id: str, headers: dict[str, str], rating: int) -> dict[str, Any]:
        response = await db_client.put(
            f"{API}/tours/{tour_id}/reviews/me",
            json={"rating": rating, "text": "Great trip"},
            headers=headers,
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        result: dict[str, Any] = response.json()
        return result

    created = await review(first_tour, alice, 1)
    updated = await review(first_tour, alice, 5)
    await review(first_tour, bob, 4)
    await review(second_tour, bob, 3)

    assert created["id"] == updated["id"]
    assert updated["rating"] == 5

    tour = (await db_client.get(f"{API}/tours/{first_tour}")).json()
    assert (tour["rating"], tour["review_count"]) == (4.5, 2)
    assert (tour["operator"]["rating"], tour["operator"]["review_count"]) == (4.0, 3)

    popular = await db_client.get(f"{API}/tour-operators/{operator}/tours")
    assert popular.json()["items"][0]["id"] == first_tour

    op_detail = (await db_client.get(f"{API}/tour-operators/{operator}")).json()
    assert op_detail["review_count"] == 3
    op_reviews = await db_client.get(f"{API}/tour-operators/{operator}/reviews")
    assert op_reviews.json()["total"] == 3
    tour_reviews = await db_client.get(f"{API}/tours/{first_tour}/reviews")
    assert tour_reviews.json()["total"] == 2
    mine = await db_client.get(f"{API}/users/me/reviews", headers=bob)
    assert mine.json()["total"] == 2

    deleted = await db_client.delete(f"{API}/tours/{first_tour}/reviews/me", headers=alice)
    assert deleted.status_code == status.HTTP_204_NO_CONTENT
    again = await db_client.delete(f"{API}/tours/{first_tour}/reviews/me", headers=alice)
    assert again.status_code == status.HTTP_404_NOT_FOUND
    tour = (await db_client.get(f"{API}/tours/{first_tour}")).json()
    assert (tour["rating"], tour["review_count"]) == (4.0, 1)

    bob_review = tour_reviews.json()["items"][0]["id"]
    forbidden = await db_client.delete(f"{API}/admin/reviews/{bob_review}", headers=bob)
    assert forbidden.status_code == status.HTTP_403_FORBIDDEN
    moderated = await db_client.delete(f"{API}/admin/reviews/{bob_review}", headers=catalog.headers)
    assert moderated.status_code == status.HTTP_204_NO_CONTENT


async def test_review_requires_sign_in_and_published_tour(
    db_client: AsyncClient, catalog: Catalog, sign_in_user: SignInUser
) -> None:
    operator = (await catalog.operator())["id"]
    published = (await catalog.tour(operator))["id"]
    draft = (await catalog.tour(operator, is_published=False))["id"]
    body = {"rating": 5, "text": "Great"}

    anonymous = await db_client.put(f"{API}/tours/{published}/reviews/me", json=body)
    on_draft = await db_client.put(
        f"{API}/tours/{draft}/reviews/me", json=body, headers=bearer(await sign_in_user())
    )

    assert anonymous.status_code == status.HTTP_401_UNAUTHORIZED
    assert on_draft.status_code == status.HTTP_404_NOT_FOUND


async def test_favorites(
    db_client: AsyncClient, catalog: Catalog, sign_in_user: SignInUser
) -> None:
    operator = (await catalog.operator())["id"]
    tour = (await catalog.tour(operator))["id"]
    draft = (await catalog.tour(operator, is_published=False))["id"]
    headers = bearer(await sign_in_user())
    other_user = bearer(await sign_in_user())

    for _ in range(2):
        added = await db_client.put(f"{API}/users/me/favorites/{tour}", headers=headers)
        assert added.status_code == status.HTTP_204_NO_CONTENT
    hidden = await db_client.put(f"{API}/users/me/favorites/{draft}", headers=headers)
    assert hidden.status_code == status.HTTP_404_NOT_FOUND

    favorites = await db_client.get(f"{API}/users/me/favorites", headers=headers)
    assert [item["id"] for item in favorites.json()["items"]] == [tour]
    ids = await db_client.get(f"{API}/users/me/favorites/ids", headers=headers)
    assert ids.json() == [tour]
    others = await db_client.get(f"{API}/users/me/favorites/ids", headers=other_user)
    assert others.json() == []

    removed = await db_client.delete(f"{API}/users/me/favorites/{tour}", headers=headers)
    assert removed.status_code == status.HTTP_204_NO_CONTENT
    ids = await db_client.get(f"{API}/users/me/favorites/ids", headers=headers)
    assert ids.json() == []

    anonymous = await db_client.get(f"{API}/users/me/favorites")
    assert anonymous.status_code == status.HTTP_401_UNAUTHORIZED


async def test_reference_lists_contain_site_filters(
    db_client: AsyncClient, catalog: Catalog
) -> None:
    location = await catalog.location()

    tags = (await db_client.get(f"{API}/tags")).json()
    languages = (await db_client.get(f"{API}/languages")).json()
    locations = (await db_client.get(f"{API}/locations")).json()

    assert {"trekking", "water", "active", "ethno"} <= {item["slug"] for item in tags}
    assert {"ru", "en", "ko", "tr"} <= {item["code"] for item in languages}
    assert location["id"] in {item["id"] for item in locations}


async def test_search_endpoint_returns_only_published(
    db_client: AsyncClient, catalog: Catalog
) -> None:
    operator = (await catalog.operator())["id"]
    published = await catalog.tour(operator, title=f"Needle {operator}")
    await catalog.tour(operator, title=f"Needle {operator}", is_published=False)

    result = await search(db_client, q=f"Needle {operator}")

    assert [item["id"] for item in result["items"]] == [published["id"]]
    assert result["total"] == 1
