"""Pluggable auth dependency.

Phase 0 shipped a dev stub: it reads an ``X-Service-Identity`` header and yields a
``CallerIdentity``. The Agent Identity harness adds ``oauth2`` mode: ``require_identity``
verifies a Bearer JWT against the issuing ``identity-service``'s JWKS (signature, iss, aud, exp,
and max-delegation-depth) and yields the same ``CallerIdentity`` shape — new fields
(``mode``/``on_behalf_of``/``actor_chain``) default empty, so ``dev``-mode callers are unaffected.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import jwt
from fastapi import Depends, Request

from .config import PlatformSettings
from .errors import PlatformError

RequireIdentity = Callable[[Request], Awaitable["CallerIdentity"]]

# One PyJWKClient per JWKS URL, reused across requests — it caches keys itself and only refetches
# on an unrecognized ``kid`` (e.g. after key rotation).
_jwks_clients: dict[str, jwt.PyJWKClient] = {}


def _jwks_client(url: str) -> jwt.PyJWKClient:
    client = _jwks_clients.get(url)
    if client is None:
        client = jwt.PyJWKClient(url, cache_keys=True)
        _jwks_clients[url] = client
    return client


@dataclass
class CallerIdentity:
    """Who is making the call.

    In ``dev`` mode, ``id`` comes from the ``X-Service-Identity`` header. In ``oauth2`` mode,
    ``id`` is always the *immediate* caller — the agent's ``sub`` for an autonomous token, or the
    innermost actor's ``sub`` (``act.sub``) for a delegated one. ``on_behalf_of`` is the principal
    being acted for (delegated only); ``actor_chain`` is the full nested ``act`` claim, for Audit.
    """

    id: str
    kind: str = "service"
    scopes: list[str] = field(default_factory=list)
    mode: str = "autonomous"
    on_behalf_of: str | None = None
    actor_chain: dict | None = None


def make_require_identity(
    settings: PlatformSettings,
) -> RequireIdentity:
    """Build the ``require_identity`` FastAPI dependency for the given settings/auth mode."""

    async def require_identity(request: Request) -> CallerIdentity:
        if settings.auth_mode == "dev":
            return CallerIdentity(id=request.headers.get("x-service-identity", "anonymous"))
        if settings.auth_mode == "oauth2":
            return _verify_oauth2_bearer(request, settings)
        raise NotImplementedError(f"auth_mode={settings.auth_mode!r} is not implemented in Phase 0")

    return require_identity


def _verify_oauth2_bearer(request: Request, settings: PlatformSettings) -> CallerIdentity:
    if not settings.oauth2_jwks_url:
        raise RuntimeError("oauth2_jwks_url must be set when auth_mode='oauth2'")

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise PlatformError("unauthorized", "missing bearer token", status_code=401)
    token = auth_header.removeprefix("Bearer ")

    try:
        signing_key = _jwks_client(settings.oauth2_jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.oauth2_audience,
            issuer=settings.oauth2_issuer,
        )
    except jwt.InvalidTokenError as exc:
        raise PlatformError("unauthorized", f"invalid token: {exc}", status_code=401) from exc

    depth = int(claims.get("depth", 0))
    if depth > settings.max_delegation_depth:
        raise PlatformError("unauthorized", "delegation depth exceeded", status_code=401)

    scopes = claims.get("scope", "").split()
    mode = claims.get("mode", "autonomous")
    act = claims.get("act")
    if mode == "delegated" and act:
        return CallerIdentity(
            id=act["sub"],
            scopes=scopes,
            mode=mode,
            on_behalf_of=claims["sub"],
            actor_chain=act,
        )
    return CallerIdentity(id=claims["sub"], scopes=scopes, mode=mode)


def require_scope(
    require_identity: RequireIdentity, scope: str
) -> Callable[[CallerIdentity], Awaitable[CallerIdentity]]:
    """Build a dependency requiring ``scope`` on top of an existing ``require_identity``.

    Usage: ``require_widgets_read = require_scope(require_identity, "widgets:read")``, then
    ``Depends(require_widgets_read)`` in a route — composes via FastAPI's nested ``Depends``
    rather than re-deriving settings, so it works the same in ``dev`` and ``oauth2`` mode.
    """

    async def _require_scope(
        identity: CallerIdentity = Depends(require_identity),
    ) -> CallerIdentity:
        if scope not in identity.scopes:
            raise PlatformError("forbidden", f"missing required scope: {scope!r}", status_code=403)
        return identity

    return _require_scope
