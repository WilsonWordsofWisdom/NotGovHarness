"""Skip-if-down: mcp-skills-demo's `list_skills` tool against a real running Skill Registry,
called through the real `mcp` client SDK over streamable-HTTP — the same round trip verified
manually while building this (D-044): a standard MCP client, not a raw HTTP request, actually
gets real Skill Registry data back through the MCP protocol.

Runs the server itself in a background thread (uvicorn), since there's no lighter-weight
in-process transport for streamable-HTTP the way TestClient gives FastAPI services.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import threading
import time

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from platform_testing.fixtures import _reachable

SKILL_REGISTRY_URL = "http://localhost:8093"
SERVER_PORT = 9501


@pytest.fixture
def running_server(monkeypatch):
    if not _reachable("localhost", 8093):
        pytest.skip("skill-registry not reachable on localhost:8093 (start the stack: task up)")

    monkeypatch.setenv("SKILL_REGISTRY_URL", SKILL_REGISTRY_URL)

    import mcp_skills_demo.main as main_module

    importlib.reload(main_module)
    app = main_module.app

    config = uvicorn.Config(app, host="127.0.0.1", port=SERVER_PORT, log_level="warning")
    server = uvicorn.Server(config)

    def _run() -> None:
        asyncio.run(server.serve())

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    deadline = time.time() + 5
    while time.time() < deadline:
        if server.started:
            break
        time.sleep(0.1)
    yield f"http://127.0.0.1:{SERVER_PORT}/mcp"
    server.should_exit = True
    thread.join(timeout=5)


async def test_list_skills_tool_returns_real_skill_registry_data(running_server):
    # Compare against whatever Skill Registry actually has right now, so this test doesn't
    # depend on a specific fixture skill existing.
    async with httpx.AsyncClient(base_url=SKILL_REGISTRY_URL) as sr_client:
        skills = (await sr_client.get("/skills")).json()

    async with streamable_http_client(running_server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert "list_skills" in [t.name for t in tools.tools]

            result = await session.call_tool("list_skills", {})
            assert result.is_error is False
            text_items = [item for item in result.content if isinstance(item, TextContent)]
            assert len(text_items) == len(result.content)
            returned = [json.loads(item.text) for item in text_items]
            assert {s["name"] for s in returned} == {s["name"] for s in skills}
