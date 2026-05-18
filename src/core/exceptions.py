"""
Custom exceptions and FastAPI error handlers.

Every exception that this service can raise is defined here so that error
handling is consistent and centralised. The ``register_error_handlers``
function wires these into the FastAPI app at startup so every failure —
expected or not — returns the same JSON envelope.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


# ── Custom exception hierarchy ──────────────────────────────────────────────


class SmsServiceError(Exception):
    """Base exception for all SMS-service errors.

    Every custom exception in this service inherits from this class so
    callers can ``except SmsServiceError`` to catch anything we raise.
    """

    def __init__(self, message: str = "An internal error occurred.", status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)


class TokenServiceUnavailableError(SmsServiceError):
    """Raised when the sibling token service is unreachable or returns garbage.

    Network failures, non-2xx responses, and JSON-shape errors all map here.
    """

    def __init__(self, message: str = "Token service is unavailable."):
        super().__init__(message=message, status_code=503)


class BeetextingProviderError(SmsServiceError):
    """Raised when BEEtexting itself returns a non-2xx response or unexpected payload."""

    def __init__(self, message: str = "BEEtexting provider returned an error."):
        super().__init__(message=message, status_code=502)


class BatchNotFoundError(SmsServiceError):
    """Raised when a status lookup references a batch_id that does not exist.

    Rendered as a 404 by the shared ``SmsServiceError`` handler, so no extra
    handler wiring is needed.
    """

    def __init__(self, message: str = "Batch not found."):
        super().__init__(message=message, status_code=404)


# ── FastAPI error handlers ──────────────────────────────────────────────────


def _build_error_body(status_code: int, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": status_code,
            "message": message,
        },
    }


def register_error_handlers(app: FastAPI) -> None:
    """Attach error handlers to the FastAPI application.

    Ensures that every response — even unexpected crashes and request
    validation failures — returns the same JSON envelope.
    """

    @app.exception_handler(SmsServiceError)
    async def _handle_sms_service_error(
        _request: Request, exc: SmsServiceError
    ) -> JSONResponse:
        logger.error("SmsServiceError: %s (status=%d)", exc.message, exc.status_code)
        return JSONResponse(
            status_code=exc.status_code,
            content=_build_error_body(exc.status_code, exc.message),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Surface the first validation issue in the message; full details in logs.
        logger.warning("Request validation failed: %s", exc.errors())
        first = exc.errors()[0] if exc.errors() else {"msg": "Invalid request."}
        location = ".".join(str(p) for p in first.get("loc", []))
        message = f"{location}: {first.get('msg', 'Invalid request.')}".strip(": ")
        return JSONResponse(
            status_code=422,
            content=_build_error_body(422, message),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content=_build_error_body(500, "An unexpected internal error occurred."),
        )
