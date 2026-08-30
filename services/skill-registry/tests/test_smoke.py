from fastapi.testclient import TestClient
from skill_registry.main import app


def test_healthz():
    assert TestClient(app).get("/healthz").json() == {"status": "ok"}


def test_hello():
    assert TestClient(app).get("/hello").json()["service"] == "skill-registry"
