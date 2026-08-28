from fastapi.testclient import TestClient
from identity_service.main import app


def test_healthz():
    assert TestClient(app).get("/healthz").json() == {"status": "ok"}


def test_jwks_exposes_a_key():
    body = TestClient(app).get("/.well-known/jwks.json").json()
    assert body["keys"][0]["kty"] == "RSA"
    assert body["keys"][0]["alg"] == "RS256"
