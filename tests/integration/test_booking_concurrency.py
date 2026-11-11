"""Одновременные покупки последних мест через настоящие параллельные соединения с БД.

Данные коммитятся, поэтому тест сам удаляет всё, что создал."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import delete, insert
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.domain.tour import ActivityLevel, ComfortLevel, TourModerationStatus
from app.infrastructure.db.models import (
    BookingModel,
    TourDepartureModel,
    TourModel,
    TourOperatorModel,
    UserModel,
)
from app.main import create_app

CAPACITY = 3
BUYERS = 10


@pytest.fixture
async def live_app() -> AsyncIterator[tuple[AsyncClient, list[str], UUID]]:
    try:
        settings = Settings()
    except ValidationError:
        pytest.skip("Нет настроек БД в .env")
    app = create_app(settings)
    engine = app.state.engine
    now = datetime.now(UTC)
    operator_id, tour_id, departure_id = uuid4(), uuid4(), uuid4()
    user_ids = [uuid4() for _ in range(BUYERS)]
    try:
        async with engine.begin() as connection:
            await connection.execute(
                insert(TourOperatorModel).values(
                    id=operator_id, name="Concurrency", created_at=now, updated_at=now
                )
            )
            await connection.execute(
                insert(TourModel).values(
                    id=tour_id,
                    operator_id=operator_id,
                    title="Concurrency",
                    description="d",
                    price=Decimal(100),
                    duration_days=2,
                    activity=ActivityLevel.EASY,
                    comfort=ComfortLevel.HIGH,
                    min_age=0,
                    max_age=99,
                    prepayment_percent=10,
                    is_published=True,
                    moderation_status=TourModerationStatus.APPROVED,
                    created_at=now,
                    updated_at=now,
                )
            )
            await connection.execute(
                insert(TourDepartureModel).values(
                    id=departure_id,
                    tour_id=tour_id,
                    start_at=now + timedelta(days=10),
                    end_at=now + timedelta(days=12),
                    capacity=CAPACITY,
                )
            )
            await connection.execute(
                insert(UserModel),
                [
                    {
                        "id": user_id,
                        "email": f"race-{user_id}@example.com",
                        "created_at": now,
                        "updated_at": now,
                    }
                    for user_id in user_ids
                ],
            )
    except (OSError, SQLAlchemyError):
        await engine.dispose()
        pytest.skip("PostgreSQL недоступен")

    tokens = [app.state.token_service.issue_access_token(user_id, now) for user_id in user_ids]
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, tokens, departure_id
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                delete(BookingModel).where(BookingModel.departure_id == departure_id)
            )
            await connection.execute(delete(TourModel).where(TourModel.id == tour_id))
            await connection.execute(
                delete(TourOperatorModel).where(TourOperatorModel.id == operator_id)
            )
            await connection.execute(delete(UserModel).where(UserModel.id.in_(user_ids)))
        await engine.dispose()


async def test_parallel_buyers_never_exceed_capacity(
    live_app: tuple[AsyncClient, list[str], UUID],
) -> None:
    client, tokens, departure_id = live_app
    body = {
        "departure_id": str(departure_id),
        "travelers": [
            {
                "first_name": "Race",
                "last_name": "Test",
                "birth_date": "1990-01-01",
                "gender": "male",
                "email": "race@example.com",
            }
        ],
    }

    responses = await asyncio.gather(
        *(
            client.post("/api/v1/bookings", json=body, headers={"Authorization": f"Bearer {token}"})
            for token in tokens
        )
    )

    codes = sorted(response.status_code for response in responses)
    assert codes.count(201) == CAPACITY, [r.text for r in responses]
    assert codes.count(409) == BUYERS - CAPACITY
