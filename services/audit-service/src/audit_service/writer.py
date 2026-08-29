"""Appends one hash-chained row per consumed event — the bridge between the pure chain math
(chain.py) and the DB-backed chain (the audit_log table).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.events import EventEnvelope

from .chain import GENESIS_HASH, canonicalize, compute_hash
from .models import AuditLog


async def append_event(session: AsyncSession, event: EventEnvelope) -> AuditLog:
    """Hash-chain one event onto the log (reading the current tail's hash first) and commit it."""
    last = (
        await session.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(1))
    ).scalar_one_or_none()
    prev_hash = last.hash if last is not None else GENESIS_HASH

    canonical = canonicalize(
        event_id=event.event_id,
        type=event.type,
        source=event.source,
        occurred_at=event.occurred_at,
        trace_id=event.trace_id,
        data=event.data,
    )
    row = AuditLog(
        event_id=event.event_id,
        type=event.type,
        source=event.source,
        occurred_at=event.occurred_at,
        trace_id=event.trace_id,
        data=event.data,
        prev_hash=prev_hash,
        hash=compute_hash(prev_hash, canonical),
    )
    session.add(row)
    await session.commit()
    return row
