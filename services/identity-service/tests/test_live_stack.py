"""Skip-if-down: identity-service running in compose against real Postgres.

Step 4 of the Agent Identity build order — "/oauth/token + JWKS over HTTP; skip-if-down" — exactly
what step 1-3's unit tests couldn't cover (a locally generated keypair, not the service's own
Postgres-backed client rows).
"""

from __future__ import annotations

import httpx


async def _client_credentials(base_url: str, client_id: str, secret: str) -> str:
    async with httpx.AsyncClient(base_url=base_url) as client:
        resp = await client.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": secret,
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def test_jwks_is_served(platform_identity_url):
    async with httpx.AsyncClient(base_url=platform_identity_url) as client:
        resp = await client.get("/.well-known/jwks.json")
    assert resp.status_code == 200
    assert resp.json()["keys"][0]["kty"] == "RSA"


async def test_client_credentials_against_seeded_client(platform_identity_url):
    token = await _client_credentials(
        platform_identity_url, "example-service", "example-service-dev-secret"
    )
    assert token.count(".") == 2  # header.payload.signature


async def test_bad_secret_is_rejected(platform_identity_url):
    async with httpx.AsyncClient(base_url=platform_identity_url) as client:
        resp = await client.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "example-service",
                "client_secret": "wrong",
            },
        )
    assert resp.status_code == 401


async def test_token_exchange_against_seeded_clients(platform_identity_url):
    subject = await _client_credentials(platform_identity_url, "alice", "alice-dev-secret")
    actor = await _client_credentials(
        platform_identity_url, "example-service", "example-service-dev-secret"
    )
    async with httpx.AsyncClient(base_url=platform_identity_url) as client:
        resp = await client.post(
            "/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "subject_token": subject,
                "actor_token": actor,
            },
        )
    assert resp.status_code == 200
    assert resp.json()["scope"] == "upstream:call"
