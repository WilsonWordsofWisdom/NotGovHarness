from fastapi.testclient import TestClient
from sandbox_service.main import app


def test_healthz():
    assert TestClient(app).get("/healthz").json() == {"status": "ok"}
