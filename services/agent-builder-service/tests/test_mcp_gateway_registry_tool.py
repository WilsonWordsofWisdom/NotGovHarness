"""Infra-free: McpGatewayRegistryTool's request shape and 401-retry behavior (D-072, D-074's
sibling sync-token concern) against stub identity-service and mcp-gateway-registry servers --
same ThreadingHTTPServer pattern as platform-core's test_llm_gateway_auth.py.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from agent_builder_service.mcp_gateway_registry_tool import McpGatewayRegistryTool
from agent_builder_service.sync_token import SyncTokenClient


@pytest.fixture
def stub_identity():
    state = {"issued": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            if self.path == "/oauth/token":
                state["issued"] += 1
                body = {"access_token": f"token-{state['issued']}", "expires_in": 300}
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", state
    server.shutdown()


@pytest.fixture
def stub_mcp_gateway_registry():
    state = {"calls": [], "reject_tokens": set()}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            auth = self.headers.get("Authorization", "")
            token = auth.removeprefix("Bearer ")
            state["calls"].append(
                {"path": self.path, "arguments": body.get("arguments"), "token": token}
            )

            if token in state["reject_tokens"]:
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"detail": "Signature has expired"}).encode())
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps({"result": {"isError": False, "content": [{"text": "ok"}]}}).encode()
            )

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", state
    server.shutdown()


def _tool(identity_url: str, gateway_url: str) -> McpGatewayRegistryTool:
    token_client = SyncTokenClient(
        identity_url=identity_url,
        client_id="agent-builder-service",
        client_secret="x",
        scope="mcp_gateway_registry:call",
    )
    return McpGatewayRegistryTool(
        name="list_skills",
        description="Lists skills from Skill Registry via mcp-gateway-registry.",
        mcp_gateway_registry_url=gateway_url,
        federated_tool_name="live-test-mcp-skills-demo-list_skills",
        token_client=token_client,
    )


def test_run_calls_the_federated_tool_with_empty_arguments(
    stub_identity, stub_mcp_gateway_registry
):
    identity_url, _identity_state = stub_identity
    gateway_url, gateway_state = stub_mcp_gateway_registry
    tool = _tool(identity_url, gateway_url)

    result = tool._run()

    assert "ok" in result
    assert gateway_state["calls"] == [
        {
            "path": "/tools/live-test-mcp-skills-demo-list_skills/call",
            "arguments": {},
            "token": "token-1",
        }
    ]


def test_run_retries_once_on_a_401_with_a_force_refreshed_token(
    stub_identity, stub_mcp_gateway_registry
):
    """Mirrors D-063/D-074's reactive-retry shape, but for the sync tool-call path: a token that
    looks fresh to this process can still be rejected by mcp-gateway-registry, so one retry with
    a force-refreshed token must recover."""
    identity_url, _identity_state = stub_identity
    gateway_url, gateway_state = stub_mcp_gateway_registry
    tool = _tool(identity_url, gateway_url)

    gateway_state["reject_tokens"].add("token-1")
    result = tool._run()

    assert "ok" in result
    tokens_used = [call["token"] for call in gateway_state["calls"]]
    assert tokens_used == ["token-1", "token-2"]
