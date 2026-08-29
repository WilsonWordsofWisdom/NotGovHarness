"""Skip-if-down: the actual end-to-end flow this harness exists to prove.

A real bearer token from a running identity-service signs a real Agent Card via its /cards/sign
endpoint (D-029: same key that signs tokens signs cards), the card is published to a running
agent-registry, fetched back, then tampered with directly in Postgres — bypassing every service —
and caught by /verify. Steps 1-4's tests already proved signing and verification in isolation;
this is the one that proves the whole live system, not direct function calls.
"""

from __future__ import annotations

import os
import uuid

import httpx
from agent_registry.models import AgentCard
from sqlalchemy import update

from platform_core.db import Database

AGENT_REGISTRY_DB_URL = os.getenv(
    "PLATFORM_TEST_AGENT_REGISTRY_DATABASE_URL",
    "postgresql+asyncpg://platform:platform@localhost:5432/agent_registry",
)


async def _sign_in_token(identity_url: str) -> str:
    async with httpx.AsyncClient(base_url=identity_url) as client:
        resp = await client.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "example-service",
                "client_secret": "example-service-dev-secret",
                "scope": "agentcard:sign registry:publish",
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def test_a_real_signed_card_is_published_fetched_and_tamper_detected(
    platform_identity_url, platform_agent_registry_url
):
    token = await _sign_in_token(platform_identity_url)
    headers = {"Authorization": f"Bearer {token}"}
    name = f"live-test-agent-{uuid.uuid4().hex[:8]}"
    card = {
        "name": name,
        "url": f"http://{name}:8000",
        "version": "1.0.0",
        "capabilities": {"streaming": False},
        "skills": [{"name": "widgets.create"}],
    }

    async with httpx.AsyncClient(base_url=platform_identity_url) as identity_client:
        signed = await identity_client.post("/cards/sign", json=card, headers=headers)
    assert signed.status_code == 200
    signature = signed.json()

    async with httpx.AsyncClient(base_url=platform_agent_registry_url) as registry_client:
        published = await registry_client.post(
            "/agents", json={"card": card, "signature": signature}, headers=headers
        )
        assert published.status_code == 201, published.text

        fetched = await registry_client.get(f"/agents/{name}/1.0.0")
        assert fetched.status_code == 200
        assert fetched.json()["card"] == card

        before = await registry_client.get(f"/agents/{name}/1.0.0/verify")
        assert before.json() == {"valid": True, "reason": None}

    # Bypass every service entirely — a raw UPDATE, exactly what a DB-level attacker (or a
    # careless admin) would do. Nothing about the API this service exposes is involved.
    forged = {**card, "url": "http://forged.example/"}
    db = Database(AGENT_REGISTRY_DB_URL)
    try:
        async with db.session() as session:
            await session.execute(
                update(AgentCard)
                .where(AgentCard.name == name, AgentCard.version == "1.0.0")
                .values(card=forged)
            )
            await session.commit()
    finally:
        await db.dispose()

    async with httpx.AsyncClient(base_url=platform_agent_registry_url) as registry_client:
        after = await registry_client.get(f"/agents/{name}/1.0.0/verify")
    assert after.json()["valid"] is False
