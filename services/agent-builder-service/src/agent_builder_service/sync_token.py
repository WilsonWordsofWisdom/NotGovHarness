"""Plain synchronous token fetch for CrewAI's tool-call path.

CrewAI's `BaseTool._run()` is called synchronously from inside `Crew.kickoff()`'s own thread —
there is no running event loop to await `platform_core.llm_gateway_auth.TokenClient` (an async
class) from there. That async client is still used for the LLM leg (`crew_layer.py` awaits it
before constructing the `Crew`), but the tool leg needs its own minimal, synchronous
fetch-and-cache, mirroring the same safety-margin/expiry logic without `async`/`await`.
"""

from __future__ import annotations

import time

import httpx


class SyncTokenClient:
    def __init__(
        self, *, identity_url: str, client_id: str, client_secret: str, scope: str
    ) -> None:
        self._identity_url = identity_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def _fetch_token(self) -> str:
        with httpx.Client(base_url=self._identity_url, timeout=10.0) as client:
            resp = client.post(
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
        self._token_expires_at = time.monotonic() + body.get("expires_in", 300)
        return token

    def ensure_token(self) -> str:
        if self._token is not None and time.monotonic() < self._token_expires_at - 30:
            return self._token
        return self._fetch_token()

    def force_refresh(self) -> str:
        return self._fetch_token()
