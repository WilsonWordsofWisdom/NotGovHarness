"""HTTP-level tests for POST /oauth/token's token-exchange branch.

Infra-free: token-exchange itself never touches the database (only client_credentials does, via
the Client lookup) — subject_token/actor_token are minted directly against the app's own signing
key without going through the DB-backed grant.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from identity_service.config import Settings
from identity_service.main import app
from identity_service.tokens import issue_autonomous_token

client = TestClient(app)
settings = Settings()


def _mint(client_id: str, scope: str) -> str:
    return issue_autonomous_token(
        app.state.signing_key, settings, client_id=client_id, scope=scope
    ).access_token


def test_token_exchange_returns_a_delegated_token():
    subject = _mint("alice", "widgets:read")
    actor = _mint("example-service", "widgets:read")

    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "subject_token": subject,
            "actor_token": actor,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "Bearer"
    assert body["scope"] == "widgets:read"


def test_token_exchange_rejects_a_forged_actor_token():
    subject = _mint("alice", "widgets:read")

    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "subject_token": subject,
            "actor_token": "not-a-real-jwt",
        },
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_token"


def test_token_exchange_requires_both_tokens():
    resp = client.post(
        "/oauth/token",
        data={"grant_type": "urn:ietf:params:oauth:grant-type:token-exchange"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_request"


def test_unsupported_grant_type_is_rejected():
    resp = client.post("/oauth/token", data={"grant_type": "password"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "unsupported_grant_type"
