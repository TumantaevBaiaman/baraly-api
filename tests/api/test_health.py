from fastapi import FastAPI, status
from httpx import AsyncClient

from app.application.use_cases.check_readiness import CheckReadiness
from app.presentation.api.dependencies import get_check_readiness


class FakeDatabaseProbe:
    def __init__(self, available: bool) -> None:
        self._available = available

    async def is_available(self) -> bool:
        return self._available


def use_database(app: FastAPI, *, available: bool) -> None:
    app.dependency_overrides[get_check_readiness] = lambda: CheckReadiness(
        FakeDatabaseProbe(available)
    )


async def test_liveness(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"status": "ok"}


async def test_ready_when_database_available(app: FastAPI, client: AsyncClient) -> None:
    use_database(app, available=True)

    response = await client.get("/health/ready")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"status": "ok"}


async def test_not_ready_when_database_unavailable(app: FastAPI, client: AsyncClient) -> None:
    use_database(app, available=False)

    response = await client.get("/health/ready")

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json() == {"status": "unavailable"}
