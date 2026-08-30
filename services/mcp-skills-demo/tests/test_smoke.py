"""Infra-free: the MCP Starlette app builds and exposes its one tool. No `/healthz` here —
unlike the platform-core-shaped services, this app is `MCPServer.streamable_http_app()`, not
`create_app()`, so it doesn't get that endpoint for free.
"""

from __future__ import annotations

from mcp_skills_demo.main import app, server


def test_app_builds():
    assert app is not None


async def test_list_skills_tool_is_registered():
    tools = await server.list_tools()
    assert [t.name for t in tools] == ["list_skills"]
