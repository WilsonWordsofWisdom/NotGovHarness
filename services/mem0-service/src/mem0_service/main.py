"""mem0-service: durable agent memory via Mem0 (Wave 3 Memory harness).

pgvector + FastEmbed + llm-gateway, per docs/superpowers/specs/2026-09-08-memory-harness-design.md
and decisions D-059..D-062.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI
from pydantic import BaseModel

from platform_core.app import create_app
from platform_core.auth import CallerIdentity, make_require_identity, require_scope
from platform_core.db import Database, lifespan_hook
from platform_core.errors import PlatformError

from .config import Settings
from .llm_gateway_client import TokenClient
from .mem0_layer import Mem0Layer


class AddMemoryIn(BaseModel):
    user_id: str
    messages: list[dict[str, Any]]


def build_app() -> FastAPI:
    settings = Settings()
    db = Database(settings.database_url)
    require_identity = make_require_identity(settings)
    require_write_scope = require_scope(require_identity, "memory:write")

    token_client = TokenClient(
        identity_url=settings.identity_url,
        client_id=settings.mem0_client_id,
        client_secret=settings.mem0_client_secret,
    )
    layer = Mem0Layer(settings, token_client)

    @asynccontextmanager
    async def warm_up_hook(_app: FastAPI) -> AsyncIterator[None]:
        await layer.warm_up()
        yield

    app = create_app(
        settings,
        readiness_checks=[db.check],
        lifespan_hooks=[lifespan_hook(db), warm_up_hook],
    )

    @app.post("/memories", tags=["memory"], status_code=201)
    async def add_memory(
        body: AddMemoryIn, _identity: CallerIdentity = Depends(require_write_scope)
    ) -> dict[str, Any]:
        try:
            return await layer.add(body.messages, user_id=body.user_id)
        except Exception as exc:  # noqa: BLE001 - surface mem0/upstream failures as a 502
            raise PlatformError(
                "memory_add_failed", f"failed to add memory: {exc}", status_code=502
            ) from exc

    @app.get("/memories", tags=["memory"])
    async def list_memories(
        user_id: str,
        query: str | None = None,
        _identity: CallerIdentity = Depends(require_identity),
    ) -> dict[str, Any]:
        try:
            if query:
                return await layer.search(query, user_id=user_id)
            return await layer.get_all(user_id=user_id)
        except Exception as exc:  # noqa: BLE001
            raise PlatformError(
                "memory_read_failed", f"failed to read memory: {exc}", status_code=502
            ) from exc

    @app.delete("/memories/{memory_id}", tags=["memory"], status_code=204)
    async def delete_memory(
        memory_id: str, _identity: CallerIdentity = Depends(require_write_scope)
    ) -> None:
        try:
            await layer.delete(memory_id)
        except Exception as exc:  # noqa: BLE001
            raise PlatformError(
                "memory_delete_failed", f"failed to delete memory: {exc}", status_code=502
            ) from exc

    return app


app = build_app()
