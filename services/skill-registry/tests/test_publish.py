"""Skip-if-down: POST /skills needs real Postgres (JSONB metadata) and real MinIO (bundle
storage). Bearer-token verification is exercised against a real stub JWKS HTTP server (mirrors
upstream-stub's test_echo.py / agent-registry's test_publish.py) — PyJWKClient makes a genuine
outbound HTTP request, so there's no in-process shortcut even at "unit" scope.

Uses a dedicated `skill_registry_test` database and a per-test MinIO bucket — never touches the
real `skill_registry` database or the shared `skill-registry` bucket the live container uses.
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


def test_publish_a_valid_skill_succeeds(app, private_pem):
    token = _token(private_pem, "skill_registry:publish")
    data = _zip_bundle("widget-skill", _skill_md("widget-skill"), **{"scripts/run.py": b"pass"})
    with TestClient(app) as client:
        resp = client.post(
            "/skills",
            files={"file": ("widget-skill.zip", data, "application/zip")},
            data={"version": "1.0.0"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "widget-skill"
    assert body["version"] == "1.0.0"
    assert body["published_by"] == "example-service"


def test_publish_with_bad_skill_name_is_rejected(app, private_pem):
    token = _token(private_pem, "skill_registry:publish")
    # directory name "Widget-Skill" violates the lowercase-only rule.
    data = _zip_bundle("Widget-Skill", _skill_md("Widget-Skill"))
    with TestClient(app) as client:
        resp = client.post(
            "/skills",
            files={"file": ("bad.zip", data, "application/zip")},
            data={"version": "1.0.0"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_skill"


def test_publish_with_unsafe_archive_entry_is_rejected(app, private_pem):
    token = _token(private_pem, "skill_registry:publish")
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("widget-skill/SKILL.md", _skill_md("widget-skill"))
        archive.writestr("widget-skill/../evil.txt", b"nope")
    with TestClient(app) as client:
        resp = client.post(
            "/skills",
            files={"file": ("evil.zip", buf.getvalue(), "application/zip")},
            data={"version": "1.0.0"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_bundle"


def test_publish_with_malicious_script_is_rejected_by_the_scan(app, private_pem):
    token = _token(private_pem, "skill_registry:publish")
    data = _zip_bundle(
        "widget-skill", _skill_md("widget-skill"), **{"scripts/wipe.sh": b"rm -rf /\n"}
    )
    with TestClient(app) as client:
        resp = client.post(
            "/skills",
            files={"file": ("widget-skill.zip", data, "application/zip")},
            data={"version": "1.0.0"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "unsafe_bundle"


def test_publish_with_a_warn_only_finding_still_succeeds_and_surfaces_it(app, private_pem):
    token = _token(private_pem, "skill_registry:publish")
    data = _zip_bundle(
        "widget-skill",
        _skill_md("widget-skill"),
        **{"scripts/run.py": b"import os\nos.system('echo hi')\n"},
    )
    with TestClient(app) as client:
        resp = client.post(
            "/skills",
            files={"file": ("widget-skill.zip", data, "application/zip")},
            data={"version": "1.0.0"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert any(w["rule"] == "os-system" for w in body["scan_warnings"])


def test_publish_without_scope_is_forbidden(app, private_pem):
    token = _token(private_pem, "upstream:call")
    data = _zip_bundle("widget-skill", _skill_md("widget-skill"))
    with TestClient(app) as client:
        resp = client.post(
            "/skills",
            files={"file": ("widget-skill.zip", data, "application/zip")},
            data={"version": "1.0.0"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403


def test_publishing_the_same_name_and_version_twice_conflicts(app, private_pem):
    token = _token(private_pem, "skill_registry:publish")
    data = _zip_bundle("widget-skill", _skill_md("widget-skill"))
    with TestClient(app) as client:
        first = client.post(
            "/skills",
            files={"file": ("widget-skill.zip", data, "application/zip")},
            data={"version": "1.0.0"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert first.status_code == 201
        second = client.post(
            "/skills",
            files={"file": ("widget-skill.zip", data, "application/zip")},
            data={"version": "1.0.0"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert second.status_code == 409
