from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.application.dto import (
    BookingFilters,
    OperatorFilters,
    OperatorSort,
    PageParams,
    ReviewFilters,
    ReviewKind,
    ReviewSort,
    TourFilters,
    TourSort,
    UserFilters,
)
from app.domain.booking import BookingStatus, Gender, Traveler
from app.domain.company import CompanyRole
from app.domain.tour import (
    ActivityLevel,
    ComfortLevel,
    Departure,
    Meal,
    OperatorContent,
    ReviewContent,
    TourContent,
    TourDay,
    TourModerationStatus,
)
from app.domain.user import UserRole


class _Response(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PageResponse[T](_Response):
    items: list[T]
    total: int
    limit: int
    offset: int


class TagResponse(_Response):
    slug: str
    name: str


class LanguageResponse(_Response):
    code: str
    name: str


class LocationResponse(_Response):
    id: UUID
    name: str
    latitude: float | None
    longitude: float | None


class OperatorSummaryResponse(_Response):
    id: UUID
    name: str
    logo_url: str | None
    rating: float | None
    review_count: int


class OperatorResponse(OperatorSummaryResponse):
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
    is_active: bool
    is_verified: bool = Field(description="Компания прошла проверку платформы")


class DepartureResponse(_Response):
    id: UUID
    start_at: datetime
    end_at: datetime
    capacity: int
    price: float
    seats_taken: int
    seats_left: int


class TourCardResponse(_Response):
    id: UUID
    title: str
    cover_image_url: str | None
    tags: list[TagResponse]
    rating: float | None
    review_count: int
    duration_days: int
    duration_nights: int | None
    next_departure: DepartureResponse | None
    schedule_note: str | None
    price: float
    currency: str = "USD"
    operator: OperatorSummaryResponse
    is_published: bool
    is_blocked: bool
    moderation_status: TourModerationStatus
    is_expired: bool = Field(description="Все даты прошли, тура нет в каталоге")
    created_at: datetime
    updated_at: datetime


class TourDayResponse(_Response):
    day_number: int
    title: str
    description: str | None
    location: LocationResponse | None
    meals: list[Meal]
    landmarks: list[str]
    image_urls: list[str]


class TourResponse(TourCardResponse):
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
    start_location: LocationResponse | None
    finish_location: LocationResponse | None
    languages: list[LanguageResponse]
    image_urls: list[str]
    days: list[TourDayResponse]
    included: list[str]
    excluded: list[str]
    departures: list[DepartureResponse]


class ReviewResponse(_Response):
    id: UUID
    kind: ReviewKind = Field(description="tour: отзыв о туре; company: отзыв о компании")
    tour_id: UUID | None
    tour_title: str | None
    operator_id: UUID
    operator_name: str
    rating: int
    text: str
    photo_urls: list[str]
    is_hidden: bool
    author_first_name: str | None
    author_last_name: str | None
    author_avatar_url: str | None
    created_at: datetime
    updated_at: datetime


class AdminReviewResponse(ReviewResponse):
    user_id: UUID


class OperatorAdminResponse(OperatorResponse):
    # Реквизиты видят только платформа и сама компания.
    legal_name: str | None
    license_number: str | None
    tours_count: int
    published_tours_count: int
    members_count: int
    verification_note: str | None
    verified_at: datetime | None
    created_at: datetime


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PageQuery(_Request):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    def to_params(self) -> PageParams:
        return PageParams(limit=self.limit, offset=self.offset)


class TourSearchQuery(PageQuery):
    q: str | None = Field(default=None, max_length=200, description="Поиск по названию")
    location: str | None = Field(default=None, max_length=200)
    date_from: date | None = None
    date_to: date | None = None
    persons: int | None = Field(default=None, ge=1)
    duration_min: int | None = Field(default=None, ge=1)
    duration_max: int | None = Field(default=None, ge=1)
    price_min: Decimal | None = Field(default=None, ge=0)
    price_max: Decimal | None = Field(default=None, ge=0)
    age_from: int | None = Field(default=None, ge=0)
    age_to: int | None = Field(default=None, ge=0)
    tags: list[str] = []
    languages: list[str] = []
    activity: list[ActivityLevel] = []
    sort: TourSort = TourSort.POPULAR

    def to_filters(self, operator_id: UUID | None = None) -> TourFilters:
        return TourFilters(
            query=self.q,
            location=self.location,
            date_from=self.date_from,
            date_to=self.date_to,
            persons=self.persons,
            duration_min=self.duration_min,
            duration_max=self.duration_max,
            price_min=self.price_min,
            price_max=self.price_max,
            age_from=self.age_from,
            age_to=self.age_to,
            tags=self.tags,
            languages=self.languages,
            activity=self.activity,
            operator_id=operator_id,
            sort=self.sort,
        )


class AdminTourSearchQuery(TourSearchQuery):
    operator_id: UUID | None = None
    is_published: bool | None = None
    is_blocked: bool | None = Field(default=None, description="Скрыт админом")
    moderation_status: TourModerationStatus | None = Field(
        default=None, description="pending: очередь модерации"
    )
    is_expired: bool | None = None

    def to_admin_filters(self) -> TourFilters:
        return replace(
            self.to_filters(self.operator_id),
            is_published=self.is_published,
            is_blocked=self.is_blocked,
            moderation_status=self.moderation_status,
            is_expired=self.is_expired,
        )


class CompanyTourSearchQuery(TourSearchQuery):
    is_published: bool | None = None
    is_blocked: bool | None = None
    moderation_status: TourModerationStatus | None = None
    is_expired: bool | None = None

    def to_company_filters(self) -> TourFilters:
        return replace(
            self.to_filters(),
            is_published=self.is_published,
            is_blocked=self.is_blocked,
            moderation_status=self.moderation_status,
            is_expired=self.is_expired,
        )


class OperatorSearchQuery(PageQuery):
    q: str | None = Field(default=None, max_length=200, description="Поиск по названию")
    is_active: bool | None = None
    is_verified: bool | None = None
    sort: OperatorSort = OperatorSort.NAME

    def to_filters(self) -> OperatorFilters:
        return OperatorFilters(
            query=self.q, is_active=self.is_active, is_verified=self.is_verified, sort=self.sort
        )


class ReviewSearchQuery(PageQuery):
    kind: ReviewKind | None = None
    tour_id: UUID | None = None
    rating_min: int | None = Field(default=None, ge=1, le=5)
    rating_max: int | None = Field(default=None, ge=1, le=5)
    is_hidden: bool | None = None
    q: str | None = Field(default=None, max_length=200, description="Поиск по тексту")
    sort: ReviewSort = ReviewSort.NEWEST

    def to_filters(self) -> ReviewFilters:
        return ReviewFilters(
            kind=self.kind,
            tour_id=self.tour_id,
            rating_min=self.rating_min,
            rating_max=self.rating_max,
            is_hidden=self.is_hidden,
            query=self.q,
            sort=self.sort,
        )


class AdminReviewSearchQuery(ReviewSearchQuery):
    operator_id: UUID | None = None
    user_id: UUID | None = None

    def to_admin_filters(self) -> ReviewFilters:
        return replace(self.to_filters(), operator_id=self.operator_id, user_id=self.user_id)


class OperatorReviewsQuery(PageQuery):
    kind: ReviewKind | None = Field(default=None, description="Только один вид отзывов")


PageQueryDep = Annotated[PageQuery, Query()]
OperatorReviewsQueryDep = Annotated[OperatorReviewsQuery, Query()]
TourSearchQueryDep = Annotated[TourSearchQuery, Query()]
AdminTourSearchQueryDep = Annotated[AdminTourSearchQuery, Query()]
CompanyTourSearchQueryDep = Annotated[CompanyTourSearchQuery, Query()]
OperatorSearchQueryDep = Annotated[OperatorSearchQuery, Query()]
ReviewSearchQueryDep = Annotated[ReviewSearchQuery, Query()]
AdminReviewSearchQueryDep = Annotated[AdminReviewSearchQuery, Query()]


class TourDayRequest(_Request):
    day_number: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=300)
    description: str | None = None
    location_id: UUID | None = None
    meals: list[Meal] = []
    landmarks: list[Annotated[str, Field(min_length=1, max_length=300)]] = []
    image_urls: list[str] = Field(default=[], max_length=20, description="Фото мест этого дня")


class DepartureRequest(_Request):
    id: UUID | None = Field(default=None, description="Укажите, чтобы изменить существующий выезд")
    start_at: AwareDatetime
    end_at: AwareDatetime
    capacity: int = Field(ge=1, le=10000)
    price: Decimal | None = Field(
        default=None,
        ge=0,
        max_digits=10,
        decimal_places=2,
        description="Если отличается от цены тура",
    )


class TourBody(_Request):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1)
    price: Decimal = Field(ge=0, max_digits=10, decimal_places=2)
    duration_days: int = Field(ge=1, le=365)
    activity: ActivityLevel
    comfort: ComfortLevel
    min_age: int = Field(ge=0, le=120)
    max_age: int = Field(ge=0, le=120)
    prepayment_percent: int = Field(ge=0, le=100)
    duration_nights: int | None = Field(default=None, ge=0, le=365)
    short_description: str | None = Field(default=None, max_length=500, description="Подзаголовок")
    free_cancellation_hours: int | None = Field(default=None, ge=0, le=8760)
    accommodation: str | None = None
    cancellation_policy: str | None = None
    booking_info: str | None = None
    schedule_note: str | None = Field(default=None, max_length=100)
    brochure_url: str | None = None
    start_location_id: UUID | None = None
    finish_location_id: UUID | None = None
    is_published: bool = False
    tag_slugs: list[str] = []
    language_codes: list[str] = []
    image_urls: list[str] = Field(default=[], description="Первое изображение становится обложкой")
    days: list[TourDayRequest] = []
    included: list[Annotated[str, Field(min_length=1, max_length=500)]] = []
    excluded: list[Annotated[str, Field(min_length=1, max_length=500)]] = []
    departures: list[DepartureRequest] = []

    def build(self, operator_id: UUID) -> TourContent:
        values = self.model_dump(exclude={"days", "departures", "operator_id"})
        return TourContent(
            **values,
            operator_id=operator_id,
            days=[TourDay(**day.model_dump()) for day in self.days],
            departures=[
                Departure(
                    id=item.id or uuid4(),
                    start_at=item.start_at,
                    end_at=item.end_at,
                    capacity=item.capacity,
                    price=item.price,
                )
                for item in self.departures
            ],
        )


class TourRequest(TourBody):
    operator_id: UUID

    def to_content(self) -> TourContent:
        return self.build(self.operator_id)


class CompanyTourRequest(TourBody):
    """Компания создаёт туры только для себя, поэтому operator_id берётся из пути."""


class OperatorRequest(_Request):
    name: str = Field(min_length=1, max_length=200)
    tagline: str | None = Field(default=None, max_length=300)
    address: str | None = Field(default=None, max_length=300)
    phone: str | None = Field(default=None, max_length=32)
    instagram: str | None = Field(default=None, max_length=100)
    logo_url: str | None = None
    cover_url: str | None = None
    description: str | None = Field(default=None, max_length=5000, description="О компании")
    email: str | None = Field(default=None, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    website: str | None = Field(default=None, max_length=300)
    whatsapp: str | None = Field(default=None, max_length=32)
    telegram: str | None = Field(default=None, max_length=100)
    legal_name: str | None = Field(default=None, max_length=300, description="Юридическое название")
    license_number: str | None = Field(
        default=None, max_length=100, description="Номер лицензии туроператора"
    )

    def to_content(self) -> OperatorContent:
        # Только поля профиля: у наследников есть служебные поля вроде owner_email.
        return OperatorContent(**self.model_dump(include=set(OperatorRequest.model_fields)))


class TagRequest(_Request):
    slug: str = Field(pattern=r"^[a-z0-9-]+$", max_length=50)
    name: str = Field(min_length=1, max_length=100)


class LocationRequest(_Request):
    name: str = Field(min_length=1, max_length=200)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class ReviewRequest(_Request):
    rating: int = Field(ge=1, le=5)
    text: str = Field(min_length=1, max_length=5000)
    photo_urls: list[str] = Field(
        default=[], max_length=10, description="Ссылки из POST /users/me/images, только для туров"
    )

    def to_content(self) -> ReviewContent:
        return ReviewContent(rating=self.rating, text=self.text, photo_urls=self.photo_urls)


class MemberResponse(_Response):
    user_id: UUID
    email: str
    first_name: str | None
    last_name: str | None
    avatar_url: str | None
    role: CompanyRole
    joined_at: datetime


class InvitationResponse(_Response):
    id: UUID
    email: str
    role: CompanyRole
    created_at: datetime


class InviteResultResponse(_Response):
    status: str = Field(
        description="member: доступ выдан существующему аккаунту; "
        "created: создан аккаунт с паролем; "
        "invited: доступ появится при первом входе через Google или Apple"
    )
    email: str
    role: CompanyRole


class CompanyMembershipResponse(_Response):
    operator_id: UUID
    name: str
    logo_url: str | None
    role: CompanyRole


class InviteMemberRequest(_Request):
    email: str = Field(max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: CompanyRole
    password: str | None = Field(
        default=None,
        min_length=10,
        max_length=128,
        description="Только для нового email: создаётся аккаунт для входа в кабинет по паролю",
    )
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)


class ChangeRoleRequest(_Request):
    role: CompanyRole


class ImageUploadResponse(BaseModel):
    url: str


_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class CreateOperatorRequest(OperatorRequest):
    owner_email: str | None = Field(
        default=None,
        max_length=320,
        pattern=_EMAIL_PATTERN,
        description="Владелец компании: получит доступ сразу или при первом входе",
    )
    owner_password: str | None = Field(
        default=None,
        min_length=10,
        max_length=128,
        description="Для нового email: владелец входит в кабинет по этому паролю",
    )

    def to_content(self) -> OperatorContent:
        return OperatorContent(**self.model_dump(include=set(OperatorRequest.model_fields)))


class OperatorStatusRequest(_Request):
    is_active: bool


class TourModerationRequest(_Request):
    is_blocked: bool
    reason: str | None = Field(default=None, max_length=1000, description="Видна компании")


class ReviewModerationRequest(_Request):
    is_hidden: bool


class ReviewPhotoResponse(_Response):
    review_id: UUID
    url: str
    rating: int
    author_first_name: str | None
    created_at: datetime


class DeparturesQuery(_Request):
    date_from: date | None = Field(default=None, description="По умолчанию сегодня")
    date_to: date | None = Field(default=None, description="По умолчанию через год")


DeparturesQueryDep = Annotated[DeparturesQuery, Query()]


class TourApprovalRequest(_Request):
    status: Literal[TourModerationStatus.APPROVED, TourModerationStatus.REJECTED]
    comment: str | None = Field(
        default=None, max_length=1000, description="Обязателен при отклонении, его видит компания"
    )


class OperatorVerificationRequest(_Request):
    is_verified: bool
    note: str | None = Field(
        default=None, max_length=1000, description="Какую проверку прошла компания"
    )


class UserRoleRequest(_Request):
    role: UserRole


class CreateStaffRequest(_Request):
    email: str = Field(max_length=320, pattern=_EMAIL_PATTERN)
    role: Literal[UserRole.ADMIN, UserRole.MODERATOR]
    password: str = Field(min_length=10, max_length=128)
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)


class SetPasswordRequest(_Request):
    password: str = Field(min_length=10, max_length=128)


class ChangePasswordRequest(_Request):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


class AdminUserResponse(_Response):
    id: UUID
    email: str
    first_name: str | None
    last_name: str | None
    phone: str | None
    avatar_url: str | None
    role: UserRole
    is_active: bool
    created_at: datetime


class UserSearchQuery(PageQuery):
    q: str | None = Field(default=None, max_length=200, description="Email или имя")
    role: UserRole | None = None

    def to_filters(self) -> UserFilters:
        return UserFilters(query=self.q, role=self.role)


UserSearchQueryDep = Annotated[UserSearchQuery, Query()]


class TravelerRequest(_Request):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    birth_date: date
    gender: Gender
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=320, pattern=_EMAIL_PATTERN)

    def to_traveler(self) -> Traveler:
        return Traveler(**self.model_dump())


class BookingRequest(_Request):
    departure_id: UUID
    travelers: list[TravelerRequest] = Field(
        min_length=1, max_length=20, description="Первый путешественник — контактное лицо"
    )


class TravelerResponse(_Response):
    first_name: str
    last_name: str
    birth_date: date
    gender: Gender
    phone: str | None
    email: str | None


class BookingResponse(_Response):
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
    unit_price: float
    total_price: float
    prepayment_amount: float = Field(description="Оплачивается онлайн, остальное организатору")
    currency: str = "USD"
    travelers: list[TravelerResponse]
    expires_at: datetime = Field(description="До этого времени места удерживаются без оплаты")
    paid_at: datetime | None
    cancelled_at: datetime | None
    created_at: datetime


class CompanyBookingResponse(BookingResponse):
    user_email: str


class PaymentStartResponse(BaseModel):
    booking: BookingResponse
    redirect_url: str | None = Field(
        description="Страница оплаты; null, если предоплата не нужна и бронь уже подтверждена"
    )


class BookingSearchQuery(PageQuery):
    status: BookingStatus | None = None
    tour_id: UUID | None = None
    departure_id: UUID | None = None

    def to_filters(self) -> BookingFilters:
        return BookingFilters(
            status=self.status, tour_id=self.tour_id, departure_id=self.departure_id
        )


class AdminBookingSearchQuery(BookingSearchQuery):
    operator_id: UUID | None = None
    user_id: UUID | None = None

    def to_admin_filters(self) -> BookingFilters:
        return replace(self.to_filters(), operator_id=self.operator_id, user_id=self.user_id)


BookingSearchQueryDep = Annotated[BookingSearchQuery, Query()]
AdminBookingSearchQueryDep = Annotated[AdminBookingSearchQuery, Query()]


class LanguageRequest(_Request):
    code: str = Field(pattern=r"^[a-z]{2}$", description="Код ISO 639-1, например ky")
    name: str = Field(min_length=1, max_length=50)
