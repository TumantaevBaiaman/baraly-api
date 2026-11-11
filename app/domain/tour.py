from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from app.domain.exceptions import DomainError

MAX_REVIEW_PHOTOS = 10


class ActivityLevel(StrEnum):
    FOR_ALL = "for_all"
    EASY = "easy"
    MODERATE = "moderate"
    INTENSIVE = "intensive"
    EXTREME = "extreme"


class ComfortLevel(StrEnum):
    BASIC = "basic"
    STANDARD = "standard"
    HIGH = "high"


class TourModerationStatus(StrEnum):
    # Ждёт проверки: туры непроверенных компаний после создания и каждого изменения.
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Meal(StrEnum):
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"


@dataclass(frozen=True, kw_only=True)
class TourDay:
    day_number: int
    title: str
    description: str | None = None
    location_id: UUID | None = None
    meals: list[Meal] = field(default_factory=list)
    landmarks: list[str] = field(default_factory=list)
    # Фото мест этого дня программы.
    image_urls: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.day_number < 1:
            raise DomainError("Day number must be positive")
        if len(set(self.meals)) != len(self.meals):
            raise DomainError(f"Day {self.day_number} has duplicate meals")


@dataclass(frozen=True, kw_only=True)
class Departure:
    start_at: datetime
    end_at: datetime
    capacity: int
    id: UUID = field(default_factory=uuid4)
    # Своя цена выезда (сезон, праздники); без неё действует цена тура.
    price: Decimal | None = None

    def __post_init__(self) -> None:
        if self.end_at <= self.start_at:
            raise DomainError("Departure must end after it starts")
        if self.capacity < 1:
            raise DomainError("Departure capacity must be positive")
        if self.price is not None and self.price < 0:
            raise DomainError("Departure price cannot be negative")


@dataclass(frozen=True, kw_only=True)
class TourContent:
    """Всё, что администратор задаёт при создании или изменении тура. Цена в USD."""

    operator_id: UUID
    title: str
    description: str
    price: Decimal
    duration_days: int
    activity: ActivityLevel
    comfort: ComfortLevel
    min_age: int
    max_age: int
    prepayment_percent: int
    duration_nights: int | None = None
    short_description: str | None = None
    free_cancellation_hours: int | None = None
    accommodation: str | None = None
    cancellation_policy: str | None = None
    booking_info: str | None = None
    schedule_note: str | None = None
    brochure_url: str | None = None
    start_location_id: UUID | None = None
    finish_location_id: UUID | None = None
    is_published: bool = False
    tag_slugs: list[str] = field(default_factory=list)
    language_codes: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    days: list[TourDay] = field(default_factory=list)
    included: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    departures: list[Departure] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise DomainError("Tour title is required")
        if self.price < 0:
            raise DomainError("Price cannot be negative")
        if self.duration_days < 1:
            raise DomainError("Duration must be at least one day")
        if self.duration_nights is not None and not 0 <= self.duration_nights <= self.duration_days:
            raise DomainError("Nights must be between 0 and the number of days")
        if not 0 <= self.min_age <= self.max_age:
            raise DomainError("Age range is invalid")
        if not 0 <= self.prepayment_percent <= 100:
            raise DomainError("Prepayment must be between 0 and 100 percent")
        if self.free_cancellation_hours is not None and self.free_cancellation_hours < 0:
            raise DomainError("Free cancellation period cannot be negative")
        numbers = [day.day_number for day in self.days]
        if len(set(numbers)) != len(numbers):
            raise DomainError("Day numbers must be unique")


@dataclass(frozen=True, kw_only=True)
class OperatorContent:
    name: str
    tagline: str | None = None
    address: str | None = None
    phone: str | None = None
    instagram: str | None = None
    logo_url: str | None = None
    cover_url: str | None = None
    description: str | None = None
    email: str | None = None
    website: str | None = None
    whatsapp: str | None = None
    telegram: str | None = None
    legal_name: str | None = None
    license_number: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise DomainError("Operator name is required")


@dataclass(frozen=True, kw_only=True)
class ReviewContent:
    rating: int
    text: str
    photo_urls: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not 1 <= self.rating <= 5:
            raise DomainError("Rating must be between 1 and 5")
        if not self.text.strip():
            raise DomainError("Review text is required")
        if len(self.photo_urls) > MAX_REVIEW_PHOTOS:
            raise DomainError(f"A review can have at most {MAX_REVIEW_PHOTOS} photos")
