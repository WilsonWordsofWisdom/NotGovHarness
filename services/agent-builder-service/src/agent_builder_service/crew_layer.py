"""Constructs and runs a real single-agent CrewAI `Crew` against `llm-gateway`, with one tool
wired through `agent-gateway` (D-072). See
docs/superpowers/specs/2026-09-08-agent-builder-harness-design.md and decisions D-070..D-074.

CrewAI's telemetry (D-070) and tracing (D-074) gates must be set before `crewai` is ever
imported — same "set before import" discipline as Mem0's `MEM0_TELEMETRY` (D-059).
"""

from __future__ import annotations

import os

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")

from dataclasses import dataclass  # noqa: E402
from typing import Any  # noqa: E402

import httpx  # noqa: E402
from crewai import LLM, Agent, Crew, CrewOutput, Task  # noqa: E402
from crewai.events import crewai_event_bus  # noqa: E402
from crewai.events.base_events import BaseEvent  # noqa: E402
from crewai.events.types.tool_usage_events import ToolUsageFinishedEvent  # noqa: E402
from starlette.concurrency import run_in_threadpool  # noqa: E402

from platform_core.llm_gateway_auth import (  # noqa: E402
    TokenClient,
    call_with_auth_retry,
    looks_like_auth_failure,
)

from .agent_gateway_tool import AgentGatewayTool  # noqa: E402
from .config import Settings  # noqa: E402
from .sync_token import SyncTokenClient  # noqa: E402

FEDERATED_TOOL_ORIGINAL_NAME = "list_skills"
MCP_SKILLS_DEMO_URL = "http://mcp-skills-demo:8000/mcp"
MCP_SKILLS_DEMO_GATEWAY_NAME = "agent-builder-mcp-skills-demo"


@dataclass
class KickoffResult:
    raw_output: str
    tool_was_called: bool


class CrewLayer:
    def __init__(self, settings: Settings, llm_token_client: TokenClient) -> None:
        self._settings = settings
        self._llm_token_client = llm_token_client
        self._federated_tool_name: str | None = None

    async def _ensure_tool_registered(self) -> str:
        """Resolve the federated name of mcp-skills-demo's `list_skills` tool behind
        agent-gateway, registering the MCP server if it isn't already there. Registration is
        idempotent-by-URL (found live building Agent Gateway) — already-registered is a pass."""
        if self._federated_tool_name is not None:
            return self._federated_tool_name

        gateway_url = self._settings.agent_gateway_url
        headers: dict[str, str] = {}

        def apply_token(token: str) -> None:
            headers["Authorization"] = f"Bearer {token}"

        async def call() -> str:
            async with httpx.AsyncClient(base_url=gateway_url, timeout=15.0) as client:
                registered = await client.post(
                    "/mcp-servers",
                    json={
                        "name": MCP_SKILLS_DEMO_GATEWAY_NAME,
                        "url": MCP_SKILLS_DEMO_URL,
                        "transport": "STREAMABLEHTTP",
                    },
                    headers=headers,
                )
                already_registered = (
                    registered.status_code == 502 and "already exists" in registered.text
                )
                if not (registered.status_code == 201 or already_registered):
                    registered.raise_for_status()

                tools = await client.get("/tools", headers=headers)
                tools.raise_for_status()
            matches = [
                t for t in tools.json() if t.get("originalName") == FEDERATED_TOOL_ORIGINAL_NAME
            ]
            if not matches:
                raise RuntimeError(
                    f"{FEDERATED_TOOL_ORIGINAL_NAME!r} not found behind agent-gateway after "
                    "registering mcp-skills-demo"
                )
            name: str = matches[0]["name"]
            return name

        # D-063's reactive-retry shape: this method only ever runs once (cached after), but the
        # same "a cached token can look fresh and still be genuinely expired" risk applies to any
        # call using this service's own long-lived token client.
        federated_tool_name = await call_with_auth_retry(self._llm_token_client, apply_token, call)
        self._federated_tool_name = federated_tool_name
        return federated_tool_name

    async def run(
        self, *, task: str, role: str, goal: str, backstory: str
    ) -> KickoffResult:
        federated_tool_name = await self._ensure_tool_registered()
        tool_token_client = SyncTokenClient(
            identity_url=self._settings.identity_url,
            client_id=self._settings.builder_client_id,
            client_secret=self._settings.builder_client_secret,
            scope="agent_gateway:call",
        )
        gateway_tool = AgentGatewayTool(
            name="list_skills",
            description="Lists the skills currently published in Skill Registry.",
            agent_gateway_url=self._settings.agent_gateway_url,
            federated_tool_name=federated_tool_name,
            token_client=tool_token_client,
        )

        # D-071 verified crewai.LLM.call() passes api_key fresh to litellm.completion() every
        # call, unlike D-069's OpenAILike — but the token is still baked into this *wrapper
        # object* at construction, one per attempt here, not re-read afterward. So the same
        # D-063 reactive backstop still applies at this level: if the single token fetched below
        # is stale (Docker VM clock suspension), retry once with a force-refreshed one.
        llm_token = await self._llm_token_client.ensure_token()
        try:
            return await self._kickoff(llm_token, gateway_tool, task, role, goal, backstory)
        except Exception as exc:  # noqa: BLE001
            if not looks_like_auth_failure(exc):
                raise
            llm_token = await self._llm_token_client.force_refresh()
            return await self._kickoff(llm_token, gateway_tool, task, role, goal, backstory)

    async def _kickoff(
        self,
        llm_token: str,
        gateway_tool: AgentGatewayTool,
        task: str,
        role: str,
        goal: str,
        backstory: str,
    ) -> KickoffResult:
        llm = LLM(
            model=f"openai/{self._settings.llm_gateway_model}",
            base_url=self._settings.llm_gateway_url,
            api_key=llm_token,
        )
        return await run_in_threadpool(
            self._kickoff_sync, llm, gateway_tool, task, role, goal, backstory
        )

    def _kickoff_sync(
        self, llm: LLM, gateway_tool: AgentGatewayTool, task: str, role: str, goal: str,
        backstory: str,
    ) -> KickoffResult:
        agent = Agent(role=role, goal=goal, backstory=backstory, llm=llm, tools=[gateway_tool])
        crew_task = Task(
            description=task, expected_output="A concise natural-language answer.", agent=agent
        )
        crew = Crew(agents=[agent], tasks=[crew_task], tracing=False)

        tool_calls: list[Any] = []

        def _on_tool_finished(_source: Any, event: BaseEvent) -> None:
            if isinstance(event, ToolUsageFinishedEvent) and event.task_id == str(crew_task.id):
                tool_calls.append(event)

        crewai_event_bus.register_handler(ToolUsageFinishedEvent, _on_tool_finished)
        try:
            output = crew.kickoff()
        finally:
            crewai_event_bus.off(ToolUsageFinishedEvent, _on_tool_finished)

        # tracing=False and no stream=True anywhere in this harness — always the plain
        # CrewOutput branch of kickoff()'s CrewOutput | CrewStreamingOutput return type.
        assert isinstance(output, CrewOutput)
        return KickoffResult(raw_output=output.raw, tool_was_called=len(tool_calls) > 0)
