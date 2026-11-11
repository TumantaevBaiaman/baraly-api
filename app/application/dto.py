"""Модели чтения для каталога: собираются запросами и не содержат бизнес-правил."""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from app.domain.booking import BookingStatus, Gender
from app.domain.company import CompanyRole
from app.domain.tour import ActivityLevel, ComfortLevel, Meal, TourModerationStatus
from app.domain.user import UserRole


class TourSort(StrEnum):
    POPULAR = "popular"
    RATING = "rating"
    PRICE_ASC = "price_asc"
    PRICE_DESC = "price_desc"
    NEWEST = "newest"
    # Для очереди модерации: дольше всех ждущие заявки первыми.
    OLDEST = "oldest"


@dataclass(frozen=True, kw_only=True)
class PageParams:
    limit: int = 20
    offset: int = 0


@dataclass(frozen=True, kw_only=True)
class Page[T]:
    items: list[T]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, kw_only=True)
class TourFilters:
    query: str | None = None
    location: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    persons: int | None = None
    duration_min: int | None = None
    duration_max: int | None = None
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    age_from: int | None = None
    age_to: int | None = None
    tags: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    activity: list[ActivityLevel] = field(default_factory=list)
    operator_id: UUID | None = None
    favorite_of: UUID | None = None
    # Только для админки и кабинета компании.
    is_published: bool | None = None
    is_blocked: bool | None = None
    moderation_status: TourModerationStatus | None = None
    is_expired: bool | None = None
    sort: TourSort = TourSort.POPULAR


@dataclass(frozen=True, kw_only=True)
class TagView:
    slug: str
    name: str


@dataclass(frozen=True, kw_only=True)
class LanguageView:
    code: str
    name: str


@dataclass(frozen=True, kw_only=True)
class LocationView:
    id: UUID
    name: str
    latitude: float | None
    longitude: float | None


@dataclass(frozen=True, kw_only=True)
class OperatorSummary:
    id: UUID
    name: str
    logo_url: str | None
    rating: float | None
    review_count: int


@dataclass(frozen=True, kw_only=True)
class OperatorDetail(OperatorSummary):
    tagline: str | None
    address: str | None
    phone: str | None
    instagram: str | None
    cover_url: str | None
    description: str | None
    email: str | None
    website: str | None
    whatsapp: str | None
    telegram: str | None
    legal_name: str | None
    license_number: str | None
    is_active: bool
    is_verified: bool


@dataclass(frozen=True, kw_only=True)
class OperatorAdminView(OperatorDetail):
    tours_count: int
    published_tours_count: int
    members_count: int
    verification_note: str | None
    verified_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, kw_only=True)
class DepartureView:
    id: UUID
    start_at: datetime
    end_at: datetime
    capacity: int
    # Итоговая цена за человека: цена выезда или цена тура.
    price: Decimal
    # Оплаченные места и неистёкшие удержания.
    seats_taken: int

    @property
    def seats_left(self) -> int:
        return max(self.capacity - self.seats_taken, 0)


@dataclass(frozen=True, kw_only=True)
class TourCard:
    id: UUID
    title: str
    cover_image_url: str | None
    tags: list[TagView]
    rating: float | None
    review_count: int
    duration_days: int
    duration_nights: int | None
    next_departure: DepartureView | None
    schedule_note: str | None
    price: Decimal
    operator: OperatorSummary
    is_published: bool
    is_blocked: bool
    moderation_status: TourModerationStatus
    # Все даты выезда в прошлом: тур не показывается в каталоге.
    is_expired: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, kw_only=True)
class TourDayView:
    day_number: int
    title: str
    description: str | None
    location: LocationView | None
    meals: list[Meal]
    landmarks: list[str]
    image_urls: list[str]


@dataclass(frozen=True, kw_only=True)
class TourDetail(TourCard):
    short_description: str | None
    description: str
    activity: ActivityLevel
    comfort: ComfortLevel
    min_age: int
    max_age: int
    prepayment_percent: int
    free_cancellation_hours: int | None
    accommodation: str | None
    cancellation_policy: str | None
    booking_info: str | None
    brochure_url: str | None
    blocked_reason: str | None
    moderation_comment: str | None
    moderated_at: datetime | None
    start_location: LocationView | None
    finish_location: LocationView | None
    languages: list[LanguageView]
    image_urls: list[str]
    days: list[TourDayView]
    included: list[str]
    excluded: list[str]
    departures: list[DepartureView]


class ReviewKind(StrEnum):
    TOUR = "tour"
    COMPANY = "company"


class ReviewSort(StrEnum):
    NEWEST = "newest"
    OLDEST = "oldest"
    RATING_DESC = "rating_desc"
    RATING_ASC = "rating_asc"


@dataclass(frozen=True, kw_only=True)
class ReviewFilters:
    kind: ReviewKind | None = None
    tour_id: UUID | None = None
    operator_id: UUID | None = None
    user_id: UUID | None = None
    rating_min: int | None = None
    rating_max: int | None = None
    is_hidden: bool | None = None
    query: str | None = None
    sort: ReviewSort = ReviewSort.NEWEST


@dataclass(frozen=True, kw_only=True)
class ReviewView:
    id: UUID
    kind: ReviewKind
    tour_id: UUID | None
    tour_title: str | None
    operator_id: UUID
    operator_name: str
    user_id: UUID
    rating: int
    text: str
    photo_urls: list[str]
    is_hidden: bool
    author_first_name: str | None
    author_last_name: str | None
    author_avatar_url: str | None
    created_at: datetime
    updated_at: datetime


class OperatorSort(StrEnum):
    NAME = "name"
    NEWEST = "newest"
    RATING = "rating"
    TOURS = "tours"


@dataclass(frozen=True, kw_only=True)
class OperatorFilters:
    query: str | None = None
    is_active: bool | None = None
    is_verified: bool | None = None
    sort: OperatorSort = OperatorSort.NAME


@dataclass(frozen=True, kw_only=True)
class MemberView:
    user_id: UUID
    email: str
    first_name: str | None
    last_name: str | None
    avatar_url: str | None
    role: CompanyRole
    joined_at: datetime


@dataclass(frozen=True, kw_only=True)
class InvitationView:
    id: UUID
    email: str
    role: CompanyRole
    created_at: datetime


@dataclass(frozen=True, kw_only=True)
class InviteResult:
    status: Literal["member", "created", "invited"]
    email: str
    role: CompanyRole


@dataclass(frozen=True, kw_only=True)
class CompanyMembershipView:
    operator_id: UUID
    name: str
    logo_url: str | None
    role: CompanyRole


@dataclass(frozen=True, kw_only=True)
class ReviewPhotoView:
    review_id: UUID
    url: str
    rating: int
    author_first_name: str | None
    created_at: datetime


@dataclass(frozen=True, kw_only=True)
class UserFilters:
    query: str | None = None
    role: UserRole | None = None


@dataclass(frozen=True, kw_only=True)
class TravelerView:
    first_name: str
    last_name: str
    birth_date: date
    gender: Gender
    phone: str | None
    email: str | None


@dataclass(frozen=True, kw_only=True)
class BookingView:
    id: UUID
    status: BookingStatus
    tour_id: UUID
    tour_title: str
    operator_id: UUID
    operator_name: str
    departure_id: UUID
    start_at: datetime
    end_at: datetime
    seats: int
    unit_price: Decimal
    total_price: Decimal
    prepayment_amount: Decimal
    user_id: UUID
    user_email: str
    travelers: list[TravelerView]
    expires_at: datetime
    paid_at: datetime | None
    cancelled_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, kw_only=True)
class BookingFilters:
    user_id: UUID | None = None
    operator_id: UUID | None = None
    tour_id: UUID | None = None
    departure_id: UUID | None = None
    status: BookingStatus | None = None
