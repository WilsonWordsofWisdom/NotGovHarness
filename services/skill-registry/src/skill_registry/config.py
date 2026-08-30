from __future__ import annotations

from platform_core.config import PlatformSettings


class Settings(PlatformSettings):
    service_name: str = "skill-registry"
    database_url: str = "postgresql+asyncpg://platform:platform@localhost:5432/skill_registry"

    # hybrid: POST /skills requires a verified skill_registry:publish token; reads stay open.
    # See docs/superpowers/specs/2026-08-30-skill-registry-harness-design.md.
    auth_mode: str = "hybrid"
    oauth2_issuer: str = "https://identity-service.notgovharness.local"
    oauth2_audience: str = "notgovharness"
    oauth2_jwks_url: str = "http://identity-service:8000/.well-known/jwks.json"

    minio_endpoint: str = "localhost:9090"
    minio_access_key: str = "minio"
    minio_secret_key: str = "miniosecret123"
    minio_bucket: str = "skill-registry"
    minio_secure: bool = False
