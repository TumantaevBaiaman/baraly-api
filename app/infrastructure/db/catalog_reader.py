from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Select, and_, exists, func, or_, select, union_all
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.application.dto import (
    DepartureView,
    LanguageView,
    LocationView,
    OperatorAdminView,
    OperatorDetail,
    OperatorFilters,
    OperatorSort,
    OperatorSummary,
    Page,
    PageParams,
    TagView,
    TourCard,
    TourDayView,
    TourDetail,
    TourFilters,
    TourSort,
)
from app.domain.booking import BookingStatus
from app.domain.tour import TourModerationStatus
from app.infrastructure.db.models import (
    BookingModel,
    CompanyMemberModel,
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
    TourTagModel,
)

_active_operator_ids = select(TourOperatorModel.id).where(TourOperatorModel.is_active)


def active_booking_condition() -> Any:
    """Бронь занимает места, если оплачена или удержание ещё не истекло."""
    return or_(
        BookingModel.status == BookingStatus.CONFIRMED,
        and_(
            BookingModel.status == BookingStatus.PENDING_PAYMENT,
            BookingModel.expires_at > func.now(),
        ),
    )


def seats_taken_expr(departure_id: Any) -> Any:
    return (
        select(func.coalesce(func.sum(BookingModel.seats), 0))
        .where(BookingModel.departure_id == departure_id, active_booking_condition())
        .scalar_subquery()
    )


def visible_tour_condition() -> Any:
    """Тур открыт по ссылке: опубликован, одобрен, не скрыт, компания активна."""
    return and_(
        TourModel.is_published,
        TourModel.is_blocked.is_(False),
        TourModel.moderation_status == TourModerationStatus.APPROVED,
        TourModel.operator_id.in_(_active_operator_ids),
    )


def expired_tour_condition() -> Any:
    """Даты были, но все прошли. Туры без точных дат ("Any day") не устаревают."""
    # Псевдоним: условие работает и в запросах, где выезд уже выбран во внешнем FROM.
    other = aliased(TourDepartureModel)
    departures = select(other.id).where(other.tour_id == TourModel.id)
    return and_(
        TourModel.schedule_note.is_(None),
        exists(departures),
        ~exists(departures.where(other.start_at > func.now())),
    )


# Рейтинги не хранятся в таблицах (3НФ), а считаются из видимых отзывов.
_tour_stats = (
    select(
        TourReviewModel.tour_id,
        func.avg(TourReviewModel.rating).label("rating"),
        func.count().label("review_count"),
    )
    .where(TourReviewModel.is_hidden.is_(False))
    .group_by(TourReviewModel.tour_id)
    .subquery("tour_stats")
)
# Рейтинг компании складывается из отзывов о её турах и отзывов о самой компании.
_operator_ratings = union_all(
    select(TourModel.operator_id.label("operator_id"), TourReviewModel.rating.label("rating"))
    .join(TourModel, TourModel.id == TourReviewModel.tour_id)
    .where(
        TourReviewModel.is_hidden.is_(False),
        TourModel.is_published,
        TourModel.is_blocked.is_(False),
        TourModel.moderation_status == TourModerationStatus.APPROVED,
    ),
    select(OperatorReviewModel.operator_id, OperatorReviewModel.rating).where(
        OperatorReviewModel.is_hidden.is_(False)
    ),
).subquery("operator_ratings")
_operator_stats = (
    select(
        _operator_ratings.c.operator_id,
        func.avg(_operator_ratings.c.rating).label("rating"),
        func.count().label("review_count"),
    )
    .group_by(_operator_ratings.c.operator_id)
    .subquery("operator_stats")
)


def _rating(value: Any) -> float | None:
    return round(float(value), 1) if value is not None else None


def _start_of(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def _location_condition(location: str) -> Any:
    matching = select(LocationModel.id).where(
        LocationModel.name.icontains(location, autoescape=True)
    )
    return or_(
        TourModel.start_location_id.in_(matching),
        TourModel.finish_location_id.in_(matching),
        exists().where(
            TourDayModel.tour_id == TourModel.id, TourDayModel.location_id.in_(matching)
        ),
    )


def _departure_condition(filters: TourFilters, now: datetime) -> Any:
    conditions = [TourDepartureModel.tour_id == TourModel.id, TourDepartureModel.start_at > now]
    if filters.date_from:
        conditions.append(TourDepartureModel.start_at >= _start_of(filters.date_from))
    if filters.date_to:
        conditions.append(
            TourDepartureModel.start_at < _start_of(filters.date_to) + timedelta(days=1)
        )
    if filters.persons:
        conditions.append(
            TourDepartureModel.capacity - seats_taken_expr(TourDepartureModel.id) >= filters.persons
        )
    # Туры без точных дат ("Any day") проводятся по запросу и подходят под любой период.
    return or_(exists().where(*conditions), TourModel.schedule_note.is_not(None))


def _conditions(filters: TourFilters, include_unpublished: bool, now: datetime) -> list[Any]:
    conditions: list[Any] = []
    if include_unpublished:
        if filters.is_published is not None:
            conditions.append(TourModel.is_published.is_(filters.is_published))
        if filters.is_blocked is not None:
            conditions.append(TourModel.is_blocked.is_(filters.is_blocked))
        if filters.moderation_status is not None:
            conditions.append(TourModel.moderation_status == filters.moderation_status)
        if filters.is_expired is not None:
            expired = expired_tour_condition()
            conditions.append(expired if filters.is_expired else ~expired)
    else:
        conditions.append(visible_tour_condition())
        # Избранное показывает и прошедшие туры, каталог только актуальные.
        if not filters.favorite_of:
            conditions.append(~expired_tour_condition())
    if filters.query:
        conditions.append(TourModel.title.icontains(filters.query, autoescape=True))
    if filters.location:
        conditions.append(_location_condition(filters.location))
    if filters.date_from or filters.date_to or filters.persons:
        conditions.append(_departure_condition(filters, now))
    if filters.duration_min is not None:
        conditions.append(TourModel.duration_days >= filters.duration_min)
    if filters.duration_max is not None:
        conditions.append(TourModel.duration_days <= filters.duration_max)
    if filters.price_min is not None:
        conditions.append(TourModel.price >= filters.price_min)
    if filters.price_max is not None:
        conditions.append(TourModel.price <= filters.price_max)
    if filters.age_from is not None:
        conditions.append(TourModel.max_age >= filters.age_from)
    if filters.age_to is not None:
        conditions.append(TourModel.min_age <= filters.age_to)
    if filters.tags:
        conditions.append(
            exists().where(
                TourTagModel.tour_id == TourModel.id, TourTagModel.tag_slug.in_(filters.tags)
            )
        )
    if filters.languages:
        conditions.append(
            exists().where(
                TourLanguageModel.tour_id == TourModel.id,
                TourLanguageModel.language_code.in_(filters.languages),
            )
        )
    if filters.activity:
        conditions.append(TourModel.activity.in_(filters.activity))
    if filters.operator_id:
        conditions.append(TourModel.operator_id == filters.operator_id)
    if filters.favorite_of:
        conditions.append(
            TourModel.id.in_(
                select(FavoriteModel.tour_id).where(FavoriteModel.user_id == filters.favorite_of)
            )
        )
    return conditions


def _order_by(sort: TourSort) -> list[Any]:
    primary = {
        TourSort.POPULAR: [
            _tour_stats.c.review_count.desc().nulls_last(),
            _tour_stats.c.rating.desc().nulls_last(),
        ],
        TourSort.RATING: [
            _tour_stats.c.rating.desc().nulls_last(),
            _tour_stats.c.review_count.desc().nulls_last(),
        ],
        TourSort.PRICE_ASC: [TourModel.price.asc()],
        TourSort.PRICE_DESC: [TourModel.price.desc()],
        TourSort.NEWEST: [TourModel.created_at.desc()],
        TourSort.OLDEST: [TourModel.updated_at.asc()],
    }[sort]
    return [*primary, TourModel.id]


def _card_query(now: datetime) -> Select[Any]:
    next_departure = (
        select(
            TourDepartureModel,
            seats_taken_expr(TourDepartureModel.id).label("seats_taken"),
        )
        .where(TourDepartureModel.start_at > now)
        .ext(distinct_on(TourDepartureModel.tour_id))
        .order_by(TourDepartureModel.tour_id, TourDepartureModel.start_at)
        .subquery("next_departure")
    )
    return (
        select(
            TourModel,
            TourOperatorModel,
            TourImageModel.url.label("cover_url"),
            _tour_stats.c.rating.label("tour_rating"),
            _tour_stats.c.review_count.label("tour_review_count"),
            _operator_stats.c.rating.label("operator_rating"),
            _operator_stats.c.review_count.label("operator_review_count"),
            next_departure.c.id.label("departure_id"),
            next_departure.c.start_at.label("departure_start_at"),
            next_departure.c.end_at.label("departure_end_at"),
            next_departure.c.capacity.label("departure_capacity"),
            next_departure.c.price.label("departure_price"),
            next_departure.c.seats_taken.label("departure_seats_taken"),
            expired_tour_condition().label("is_expired"),
        )
        .join(TourOperatorModel, TourOperatorModel.id == TourModel.operator_id)
        .outerjoin(
            TourImageModel,
            and_(TourImageModel.tour_id == TourModel.id, TourImageModel.position == 0),
        )
        .outerjoin(_tour_stats, _tour_stats.c.tour_id == TourModel.id)
        .outerjoin(_operator_stats, _operator_stats.c.operator_id == TourModel.operator_id)
        .outerjoin(next_departure, next_departure.c.tour_id == TourModel.id)
    )


def _card_fields(row: Any, tags: list[TagView]) -> dict[str, Any]:
    tour: TourModel = row[0]
    operator: TourOperatorModel = row[1]
    departure = None
    if row.departure_id is not None:
        departure = DepartureView(
            id=row.departure_id,
            start_at=row.departure_start_at,
            end_at=row.departure_end_at,
            capacity=row.departure_capacity,
            price=row.departure_price if row.departure_price is not None else tour.price,
            seats_taken=row.departure_seats_taken,
        )
    return {
        "id": tour.id,
        "title": tour.title,
        "cover_image_url": row.cover_url,
        "tags": tags,
        "rating": _rating(row.tour_rating),
        "review_count": row.tour_review_count or 0,
        "duration_days": tour.duration_days,
        "duration_nights": tour.duration_nights,
        "next_departure": departure,
        "schedule_note": tour.schedule_note,
        "price": tour.price,
        "operator": OperatorSummary(
            id=operator.id,
            name=operator.name,
            logo_url=operator.logo_url,
            rating=_rating(row.operator_rating),
            review_count=row.operator_review_count or 0,
        ),
        "is_published": tour.is_published,
        "is_blocked": tour.is_blocked,
        "moderation_status": tour.moderation_status,
        "is_expired": bool(row.is_expired),
        "created_at": tour.created_at,
        "updated_at": tour.updated_at,
    }


def _to_location(model: LocationModel) -> LocationView:
    return LocationView(
        id=model.id, name=model.name, latitude=model.latitude, longitude=model.longitude
    )


def _operator_fields(row: Any) -> dict[str, Any]:
    operator: TourOperatorModel = row[0]
    return {
        "id": operator.id,
        "name": operator.name,
        "logo_url": operator.logo_url,
        "rating": _rating(row.operator_rating),
        "review_count": row.operator_review_count or 0,
        "tagline": operator.tagline,
        "address": operator.address,
        "phone": operator.phone,
        "instagram": operator.instagram,
        "cover_url": operator.cover_url,
        "description": operator.description,
        "email": operator.email,
        "website": operator.website,
        "whatsapp": operator.whatsapp,
        "telegram": operator.telegram,
        "legal_name": operator.legal_name,
        "license_number": operator.license_number,
        "is_active": operator.is_active,
        "is_verified": operator.is_verified,
    }


def _operator_query() -> Select[Any]:
    return select(
        TourOperatorModel,
        _operator_stats.c.rating.label("operator_rating"),
        _operator_stats.c.review_count.label("operator_review_count"),
    ).outerjoin(_operator_stats, _operator_stats.c.operator_id == TourOperatorModel.id)


def _operator_admin_query() -> Select[Any]:
    def count_tours(*conditions: Any) -> Any:
        return (
            select(func.count())
            .where(TourModel.operator_id == TourOperatorModel.id, *conditions)
            .correlate(TourOperatorModel)
            .scalar_subquery()
        )

    members = (
        select(func.count())
        .where(CompanyMemberModel.operator_id == TourOperatorModel.id)
        .correlate(TourOperatorModel)
        .scalar_subquery()
    )
    return _operator_query().add_columns(
        count_tours().label("tours_count"),
        count_tours(
            TourModel.is_published,
            TourModel.is_blocked.is_(False),
            TourModel.moderation_status == TourModerationStatus.APPROVED,
        ).label("published_tours_count"),
        members.label("members_count"),
    )


def _to_operator_admin(row: Any) -> OperatorAdminView:
    return OperatorAdminView(
        **_operator_fields(row),
        tours_count=row.tours_count,
        published_tours_count=row.published_tours_count,
        members_count=row.members_count,
        verification_note=row[0].verification_note,
        verified_at=row[0].verified_at,
        created_at=row[0].created_at,
    )


class SqlAlchemyCatalogReader:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search_tours(
        self, filters: TourFilters, page: PageParams, *, include_unpublished: bool = False
    ) -> Page[TourCard]:
        now = datetime.now(UTC)
        conditions = _conditions(filters, include_unpublished, now)
        total = await self._session.scalar(
            select(func.count()).select_from(TourModel).where(*conditions)
        )
        rows = (
            await self._session.execute(
                _card_query(now)
                .where(*conditions)
                .order_by(*_order_by(filters.sort))
                .limit(page.limit)
                .offset(page.offset)
            )
        ).all()
        tags = await self._tags_for([row[0].id for row in rows])
        return Page(
            items=[TourCard(**_card_fields(row, tags[row[0].id])) for row in rows],
            total=total or 0,
            limit=page.limit,
            offset=page.offset,
        )

    async def get_tour(
        self, tour_id: UUID, *, include_unpublished: bool = False
    ) -> TourDetail | None:
        statement = _card_query(datetime.now(UTC)).where(TourModel.id == tour_id)
        if not include_unpublished:
            statement = statement.where(visible_tour_condition())
        row = (await self._session.execute(statement)).first()
        if row is None:
            return None

        tour: TourModel = row[0]
        tags = await self._tags_for([tour.id])
        locations = await self._locations_for(tour)
        inclusions = await self._session.scalars(
            select(TourInclusionModel)
            .where(TourInclusionModel.tour_id == tour_id)
            .order_by(TourInclusionModel.position)
        )
        included: list[str] = []
        excluded: list[str] = []
        for inclusion in inclusions:
            (included if inclusion.is_included else excluded).append(inclusion.text)

        def location(location_id: UUID | None) -> LocationView | None:
            return locations.get(location_id) if location_id else None

        return TourDetail(
            **_card_fields(row, tags[tour.id]),
            short_description=tour.short_description,
            description=tour.description,
            activity=tour.activity,
            comfort=tour.comfort,
            min_age=tour.min_age,
            max_age=tour.max_age,
            prepayment_percent=tour.prepayment_percent,
            free_cancellation_hours=tour.free_cancellation_hours,
            accommodation=tour.accommodation,
            cancellation_policy=tour.cancellation_policy,
            booking_info=tour.booking_info,
            brochure_url=tour.brochure_url,
            blocked_reason=tour.blocked_reason,
            moderation_comment=tour.moderation_comment,
            moderated_at=tour.moderated_at,
            start_location=location(tour.start_location_id),
            finish_location=location(tour.finish_location_id),
            languages=await self._languages_for(tour_id),
            image_urls=list(
                await self._session.scalars(
                    select(TourImageModel.url)
                    .where(TourImageModel.tour_id == tour_id)
                    .order_by(TourImageModel.position)
                )
            ),
            days=await self._days_for(tour_id, locations),
            included=included,
            excluded=excluded,
            departures=await self.list_departures(tour_id, tour.price),
        )

    async def is_tour_visible(self, tour_id: UUID) -> bool:
        found = await self._session.scalar(
            select(TourModel.id).where(TourModel.id == tour_id, visible_tour_condition())
        )
        return found is not None

    async def list_operators(self, page: PageParams) -> Page[OperatorDetail]:
        total = await self._session.scalar(
            select(func.count()).select_from(TourOperatorModel).where(TourOperatorModel.is_active)
        )
        rows = await self._session.execute(
            _operator_query()
            .where(TourOperatorModel.is_active)
            .order_by(TourOperatorModel.name, TourOperatorModel.id)
            .limit(page.limit)
            .offset(page.offset)
        )
        return Page(
            items=[OperatorDetail(**_operator_fields(row)) for row in rows],
            total=total or 0,
            limit=page.limit,
            offset=page.offset,
        )

    async def get_operator(
        self, operator_id: UUID, *, include_inactive: bool = False
    ) -> OperatorDetail | None:
        statement = _operator_query().where(TourOperatorModel.id == operator_id)
        if not include_inactive:
            statement = statement.where(TourOperatorModel.is_active)
        row = (await self._session.execute(statement)).first()
        return OperatorDetail(**_operator_fields(row)) if row else None

    async def search_operators_admin(
        self, filters: OperatorFilters, page: PageParams
    ) -> Page[OperatorAdminView]:
        conditions: list[Any] = []
        if filters.query:
            conditions.append(TourOperatorModel.name.icontains(filters.query, autoescape=True))
        if filters.is_active is not None:
            conditions.append(TourOperatorModel.is_active.is_(filters.is_active))
        if filters.is_verified is not None:
            conditions.append(TourOperatorModel.is_verified.is_(filters.is_verified))
        total = await self._session.scalar(
            select(func.count()).select_from(TourOperatorModel).where(*conditions)
        )
        statement = _operator_admin_query().where(*conditions)
        orders: dict[OperatorSort, list[Any]] = {
            OperatorSort.NAME: [TourOperatorModel.name.asc()],
            OperatorSort.NEWEST: [TourOperatorModel.created_at.desc()],
            OperatorSort.RATING: [_operator_stats.c.rating.desc().nulls_last()],
            OperatorSort.TOURS: [statement.selected_columns.tours_count.desc()],
        }
        order = orders[filters.sort]
        rows = await self._session.execute(
            statement.order_by(*order, TourOperatorModel.id).limit(page.limit).offset(page.offset)
        )
        return Page(
            items=[_to_operator_admin(row) for row in rows],
            total=total or 0,
            limit=page.limit,
            offset=page.offset,
        )

    async def get_operator_admin(self, operator_id: UUID) -> OperatorAdminView | None:
        row = (
            await self._session.execute(
                _operator_admin_query().where(TourOperatorModel.id == operator_id)
            )
        ).first()
        return _to_operator_admin(row) if row else None

    async def list_tags(self) -> list[TagView]:
        tags = await self._session.scalars(select(TagModel).order_by(TagModel.name))
        return [TagView(slug=tag.slug, name=tag.name) for tag in tags]

    async def list_languages(self) -> list[LanguageView]:
        languages = await self._session.scalars(select(LanguageModel).order_by(LanguageModel.name))
        return [LanguageView(code=item.code, name=item.name) for item in languages]

    async def list_locations(self) -> list[LocationView]:
        locations = await self._session.scalars(select(LocationModel).order_by(LocationModel.name))
        return [_to_location(item) for item in locations]

    async def _tags_for(self, tour_ids: list[UUID]) -> dict[UUID, list[TagView]]:
        result: dict[UUID, list[TagView]] = defaultdict(list)
        if not tour_ids:
            return result
        rows = await self._session.execute(
            select(TourTagModel.tour_id, TagModel.slug, TagModel.name)
            .join(TagModel, TagModel.slug == TourTagModel.tag_slug)
            .where(TourTagModel.tour_id.in_(tour_ids))
            .order_by(TagModel.name)
        )
        for tour_id, slug, name in rows:
            result[tour_id].append(TagView(slug=slug, name=name))
        return result

    async def _languages_for(self, tour_id: UUID) -> list[LanguageView]:
        rows = await self._session.execute(
            select(LanguageModel.code, LanguageModel.name)
            .join(TourLanguageModel, TourLanguageModel.language_code == LanguageModel.code)
            .where(TourLanguageModel.tour_id == tour_id)
            .order_by(LanguageModel.name)
        )
        return [LanguageView(code=code, name=name) for code, name in rows]

    async def _locations_for(self, tour: TourModel) -> dict[UUID, LocationView]:
        day_locations = select(TourDayModel.location_id).where(TourDayModel.tour_id == tour.id)
        route_locations = [
            item for item in (tour.start_location_id, tour.finish_location_id) if item
        ]
        locations = await self._session.scalars(
            select(LocationModel).where(
                or_(
                    LocationModel.id.in_(day_locations),
                    LocationModel.id.in_(route_locations),
                )
            )
        )
        return {item.id: _to_location(item) for item in locations}

    async def _days_for(
        self, tour_id: UUID, locations: dict[UUID, LocationView]
    ) -> list[TourDayView]:
        days = (
            await self._session.scalars(
                select(TourDayModel)
                .where(TourDayModel.tour_id == tour_id)
                .order_by(TourDayModel.day_number)
            )
        ).all()
        day_ids = [day.id for day in days]
        meals: dict[UUID, list[Any]] = defaultdict(list)
        landmarks: dict[UUID, list[str]] = defaultdict(list)
        images: dict[UUID, list[str]] = defaultdict(list)
        if day_ids:
            for meal in await self._session.scalars(
                select(TourDayMealModel).where(TourDayMealModel.day_id.in_(day_ids))
            ):
                meals[meal.day_id].append(meal.meal)
            for landmark in await self._session.scalars(
                select(TourDayLandmarkModel)
                .where(TourDayLandmarkModel.day_id.in_(day_ids))
                .order_by(TourDayLandmarkModel.position)
            ):
                landmarks[landmark.day_id].append(landmark.name)
            for image in await self._session.scalars(
                select(TourDayImageModel)
                .where(TourDayImageModel.day_id.in_(day_ids))
                .order_by(TourDayImageModel.position)
            ):
                images[image.day_id].append(image.url)
        return [
            TourDayView(
                day_number=day.day_number,
                title=day.title,
                description=day.description,
                location=locations.get(day.location_id) if day.location_id else None,
                meals=meals[day.id],
                landmarks=landmarks[day.id],
                image_urls=images[day.id],
            )
            for day in days
        ]

    async def list_departures(
        self,
        tour_id: UUID,
        tour_price: Decimal,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[DepartureView]:
        conditions: list[Any] = [TourDepartureModel.tour_id == tour_id]
        if start is not None:
            conditions.append(TourDepartureModel.start_at >= start)
        if end is not None:
            conditions.append(TourDepartureModel.start_at < end)
        rows = await self._session.execute(
            select(TourDepartureModel, seats_taken_expr(TourDepartureModel.id))
            .where(*conditions)
            .order_by(TourDepartureModel.start_at)
        )
        return [
            DepartureView(
                id=item.id,
                start_at=item.start_at,
                end_at=item.end_at,
                capacity=item.capacity,
                price=item.price if item.price is not None else tour_price,
                seats_taken=seats_taken,
            )
            for item, seats_taken in rows
        ]

    async def get_tour_price(self, tour_id: UUID) -> Decimal | None:
        return await self._session.scalar(
            select(TourModel.price).where(TourModel.id == tour_id, visible_tour_condition())
        )
