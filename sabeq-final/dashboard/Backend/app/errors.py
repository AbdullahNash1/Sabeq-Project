"""Error envelope (API_CONTRACT §1.1). No response may use FastAPI's {"detail": ...}."""
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.models import ErrorBody, ErrorEnvelope
from app.state.machine import InvalidOutcome, InvalidTransition

log = logging.getLogger(__name__)

# Default `code` per status when an HTTPException is raised without one.
_STATUS_CODES = {
    400: "bad_request",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    503: "service_unavailable",
}


class ApiError(Exception):
    """Raise from routes/services with a contract error code, e.g. ApiError(503, "drone_unavailable", ...)."""

    def __init__(self, status_code: int, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def error_response(
    status_code: int, code: str, message: str, details: "dict[str, Any] | None" = None
) -> JSONResponse:
    body = ErrorEnvelope(error=ErrorBody(code=code, message=message, details=details or {}))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message, exc.details)


async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
        for e in exc.errors()
    ]
    return error_response(400, "bad_request", "Request validation failed", {"errors": errors})


async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _STATUS_CODES.get(exc.status_code, "http_error")
    return error_response(exc.status_code, code, str(exc.detail))


async def _invalid_transition(_: Request, exc: InvalidTransition) -> JSONResponse:
    return error_response(
        409, "invalid_transition", str(exc), {"state": exc.state.value, "action": exc.action}
    )


async def _invalid_outcome(_: Request, exc: InvalidOutcome) -> JSONResponse:
    return error_response(400, "bad_request", str(exc))


async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
    log.exception("Unhandled error", exc_info=exc)
    return error_response(500, "internal_error", "Internal server error")


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(InvalidTransition, _invalid_transition)
    app.add_exception_handler(InvalidOutcome, _invalid_outcome)
    app.add_exception_handler(Exception, _unhandled)
