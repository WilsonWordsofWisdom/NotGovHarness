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

import httpx
import pytest

from platform_core.db import Database
from platform_core.events import Consumer, EventEnvelope

PG_URL = os.getenv(
    "PLATFORM_TEST_DATABASE_URL",
    "postgresql+asyncpg://platform:platform@localhost:5432/example_service",
)
KAFKA = os.getenv("PLATFORM_TEST_KAFKA", "localhost:9092")
LANGFUSE_URL = os.getenv("PLATFORM_TEST_LANGFUSE_URL", "http://localhost:3000")
# Seeded dev key pair (LANGFUSE_INIT_PROJECT_* in docker-compose.yml's langfuse-web service).
LANGFUSE_AUTH = (
    os.getenv("PLATFORM_TEST_LANGFUSE_PUBLIC_KEY", "pk-lf-00000000-0000-4000-8000-000000000000"),
    os.getenv("PLATFORM_TEST_LANGFUSE_SECRET_KEY", "sk-lf-00000000-0000-4000-8000-000000000000"),
)


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


class LangfuseProbe:
    """Polls Langfuse's Observations API v2 for a span by name (ingestion is async — the
    otel-collector batches, and langfuse-worker consumes a queue — so this retries, not one-shot).
    """

    def __init__(self, base_url: str, auth: tuple[str, str]) -> None:
        self._base_url = base_url
        self._auth = auth

    async def wait_for_observation(
        self,
        name: str,
        *,
        timeout: float = 30.0,  # noqa: ASYNC109 - probe budget, not a cancellation deadline
        interval: float = 2.0,
    ) -> dict | None:
        async with httpx.AsyncClient(base_url=self._base_url, auth=self._auth) as client:
            deadline = asyncio.get_event_loop().time() + timeout
            while True:
                resp = await client.get(
                    "/api/public/v2/observations", params={"name": name, "limit": 1}
                )
                resp.raise_for_status()
                data = resp.json()["data"]
                if data:
                    return data[0]
                if asyncio.get_event_loop().time() >= deadline:
                    return None
                await asyncio.sleep(interval)


@pytest.fixture
def platform_langfuse() -> LangfuseProbe:
    host, _, port = LANGFUSE_URL.replace("http://", "").replace("https://", "").partition(":")
    if not _reachable(host or "localhost", int(port or "3000")):
        pytest.skip(
            "Langfuse not reachable on "
            f"{LANGFUSE_URL} (start the stack: "
            "docker compose --profile core --profile clickhouse --profile objectstore "
            "--profile observability up -d)"
        )
    return LangfuseProbe(LANGFUSE_URL, LANGFUSE_AUTH)
