"""Infra-free: agent-gateway's list-tools/call-tool endpoints against a stub ContextForge HTTP
server and a stub identity-service JWKS server. See test_mcp_servers.py's docstring for why
stubbing the far end is unavoidable here.
"""

from __future__ import annotations

import importlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

ISSUER = "https://identity-service.notgovharness.local"
AUDIENCE = "notgovharness"
KID = "test-kid"


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def private_pem(rsa_key):
    return rsa_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


@pytest.fixture(scope="module")
def jwks_server(rsa_key):
    jwk = json.loads(RSAAlgorithm.to_jwk(rsa_key.public_key()))
    jwk.update(kid=KID, use="sig", alg="RS256")
    body = json.dumps({"keys": [jwk]}).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/jwks.json"
    server.shutdown()


def _token(private_pem, scope: str) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 300,
        "sub": "example-service",
        "scope": scope,
        "mode": "autonomous",
    }
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": KID})


@pytest.fixture
def stub_contextforge():
    calls: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, status: int, body: dict | list) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/auth/email/login":
                self._send_json(200, {"access_token": "stub-token", "expires_in": 1200})
                return
            if self.path == "/rpc":
                calls.append(payload)
                params = payload.get("params", {})
                if params.get("name") == "list_skills":
                    result = {
                        "jsonrpc": "2.0",
                        "id": payload.get("id"),
                        "result": {
                            "content": [{"type": "text", "text": '{"name": "widget-skill"}'}]
                        },
                    }
                    self._send_json(200, result)
                    return
                self._send_json(
                    200,
                    {
                        "jsonrpc": "2.0",
                        "id": payload.get("id"),
                        "error": {"code": -32601, "message": "not found"},
                    },
                )
                return
            self._send_json(404, {"detail": "not found"})

        def do_GET(self) -> None:
            if self.path == "/tools":
                self._send_json(200, [{"name": "list_skills", "description": "list skills"}])
                return
            self._send_json(404, {"detail": "not found"})

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", calls
    server.shutdown()


@pytest.fixture
def app(jwks_server, stub_contextforge, monkeypatch):
    cf_url, _ = stub_contextforge
    monkeypatch.setenv("OAUTH2_JWKS_URL", jwks_server)
    monkeypatch.setenv("SERVICE_NAME", "agent-gateway")
    monkeypatch.setenv("CONTEXTFORGE_URL", cf_url)
    monkeypatch.setenv("CONTEXTFORGE_ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("CONTEXTFORGE_ADMIN_PASSWORD", "changeme123")

    from agent_gateway import main as main_module

    importlib.reload(main_module)
    return main_module.app


def test_list_tools(app, private_pem):
    token = _token(private_pem, "agent_gateway:call")
    client = TestClient(app)
    resp = client.get("/tools", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == [{"name": "list_skills", "description": "list skills"}]


def test_call_tool_returns_the_rpc_result(app, private_pem, stub_contextforge):
    _, calls = stub_contextforge
    token = _token(private_pem, "agent_gateway:call")
    client = TestClient(app)
    resp = client.post(
        "/tools/list_skills/call",
        json={"arguments": {}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["content"][0]["text"] == '{"name": "widget-skill"}'
    assert calls[0]["params"]["name"] == "list_skills"


def test_call_tool_without_scope_is_forbidden(app, private_pem):
    token = _token(private_pem, "upstream:call")
    client = TestClient(app)
    resp = client.post(
        "/tools/list_skills/call",
        json={"arguments": {}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
