"""Event backbone over Kafka/Redpanda.

A typed ``EventEnvelope`` plus a ``Producer`` and ``Consumer`` that hide aiokafka. Trace context
is injected into Kafka headers on publish and extracted on consume, so a consumer span becomes a
child of the producing request's span (one trace end to end). Failed handling is retried, then
dead-lettered to ``<topic>.dlq``.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from fastapi import FastAPI
from opentelemetry import propagate, trace
from pydantic import BaseModel, Field

from .context import current_trace_id
from .logging import get_logger

_log = get_logger("platform_core.events")
_tracer = trace.get_tracer("platform_core.events")

Handler = Callable[["EventEnvelope"], Awaitable[None]]


class EventEnvelope(BaseModel):
    """Standard event wrapper. ``data`` carries the per-event-type payload."""

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    type: str
    source: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    trace_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class Producer:
    """Publishes ``EventEnvelope``s. Start/stop tied to the app lifespan."""

    def __init__(self, brokers: str) -> None:
        self._brokers = brokers
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        self._producer = AIOKafkaProducer(bootstrap_servers=self._brokers)
        await self._producer.start()

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def publish(self, topic: str, event: EventEnvelope) -> None:
        if self._producer is None:
            raise RuntimeError("Producer not started")
        if event.trace_id is None:
            event.trace_id = current_trace_id()
        carrier: dict[str, str] = {}
        propagate.inject(carrier)
        headers = [(k, v.encode()) for k, v in carrier.items()]
        await self._producer.send_and_wait(
            topic, value=event.model_dump_json().encode(), headers=headers
        )


class Consumer:
    """Consumes a topic set into ``handler`` with bounded retries, then dead-letters."""

    def __init__(
        self,
        brokers: str,
        group: str,
        topics: Sequence[str],
        handler: Handler,
        *,
        retries: int = 3,
        dlq_suffix: str = ".dlq",
    ) -> None:
        self._brokers = brokers
        self._group = group
        self._topics = list(topics)
        self._handler = handler
        self._retries = retries
        self._dlq_suffix = dlq_suffix
        self._consumer: AIOKafkaConsumer | None = None
        self._dlq: AIOKafkaProducer | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._consumer = AIOKafkaConsumer(
            *self._topics,
            bootstrap_servers=self._brokers,
            group_id=self._group,
            enable_auto_commit=True,
            auto_offset_reset="earliest",
        )
        await self._consumer.start()
        self._dlq = AIOKafkaProducer(bootstrap_servers=self._brokers)
        await self._dlq.start()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._consumer is not None:
            await self._consumer.stop()
            self._consumer = None
        if self._dlq is not None:
            await self._dlq.stop()
            self._dlq = None

    async def _run(self) -> None:
        assert self._consumer is not None
        async for msg in self._consumer:
            carrier = {k: v.decode() for k, v in (msg.headers or [])}
            ctx = propagate.extract(carrier)
            with _tracer.start_as_current_span(f"consume {msg.topic}", context=ctx):
                await self._dispatch(msg)

    async def _dispatch(self, msg: Any) -> None:
        try:
            event = EventEnvelope.model_validate_json(msg.value)
        except Exception:
            _log.error("event_decode_failed", topic=msg.topic)
            await self._dead_letter(msg, reason="decode_error")
            return

        for attempt in range(self._retries + 1):
            try:
                await self._handler(event)
                return
            except Exception as exc:  # noqa: BLE001 - retry then dead-letter any handler failure
                if attempt >= self._retries:
                    _log.error(
                        "event_handler_failed",
                        topic=msg.topic,
                        event_id=event.event_id,
                        error=str(exc),
                    )
                    await self._dead_letter(msg, reason=str(exc))
                    return
                await asyncio.sleep(0.05 * (attempt + 1))

    async def _dead_letter(self, msg: Any, *, reason: str) -> None:
        if self._dlq is None:
            return
        headers = list(msg.headers or []) + [("x-dlq-reason", reason.encode())]
        await self._dlq.send_and_wait(
            msg.topic + self._dlq_suffix, value=msg.value, headers=headers
        )


def producer_lifespan(
    producer: Producer,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def hook(_app: FastAPI):
        await producer.start()
        try:
            yield
        finally:
            await producer.stop()

    return hook


def consumer_lifespan(
    consumer: Consumer,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def hook(_app: FastAPI):
        await consumer.start()
        try:
            yield
        finally:
            await consumer.stop()

    return hook
