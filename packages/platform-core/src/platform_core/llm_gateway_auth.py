"""Shared auth client for a platform service calling `llm-gateway` as itself.

D-062: the first harness where a platform service calls *another* platform service, rather than
an external caller hitting a harness (D-030) or a façade wrapping an external tool's own auth
(D-043). identity-service's access tokens are short-lived (`access_token_ttl_seconds = 300`,
checked before assuming a one-time fetch would work) — this client proactively refreshes well
before expiry, same shape as `ContextForgeClient`'s refresh handling (D-046).

D-067: originally built inside `mem0_service` (D-062/D-063), promoted here once Knowledge/RAG
needed the identical shape of call — re-deriving the pattern per-harness without reusing the code
would have meant a second, independently-maintained copy of D-063's load-bearing reactive-retry
fix.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

import httpx

try:
    import openai

    _AuthenticationError: type[Exception] | None = openai.AuthenticationError
except ImportError:  # pragma: no cover - openai isn't every caller's dependency
    _AuthenticationError = None

# A caller's LLM SDK may wrap the underlying auth failure into its own generic exception, losing
# the original type but keeping this substring in the message — confirmed live for Mem0 (D-063):
# "Error code: 401 - {'error': {'code': 'unauthorized', ... 'Signature has expired' ...}}".
_AUTH_FAILURE_MARKERS = ("401", "unauthorized", "signature has expired")


class TokenClient:
    def __init__(
        self,
        *,
        identity_url: str,
        client_id: str,
        client_secret: str,
        scope: str = "llm_gateway:call",
    ) -> None:
        self._identity_url = identity_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    async def _fetch_token(self) -> str:
        async with httpx.AsyncClient(base_url=self._identity_url, timeout=10.0) as client:
            resp = await client.post(
                "/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "scope": self._scope,
                },
            )
            resp.raise_for_status()
            body = resp.json()
        token: str = body["access_token"]
        self._token = token
        # 30s safety margin on a 300s TTL — proactive refresh, not just reactive-on-401, since a
        # token this short-lived would otherwise fail requests unnecessarily often.
        self._token_expires_at = time.monotonic() + body.get("expires_in", 300)
        return token

    async def ensure_token(self) -> str:
        if self._token is not None and time.monotonic() < self._token_expires_at - 30:
            return self._token
        return await self._fetch_token()

    async def force_refresh(self) -> str:
        """Bypass the cache entirely and fetch a new token.

        Real finding, live (D-063): `time.monotonic()`-based expiry tracking can under-detect
        elapsed time when Docker Desktop suspends/throttles its VM's clock during host idle
        periods — a token genuinely expired by wall-clock time (checked by identity-service's JWT
        verification) can still look unexpired from this process's own monotonic clock. This is
        the reactive backstop `call_with_auth_retry` falls back to on an auth failure, same shape
        as `ContextForgeClient`'s retry-on-401 (D-046), needed here for a reason D-046 didn't
        anticipate.
        """
        return await self._fetch_token()


def looks_like_auth_failure(exc: Exception) -> bool:
    if _AuthenticationError is not None and isinstance(exc, _AuthenticationError):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _AUTH_FAILURE_MARKERS)


async def call_with_auth_retry[T](
    token_client: TokenClient,
    apply_token: Callable[[str], Awaitable[None]] | Callable[[str], None],
    call: Callable[[], Awaitable[T]],
) -> T:
    """Proactive refresh first, then one reactive retry on an auth-shaped failure.

    `apply_token` pushes a fresh token into whatever SDK object the caller holds (each library's
    client shapes this differently — e.g. `memory.llm.client.api_key = token` for Mem0,
    `llm.api_key = token` for LlamaIndex's `OpenAILike`), so it's supplied by the caller rather
    than assumed here.
    """
    token = await token_client.ensure_token()
    result = apply_token(token)
    if isinstance(result, Awaitable):
        await result
    try:
        return await call()
    except Exception as exc:
        if not looks_like_auth_failure(exc):
            raise
        token = await token_client.force_refresh()
        result = apply_token(token)
        if isinstance(result, Awaitable):
            await result
        return await call()
