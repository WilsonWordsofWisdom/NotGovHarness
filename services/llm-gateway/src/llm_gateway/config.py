from __future__ import annotations

from platform_core.config import PlatformSettings


class Settings(PlatformSettings):
    service_name: str = "llm-gateway"

    # hybrid: every proxy endpoint requires a verified llm_gateway:call token; no dev-mode bypass
    # for this façade specifically. See
    # docs/superpowers/specs/2026-09-01-llm-gateway-harness-design.md.
    auth_mode: str = "hybrid"
    oauth2_issuer: str = "https://identity-service.notgovharness.local"
    oauth2_audience: str = "notgovharness"
    oauth2_jwks_url: str = "http://identity-service:8000/.well-known/jwks.json"

    # LiteLLM's own native credential (D-058, same shape as ContextForge's admin credential,
    # D-043) — a backend-only secret this façade holds; callers never see or need it.
    litellm_url: str = "http://localhost:4000"
    litellm_virtual_key: str = "changeme"
