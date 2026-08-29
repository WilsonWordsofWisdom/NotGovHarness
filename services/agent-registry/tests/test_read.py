"""Skip-if-down: GET /agents, GET /agents/{name}[/{version}], and the /verify endpoint, against
real Postgres. Reuses the same stub-JWKS + agent_registry_test setup as test_publish.py — see
that file's docstring for why both are needed.
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
from sqlalchemy import delete, update

from platform_testing.fixtures import _reachable

ISSUER = "https://identity-service.notgovharness.local"
AUDIENCE = "notgovharness"
KID = "test-kid"

AGENT_REGISTRY_TEST_DB_URL = (
    "postgresql+asyncpg://platform:platform@localhost:5432/agent_registry_test"
)


def _card(name: str, version: str, **overrides) -> dict:
    return {
        "name": name,
        "url": f"http://{name}:8000",
        "version": version,
        "capabilities": {"streaming": False},
        "skills": [{"name": "widgets.create"}],
        **overrides,
    }


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


def _publish(client: TestClient, private_pem, card: dict) -> None:
    token = _token(private_pem, "registry:publish")
    resp = client.post(
        "/agents",
        json={"card": card, "signature": _sign(private_pem, card)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text


def test_list_and_skill_filter(app, private_pem):
    with TestClient(app) as client:
        _publish(client, private_pem, _card("widget-agent", "1.0.0"))
        _publish(
            client, private_pem, _card("other-agent", "1.0.0", skills=[{"name": "other.skill"}])
        )

        listing = client.get("/agents").json()
        assert {row["name"] for row in listing} == {"widget-agent", "other-agent"}

        filtered = client.get("/agents", params={"skill": "widgets.create"}).json()
        assert [row["name"] for row in filtered] == ["widget-agent"]


def test_get_latest_returns_the_most_recently_published_version(app, private_pem):
    with TestClient(app) as client:
        _publish(client, private_pem, _card("widget-agent", "1.0.0"))
        _publish(client, private_pem, _card("widget-agent", "2.0.0"))

        latest = client.get("/agents/widget-agent").json()
        assert latest["card"]["version"] == "2.0.0"

        pinned = client.get("/agents/widget-agent/1.0.0").json()
        assert pinned["card"]["version"] == "1.0.0"


def test_get_unknown_agent_is_404(app):
    with TestClient(app) as client:
        resp = client.get("/agents/does-not-exist")
    assert resp.status_code == 404


def test_verify_on_intact_card_is_valid(app, private_pem):
    with TestClient(app) as client:
        _publish(client, private_pem, _card("widget-agent", "1.0.0"))
        resp = client.get("/agents/widget-agent/1.0.0/verify")
    assert resp.json() == {"valid": True, "reason": None}


def test_verify_catches_a_card_tampered_directly_in_postgres(app, private_pem):
    # /verify re-checks the signature against `card` (the exact signed payload) — a raw SQL
    # UPDATE bypassing the service entirely, the same discipline as Audit's live tampering test,
    # not a change routed through any API this service exposes.
    from agent_registry.models import AgentCard

    from platform_core.db import Database

    with TestClient(app) as client:
        _publish(client, private_pem, _card("widget-agent", "1.0.0"))

        async def _tamper() -> None:
            forged = _card("widget-agent", "1.0.0")
            forged["url"] = "http://forged.example/"
            db = Database(AGENT_REGISTRY_TEST_DB_URL)
            async with db.session() as session:
                await session.execute(
                    update(AgentCard)
                    .where(AgentCard.name == "widget-agent", AgentCard.version == "1.0.0")
                    .values(card=forged)
                )
                await session.commit()
            await db.dispose()

        asyncio.run(_tamper())

        resp = client.get("/agents/widget-agent/1.0.0/verify")
    assert resp.json()["valid"] is False
