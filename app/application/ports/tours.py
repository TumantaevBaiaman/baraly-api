from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from app.application.dto import (
    DepartureView,
    LanguageView,
    LocationView,
    OperatorAdminView,
    OperatorDetail,
    OperatorFilters,
    Page,
    PageParams,
    ReviewFilters,
    ReviewPhotoView,
    ReviewView,
    TagView,
    TourCard,
    TourDetail,
    TourFilters,
)
from app.domain.tour import (
    OperatorContent,
    ReviewContent,
    TourContent,
    TourModerationStatus,
)


class CatalogReader(Protocol):
    async def search_tours(
        self, filters: TourFilters, page: PageParams, *, include_unpublished: bool = False
    ) -> Page[TourCard]: ...

    async def get_tour(
        self, tour_id: UUID, *, include_unpublished: bool = False
    ) -> TourDetail | None: ...

    async def is_tour_visible(self, tour_id: UUID) -> bool:
        """Тур опубликован, не скрыт админом, а его компания активна."""
        ...

    async def get_tour_price(self, tour_id: UUID) -> Decimal | None:
        """Цена видимого тура или None, если тура нет на сайте."""
        ...

    async def list_departures(
        self,
        tour_id: UUID,
        tour_price: Decimal,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[DepartureView]: ...

    async def list_operators(self, page: PageParams) -> Page[OperatorDetail]: ...

    async def get_operator(
        self, operator_id: UUID, *, include_inactive: bool = False
    ) -> OperatorDetail | None: ...

    async def search_operators_admin(
        self, filters: OperatorFilters, page: PageParams
    ) -> Page[OperatorAdminView]: ...

    async def get_operator_admin(self, operator_id: UUID) -> OperatorAdminView | None: ...

    async def list_tags(self) -> list[TagView]: ...

    async def list_languages(self) -> list[LanguageView]: ...

    async def list_locations(self) -> list[LocationView]: ...


class TourRepository(Protocol):
    async def create(
        self,
        tour_id: UUID,
        content: TourContent,
        moderation_status: TourModerationStatus,
        now: datetime,
    ) -> None: ...

    async def update(
        self,
        tour_id: UUID,
        content: TourContent,
        moderation_status: TourModerationStatus,
        now: datetime,
    ) -> bool:
        """Изменение содержимого сбрасывает прошлое решение модератора."""
        ...

    async def set_moderation(
        self,
        tour_id: UUID,
        status: TourModerationStatus,
        comment: str | None,
        moderator_id: UUID,
        now: datetime,
    ) -> bool: ...

    async def set_blocked(
        self, tour_id: UUID, is_blocked: bool, reason: str | None, now: datetime
    ) -> bool: ...

    async def delete(self, tour_id: UUID) -> bool: ...


class OperatorRepository(Protocol):
    async def create(self, operator_id: UUID, content: OperatorContent, now: datetime) -> None: ...

    async def update(self, operator_id: UUID, content: OperatorContent, now: datetime) -> bool: ...

    async def set_active(self, operator_id: UUID, is_active: bool, now: datetime) -> bool: ...

    async def set_verification(
        self, operator_id: UUID, is_verified: bool, note: str | None, now: datetime
    ) -> bool: ...

    async def delete(self, operator_id: UUID) -> bool: ...


class ReferenceRepository(Protocol):
    async def add_tag(self, slug: str, name: str) -> None: ...

    async def delete_tag(self, slug: str) -> bool: ...

    async def add_language(self, code: str, name: str) -> None: ...

    async def delete_language(self, code: str) -> bool: ...

    async def add_location(
        self, location_id: UUID, name: str, latitude: float | None, longitude: float | None
    ) -> None: ...

    async def delete_location(self, location_id: UUID) -> bool: ...


class ReviewRepository(Protocol):
    async def upsert_tour_review(
        self, tour_id: UUID, user_id: UUID, content: ReviewContent, now: datetime
    ) -> UUID: ...

    async def upsert_company_review(
        self, operator_id: UUID, user_id: UUID, content: ReviewContent, now: datetime
    ) -> UUID: ...

    async def delete_own_tour_review(self, tour_id: UUID, user_id: UUID) -> bool: ...

    async def delete_own_company_review(self, operator_id: UUID, user_id: UUID) -> bool: ...

    async def search(
        self, filters: ReviewFilters, page: PageParams, *, visible_only: bool
    ) -> Page[ReviewView]:
        """visible_only: без скрытых отзывов и без отзывов о скрытых турах и компаниях."""
        ...

    async def get(self, review_id: UUID) -> ReviewView | None: ...

    async def list_tour_review_photos(
        self, tour_id: UUID, page: PageParams
    ) -> Page[ReviewPhotoView]: ...

    async def set_hidden(self, review_id: UUID, is_hidden: bool) -> bool: ...

    async def delete(self, review_id: UUID) -> bool: ...


class FavoriteRepository(Protocol):
    async def add(self, user_id: UUID, tour_id: UUID, now: datetime) -> None: ...

    async def remove(self, user_id: UUID, tour_id: UUID) -> None: ...

    async def list_tour_ids(self, user_id: UUID) -> list[UUID]: ...
