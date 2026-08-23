"""Reusable pytest fixtures for service integration tests.

Endpoint-driven: fixtures read connection info from env (localhost defaults matching the `core`
compose stack) and skip when the stack is not reachable. Testcontainers-based ephemeral infra is
deferred until the Docker Engine is upgraded (see docs/implementation-plan.md).

Use in a service's tests via ``conftest.py``::

    from platform_testing.fixtures import *  # noqa: F401,F403
"""

from __future__ import annotations

import asyncio
import os
import socket
from uuid import uuid4

import pytest

from platform_core.db import Database
from platform_core.events import Consumer, EventEnvelope

PG_URL = os.getenv(
    "PLATFORM_TEST_DATABASE_URL",
    "postgresql+asyncpg://platform:platform@localhost:5432/example_service",
)
KAFKA = os.getenv("PLATFORM_TEST_KAFKA", "localhost:9092")


def _reachable(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture
def platform_pg_url() -> str:
    if not _reachable("localhost", 5432):
        pytest.skip("Postgres not reachable on localhost:5432 (start the stack: task up)")
    return PG_URL


@pytest.fixture
def platform_kafka_brokers() -> str:
    host, _, port = KAFKA.partition(":")
    if not _reachable(host or "localhost", int(port or "9092")):
        pytest.skip("Redpanda/Kafka not reachable on localhost:9092 (start the stack: task up)")
    return KAFKA


@pytest.fixture
async def platform_database(platform_pg_url: str):
    db = Database(platform_pg_url)
    try:
        yield db
    finally:
        await db.dispose()


class EventProbe:
    """Collects events published to a topic, for asserting a service emitted them."""

    def __init__(self, brokers: str) -> None:
        self._brokers = brokers

    async def collect(
        self,
        topic: str,
        *,
        count: int = 1,
        timeout: float = 30.0,  # noqa: ASYNC109 - probe budget, not a cancellation deadline
    ) -> list[EventEnvelope]:
        received: list[EventEnvelope] = []
        done = asyncio.Event()

        async def handler(event: EventEnvelope) -> None:
            received.append(event)
            if len(received) >= count:
                done.set()

        consumer = Consumer(self._brokers, f"probe-{uuid4().hex}", [topic], handler)
        await consumer.start()
        try:
            await asyncio.wait_for(done.wait(), timeout=timeout)
        except TimeoutError:
            pass  # return whatever was collected within the window
        finally:
            await consumer.stop()
        return received


@pytest.fixture
def event_probe(platform_kafka_brokers: str) -> EventProbe:
    return EventProbe(platform_kafka_brokers)
