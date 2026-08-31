from __future__ import annotations

from platform_core.config import PlatformSettings


class Settings(PlatformSettings):
    service_name: str = "sandbox-service"
    database_url: str = "postgresql+asyncpg://platform:platform@localhost:5432/sandbox"

    # hybrid: POST /executions requires a verified sandbox:execute token; GET reads require any
    # authenticated caller. See docs/superpowers/specs/2026-08-31-sandbox-harness-design.md.
    auth_mode: str = "hybrid"
    oauth2_issuer: str = "https://identity-service.notgovharness.local"
    oauth2_audience: str = "notgovharness"
    oauth2_jwks_url: str = "http://identity-service:8000/.well-known/jwks.json"

    # Docker Engine the executor launches ephemeral containers on. None uses the local
    # environment's default (DOCKER_HOST or the platform socket) — set explicitly in compose.
    docker_base_url: str | None = None
    sandbox_image: str = "python:3.12-slim"
    default_timeout_seconds: int = 10
    max_timeout_seconds: int = 60
    memory_limit: str = "128m"
    nano_cpus: int = 500_000_000  # 0.5 CPU
