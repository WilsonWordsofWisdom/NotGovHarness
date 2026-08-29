"""Skip-if-down: GET /audit/records and GET /audit/verify against real Postgres (same JSONB
constraint as test_writer.py). Reuses writer.append_event to seed rows rather than duplicating
chain-building logic.

Uses `audit_test`, a dedicated database — *not* `audit`, which the live audit-service container
writes to. This exercises a *separate* app instance (imported and pointed at audit_test via
monkeypatch) rather than the real running container, so it's safe to wipe between tests.
"""

from __future__ import annotations

import os

import pytest
from audit_service.models import AuditLog
from audit_service.writer import append_event
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from platform_core.db import Database
from platform_core.events import EventEnvelope
from platform_testing.fixtures import _reachable

AUDIT_DB_URL = os.getenv(
    "PLATFORM_TEST_AUDIT_DATABASE_URL",
    "postgresql+asyncpg://platform:platform@localhost:5432/audit_test",
)


@pytest.fixture
async def db():
    if not _reachable("localhost", 5432):
        pytest.skip("Postgres not reachable on localhost:5432 (start the stack: task up)")
    database = Database(AUDIT_DB_URL)
    async with database.session() as session:
        await session.execute(delete(AuditLog))
        await session.commit()
    try:
        yield database
    finally:
        async with database.session() as session:
            await session.execute(delete(AuditLog))
            await session.commit()
        await database.dispose()


@pytest.fixture
def app(db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", AUDIT_DB_URL)
    monkeypatch.setenv("SERVICE_NAME", "audit-service")
    monkeypatch.setenv("KAFKA_BROKERS", "localhost:9092")
    import importlib

    from audit_service import main as main_module

    importlib.reload(main_module)
    return main_module.app


async def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://audit")


async def _seed(db, count: int) -> None:
    async with db.session() as session:
        for i in range(count):
            await append_event(
                session,
                EventEnvelope(
                    event_id=f"evt-{i}",
                    type="widget.created",
                    source="example-service",
                    data={"n": i},
                ),
            )


async def test_records_empty_log(app):
    async with await _client(app) as client:
        resp = await client.get("/audit/records")
    assert resp.status_code == 200
    assert resp.json() == {"data": [], "next_cursor": None}


async def test_records_are_chronological_and_paginated(app, db):
    await _seed(db, 5)
    async with await _client(app) as client:
        page1 = (await client.get("/audit/records", params={"limit": 3})).json()
        assert [r["event_id"] for r in page1["data"]] == ["evt-0", "evt-1", "evt-2"]
        assert page1["next_cursor"] == page1["data"][-1]["id"]

        page2 = (
            await client.get("/audit/records", params={"limit": 3, "cursor": page1["next_cursor"]})
        ).json()
        assert [r["event_id"] for r in page2["data"]] == ["evt-3", "evt-4"]
        assert page2["next_cursor"] is None  # short page: no more after this


async def test_verify_on_empty_log(app):
    async with await _client(app) as client:
        resp = await client.get("/audit/verify")
    assert resp.json() == {"valid": True, "checked": 0, "broken_at": None}


async def test_verify_on_intact_chain(app, db):
    await _seed(db, 4)
    async with await _client(app) as client:
        resp = await client.get("/audit/verify")
    assert resp.json() == {"valid": True, "checked": 4, "broken_at": None}


async def test_verify_catches_a_row_tampered_directly_in_postgres(app, db):
    """The actual point of the harness, per the spec's step-5 done-when criterion: corrupt a
    historical row's data *directly in the database, bypassing the service entirely* - not via
    any API this service exposes - and confirm verification still catches it.
    """
    await _seed(db, 4)
    async with db.session() as session:
        target = (
            await session.execute(select(AuditLog).order_by(AuditLog.id).offset(1).limit(1))
        ).scalar_one()
        target_id = target.id
        target.data = {"forged": True}
        await session.commit()

    async with await _client(app) as client:
        resp = await client.get("/audit/verify")
    body = resp.json()
    assert body["valid"] is False
    assert body["broken_at"] == target_id
