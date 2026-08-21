"""Pluggable auth dependency.

Phase 0 ships a dev stub: it reads an ``X-Service-Identity`` header and yields a
``CallerIdentity``. This is the seam the Agent Identity harness (SPIFFE/SPIRE + OAuth2) replaces
later — the ``CallerIdentity`` type and ``require_identity`` dependency stay stable; only the
implementation changes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from fastapi import Request

from .config import PlatformSettings


@dataclass
class CallerIdentity:
    """Who is making the call. In dev, ``id`` comes from the ``X-Service-Identity`` header."""

    id: str
    kind: str = "service"
    scopes: list[str] = field(default_factory=list)


def make_require_identity(
    settings: PlatformSettings,
) -> Callable[[Request], Awaitable[CallerIdentity]]:
    """Build the ``require_identity`` FastAPI dependency for the given settings/auth mode."""

    async def require_identity(request: Request) -> CallerIdentity:
        if settings.auth_mode == "dev":
            return CallerIdentity(id=request.headers.get("x-service-identity", "anonymous"))
        raise NotImplementedError(f"auth_mode={settings.auth_mode!r} is not implemented in Phase 0")

    return require_identity
