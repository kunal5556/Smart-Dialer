import logging
import uuid

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.logging_config import log_event
from app.state_machines.errors import InvalidStateTransition, UnauthorizedTransitionActor

logger = logging.getLogger(__name__)

ERROR_NOT_FOUND = "not_found"
ERROR_CONFLICT = "conflict"
ERROR_VALIDATION = "validation_error"
ERROR_UNAUTHORIZED = "unauthorized"
ERROR_INTERNAL = "internal_error"
ERROR_RATE_LIMITED = "rate_limited"


class ApiError(Exception):
    def __init__(self, code: str, message: str, status_code: int, details: dict | None = None):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


class NotFoundError(ApiError):
    def __init__(self, resource: str, identifier: str) -> None:
        super().__init__(
            code=ERROR_NOT_FOUND,
            message=f"{resource} {identifier} was not found",
            status_code=status.HTTP_404_NOT_FOUND,
            details={"resource": resource, "id": identifier},
        )


class ConflictError(ApiError):
    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(
            code=ERROR_CONFLICT,
            message=message,
            status_code=status.HTTP_409_CONFLICT,
            details=details,
        )


class UnauthorizedError(ApiError):
    def __init__(self, message: str = "A valid X-API-Key header is required") -> None:
        super().__init__(
            code=ERROR_UNAUTHORIZED,
            message=message,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )


class RateLimitedError(ApiError):
    def __init__(self, retry_after_seconds: float) -> None:
        super().__init__(
            code=ERROR_RATE_LIMITED,
            message="This endpoint is rate limited, try again shortly",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            details={"retry_after_seconds": round(retry_after_seconds, 2)},
        )


def error_body(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, error: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content=error_body(error.code, error.message, error.details),
        )

    @app.exception_handler(InvalidStateTransition)
    async def handle_invalid_transition(
        request: Request, error: InvalidStateTransition
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=error_body(
                ERROR_CONFLICT,
                str(error),
                {"from": error.current, "to": error.target, "actor": error.actor},
            ),
        )

    @app.exception_handler(UnauthorizedTransitionActor)
    async def handle_unauthorized_actor(
        request: Request, error: UnauthorizedTransitionActor
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=error_body(
                ERROR_CONFLICT,
                str(error),
                {"from": error.current, "to": error.target, "actor": error.actor},
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_body(ERROR_VALIDATION, "Request validation failed", {"errors": error.errors()}),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
        correlation_id = uuid.uuid4().hex
        log_event(
            logger,
            logging.ERROR,
            "api_unhandled_error",
            f"Unhandled error {correlation_id} on {request.url.path}: {error}",
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_body(
                ERROR_INTERNAL,
                "The server hit an unexpected error",
                {"correlation_id": correlation_id},
            ),
        )
