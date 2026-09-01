"""Skip-if-down: the actual end-to-end flow this harness exists to prove — a real bearer token,
a real llm-gateway façade, a real LiteLLM, a real (local) model, a real completion. Neither
LiteLLM's master key nor its virtual key ever appear in a caller-facing response.
"""

from __future__ import annotations

import httpx


async def _token(identity_url: str) -> str:
    async with httpx.AsyncClient(base_url=identity_url) as client:
        resp = await client.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "example-service",
                "client_secret": "example-service-dev-secret",
                "scope": "llm_gateway:call",
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def test_real_completion_through_the_full_chain(
    platform_identity_url, platform_llm_gateway_url
):
    token = await _token(platform_identity_url)
    async with httpx.AsyncClient(base_url=platform_llm_gateway_url, timeout=60.0) as client:
        resp = await client.post(
            "/chat/completions",
            json={
                "model": "qwen3.8",
                "messages": [{"role": "user", "content": "Reply with exactly one word: pong"}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "choices" in body
        assert body["choices"][0]["message"]["role"] == "assistant"
        assert body["usage"]["total_tokens"] > 0
        # Neither LiteLLM secret should ever appear in a response this façade returns.
        raw = resp.text
        assert "sk-litellm-master" not in raw
        assert "sk-x65z" not in raw


async def test_missing_scope_is_rejected(platform_identity_url, platform_llm_gateway_url):
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

    async with httpx.AsyncClient(base_url=platform_llm_gateway_url, timeout=15.0) as client:
        resp = await client.post(
            "/chat/completions",
            json={"model": "qwen3.8", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
