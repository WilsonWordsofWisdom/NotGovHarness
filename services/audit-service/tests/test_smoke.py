from audit_service.main import app
from fastapi.testclient import TestClient


def test_healthz():
    assert TestClient(app).get("/healthz").json() == {"status": "ok"}


def test_hello():
    assert TestClient(app).get("/hello").json()["service"] == "audit-service"
