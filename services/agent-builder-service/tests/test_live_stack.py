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
        assert body["task"] == (
            "Call the list_skills tool to see what skills are available, then report "
            "how many skills you found in one short sentence."
        )

    # The published card must be fetchable back, byte-for-byte the same bar every registry
    # harness already proved for its own publish path.
    async with httpx.AsyncClient(base_url=platform_agent_registry_url, timeout=15.0) as client:
        fetched = await client.get(f"/agents/{name}/0.1.0")
        assert fetched.status_code == 200, fetched.text
        card = fetched.json()["card"]
        assert card["name"] == name

        # D-088: the card must carry enough to redeploy this agent, not just describe it.
        config = card["extensions"][0]["params"]
        assert config["role"] == "Skill Registry auditor"
        assert config["goal"] == "Report how many skills are currently published in Skill Registry."
        assert config["backstory"] == "You audit this platform's own skill catalog on request."
        assert "list_skills" in config["task"]


async def test_build_without_backstory_gets_a_real_generated_default(
    platform_identity_url, platform_agent_builder_url, platform_agent_registry_url
):
    """D-104: backstory is optional now -- the UI doesn't even ask for it -- but CrewAI's Agent
    still requires one under the hood, so a real one must still land in the published card."""
    token = await _token(
        platform_identity_url, "example-service", "example-service-dev-secret", "builder:build"
    )
    headers = {"Authorization": f"Bearer {token}"}
    marker = uuid.uuid4().hex[:8]
    name = f"agent-builder-no-backstory-test-{marker}"

    async with httpx.AsyncClient(base_url=platform_agent_builder_url, timeout=120.0) as client:
        built = await client.post(
            "/builds",
            json={
                "name": name,
                "description": "Built with no backstory field in the request at all.",
                "task": "Say hello in one short sentence.",
                "role": "Greeter",
                "goal": "Greet the caller.",
            },
            headers=headers,
        )
        assert built.status_code == 201, built.text

    async with httpx.AsyncClient(base_url=platform_agent_registry_url, timeout=15.0) as client:
        card = await client.get(f"/agents/{name}/0.1.0")
        assert card.status_code == 200, card.text
        backstory = card.json()["card"]["extensions"][0]["params"]["backstory"]
        assert backstory, "a real backstory must still reach CrewAI's Agent"
        assert "Greeter" in backstory
        assert "Greet the caller." in backstory


async def test_revise_bumps_version_and_preserves_build_history(
    platform_identity_url, platform_agent_builder_url, platform_agent_registry_url
):
    """D-103: revise is a new, versioned build, not an in-place edit -- the old Build row and the
    old published card must both survive untouched alongside the new one."""
    token = await _token(
        platform_identity_url, "example-service", "example-service-dev-secret", "builder:build"
    )
    headers = {"Authorization": f"Bearer {token}"}
    marker = uuid.uuid4().hex[:8]
    name = f"agent-builder-revise-test-{marker}"

    async with httpx.AsyncClient(base_url=platform_agent_builder_url, timeout=120.0) as client:
        created = await client.post(
            "/builds",
            json={
                "name": name,
                "description": "Original build, before any revision.",
                "task": "Say hello in one short sentence.",
                "role": "Greeter",
                "goal": "Greet the caller.",
                "backstory": "You say hello for a living.",
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        created_body = created.json()
        assert created_body["card_version"] == "0.1.0"

        revised = await client.post(
            f"/builds/{name}/revise",
            json={
                "description": "Revised build -- now says goodbye instead.",
                "task": "Say goodbye in one short sentence.",
                "role": "Farewell agent",
                "goal": "Bid the caller farewell.",
                "backstory": "You say goodbye for a living.",
            },
            headers=headers,
        )
        assert revised.status_code == 201, revised.text
        revised_body = revised.json()
        assert revised_body["card_version"] == "0.2.0"
        assert revised_body["build_id"] != created_body["build_id"]

        listed = await client.get("/builds", headers=headers)
        assert listed.status_code == 200
        matching = [b for b in listed.json() if b["name"] == name]
        assert len(matching) == 2, "both the original and the revised build must be listed"

    # Both card versions must remain independently fetchable -- revising must not overwrite the
    # original.
    async with httpx.AsyncClient(base_url=platform_agent_registry_url, timeout=15.0) as client:
        original_card = await client.get(f"/agents/{name}/0.1.0")
        assert original_card.status_code == 200, original_card.text
        original_config = original_card.json()["card"]["extensions"][0]["params"]
        assert original_config["role"] == "Greeter"

        revised_card = await client.get(f"/agents/{name}/0.2.0")
        assert revised_card.status_code == 200, revised_card.text
        revised_config = revised_card.json()["card"]["extensions"][0]["params"]
        assert revised_config["role"] == "Farewell agent"


async def test_revise_requires_an_existing_build(platform_identity_url, platform_agent_builder_url):
    token = await _token(
        platform_identity_url, "example-service", "example-service-dev-secret", "builder:build"
    )
    name = f"agent-builder-no-such-build-{uuid.uuid4().hex[:8]}"

    async with httpx.AsyncClient(base_url=platform_agent_builder_url, timeout=15.0) as client:
        resp = await client.post(
            f"/builds/{name}/revise",
            json={
                "description": "d",
                "task": "t",
                "role": "r",
                "goal": "g",
                "backstory": "b",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "agent_not_found"


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
