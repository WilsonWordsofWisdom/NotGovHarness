"""Wraps `mcp-gateway-registry`'s `POST /tools/{name}/call` as a CrewAI `BaseTool` (D-072).

Deliberately not `crewai-tools`' `MCPServerAdapter` — that connects straight to an MCP server,
bypassing this platform's identity/scope model entirely, the exact seam D-043 built
`mcp-gateway-registry` (then named Agent Gateway, D-105) to close. This tool goes through the
same REST surface any other authenticated caller uses.
"""

from __future__ import annotations

from typing import Any

import httpx
from crewai.tools import BaseTool
from pydantic import BaseModel

from .sync_token import SyncTokenClient

_AUTH_FAILURE_MARKERS = ("401", "unauthorized", "signature has expired")


class _NoArgsSchema(BaseModel):
    pass


class McpGatewayRegistryTool(BaseTool):
    """Calls one specific federated tool behind `mcp-gateway-registry`, by its federated name."""

    name: str
    description: str
    args_schema: type[BaseModel] = _NoArgsSchema

    mcp_gateway_registry_url: str
    federated_tool_name: str
    token_client: SyncTokenClient

    def _call(self, token: str) -> httpx.Response:
        with httpx.Client(base_url=self.mcp_gateway_registry_url, timeout=15.0) as client:
            return client.post(
                f"/tools/{self.federated_tool_name}/call",
                json={"arguments": {}},
                headers={"Authorization": f"Bearer {token}"},
            )

    def _run(self, **_kwargs: Any) -> str:
        token = self.token_client.ensure_token()
        resp = self._call(token)
        if resp.status_code == 401 or any(
            marker in resp.text.lower() for marker in _AUTH_FAILURE_MARKERS
        ):
            token = self.token_client.force_refresh()
            resp = self._call(token)
        resp.raise_for_status()
        return str(resp.json())
