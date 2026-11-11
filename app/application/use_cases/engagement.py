from datetime import UTC, datetime
from uuid import UUID

from app.application.dto import Page, PageParams, ReviewFilters, ReviewView, TourCard, TourFilters
from app.application.ports.storage import ImageStorage
from app.application.ports.tours import CatalogReader, FavoriteRepository, ReviewRepository
from app.application.ports.unit_of_work import UnitOfWork
from app.application.use_cases.images import user_images_prefix
from app.domain.exceptions import DomainError, NotFoundError
from app.domain.tour import ReviewContent


class ManageFavorites:
    def __init__(
        self, favorites: FavoriteRepository, reader: CatalogReader, unit_of_work: UnitOfWork
    ) -> None:
        self._favorites = favorites
        self._reader = reader
        self._unit_of_work = unit_of_work

    async def list_tours(self, user_id: UUID, page: PageParams) -> Page[TourCard]:
        return await self._reader.search_tours(TourFilters(favorite_of=user_id), page)

    async def list_tour_ids(self, user_id: UUID) -> list[UUID]:
        return await self._favorites.list_tour_ids(user_id)

    async def add(self, user_id: UUID, tour_id: UUID) -> None:
        if not await self._reader.is_tour_visible(tour_id):
            raise NotFoundError("Tour not found")
        await self._favorites.add(user_id, tour_id, datetime.now(UTC))
        await self._unit_of_work.commit()

    async def remove(self, user_id: UUID, tour_id: UUID) -> None:
        await self._favorites.remove(user_id, tour_id)
        await self._unit_of_work.commit()


class ManageOwnReviews:
    def __init__(
        self,
        reviews: ReviewRepository,
        reader: CatalogReader,
        unit_of_work: UnitOfWork,
        storage: ImageStorage,
    ) -> None:
        self._reviews = reviews
        self._reader = reader
        self._unit_of_work = unit_of_work
        self._storage = storage

    async def save_tour_review(
        self, tour_id: UUID, user_id: UUID, content: ReviewContent
    ) -> ReviewView:
        if not await self._reader.is_tour_visible(tour_id):
            raise NotFoundError("Tour not found")
        # Только свои загруженные фото: чужие ссылки могли бы следить за читателями.
        own_prefix = self._storage.public_url(user_images_prefix(user_id))
        if any(not url.startswith(own_prefix) for url in content.photo_urls):
            raise DomainError("Upload review photos via POST /api/v1/users/me/images")
        review_id = await self._reviews.upsert_tour_review(
            tour_id, user_id, content, datetime.now(UTC)
        )
        return await self._commit_and_get(review_id)

    async def save_company_review(
        self, operator_id: UUID, user_id: UUID, content: ReviewContent
    ) -> ReviewView:
        if await self._reader.get_operator(operator_id) is None:
            raise NotFoundError("Tour operator not found")
        if content.photo_urls:
            raise DomainError("Photos are supported only in tour reviews")
        review_id = await self._reviews.upsert_company_review(
            operator_id, user_id, content, datetime.now(UTC)
        )
        return await self._commit_and_get(review_id)

    async def delete_tour_review(self, tour_id: UUID, user_id: UUID) -> None:
        if not await self._reviews.delete_own_tour_review(tour_id, user_id):
            raise NotFoundError("Review not found")
        await self._unit_of_work.commit()

    async def delete_company_review(self, operator_id: UUID, user_id: UUID) -> None:
        if not await self._reviews.delete_own_company_review(operator_id, user_id):
            raise NotFoundError("Review not found")
        await self._unit_of_work.commit()

    async def list_mine(self, user_id: UUID, page: PageParams) -> Page[ReviewView]:
        return await self._reviews.search(ReviewFilters(user_id=user_id), page, visible_only=False)

    async def _commit_and_get(self, review_id: UUID) -> ReviewView:
        await self._unit_of_work.commit()
        review = await self._reviews.get(review_id)
        if review is None:
            raise NotFoundError("Review not found")
        return review
