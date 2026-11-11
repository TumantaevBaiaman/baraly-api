from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from app.application.dto import (
    DepartureView,
    LanguageView,
    LocationView,
    OperatorDetail,
    Page,
    PageParams,
    ReviewFilters,
    ReviewKind,
    ReviewPhotoView,
    ReviewView,
    TagView,
    TourCard,
    TourDetail,
    TourFilters,
)
from app.application.ports.tours import CatalogReader, ReviewRepository
from app.domain.exceptions import DomainError, NotFoundError

_MAX_CALENDAR_DAYS = 400


def _start_of(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


class BrowseCatalog:
    def __init__(self, reader: CatalogReader, reviews: ReviewRepository) -> None:
        self._reader = reader
        self._reviews = reviews

    async def search_tours(self, filters: TourFilters, page: PageParams) -> Page[TourCard]:
        return await self._reader.search_tours(filters, page)

    async def get_tour(self, tour_id: UUID) -> TourDetail:
        tour = await self._reader.get_tour(tour_id)
        if tour is None:
            raise NotFoundError("Tour not found")
        return tour

    async def list_tour_reviews(self, tour_id: UUID, page: PageParams) -> Page[ReviewView]:
        if not await self._reader.is_tour_visible(tour_id):
            raise NotFoundError("Tour not found")
        filters = ReviewFilters(tour_id=tour_id, kind=ReviewKind.TOUR)
        return await self._reviews.search(filters, page, visible_only=True)

    async def list_departures(
        self, tour_id: UUID, date_from: date | None, date_to: date | None
    ) -> list[DepartureView]:
        """Даты выездов для календаря; по умолчанию на год вперёд от сегодня."""
        price = await self._reader.get_tour_price(tour_id)
        if price is None:
            raise NotFoundError("Tour not found")
        today = datetime.now(UTC).date()
        start = date_from or today
        end = date_to or start + timedelta(days=365)
        if end < start:
            raise DomainError("date_to must not be earlier than date_from")
        if (end - start).days > _MAX_CALENDAR_DAYS:
            raise DomainError(f"Period must be at most {_MAX_CALENDAR_DAYS} days")
        return await self._reader.list_departures(
            tour_id,
            price,
            start=max(_start_of(start), datetime.now(UTC)),
            end=_start_of(end) + timedelta(days=1),
        )

    async def list_tour_review_photos(
        self, tour_id: UUID, page: PageParams
    ) -> Page[ReviewPhotoView]:
        if not await self._reader.is_tour_visible(tour_id):
            raise NotFoundError("Tour not found")
        return await self._reviews.list_tour_review_photos(tour_id, page)

    async def list_operators(self, page: PageParams) -> Page[OperatorDetail]:
        return await self._reader.list_operators(page)

    async def get_operator(self, operator_id: UUID) -> OperatorDetail:
        operator = await self._reader.get_operator(operator_id)
        if operator is None:
            raise NotFoundError("Tour operator not found")
        return operator

    async def list_operator_tours(
        self, operator_id: UUID, filters: TourFilters, page: PageParams
    ) -> Page[TourCard]:
        await self.get_operator(operator_id)
        return await self._reader.search_tours(replace(filters, operator_id=operator_id), page)

    async def list_operator_reviews(
        self, operator_id: UUID, page: PageParams, kind: ReviewKind | None = None
    ) -> Page[ReviewView]:
        """Отзывы о компании и о её турах, как в рейтинге компании."""
        await self.get_operator(operator_id)
        filters = ReviewFilters(operator_id=operator_id, kind=kind)
        return await self._reviews.search(filters, page, visible_only=True)

    async def list_tags(self) -> list[TagView]:
        return await self._reader.list_tags()

    async def list_languages(self) -> list[LanguageView]:
        return await self._reader.list_languages()

    async def list_locations(self) -> list[LocationView]:
        return await self._reader.list_locations()
