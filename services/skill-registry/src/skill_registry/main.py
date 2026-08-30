"""skill-registry: catalog of `SKILL.md`-format skill bundles (Wave 2 Skill Registry harness).

Greenfield, façade-free (like the other registries) — see
docs/superpowers/specs/2026-08-30-skill-registry-harness-design.md for the full design. No Kafka
consumer: this is a catalog CRUD service, not an event listener.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Form, Response, UploadFile
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.app import create_app
from platform_core.auth import CallerIdentity, make_require_identity, require_scope
from platform_core.db import Database, lifespan_hook, session_dependency
from platform_core.errors import PlatformError
from platform_core.objectstore import ObjectNotFoundError, ObjectStore

from .bundle import BundleError, extract_bundle
from .config import Settings
from .models import Skill
from .scan import scan_bundle
from .skillmd import SkillValidationError, parse_skill_md


def build_app() -> FastAPI:
    settings = Settings()
    db = Database(settings.database_url)
    get_session = session_dependency(db)
    require_identity = make_require_identity(settings)
    require_publish_scope = require_scope(require_identity, "skill_registry:publish")
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

    @app.post("/skills", tags=["skill-registry"], status_code=201)
    async def publish_skill(
        file: UploadFile,
        version: str = Form(...),
        identity: CallerIdentity = Depends(require_publish_scope),
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        if not version:
            raise PlatformError("invalid_request", "'version' is required", status_code=422)

        data = await file.read()
        try:
            extracted = extract_bundle(data)
        except BundleError as exc:
            raise PlatformError("invalid_bundle", exc.reason, status_code=422) from exc

        try:
            parsed = parse_skill_md(
                extracted.skill_md_content, directory_name=extracted.directory_name
            )
        except SkillValidationError as exc:
            raise PlatformError("invalid_skill", exc.reason, status_code=422) from exc

        scan_result = scan_bundle(extracted.files)
        block_findings = [f for f in scan_result.findings if f.severity == "block"]
        if block_findings:
            raise PlatformError(
                "unsafe_bundle",
                "bundle failed the malicious-content scan: "
                + "; ".join(f"{f.label}: {f.detail} ({f.rule})" for f in block_findings),
                status_code=422,
            )
        warn_findings = [
            {"file": f.label, "rule": f.rule, "detail": f.detail}
            for f in scan_result.findings
            if f.severity == "warn"
        ]

        object_key = f"{parsed.name}/{version}.zip"
        # Postgres uniqueness check *before* the MinIO write: a rejected duplicate publish must
        # never touch a bundle a prior, successful publish already stored at this same key.
        row = Skill(
            name=parsed.name,
            version=version,
            description=parsed.description,
            license=parsed.license,
            compatibility=parsed.compatibility,
            metadata_=parsed.metadata,
            allowed_tools=parsed.allowed_tools,
            skill_md=parsed.raw,
            bundle_object_key=object_key,
            bundle_size_bytes=len(data),
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
                f"{parsed.name!r} version {version!r} is already published",
                status_code=409,
            ) from exc
        await store.put_object(object_key, data, content_type="application/zip")
        await session.refresh(row)
        return {
            "id": row.id,
            "name": row.name,
            "version": row.version,
            "published_by": row.published_by,
            "created_at": row.created_at.isoformat(),
            "scan_warnings": warn_findings,
        }

    @app.get("/skills", tags=["skill-registry"])
    async def list_skills(
        q: str | None = None, session: AsyncSession = Depends(get_session)
    ) -> list[dict[str, Any]]:
        # Discovery stage (progressive disclosure): name + description only, matching what an
        # agent loads at startup for every available skill — full instructions come later, at
        # GET /skills/{name}.
        # Ordered by created_at within each name, so the last row seen per name is the same
        # "latest" GET /skills/{name} would return — not alphabetically-last version string.
        rows = (
            await session.execute(select(Skill).order_by(Skill.name, Skill.created_at))
        ).scalars()
        seen: dict[str, str] = {}
        for row in rows:
            if q and q.lower() not in row.name.lower() and q.lower() not in row.description.lower():
                continue
            seen[row.name] = row.description
        return [{"name": name, "description": description} for name, description in seen.items()]

    async def _latest(session: AsyncSession, name: str) -> Skill:
        row = (
            await session.execute(
                select(Skill).where(Skill.name == name).order_by(Skill.created_at.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            raise PlatformError("not_found", f"no skill published for {name!r}", status_code=404)
        return row

    async def _version(session: AsyncSession, name: str, version: str) -> Skill:
        row = (
            await session.execute(select(Skill).where(Skill.name == name, Skill.version == version))
        ).scalar_one_or_none()
        if row is None:
            raise PlatformError(
                "not_found",
                f"no skill published for {name!r} version {version!r}",
                status_code=404,
            )
        return row

    def _skill_out(row: Skill) -> dict[str, Any]:
        # Activation stage: the full frontmatter + SKILL.md body an agent loads once it decides
        # this skill is relevant.
        return {
            "id": row.id,
            "name": row.name,
            "version": row.version,
            "description": row.description,
            "license": row.license,
            "compatibility": row.compatibility,
            "metadata": row.metadata_,
            "allowed_tools": row.allowed_tools,
            "skill_md": row.skill_md,
            "bundle_size_bytes": row.bundle_size_bytes,
            "scan_findings": row.scan_findings,
            "published_by": row.published_by,
            "created_at": row.created_at.isoformat(),
        }

    @app.get("/skills/{name}", tags=["skill-registry"])
    async def get_latest_skill(
        name: str, session: AsyncSession = Depends(get_session)
    ) -> dict[str, Any]:
        return _skill_out(await _latest(session, name))

    @app.get("/skills/{name}/{version}", tags=["skill-registry"])
    async def get_skill_version(
        name: str, version: str, session: AsyncSession = Depends(get_session)
    ) -> dict[str, Any]:
        return _skill_out(await _version(session, name, version))

    @app.get("/skills/{name}/{version}/bundle", tags=["skill-registry"])
    async def download_bundle(
        name: str, version: str, session: AsyncSession = Depends(get_session)
    ) -> Response:
        # Execution stage: the full archive (scripts/references/assets), fetched only once the
        # agent actually needs a bundled file — not loaded at Discovery or Activation.
        row = await _version(session, name, version)
        try:
            data = await store.get_object(row.bundle_object_key)
        except ObjectNotFoundError as exc:
            raise PlatformError(
                "bundle_missing", f"stored bundle object is missing: {exc}", status_code=500
            ) from exc
        return Response(
            content=data,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{name}-{version}.zip"'},
        )

    static_dir = Path(__file__).resolve().parents[2] / "static"
    if static_dir.is_dir():
        # Mounted last, after every API route above: Starlette matches routes in registration
        # order, so /skills and friends are still handled by the API — this only catches paths
        # nothing else claimed (/ui, /ui/index.html).
        app.mount("/ui", StaticFiles(directory=static_dir, html=True), name="ui")

    return app


app = build_app()
