"""guardrails-service: layered input/output safety checks (Wave 3 Guardrails harness).

Three independently-real libraries, always run together (defense-in-depth visibility, not
first-block-wins) — see docs/superpowers/specs/2026-08-31-guardrails-harness-design.md and
decisions D-051..D-053 for what it took to get each one actually working.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.app import create_app
from platform_core.auth import CallerIdentity, make_require_identity, require_scope
from platform_core.db import Database, lifespan_hook, session_dependency
from platform_core.errors import PlatformError

from . import guardrails_ai_layer, llm_guard_layer, nemo_layer
from .config import Settings
from .models import Check

_VALID_STAGES = {"input", "output"}


class CheckIn(BaseModel):
    stage: str
    text: str


async def _run_all_layers(text: str) -> list[dict[str, Any]]:
    findings = []
    findings += llm_guard_layer.check(text)
    findings += await nemo_layer.check(text)
    findings += guardrails_ai_layer.check(text)
    return [asdict(f) for f in findings]


def _check_out(row: Check) -> dict[str, Any]:
    return {
        "id": row.id,
        "requester": row.requester,
        "stage": row.stage,
        "text": row.text,
        "decision": row.decision,
        "findings": row.findings,
        "created_at": row.created_at.isoformat(),
    }


def build_app() -> FastAPI:
    settings = Settings()
    db = Database(settings.database_url)
    get_session = session_dependency(db)
    require_identity = make_require_identity(settings)
    require_check_scope = require_scope(require_identity, "guardrails:check")

    app = create_app(settings, readiness_checks=[db.check], lifespan_hooks=[lifespan_hook(db)])

    @app.post("/check", tags=["guardrails"], status_code=201)
    async def submit_check(
        body: CheckIn,
        identity: CallerIdentity = Depends(require_check_scope),
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        if body.stage not in _VALID_STAGES:
            raise PlatformError(
                "invalid_stage", f"stage must be one of {sorted(_VALID_STAGES)}", status_code=422
            )

        findings = await _run_all_layers(body.text)
        decision = "block" if any(f["severity"] == "block" for f in findings) else "allow"

        row = Check(
            requester=identity.id,
            stage=body.stage,
            text=body.text,
            decision=decision,
            findings=findings,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _check_out(row)

    @app.get("/checks", tags=["guardrails"])
    async def list_checks(
        decision: str | None = None,
        stage: str | None = None,
        _identity: CallerIdentity = Depends(require_identity),
        session: AsyncSession = Depends(get_session),
    ) -> list[dict[str, Any]]:
        stmt = select(Check).order_by(Check.created_at.desc())
        if decision:
            stmt = stmt.where(Check.decision == decision)
        if stage:
            stmt = stmt.where(Check.stage == stage)
        rows = (await session.execute(stmt)).scalars()
        return [_check_out(row) for row in rows]

    @app.get("/checks/{check_id}", tags=["guardrails"])
    async def get_check(
        check_id: int,
        _identity: CallerIdentity = Depends(require_identity),
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        row = await session.get(Check, check_id)
        if row is None:
            raise PlatformError("not_found", f"no check with id {check_id}", status_code=404)
        return _check_out(row)

    return app


app = build_app()
