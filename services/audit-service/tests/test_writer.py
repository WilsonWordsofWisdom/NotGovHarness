"""Skip-if-down: a stub event list produces the expected chain (per the harness spec's step-3
verify criterion). Needs real Postgres — the audit_log table's `data` column is JSONB, a
Postgres-specific type, so this can't run against an infra-free in-memory substitute.
"""

from __future__ import annotations

import os

import pytest
from audit_service.chain import GENESIS_HASH, canonicalize, verify_link
from audit_service.models import AuditLog
from audit_service.writer import append_event
from sqlalchemy import delete, select

from platform_core.db import Database
from platform_core.events import EventEnvelope
from platform_testing.fixtures import _reachable

AUDIT_DB_URL = os.getenv(
    "PLATFORM_TEST_AUDIT_DATABASE_URL",
    "postgresql+asyncpg://platform:platform@localhost:5432/audit",
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


def _event(event_id: str, **overrides) -> EventEnvelope:
    defaults = {"type": "widget.created", "source": "example-service", "data": {"id": 1}}
    return EventEnvelope(event_id=event_id, **{**defaults, **overrides})


async def test_first_event_chains_from_genesis(db):
    async with db.session() as session:
        row = await append_event(session, _event("evt-1"))
    assert row.prev_hash == GENESIS_HASH
    canonical = canonicalize(
        event_id=row.event_id,
        type=row.type,
        source=row.source,
        occurred_at=row.occurred_at,
        trace_id=row.trace_id,
        data=row.data,
    )
    assert verify_link(GENESIS_HASH, canonical, row.hash)


async def test_a_stub_event_list_produces_the_expected_chain(db):
    events = [_event(f"evt-{i}") for i in range(1, 4)]
    rows = []
    async with db.session() as session:
        for event in events:
            rows.append(await append_event(session, event))

    assert [r.event_id for r in rows] == ["evt-1", "evt-2", "evt-3"]
    # Each row's prev_hash is the row before it's hash - the chain, not just three inserts.
    assert rows[0].prev_hash == GENESIS_HASH
    assert rows[1].prev_hash == rows[0].hash
    assert rows[2].prev_hash == rows[1].hash

    for row in rows:
        canonical = canonicalize(
            event_id=row.event_id,
            type=row.type,
            source=row.source,
            occurred_at=row.occurred_at,
            trace_id=row.trace_id,
            data=row.data,
        )
        assert verify_link(row.prev_hash, canonical, row.hash)


async def test_chain_persists_across_separate_sessions(db):
    # append_event reads the tail from the DB each time, not from in-process state - two events
    # appended in two separate sessions (matching how the real consumer opens a fresh session per
    # message) must still link correctly.
    async with db.session() as session:
        first = await append_event(session, _event("evt-a"))
    async with db.session() as session:
        second = await append_event(session, _event("evt-b"))
    assert second.prev_hash == first.hash

    async with db.session() as session:
        stored = (await session.execute(select(AuditLog).order_by(AuditLog.id))).scalars().all()
    assert [r.event_id for r in stored] == ["evt-a", "evt-b"]
