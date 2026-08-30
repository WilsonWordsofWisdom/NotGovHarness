"""HTTP-level tests for POST /cards/sign.

Infra-free: /cards/sign authenticates via verify_own_token (in-process, no JWKS fetch — see
D-032, why a real oauth2-mode JWKS-over-HTTP check would deadlock identity-service against
itself), so this needs neither a database nor a stub HTTP server — mints a token directly
against the app's own signing key, same technique test_oauth_endpoint.py uses.
"""

from __future__ import annotations

import time

import jwt
from fastapi.testclient import TestClient
from identity_service.config import Settings
from identity_service.main import app
from identity_service.tokens import issue_autonomous_token

client = TestClient(app)
settings = Settings()

CARD = {
    "name": "example-service",
    "url": "http://example-service:8000",
    "version": "1.0.0",
    "capabilities": {"streaming": False},
}


def _mint(scope: str) -> str:
    return issue_autonomous_token(
        app.state.signing_key, settings, client_id="example-service", scope=scope
    ).access_token


def test_sign_card_with_correct_scope_returns_a_verifiable_jws():
    token = _mint("agentcard:sign")
    resp = client.post("/cards/sign", json=CARD, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["signing_algorithm"] == "RS256"
    assert body["signing_key_id"] == app.state.signing_key.kid

    decoded = jwt.decode(
        body["signature_value"], app.state.signing_key.public_key, algorithms=["RS256"]
    )
    assert decoded == CARD


def test_sign_card_without_scope_is_forbidden():
    token = _mint("upstream:call")
    resp = client.post("/cards/sign", json=CARD, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_sign_card_without_a_token_is_unauthorized():
    resp = client.post("/cards/sign", json=CARD)
    assert resp.status_code == 401


def test_sign_card_with_a_garbage_token_is_unauthorized():
    resp = client.post("/cards/sign", json=CARD, headers={"Authorization": "Bearer not-a-real-jwt"})
    assert resp.status_code == 401


def test_sign_card_with_an_expired_token_is_unauthorized():
    signing_key = app.state.signing_key
    now = int(time.time())
    expired = jwt.encode(
        {
            "iss": settings.oauth2_issuer,
            "aud": settings.oauth2_audience,
            "iat": now - 600,
            "exp": now - 300,
            "sub": "example-service",
            "scope": "agentcard:sign",
            "mode": "autonomous",
        },
        signing_key.private_pem,
        algorithm="RS256",
        headers={"kid": signing_key.kid},
    )
    resp = client.post("/cards/sign", json=CARD, headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401
