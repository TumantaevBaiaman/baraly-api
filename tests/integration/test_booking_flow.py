"""Покупка мест: удержание, оплата, возврат мест и защита от перепродажи."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi import status
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncConnection

from app.infrastructure.db.models import BookingModel
from tests.integration.conftest import SignInUser, bearer
from tests.integration.test_tours_flow import API, Catalog, departure, tour_body


@pytest.fixture
def catalog(db_client: AsyncClient, admin_headers: dict[str, str]) -> Catalog:
    return Catalog(db_client, admin_headers)


def traveler(**overrides: Any) -> dict[str, Any]:
    return {
        "first_name": "Aigul",
        "last_name": "Asanova",
        "birth_date": "1995-05-20",
        "gender": "female",
        "phone": "+996 502800202",
    } | overrides


class Tour:
    def __init__(self, data: dict[str, Any]) -> None:
        self.id = data["id"]
        self.operator_id = data["operator"]["id"]
        self.departure_id = data["departures"][0]["id"]


async def create_tour(catalog: Catalog, capacity: int = 3, **overrides: Any) -> Tour:
    operator = (await catalog.operator())["id"]
    data = await catalog.tour(
        operator,
        price=300,
        prepayment_percent=34,
        departures=[departure(10, capacity)],
        **overrides,
    )
    return Tour(data)


async def reserve(
    client: AsyncClient, headers: dict[str, str], departure_id: str, seats: int = 1, **extra: Any
) -> Any:
    travelers = [traveler(**extra) for _ in range(seats)]
    return await client.post(
        f"{API}/bookings",
        json={"departure_id": departure_id, "travelers": travelers},
        headers=headers,
    )


async def seats_left(client: AsyncClient, tour: Tour) -> int:
    departures = (await client.get(f"{API}/tours/{tour.id}/departures")).json()
    return int(departures[0]["seats_left"])


async def expire(connection: AsyncConnection, booking_id: str) -> None:
    await connection.execute(
        update(BookingModel)
        .where(BookingModel.id == booking_id)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )


async def pay(client: AsyncClient, headers: dict[str, str], booking_id: str) -> Any:
    return await client.post(f"{API}/bookings/{booking_id}/pay", headers=headers)


async def complete(client: AsyncClient, redirect_url: str) -> Any:
    reference = redirect_url.rstrip("/").split("/")[-2]
    return await client.post(f"{API}/payments/fake/{reference}/complete")


async def test_reserve_holds_seats_and_prices(
    db_client: AsyncClient, catalog: Catalog, sign_in_user: SignInUser
) -> None:
    tour = await create_tour(catalog, capacity=3)
    user = bearer(await sign_in_user())

    response = await reserve(db_client, user, tour.departure_id, seats=2)

    assert response.status_code == status.HTTP_201_CREATED, response.text
    booking = response.json()
    assert booking["status"] == "pending_payment"
    assert (booking["total_price"], booking["prepayment_amount"]) == (600, 204)
    assert booking["travelers"][0]["phone"] == "+996502800202"
    assert await seats_left(db_client, tour) == 1


async def test_seats_cannot_be_oversold(
    db_client: AsyncClient, catalog: Catalog, sign_in_user: SignInUser
) -> None:
    tour = await create_tour(catalog, capacity=2)
    first = bearer(await sign_in_user())
    second = bearer(await sign_in_user())

    held = await reserve(db_client, first, tour.departure_id, seats=2)
    sold_out = await reserve(db_client, second, tour.departure_id, seats=1)

    assert held.status_code == status.HTTP_201_CREATED
    assert sold_out.status_code == status.HTTP_409_CONFLICT
    assert "0 seats" in sold_out.json()["detail"]


async def test_unpaid_hold_expires_and_returns_seats(
    db_client: AsyncClient,
    catalog: Catalog,
    sign_in_user: SignInUser,
    db_connection: AsyncConnection,
) -> None:
    tour = await create_tour(catalog, capacity=1)
    first = bearer(await sign_in_user())
    second = bearer(await sign_in_user())
    held = (await reserve(db_client, first, tour.departure_id)).json()

    await expire(db_connection, held["id"])

    assert await seats_left(db_client, tour) == 1
    mine = (await db_client.get(f"{API}/bookings/{held['id']}", headers=first)).json()
    assert mine["status"] == "expired"
    assert (await pay(db_client, first, held["id"])).status_code == status.HTTP_409_CONFLICT
    taken_by_other = await reserve(db_client, second, tour.departure_id)
    assert taken_by_other.status_code == status.HTTP_201_CREATED


async def test_cancel_returns_seats(
    db_client: AsyncClient, catalog: Catalog, sign_in_user: SignInUser
) -> None:
    tour = await create_tour(catalog, capacity=2)
    user = bearer(await sign_in_user())
    held = (await reserve(db_client, user, tour.departure_id, seats=2)).json()

    cancelled = await db_client.post(f"{API}/bookings/{held['id']}/cancel", headers=user)

    assert cancelled.json()["status"] == "cancelled"
    assert await seats_left(db_client, tour) == 2


async def test_repeated_buy_click_replaces_previous_hold(
    db_client: AsyncClient, catalog: Catalog, sign_in_user: SignInUser
) -> None:
    tour = await create_tour(catalog, capacity=3)
    user = bearer(await sign_in_user())
    first = (await reserve(db_client, user, tour.departure_id, seats=2)).json()

    second = (await reserve(db_client, user, tour.departure_id, seats=1)).json()

    assert await seats_left(db_client, tour) == 2
    old = (await db_client.get(f"{API}/bookings/{first['id']}", headers=user)).json()
    assert old["status"] == "cancelled"
    assert second["status"] == "pending_payment"


async def test_payment_confirms_booking_for_user_and_company(
    db_client: AsyncClient, catalog: Catalog, sign_in_user: SignInUser
) -> None:
    tour = await create_tour(catalog, capacity=3)
    user = bearer(await sign_in_user())
    held = (await reserve(db_client, user, tour.departure_id, seats=2)).json()

    started = await pay(db_client, user, held["id"])
    assert started.status_code == status.HTTP_200_OK, started.text
    redirect_url = started.json()["redirect_url"]
    assert "/payments/fake/" in redirect_url

    confirmed = await complete(db_client, redirect_url)
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["paid_at"] is not None
    again = await complete(db_client, redirect_url)
    assert again.json()["status"] == "confirmed"
    assert await seats_left(db_client, tour) == 1

    mine = await db_client.get(
        f"{API}/users/me/bookings", params={"status": "confirmed"}, headers=user
    )
    assert [item["id"] for item in mine.json()["items"]] == [held["id"]]

    company = await db_client.get(
        f"{API}/company/{tour.operator_id}/bookings", headers=catalog.headers
    )
    assert company.json()["items"][0]["user_email"].endswith("@example.com")
    assert len(company.json()["items"][0]["travelers"]) == 2

    admin = await db_client.get(
        f"{API}/admin/bookings", params={"tour_id": tour.id}, headers=catalog.headers
    )
    assert admin.json()["total"] == 1
    assert (
        await db_client.post(f"{API}/bookings/{held['id']}/cancel", headers=user)
    ).status_code == status.HTTP_409_CONFLICT


async def test_late_payment_confirms_only_if_seats_remain(
    db_client: AsyncClient,
    catalog: Catalog,
    sign_in_user: SignInUser,
    db_connection: AsyncConnection,
) -> None:
    tour = await create_tour(catalog, capacity=1)
    first = bearer(await sign_in_user())
    second = bearer(await sign_in_user())

    held = (await reserve(db_client, first, tour.departure_id)).json()
    url = (await pay(db_client, first, held["id"])).json()["redirect_url"]
    await expire(db_connection, held["id"])
    assert (await complete(db_client, url)).json()["status"] == "confirmed"

    tour = await create_tour(catalog, capacity=1)
    held = (await reserve(db_client, first, tour.departure_id)).json()
    url = (await pay(db_client, first, held["id"])).json()["redirect_url"]
    await expire(db_connection, held["id"])
    assert (await reserve(db_client, second, tour.departure_id)).status_code == 201

    late = await complete(db_client, url)
    assert late.status_code == status.HTTP_409_CONFLICT
    assert "refund" in late.json()["detail"]
    final = (await db_client.get(f"{API}/bookings/{held['id']}", headers=first)).json()
    assert final["status"] == "expired"


async def test_no_prepayment_confirms_without_payment_page(
    db_client: AsyncClient, catalog: Catalog, sign_in_user: SignInUser
) -> None:
    operator = (await catalog.operator())["id"]
    tour = Tour(await catalog.tour(operator, prepayment_percent=0, departures=[departure(10)]))
    user = bearer(await sign_in_user())
    held = (await reserve(db_client, user, tour.departure_id)).json()

    started = (await pay(db_client, user, held["id"])).json()

    assert started["redirect_url"] is None
    assert started["booking"]["status"] == "confirmed"


@pytest.mark.parametrize(
    "extra",
    [
        {"phone": None},
        {"birth_date": (date.today() - timedelta(days=365 * 5)).isoformat()},
        {"birth_date": (date.today() + timedelta(days=30)).isoformat()},
    ],
    ids=["contact-without-phone-or-email", "too-young", "born-after-start"],
)
async def test_invalid_travelers_are_rejected(
    db_client: AsyncClient, catalog: Catalog, sign_in_user: SignInUser, extra: dict[str, Any]
) -> None:
    tour = await create_tour(catalog, min_age=10)
    user = bearer(await sign_in_user())

    response = await reserve(db_client, user, tour.departure_id, **extra)

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.text


async def test_unavailable_departures_cannot_be_booked(
    db_client: AsyncClient, catalog: Catalog, sign_in_user: SignInUser
) -> None:
    draft = await create_tour(catalog, is_published=False)
    user = bearer(await sign_in_user())

    assert (await reserve(db_client, user, draft.departure_id)).status_code == 404
    anonymous = await db_client.post(
        f"{API}/bookings", json={"departure_id": draft.departure_id, "travelers": [traveler()]}
    )
    assert anonymous.status_code == status.HTTP_401_UNAUTHORIZED


async def test_other_users_cannot_touch_booking(
    db_client: AsyncClient, catalog: Catalog, sign_in_user: SignInUser
) -> None:
    tour = await create_tour(catalog)
    owner = bearer(await sign_in_user())
    stranger = bearer(await sign_in_user())
    held = (await reserve(db_client, owner, tour.departure_id)).json()

    for method, path in (
        ("GET", f"/bookings/{held['id']}"),
        ("POST", f"/bookings/{held['id']}/pay"),
        ("POST", f"/bookings/{held['id']}/cancel"),
    ):
        response = await db_client.request(method, f"{API}{path}", headers=stranger)
        assert response.status_code == status.HTTP_404_NOT_FOUND, path
    assert (await db_client.get(f"{API}/admin/bookings", headers=stranger)).status_code == 403


async def test_booked_departures_are_protected_from_edits(
    db_client: AsyncClient, catalog: Catalog, sign_in_user: SignInUser
) -> None:
    tour = await create_tour(catalog, capacity=3)
    user = bearer(await sign_in_user())
    await reserve(db_client, user, tour.departure_id, seats=2)
    current = (await db_client.get(f"{API}/admin/tours/{tour.id}", headers=catalog.headers)).json()
    booked = current["departures"][0]

    def body(departures: list[dict[str, Any]]) -> dict[str, Any]:
        return tour_body(tour.operator_id, departures=departures)

    shrink = await db_client.put(
        f"{API}/admin/tours/{tour.id}",
        json=body(
            [
                {
                    "id": booked["id"],
                    "start_at": booked["start_at"],
                    "end_at": booked["end_at"],
                    "capacity": 1,
                }
            ]
        ),
        headers=catalog.headers,
    )
    remove = await db_client.put(
        f"{API}/admin/tours/{tour.id}", json=body([departure(20)]), headers=catalog.headers
    )
    delete = await db_client.delete(f"{API}/admin/tours/{tour.id}", headers=catalog.headers)

    assert shrink.status_code == status.HTTP_409_CONFLICT
    assert remove.status_code == status.HTTP_409_CONFLICT
    assert delete.status_code == status.HTTP_409_CONFLICT


async def test_persons_filter_counts_free_seats(
    db_client: AsyncClient, catalog: Catalog, sign_in_user: SignInUser
) -> None:
    tour = await create_tour(catalog, capacity=3)
    user = bearer(await sign_in_user())
    await reserve(db_client, user, tour.departure_id, seats=2)

    def found(persons: int) -> Any:
        return db_client.get(
            f"{API}/tour-operators/{tour.operator_id}/tours", params={"persons": persons}
        )

    assert (await found(1)).json()["total"] == 1
    assert (await found(2)).json()["total"] == 0
