"""Standard JSON error envelope: ``{"error": {code, message, detail, trace_id}}``."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .context import current_trace_id


class PlatformError(Exception):
    """Raise for expected, client-facing failures rendered under the error envelope."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail


def _envelope(code: str, message: str, detail: Any = None, status_code: int = 400) -> JSONResponse:
    body = {
        "error": {
            "code": code,
            "message": message,
            "detail": jsonable_encoder(detail) if detail is not None else None,
            "trace_id": current_trace_id(),
        }
    }
    return JSONResponse(status_code=status_code, content=body)


def install_error_handlers(app: FastAPI) -> None:
    """Register handlers that render PlatformError, validation errors, and unexpected errors."""

    @app.exception_handler(PlatformError)
    async def _platform_error(_request: Request, exc: PlatformError) -> JSONResponse:
        return _envelope(exc.code, exc.message, exc.detail, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return _envelope("validation_error", "Request validation failed", exc.errors(), 422)

    @app.exception_handler(Exception)
    async def _unhandled_error(_request: Request, _exc: Exception) -> JSONResponse:
        return _envelope("internal_error", "Internal server error", None, 500)
