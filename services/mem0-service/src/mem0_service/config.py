from __future__ import annotations

from platform_core.config import PlatformSettings


class Settings(PlatformSettings):
    service_name: str = "mem0-service"
    # asyncpg URL used only for this service's own readiness check (platform_core.db.Database) —
    # Mem0's pgvector backend connects to the same `memory` database separately, via psycopg.
    database_url: str = "postgresql+asyncpg://platform:platform@localhost:5432/memory"

    # hybrid: POST/DELETE /memories require a verified memory:write token; GET reads require any
    # authenticated caller. See docs/superpowers/specs/2026-09-08-memory-harness-design.md.
    auth_mode: str = "hybrid"
    oauth2_issuer: str = "https://identity-service.notgovharness.local"
    oauth2_audience: str = "notgovharness"
    oauth2_jwks_url: str = "http://identity-service:8000/.well-known/jwks.json"

    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_user: str = "platform"
    pg_password: str = "platform"
    pg_dbname: str = "memory"

    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dims: int = 384

    llm_gateway_url: str = "http://localhost:8099"
    llm_gateway_model: str = "qwen3.8"

    # This service's own OAuth2 client (D-062) — a real registered identity, not a borrowed
    # simulated-principal (D-030), since mem0-service is a real platform service calling another.
    identity_url: str = "http://localhost:8090"
    mem0_client_id: str = "mem0-service"
    mem0_client_secret: str = "mem0-service-dev-secret"
