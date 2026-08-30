"""Thin async client for ContextForge's own REST API.

Authenticates with ContextForge's native admin credential (email/password login, D-043) — a
backend-only secret this façade holds so it can call ContextForge, never something a caller of
this façade sees or needs. Not `platform_core.facade.UpstreamClient`: that's shaped for calling
*our own* SPIFFE-trust-domain services with mTLS; ContextForge is a third-party container with
its own auth scheme, so a small dedicated client fits better than force-fitting it into that.
"""

from __future__ import annotations

import time
from typing import Any

import httpx


class ContextForgeError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"ContextForge error {status_code}: {detail}")


class ContextForgeClient:
    def __init__(self, base_url: str, admin_email: str, admin_password: str) -> None:
        self._base_url = base_url
        self._admin_email = admin_email
        self._admin_password = admin_password
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    async def _ensure_token(self, client: httpx.AsyncClient) -> str:
        # 30s safety margin so a token doesn't expire mid-request.
        if self._token is not None and time.monotonic() < self._token_expires_at - 30:
            return self._token
        resp = await client.post(
            "/auth/email/login",
            json={"email": self._admin_email, "password": self._admin_password},
        )
        resp.raise_for_status()
        body = resp.json()
        token: str = body["access_token"]
        self._token = token
        self._token_expires_at = time.monotonic() + body.get("expires_in", 1200)
        return token

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=10.0) as client:
            token = await self._ensure_token(client)
            resp = await client.request(
                method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs
            )
        if resp.status_code >= 400:
            raise ContextForgeError(resp.status_code, resp.text)
        return resp

    async def register_gateway(self, name: str, url: str, transport: str = "SSE") -> dict[str, Any]:
        resp = await self._request(
            "POST", "/gateways", json={"name": name, "url": url, "transport": transport}
        )
        return resp.json()

    async def list_gateways(self) -> list[dict[str, Any]]:
        resp = await self._request("GET", "/gateways")
        return resp.json()

    async def list_tools(self) -> list[dict[str, Any]]:
        resp = await self._request("GET", "/tools")
        return resp.json()

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        resp = await self._request(
            "POST",
            "/rpc",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
        return resp.json()
