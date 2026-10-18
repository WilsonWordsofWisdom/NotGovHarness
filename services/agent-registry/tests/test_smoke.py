from agent_registry.main import app
from fastapi.testclient import TestClient


def test_healthz():
    assert TestClient(app).get("/healthz").json() == {"status": "ok"}


def test_ui_index_is_served():
    resp = TestClient(app).get("/ui/")
    assert resp.status_code == 200
    assert "Agent Registry" in resp.text
