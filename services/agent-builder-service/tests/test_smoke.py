from agent_builder_service.main import app, bump_version
from fastapi.testclient import TestClient


def test_healthz():
    assert TestClient(app).get("/healthz").json() == {"status": "ok"}


def test_ui_index_is_served():
    resp = TestClient(app).get("/ui/")
    assert resp.status_code == 200
    assert "Agent Builder" in resp.text


def test_bump_version_increments_minor_and_resets_patch():
    assert bump_version("0.1.0") == "0.2.0"
    assert bump_version("0.9.3") == "0.10.0"
    assert bump_version("1.2.5") == "1.3.0"
