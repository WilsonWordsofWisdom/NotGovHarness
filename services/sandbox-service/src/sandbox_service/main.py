"""sandbox-service: isolated code execution for agent-generated code (Wave 3 Sandbox harness,
D-014, redesigned per D-050).

Greenfield service (Postgres `sandbox`) plus a Docker-backed executor — see
docs/superpowers/specs/2026-08-31-sandbox-harness-design.md for why this runs on plain Docker
containers instead of real E2B/Firecracker, and what that trades away.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.app import create_app
from platform_core.auth import CallerIdentity, make_require_identity, require_scope
from platform_core.db import Database, lifespan_hook, session_dependency
from platform_core.errors import PlatformError

from .config import Settings
from .executor import Executor
from .models import Execution

_SUPPORTED_LANGUAGES = {"python"}


class SubmitExecutionIn(BaseModel):
    language: str = "python"
    code: str
    timeout_seconds: int | None = Field(default=None, ge=1)


def _execution_out(row: Execution) -> dict[str, Any]:
    return {
        "id": row.id,
        "requester": row.requester,
        "language": row.language,
        "code": row.code,
        "status": row.status,
        "stdout": row.stdout,
        "stderr": row.stderr,
        "exit_code": row.exit_code,
        "timeout_seconds": row.timeout_seconds,
        "duration_ms": row.duration_ms,
        "created_at": row.created_at.isoformat(),
    }


def build_app() -> FastAPI:
    settings = Settings()
    db = Database(settings.database_url)
    get_session = session_dependency(db)
    require_identity = make_require_identity(settings)
    require_execute_scope = require_scope(require_identity, "sandbox:execute")
    executor = Executor(
        base_url=settings.docker_base_url,
        image=settings.sandbox_image,
        memory_limit=settings.memory_limit,
        nano_cpus=settings.nano_cpus,
    )

    @asynccontextmanager
    async def pull_image_hook(_app: FastAPI) -> AsyncIterator[None]:
        await executor.ensure_image()
        yield

    @asynccontextmanager
    async def reap_orphans_hook(_app: FastAPI) -> AsyncIterator[None]:
        # A prior instance killed mid-execution (crash, OOM, `docker kill`) leaves its spawned
        # container running — nothing in this design supervises it once the parent process is
        # gone. Found live during this harness's own verification; reconciled here rather than
        # left as a known-but-unaddressed gap.
        await executor.reap_orphaned_containers()
        yield

    app = create_app(
        settings,
        readiness_checks=[db.check],
        lifespan_hooks=[lifespan_hook(db), pull_image_hook, reap_orphans_hook],
    )

    @app.post("/executions", tags=["sandbox"], status_code=201)
    async def submit_execution(
        body: SubmitExecutionIn,
        identity: CallerIdentity = Depends(require_execute_scope),
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        if body.language not in _SUPPORTED_LANGUAGES:
            raise PlatformError(
                "unsupported_language",
                f"language must be one of {sorted(_SUPPORTED_LANGUAGES)}",
                status_code=422,
            )
        timeout_seconds = body.timeout_seconds or settings.default_timeout_seconds
        if timeout_seconds > settings.max_timeout_seconds:
            raise PlatformError(
                "timeout_too_large",
                f"timeout_seconds must be <= {settings.max_timeout_seconds}",
                status_code=422,
            )

        result = await executor.run_python(body.code, timeout_seconds=timeout_seconds)

        row = Execution(
            requester=identity.id,
            language=body.language,
            code=body.code,
            status=result.status,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            timeout_seconds=timeout_seconds,
            duration_ms=result.duration_ms,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _execution_out(row)

    @app.get("/executions", tags=["sandbox"])
    async def list_executions(
        status: str | None = None,
        _identity: CallerIdentity = Depends(require_identity),
        session: AsyncSession = Depends(get_session),
    ) -> list[dict[str, Any]]:
        stmt = select(Execution).order_by(Execution.created_at.desc())
        if status:
            stmt = stmt.where(Execution.status == status)
        rows = (await session.execute(stmt)).scalars()
        return [_execution_out(row) for row in rows]

    @app.get("/executions/{execution_id}", tags=["sandbox"])
    async def get_execution(
        execution_id: int,
        _identity: CallerIdentity = Depends(require_identity),
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        row = await session.get(Execution, execution_id)
        if row is None:
            raise PlatformError(
                "not_found", f"no execution with id {execution_id}", status_code=404
            )
        return _execution_out(row)

    return app


app = build_app()
