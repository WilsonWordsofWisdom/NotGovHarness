"""eval-registry: catalog of versioned eval suites (Wave 2 Eval Registry harness).

Greenfield, façade-free (like the other registries) — see
docs/superpowers/specs/2026-08-30-eval-registry-harness-design.md for the full design. No Kafka
consumer: this is a catalog CRUD service, not an event listener. Nothing here executes an eval —
that's the not-yet-built Evals runner (Wave 4); this harness only stores and serves suites.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Form, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.app import create_app
from platform_core.auth import CallerIdentity, make_require_identity, require_scope
from platform_core.db import Database, lifespan_hook, session_dependency
from platform_core.errors import PlatformError
from platform_core.objectstore import ObjectNotFoundError, ObjectStore

from .config import Settings
from .models import Suite
from .scan import scan_suite
from .suite import SuiteValidationError, parse_goldens, parse_suite_metadata


def build_app() -> FastAPI:
    settings = Settings()
    db = Database(settings.database_url)
    get_session = session_dependency(db)
    require_identity = make_require_identity(settings)
    require_publish_scope = require_scope(require_identity, "eval_registry:publish")
    store = ObjectStore(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key,
        settings.minio_bucket,
        secure=settings.minio_secure,
    )

    @asynccontextmanager
    async def ensure_bucket_hook(_app: FastAPI) -> AsyncIterator[None]:
        await store.ensure_bucket()
        yield

    app = create_app(
        settings,
        readiness_checks=[db.check],
        lifespan_hooks=[lifespan_hook(db), ensure_bucket_hook],
    )

    @app.post("/suites", tags=["eval-registry"], status_code=201)
    async def publish_suite(
        metadata: str = Form(...),
        dataset: UploadFile | None = None,
        identity: CallerIdentity = Depends(require_publish_scope),
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        try:
            raw_metadata = json.loads(metadata)
        except json.JSONDecodeError as exc:
            raise PlatformError(
                "invalid_request", f"'metadata' is not valid JSON: {exc}", status_code=422
            ) from exc
        if not isinstance(raw_metadata, dict):
            raise PlatformError(
                "invalid_request", "'metadata' must be a JSON object", status_code=422
            )

        try:
            parsed = parse_suite_metadata(raw_metadata)
        except SuiteValidationError as exc:
            raise PlatformError("invalid_suite", exc.reason, status_code=422) from exc

        scan_result = scan_suite(parsed)
        block_findings = [f for f in scan_result.findings if f.severity == "block"]
        if block_findings:
            raise PlatformError(
                "unsafe_suite",
                "suite failed the judge-rubric scan: "
                + "; ".join(f"{f.label}: {f.detail} ({f.rule})" for f in block_findings),
                status_code=422,
            )
        warn_findings = [
            {"label": f.label, "rule": f.rule, "detail": f.detail}
            for f in scan_result.findings
            if f.severity == "warn"
        ]

        dataset_bytes: bytes | None = None
        case_count: int | None = None
        object_key: str | None = None

        if parsed.kind == "cases":
            if dataset is None:
                raise PlatformError(
                    "invalid_request", "'dataset' is required for a 'cases' suite", status_code=422
                )
            dataset_bytes = await dataset.read()
            try:
                goldens = parse_goldens(dataset_bytes.decode("utf-8"))
            except UnicodeDecodeError as exc:
                raise PlatformError(
                    "invalid_request", f"dataset is not valid UTF-8: {exc}", status_code=422
                ) from exc
            except SuiteValidationError as exc:
                raise PlatformError("invalid_dataset", exc.reason, status_code=422) from exc
            case_count = len(goldens)
            object_key = f"{parsed.name}/{parsed.version}.jsonl"

        # Postgres uniqueness check *before* the MinIO write — same ordering fix as Skill
        # Registry (D-036): a rejected duplicate publish must never overwrite an existing
        # dataset stored at the same deterministic key.
        row = Suite(
            name=parsed.name,
            version=parsed.version,
            description=parsed.description,
            kind=parsed.kind,
            applies_to=parsed.applies_to,
            metrics=parsed.metrics,
            redteam_config=parsed.redteam_config,
            dataset_object_key=object_key,
            case_count=case_count,
            scan_findings=warn_findings,
            published_by=identity.id,
        )
        session.add(row)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise PlatformError(
                "already_published",
                f"{parsed.name!r} version {parsed.version!r} is already published",
                status_code=409,
            ) from exc

        if object_key is not None and dataset_bytes is not None:
            await store.put_object(object_key, dataset_bytes, content_type="application/x-ndjson")

        await session.refresh(row)
        return {
            "id": row.id,
            "name": row.name,
            "version": row.version,
            "kind": row.kind,
            "case_count": row.case_count,
            "published_by": row.published_by,
            "created_at": row.created_at.isoformat(),
            "scan_warnings": warn_findings,
        }

    @app.get("/suites", tags=["eval-registry"])
    async def list_suites(
        applies_to: str | None = None, session: AsyncSession = Depends(get_session)
    ) -> list[dict[str, Any]]:
        # Ordered by created_at within each name, so the last row seen per name is the same
        # "latest" GET /suites/{name} would return.
        rows = (
            await session.execute(select(Suite).order_by(Suite.name, Suite.created_at))
        ).scalars()
        seen: dict[str, dict[str, Any]] = {}
        for row in rows:
            if applies_to is not None and applies_to not in row.applies_to:
                continue
            seen[row.name] = {
                "name": row.name,
                "description": row.description,
                "kind": row.kind,
                "applies_to": row.applies_to,
            }
        return list(seen.values())

    async def _latest(session: AsyncSession, name: str) -> Suite:
        row = (
            await session.execute(
                select(Suite).where(Suite.name == name).order_by(Suite.created_at.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            raise PlatformError("not_found", f"no suite published for {name!r}", status_code=404)
        return row

    async def _version(session: AsyncSession, name: str, version: str) -> Suite:
        row = (
            await session.execute(select(Suite).where(Suite.name == name, Suite.version == version))
        ).scalar_one_or_none()
        if row is None:
            raise PlatformError(
                "not_found",
                f"no suite published for {name!r} version {version!r}",
                status_code=404,
            )
        return row

    def _suite_out(row: Suite) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "version": row.version,
            "description": row.description,
            "kind": row.kind,
            "applies_to": row.applies_to,
            "metrics": row.metrics,
            "redteam_config": row.redteam_config,
            "case_count": row.case_count,
            "scan_findings": row.scan_findings,
            "published_by": row.published_by,
            "created_at": row.created_at.isoformat(),
        }

    @app.get("/suites/{name}", tags=["eval-registry"])
    async def get_latest_suite(
        name: str, session: AsyncSession = Depends(get_session)
    ) -> dict[str, Any]:
        return _suite_out(await _latest(session, name))

    @app.get("/suites/{name}/{version}", tags=["eval-registry"])
    async def get_suite_version(
        name: str, version: str, session: AsyncSession = Depends(get_session)
    ) -> dict[str, Any]:
        return _suite_out(await _version(session, name, version))

    @app.get("/suites/{name}/{version}/dataset", tags=["eval-registry"])
    async def download_dataset(
        name: str, version: str, session: AsyncSession = Depends(get_session)
    ) -> Response:
        row = await _version(session, name, version)
        if row.dataset_object_key is None:
            raise PlatformError(
                "no_dataset",
                f"suite {name!r} version {version!r} has no dataset (kind={row.kind!r})",
                status_code=404,
            )
        try:
            data = await store.get_object(row.dataset_object_key)
        except ObjectNotFoundError as exc:
            raise PlatformError(
                "dataset_missing", f"stored dataset object is missing: {exc}", status_code=500
            ) from exc
        return Response(
            content=data,
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="{name}-{version}.jsonl"'},
        )

    return app


app = build_app()
