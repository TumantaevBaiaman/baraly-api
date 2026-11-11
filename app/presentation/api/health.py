from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app.presentation.api.dependencies import CheckReadinessDep

router = APIRouter(prefix="/health", tags=["health"])


class HealthResponse(BaseModel):
    status: str


@router.get("")
async def liveness() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/ready",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse}},
)
async def readiness(use_case: CheckReadinessDep, response: Response) -> HealthResponse:
    if await use_case.execute():
        return HealthResponse(status="ok")
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(status="unavailable")
