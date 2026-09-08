"""Skip-if-down: the actual end-to-end flow this harness exists to prove — a real bearer token
submits a build request, a real CrewAI crew.kickoff() runs against real llm-gateway output, the
built agent genuinely calls a tool through real agent-gateway, the resulting Agent Card is
signed and published for real and fetchable back from agent-registry, and Eval Registry is
queried for real for matching suites.
"""

from __future__ import annotations

import uuid

import httpx


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


async def test_build_kickoff_calls_a_tool_and_publishes_a_fetchable_card(
    platform_identity_url, platform_agent_builder_url, platform_agent_registry_url
):
    token = await _token(
        platform_identity_url, "example-service", "example-service-dev-secret", "builder:build"
    )
    headers = {"Authorization": f"Bearer {token}"}
    marker = uuid.uuid4().hex[:8]
    name = f"agent-builder-test-{marker}"

    async with httpx.AsyncClient(base_url=platform_agent_builder_url, timeout=120.0) as client:
        built = await client.post(
            "/builds",
            json={
                "name": name,
                "description": "A test agent built live by agent-builder-service's own tests.",
                "task": (
                    "Call the list_skills tool to see what skills are available, then report "
                    "how many skills you found in one short sentence."
                ),
                "role": "Skill Registry auditor",
                "goal": "Report how many skills are currently published in Skill Registry.",
                "backstory": "You audit this platform's own skill catalog on request.",
            },
            headers=headers,
        )
        assert built.status_code == 201, built.text
        body = built.json()
        assert body["kickoff_result"], "kickoff produced no output"
        assert body["tool_was_called"] is True
        assert isinstance(body["eval_suites"], list)  # queried for real; may legitimately be empty

    # The published card must be fetchable back, byte-for-byte the same bar every registry
    # harness already proved for its own publish path.
    async with httpx.AsyncClient(base_url=platform_agent_registry_url, timeout=15.0) as client:
        fetched = await client.get(f"/agents/{name}/0.1.0")
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["card"]["name"] == name


async def test_builds_requires_scope_but_read_is_open_to_any_caller(
    platform_identity_url, platform_agent_builder_url
):
    read_only_token = await _token(
        platform_identity_url, "example-service", "example-service-dev-secret", "upstream:call"
    )

    async with httpx.AsyncClient(base_url=platform_agent_builder_url, timeout=15.0) as client:
        forbidden = await client.post(
            "/builds",
            json={
                "name": "x",
                "description": "y",
                "task": "z",
                "role": "r",
                "goal": "g",
                "backstory": "b",
            },
            headers={"Authorization": f"Bearer {read_only_token}"},
        )
        assert forbidden.status_code == 403

        allowed = await client.get(
            "/builds", headers={"Authorization": f"Bearer {read_only_token}"}
        )
        assert allowed.status_code == 200
