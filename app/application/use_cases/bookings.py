import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.application.dto import BookingFilters, BookingView, Page, PageParams
from app.application.exceptions import AccessDeniedError
from app.application.ports.bookings import BookingRepository, PaymentGateway
from app.application.ports.unit_of_work import UnitOfWork
from app.application.use_cases.company import CompanyAccess
from app.domain.booking import (
    BookingStatus,
    Traveler,
    calculate_price,
    effective_status,
    validate_travelers,
)
from app.domain.company import CompanyPermission
from app.domain.exceptions import ConflictError, NotFoundError
from app.domain.user import User

logger = logging.getLogger(__name__)

CURRENCY = "USD"


@dataclass(frozen=True, kw_only=True)
class PaymentStart:
    booking: BookingView
    # None, если предоплата 0%: бронь подтверждается сразу.
    redirect_url: str | None


class ReserveSeats:
    """Нажатие «Купить»: места удерживаются на время оплаты."""

    def __init__(
        self, bookings: BookingRepository, unit_of_work: UnitOfWork, hold: timedelta
    ) -> None:
        self._bookings = bookings
        self._unit_of_work = unit_of_work
        self._hold = hold

    async def execute(
        self, user: User, departure_id: UUID, travelers: list[Traveler]
    ) -> BookingView:
        now = datetime.now(UTC)
        departure = await self._bookings.lock_departure(departure_id)
        if departure is None or not departure.is_bookable:
            raise NotFoundError("Departure is not available for booking")
        checked = validate_travelers(
            travelers,
            start_date=departure.start_at.date(),
            min_age=departure.min_age,
            max_age=departure.max_age,
        )

        await self._bookings.cancel_user_holds(user.id, departure_id, now)
        seats_left = departure.capacity - await self._bookings.seats_taken(departure_id)
        if len(checked) > seats_left:
            raise ConflictError(f"Only {max(seats_left, 0)} seats left on this date")

        booking_id = uuid4()
        await self._bookings.create(
            booking_id=booking_id,
            user_id=user.id,
            departure_id=departure_id,
            travelers=checked,
            price=calculate_price(departure.unit_price, len(checked), departure.prepayment_percent),
            expires_at=now + self._hold,
            now=now,
        )
        await self._unit_of_work.commit()
        return await _get(self._bookings, booking_id)


class PayForBooking:
    def __init__(
        self, bookings: BookingRepository, payments: PaymentGateway, unit_of_work: UnitOfWork
    ) -> None:
        self._bookings = bookings
        self._payments = payments
        self._unit_of_work = unit_of_work

    async def execute(self, user: User, booking_id: UUID) -> PaymentStart:
        now = datetime.now(UTC)
        record = await self._bookings.get_for_update(booking_id)
        if record is None or record.user_id != user.id:
            raise NotFoundError("Booking not found")
        if effective_status(record.status, record.expires_at, now) is not (
            BookingStatus.PENDING_PAYMENT
        ):
            raise ConflictError("Booking can no longer be paid; book again")

        if record.prepayment_amount == 0:
            await self._bookings.set_status(record.id, BookingStatus.CONFIRMED, now)
            await self._unit_of_work.commit()
            return PaymentStart(booking=await _get(self._bookings, record.id), redirect_url=None)

        session = await self._payments.create_payment(
            booking_id=record.id,
            amount=record.prepayment_amount,
            currency=CURRENCY,
            expires_at=record.expires_at,
        )
        await self._bookings.set_payment_reference(
            record.id, self._payments.provider, session.reference, now
        )
        await self._unit_of_work.commit()
        return PaymentStart(
            booking=await _get(self._bookings, record.id), redirect_url=session.redirect_url
        )


class ConfirmPayment:
    """Вызывается по уведомлению платёжного провайдера; повторный вызов безопасен."""

    def __init__(self, bookings: BookingRepository, unit_of_work: UnitOfWork) -> None:
        self._bookings = bookings
        self._unit_of_work = unit_of_work

    async def execute(self, reference: str) -> BookingView:
        now = datetime.now(UTC)
        record = await self._bookings.get_by_reference_for_update(reference)
        if record is None:
            raise NotFoundError("Payment not found")
        if record.status is BookingStatus.CONFIRMED:
            return await _get(self._bookings, record.id)
        if record.status is not BookingStatus.PENDING_PAYMENT:
            logger.error(
                "Payment %s for %s booking %s needs a refund", reference, record.status, record.id
            )
            raise ConflictError("Booking is not awaiting payment; the payment must be refunded")

        if now >= record.expires_at:
            # Оплата пришла после окончания удержания: подтверждаем, только если места остались.
            departure = await self._bookings.lock_departure(record.departure_id)
            taken = await self._bookings.seats_taken(record.departure_id, exclude=record.id)
            if departure is None or record.seats > departure.capacity - taken:
                await self._bookings.set_status(record.id, BookingStatus.EXPIRED, now)
                await self._unit_of_work.commit()
                logger.error("Late payment %s for booking %s needs a refund", reference, record.id)
                raise ConflictError(
                    "Seats were released before payment; the payment must be refunded"
                )

        await self._bookings.set_status(record.id, BookingStatus.CONFIRMED, now)
        await self._unit_of_work.commit()
        return await _get(self._bookings, record.id)


class CancelBooking:
    def __init__(self, bookings: BookingRepository, unit_of_work: UnitOfWork) -> None:
        self._bookings = bookings
        self._unit_of_work = unit_of_work

    async def execute(self, user: User, booking_id: UUID) -> BookingView:
        now = datetime.now(UTC)
        record = await self._bookings.get_for_update(booking_id)
        if record is None or record.user_id != user.id:
            raise NotFoundError("Booking not found")
        status = effective_status(record.status, record.expires_at, now)
        if status is BookingStatus.CONFIRMED:
            raise ConflictError("Paid booking is cancelled through the tour operator")
        if status is BookingStatus.PENDING_PAYMENT:
            await self._bookings.set_status(record.id, BookingStatus.CANCELLED, now)
            await self._unit_of_work.commit()
        return await _get(self._bookings, record.id)


class ViewBookings:
    def __init__(self, bookings: BookingRepository, access: CompanyAccess) -> None:
        self._bookings = bookings
        self._access = access

    async def get_mine(self, user: User, booking_id: UUID) -> BookingView:
        booking = await self._bookings.get(booking_id)
        if booking is None or booking.user_id != user.id:
            raise NotFoundError("Booking not found")
        return booking

    async def list_mine(
        self, user: User, filters: BookingFilters, page: PageParams
    ) -> Page[BookingView]:
        return await self._bookings.search(replace(filters, user_id=user.id), page)

    async def list_for_company(
        self, user: User, operator_id: UUID, filters: BookingFilters, page: PageParams
    ) -> Page[BookingView]:
        await self._access.require(user, operator_id, CompanyPermission.MANAGE_TOURS)
        return await self._bookings.search(replace(filters, operator_id=operator_id), page)

    async def list_all(
        self, user: User, filters: BookingFilters, page: PageParams
    ) -> Page[BookingView]:
        if not user.is_admin:
            raise AccessDeniedError("Administrator access required")
        return await self._bookings.search(filters, page)


async def _get(bookings: BookingRepository, booking_id: UUID) -> BookingView:
    booking = await bookings.get(booking_id)
    if booking is None:
        raise NotFoundError("Booking not found")
    return booking
