import os
import socket

import pytest

PG_URL = os.getenv(
    "PLATFORM_TEST_DATABASE_URL",
    "postgresql+asyncpg://platform:platform@localhost:5432/example_service",
)
KAFKA = os.getenv("PLATFORM_TEST_KAFKA", "localhost:9092")
MINIO_ENDPOINT = os.getenv("PLATFORM_TEST_MINIO_ENDPOINT", "localhost:9090")


def _reachable(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture
def pg_url() -> str:
    if not _reachable("localhost", 5432):
        pytest.skip("Postgres not reachable on localhost:5432 (start the stack: task up)")
    return PG_URL


@pytest.fixture
def kafka_brokers() -> str:
    host, _, port = KAFKA.partition(":")
    if not _reachable(host or "localhost", int(port or "9092")):
        pytest.skip("Redpanda/Kafka not reachable on localhost:9092 (start the stack: task up)")
    return KAFKA


@pytest.fixture
def minio_endpoint() -> str:
    host, _, port = MINIO_ENDPOINT.partition(":")
    if not _reachable(host or "localhost", int(port or "9090")):
        pytest.skip(
            "MinIO not reachable on localhost:9090 (start the stack: "
            "docker compose --profile core --profile objectstore up -d)"
        )
    return MINIO_ENDPOINT
