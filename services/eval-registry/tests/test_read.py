"""Skip-if-down: GET /suites, GET /suites/{name}[/{version}], and the dataset download
endpoint, against real Postgres + MinIO. Reuses the same stub-JWKS + eval_registry_test setup
as test_publish.py — see that file's docstring for why both are needed.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from sqlalchemy import delete

from platform_testing.fixtures import _reachable

ISSUER = "https://identity-service.notgovharness.local"
AUDIENCE = "notgovharness"
KID = "test-kid"

EVAL_REGISTRY_TEST_DB_URL = (
    "postgresql+asyncpg://platform:platform@localhost:5432/eval_registry_test"
)


def _cases_metadata(name: str, version: str, **overrides) -> dict:
    return {
        "name": name,
        "version": version,
        "description": overrides.pop("description", "A test suite."),
        "kind": "cases",
        "applies_to": overrides.pop("applies_to", ["tool_use"]),
        "metrics": [{"engine": "deepeval", "metric_id": "ToolCorrectnessMetric", "params": {}}],
        **overrides,
    }


def _redteam_metadata(name: str, version: str) -> dict:
    return {
        "name": name,
        "version": version,
        "description": "A red-team suite.",
        "kind": "redteam",
        "applies_to": ["always"],
        "redteam_config": {"purpose": "probe for jailbreak resistance"},
    }


VALID_JSONL = '{"input": "What is 2+2?", "expected_output": "4"}\n'


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def private_pem(rsa_key):
    return rsa_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


@pytest.fixture(scope="module")
def jwks_server(rsa_key):
    jwk = json.loads(RSAAlgorithm.to_jwk(rsa_key.public_key()))
    jwk.update(kid=KID, use="sig", alg="RS256")
    body = json.dumps({"keys": [jwk]}).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/jwks.json"
    server.shutdown()


def _token(private_pem, scope: str) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 300,
        "sub": "example-service",
        "scope": scope,
        "mode": "autonomous",
    }
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": KID})


@pytest.fixture
def app(jwks_server, platform_minio_endpoint, monkeypatch):
    if not _reachable("localhost", 5432):
        pytest.skip("Postgres not reachable on localhost:5432 (start the stack: task up)")
    bucket = f"eval-registry-test-{int(time.time() * 1000)}"
    monkeypatch.setenv("DATABASE_URL", EVAL_REGISTRY_TEST_DB_URL)
    monkeypatch.setenv("OAUTH2_JWKS_URL", jwks_server)
    monkeypatch.setenv("SERVICE_NAME", "eval-registry")
    monkeypatch.setenv("MINIO_ENDPOINT", platform_minio_endpoint)
    monkeypatch.setenv("MINIO_BUCKET", bucket)

    from eval_registry import main as main_module
    from eval_registry.models import Suite

    from platform_core.db import Database

    importlib.reload(main_module)

    async def _clear() -> None:
        db = Database(EVAL_REGISTRY_TEST_DB_URL)
        async with db.session() as session:
            await session.execute(delete(Suite))
            await session.commit()
        await db.dispose()

    asyncio.run(_clear())
    return main_module.app


def _publish(client: TestClient, private_pem, metadata: dict, dataset: bytes | None = None):
    token = _token(private_pem, "eval_registry:publish")
    files = {"dataset": ("dataset.jsonl", dataset, "application/x-ndjson")} if dataset else None
    resp = client.post(
        "/suites",
        data={"metadata": json.dumps(metadata)},
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text


def test_list_and_applies_to_filter(app, private_pem):
    with TestClient(app) as client:
        _publish(client, private_pem, _cases_metadata("tool-suite", "1.0.0"), VALID_JSONL.encode())
        _publish(
            client,
            private_pem,
            _cases_metadata("rag-suite", "1.0.0", applies_to=["rag"]),
            VALID_JSONL.encode(),
        )

        listing = client.get("/suites").json()
        assert {row["name"] for row in listing} == {"tool-suite", "rag-suite"}

        filtered = client.get("/suites", params={"applies_to": "rag"}).json()
        assert [row["name"] for row in filtered] == ["rag-suite"]


def test_get_latest_returns_the_most_recently_published_version(app, private_pem):
    with TestClient(app) as client:
        _publish(client, private_pem, _cases_metadata("tool-suite", "1.0.0"), VALID_JSONL.encode())
        _publish(client, private_pem, _cases_metadata("tool-suite", "2.0.0"), VALID_JSONL.encode())

        latest = client.get("/suites/tool-suite").json()
        assert latest["version"] == "2.0.0"

        pinned = client.get("/suites/tool-suite/1.0.0").json()
        assert pinned["version"] == "1.0.0"


def test_get_unknown_suite_is_404(app):
    with TestClient(app) as client:
        resp = client.get("/suites/does-not-exist")
    assert resp.status_code == 404


def test_dataset_download_matches_the_uploaded_jsonl_byte_for_byte(app, private_pem):
    with TestClient(app) as client:
        _publish(client, private_pem, _cases_metadata("tool-suite", "1.0.0"), VALID_JSONL.encode())
        download = client.get("/suites/tool-suite/1.0.0/dataset")
    assert download.status_code == 200
    assert download.content == VALID_JSONL.encode()
    assert download.headers["content-type"] == "application/x-ndjson"


def test_dataset_download_for_a_redteam_suite_is_404(app, private_pem):
    with TestClient(app) as client:
        _publish(client, private_pem, _redteam_metadata("safety-suite", "1.0.0"))
        download = client.get("/suites/safety-suite/1.0.0/dataset")
    assert download.status_code == 404
    assert download.json()["error"]["code"] == "no_dataset"
