"""Infra-free: TokenClient's cache/refresh behavior (D-062, D-063) against a stub identity-service
that simulates `/oauth/token`, same pattern as mcp-gateway-registry's test_contextforge_client.py.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from platform_core.llm_gateway_auth import TokenClient, call_with_auth_retry


@pytest.fixture
def stub_identity():
    state = {"issued": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)  # drain the body so keep-alive framing stays correct
            if self.path == "/oauth/token":
                state["issued"] += 1
                body = {"access_token": f"token-{state['issued']}", "expires_in": 300}
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", state
    server.shutdown()


async def test_ensure_token_caches_within_the_ttl(stub_identity):
    base_url, state = stub_identity
    client = TokenClient(identity_url=base_url, client_id="mem0-service", client_secret="x")

    t1 = await client.ensure_token()
    t2 = await client.ensure_token()
    assert t1 == t2
    assert state["issued"] == 1


async def test_ensure_token_refetches_once_within_the_safety_margin(stub_identity):
    base_url, state = stub_identity
    client = TokenClient(identity_url=base_url, client_id="mem0-service", client_secret="x")

    await client.ensure_token()
    # Simulate the cached token being within the 30s safety margin of expiry.
    client._token_expires_at = time.monotonic() + 5  # noqa: SLF001
    t2 = await client.ensure_token()

    assert state["issued"] == 2
    assert t2 == "token-2"


async def test_force_refresh_bypasses_a_still_fresh_cache(stub_identity):
    """D-063: this is the reactive backstop's actual mechanism — a token that *looks* unexpired
    by this process's own clock (e.g. because Docker Desktop suspended the VM's clock during a
    host idle period) can still be genuinely expired by wall-clock time. force_refresh() must not
    trust the cache at all, unlike ensure_token()."""
    base_url, state = stub_identity
    client = TokenClient(identity_url=base_url, client_id="mem0-service", client_secret="x")

    t1 = await client.ensure_token()
    # Cache looks perfectly fresh from this process's point of view...
    assert await client.ensure_token() == t1
    assert state["issued"] == 1

    # ...but force_refresh() must still fetch a new one regardless.
    t2 = await client.force_refresh()
    assert t2 != t1
    assert state["issued"] == 2


async def test_call_with_auth_retry_succeeds_on_the_first_try_when_nothing_is_wrong(
    stub_identity,
):
    base_url, state = stub_identity
    client = TokenClient(identity_url=base_url, client_id="mem0-service", client_secret="x")
    applied: list[str] = []

    result = await call_with_auth_retry(client, applied.append, lambda: _ok("done"))

    assert result == "done"
    assert state["issued"] == 1
    assert applied == ["token-1"]


async def test_call_with_auth_retry_recovers_from_an_auth_shaped_failure(stub_identity):
    """The exact scenario D-063 found live: the proactive check thinks the cached token is fine,
    but the actual call fails with an auth-shaped error anyway (e.g. wall-clock expiry the
    process's own monotonic clock didn't detect). One retry, with a force-refreshed token, must
    save it."""
    base_url, state = stub_identity
    client = TokenClient(identity_url=base_url, client_id="mem0-service", client_secret="x")
    applied: list[str] = []
    attempts = {"count": 0}

    async def flaky_call() -> str:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("Error code: 401 - {'error': {'message': 'Signature has expired'}}")
        return "recovered"

    result = await call_with_auth_retry(client, applied.append, flaky_call)

    assert result == "recovered"
    assert state["issued"] == 2  # the initial fetch, then one force_refresh
    assert applied == ["token-1", "token-2"]


async def test_call_with_auth_retry_raises_a_non_auth_failure_immediately(stub_identity):
    base_url, _state = stub_identity
    client = TokenClient(identity_url=base_url, client_id="mem0-service", client_secret="x")

    async def always_fails() -> str:
        raise ValueError("something unrelated to auth broke")

    with pytest.raises(ValueError, match="unrelated to auth"):
        await call_with_auth_retry(client, lambda _token: None, always_fails)


async def _ok(value: str) -> str:
    return value
