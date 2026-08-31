"""mcp-skills-demo: a minimal MCP server exposing one tool, `list_skills`, wrapping Skill
Registry's `GET /skills`.

Exists purely so the Agent Gateway harness has something real to federate and prove the chain
against (D-044) — not a general-purpose reference MCP server. No identity gating: ContextForge
calls this directly (D-043 keeps our identity-service out of the ContextForge<->tool hop).
"""

from __future__ import annotations

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from platform_core.config import PlatformSettings


class Settings(PlatformSettings):
    service_name: str = "mcp-skills-demo"
    skill_registry_url: str = "http://localhost:8093"


settings = Settings()
server = MCPServer("mcp-skills-demo")


@server.tool()
async def list_skills() -> list[dict]:
    """List every skill currently published in the Skill Registry (name + description)."""
    async with httpx.AsyncClient(base_url=settings.skill_registry_url, timeout=10.0) as client:
        resp = await client.get("/skills")
        resp.raise_for_status()
        return resp.json()


# DNS-rebinding protection validates the incoming Host header against an allowlist — this
# server is only ever reached over the compose-internal Docker network (never the public
# internet, not even fronted by Traefik), and ContextForge connects via the Docker network's
# service-name hostname (e.g. "mcp-skills-demo:8000"), not "localhost" — found live (a real
# request came back 421 Misdirected Request until this was disabled). Disabling is a reasonable
# trade-off for a reference-platform-internal demo service; a real deployment would allowlist
# the real hostname instead.
app = server.streamable_http_app(
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
)
