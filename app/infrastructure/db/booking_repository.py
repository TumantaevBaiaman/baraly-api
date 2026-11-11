from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, and_, case, func, insert, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dto import BookingFilters, BookingView, Page, PageParams, TravelerView
from app.application.ports.bookings import BookingRecord, DepartureForBooking
from app.domain.booking import BookingPrice, BookingStatus, Traveler
from app.infrastructure.db.catalog_reader import (
    active_booking_condition,
    expired_tour_condition,
    visible_tour_condition,
)
from app.infrastructure.db.models import (
    BookingModel,
    BookingTravelerModel,
    TourDepartureModel,
    TourModel,
    TourOperatorModel,
    UserModel,
)

# Статус с учётом времени: неоплаченная бронь после expires_at считается истёкшей.
_effective_status = case(
    (
        and_(
            BookingModel.status == BookingStatus.PENDING_PAYMENT,
            BookingModel.expires_at <= func.now(),
        ),
        literal(BookingStatus.EXPIRED.value),
    ),
    else_=BookingModel.status,
)


def _to_record(model: BookingModel) -> BookingRecord:
    return BookingRecord(
        id=model.id,
        user_id=model.user_id,
        departure_id=model.departure_id,
        seats=model.seats,
        prepayment_amount=model.prepayment_amount,
        status=model.status,
        expires_at=model.expires_at,
        payment_reference=model.payment_reference,
    )


class SqlAlchemyBookingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_departure(self, departure_id: UUID) -> DepartureForBooking | None:
        row = (
            await self._session.execute(
                select(
                    TourDepartureModel,
                    TourModel,
                    and_(
                        visible_tour_condition(),
                        ~expired_tour_condition(),
                        TourDepartureModel.start_at > func.now(),
                    ).label("is_bookable"),
                )
                .join(TourModel, TourModel.id == TourDepartureModel.tour_id)
                .where(TourDepartureModel.id == departure_id)
                .with_for_update(of=TourDepartureModel)
            )
        ).first()
        if row is None:
            return None
        departure, tour, is_bookable = row
        return DepartureForBooking(
            id=departure.id,
            tour_id=tour.id,
            operator_id=tour.operator_id,
            start_at=departure.start_at,
            capacity=departure.capacity,
            unit_price=departure.price if departure.price is not None else tour.price,
            prepayment_percent=tour.prepayment_percent,
            min_age=tour.min_age,
            max_age=tour.max_age,
            is_bookable=bool(is_bookable),
        )

    async def seats_taken(self, departure_id: UUID, *, exclude: UUID | None = None) -> int:
        conditions: list[Any] = [
            BookingModel.departure_id == departure_id,
            active_booking_condition(),
        ]
        if exclude is not None:
            conditions.append(BookingModel.id != exclude)
        taken = await self._session.scalar(
            select(func.coalesce(func.sum(BookingModel.seats), 0)).where(*conditions)
        )
        return int(taken or 0)

    async def cancel_user_holds(self, user_id: UUID, departure_id: UUID, now: datetime) -> None:
        await self._session.execute(
            update(BookingModel)
            .where(
                BookingModel.user_id == user_id,
                BookingModel.departure_id == departure_id,
                BookingModel.status == BookingStatus.PENDING_PAYMENT,
            )
            .values(status=BookingStatus.CANCELLED, cancelled_at=now, updated_at=now)
        )

    async def create(
        self,
        *,
        booking_id: UUID,
        user_id: UUID,
        departure_id: UUID,
        travelers: list[Traveler],
        price: BookingPrice,
        expires_at: datetime,
        now: datetime,
    ) -> None:
        await self._session.execute(
            insert(BookingModel).values(
                id=booking_id,
                user_id=user_id,
                departure_id=departure_id,
                seats=len(travelers),
                unit_price=price.unit_price,
                total_price=price.total_price,
                prepayment_amount=price.prepayment_amount,
                status=BookingStatus.PENDING_PAYMENT,
                expires_at=expires_at,
                created_at=now,
                updated_at=now,
            )
        )
        await self._session.execute(
            insert(BookingTravelerModel),
            [
                {
                    "booking_id": booking_id,
                    "position": position,
                    "first_name": traveler.first_name,
                    "last_name": traveler.last_name,
                    "birth_date": traveler.birth_date,
                    "gender": traveler.gender,
                    "phone": traveler.phone,
                    "email": traveler.email,
                }
                for position, traveler in enumerate(travelers)
            ],
        )

    async def get_for_update(self, booking_id: UUID) -> BookingRecord | None:
        model = await self._session.scalar(
            select(BookingModel).where(BookingModel.id == booking_id).with_for_update()
        )
        return _to_record(model) if model else None

    async def get_by_reference_for_update(self, reference: str) -> BookingRecord | None:
        model = await self._session.scalar(
            select(BookingModel)
            .where(BookingModel.payment_reference == reference)
            .with_for_update()
        )
        return _to_record(model) if model else None

    async def set_payment_reference(
        self, booking_id: UUID, provider: str, reference: str, now: datetime
    ) -> None:
        await self._session.execute(
            update(BookingModel)
            .where(BookingModel.id == booking_id)
            .values(payment_provider=provider, payment_reference=reference, updated_at=now)
        )

    async def set_status(self, booking_id: UUID, status: BookingStatus, now: datetime) -> None:
        values: dict[str, Any] = {"status": status, "updated_at": now}
        if status is BookingStatus.CONFIRMED:
            values["paid_at"] = now
        if status is BookingStatus.CANCELLED:
            values["cancelled_at"] = now
        await self._session.execute(
            update(BookingModel).where(BookingModel.id == booking_id).values(**values)
        )

    @staticmethod
    def _query() -> Select[Any]:
        return (
            select(
                BookingModel,
                _effective_status.label("effective_status"),
                TourDepartureModel.start_at,
                TourDepartureModel.end_at,
                TourModel.id.label("tour_id"),
                TourModel.title,
                TourOperatorModel.id.label("operator_id"),
                TourOperatorModel.name.label("operator_name"),
                UserModel.email,
            )
            .join(TourDepartureModel, TourDepartureModel.id == BookingModel.departure_id)
            .join(TourModel, TourModel.id == TourDepartureModel.tour_id)
            .join(TourOperatorModel, TourOperatorModel.id == TourModel.operator_id)
            .join(UserModel, UserModel.id == BookingModel.user_id)
        )

    async def _travelers_for(self, booking_ids: list[UUID]) -> dict[UUID, list[TravelerView]]:
        result: dict[UUID, list[TravelerView]] = {booking_id: [] for booking_id in booking_ids}
        if booking_ids:
            travelers = await self._session.scalars(
                select(BookingTravelerModel)
                .where(BookingTravelerModel.booking_id.in_(booking_ids))
                .order_by(BookingTravelerModel.position)
            )
            for item in travelers:
                result[item.booking_id].append(
                    TravelerView(
                        first_name=item.first_name,
                        last_name=item.last_name,
                        birth_date=item.birth_date,
                        gender=item.gender,
                        phone=item.phone,
                        email=item.email,
                    )
                )
        return result

    @staticmethod
    def _to_view(row: Any, travelers: list[TravelerView]) -> BookingView:
        booking: BookingModel = row[0]
        return BookingView(
            id=booking.id,
            status=BookingStatus(row.effective_status),
            tour_id=row.tour_id,
            tour_title=row.title,
            operator_id=row.operator_id,
            operator_name=row.operator_name,
            departure_id=booking.departure_id,
            start_at=row.start_at,
            end_at=row.end_at,
            seats=booking.seats,
            unit_price=booking.unit_price,
            total_price=booking.total_price,
            prepayment_amount=booking.prepayment_amount,
            user_id=booking.user_id,
            user_email=row.email,
            travelers=travelers,
            expires_at=booking.expires_at,
            paid_at=booking.paid_at,
            cancelled_at=booking.cancelled_at,
            created_at=booking.created_at,
        )

    async def get(self, booking_id: UUID) -> BookingView | None:
        row = (
            await self._session.execute(
                self._query()
                .where(BookingModel.id == booking_id)
                .execution_options(populate_existing=True)
            )
        ).first()
        if row is None:
            return None
        return self._to_view(row, (await self._travelers_for([booking_id]))[booking_id])

    async def search(self, filters: BookingFilters, page: PageParams) -> Page[BookingView]:
        conditions: list[Any] = []
        if filters.user_id:
            conditions.append(BookingModel.user_id == filters.user_id)
        if filters.operator_id:
            conditions.append(TourModel.operator_id == filters.operator_id)
        if filters.tour_id:
            conditions.append(TourModel.id == filters.tour_id)
        if filters.departure_id:
            conditions.append(BookingModel.departure_id == filters.departure_id)
        if filters.status:
            conditions.append(_effective_status == filters.status.value)
        base = self._query().where(*conditions)
        total = await self._session.scalar(select(func.count()).select_from(base.subquery()))
        rows = (
            await self._session.execute(
                base.order_by(BookingModel.created_at.desc(), BookingModel.id)
                .limit(page.limit)
                .offset(page.offset)
                .execution_options(populate_existing=True)
            )
        ).all()
        travelers = await self._travelers_for([row[0].id for row in rows])
        return Page(
            items=[self._to_view(row, travelers[row[0].id]) for row in rows],
            total=total or 0,
            limit=page.limit,
            offset=page.offset,
        )
