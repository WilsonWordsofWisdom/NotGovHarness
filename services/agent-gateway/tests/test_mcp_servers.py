"""Infra-free: agent-gateway's register/list-servers endpoints against a stub ContextForge HTTP
server and a stub identity-service JWKS server — mirrors the pattern already used throughout
this repo (upstream-stub's test_echo.py, agent-registry's test_publish.py) for exactly this
"a real outbound HTTP call is unavoidable, so stub the far end" situation.
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


class _StubContextForgeState:
    login_calls = 0
    gateways: list[dict] = []


@pytest.fixture
def stub_contextforge():
    _StubContextForgeState.login_calls = 0
    _StubContextForgeState.gateways = []

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
                _StubContextForgeState.login_calls += 1
                self._send_json(200, {"access_token": "stub-token", "expires_in": 1200})
                return
            if self.path == "/gateways":
                if self.headers.get("Authorization") != "Bearer stub-token":
                    self._send_json(401, {"detail": "unauthorized"})
                    return
                row = {"id": len(_StubContextForgeState.gateways) + 1, **payload}
                _StubContextForgeState.gateways.append(row)
                self._send_json(201, row)
                return
            self._send_json(404, {"detail": "not found"})

        def do_GET(self) -> None:
            if self.path == "/gateways":
                if self.headers.get("Authorization") != "Bearer stub-token":
                    self._send_json(401, {"detail": "unauthorized"})
                    return
                self._send_json(200, _StubContextForgeState.gateways)
                return
            self._send_json(404, {"detail": "not found"})

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", _StubContextForgeState
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


def test_register_and_list_mcp_server(app, private_pem, stub_contextforge):
    _, state = stub_contextforge
    token = _token(private_pem, "agent_gateway:call")
    client = TestClient(app)

    resp = client.post(
        "/mcp-servers",
        json={"name": "mcp-skills-demo", "url": "http://mcp-skills-demo:9000/sse"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == "mcp-skills-demo"

    listing = client.get("/mcp-servers", headers={"Authorization": f"Bearer {token}"})
    assert listing.status_code == 200
    assert [row["name"] for row in listing.json()] == ["mcp-skills-demo"]

    # Only one login for two calls — the token was cached, not re-fetched per request.
    assert state.login_calls == 1


def test_register_without_scope_is_forbidden(app, private_pem):
    token = _token(private_pem, "upstream:call")
    client = TestClient(app)
    resp = client.post(
        "/mcp-servers",
        json={"name": "mcp-skills-demo", "url": "http://mcp-skills-demo:9000/sse"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_register_without_a_token_is_forbidden(app):
    client = TestClient(app)
    resp = client.post("/mcp-servers", json={"name": "x", "url": "http://x:9000/sse"})
    # hybrid mode: no bearer token falls back to dev's anonymous header identity, which never
    # carries scopes — require_scope() 403s it, same as identity-service's /cards/sign.
    assert resp.status_code == 403
