"""Wraps Mem0's `Memory`: pgvector + FastEmbed + llm-gateway (D-059..D-063).

`MEM0_TELEMETRY` must be set before `mem0` is ever imported (D-059) — done here, at this module's
top, before the `mem0` import line, since nothing else in this service imports it first.

`Memory.from_config(...)` is lazy (built on first real use, in a threadpool), not constructed at
`Mem0Layer.__init__` — it eagerly connects to pgvector *and* loads the FastEmbed model, so doing
it at service-startup/import time would make even `GET /healthz` depend on a live database and a
model download, unlike every other harness's lazily-connecting dependencies.
"""

from __future__ import annotations

import os

os.environ.setdefault("MEM0_TELEMETRY", "false")

from typing import Any  # noqa: E402

from mem0 import Memory  # noqa: E402
from starlette.concurrency import run_in_threadpool  # noqa: E402

from platform_core.llm_gateway_auth import TokenClient, call_with_auth_retry  # noqa: E402

from .config import Settings  # noqa: E402


class Mem0Layer:
    def __init__(self, settings: Settings, token_client: TokenClient) -> None:
        self._token_client = token_client
        self._config = {
            "vector_store": {
                "provider": "pgvector",
                "config": {
                    "host": settings.pg_host,
                    "port": settings.pg_port,
                    "user": settings.pg_user,
                    "password": settings.pg_password,
                    "dbname": settings.pg_dbname,
                    "embedding_model_dims": settings.embedding_dims,
                },
            },
            "embedder": {
                "provider": "fastembed",
                "config": {
                    "model": settings.embedding_model,
                    "embedding_dims": settings.embedding_dims,
                },
            },
            "llm": {
                "provider": "openai",
                "config": {
                    "model": settings.llm_gateway_model,
                    "openai_base_url": settings.llm_gateway_url,
                    # Placeholder — overwritten before every operation via call_with_auth_retry's
                    # apply_token callback. Mem0 requires a non-empty value at construction time
                    # even though the real one is supplied later.
                    "api_key": "unset",
                },
            },
        }
        self._memory: Memory | None = None

    async def _ensure_memory(self) -> Memory:
        if self._memory is None:
            self._memory = await run_in_threadpool(Memory.from_config, self._config)
        return self._memory

    async def warm_up(self) -> None:
        """Pre-construct Memory (pgvector connection + FastEmbed model load) at service
        startup rather than on the first live request — same "pre-warm, don't surprise the
        first caller" lesson as Sandbox's image pull and Guardrails' spaCy model."""
        await self._ensure_memory()

    def _apply_token(self, memory: Memory, token: str) -> None:
        # Reaching into Mem0's internal openai.OpenAI client: its `api_key` is read fresh on
        # every request (confirmed directly against the installed SDK's request-building code,
        # not just assumed from the property existing), so mutating it here before each
        # operation is how this platform's short-lived, refreshable tokens reach a client Mem0
        # itself only ever constructs once. D-062 — the bridge between Mem0's synchronous,
        # construction-time OpenAI client and this platform's async, refresh-based auth model.
        memory.llm.client.api_key = token

    async def add(self, messages: list[dict[str, Any]], user_id: str) -> dict[str, Any]:
        memory = await self._ensure_memory()
        return await call_with_auth_retry(
            self._token_client,
            lambda token: self._apply_token(memory, token),
            lambda: run_in_threadpool(memory.add, messages, user_id=user_id),
        )

    async def search(self, query: str, user_id: str) -> dict[str, Any]:
        # This mem0ai version's search()/get_all() take a `filters` dict, not a `user_id` kwarg
        # directly — verified against the installed version's actual signature before assuming.
        memory = await self._ensure_memory()
        return await call_with_auth_retry(
            self._token_client,
            lambda token: self._apply_token(memory, token),
            lambda: run_in_threadpool(memory.search, query, filters={"user_id": user_id}),
        )

    async def get_all(self, user_id: str) -> dict[str, Any]:
        memory = await self._ensure_memory()
        return await run_in_threadpool(memory.get_all, filters={"user_id": user_id})

    async def delete(self, memory_id: str) -> dict[str, Any]:
        memory = await self._ensure_memory()
        return await run_in_threadpool(memory.delete, memory_id)
