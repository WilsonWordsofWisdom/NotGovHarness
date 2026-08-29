"""audit-service: a hash-chained, tamper-evident compliance log consuming platform events.

Greenfield, no HTTP write path — the only way into the log is consuming an event off Kafka (via
the existing platform_core.events.Consumer) and hash-chaining it. See
docs/superpowers/specs/2026-08-29-audit-plane-harness-design.md.
"""

from __future__ import annotations

from fastapi import FastAPI

from platform_core.app import create_app
from platform_core.db import Database
from platform_core.db import lifespan_hook as db_lifespan
from platform_core.events import Consumer, EventEnvelope, consumer_lifespan
from platform_core.logging import get_logger

from .config import AUDITED_TOPICS, Settings
from .writer import append_event

log = get_logger("audit_service")


def build_app() -> FastAPI:
    settings = Settings()
    db = Database(settings.database_url)

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

    return app


app = build_app()
