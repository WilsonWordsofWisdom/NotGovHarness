"""Skip-if-down: GET /skills, GET /skills/{name}[/{version}], and the bundle download endpoint,
against real Postgres + MinIO. Reuses the same stub-JWKS + skill_registry_test setup as
test_publish.py — see that file's docstring for why both are needed.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO

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

SKILL_REGISTRY_TEST_DB_URL = (
    "postgresql+asyncpg://platform:platform@localhost:5432/skill_registry_test"
)


def _zip_bundle(name: str, skill_md: bytes, **extra_files: bytes) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(f"{name}/SKILL.md", skill_md)
        for path, content in extra_files.items():
            archive.writestr(f"{name}/{path}", content)
    return buf.getvalue()


def _skill_md(name: str, description: str = "A valid description of this skill.") -> bytes:
    return f"---\nname: {name}\ndescription: {description}\n---\n\nBody instructions.".encode()


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
    bucket = f"skill-registry-test-{int(time.time() * 1000)}"
    monkeypatch.setenv("DATABASE_URL", SKILL_REGISTRY_TEST_DB_URL)
    monkeypatch.setenv("OAUTH2_JWKS_URL", jwks_server)
    monkeypatch.setenv("SERVICE_NAME", "skill-registry")
    monkeypatch.setenv("MINIO_ENDPOINT", platform_minio_endpoint)
    monkeypatch.setenv("MINIO_BUCKET", bucket)

    from skill_registry import main as main_module
    from skill_registry.models import Skill

    from platform_core.db import Database

    importlib.reload(main_module)

    async def _clear() -> None:
        db = Database(SKILL_REGISTRY_TEST_DB_URL)
        async with db.session() as session:
            await session.execute(delete(Skill))
            await session.commit()
        await db.dispose()

    asyncio.run(_clear())
    return main_module.app


def _publish(client: TestClient, private_pem, name: str, version: str, **overrides) -> None:
    token = _token(private_pem, "skill_registry:publish")
    md_kwargs = {}
    if "description" in overrides:
        md_kwargs["description"] = overrides.pop("description")
    data = _zip_bundle(name, _skill_md(name, **md_kwargs), **overrides)
    resp = client.post(
        "/skills",
        files={"file": (f"{name}.zip", data, "application/zip")},
        data={"version": version},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text


def test_list_is_discovery_shaped_and_supports_search(app, private_pem):
    with TestClient(app) as client:
        _publish(
            client,
            private_pem,
            "widget-skill",
            "1.0.0",
            description="Handles widgets. Use when the user mentions widgets.",
        )
        _publish(
            client,
            private_pem,
            "gadget-skill",
            "1.0.0",
            description="Handles gadgets and gizmos.",
        )

        listing = client.get("/skills").json()
        assert listing == sorted(listing, key=lambda r: r["name"])
        assert set(listing[0].keys()) == {"name", "description"}
        assert {row["name"] for row in listing} == {"widget-skill", "gadget-skill"}

        filtered = client.get("/skills", params={"q": "gizmo"}).json()
        assert [row["name"] for row in filtered] == ["gadget-skill"]


def test_get_latest_returns_the_most_recently_published_version(app, private_pem):
    with TestClient(app) as client:
        _publish(client, private_pem, "widget-skill", "1.0.0")
        _publish(client, private_pem, "widget-skill", "2.0.0")

        latest = client.get("/skills/widget-skill").json()
        assert latest["version"] == "2.0.0"

        pinned = client.get("/skills/widget-skill/1.0.0").json()
        assert pinned["version"] == "1.0.0"
        assert "name: widget-skill" in pinned["skill_md"]


def test_get_unknown_skill_is_404(app):
    with TestClient(app) as client:
        resp = client.get("/skills/does-not-exist")
    assert resp.status_code == 404


def test_bundle_download_matches_the_uploaded_archive_byte_for_byte(app, private_pem):
    with TestClient(app) as client:
        token = _token(private_pem, "skill_registry:publish")
        data = _zip_bundle(
            "widget-skill", _skill_md("widget-skill"), **{"scripts/run.py": b"print('hi')"}
        )
        resp = client.post(
            "/skills",
            files={"file": ("widget-skill.zip", data, "application/zip")},
            data={"version": "1.0.0"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201

        download = client.get("/skills/widget-skill/1.0.0/bundle")
    assert download.status_code == 200
    assert download.content == data
    assert download.headers["content-type"] == "application/zip"
