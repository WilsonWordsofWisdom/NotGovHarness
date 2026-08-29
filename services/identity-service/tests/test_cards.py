"""HTTP-level tests for POST /cards/sign.

Infra-free, but self-referential JWKS verification (D-029/D-031) means /cards/sign really does
fetch identity-service's own JWKS over HTTP during request handling (PyJWKClient makes a real
outbound request; TestClient's ASGI transport isn't a real socket) — mirrors upstream-stub's
test_echo.py: a tiny real JWKS HTTP server, with OAUTH2_JWKS_URL pointed at it, serving whatever
key the (freshly reloaded) app instance actually signs with.
"""

from __future__ import annotations

import importlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
import pytest
from fastapi.testclient import TestClient
from identity_service.config import Settings
from identity_service.tokens import issue_autonomous_token

CARD = {
    "name": "example-service",
    "url": "http://example-service:8000",
    "version": "1.0.0",
    "capabilities": {"streaming": False},
}

_jwks_body: dict = {"keys": []}


@pytest.fixture(scope="module")
def jwks_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(_jwks_body).encode())

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/jwks.json"
    server.shutdown()


@pytest.fixture
def app(jwks_server, monkeypatch):
    monkeypatch.setenv("OAUTH2_JWKS_URL", jwks_server)
    monkeypatch.setenv("SERVICE_NAME", "identity-service")

    from identity_service import main as main_module

    importlib.reload(main_module)
    _jwks_body["keys"] = [main_module.app.state.signing_key.jwk()]
    return main_module.app


def _mint(app, scope: str) -> str:
    return issue_autonomous_token(
        app.state.signing_key, Settings(), client_id="example-service", scope=scope
    ).access_token


def test_sign_card_with_correct_scope_returns_a_verifiable_jws(app):
    client = TestClient(app)
    token = _mint(app, "agentcard:sign")
    resp = client.post("/cards/sign", json=CARD, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["signing_algorithm"] == "RS256"
    assert body["signing_key_id"] == app.state.signing_key.kid

    decoded = jwt.decode(
        body["signature_value"], app.state.signing_key.public_key, algorithms=["RS256"]
    )
    assert decoded == CARD


def test_sign_card_without_scope_is_forbidden(app):
    client = TestClient(app)
    token = _mint(app, "upstream:call")
    resp = client.post("/cards/sign", json=CARD, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_sign_card_without_a_token_is_forbidden(app):
    # hybrid mode: no bearer token falls back to dev's anonymous header identity, which never
    # carries scopes — require_scope() 403s it, same as any other missing-scope caller.
    client = TestClient(app)
    resp = client.post("/cards/sign", json=CARD)
    assert resp.status_code == 403
