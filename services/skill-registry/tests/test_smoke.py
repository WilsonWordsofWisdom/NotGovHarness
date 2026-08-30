from fastapi.testclient import TestClient
from skill_registry.main import app


def test_healthz():
    assert TestClient(app).get("/healthz").json() == {"status": "ok"}
