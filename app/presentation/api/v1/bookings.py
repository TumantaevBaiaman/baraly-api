from uuid import UUID

from fastapi import APIRouter, status

from app.presentation.api.dependencies import (
    CancelBookingDep,
    ConfirmPaymentDep,
    CurrentUserDep,
    PayForBookingDep,
    ReserveSeatsDep,
    ViewBookingsDep,
)
from app.presentation.api.v1.schemas import (
    AdminBookingSearchQueryDep,
    BookingRequest,
    BookingResponse,
    BookingSearchQueryDep,
    CompanyBookingResponse,
    PageResponse,
    PaymentStartResponse,
)

router = APIRouter(tags=["bookings"])


@router.post("/bookings", status_code=status.HTTP_201_CREATED)
async def reserve(
    body: BookingRequest, user: CurrentUserDep, use_case: ReserveSeatsDep
) -> BookingResponse:
    """Нажатие «Купить»: места удерживаются до expires_at, затем нужно оплатить."""
    booking = await use_case.execute(
        user, body.departure_id, [item.to_traveler() for item in body.travelers]
    )
    return BookingResponse.model_validate(booking)


@router.get("/bookings/{booking_id}")
async def get_booking(
    booking_id: UUID, user: CurrentUserDep, use_case: ViewBookingsDep
) -> BookingResponse:
    return BookingResponse.model_validate(await use_case.get_mine(user, booking_id))


@router.post("/bookings/{booking_id}/pay")
async def pay(
    booking_id: UUID, user: CurrentUserDep, use_case: PayForBookingDep
) -> PaymentStartResponse:
    started = await use_case.execute(user, booking_id)
    return PaymentStartResponse(
        booking=BookingResponse.model_validate(started.booking),
        redirect_url=started.redirect_url,
    )


@router.post("/bookings/{booking_id}/cancel")
async def cancel(
    booking_id: UUID, user: CurrentUserDep, use_case: CancelBookingDep
) -> BookingResponse:
    """Отмена неоплаченной брони сразу возвращает места."""
    return BookingResponse.model_validate(await use_case.execute(user, booking_id))


@router.get("/users/me/bookings", tags=["users"])
async def list_my_bookings(
    query: BookingSearchQueryDep, user: CurrentUserDep, use_case: ViewBookingsDep
) -> PageResponse[BookingResponse]:
    page = await use_case.list_mine(user, query.to_filters(), query.to_params())
    return PageResponse[BookingResponse].model_validate(page)


@router.get("/company/{operator_id}/bookings", tags=["company"])
async def list_company_bookings(
    operator_id: UUID,
    query: BookingSearchQueryDep,
    user: CurrentUserDep,
    use_case: ViewBookingsDep,
) -> PageResponse[CompanyBookingResponse]:
    page = await use_case.list_for_company(user, operator_id, query.to_filters(), query.to_params())
    return PageResponse[CompanyBookingResponse].model_validate(page)


@router.get("/admin/bookings", tags=["admin"])
async def list_all_bookings(
    query: AdminBookingSearchQueryDep, user: CurrentUserDep, use_case: ViewBookingsDep
) -> PageResponse[CompanyBookingResponse]:
    page = await use_case.list_all(user, query.to_admin_filters(), query.to_params())
    return PageResponse[CompanyBookingResponse].model_validate(page)


fake_payments_router = APIRouter(tags=["payments (dev)"])


@fake_payments_router.post("/payments/fake/{reference}/complete")
async def complete_fake_payment(reference: str, use_case: ConfirmPaymentDep) -> BookingResponse:
    """Только для разработки: имитирует успешную оплату у провайдера."""
    return BookingResponse.model_validate(await use_case.execute(reference))
