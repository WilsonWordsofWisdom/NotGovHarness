"""approvals-service: approve/edit/reject gates on risky actions, via real Temporal signals
(Wave 3 Approvals/HITL harness, D-015).

Greenfield service (Postgres `approvals`) plus a Temporal client — the actual gate mechanism is
the `ApprovalWorkflow` (temporal_workflow.py), executed by a separate worker process
(worker.py); this API only starts workflows, signals them, and reads the Postgres projection
those workflows themselves keep up to date via an activity. See
docs/superpowers/specs/2026-08-31-approvals-hitl-harness-design.md.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client

from platform_core.app import create_app
from platform_core.auth import CallerIdentity, make_require_identity, require_scope
from platform_core.db import Database, lifespan_hook, session_dependency
from platform_core.errors import PlatformError

from .config import Settings
from .models import Approval
from .temporal_types import ApprovalDecision
from .temporal_workflow import ApprovalWorkflow

_DECISION_STATUSES = {"approve": "approved", "reject": "rejected", "edit": "edited"}


class RequestApprovalIn(BaseModel):
    action_type: str
    action_payload: dict[str, Any]
    risk_level: str
    timeout_hours: float | None = None


class DecideIn(BaseModel):
    decision: str
    edited_payload: dict[str, Any] | None = None


class _TemporalClientHolder:
    """`Client.connect` is async, but `build_app()` is synchronous — this holder lets the
    lifespan hook populate the client at startup while routes close over the same instance."""

    client: Client | None = None


def _approval_out(row: Approval) -> dict[str, Any]:
    return {
        "id": row.id,
        "workflow_id": row.workflow_id,
        "requester": row.requester,
        "action_type": row.action_type,
        "action_payload": row.action_payload,
        "risk_level": row.risk_level,
        "status": row.status,
        "decision_payload": row.decision_payload,
        "decided_by": row.decided_by,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "created_at": row.created_at.isoformat(),
    }


async def _get_approval(session: AsyncSession, approval_id: int) -> Approval:
    row = await session.get(Approval, approval_id)
    if row is None:
        raise PlatformError("not_found", f"no approval with id {approval_id}", status_code=404)
    return row


def build_app() -> FastAPI:
    settings = Settings()
    db = Database(settings.database_url)
    get_session = session_dependency(db)
    require_identity = make_require_identity(settings)
    require_request_scope = require_scope(require_identity, "approvals:request")
    require_decide_scope = require_scope(require_identity, "approvals:decide")
    temporal = _TemporalClientHolder()

    @asynccontextmanager
    async def connect_temporal_hook(_app: FastAPI) -> AsyncIterator[None]:
        temporal.client = await Client.connect(settings.temporal_address)
        yield

    app = create_app(
        settings,
        readiness_checks=[db.check],
        lifespan_hooks=[lifespan_hook(db), connect_temporal_hook],
    )

    @app.post("/approvals", tags=["approvals"], status_code=201)
    async def request_approval(
        body: RequestApprovalIn,
        identity: CallerIdentity = Depends(require_request_scope),
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        assert temporal.client is not None
        workflow_id = f"approval-{uuid.uuid4().hex}"
        row = Approval(
            workflow_id=workflow_id,
            requester=identity.id,
            action_type=body.action_type,
            action_payload=body.action_payload,
            risk_level=body.risk_level,
            status="pending",
        )
        session.add(row)
        await session.commit()

        try:
            await temporal.client.start_workflow(
                ApprovalWorkflow.run,
                body.timeout_hours or settings.default_timeout_hours,
                id=workflow_id,
                task_queue=settings.temporal_task_queue,
            )
        except Exception as exc:
            raise PlatformError(
                "workflow_start_failed",
                f"could not start approval workflow: {exc}",
                status_code=502,
            ) from exc

        await session.refresh(row)
        return _approval_out(row)

    @app.get("/approvals", tags=["approvals"])
    async def list_approvals(
        status: str | None = None,
        _identity: CallerIdentity = Depends(require_identity),
        session: AsyncSession = Depends(get_session),
    ) -> list[dict[str, Any]]:
        stmt = select(Approval).order_by(Approval.created_at.desc())
        if status:
            stmt = stmt.where(Approval.status == status)
        rows = (await session.execute(stmt)).scalars()
        return [_approval_out(row) for row in rows]

    @app.get("/approvals/{approval_id}", tags=["approvals"])
    async def get_approval(
        approval_id: int,
        _identity: CallerIdentity = Depends(require_identity),
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        return _approval_out(await _get_approval(session, approval_id))

    @app.post("/approvals/{approval_id}/decide", tags=["approvals"], response_model=None)
    async def decide_approval(
        approval_id: int,
        body: DecideIn,
        identity: CallerIdentity = Depends(require_decide_scope),
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any] | JSONResponse:
        if body.decision not in _DECISION_STATUSES:
            raise PlatformError(
                "invalid_request",
                f"decision must be one of {sorted(_DECISION_STATUSES)}",
                status_code=422,
            )
        row = await _get_approval(session, approval_id)
        if row.status != "pending":
            raise PlatformError(
                "already_decided",
                f"approval {approval_id} is already {row.status!r}",
                status_code=409,
            )

        assert temporal.client is not None
        handle = temporal.client.get_workflow_handle(row.workflow_id)
        await handle.signal(
            ApprovalWorkflow.decide,
            ApprovalDecision(
                decision=body.decision,
                decided_by=identity.id,
                edited_payload=body.edited_payload,
            ),
        )
        try:
            # The workflow completes (and its persist_outcome activity runs) almost immediately
            # after processing the signal — waiting here lets this response reflect the
            # just-persisted Postgres state without a second polling round-trip.
            await asyncio.wait_for(handle.result(), timeout=15)
        except TimeoutError:
            # Signal was delivered; the workflow just hasn't finished persisting yet. Not an
            # error — the caller polls GET /approvals/{id} to observe the terminal state.
            await session.refresh(row)
            return JSONResponse(status_code=202, content=_approval_out(row))

        await session.refresh(row)
        return _approval_out(row)

    static_dir = Path(__file__).resolve().parents[2] / "static"
    if static_dir.is_dir():
        app.mount("/ui", StaticFiles(directory=static_dir, html=True), name="ui")

    return app


app = build_app()
