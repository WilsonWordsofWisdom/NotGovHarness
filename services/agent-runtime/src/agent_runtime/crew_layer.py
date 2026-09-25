"""Constructs and runs a real single-agent CrewAI `Crew` against `llm-gateway`, using the
role/goal/backstory baked into this container's own environment at start (D-087) — no tool
wiring for v1 (Agent Builder's own build step already proves tool-calling works; this runtime
doesn't need to re-prove it).

CrewAI's telemetry (D-070) and tracing (D-074) gates must be set before `crewai` is ever
imported — same "set before import" discipline as Mem0's `MEM0_TELEMETRY` (D-059) and Agent
Builder's own `crew_layer.py`.
"""

from __future__ import annotations

import os

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")

from crewai import LLM, Agent, Crew, CrewOutput, Task  # noqa: E402
from starlette.concurrency import run_in_threadpool  # noqa: E402

from platform_core.llm_gateway_auth import TokenClient, looks_like_auth_failure  # noqa: E402

from .config import Settings  # noqa: E402


class CrewLayer:
    def __init__(self, settings: Settings, llm_token_client: TokenClient) -> None:
        self._settings = settings
        self._llm_token_client = llm_token_client

    async def run(self, task: str) -> str:
        # D-063/D-071/D-086's reactive-retry shape: retry the whole kickoff once with a
        # force-refreshed token on an auth-shaped failure, rather than trusting the single
        # proactive ensure_token() call to always land inside the safety margin.
        llm_token = await self._llm_token_client.ensure_token()
        try:
            return await self._kickoff(llm_token, task)
        except Exception as exc:  # noqa: BLE001
            if not looks_like_auth_failure(exc):
                raise
            llm_token = await self._llm_token_client.force_refresh()
            return await self._kickoff(llm_token, task)

    async def _kickoff(self, llm_token: str, task: str) -> str:
        llm = LLM(
            model=f"openai/{self._settings.llm_gateway_model}",
            base_url=self._settings.llm_gateway_url,
            api_key=llm_token,
        )
        return await run_in_threadpool(self._kickoff_sync, llm, task)

    def _kickoff_sync(self, llm: LLM, task: str) -> str:
        agent = Agent(
            role=self._settings.agent_role,
            goal=self._settings.agent_goal,
            backstory=self._settings.agent_backstory,
            llm=llm,
        )
        crew_task = Task(
            description=task, expected_output="A concise natural-language answer.", agent=agent
        )
        crew = Crew(agents=[agent], tasks=[crew_task], tracing=False)
        output = crew.kickoff()
        assert isinstance(output, CrewOutput)
        return output.raw
