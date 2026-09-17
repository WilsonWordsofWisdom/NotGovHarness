"""evals-runner-service: runs published Eval Registry suites for real (Wave 4 Evals Runner
harness). See docs/superpowers/specs/2026-09-18-evals-runner-harness-design.md and decisions
D-077..D-080.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.app import create_app
from platform_core.auth import CallerIdentity, make_require_identity, require_scope
from platform_core.db import Database, lifespan_hook, session_dependency
from platform_core.errors import PlatformError
from platform_core.llm_gateway_auth import TokenClient

from .config import Settings
from .eval_registry_client import EvalRegistryClient, EvalRegistryError, Golden
from .llm_gateway_target import LlmGatewayTarget
from .metrics import deepeval_engine, ragas_engine
from .models import Run


class RunIn(BaseModel):
    suite_name: str
    suite_version: str | None = None
    model: str | None = None


def _run_out(row: Run) -> dict[str, Any]:
    return {
        "run_id": row.id,
        "suite_name": row.suite_name,
        "suite_version": row.suite_version,
        "model": row.model,
        "case_results": row.case_results,
        "pass_count": row.pass_count,
        "fail_count": row.fail_count,
        "created_at": row.created_at.isoformat(),
    }


async def _run_metric(
    metric: dict[str, Any],
    golden: Golden,
    actual_output: str,
    settings: Settings,
    token_client: TokenClient,
) -> dict[str, Any]:
    engine = metric["engine"]
    metric_id = metric["metric_id"]
    params = metric.get("params", {})

    if engine == "deepeval":
        judge = deepeval_engine.DeepEvalTargetModel(
            settings.llm_gateway_url, settings.llm_gateway_model, token_client
        )
        return await deepeval_engine.run_metric(metric_id, params, golden, actual_output, judge)
    if engine == "ragas":
        return await ragas_engine.run_metric(
            metric_id,
            params,
            golden,
            actual_output,
            settings.llm_gateway_url,
            settings.llm_gateway_model,
            token_client,
        )
    raise PlatformError(
        "unsupported_engine",
        f"engine {engine!r} is not runnable (v1 supports 'deepeval' and 'ragas' only)",
        status_code=422,
    )


def build_app() -> FastAPI:
    settings = Settings()
    db = Database(settings.database_url)
    get_session = session_dependency(db)
    require_identity = make_require_identity(settings)
    require_run_scope = require_scope(require_identity, "evals:run")

    token_client = TokenClient(
        identity_url=settings.identity_url,
        client_id=settings.evals_client_id,
        client_secret=settings.evals_client_secret,
        scope="llm_gateway:call",
    )
    eval_registry = EvalRegistryClient(settings.eval_registry_url)

    app = create_app(settings, readiness_checks=[db.check], lifespan_hooks=[lifespan_hook(db)])

    @app.post("/runs", tags=["evals"], status_code=201)
    async def create_run(
        body: RunIn,
        identity: CallerIdentity = Depends(require_run_scope),
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        try:
            suite = await eval_registry.fetch(body.suite_name, body.suite_version)
        except EvalRegistryError as exc:
            raise PlatformError("invalid_suite", exc.reason, status_code=422) from exc

        model = body.model or settings.llm_gateway_model
        target = LlmGatewayTarget(settings.llm_gateway_url, model, token_client)

        case_results: list[dict[str, Any]] = []
        pass_count = 0
        fail_count = 0

        for golden in suite.goldens:
            try:
                actual_output = await target.complete(golden.input)
                metric_results = [
                    await _run_metric(metric, golden, actual_output, settings, token_client)
                    for metric in (suite.metrics or [])
                ]
            except Exception as exc:  # noqa: BLE001
                raise PlatformError(
                    "run_failed", f"failed to run case {golden.input!r}: {exc}", status_code=502
                ) from exc

            case_passed = all(m["passed"] for m in metric_results) if metric_results else False
            pass_count += 1 if case_passed else 0
            fail_count += 0 if case_passed else 1
            case_results.append(
                {
                    "input": golden.input,
                    "expected_output": golden.expected_output,
                    "actual_output": actual_output,
                    "metrics": metric_results,
                }
            )

        row = Run(
            id=str(uuid.uuid4()),
            requester=identity.id,
            suite_name=suite.name,
            suite_version=suite.version,
            model=model,
            case_results=case_results,
            pass_count=pass_count,
            fail_count=fail_count,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _run_out(row)

    @app.get("/runs", tags=["evals"])
    async def list_runs(
        _identity: CallerIdentity = Depends(require_identity),
        session: AsyncSession = Depends(get_session),
    ) -> list[dict[str, Any]]:
        rows = (await session.execute(select(Run).order_by(Run.created_at.desc()))).scalars()
        return [_run_out(row) for row in rows]

    @app.get("/runs/{run_id}", tags=["evals"])
    async def get_run(
        run_id: str,
        _identity: CallerIdentity = Depends(require_identity),
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        row = await session.get(Run, run_id)
        if row is None:
            raise PlatformError("run_not_found", f"no run with id {run_id!r}", status_code=404)
        return _run_out(row)

    return app


app = build_app()
