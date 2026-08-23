"""Helpers for façade services that wrap an upstream OSS service behind our contract.

An ``UpstreamClient`` forwards requests to the upstream over httpx (auto-instrumented by OTel, so
W3C trace context propagates on every hop) and forwards the caller's identity.
``raise_for_upstream`` re-exposes upstream failures under the platform error envelope.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI

from .auth import CallerIdentity
from .errors import PlatformError


class UpstreamClient:
    """Thin async httpx wrapper pointed at one upstream service."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def forward(
        self,
        method: str,
        path: str,
        *,
        identity: CallerIdentity | None = None,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Forward a request upstream, propagating identity (trace context is auto-injected)."""
        merged = dict(headers or {})
        if identity is not None:
            merged["x-service-identity"] = identity.id
        return await self._client.request(method, path, headers=merged, **kwargs)


def raise_for_upstream(response: httpx.Response) -> httpx.Response:
    """Map a non-2xx upstream response to a PlatformError under our envelope."""
    if response.is_success:
        return response
    detail: Any
    try:
        detail = response.json()
    except ValueError:
        detail = response.text
    raise PlatformError(
        "upstream_error",
        f"Upstream returned {response.status_code}",
        status_code=502,
        detail=detail,
    )


def lifespan_hook(
    client: UpstreamClient,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Build a lifespan hook that closes the upstream client on shutdown."""

    @asynccontextmanager
    async def hook(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await client.aclose()

    return hook
