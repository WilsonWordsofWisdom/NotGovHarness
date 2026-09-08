"""Token client for authenticating mem0-service to llm-gateway as a platform caller.

D-062: the first harness where a platform service calls *another* platform service, rather than
an external caller hitting a harness (D-030) or a façade wrapping an external tool's own auth
(D-043). identity-service's access tokens are short-lived (`access_token_ttl_seconds = 300`,
checked before assuming a one-time fetch would work) — this client proactively refreshes well
before expiry, same shape as `ContextForgeClient`'s refresh handling (D-046), but pure token
management: mem0-service's actual HTTP calls to llm-gateway are made by Mem0's own internal
`openai` SDK client (see `mem0_layer.py`), not by this client.
"""

from __future__ import annotations

import time

import httpx


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

        Real finding, live: `time.monotonic()`-based expiry tracking can under-detect elapsed
        time when Docker Desktop suspends/throttles its VM's clock during host idle periods — a
        token genuinely expired by wall-clock time (checked by identity-service's JWT
        verification) can still look unexpired from this process's own monotonic clock. This is
        the reactive backstop `Mem0Layer` calls on an auth failure, same shape as
        `ContextForgeClient`'s retry-on-401 (D-046), needed here for a reason D-046 didn't
        anticipate.
        """
        return await self._fetch_token()
