"""skill-registry: catalog of `SKILL.md`-format skill bundles (Wave 2 Skill Registry harness).

Greenfield, façade-free (like the other registries) — see
docs/superpowers/specs/2026-08-30-skill-registry-harness-design.md for the full design. No Kafka
consumer: this is a catalog CRUD service, not an event listener.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Form, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.app import create_app
from platform_core.auth import CallerIdentity, make_require_identity, require_scope
from platform_core.db import Database, lifespan_hook, session_dependency
from platform_core.errors import PlatformError
from platform_core.objectstore import ObjectStore

from .bundle import BundleError, extract_skill_md
from .config import Settings
from .models import Skill
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
            extracted = extract_skill_md(data)
        except BundleError as exc:
            raise PlatformError("invalid_bundle", exc.reason, status_code=422) from exc

        try:
            parsed = parse_skill_md(
                extracted.skill_md_content, directory_name=extracted.directory_name
            )
        except SkillValidationError as exc:
            raise PlatformError("invalid_skill", exc.reason, status_code=422) from exc

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
        }

    return app


app = build_app()
