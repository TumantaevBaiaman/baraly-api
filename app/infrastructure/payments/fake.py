"""Тестовый платёжный провайдер для разработки: деньги не списываются.

Оплата «проходит», когда вызывается POST /api/v1/payments/fake/{reference}/complete.
В production такой провайдер запрещён настройками."""

import secrets
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.application.ports.bookings import PaymentSession


class FakePaymentGateway:
    def __init__(self, public_api_url: str) -> None:
        self._public_api_url = public_api_url.rstrip("/")

    @property
    def provider(self) -> str:
        return "fake"

    async def create_payment(
        self, *, booking_id: UUID, amount: Decimal, currency: str, expires_at: datetime
    ) -> PaymentSession:
        reference = f"fake_{secrets.token_urlsafe(16)}"
        return PaymentSession(
            reference=reference,
            redirect_url=f"{self._public_api_url}/api/v1/payments/fake/{reference}/complete",
        )
