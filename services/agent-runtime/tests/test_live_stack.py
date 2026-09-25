"""Skip-if-down: a real POST /invoke runs a genuine CrewAI kickoff against real llm-gateway.

Runs the FastAPI app in-process (TestClient) against the real running identity-service and
llm-gateway — this proves the invoke path for real; the separate, manual step of actually
launching this service *as a Docker container* (the way deployment-pipeline-service will) is
this branch's own live-verification step, not a committed automated test (same split the design
doc's testing strategy calls for).
"""

from __future__ import annotations

import os

os.environ.setdefault("AGENT_ROLE", "Capital cities expert")
os.environ.setdefault("AGENT_GOAL", "Answer questions about world capitals accurately.")
os.environ.setdefault(
    "AGENT_BACKSTORY", "You are a geography expert who only answers questions about capitals."
)

import httpx  # noqa: E402
from agent_runtime.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


async def _token(identity_url: str, client_id: str, client_secret: str, scope: str) -> str:
    async with httpx.AsyncClient(base_url=identity_url) as client:
        resp = await client.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": scope,
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def test_invoke_runs_a_real_crewai_kickoff(
    platform_identity_url, platform_llm_gateway_url
):
    token = await _token(
        platform_identity_url,
        "example-service",
        "example-service-dev-secret",
        "agent_runtime:invoke",
    )

    client = TestClient(app)
    resp = client.post(
        "/invoke",
        json={"input": "What is the capital of France? Answer in a single full sentence."},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert "paris" in resp.json()["output"].lower()


def test_invoke_requires_scope():
    client = TestClient(app)
    resp = client.post("/invoke", json={"input": "anything"})
    assert resp.status_code == 401
