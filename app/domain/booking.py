from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from app.domain.exceptions import DomainError
from app.domain.user import normalize_email, normalize_name, normalize_phone

MAX_TRAVELERS_PER_BOOKING = 20
_CENT = Decimal("0.01")


class BookingStatus(StrEnum):
    # Места удерживаются до expires_at, пока пользователь оплачивает.
    PENDING_PAYMENT = "pending_payment"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class Gender(StrEnum):
    MALE = "male"
    FEMALE = "female"


def effective_status(status: BookingStatus, expires_at: datetime, now: datetime) -> BookingStatus:
    """Неоплаченная бронь после expires_at считается истёкшей, даже если строка не обновлена."""
    if status is BookingStatus.PENDING_PAYMENT and now >= expires_at:
        return BookingStatus.EXPIRED
    return status


def age_on(birth_date: date, day: date) -> int:
    years = day.year - birth_date.year
    if (day.month, day.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years


@dataclass(frozen=True, kw_only=True)
class Traveler:
    """Данные путешественника должны совпадать с паспортом."""

    first_name: str
    last_name: str
    birth_date: date
    gender: Gender
    phone: str | None = None
    email: str | None = None

    def normalized(self) -> "Traveler":
        first_name = normalize_name(self.first_name)
        last_name = normalize_name(self.last_name)
        if first_name is None or last_name is None:
            raise DomainError("Traveler name and surname are required")
        return Traveler(
            first_name=first_name,
            last_name=last_name,
            birth_date=self.birth_date,
            gender=self.gender,
            phone=normalize_phone(self.phone),
            email=normalize_email(self.email) if self.email and self.email.strip() else None,
        )


@dataclass(frozen=True, kw_only=True)
class BookingPrice:
    unit_price: Decimal
    total_price: Decimal
    # Платится онлайн при бронировании, остальное напрямую организатору.
    prepayment_amount: Decimal


def calculate_price(unit_price: Decimal, seats: int, prepayment_percent: int) -> BookingPrice:
    total = (unit_price * seats).quantize(_CENT, rounding=ROUND_HALF_UP)
    prepayment = (total * prepayment_percent / 100).quantize(_CENT, rounding=ROUND_HALF_UP)
    return BookingPrice(unit_price=unit_price, total_price=total, prepayment_amount=prepayment)


def validate_travelers(
    travelers: list[Traveler], *, start_date: date, min_age: int, max_age: int
) -> list[Traveler]:
    """Первый путешественник — контактное лицо по брони."""
    if not travelers:
        raise DomainError("Add at least one traveler")
    if len(travelers) > MAX_TRAVELERS_PER_BOOKING:
        raise DomainError(f"At most {MAX_TRAVELERS_PER_BOOKING} travelers per booking")
    result = [traveler.normalized() for traveler in travelers]
    if not (result[0].phone or result[0].email):
        raise DomainError("The first traveler is the contact person and needs a phone or email")
    for traveler in result:
        if traveler.birth_date > start_date:
            raise DomainError("Birth date cannot be after the tour starts")
        age = age_on(traveler.birth_date, start_date)
        if not min_age <= age <= max_age:
            raise DomainError(
                f"{traveler.first_name} {traveler.last_name} is {age}; "
                f"this tour is for ages {min_age}-{max_age}"
            )
    return result
