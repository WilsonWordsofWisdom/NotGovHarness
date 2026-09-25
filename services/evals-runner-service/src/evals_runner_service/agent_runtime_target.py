"""A real agent-runtime target (D-089): one deployed `agent-runtime` container's `POST /invoke`
per golden, producing `actual_output` — closes D-077's deferred non-goal now that something real
exists to call, rather than only ever grading `llm-gateway` directly.
"""

from __future__ import annotations

import httpx

from platform_core.llm_gateway_auth import TokenClient, call_with_auth_retry


class AgentRuntimeTarget:
    def __init__(self, url: str, token_client: TokenClient) -> None:
        self._client = httpx.AsyncClient(base_url=url, timeout=120.0)
        self._token_client = token_client

    async def complete(self, prompt: str) -> str:
        headers: dict[str, str] = {}

        def apply_token(token: str) -> None:
            headers["Authorization"] = f"Bearer {token}"

        async def call() -> str:
            resp = await self._client.post("/invoke", json={"input": prompt}, headers=headers)
            resp.raise_for_status()
            return resp.json()["output"]

        return await call_with_auth_retry(self._token_client, apply_token, call)
