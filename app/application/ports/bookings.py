from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from app.application.dto import BookingFilters, BookingView, Page, PageParams
from app.domain.booking import BookingPrice, BookingStatus, Traveler


@dataclass(frozen=True, kw_only=True)
class DepartureForBooking:
    id: UUID
    tour_id: UUID
    operator_id: UUID
    start_at: datetime
    capacity: int
    unit_price: Decimal
    prepayment_percent: int
    min_age: int
    max_age: int
    # Тур виден на сайте и выезд ещё не начался.
    is_bookable: bool


@dataclass(frozen=True, kw_only=True)
class BookingRecord:
    """Состояние брони, нужное для оплаты и отмены."""

    id: UUID
    user_id: UUID
    departure_id: UUID
    seats: int
    prepayment_amount: Decimal
    status: BookingStatus
    expires_at: datetime
    payment_reference: str | None


class BookingRepository(Protocol):
    async def lock_departure(self, departure_id: UUID) -> DepartureForBooking | None:
        """Блокирует строку выезда до конца транзакции: брони на него идут по очереди."""
        ...

    async def seats_taken(self, departure_id: UUID, *, exclude: UUID | None = None) -> int: ...

    async def cancel_user_holds(self, user_id: UUID, departure_id: UUID, now: datetime) -> None:
        """Повторное нажатие «Купить» заменяет прежнее удержание, а не копит места."""
        ...

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
    ) -> None: ...

    async def get_for_update(self, booking_id: UUID) -> BookingRecord | None: ...

    async def get_by_reference_for_update(self, reference: str) -> BookingRecord | None: ...

    async def set_payment_reference(
        self, booking_id: UUID, provider: str, reference: str, now: datetime
    ) -> None: ...

    async def set_status(self, booking_id: UUID, status: BookingStatus, now: datetime) -> None: ...

    async def get(self, booking_id: UUID) -> BookingView | None: ...

    async def search(self, filters: BookingFilters, page: PageParams) -> Page[BookingView]: ...


@dataclass(frozen=True, kw_only=True)
class PaymentSession:
    reference: str
    # Страница оплаты провайдера, куда фронтенд отправляет пользователя.
    redirect_url: str


class PaymentGateway(Protocol):
    @property
    def provider(self) -> str: ...

    async def create_payment(
        self, *, booking_id: UUID, amount: Decimal, currency: str, expires_at: datetime
    ) -> PaymentSession: ...
