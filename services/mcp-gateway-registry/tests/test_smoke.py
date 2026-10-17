from fastapi.testclient import TestClient
from mcp_gateway_registry.main import app


def test_healthz():
    assert TestClient(app).get("/healthz").json() == {"status": "ok"}
