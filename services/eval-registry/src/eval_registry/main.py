"""eval-registry: catalog of versioned eval suites (Wave 2 Eval Registry harness).

Greenfield, façade-free (like the other registries) — see
docs/superpowers/specs/2026-08-30-eval-registry-harness-design.md for the full design. No Kafka
consumer: this is a catalog CRUD service, not an event listener. Nothing here executes an eval —
that's the not-yet-built Evals runner (Wave 4); this harness only stores and serves suites.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from platform_core.app import create_app
from platform_core.db import Database, lifespan_hook
from platform_core.objectstore import ObjectStore

from .config import Settings


def build_app() -> FastAPI:
    settings = Settings()
    db = Database(settings.database_url)
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

    return app


app = build_app()
