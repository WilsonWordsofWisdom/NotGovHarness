from fastapi.testclient import TestClient
from skill_registry.main import app


def test_healthz():
    assert TestClient(app).get("/healthz").json() == {"status": "ok"}


def test_ui_index_is_served():
    resp = TestClient(app).get("/ui/")
    assert resp.status_code == 200
    assert "Skill Registry" in resp.text
