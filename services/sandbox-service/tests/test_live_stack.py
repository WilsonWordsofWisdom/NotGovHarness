"""Skip-if-down: the HTTP/auth/persistence layer around the (already-proven, in test_executor.py)
Docker-backed executor — a real identity-service token, a real sandbox-service, a real execution.
"""

from __future__ import annotations

import httpx


async def _token(identity_url: str, scope: str) -> str:
    async with httpx.AsyncClient(base_url=identity_url) as client:
        resp = await client.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "example-service",
                "client_secret": "example-service-dev-secret",
                "scope": scope,
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def test_submit_and_read_back_a_real_execution(platform_identity_url, platform_sandbox_url):
    token = await _token(platform_identity_url, "sandbox:execute")
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(base_url=platform_sandbox_url, timeout=30.0) as client:
        submitted = await client.post(
            "/executions",
            json={"language": "python", "code": "print('hello from sandbox')"},
            headers=headers,
        )
        assert submitted.status_code == 201, submitted.text
        body = submitted.json()
        assert body["status"] == "completed"
        assert body["stdout"] == "hello from sandbox\n"

        fetched = await client.get(f"/executions/{body['id']}", headers=headers)
        assert fetched.status_code == 200
        assert fetched.json()["stdout"] == "hello from sandbox\n"


async def test_missing_scope_is_rejected(platform_identity_url, platform_sandbox_url):
    # example-service's token without the sandbox scope requested — a caller with no
    # sandbox:execute grant at all should be refused, not silently allowed through.
    async with httpx.AsyncClient(base_url=platform_identity_url) as client:
        resp = await client.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "example-service",
                "client_secret": "example-service-dev-secret",
                "scope": "upstream:call",
            },
        )
        resp.raise_for_status()
        token = resp.json()["access_token"]

    async with httpx.AsyncClient(base_url=platform_sandbox_url, timeout=15.0) as client:
        resp = await client.post(
            "/executions",
            json={"language": "python", "code": "print(1)"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


async def test_a_runaway_execution_is_killed_and_recorded(
    platform_identity_url, platform_sandbox_url
):
    token = await _token(platform_identity_url, "sandbox:execute")
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(base_url=platform_sandbox_url, timeout=30.0) as client:
        submitted = await client.post(
            "/executions",
            json={"language": "python", "code": "while True: pass", "timeout_seconds": 2},
            headers=headers,
        )
        assert submitted.status_code == 201, submitted.text
        body = submitted.json()
        assert body["status"] == "timed_out"
        assert body["exit_code"] is None
