"""Skip-if-down: the actual end-to-end flow this harness exists to prove — a real published,
redeployable Agent Card (D-088) is packaged into a real running `agent-runtime` container (D-087),
gated by a real Evals Runner run against the deployed agent itself (D-089), and rolled back on a
failing gate, never left running. Publishes its own agent (via agent-builder-service) and its own
pass/fail eval suites (via eval-registry) so this test is self-contained, not dependent on
anything published by hand in an earlier manual session.

Every slow step (an agent build's own CrewAI kickoff, the deployment's own container-start +
eval-gate cycle) mints its own token immediately before use rather than reusing one minted earlier
in the test — identity-service's access tokens are short-lived (300s) and this chain of real calls
can legitimately run close to or past that window, the same staleness evals-runner-service's own
live-stack test already found and documented for its own long-running run.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx

_REQUESTER = ("example-service", "example-service-dev-secret")


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


async def _publish_agent(identity_url: str, agent_builder_url: str, name: str) -> None:
    token = await _token(identity_url, *_REQUESTER, "builder:build")
    async with httpx.AsyncClient(base_url=agent_builder_url, timeout=120.0) as client:
        built = await client.post(
            "/builds",
            json={
                "name": name,
                "description": "A test agent built live by this service's own tests.",
                "task": "Answer the given question about world capitals in a single full sentence.",
                "role": "Geography assistant",
                "goal": "Answer questions about world capitals accurately.",
                "backstory": "An assistant that knows world capitals.",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert built.status_code == 201, built.text


async def _publish_suite(
    identity_url: str, eval_registry_url: str, *, name: str, expected_output: str
) -> None:
    token = await _token(identity_url, *_REQUESTER, "eval_registry:publish")
    metadata = {
        "name": name,
        "version": "0.1.0",
        "description": "Live-verifies deployment-pipeline-service's eval gate.",
        "kind": "cases",
        "applies_to": [name],
        # Both engines, matching evals-runner-service's own live-stack suite (D-084): a case only
        # passes if every metric passes, and a single LLM-judge occasionally rationalizes an
        # obviously wrong expected_output as correct — two independent engines make the
        # deliberately-wrong suite below reliably fail instead of flaking.
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
            {"engine": "ragas", "metric_id": "factual_correctness", "params": {"threshold": 0.5}},
        ],
    }
    dataset = json.dumps(
        {
            "input": "What is the capital of France? Answer in a single full sentence.",
            "expected_output": expected_output,
        }
    ) + "\n"
    async with httpx.AsyncClient(base_url=eval_registry_url, timeout=15.0) as client:
        published = await client.post(
            "/suites",
            data={"metadata": json.dumps(metadata)},
            files={"dataset": ("dataset.jsonl", dataset, "application/x-ndjson")},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert published.status_code in (201, 409), published.text


async def _create_deployment(
    identity_url: str,
    deployment_pipeline_url: str,
    *,
    agent_name: str,
    suite_name: str,
) -> str:
    token = await _token(identity_url, *_REQUESTER, "deploy:create")
    async with httpx.AsyncClient(base_url=deployment_pipeline_url, timeout=15.0) as client:
        created = await client.post(
            "/deployments",
            json={
                "agent_name": agent_name,
                "agent_version": "0.1.0",
                "eval_suite_name": suite_name,
                "eval_suite_version": "0.1.0",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["status"] == "deploying"
        return body["id"]


async def _poll_until_terminal(
    identity_url: str,
    deployment_pipeline_url: str,
    deployment_id: str,
    *,
    timeout_seconds: float = 240.0,
) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    async with httpx.AsyncClient(base_url=deployment_pipeline_url, timeout=15.0) as client:
        while asyncio.get_event_loop().time() < deadline:
            # A fresh token every iteration, not one held for the whole poll — the deploy →
            # health → eval-gate cycle can run close to identity-service's 300s token TTL.
            token = await _token(identity_url, *_REQUESTER, "deploy:create")
            resp = await client.get(
                f"/deployments/{deployment_id}", headers={"Authorization": f"Bearer {token}"}
            )
            resp.raise_for_status()
            body = resp.json()
            if body["status"] != "deploying":
                return body
            await asyncio.sleep(3)
    raise AssertionError(
        f"deployment {deployment_id} never left 'deploying' within {timeout_seconds}s"
    )


async def test_passing_gate_deploys_and_stop_tears_it_down(
    platform_identity_url,
    platform_agent_builder_url,
    platform_eval_registry_url,
    platform_deployment_pipeline_url,
):
    marker = uuid.uuid4().hex[:8]
    agent_name = f"deploy-pipeline-pass-test-{marker}"
    suite_name = f"deploy-pipeline-pass-suite-{marker}"

    await _publish_agent(platform_identity_url, platform_agent_builder_url, agent_name)
    await _publish_suite(
        platform_identity_url,
        platform_eval_registry_url,
        name=suite_name,
        expected_output="Paris is the capital of France.",  # correct — the gate should pass
    )

    deployment_id = await _create_deployment(
        platform_identity_url,
        platform_deployment_pipeline_url,
        agent_name=agent_name,
        suite_name=suite_name,
    )

    final = await _poll_until_terminal(
        platform_identity_url, platform_deployment_pipeline_url, deployment_id
    )
    assert final["status"] == "running", final
    assert final["container_id"]
    assert final["host_port"]
    assert final["eval_run_id"]
    assert final["error"] is None

    stop_token = await _token(platform_identity_url, *_REQUESTER, "deploy:create")
    async with httpx.AsyncClient(base_url=platform_deployment_pipeline_url, timeout=15.0) as client:
        stopped = await client.post(
            f"/deployments/{deployment_id}/stop",
            headers={"Authorization": f"Bearer {stop_token}"},
        )
        assert stopped.status_code == 200, stopped.text
        body = stopped.json()
        assert body["status"] == "stopped"
        assert body["container_id"] is None
        assert body["host_port"] is None


async def test_failing_gate_rolls_back_and_never_leaves_it_running(
    platform_identity_url,
    platform_agent_builder_url,
    platform_eval_registry_url,
    platform_deployment_pipeline_url,
):
    marker = uuid.uuid4().hex[:8]
    agent_name = f"deploy-pipeline-fail-test-{marker}"
    suite_name = f"deploy-pipeline-fail-suite-{marker}"

    await _publish_agent(platform_identity_url, platform_agent_builder_url, agent_name)
    await _publish_suite(
        platform_identity_url,
        platform_eval_registry_url,
        name=suite_name,
        expected_output="London is the capital of France.",  # wrong on purpose — gate must fail
    )

    deployment_id = await _create_deployment(
        platform_identity_url,
        platform_deployment_pipeline_url,
        agent_name=agent_name,
        suite_name=suite_name,
    )

    final = await _poll_until_terminal(
        platform_identity_url, platform_deployment_pipeline_url, deployment_id
    )
    assert final["status"] == "eval_failed", final
    assert final["container_id"] is None  # rolled back — never left running
    assert final["host_port"] is None
    assert final["eval_run_id"]
    assert final["error"] is None
