"""Infra-free: TokenClient's cache/refresh behavior (D-062, D-063) against a stub identity-service
that simulates `/oauth/token`, same pattern as agent-gateway's test_contextforge_client.py.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from mem0_service.llm_gateway_client import TokenClient


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
