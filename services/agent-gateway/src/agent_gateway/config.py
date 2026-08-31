from __future__ import annotations

from platform_core.config import PlatformSettings


class Settings(PlatformSettings):
    service_name: str = "agent-gateway"

    # hybrid: every proxy endpoint requires a verified agent_gateway:call token; no dev-mode
    # bypass for this façade specifically (see main.py's require_call_scope). See
    # docs/superpowers/specs/2026-08-31-agent-gateway-harness-design.md.
    auth_mode: str = "hybrid"
    oauth2_issuer: str = "https://identity-service.notgovharness.local"
    oauth2_audience: str = "notgovharness"
    oauth2_jwks_url: str = "http://identity-service:8000/.well-known/jwks.json"

    # ContextForge's own native credential (D-043) — a backend-only secret this façade holds;
    # callers never see or need it. Dev-only bootstrap values, matching contextforge's own
    # compose block.
    contextforge_url: str = "http://localhost:4444"
    contextforge_admin_email: str = "admin@example.com"
    contextforge_admin_password: str = "changeme123"
