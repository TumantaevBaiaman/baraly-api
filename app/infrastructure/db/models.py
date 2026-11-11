from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    false,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.auth import AuthProvider
from app.domain.booking import BookingStatus, Gender
from app.domain.company import CompanyRole
from app.domain.tour import ActivityLevel, ComfortLevel, Meal, TourModerationStatus
from app.domain.user import UserRole
from app.infrastructure.db.base import Base


def _enum(enum_class: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=20,
        values_callable=lambda items: [item.value for item in items],
    )


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    # Телефон не подтверждается, поэтому без UNIQUE: иначе чужой номер можно занять.
    phone: Mapped[str | None] = mapped_column(String(16))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True)
    role: Mapped[UserRole] = mapped_column(_enum(UserRole, "user_role"), default=UserRole.USER)
    # Пароль только у сотрудников платформы: вход в админку. Хеш Argon2.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    failed_login_count: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuthIdentityModel(Base):
    __tablename__ = "auth_identities"
    __table_args__ = (UniqueConstraint("user_id", "provider"),)

    provider: Mapped[AuthProvider] = mapped_column(
        _enum(AuthProvider, "auth_provider"),
        primary_key=True,
    )
    subject: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RefreshTokenModel(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TagModel(Base):
    __tablename__ = "tags"

    slug: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))


class LanguageModel(Base):
    __tablename__ = "languages"

    code: Mapped[str] = mapped_column(String(2), primary_key=True)
    name: Mapped[str] = mapped_column(String(50))


class LocationModel(Base):
    __tablename__ = "locations"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)


class TourOperatorModel(Base):
    __tablename__ = "tour_operators"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    tagline: Mapped[str | None] = mapped_column(String(300))
    address: Mapped[str | None] = mapped_column(String(300))
    phone: Mapped[str | None] = mapped_column(String(32))
    instagram: Mapped[str | None] = mapped_column(String(100))
    # О компании и контакты для путешественников.
    description: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(String(320))
    website: Mapped[str | None] = mapped_column(String(300))
    whatsapp: Mapped[str | None] = mapped_column(String(32))
    telegram: Mapped[str | None] = mapped_column(String(100))
    # Реквизиты для проверки компании платформой.
    legal_name: Mapped[str | None] = mapped_column(String(300))
    license_number: Mapped[str | None] = mapped_column(String(100))
    logo_url: Mapped[str | None] = mapped_column(Text)
    cover_url: Mapped[str | None] = mapped_column(Text)
    # Отключённая админом компания и её туры не видны на сайте.
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())
    # Проверенная компания публикует туры без модерации.
    is_verified: Mapped[bool] = mapped_column(default=False, server_default=false())
    verification_note: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TourModel(Base):
    __tablename__ = "tours"
    __table_args__ = (
        CheckConstraint("price >= 0", name="price_not_negative"),
        CheckConstraint("duration_days > 0", name="duration_positive"),
        CheckConstraint(
            "duration_nights >= 0 AND duration_nights <= duration_days", name="nights_range"
        ),
        CheckConstraint("min_age >= 0 AND min_age <= max_age", name="age_range"),
        CheckConstraint("prepayment_percent BETWEEN 0 AND 100", name="prepayment_range"),
        CheckConstraint("free_cancellation_hours >= 0", name="cancellation_not_negative"),
        Index("ix_tours_is_published_price", "is_published", "price"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    # RESTRICT: оператора с турами удалить нельзя.
    operator_id: Mapped[UUID] = mapped_column(
        ForeignKey("tour_operators.id", ondelete="RESTRICT"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    duration_days: Mapped[int] = mapped_column(SmallInteger)
    activity: Mapped[ActivityLevel] = mapped_column(_enum(ActivityLevel, "activity_level"))
    comfort: Mapped[ComfortLevel] = mapped_column(_enum(ComfortLevel, "comfort_level"))
    min_age: Mapped[int] = mapped_column(SmallInteger)
    max_age: Mapped[int] = mapped_column(SmallInteger)
    prepayment_percent: Mapped[int] = mapped_column(SmallInteger)
    duration_nights: Mapped[int | None] = mapped_column(SmallInteger)
    short_description: Mapped[str | None] = mapped_column(String(500))
    free_cancellation_hours: Mapped[int | None] = mapped_column(SmallInteger)
    accommodation: Mapped[str | None] = mapped_column(Text)
    cancellation_policy: Mapped[str | None] = mapped_column(Text)
    booking_info: Mapped[str | None] = mapped_column(Text)
    # Для туров без точных дат: "Any day", "Weekends".
    schedule_note: Mapped[str | None] = mapped_column(String(100))
    brochure_url: Mapped[str | None] = mapped_column(Text)
    start_location_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("locations.id", ondelete="RESTRICT")
    )
    finish_location_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("locations.id", ondelete="RESTRICT")
    )
    is_published: Mapped[bool] = mapped_column()
    # Скрыт админом: компания не может снова показать тур сама.
    is_blocked: Mapped[bool] = mapped_column(default=False, server_default=false())
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    moderation_status: Mapped[TourModerationStatus] = mapped_column(
        _enum(TourModerationStatus, "moderation_status")
    )
    moderation_comment: Mapped[str | None] = mapped_column(Text)
    moderated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    moderated_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TourTagModel(Base):
    __tablename__ = "tour_tags"

    tour_id: Mapped[UUID] = mapped_column(
        ForeignKey("tours.id", ondelete="CASCADE"), primary_key=True
    )
    tag_slug: Mapped[str] = mapped_column(
        ForeignKey("tags.slug", ondelete="RESTRICT"), primary_key=True, index=True
    )


class TourLanguageModel(Base):
    __tablename__ = "tour_languages"

    tour_id: Mapped[UUID] = mapped_column(
        ForeignKey("tours.id", ondelete="CASCADE"), primary_key=True
    )
    language_code: Mapped[str] = mapped_column(
        ForeignKey("languages.code", ondelete="RESTRICT"), primary_key=True, index=True
    )


class TourImageModel(Base):
    __tablename__ = "tour_images"

    tour_id: Mapped[UUID] = mapped_column(
        ForeignKey("tours.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    url: Mapped[str] = mapped_column(Text)


class TourInclusionModel(Base):
    __tablename__ = "tour_inclusions"

    tour_id: Mapped[UUID] = mapped_column(
        ForeignKey("tours.id", ondelete="CASCADE"), primary_key=True
    )
    is_included: Mapped[bool] = mapped_column(primary_key=True)
    position: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    text: Mapped[str] = mapped_column(String(500))


class TourDayModel(Base):
    __tablename__ = "tour_days"
    __table_args__ = (
        UniqueConstraint("tour_id", "day_number"),
        CheckConstraint("day_number > 0", name="day_number_positive"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tour_id: Mapped[UUID] = mapped_column(ForeignKey("tours.id", ondelete="CASCADE"))
    day_number: Mapped[int] = mapped_column(SmallInteger)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    location_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("locations.id", ondelete="RESTRICT"), index=True
    )


class TourDayMealModel(Base):
    __tablename__ = "tour_day_meals"

    day_id: Mapped[UUID] = mapped_column(
        ForeignKey("tour_days.id", ondelete="CASCADE"), primary_key=True
    )
    meal: Mapped[Meal] = mapped_column(_enum(Meal, "meal"), primary_key=True)


class TourDayLandmarkModel(Base):
    __tablename__ = "tour_day_landmarks"

    day_id: Mapped[UUID] = mapped_column(
        ForeignKey("tour_days.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(300))


class TourDepartureModel(Base):
    __tablename__ = "tour_departures"
    __table_args__ = (
        CheckConstraint("end_at > start_at", name="ends_after_start"),
        CheckConstraint("capacity > 0", name="capacity_positive"),
        CheckConstraint("price >= 0", name="price_not_negative"),
        Index("ix_tour_departures_tour_id_start_at", "tour_id", "start_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tour_id: Mapped[UUID] = mapped_column(ForeignKey("tours.id", ondelete="CASCADE"))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    capacity: Mapped[int] = mapped_column(SmallInteger)
    price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))


class TourReviewModel(Base):
    __tablename__ = "tour_reviews"
    __table_args__ = (
        UniqueConstraint("tour_id", "user_id"),
        CheckConstraint("rating BETWEEN 1 AND 5", name="rating_range"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tour_id: Mapped[UUID] = mapped_column(ForeignKey("tours.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    rating: Mapped[int] = mapped_column(SmallInteger)
    text: Mapped[str] = mapped_column(Text)
    is_hidden: Mapped[bool] = mapped_column(default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FavoriteModel(Base):
    __tablename__ = "favorites"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    tour_id: Mapped[UUID] = mapped_column(
        ForeignKey("tours.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CompanyMemberModel(Base):
    __tablename__ = "company_members"

    operator_id: Mapped[UUID] = mapped_column(
        ForeignKey("tour_operators.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[CompanyRole] = mapped_column(_enum(CompanyRole, "company_role"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CompanyInvitationModel(Base):
    __tablename__ = "company_invitations"
    __table_args__ = (UniqueConstraint("operator_id", "email"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    operator_id: Mapped[UUID] = mapped_column(ForeignKey("tour_operators.id", ondelete="CASCADE"))
    email: Mapped[str] = mapped_column(String(320), index=True)
    role: Mapped[CompanyRole] = mapped_column(_enum(CompanyRole, "company_role"))
    invited_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OperatorReviewModel(Base):
    __tablename__ = "operator_reviews"
    __table_args__ = (
        UniqueConstraint("operator_id", "user_id"),
        CheckConstraint("rating BETWEEN 1 AND 5", name="rating_range"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    operator_id: Mapped[UUID] = mapped_column(ForeignKey("tour_operators.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    rating: Mapped[int] = mapped_column(SmallInteger)
    text: Mapped[str] = mapped_column(Text)
    is_hidden: Mapped[bool] = mapped_column(default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TourDayImageModel(Base):
    __tablename__ = "tour_day_images"

    day_id: Mapped[UUID] = mapped_column(
        ForeignKey("tour_days.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    url: Mapped[str] = mapped_column(Text)


class TourReviewPhotoModel(Base):
    __tablename__ = "tour_review_photos"

    review_id: Mapped[UUID] = mapped_column(
        ForeignKey("tour_reviews.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    url: Mapped[str] = mapped_column(Text)


class BookingModel(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("seats > 0", name="seats_positive"),
        CheckConstraint("total_price >= 0 AND prepayment_amount >= 0", name="prices_not_negative"),
        Index("ix_bookings_departure_id_status", "departure_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    # RESTRICT: выезд с бронями нельзя удалить вместе с туром.
    departure_id: Mapped[UUID] = mapped_column(
        ForeignKey("tour_departures.id", ondelete="RESTRICT")
    )
    seats: Mapped[int] = mapped_column(SmallInteger)
    # Цены фиксируются на момент брони: изменение цены тура их не меняет.
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    total_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    prepayment_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[BookingStatus] = mapped_column(_enum(BookingStatus, "booking_status"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payment_provider: Mapped[str | None] = mapped_column(String(30))
    payment_reference: Mapped[str | None] = mapped_column(String(200), unique=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BookingTravelerModel(Base):
    __tablename__ = "booking_travelers"

    booking_id: Mapped[UUID] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), primary_key=True
    )
    # 0 — контактное лицо.
    position: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    birth_date: Mapped[date] = mapped_column(Date)
    gender: Mapped[Gender] = mapped_column(_enum(Gender, "gender"))
    phone: Mapped[str | None] = mapped_column(String(16))
    email: Mapped[str | None] = mapped_column(String(320))
