"""Skip-if-down: the actual end-to-end flow this harness exists to prove.

A real widget created through example-service's HTTP API (via Traefik) flows through Redpanda
into the audit log, and tampering with a row directly in Postgres — bypassing every service, not
through any API — is caught by /audit/verify. Steps 1-4's tests already proved the chain math and
the read API in isolation; this is the one that proves the whole live system, not direct function
calls.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime

import httpx
import pytest
from audit_service.chain import canonicalize, verify_link
from sqlalchemy import text

from platform_core.db import Database
from platform_testing.fixtures import _reachable

AUDIT_DB_URL = os.getenv(
    "PLATFORM_TEST_AUDIT_DATABASE_URL",
    "postgresql+asyncpg://platform:platform@localhost:5432/audit",
)
TRAEFIK_URL = os.getenv("PLATFORM_TEST_TRAEFIK_URL", "http://localhost")


@pytest.fixture
def traefik_reachable():
    if not _reachable("localhost", 80):
        pytest.skip("Traefik not reachable on localhost:80 (start the stack: task up)")


async def _wait_for_record(
    audit_url: str,
    widget_id: int,
    *,
    timeout: float = 15.0,  # noqa: ASYNC109 - probe budget, not a cancellation deadline
) -> dict:
    """Kafka consumption is async — poll rather than assume the row landed immediately."""
    deadline = asyncio.get_event_loop().time() + timeout
    async with httpx.AsyncClient(base_url=audit_url) as client:
        while True:
            resp = await client.get("/audit/records", params={"limit": 1000})
            resp.raise_for_status()
            for row in resp.json()["data"]:
                if row["type"] == "widget.created" and row["data"].get("id") == widget_id:
                    return row
            if asyncio.get_event_loop().time() >= deadline:
                raise TimeoutError(f"no audit record for widget {widget_id} within {timeout}s")
            await asyncio.sleep(0.5)


async def test_real_widget_creation_flows_through_kafka_into_the_chain(
    platform_audit_url, traefik_reachable
):
    unique_name = f"audit-live-test-{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(base_url=TRAEFIK_URL) as client:
        created = await client.post("/example/widgets", json={"name": unique_name})
    assert created.status_code == 201
    widget_id = created.json()["id"]

    row = await _wait_for_record(platform_audit_url, widget_id)
    assert row["data"]["name"] == unique_name
    assert row["source"] == "example-service"


def _link_is_intact(row: dict) -> bool:
    canonical = canonicalize(
        event_id=row["event_id"],
        type=row["type"],
        source=row["source"],
        occurred_at=datetime.fromisoformat(row["occurred_at"]),
        trace_id=row["trace_id"],
        data=row["data"],
    )
    return verify_link(row["prev_hash"], canonical, row["hash"])


async def _fetch_row(audit_url: str, row_id: int) -> dict:
    async with httpx.AsyncClient(base_url=audit_url) as client:
        page = await client.get("/audit/records", params={"cursor": row_id - 1, "limit": 1})
    return page.json()["data"][0]


async def test_tampering_a_row_directly_in_postgres_is_caught_by_verify(
    platform_audit_url, traefik_reachable
):
    """`audit` is a live, shared database this harness deliberately never wipes (see
    test_writer.py's docstring for why) — so unrelated history (other runs of this very test,
    real usage) may already contain earlier, genuine tampering that doesn't get "healed" by
    running this test again. So this doesn't assert the *whole* chain is valid beforehand, or
    that /audit/verify's exact `broken_at` lands on this specific row — both depend on prior
    history this test doesn't control. What it proves precisely: this row's own hash link is
    intact before tampering and provably broken after (checked directly against chain.py, the
    same math /audit/verify itself uses), and the real endpoint agrees the chain is now invalid.
    """
    unique_name = f"audit-tamper-test-{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(base_url=TRAEFIK_URL) as client:
        created = await client.post("/example/widgets", json={"name": unique_name})
    widget_id = created.json()["id"]
    row = await _wait_for_record(platform_audit_url, widget_id)
    assert _link_is_intact(row)

    # Bypass every service entirely — a raw SQL UPDATE, exactly what a DB-level attacker (or a
    # careless admin) would do. Nothing about the API this service exposes is involved.
    db = Database(AUDIT_DB_URL)
    try:
        async with db.session() as session:
            await session.execute(
                # A space before the cast — SQLAlchemy's text() bind-param parser misreads
                # ":data::jsonb" (no space) as part of the parameter name, not a cast.
                text("UPDATE audit_log SET data = :data ::jsonb WHERE id = :id"),
                {"data": '{"forged": true}', "id": row["id"]},
            )
            await session.commit()
    finally:
        await db.dispose()

    tampered_row = await _fetch_row(platform_audit_url, row["id"])
    assert not _link_is_intact(tampered_row)

    async with httpx.AsyncClient(base_url=platform_audit_url) as client:
        after = await client.get("/audit/verify")
    assert after.json()["valid"] is False
