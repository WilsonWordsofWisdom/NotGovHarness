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
from platform_core.auth import CallerIdentity, make_require_identity
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
from platform_core.facade import UpstreamClient, raise_for_upstream
from platform_core.facade import lifespan_hook as facade_lifespan
from platform_core.logging import get_logger
from platform_core.svid import try_x509_source

from .config import WIDGET_TOPIC, ExampleSettings
from .identity_client import try_get_delegated_token
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
    svid_source = try_x509_source()
    upstream = UpstreamClient(
        settings.upstream_url,
        svid_source=svid_source,
        expected_peer_spiffe_id=settings.upstream_spiffe_id if svid_source else None,
    )
    require_identity = make_require_identity(settings)

    app = create_app(
        settings,
        readiness_checks=[db.check],
        lifespan_hooks=[
            db_lifespan(db),
            producer_lifespan(producer),
            consumer_lifespan(consumer),
            facade_lifespan(upstream),
        ],
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

    @app.get("/proxy", tags=["facade"])
    async def proxy(identity: CallerIdentity = Depends(require_identity)) -> dict:
        """Façade shape: forward to the upstream, propagating identity + trace context.

        Fetches a real delegated token from identity-service (alice, the simulated principal,
        acted for by this service) when it's reachable — otherwise falls back to the Phase 0
        X-Service-Identity header, so this still works under `core` alone.
        """
        headers = {}
        delegated_token = await try_get_delegated_token(
            settings.identity_service_url,
            actor_client_id=settings.identity_client_id,
            actor_client_secret=settings.identity_client_secret,
            principal_client_id=settings.principal_client_id,
            principal_client_secret=settings.principal_client_secret,
        )
        if delegated_token:
            headers["Authorization"] = f"Bearer {delegated_token}"

        response = await upstream.forward("GET", "/echo", identity=identity, headers=headers)
        raise_for_upstream(response)
        return response.json()

    return app


app = build_app()
