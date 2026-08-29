"""Skip-if-down: POST /agents needs real Postgres for the JSONB card write. Signature
verification is exercised against a real stub JWKS HTTP server (mirrors upstream-stub's
test_echo.py / identity-service's test_cards.py) — PyJWKClient makes a genuine outbound HTTP
request, so there's no in-process shortcut even at "unit" scope.

One RSA key plays identity-service's role for both bearer-token minting and card signing — the
same key does both in the real design (D-029), so this mirrors production exactly rather than
being a simplification.

Uses a dedicated `agent_registry_test` database, not `agent_registry` — the same db-per-service-
test split audit-service uses (see its test_api.py docstring), so nothing here can ever collide
with a real published catalog.
"""

from __future__ import annotations

import asyncio
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
from sqlalchemy import delete

from platform_testing.fixtures import _reachable

ISSUER = "https://identity-service.notgovharness.local"
AUDIENCE = "notgovharness"
KID = "test-kid"

CARD = {
    "name": "example-service",
    "url": "http://example-service:8000",
    "version": "1.0.0",
    "capabilities": {"streaming": False},
}

AGENT_REGISTRY_TEST_DB_URL = (
    "postgresql+asyncpg://platform:platform@localhost:5432/agent_registry_test"
)


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


def _sign(private_pem, card: dict) -> dict:
    return {
        "signing_algorithm": "RS256",
        "signing_key_id": KID,
        "signature_value": jwt.encode(card, private_pem, algorithm="RS256", headers={"kid": KID}),
    }


@pytest.fixture
def app(jwks_server, monkeypatch):
    if not _reachable("localhost", 5432):
        pytest.skip("Postgres not reachable on localhost:5432 (start the stack: task up)")
    monkeypatch.setenv("DATABASE_URL", AGENT_REGISTRY_TEST_DB_URL)
    monkeypatch.setenv("OAUTH2_JWKS_URL", jwks_server)
    monkeypatch.setenv("SERVICE_NAME", "agent-registry")

    from agent_registry import main as main_module
    from agent_registry.models import AgentCard

    from platform_core.db import Database

    importlib.reload(main_module)

    async def _clear() -> None:
        db = Database(AGENT_REGISTRY_TEST_DB_URL)
        async with db.session() as session:
            await session.execute(delete(AgentCard))
            await session.commit()
        await db.dispose()

    asyncio.run(_clear())
    return main_module.app


def test_publish_with_valid_signature_and_scope_succeeds(app, private_pem):
    client = TestClient(app)
    token = _token(private_pem, "registry:publish")
    resp = client.post(
        "/agents",
        json={"card": CARD, "signature": _sign(private_pem, CARD)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "example-service"
    assert body["version"] == "1.0.0"
    assert body["published_by"] == "example-service"


def test_publish_with_tampered_card_content_is_rejected(app, private_pem):
    client = TestClient(app)
    token = _token(private_pem, "registry:publish")
    signature = _sign(private_pem, CARD)
    tampered = {**CARD, "url": "http://evil.example/"}
    resp = client.post(
        "/agents",
        json={"card": tampered, "signature": signature},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_signature"


def test_publish_without_publish_scope_is_forbidden(app, private_pem):
    client = TestClient(app)
    token = _token(private_pem, "something:else")
    resp = client.post(
        "/agents",
        json={"card": CARD, "signature": _sign(private_pem, CARD)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_publish_missing_required_field_is_rejected(app, private_pem):
    client = TestClient(app)
    token = _token(private_pem, "registry:publish")
    bad_card = {"name": "no-url-agent", "version": "1.0.0", "capabilities": {}}
    resp = client.post(
        "/agents",
        json={"card": bad_card, "signature": _sign(private_pem, bad_card)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


def test_publishing_the_same_name_and_version_twice_conflicts(app, private_pem):
    # Entered as a context manager (not a bare TestClient(app)) so both calls below share one
    # portal/event loop — the async engine's pooled asyncpg connections are bound to whichever
    # loop first used them, and a second, separately-dispatched call would reuse a connection
    # from a now-closed loop otherwise.
    token = _token(private_pem, "registry:publish")
    body = {"card": CARD, "signature": _sign(private_pem, CARD)}
    with TestClient(app) as client:
        first = client.post("/agents", json=body, headers={"Authorization": f"Bearer {token}"})
        assert first.status_code == 201
        second = client.post("/agents", json=body, headers={"Authorization": f"Bearer {token}"})
    assert second.status_code == 409
