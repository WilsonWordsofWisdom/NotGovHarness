from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from platform_core.db import Base

# No application-owned tables (Mem0's pgvector backend manages its own schema in this same
# database) — target_metadata is deliberately empty; this service's only migration enables the
# `vector` extension.
config = context.config
target_metadata = Base.metadata
url = os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url")


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_run_async())
