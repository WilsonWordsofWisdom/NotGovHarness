from fastapi.testclient import TestClient
from llm_gateway.main import app


def test_healthz():
    assert TestClient(app).get("/healthz").json() == {"status": "ok"}
