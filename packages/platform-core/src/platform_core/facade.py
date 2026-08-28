"""Helpers for façade services that wrap an upstream OSS service behind our contract.

An ``UpstreamClient`` forwards requests to the upstream over httpx (auto-instrumented by OTel, so
W3C trace context propagates on every hop) and forwards the caller's identity. Given an
``svid_source``, it also calls over mTLS and verifies the upstream's peer SPIFFE ID — the
server-side half (upstream requiring + validating *this* client's cert) is wired separately via
uvicorn's own TLS flags; see ``svid.py``'s module docstring for why.
``raise_for_upstream`` re-exposes upstream failures under the platform error envelope.
"""

from __future__ import annotations

import ssl
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import FastAPI

from .auth import CallerIdentity
from .errors import PlatformError
from .logging import get_logger

if TYPE_CHECKING:
    from spiffe import X509Source

log = get_logger("platform_core.facade")


class UpstreamPeerIdentityError(Exception):
    """The upstream's mTLS peer certificate didn't carry the expected SPIFFE ID."""


class UpstreamClient:
    """Thin async httpx wrapper pointed at one upstream service."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        svid_source: X509Source | None = None,
        expected_peer_spiffe_id: str | None = None,
    ) -> None:
        verify: bool | ssl.SSLContext = True
        if svid_source is not None:
            from .svid import build_client_ssl_context

            verify = build_client_ssl_context(svid_source)
            # Callers pass one canonical http:// URL (e.g. from a compose env var); mTLS implies
            # https, so that's this client's call to make, not something compose needs to know.
            base_url = str(httpx.URL(base_url).copy_with(scheme="https"))
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=timeout, transport=transport, verify=verify
        )
        self._expected_peer_spiffe_id = expected_peer_spiffe_id

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
        response = await self._client.request(method, path, headers=merged, **kwargs)
        if self._expected_peer_spiffe_id is not None:
            self._verify_peer(response)
        return response

    def _verify_peer(self, response: httpx.Response) -> None:
        from .svid import peer_spiffe_id

        network_stream = response.extensions.get("network_stream")
        ssl_object = network_stream.get_extra_info("ssl_object") if network_stream else None
        if ssl_object is None:
            raise UpstreamPeerIdentityError("no TLS connection to verify a peer SPIFFE ID on")
        actual = str(peer_spiffe_id(ssl_object.getpeercert(True)))
        if actual != self._expected_peer_spiffe_id:
            raise UpstreamPeerIdentityError(
                f"upstream peer SPIFFE ID {actual!r} != expected {self._expected_peer_spiffe_id!r}"
            )
        # The audit-relevant proof this mTLS hop is who it claims to be — see the harness spec's
        # reference flow ("logs + one Jaeger trace show spiffe_id, sub, and the act chain").
        log.info("upstream_peer_verified", spiffe_id=actual)


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
