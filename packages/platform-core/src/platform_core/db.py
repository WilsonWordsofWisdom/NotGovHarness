"""Async persistence (Postgres via SQLAlchemy), db-per-service.

Engine-agnostic surface kept deliberately small — construct, hand out sessions, a readiness
``check``, a ``dispose`` lifespan — so a future service can bind a different engine without
consumers changing. Services define models against the shared ``Base``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base; services subclass it for their ORM models."""


class Database:
    """Owns one async engine + session factory for a single service's database."""

    def __init__(self, url: str, *, echo: bool = False) -> None:
        self._engine = create_async_engine(url, echo=echo, pool_pre_ping=True)
        self._sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False)
        # Per-engine instrumentation is safe to call once per engine instance.
        SQLAlchemyInstrumentor().instrument(engine=self._engine.sync_engine)

    @property
    def engine(self):
        return self._engine

    def session(self) -> AsyncSession:
        """Return a new session (use as an async context manager)."""
        return self._sessionmaker()

    async def check(self) -> None:
        """Readiness probe: raises if the database is unreachable."""
        async with self._engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        await self._engine.dispose()


def session_dependency(db: Database) -> Callable[[], AsyncIterator[AsyncSession]]:
    """Build a FastAPI dependency yielding a request-scoped session."""

    async def get_session() -> AsyncIterator[AsyncSession]:
        async with db.session() as session:
            yield session

    return get_session


def lifespan_hook(db: Database) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Build a lifespan hook that disposes the engine on shutdown."""

    @asynccontextmanager
    async def hook(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await db.dispose()

    return hook
