"""Infra-free: ContextForgeClient's retry-on-401 behavior (D-046) against a stub ContextForge
server that simulates a session ContextForge itself has invalidated — the exact situation found
live when ContextForge was restarted mid-session and kept rejecting the façade's still-cached,
not-yet-expired-by-our-clock token.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from agent_gateway.contextforge import ContextForgeClient, ContextForgeError


@pytest.fixture
def stub_contextforge():
    state = {"logins": 0, "valid_token": None}

    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, status: int, body: dict) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)  # drain the body so keep-alive framing stays correct
            if self.path == "/auth/email/login":
                state["logins"] += 1
                state["valid_token"] = f"token-{state['logins']}"
                self._send_json(200, {"access_token": state["valid_token"], "expires_in": 1200})
                return
            if self.path == "/gateways":
                auth = self.headers.get("Authorization", "")
                token = auth.removeprefix("Bearer ")
                if token != state["valid_token"]:
                    self._send_json(401, {"detail": "Invalid authentication credentials"})
                    return
                self._send_json(201, {"id": 1, "name": "x"})
                return
            self._send_json(404, {"detail": "not found"})

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", state
    server.shutdown()


async def test_a_401_from_an_unexpired_cached_token_is_retried_with_a_fresh_login(
    stub_contextforge,
):
    base_url, state = stub_contextforge
    client = ContextForgeClient(base_url, "admin@example.com", "changeme123")

    # First call: logs in, succeeds normally.
    await client.register_gateway("x", "http://x:8000")
    assert state["logins"] == 1

    # Simulate ContextForge invalidating that session server-side (e.g. a restart) without the
    # client's own expiry clock knowing — the *next* call must still succeed, via a retry.
    state["valid_token"] = "a-completely-different-token-issued-out-of-band"
    result = await client.register_gateway("y", "http://y:8000")
    assert result == {"id": 1, "name": "x"}
    assert state["logins"] == 2  # retried with a fresh login, not a second failure


async def test_a_persistent_401_still_raises_after_one_retry(stub_contextforge, monkeypatch):
    base_url, state = stub_contextforge
    client = ContextForgeClient(base_url, "admin@example.com", "wrong-password")

    async def _always_wrong_login(_client) -> str:
        # Login "succeeds" (stub doesn't check the password) but issue a token that never
        # matches what /gateways expects, so every request 401s regardless of retry.
        state["valid_token"] = "server-expects-this-token"
        client._token = "client-has-this-token"  # noqa: SLF001
        client._token_expires_at = 10**12  # noqa: SLF001
        return "client-has-this-token"

    monkeypatch.setattr(client, "_login", _always_wrong_login)

    with pytest.raises(ContextForgeError) as exc_info:
        await client.register_gateway("z", "http://z:8000")
    assert exc_info.value.status_code == 401
