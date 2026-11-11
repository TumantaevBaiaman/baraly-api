from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from app.domain.exceptions import DomainError
from app.domain.tour import (
    ActivityLevel,
    ComfortLevel,
    Departure,
    Meal,
    OperatorContent,
    ReviewContent,
    TourContent,
    TourDay,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def make_tour(**overrides: Any) -> TourContent:
    values: dict[str, Any] = {
        "operator_id": uuid4(),
        "title": "Son-Kul",
        "description": "Lake expedition",
        "price": Decimal("300.00"),
        "duration_days": 4,
        "activity": ActivityLevel.MODERATE,
        "comfort": ComfortLevel.HIGH,
        "min_age": 10,
        "max_age": 75,
        "prepayment_percent": 34,
    }
    return TourContent(**(values | overrides))


def test_valid_tour() -> None:
    assert make_tour().title == "Son-Kul"


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": "  "},
        {"price": Decimal("-1")},
        {"duration_days": 0},
        {"min_age": 80, "max_age": 10},
        {"min_age": -1},
        {"prepayment_percent": 101},
        {"free_cancellation_hours": -1},
        {"days": [TourDay(day_number=1, title="a"), TourDay(day_number=1, title="b")]},
    ],
    ids=[
        "blank-title",
        "negative-price",
        "zero-duration",
        "inverted-ages",
        "negative-age",
        "prepayment-over-100",
        "negative-cancellation",
        "duplicate-days",
    ],
)
def test_invalid_tour(overrides: dict[str, Any]) -> None:
    with pytest.raises(DomainError):
        make_tour(**overrides)


def test_day_rejects_duplicate_meals_and_invalid_number() -> None:
    with pytest.raises(DomainError):
        TourDay(day_number=1, title="a", meals=[Meal.LUNCH, Meal.LUNCH])
    with pytest.raises(DomainError):
        TourDay(day_number=0, title="a")


def test_departure_rejects_invalid_dates_and_capacity() -> None:
    with pytest.raises(DomainError):
        Departure(start_at=NOW, end_at=NOW, capacity=5)
    with pytest.raises(DomainError):
        Departure(start_at=NOW, end_at=NOW + timedelta(days=1), capacity=0)


def test_operator_requires_name() -> None:
    with pytest.raises(DomainError):
        OperatorContent(name=" ")


@pytest.mark.parametrize(("rating", "text"), [(0, "ok"), (6, "ok"), (5, "  ")])
def test_invalid_review(rating: int, text: str) -> None:
    with pytest.raises(DomainError):
        ReviewContent(rating=rating, text=text)
