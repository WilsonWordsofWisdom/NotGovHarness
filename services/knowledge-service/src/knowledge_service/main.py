"""knowledge-service: retrieval-augmented generation (Wave 3 Knowledge/RAG harness).

Hybrid search (D-065) + FastEmbed rerank (D-066) + generation via llm-gateway, per
docs/superpowers/specs/2026-09-08-knowledge-rag-harness-design.md and decisions D-064..D-068.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.app import create_app
from platform_core.auth import CallerIdentity, make_require_identity, require_scope
from platform_core.db import Database, lifespan_hook, session_dependency
from platform_core.errors import PlatformError
from platform_core.llm_gateway_auth import TokenClient
from platform_core.objectstore import ObjectStore

from .config import Settings
from .models import Document
from .rag_layer import RagLayer


class IngestIn(BaseModel):
    title: str
    text: str
    visibility: Literal["shared", "private"] = "shared"


class QueryIn(BaseModel):
    question: str


def _document_out(row: Document) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "ingested_by": row.ingested_by,
        "visibility": row.visibility,
        "created_at": row.created_at.isoformat(),
    }


def build_app() -> FastAPI:
    settings = Settings()
    db = Database(settings.database_url)
    get_session = session_dependency(db)
    require_identity = make_require_identity(settings)
    require_write_scope = require_scope(require_identity, "knowledge:write")

    token_client = TokenClient(
        identity_url=settings.identity_url,
        client_id=settings.knowledge_client_id,
        client_secret=settings.knowledge_client_secret,
    )
    rag = RagLayer(settings, token_client)
    store = ObjectStore(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key,
        settings.minio_bucket,
        secure=settings.minio_secure,
    )

    @asynccontextmanager
    async def warm_up_hook(_app: FastAPI) -> AsyncIterator[None]:
        await store.ensure_bucket()
        await rag.warm_up()
        yield

    app = create_app(
        settings,
        readiness_checks=[db.check],
        lifespan_hooks=[lifespan_hook(db), warm_up_hook],
    )

    @app.post("/documents", tags=["knowledge"], status_code=201)
    async def ingest_document(
        body: IngestIn,
        identity: CallerIdentity = Depends(require_write_scope),
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        document_id = str(uuid.uuid4())
        object_key = f"{document_id}.txt"

        try:
            chunk_count = await rag.ingest(
                document_id,
                body.text,
                metadata={
                    "document_id": document_id,
                    "visibility": body.visibility,
                    "ingested_by": identity.id,
                },
            )
        except Exception as exc:  # noqa: BLE001
            raise PlatformError(
                "ingest_failed", f"failed to ingest document: {exc}", status_code=502
            ) from exc

        await store.put_object(object_key, body.text.encode(), content_type="text/plain")

        row = Document(
            id=document_id,
            title=body.title,
            ingested_by=identity.id,
            visibility=body.visibility,
            minio_object_key=object_key,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)

        return {**_document_out(row), "chunk_count": chunk_count}

    @app.post("/query", tags=["knowledge"])
    async def query(
        body: QueryIn, identity: CallerIdentity = Depends(require_identity)
    ) -> dict[str, Any]:
        try:
            result = await rag.query(body.question, caller_id=identity.id)
        except Exception as exc:  # noqa: BLE001
            raise PlatformError(
                "query_failed", f"failed to answer query: {exc}", status_code=502
            ) from exc
        return {"answer": result.answer, "source_document_ids": result.source_document_ids}

    @app.get("/documents", tags=["knowledge"])
    async def list_documents(
        identity: CallerIdentity = Depends(require_identity),
        session: AsyncSession = Depends(get_session),
    ) -> list[dict[str, Any]]:
        stmt = select(Document).where(
            (Document.visibility == "shared") | (Document.ingested_by == identity.id)
        )
        rows = (await session.execute(stmt)).scalars()
        return [_document_out(row) for row in rows]

    return app


app = build_app()
