from __future__ import annotations

from platform_core.config import PlatformSettings


class Settings(PlatformSettings):
    service_name: str = "identity-service"
    database_url: str = "postgresql+asyncpg://platform:platform@localhost:5432/identity"

    # Token claims (see docs/superpowers/specs/2026-08-23-agent-identity-harness-design.md).
    oauth2_issuer: str = "https://identity-service.notgovharness.local"
    oauth2_audience: str = "notgovharness"
    access_token_ttl_seconds: int = 300
    max_delegation_depth: int = 3

    # hybrid: only routes that explicitly Depends() on require_identity/require_scope are
    # gated (POST /cards/sign) — /oauth/token, /.well-known/jwks.json, /clients stay open, as
    # before. Self-referential JWKS: identity-service verifies its own issued tokens against its
    # own published keys — the standard resource-server pattern (see the Agent Registry harness
    # design, D-029/D-031).
    auth_mode: str = "hybrid"
    oauth2_jwks_url: str = "http://localhost:8000/.well-known/jwks.json"

    # RS256 signing key, PEM-encoded. Unset in dev: identity-service generates an ephemeral
    # keypair at startup (tokens are short-lived, so a restart naturally invalidating old ones
    # is fine — see the harness spec's signing-key-management risk note. A KMS is out of scope).
    oauth2_signing_key_pem: str | None = None
