from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Select,
    String,
    Uuid,
    and_,
    cast,
    delete,
    func,
    insert,
    literal_column,
    null,
    select,
    union_all,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dto import (
    Page,
    PageParams,
    ReviewFilters,
    ReviewKind,
    ReviewPhotoView,
    ReviewSort,
    ReviewView,
)
from app.domain.exceptions import ConflictError
from app.domain.tour import OperatorContent, ReviewContent, TourContent, TourModerationStatus
from app.infrastructure.db.catalog_reader import seats_taken_expr, visible_tour_condition
from app.infrastructure.db.errors import conflict_on_integrity_error
from app.infrastructure.db.models import (
    FavoriteModel,
    LanguageModel,
    LocationModel,
    OperatorReviewModel,
    TagModel,
    TourDayImageModel,
    TourDayLandmarkModel,
    TourDayMealModel,
    TourDayModel,
    TourDepartureModel,
    TourImageModel,
    TourInclusionModel,
    TourLanguageModel,
    TourModel,
    TourOperatorModel,
    TourReviewModel,
    TourReviewPhotoModel,
    TourTagModel,
    UserModel,
)

_INVALID_TOUR_REFERENCES = (
    "Tour has an unknown operator, tag, language or location, or duplicate values"
)


def _tour_values(content: TourContent) -> dict[str, Any]:
    return {
        "operator_id": content.operator_id,
        "title": content.title.strip(),
        "description": content.description,
        "price": content.price,
        "duration_days": content.duration_days,
        "activity": content.activity,
        "comfort": content.comfort,
        "min_age": content.min_age,
        "max_age": content.max_age,
        "prepayment_percent": content.prepayment_percent,
        "duration_nights": content.duration_nights,
        "short_description": content.short_description,
        "free_cancellation_hours": content.free_cancellation_hours,
        "accommodation": content.accommodation,
        "cancellation_policy": content.cancellation_policy,
        "booking_info": content.booking_info,
        "schedule_note": content.schedule_note,
        "brochure_url": content.brochure_url,
        "start_location_id": content.start_location_id,
        "finish_location_id": content.finish_location_id,
        "is_published": content.is_published,
    }


class SqlAlchemyTourRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        tour_id: UUID,
        content: TourContent,
        moderation_status: TourModerationStatus,
        now: datetime,
    ) -> None:
        with conflict_on_integrity_error(_INVALID_TOUR_REFERENCES):
            await self._session.execute(
                insert(TourModel).values(
                    id=tour_id,
                    created_at=now,
                    updated_at=now,
                    moderation_status=moderation_status,
                    **_tour_values(content),
                )
            )
            await self._insert_children(tour_id, content)
            await self._sync_departures(tour_id, content)

    async def update(
        self,
        tour_id: UUID,
        content: TourContent,
        moderation_status: TourModerationStatus,
        now: datetime,
    ) -> bool:
        with conflict_on_integrity_error(_INVALID_TOUR_REFERENCES):
            updated = await self._session.scalar(
                update(TourModel)
                .where(TourModel.id == tour_id)
                .values(
                    updated_at=now,
                    moderation_status=moderation_status,
                    moderation_comment=None,
                    moderated_at=None,
                    moderated_by=None,
                    **_tour_values(content),
                )
                .returning(TourModel.id)
            )
            if updated is None:
                return False
            for model in (
                TourTagModel,
                TourLanguageModel,
                TourImageModel,
                TourInclusionModel,
                TourDayModel,
            ):
                await self._session.execute(delete(model).where(model.tour_id == tour_id))
            await self._insert_children(tour_id, content)
            await self._sync_departures(tour_id, content)
        return True

    async def set_moderation(
        self,
        tour_id: UUID,
        status: TourModerationStatus,
        comment: str | None,
        moderator_id: UUID,
        now: datetime,
    ) -> bool:
        updated = await self._session.scalar(
            update(TourModel)
            .where(TourModel.id == tour_id)
            .values(
                moderation_status=status,
                moderation_comment=comment,
                moderated_at=now,
                moderated_by=moderator_id,
            )
            .returning(TourModel.id)
        )
        return updated is not None

    async def set_blocked(
        self, tour_id: UUID, is_blocked: bool, reason: str | None, now: datetime
    ) -> bool:
        updated = await self._session.scalar(
            update(TourModel)
            .where(TourModel.id == tour_id)
            .values(
                is_blocked=is_blocked,
                blocked_reason=reason if is_blocked else None,
                updated_at=now,
            )
            .returning(TourModel.id)
        )
        return updated is not None

    async def delete(self, tour_id: UUID) -> bool:
        with conflict_on_integrity_error("Tour has bookings; unpublish it instead of deleting"):
            deleted = await self._session.scalar(
                delete(TourModel).where(TourModel.id == tour_id).returning(TourModel.id)
            )
        return deleted is not None

    async def _insert_children(self, tour_id: UUID, content: TourContent) -> None:
        await self._insert(
            TourTagModel, [{"tour_id": tour_id, "tag_slug": slug} for slug in content.tag_slugs]
        )
        await self._insert(
            TourLanguageModel,
            [{"tour_id": tour_id, "language_code": code} for code in content.language_codes],
        )
        await self._insert(
            TourImageModel,
            [
                {"tour_id": tour_id, "position": position, "url": url}
                for position, url in enumerate(content.image_urls)
            ],
        )
        await self._insert(
            TourInclusionModel,
            [
                {"tour_id": tour_id, "is_included": is_included, "position": position, "text": text}
                for is_included, items in ((True, content.included), (False, content.excluded))
                for position, text in enumerate(items)
            ],
        )
        for day in content.days:
            day_id = uuid4()
            await self._session.execute(
                insert(TourDayModel).values(
                    id=day_id,
                    tour_id=tour_id,
                    day_number=day.day_number,
                    title=day.title,
                    description=day.description,
                    location_id=day.location_id,
                )
            )
            await self._insert(
                TourDayMealModel, [{"day_id": day_id, "meal": meal} for meal in day.meals]
            )
            await self._insert(
                TourDayLandmarkModel,
                [
                    {"day_id": day_id, "position": position, "name": name}
                    for position, name in enumerate(day.landmarks)
                ],
            )
            await self._insert(
                TourDayImageModel,
                [
                    {"day_id": day_id, "position": position, "url": url}
                    for position, url in enumerate(day.image_urls)
                ],
            )

    async def _sync_departures(self, tour_id: UUID, content: TourContent) -> None:
        # Идентификаторы выездов сохраняются: на них ссылаются брони.
        keep = [departure.id for departure in content.departures]
        with conflict_on_integrity_error("A removed departure has bookings and cannot be deleted"):
            await self._session.execute(
                delete(TourDepartureModel).where(
                    TourDepartureModel.tour_id == tour_id, TourDepartureModel.id.not_in(keep)
                )
            )
        # Блокировка выездов: параллельная бронь не займёт место, пока меняется вместимость.
        rows = await self._session.execute(
            select(TourDepartureModel.id, seats_taken_expr(TourDepartureModel.id))
            .where(TourDepartureModel.tour_id == tour_id)
            .with_for_update(of=TourDepartureModel)
        )
        taken: dict[UUID, int] = {row[0]: row[1] for row in rows}
        existing = set(taken)
        for departure in content.departures:
            if departure.id in taken and departure.capacity < taken[departure.id]:
                raise ConflictError(
                    f"Departure on {departure.start_at:%Y-%m-%d} already has "
                    f"{taken[departure.id]} booked seats"
                )
        for departure in content.departures:
            values = {
                "start_at": departure.start_at,
                "end_at": departure.end_at,
                "capacity": departure.capacity,
                "price": departure.price,
            }
            if departure.id in existing:
                await self._session.execute(
                    update(TourDepartureModel)
                    .where(TourDepartureModel.id == departure.id)
                    .values(**values)
                )
            else:
                await self._session.execute(
                    insert(TourDepartureModel).values(id=departure.id, tour_id=tour_id, **values)
                )

    async def _insert(self, model: type[Any], rows: list[dict[str, Any]]) -> None:
        if rows:
            await self._session.execute(insert(model), rows)


class SqlAlchemyOperatorRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _values(content: OperatorContent) -> dict[str, Any]:
        return {
            "name": content.name.strip(),
            "tagline": content.tagline,
            "address": content.address,
            "phone": content.phone,
            "instagram": content.instagram,
            "logo_url": content.logo_url,
            "cover_url": content.cover_url,
            "description": content.description,
            "email": content.email,
            "website": content.website,
            "whatsapp": content.whatsapp,
            "telegram": content.telegram,
            "legal_name": content.legal_name,
            "license_number": content.license_number,
        }

    async def create(self, operator_id: UUID, content: OperatorContent, now: datetime) -> None:
        await self._session.execute(
            insert(TourOperatorModel).values(
                id=operator_id, created_at=now, updated_at=now, **self._values(content)
            )
        )

    async def update(self, operator_id: UUID, content: OperatorContent, now: datetime) -> bool:
        updated = await self._session.scalar(
            update(TourOperatorModel)
            .where(TourOperatorModel.id == operator_id)
            .values(updated_at=now, **self._values(content))
            .returning(TourOperatorModel.id)
        )
        return updated is not None

    async def set_verification(
        self, operator_id: UUID, is_verified: bool, note: str | None, now: datetime
    ) -> bool:
        updated = await self._session.scalar(
            update(TourOperatorModel)
            .where(TourOperatorModel.id == operator_id)
            .values(
                is_verified=is_verified,
                verification_note=note if is_verified else None,
                verified_at=now if is_verified else None,
                updated_at=now,
            )
            .returning(TourOperatorModel.id)
        )
        return updated is not None

    async def set_active(self, operator_id: UUID, is_active: bool, now: datetime) -> bool:
        updated = await self._session.scalar(
            update(TourOperatorModel)
            .where(TourOperatorModel.id == operator_id)
            .values(is_active=is_active, updated_at=now)
            .returning(TourOperatorModel.id)
        )
        return updated is not None

    async def delete(self, operator_id: UUID) -> bool:
        with conflict_on_integrity_error("Tour operator still has tours"):
            deleted = await self._session.scalar(
                delete(TourOperatorModel)
                .where(TourOperatorModel.id == operator_id)
                .returning(TourOperatorModel.id)
            )
        return deleted is not None


class SqlAlchemyReferenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_tag(self, slug: str, name: str) -> None:
        with conflict_on_integrity_error("Tag already exists"):
            await self._session.execute(insert(TagModel).values(slug=slug, name=name))

    async def delete_tag(self, slug: str) -> bool:
        with conflict_on_integrity_error("Tag is used by tours"):
            deleted = await self._session.scalar(
                delete(TagModel).where(TagModel.slug == slug).returning(TagModel.slug)
            )
        return deleted is not None

    async def add_language(self, code: str, name: str) -> None:
        with conflict_on_integrity_error("Language already exists"):
            await self._session.execute(insert(LanguageModel).values(code=code, name=name))

    async def delete_language(self, code: str) -> bool:
        with conflict_on_integrity_error("Language is used by tours"):
            deleted = await self._session.scalar(
                delete(LanguageModel)
                .where(LanguageModel.code == code)
                .returning(LanguageModel.code)
            )
        return deleted is not None

    async def add_location(
        self, location_id: UUID, name: str, latitude: float | None, longitude: float | None
    ) -> None:
        with conflict_on_integrity_error("Location already exists"):
            await self._session.execute(
                insert(LocationModel).values(
                    id=location_id, name=name, latitude=latitude, longitude=longitude
                )
            )

    async def delete_location(self, location_id: UUID) -> bool:
        with conflict_on_integrity_error("Location is used by tours"):
            deleted = await self._session.scalar(
                delete(LocationModel)
                .where(LocationModel.id == location_id)
                .returning(LocationModel.id)
            )
        return deleted is not None


def _review_union() -> Any:
    tour_reviews = (
        select(
            literal_column("'tour'", String).label("kind"),
            TourReviewModel.id.label("id"),
            TourModel.id.label("tour_id"),
            TourModel.title.label("tour_title"),
            TourOperatorModel.id.label("operator_id"),
            TourOperatorModel.name.label("operator_name"),
            TourReviewModel.user_id.label("user_id"),
            TourReviewModel.rating.label("rating"),
            TourReviewModel.text.label("text"),
            TourReviewModel.is_hidden.label("is_hidden"),
            TourReviewModel.created_at.label("created_at"),
            TourReviewModel.updated_at.label("updated_at"),
            and_(
                TourModel.is_published,
                TourModel.is_blocked.is_(False),
                TourModel.moderation_status == TourModerationStatus.APPROVED,
                TourOperatorModel.is_active,
            ).label("target_visible"),
        )
        .join(TourModel, TourModel.id == TourReviewModel.tour_id)
        .join(TourOperatorModel, TourOperatorModel.id == TourModel.operator_id)
    )
    company_reviews = select(
        literal_column("'company'", String).label("kind"),
        OperatorReviewModel.id,
        cast(null(), Uuid).label("tour_id"),
        cast(null(), String).label("tour_title"),
        TourOperatorModel.id,
        TourOperatorModel.name,
        OperatorReviewModel.user_id,
        OperatorReviewModel.rating,
        OperatorReviewModel.text,
        OperatorReviewModel.is_hidden,
        OperatorReviewModel.created_at,
        OperatorReviewModel.updated_at,
        TourOperatorModel.is_active,
    ).join(TourOperatorModel, TourOperatorModel.id == OperatorReviewModel.operator_id)
    return union_all(tour_reviews, company_reviews).subquery("reviews")


_REVIEW_ORDER = {
    ReviewSort.NEWEST: lambda r: [r.c.created_at.desc()],
    ReviewSort.OLDEST: lambda r: [r.c.created_at.asc()],
    ReviewSort.RATING_DESC: lambda r: [r.c.rating.desc(), r.c.created_at.desc()],
    ReviewSort.RATING_ASC: lambda r: [r.c.rating.asc(), r.c.created_at.desc()],
}


class SqlAlchemyReviewRepository:
    """Отзывы о турах и о компаниях хранятся в разных таблицах, читаются вместе."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _query(reviews: Any) -> Select[Any]:
        return select(
            reviews, UserModel.first_name, UserModel.last_name, UserModel.avatar_url
        ).join(UserModel, UserModel.id == reviews.c.user_id)

    async def _photos_for(self, review_ids: list[UUID]) -> dict[UUID, list[str]]:
        photos: dict[UUID, list[str]] = {review_id: [] for review_id in review_ids}
        if review_ids:
            rows = await self._session.execute(
                select(TourReviewPhotoModel.review_id, TourReviewPhotoModel.url)
                .where(TourReviewPhotoModel.review_id.in_(review_ids))
                .order_by(TourReviewPhotoModel.position)
            )
            for review_id, url in rows:
                photos[review_id].append(url)
        return photos

    @staticmethod
    def _to_view(row: Any, photo_urls: list[str]) -> ReviewView:
        return ReviewView(
            id=row.id,
            kind=ReviewKind(row.kind),
            tour_id=row.tour_id,
            tour_title=row.tour_title,
            operator_id=row.operator_id,
            operator_name=row.operator_name,
            user_id=row.user_id,
            rating=row.rating,
            text=row.text,
            photo_urls=photo_urls,
            is_hidden=row.is_hidden,
            author_first_name=row.first_name,
            author_last_name=row.last_name,
            author_avatar_url=row.avatar_url,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def search(
        self, filters: ReviewFilters, page: PageParams, *, visible_only: bool
    ) -> Page[ReviewView]:
        reviews = _review_union()
        c = reviews.c
        conditions: list[Any] = []
        if visible_only:
            conditions += [c.is_hidden.is_(False), c.target_visible]
        if filters.kind:
            conditions.append(c.kind == filters.kind.value)
        if filters.tour_id:
            conditions.append(c.tour_id == filters.tour_id)
        if filters.operator_id:
            conditions.append(c.operator_id == filters.operator_id)
        if filters.user_id:
            conditions.append(c.user_id == filters.user_id)
        if filters.rating_min is not None:
            conditions.append(c.rating >= filters.rating_min)
        if filters.rating_max is not None:
            conditions.append(c.rating <= filters.rating_max)
        if filters.is_hidden is not None:
            conditions.append(c.is_hidden.is_(filters.is_hidden))
        if filters.query:
            conditions.append(c.text.icontains(filters.query, autoescape=True))

        total = await self._session.scalar(
            select(func.count()).select_from(reviews).where(*conditions)
        )
        rows = (
            await self._session.execute(
                self._query(reviews)
                .where(*conditions)
                .order_by(*_REVIEW_ORDER[filters.sort](reviews), c.id)
                .limit(page.limit)
                .offset(page.offset)
            )
        ).all()
        photos = await self._photos_for([row.id for row in rows])
        return Page(
            items=[self._to_view(row, photos[row.id]) for row in rows],
            total=total or 0,
            limit=page.limit,
            offset=page.offset,
        )

    async def get(self, review_id: UUID) -> ReviewView | None:
        reviews = _review_union()
        row = (
            await self._session.execute(self._query(reviews).where(reviews.c.id == review_id))
        ).first()
        if row is None:
            return None
        return self._to_view(row, (await self._photos_for([row.id]))[row.id])

    async def list_tour_review_photos(
        self, tour_id: UUID, page: PageParams
    ) -> Page[ReviewPhotoView]:
        base = (
            select(
                TourReviewPhotoModel.review_id,
                TourReviewPhotoModel.url,
                TourReviewModel.rating,
                UserModel.first_name,
                TourReviewModel.created_at,
            )
            .join(TourReviewModel, TourReviewModel.id == TourReviewPhotoModel.review_id)
            .join(UserModel, UserModel.id == TourReviewModel.user_id)
            .where(TourReviewModel.tour_id == tour_id, TourReviewModel.is_hidden.is_(False))
        )
        total = await self._session.scalar(select(func.count()).select_from(base.subquery()))
        rows = await self._session.execute(
            base.order_by(
                TourReviewModel.created_at.desc(),
                TourReviewPhotoModel.review_id,
                TourReviewPhotoModel.position,
            )
            .limit(page.limit)
            .offset(page.offset)
        )
        return Page(
            items=[
                ReviewPhotoView(
                    review_id=review_id,
                    url=url,
                    rating=rating,
                    author_first_name=first_name,
                    created_at=created_at,
                )
                for review_id, url, rating, first_name, created_at in rows
            ],
            total=total or 0,
            limit=page.limit,
            offset=page.offset,
        )

    async def upsert_tour_review(
        self, tour_id: UUID, user_id: UUID, content: ReviewContent, now: datetime
    ) -> UUID:
        review_id = await self._upsert(
            TourReviewModel, {"tour_id": tour_id}, ["tour_id", "user_id"], user_id, content, now
        )
        # Фото заменяются целиком вместе с текстом отзыва.
        await self._session.execute(
            delete(TourReviewPhotoModel).where(TourReviewPhotoModel.review_id == review_id)
        )
        if content.photo_urls:
            await self._session.execute(
                insert(TourReviewPhotoModel),
                [
                    {"review_id": review_id, "position": position, "url": url}
                    for position, url in enumerate(content.photo_urls)
                ],
            )
        return review_id

    async def upsert_company_review(
        self, operator_id: UUID, user_id: UUID, content: ReviewContent, now: datetime
    ) -> UUID:
        return await self._upsert(
            OperatorReviewModel,
            {"operator_id": operator_id},
            ["operator_id", "user_id"],
            user_id,
            content,
            now,
        )

    async def _upsert(
        self,
        model: Any,
        target: dict[str, UUID],
        conflict_columns: list[str],
        user_id: UUID,
        content: ReviewContent,
        now: datetime,
    ) -> UUID:
        statement = pg_insert(model).values(
            id=uuid4(),
            user_id=user_id,
            rating=content.rating,
            text=content.text.strip(),
            created_at=now,
            updated_at=now,
            **target,
        )
        review_id = await self._session.scalar(
            statement.on_conflict_do_update(
                index_elements=conflict_columns,
                set_={
                    "rating": statement.excluded.rating,
                    "text": statement.excluded.text,
                    "updated_at": statement.excluded.updated_at,
                },
            ).returning(model.id)
        )
        assert review_id is not None
        return UUID(str(review_id))

    async def delete_own_tour_review(self, tour_id: UUID, user_id: UUID) -> bool:
        deleted = await self._session.scalar(
            delete(TourReviewModel)
            .where(TourReviewModel.tour_id == tour_id, TourReviewModel.user_id == user_id)
            .returning(TourReviewModel.id)
        )
        return deleted is not None

    async def delete_own_company_review(self, operator_id: UUID, user_id: UUID) -> bool:
        deleted = await self._session.scalar(
            delete(OperatorReviewModel)
            .where(
                OperatorReviewModel.operator_id == operator_id,
                OperatorReviewModel.user_id == user_id,
            )
            .returning(OperatorReviewModel.id)
        )
        return deleted is not None

    async def set_hidden(self, review_id: UUID, is_hidden: bool) -> bool:
        for model in (TourReviewModel, OperatorReviewModel):
            updated = await self._session.scalar(
                update(model)
                .where(model.id == review_id)
                .values(is_hidden=is_hidden)
                .returning(model.id)
            )
            if updated is not None:
                return True
        return False

    async def delete(self, review_id: UUID) -> bool:
        for model in (TourReviewModel, OperatorReviewModel):
            deleted = await self._session.scalar(
                delete(model).where(model.id == review_id).returning(model.id)
            )
            if deleted is not None:
                return True
        return False


class SqlAlchemyFavoriteRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, user_id: UUID, tour_id: UUID, now: datetime) -> None:
        await self._session.execute(
            pg_insert(FavoriteModel)
            .values(user_id=user_id, tour_id=tour_id, created_at=now)
            .on_conflict_do_nothing()
        )

    async def remove(self, user_id: UUID, tour_id: UUID) -> None:
        await self._session.execute(
            delete(FavoriteModel).where(
                FavoriteModel.user_id == user_id, FavoriteModel.tour_id == tour_id
            )
        )

    async def list_tour_ids(self, user_id: UUID) -> list[UUID]:
        ids = await self._session.scalars(
            select(FavoriteModel.tour_id)
            .join(TourModel, TourModel.id == FavoriteModel.tour_id)
            .where(FavoriteModel.user_id == user_id, visible_tour_condition())
            .order_by(FavoriteModel.created_at.desc())
        )
        return list(ids)
