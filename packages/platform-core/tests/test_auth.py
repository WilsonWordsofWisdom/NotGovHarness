from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from platform_core.auth import CallerIdentity, make_require_identity, require_scope
from platform_core.config import PlatformSettings
from platform_core.errors import install_error_handlers

ISSUER = "https://issuer.test"
AUDIENCE = "notgovharness"


def _app(auth_mode: str = "dev", **settings_kwargs) -> FastAPI:
    settings = PlatformSettings(service_name="t", auth_mode=auth_mode, **settings_kwargs)
    require_identity = make_require_identity(settings)
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/whoami")
    async def whoami(identity: CallerIdentity = Depends(require_identity)):
        return {
            "id": identity.id,
            "kind": identity.kind,
            "mode": identity.mode,
            "on_behalf_of": identity.on_behalf_of,
            "actor_chain": identity.actor_chain,
        }

    @app.get("/widgets")
    async def widgets(
        identity: CallerIdentity = Depends(require_scope(require_identity, "widgets:read")),
    ):
        return {"id": identity.id}

    return app


def test_dev_reads_header():
    r = TestClient(_app()).get("/whoami", headers={"x-service-identity": "builder"})
    assert r.json()["id"] == "builder"
    assert r.json()["kind"] == "service"


def test_dev_defaults_anonymous():
    r = TestClient(_app()).get("/whoami")
    assert r.json()["id"] == "anonymous"


# --- oauth2 mode: a real (tiny, in-process) JWKS server, per the harness spec's own
# "verify tokens with a stub JWKS" instruction --------------------------------------------------


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def jwks_url(rsa_key):
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
            pass  # keep test output quiet

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
        "sub": "example-service",
        "scope": "",
        "mode": "autonomous",
        "depth": 0,
        **claim_overrides,
    }
    private_pem = rsa_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": "test-kid"})


@pytest.fixture
def oauth2_app(jwks_url):
    return _app("oauth2", oauth2_jwks_url=jwks_url, oauth2_issuer=ISSUER, oauth2_audience=AUDIENCE)


def test_oauth2_autonomous_token_yields_identity(oauth2_app, rsa_key):
    token = _mint(rsa_key, sub="example-service", scope="widgets:read")
    r = TestClient(oauth2_app).get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "id": "example-service",
        "kind": "service",
        "mode": "autonomous",
        "on_behalf_of": None,
        "actor_chain": None,
    }


def test_oauth2_delegated_token_surfaces_principal_and_actor_chain(oauth2_app, rsa_key):
    act = {"sub": "example-service"}
    token = _mint(rsa_key, sub="alice", scope="widgets:read", mode="delegated", act=act, depth=1)
    r = TestClient(oauth2_app).get("/whoami", headers={"Authorization": f"Bearer {token}"})
    body = r.json()
    # The immediate caller (the agent), not the principal, is what "id" reports.
    assert body["id"] == "example-service"
    assert body["mode"] == "delegated"
    assert body["on_behalf_of"] == "alice"
    assert body["actor_chain"] == act


def test_oauth2_missing_bearer_is_401(oauth2_app):
    r = TestClient(oauth2_app).get("/whoami")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_oauth2_wrong_audience_is_401(oauth2_app, rsa_key):
    token = _mint(rsa_key, aud="some-other-audience")
    r = TestClient(oauth2_app).get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_oauth2_depth_exceeding_max_is_401(oauth2_app, rsa_key):
    token = _mint(rsa_key, depth=99)
    r = TestClient(oauth2_app).get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    assert "depth" in r.json()["error"]["message"]


def test_require_scope_allows_when_scope_present(oauth2_app, rsa_key):
    token = _mint(rsa_key, scope="widgets:read widgets:write")
    r = TestClient(oauth2_app).get("/widgets", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_require_scope_forbids_when_scope_missing(oauth2_app, rsa_key):
    token = _mint(rsa_key, scope="widgets:write")
    r = TestClient(oauth2_app).get("/widgets", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


def test_oauth2_mode_requires_jwks_url_configured():
    app = _app("oauth2")  # no oauth2_jwks_url
    # A misconfiguration, not a client-facing 401/403 — TestClient re-raises it (Starlette's
    # generic Exception handler still produces a 500 for a real server, but re-raises for tests).
    with pytest.raises(RuntimeError, match="oauth2_jwks_url"):
        TestClient(app).get("/whoami", headers={"Authorization": "Bearer x"})


@pytest.fixture
def hybrid_app(jwks_url):
    return _app("hybrid", oauth2_jwks_url=jwks_url, oauth2_issuer=ISSUER, oauth2_audience=AUDIENCE)


def test_hybrid_falls_back_to_dev_header_without_a_bearer_token(hybrid_app):
    r = TestClient(hybrid_app).get("/whoami", headers={"x-service-identity": "builder"})
    assert r.json()["id"] == "builder"
    assert r.json()["mode"] == "autonomous"


def test_hybrid_falls_back_to_anonymous_with_no_header_at_all(hybrid_app):
    r = TestClient(hybrid_app).get("/whoami")
    assert r.json()["id"] == "anonymous"


def test_hybrid_verifies_a_bearer_token_when_present(hybrid_app, rsa_key):
    token = _mint(rsa_key, sub="alice", mode="delegated", act={"sub": "example-service"}, depth=1)
    r = TestClient(hybrid_app).get("/whoami", headers={"Authorization": f"Bearer {token}"})
    body = r.json()
    assert body["id"] == "example-service"
    assert body["on_behalf_of"] == "alice"


def test_hybrid_rejects_an_invalid_bearer_token_rather_than_falling_back(hybrid_app):
    r = TestClient(hybrid_app).get("/whoami", headers={"Authorization": "Bearer not-a-real-jwt"})
    assert r.status_code == 401
