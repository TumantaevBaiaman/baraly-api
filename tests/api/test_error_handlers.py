import pytest
from fastapi import FastAPI, status
from httpx import AsyncClient

from app.domain.exceptions import DomainError, NotFoundError


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (DomainError("rule broken"), status.HTTP_400_BAD_REQUEST),
        (NotFoundError("rule broken"), status.HTTP_404_NOT_FOUND),
    ],
)
async def test_domain_errors_map_to_http(
    app: FastAPI, client: AsyncClient, error: DomainError, expected_status: int
) -> None:
    async def failing_route() -> None:
        raise error

    app.add_api_route("/failing", failing_route)

    response = await client.get("/failing")

    assert response.status_code == expected_status
    assert response.json() == {"detail": "rule broken"}
