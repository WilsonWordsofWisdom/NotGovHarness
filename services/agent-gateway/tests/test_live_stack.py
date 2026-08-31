"""Skip-if-down: the actual end-to-end flow this harness exists to prove.

A real bearer token from a running identity-service registers the real mcp-skills-demo MCP
server with a real running ContextForge, through a real running agent-gateway façade, then
calls its `list_skills` tool through that same façade and gets back real Skill Registry data —
not a mock, not a container-is-up check. Steps 1-4's tests already proved each hop in isolation
(ContextForge itself, the façade against a stub ContextForge, the MCP server against a real
Skill Registry); this is the one that proves the whole chain, live.
"""

from __future__ import annotations

import httpx

MCP_SKILLS_DEMO_URL = "http://mcp-skills-demo:8000/mcp"
# Fixed, not unique-per-run: ContextForge dedupes gateway registration by URL, not name (found
# live) — a second registration attempt at the same URL 502s as "Gateway already exists"
# regardless of what name is used. So registration here is idempotent-by-design: already
# registered from a prior run is a pass, not a failure, since the actual point of this test is
# the tool call, not proving registration is repeatable.
GATEWAY_NAME = "live-test-mcp-skills-demo"


async def _call_scoped_token(identity_url: str) -> str:
    async with httpx.AsyncClient(base_url=identity_url) as client:
        resp = await client.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "example-service",
                "client_secret": "example-service-dev-secret",
                "scope": "agent_gateway:call",
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def test_a_real_tool_call_routes_through_contextforge_to_real_skill_registry_data(
    platform_identity_url, platform_agent_gateway_url
):
    token = await _call_scoped_token(platform_identity_url)
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(base_url=platform_agent_gateway_url, timeout=15.0) as client:
        registered = await client.post(
            "/mcp-servers",
            json={"name": GATEWAY_NAME, "url": MCP_SKILLS_DEMO_URL, "transport": "STREAMABLEHTTP"},
            headers=headers,
        )
        already_registered = registered.status_code == 502 and "already exists" in registered.text
        assert registered.status_code == 201 or already_registered, registered.text

        tools = await client.get("/tools", headers=headers)
        assert tools.status_code == 200
        matches = [t for t in tools.json() if t.get("originalName") == "list_skills"]
        assert matches, f"list_skills not found in {tools.json()}"
        # ContextForge federates a tool under "{gateway_slug}-{tool_name}", not the tool's own
        # bare name (found live) — the caller has to use the federated name from GET /tools.
        federated_name = matches[0]["name"]

        called = await client.post(
            f"/tools/{federated_name}/call", json={"arguments": {}}, headers=headers
        )
        assert called.status_code == 200
        body = called.json()
        assert body.get("result", {}).get("isError") is False
        # Whatever is actually in Skill Registry right now — this proves the data is real and
        # flowed through the whole chain, not that any specific skill exists.
        assert len(body["result"]["content"]) > 0
