import pytest
from fastapi.testclient import TestClient

from platform_core.app import create_app
from platform_core.config import PlatformSettings


def _client(**kwargs) -> TestClient:
    app = create_app(PlatformSettings(service_name="test-service"), **kwargs)
    return TestClient(app)


def test_healthz():
    r = _client().get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_readyz_ok_without_checks():
    r = _client().get("/readyz")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_readyz_reports_failure():
    async def failing_check() -> None:
        raise RuntimeError("db down")

    r = _client(readiness_checks=[failing_check]).get("/readyz")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "not_ready"
    assert "db down" in body["checks"][0]


def test_request_id_echoed():
    r = _client().get("/healthz")
    assert r.headers.get("x-request-id")


def test_request_id_preserved():
    r = _client().get("/healthz", headers={"x-request-id": "abc123"})
    assert r.headers["x-request-id"] == "abc123"


@pytest.mark.asyncio
async def test_lifespan_hook_runs():
    events: list[str] = []

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def hook(_app):
        events.append("startup")
        yield
        events.append("shutdown")

    app = create_app(PlatformSettings(service_name="t"), lifespan_hooks=[hook])
    with TestClient(app):
        pass
    assert events == ["startup", "shutdown"]
