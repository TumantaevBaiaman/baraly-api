from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.application.exceptions import (
    AccessDeniedError,
    AuthenticationError,
    ExternalServiceError,
)
from app.domain.exceptions import ConflictError, DomainError, NotFoundError


def _error_response(status_code: int, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": str(exc)})


async def _handle_domain_error(_: Request, exc: Exception) -> JSONResponse:
    return _error_response(status.HTTP_400_BAD_REQUEST, exc)


async def _handle_not_found(_: Request, exc: Exception) -> JSONResponse:
    return _error_response(status.HTTP_404_NOT_FOUND, exc)


async def _handle_conflict(_: Request, exc: Exception) -> JSONResponse:
    return _error_response(status.HTTP_409_CONFLICT, exc)


async def _handle_authentication_error(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": str(exc)},
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _handle_access_denied(_: Request, exc: Exception) -> JSONResponse:
    return _error_response(status.HTTP_403_FORBIDDEN, exc)


async def _handle_external_service_error(_: Request, exc: Exception) -> JSONResponse:
    return _error_response(status.HTTP_503_SERVICE_UNAVAILABLE, exc)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _handle_domain_error)
    app.add_exception_handler(NotFoundError, _handle_not_found)
    app.add_exception_handler(ConflictError, _handle_conflict)
    app.add_exception_handler(AuthenticationError, _handle_authentication_error)
    app.add_exception_handler(AccessDeniedError, _handle_access_denied)
    app.add_exception_handler(ExternalServiceError, _handle_external_service_error)
