from agent_gateway.main import app
from fastapi.testclient import TestClient


def test_healthz():
    assert TestClient(app).get("/healthz").json() == {"status": "ok"}
