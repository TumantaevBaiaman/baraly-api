from datetime import UTC, datetime

import pytest

from app.domain.exceptions import DomainError
from app.domain.user import User, normalize_phone

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def make_user() -> User:
    return User.register(email="  Traveler@Example.COM ", first_name=" Aigul ", now=NOW)


def test_register_normalizes_fields() -> None:
    user = make_user()

    assert user.email == "traveler@example.com"
    assert user.first_name == "Aigul"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+996 502800202", "+996502800202"),
        ("+996 (502) 80-02-02", "+996502800202"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_phone(raw: str | None, expected: str | None) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["0502800202", "+0123456789", "+99650abc", "+123"])
def test_normalize_phone_rejects_invalid(raw: str) -> None:
    with pytest.raises(DomainError):
        normalize_phone(raw)


def test_update_profile_changes_only_given_fields() -> None:
    user = make_user()
    later = datetime(2026, 2, 1, tzinfo=UTC)

    user.update_profile({"phone": "+996 502800202"}, later)

    assert user.phone == "+996502800202"
    assert user.first_name == "Aigul"
    assert user.updated_at == later


def test_update_profile_clears_field_with_none() -> None:
    user = make_user()

    user.update_profile({"first_name": None}, NOW)

    assert user.first_name is None
