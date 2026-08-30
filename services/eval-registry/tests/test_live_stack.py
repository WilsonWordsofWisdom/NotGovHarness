"""Skip-if-down: the actual end-to-end flow this harness exists to prove.

A real bearer token from a running identity-service publishes a real suite (metadata + JSONL
dataset) to a running eval-registry, fetched back byte-for-byte — both the parsed metadata and
the raw dataset download. Steps 1-4's tests already proved schema validation, the judge-rubric
scan, and reads against test databases/buckets in isolation; this is the one that proves the
whole live system.
"""

from __future__ import annotations

import json
import uuid

import httpx


async def _publish_token(identity_url: str) -> str:
    async with httpx.AsyncClient(base_url=identity_url) as client:
        resp = await client.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "example-service",
                "client_secret": "example-service-dev-secret",
                "scope": "eval_registry:publish",
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def test_a_real_suite_is_published_fetched_and_dataset_downloaded_intact(
    platform_identity_url, platform_eval_registry_url
):
    token = await _publish_token(platform_identity_url)
    headers = {"Authorization": f"Bearer {token}"}
    name = f"live-test-suite-{uuid.uuid4().hex[:8]}"
    metadata = {
        "name": name,
        "version": "1.0.0",
        "description": "A live-stack test suite.",
        "kind": "cases",
        "applies_to": ["tool_use"],
        "metrics": [{"engine": "deepeval", "metric_id": "ToolCorrectnessMetric", "params": {}}],
    }
    dataset = b'{"input": "What is 2+2?", "expected_output": "4"}\n'

    async with httpx.AsyncClient(base_url=platform_eval_registry_url) as client:
        published = await client.post(
            "/suites",
            data={"metadata": json.dumps(metadata)},
            files={"dataset": ("dataset.jsonl", dataset, "application/x-ndjson")},
            headers=headers,
        )
        assert published.status_code == 201, published.text

        fetched = await client.get(f"/suites/{name}/1.0.0")
        assert fetched.status_code == 200
        assert fetched.json()["case_count"] == 1

        downloaded = await client.get(f"/suites/{name}/1.0.0/dataset")
        assert downloaded.status_code == 200
        assert downloaded.content == dataset
