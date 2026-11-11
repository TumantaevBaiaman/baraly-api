from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.dto import (
    LanguageView,
    LocationView,
    OperatorAdminView,
    OperatorFilters,
    Page,
    PageParams,
    ReviewFilters,
    ReviewView,
    TagView,
    TourCard,
    TourDetail,
    TourFilters,
)
from app.application.ports.tours import (
    CatalogReader,
    OperatorRepository,
    ReferenceRepository,
    ReviewRepository,
    TourRepository,
)
from app.application.ports.unit_of_work import UnitOfWork
from app.domain.exceptions import DomainError, NotFoundError
from app.domain.tour import OperatorContent, TourContent, TourModerationStatus
from app.domain.user import User


class ManageTours:
    def __init__(
        self, tours: TourRepository, reader: CatalogReader, unit_of_work: UnitOfWork
    ) -> None:
        self._tours = tours
        self._reader = reader
        self._unit_of_work = unit_of_work

    async def search(self, filters: TourFilters, page: PageParams) -> Page[TourCard]:
        return await self._reader.search_tours(filters, page, include_unpublished=True)

    async def get(self, tour_id: UUID) -> TourDetail:
        tour = await self._reader.get_tour(tour_id, include_unpublished=True)
        if tour is None:
            raise NotFoundError("Tour not found")
        return tour

    async def create(
        self,
        content: TourContent,
        moderation_status: TourModerationStatus = TourModerationStatus.APPROVED,
    ) -> TourDetail:
        """Туры от админа одобрены сразу; компании передают свой статус."""
        tour_id = uuid4()
        await self._tours.create(tour_id, content, moderation_status, datetime.now(UTC))
        await self._unit_of_work.commit()
        return await self.get(tour_id)

    async def update(
        self,
        tour_id: UUID,
        content: TourContent,
        moderation_status: TourModerationStatus = TourModerationStatus.APPROVED,
    ) -> TourDetail:
        if not await self._tours.update(tour_id, content, moderation_status, datetime.now(UTC)):
            raise NotFoundError("Tour not found")
        await self._unit_of_work.commit()
        return await self.get(tour_id)

    async def review(
        self,
        tour_id: UUID,
        status: TourModerationStatus,
        comment: str | None,
        moderator: User,
    ) -> TourDetail:
        """Решение модератора: одобрить или отклонить с причиной для компании."""
        if status is TourModerationStatus.PENDING:
            raise DomainError("Choose approved or rejected")
        if status is TourModerationStatus.REJECTED and not (comment and comment.strip()):
            raise DomainError("Explain to the company why the tour is rejected")
        saved = await self._tours.set_moderation(
            tour_id, status, comment, moderator.id, datetime.now(UTC)
        )
        if not saved:
            raise NotFoundError("Tour not found")
        await self._unit_of_work.commit()
        return await self.get(tour_id)

    async def set_blocked(self, tour_id: UUID, is_blocked: bool, reason: str | None) -> TourDetail:
        """Скрытие админом не зависит от публикации: компания не может его отменить."""
        if not await self._tours.set_blocked(tour_id, is_blocked, reason, datetime.now(UTC)):
            raise NotFoundError("Tour not found")
        await self._unit_of_work.commit()
        return await self.get(tour_id)

    async def delete(self, tour_id: UUID) -> None:
        if not await self._tours.delete(tour_id):
            raise NotFoundError("Tour not found")
        await self._unit_of_work.commit()


class ManageOperators:
    def __init__(
        self, operators: OperatorRepository, reader: CatalogReader, unit_of_work: UnitOfWork
    ) -> None:
        self._operators = operators
        self._reader = reader
        self._unit_of_work = unit_of_work

    async def search(self, filters: OperatorFilters, page: PageParams) -> Page[OperatorAdminView]:
        return await self._reader.search_operators_admin(filters, page)

    async def get(self, operator_id: UUID) -> OperatorAdminView:
        operator = await self._reader.get_operator_admin(operator_id)
        if operator is None:
            raise NotFoundError("Tour operator not found")
        return operator

    async def create(self, content: OperatorContent) -> OperatorAdminView:
        operator_id = uuid4()
        await self._operators.create(operator_id, content, datetime.now(UTC))
        await self._unit_of_work.commit()
        return await self.get(operator_id)

    async def update(self, operator_id: UUID, content: OperatorContent) -> OperatorAdminView:
        if not await self._operators.update(operator_id, content, datetime.now(UTC)):
            raise NotFoundError("Tour operator not found")
        await self._unit_of_work.commit()
        return await self.get(operator_id)

    async def set_verification(
        self, operator_id: UUID, is_verified: bool, note: str | None
    ) -> OperatorAdminView:
        """Проверенная компания публикует новые туры без модерации."""
        if not await self._operators.set_verification(
            operator_id, is_verified, note, datetime.now(UTC)
        ):
            raise NotFoundError("Tour operator not found")
        await self._unit_of_work.commit()
        return await self.get(operator_id)

    async def set_active(self, operator_id: UUID, is_active: bool) -> OperatorAdminView:
        if not await self._operators.set_active(operator_id, is_active, datetime.now(UTC)):
            raise NotFoundError("Tour operator not found")
        await self._unit_of_work.commit()
        return await self.get(operator_id)

    async def delete(self, operator_id: UUID) -> None:
        if not await self._operators.delete(operator_id):
            raise NotFoundError("Tour operator not found")
        await self._unit_of_work.commit()


class ManageReferenceData:
    def __init__(self, reference: ReferenceRepository, unit_of_work: UnitOfWork) -> None:
        self._reference = reference
        self._unit_of_work = unit_of_work

    async def add_tag(self, slug: str, name: str) -> TagView:
        await self._reference.add_tag(slug, name)
        await self._unit_of_work.commit()
        return TagView(slug=slug, name=name)

    async def delete_tag(self, slug: str) -> None:
        if not await self._reference.delete_tag(slug):
            raise NotFoundError("Tag not found")
        await self._unit_of_work.commit()

    async def add_language(self, code: str, name: str) -> LanguageView:
        await self._reference.add_language(code, name)
        await self._unit_of_work.commit()
        return LanguageView(code=code, name=name)

    async def delete_language(self, code: str) -> None:
        if not await self._reference.delete_language(code):
            raise NotFoundError("Language not found")
        await self._unit_of_work.commit()

    async def add_location(
        self, name: str, latitude: float | None, longitude: float | None
    ) -> LocationView:
        location_id = uuid4()
        await self._reference.add_location(location_id, name, latitude, longitude)
        await self._unit_of_work.commit()
        return LocationView(id=location_id, name=name, latitude=latitude, longitude=longitude)

    async def delete_location(self, location_id: UUID) -> None:
        if not await self._reference.delete_location(location_id):
            raise NotFoundError("Location not found")
        await self._unit_of_work.commit()


class ModerateReviews:
    def __init__(self, reviews: ReviewRepository, unit_of_work: UnitOfWork) -> None:
        self._reviews = reviews
        self._unit_of_work = unit_of_work

    async def search(self, filters: ReviewFilters, page: PageParams) -> Page[ReviewView]:
        return await self._reviews.search(filters, page, visible_only=False)

    async def get(self, review_id: UUID) -> ReviewView:
        review = await self._reviews.get(review_id)
        if review is None:
            raise NotFoundError("Review not found")
        return review

    async def set_hidden(self, review_id: UUID, is_hidden: bool) -> ReviewView:
        if not await self._reviews.set_hidden(review_id, is_hidden):
            raise NotFoundError("Review not found")
        await self._unit_of_work.commit()
        return await self.get(review_id)

    async def delete(self, review_id: UUID) -> None:
        if not await self._reviews.delete(review_id):
            raise NotFoundError("Review not found")
        await self._unit_of_work.commit()
