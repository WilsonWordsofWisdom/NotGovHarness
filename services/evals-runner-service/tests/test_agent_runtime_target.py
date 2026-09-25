"""Skip-if-down: `target: {"type": "agent_runtime", ...}` judges a real, manually-started
`agent-runtime` container for real (D-089) — the deployed-agent target, not the default
direct-`llm-gateway` one (which the rest of this service's own test suite already covers
unchanged, confirming this addition is backward-compatible).

Unlike every other `platform_testing` fixture, there's no compose-managed, always-up instance to
point at: `agent-runtime` is a build-only image (D-087), instantiated per deployment. Start one
by hand before running this test:

    docker build -f services/agent-runtime/Dockerfile -t notgovharness-agent-runtime:latest .
    docker run -d --rm --name agent-runtime-for-evals-test \\
      -e AGENT_ROLE="Capital cities expert" \\
      -e AGENT_GOAL="Answer questions about world capitals accurately." \\
      -e AGENT_BACKSTORY="You are a geography expert who only answers questions about capitals." \\
      -e OAUTH2_JWKS_URL="http://host.docker.internal:8090/.well-known/jwks.json" \\
      -e IDENTITY_URL="http://host.docker.internal:8090" \\
      -e LLM_GATEWAY_URL="http://host.docker.internal:8099" \\
      -e AGENT_RUNTIME_CLIENT_SECRET="agent-runtime-dev-secret" \\
      -p 18101:8000 notgovharness-agent-runtime:latest
"""

from __future__ import annotations

import json
import socket

import httpx
import pytest

AGENT_RUNTIME_URL = "http://localhost:18101"


def _reachable(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture
def agent_runtime_url() -> str:
    if not _reachable("localhost", 18101):
        pytest.skip(
            f"agent-runtime not reachable on {AGENT_RUNTIME_URL} — start one by hand "
            "(see this file's own module docstring for the exact command)"
        )
    return AGENT_RUNTIME_URL


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


async def test_agent_runtime_target_judges_the_deployed_agent_for_real(
    platform_identity_url, platform_eval_registry_url, platform_evals_runner_url, agent_runtime_url
):
    publish_token = await _token(
        platform_identity_url,
        "example-service",
        "example-service-dev-secret",
        "eval_registry:publish",
    )
    metadata = {
        "name": "evals-runner-agent-target-smoke",
        "version": "0.1.0",
        "description": "Live-verifies the agent_runtime target against a real deployed agent.",
        "kind": "cases",
        "applies_to": ["evals-runner-agent-target-smoke"],
        "metrics": [
            {
                "engine": "deepeval",
                "metric_id": "g_eval",
                "params": {
                    "name": "Correctness",
                    "criteria": (
                        "Determine whether the actual output matches the expected output "
                        "factually."
                    ),
                    "evaluation_params": ["input", "actual_output", "expected_output"],
                    "threshold": 0.5,
                },
            },
        ],
    }
    dataset = (
        '{"input": "What is the capital of Japan? Answer in a single full sentence.", '
        '"expected_output": "Tokyo is the capital of Japan."}\n'
    )
    async with httpx.AsyncClient(base_url=platform_eval_registry_url, timeout=15.0) as client:
        published = await client.post(
            "/suites",
            data={"metadata": json.dumps(metadata)},
            files={"dataset": ("dataset.jsonl", dataset, "application/x-ndjson")},
            headers={"Authorization": f"Bearer {publish_token}"},
        )
        assert published.status_code in (201, 409), published.text

    run_token = await _token(
        platform_identity_url, "example-service", "example-service-dev-secret", "evals:run"
    )
    async with httpx.AsyncClient(base_url=platform_evals_runner_url, timeout=600.0) as client:
        run = await client.post(
            "/runs",
            json={
                "suite_name": "evals-runner-agent-target-smoke",
                "target": {"type": "agent_runtime", "url": agent_runtime_url},
            },
            headers={"Authorization": f"Bearer {run_token}"},
        )
        assert run.status_code == 201, run.text
        body = run.json()

    assert body["model"] == f"agent_runtime:{agent_runtime_url}"
    assert body["pass_count"] == 1
    assert body["fail_count"] == 0
    assert "tokyo" in body["case_results"][0]["actual_output"].lower()
