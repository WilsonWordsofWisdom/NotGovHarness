"""GET /echo: hybrid auth — dev header fallback when no bearer token, real scope enforcement
when one is verified. Uses a real (tiny, in-process) JWKS server, matching platform-core's own
"verify tokens with a stub JWKS" pattern, since StubSettings hardcodes an oauth2_jwks_url.
"""

from __future__ import annotations

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


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def jwks_server(rsa_key):
    jwk = json.loads(RSAAlgorithm.to_jwk(rsa_key.public_key()))
    jwk.update(kid="test-kid", use="sig", alg="RS256")
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


def _mint(rsa_key, **claim_overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 300,
        "jti": "test-jti",
        "sub": "alice",
        "scope": "upstream:call",
        "mode": "delegated",
        "act": {"sub": "example-service"},
        "depth": 1,
        **claim_overrides,
    }
    private_pem = rsa_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": "test-kid"})


@pytest.fixture
def client(jwks_server, monkeypatch):
    monkeypatch.setenv("OAUTH2_JWKS_URL", jwks_server)
    monkeypatch.setenv("SERVICE_NAME", "upstream-stub")
    # A fresh app per test — StubSettings/require_identity are built at import time otherwise,
    # before the env vars above are set.
    import importlib

    from upstream_stub import main as main_module

    importlib.reload(main_module)
    return TestClient(main_module.app)


def test_dev_fallback_with_no_bearer_token(client):
    r = client.get("/echo", headers={"x-service-identity": "builder"})
    assert r.status_code == 200
    body = r.json()
    assert body["seen_identity"] == "builder"
    assert body["mode"] == "autonomous"


def test_delegated_token_with_correct_scope_is_accepted(client, rsa_key):
    token = _mint(rsa_key)
    r = client.get("/echo", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["seen_identity"] == "example-service"  # the immediate actor, not the principal
    assert body["on_behalf_of"] == "alice"
    assert body["actor_chain"] == {"sub": "example-service"}


def test_delegated_token_missing_scope_is_forbidden(client, rsa_key):
    token = _mint(rsa_key, scope="something:else")
    r = client.get("/echo", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


def test_invalid_bearer_token_is_rejected_not_silently_downgraded(client):
    r = client.get("/echo", headers={"Authorization": "Bearer not-a-real-jwt"})
    assert r.status_code == 401
