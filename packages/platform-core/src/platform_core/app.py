"""The app factory every service uses.

``create_app`` returns a FastAPI app wired with structured logging, tracing, the error
envelope, a request-context middleware, and ``/healthz`` + ``/readyz`` endpoints. Services pass
readiness checks (e.g. DB/broker pings) and lifespan hooks (e.g. opening pools) as they need them.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from .config import PlatformSettings
from .errors import install_error_handlers
from .logging import configure_logging
from .otel import configure_tracing

ReadinessCheck = Callable[[], Awaitable[None]]
"""An async callable that raises if the dependency it checks is not ready."""

LifespanHook = Callable[[FastAPI], AbstractAsyncContextManager[Any]]
"""An async context manager opened on startup and closed on shutdown (pools, consumers, ...)."""


def create_app(
    settings: PlatformSettings,
    *,
    readiness_checks: Sequence[ReadinessCheck] | None = None,
    lifespan_hooks: Sequence[LifespanHook] | None = None,
) -> FastAPI:
    configure_logging(settings.log_level)
    checks = list(readiness_checks or [])
    hooks = list(lifespan_hooks or [])

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with AsyncExitStack() as stack:
            for hook in hooks:
                await stack.enter_async_context(hook(app))
            yield

    app = FastAPI(title=settings.service_name, lifespan=lifespan)
    configure_tracing(settings, app)
    install_error_handlers(app)

    @app.middleware("http")
    async def _request_context(request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        response.headers["x-request-id"] = request_id
        return response

    @app.get("/healthz", tags=["platform"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["platform"])
    async def readyz() -> Any:
        failures: list[str] = []
        for check in checks:
            try:
                await check()
            except Exception as exc:  # noqa: BLE001 - surface any dependency failure as not-ready
                failures.append(str(exc))
        if failures:
            return JSONResponse(
                status_code=503, content={"status": "not_ready", "checks": failures}
            )
        return {"status": "ready"}

    return app
