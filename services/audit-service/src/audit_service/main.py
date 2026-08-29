"""audit-service: a hash-chained, tamper-evident compliance log consuming platform events.

Greenfield, no HTTP write path — the only way into the log is consuming an event off Kafka (via
the existing platform_core.events.Consumer) and hash-chaining it. See
docs/superpowers/specs/2026-08-29-audit-plane-harness-design.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.app import create_app
from platform_core.db import Database, session_dependency
from platform_core.db import lifespan_hook as db_lifespan
from platform_core.events import Consumer, EventEnvelope, consumer_lifespan
from platform_core.logging import get_logger

from .config import AUDITED_TOPICS, Settings
from .models import AuditLog
from .verifier import verify_chain
from .writer import append_event

log = get_logger("audit_service")


class AuditRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: str
    type: str
    source: str
    occurred_at: datetime
    trace_id: str | None
    data: dict[str, Any]
    prev_hash: str
    hash: str
    consumed_at: datetime


class RecordsPage(BaseModel):
    data: list[AuditRecordOut]
    next_cursor: int | None


class VerifyResultOut(BaseModel):
    valid: bool
    checked: int
    broken_at: int | None


def build_app() -> FastAPI:
    settings = Settings()
    db = Database(settings.database_url)
    get_session = session_dependency(db)

    async def on_event(event: EventEnvelope) -> None:
        async with db.session() as session:
            row = await append_event(session, event)
        log.info("event_chained", id=row.id, event_id=row.event_id, type=row.type)

    consumer = Consumer(settings.kafka_brokers, settings.service_name, AUDITED_TOPICS, on_event)

    app = create_app(
        settings,
        readiness_checks=[db.check],
        lifespan_hooks=[db_lifespan(db), consumer_lifespan(consumer)],
    )

    @app.get("/audit/records", response_model=RecordsPage, tags=["audit"])
    async def list_records(
        cursor: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=1000),
        session: AsyncSession = Depends(get_session),
    ) -> RecordsPage:
        """Chronological (oldest first), keyset-paginated on ``id`` — no OFFSET scan."""
        rows = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.id > cursor).order_by(AuditLog.id).limit(limit)
                )
            )
            .scalars()
            .all()
        )
        next_cursor = rows[-1].id if len(rows) == limit else None
        return RecordsPage(
            data=[AuditRecordOut.model_validate(r) for r in rows], next_cursor=next_cursor
        )

    @app.get("/audit/verify", response_model=VerifyResultOut, tags=["audit"])
    async def verify(session: AsyncSession = Depends(get_session)) -> VerifyResultOut:
        """Walks the whole chain, recomputing every row's hash from its stored content."""
        rows = (await session.execute(select(AuditLog).order_by(AuditLog.id))).scalars().all()
        result = verify_chain(list(rows))
        return VerifyResultOut(
            valid=result.valid, checked=result.checked, broken_at=result.broken_at
        )

    return app


app = build_app()
