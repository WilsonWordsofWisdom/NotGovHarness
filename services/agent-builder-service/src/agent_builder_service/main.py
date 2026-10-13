"""agent-builder-service: builds, runs, signs, and publishes real CrewAI agents (Wave 4 Agent
Builder harness). See docs/superpowers/specs/2026-09-08-agent-builder-harness-design.md and
decisions D-054, D-070..D-074.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.app import create_app
from platform_core.auth import CallerIdentity, make_require_identity, require_scope
from platform_core.db import Database, lifespan_hook, session_dependency
from platform_core.errors import PlatformError
from platform_core.llm_gateway_auth import TokenClient, call_with_auth_retry

from .config import Settings
from .crew_layer import CrewLayer
from .models import Build


class BuildIn(BaseModel):
    name: str
    description: str
    task: str
    role: str
    goal: str
    backstory: str


def _build_out(row: Build) -> dict[str, Any]:
    return {
        "build_id": row.id,
        "name": row.name,
        "description": row.description,
        "kickoff_result": row.kickoff_raw_output,
        "tool_was_called": row.tool_was_called,
        "agent_card_id": row.agent_card_id,
        "eval_suites": row.eval_suite_names,
        "created_at": row.created_at.isoformat(),
    }


def build_app() -> FastAPI:
    settings = Settings()
    db = Database(settings.database_url)
    get_session = session_dependency(db)
    require_identity = make_require_identity(settings)
    require_build_scope = require_scope(require_identity, "builder:build")

    # This service's own token client (D-062/D-073), scoped for every downstream call it makes
    # as itself: llm-gateway (via crew_layer), agent-gateway (registering/listing tools),
    # identity-service (signing cards), agent-registry (publishing).
    token_client = TokenClient(
        identity_url=settings.identity_url,
        client_id=settings.builder_client_id,
        client_secret=settings.builder_client_secret,
        scope="llm_gateway:call agent_gateway:call agentcard:sign registry:publish",
    )
    crew_layer = CrewLayer(settings, token_client)

    app = create_app(settings, readiness_checks=[db.check], lifespan_hooks=[lifespan_hook(db)])

    async def _authed_call(
        method_call: Any,
    ) -> httpx.Response:
        # D-063's reactive-retry shape, reused here (not just ensure_token() once) — this
        # service's own token client is a long-lived singleton across every build request, so a
        # cached-but-actually-expired token (Docker VM clock suspension, D-063) can surface on
        # any of these three downstream calls, not just llm-gateway's.
        headers: dict[str, str] = {}

        def apply_token(token: str) -> None:
            headers["Authorization"] = f"Bearer {token}"

        async def call() -> httpx.Response:
            resp = await method_call(headers)
            resp.raise_for_status()
            return resp

        return await call_with_auth_retry(token_client, apply_token, call)

    async def _sign_and_publish_card(
        name: str, description: str, role: str, goal: str, backstory: str, task: str
    ) -> dict[str, Any]:
        card = {
            "name": name,
            "url": f"agent-builder-service://agents/{name}",
            "version": "0.1.0",
            "description": description,
            "capabilities": {"streaming": False, "pushNotifications": False},
            # D-088: role/goal/backstory/task are used to construct the CrewAI agent at build
            # time but were never persisted anywhere before this, not even on this service's own
            # `builds` row — nothing durable said how to actually redeploy this agent, despite
            # the plan's own control-plane diagram saying Deployment Pipeline "reads card, REST".
            # A2A's `extensions` field is the real mechanism for platform-specific enrichment
            # like this, not a bespoke top-level field.
            "extensions": [
                {
                    "uri": "urn:notgovharness:crewai-agent-config",
                    "description": "CrewAI construction params for redeploying this agent.",
                    "params": {
                        "role": role,
                        "goal": goal,
                        "backstory": backstory,
                        "task": task,
                    },
                }
            ],
        }

        async def _sign(headers: dict[str, str]) -> httpx.Response:
            async with httpx.AsyncClient(base_url=settings.identity_url, timeout=15.0) as client:
                return await client.post("/cards/sign", json=card, headers=headers)

        signature = (await _authed_call(_sign)).json()

        async def _publish(headers: dict[str, str]) -> httpx.Response:
            async with httpx.AsyncClient(
                base_url=settings.agent_registry_url, timeout=15.0
            ) as client:
                return await client.post(
                    "/agents", json={"card": card, "signature": signature}, headers=headers
                )

        try:
            published = await _authed_call(_publish)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 409:
                raise
            # Same name+version already published (e.g. a retried build) — not a build failure;
            # the card exists and is fetchable, which is all criterion 3 requires.
            return {"name": name, "version": "0.1.0"}
        return published.json()

    async def _select_eval_suites(capability_tag: str) -> list[str]:
        # No separate "declared capabilities" field on BuildIn for v1 (YAGNI) — the built
        # agent's own name doubles as its capability tag, matching whatever `applies_to` an
        # eval suite happens to be published under. Criterion 4 only requires this query to
        # happen for real, not that it matches anything yet.
        async def _list(headers: dict[str, str]) -> httpx.Response:
            async with httpx.AsyncClient(
                base_url=settings.eval_registry_url, timeout=15.0
            ) as client:
                return await client.get(
                    "/suites", params={"applies_to": capability_tag}, headers=headers
                )

        resp = await _authed_call(_list)
        return [suite["name"] for suite in resp.json()]

    @app.post("/builds", tags=["agent-builder"], status_code=201)
    async def create_build(
        body: BuildIn,
        identity: CallerIdentity = Depends(require_build_scope),
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        try:
            kickoff = await crew_layer.run(
                task=body.task,
                role=body.role,
                goal=body.goal,
                backstory=body.backstory,
            )
        except Exception as exc:  # noqa: BLE001
            raise PlatformError(
                "kickoff_failed", f"crew execution failed: {exc}", status_code=502
            ) from exc

        try:
            published = await _sign_and_publish_card(
                body.name, body.description, body.role, body.goal, body.backstory, body.task
            )
            eval_suites = await _select_eval_suites(body.name)
        except httpx.HTTPStatusError as exc:
            raise PlatformError(
                "publish_failed", f"card sign/publish/eval-select failed: {exc}", status_code=502
            ) from exc

        row = Build(
            id=str(uuid.uuid4()),
            requester=identity.id,
            name=body.name,
            description=body.description,
            task=body.task,
            kickoff_raw_output=kickoff.raw_output,
            tool_was_called=kickoff.tool_was_called,
            agent_card_id=str(published.get("id", published.get("name", body.name))),
            eval_suite_names=eval_suites,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _build_out(row)

    @app.get("/builds", tags=["agent-builder"])
    async def list_builds(
        _identity: CallerIdentity = Depends(require_identity),
        session: AsyncSession = Depends(get_session),
    ) -> list[dict[str, Any]]:
        rows = (await session.execute(select(Build).order_by(Build.created_at.desc()))).scalars()
        return [_build_out(row) for row in rows]

    @app.get("/builds/{build_id}", tags=["agent-builder"])
    async def get_build(
        build_id: str,
        _identity: CallerIdentity = Depends(require_identity),
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        row = await session.get(Build, build_id)
        if row is None:
            raise PlatformError(
                "build_not_found", f"no build with id {build_id!r}", status_code=404
            )
        return _build_out(row)

    static_dir = Path(__file__).resolve().parents[2] / "static"
    if static_dir.is_dir():
        # Mounted last, after every API route above: Starlette matches routes in registration
        # order, so /builds and friends are still handled by the API -- this only catches paths
        # nothing else claimed (/ui, /ui/index.html). Same shape as Skill Registry's /ui (D-037).
        app.mount("/ui", StaticFiles(directory=static_dir, html=True), name="ui")

    return app


app = build_app()
