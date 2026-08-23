"""example-service app: a REST resource backed by Postgres that emits an event on write.

Exercises the whole kit together — db sessions, the event producer + a background consumer,
and OTel — so a single POST produces one end-to-end trace (request -> DB -> event -> consume).
"""

from __future__ import annotations

from fastapi import Depends, FastAPI
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.app import create_app
from platform_core.db import Database, session_dependency
from platform_core.db import lifespan_hook as db_lifespan
from platform_core.errors import PlatformError
from platform_core.events import (
    Consumer,
    EventEnvelope,
    Producer,
    consumer_lifespan,
    producer_lifespan,
)
from platform_core.logging import get_logger

from .config import WIDGET_TOPIC, ExampleSettings
from .models import Widget

log = get_logger("example_service")


class WidgetIn(BaseModel):
    name: str


class WidgetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


def build_app() -> FastAPI:
    settings = ExampleSettings()
    db = Database(settings.database_url)
    producer = Producer(settings.kafka_brokers)

    async def on_widget_event(event: EventEnvelope) -> None:
        log.info("widget_event_consumed", event_type=event.type, widget=event.data)

    consumer = Consumer(
        settings.kafka_brokers, settings.service_name, [WIDGET_TOPIC], on_widget_event
    )
    get_session = session_dependency(db)

    app = create_app(
        settings,
        readiness_checks=[db.check],
        lifespan_hooks=[db_lifespan(db), producer_lifespan(producer), consumer_lifespan(consumer)],
    )

    @app.post("/widgets", response_model=WidgetOut, status_code=201, tags=["widgets"])
    async def create_widget(body: WidgetIn, session: AsyncSession = Depends(get_session)) -> Widget:
        widget = Widget(name=body.name)
        session.add(widget)
        await session.commit()
        await session.refresh(widget)
        await producer.publish(
            WIDGET_TOPIC,
            EventEnvelope(
                type="widget.created",
                source=settings.service_name,
                data={"id": widget.id, "name": widget.name},
            ),
        )
        return widget

    @app.get("/widgets/{widget_id}", response_model=WidgetOut, tags=["widgets"])
    async def get_widget(widget_id: int, session: AsyncSession = Depends(get_session)) -> Widget:
        widget = await session.get(Widget, widget_id)
        if widget is None:
            raise PlatformError("not_found", "widget not found", status_code=404)
        return widget

    @app.get("/widgets", response_model=list[WidgetOut], tags=["widgets"])
    async def list_widgets(session: AsyncSession = Depends(get_session)) -> list[Widget]:
        rows = (await session.execute(select(Widget).order_by(Widget.id))).scalars().all()
        return list(rows)

    return app


app = build_app()
