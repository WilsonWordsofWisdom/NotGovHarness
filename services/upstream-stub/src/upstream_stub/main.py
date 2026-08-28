"""A minimal upstream service. It is instrumented via ``create_app``, so a façade call into it
shows up as its own spans under the *same* trace — proving cross-hop trace propagation.

``auth_mode="hybrid"`` (default): accepts a delegated bearer token from identity-service when
present (enforcing the ``upstream:call`` scope, the act chain, and delegation depth), falling back
to the Phase 0 dev header when absent — so this still works under `core` alone, no identity-service
required. See platform_core.auth's module docstring.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, Request

from platform_core.app import create_app
from platform_core.auth import CallerIdentity, make_require_identity
from platform_core.config import PlatformSettings
from platform_core.errors import PlatformError
from platform_core.logging import get_logger

log = get_logger("upstream_stub")


class StubSettings(PlatformSettings):
    service_name: str = "upstream-stub"
    auth_mode: str = "hybrid"
    oauth2_issuer: str = "https://identity-service.notgovharness.local"
    oauth2_audience: str = "notgovharness"
    oauth2_jwks_url: str = "http://identity-service:8000/.well-known/jwks.json"


def build_app() -> FastAPI:
    settings = StubSettings()
    require_identity = make_require_identity(settings)
    app = create_app(settings)

    @app.get("/echo", tags=["stub"])
    async def echo(
        request: Request, identity: CallerIdentity = Depends(require_identity)
    ) -> dict[str, object]:
        # Scope is only meaningful for a genuinely-verified delegated token — hybrid's dev-mode
        # fallback (no bearer token at all, `core` alone) has no concept of scopes and must keep
        # working unauthenticated, matching Phase 0. require_scope() would 403 that fallback path
        # too, since its CallerIdentity never carries scopes; checking mode=="delegated" is what
        # actually distinguishes "a real token was verified" from "no token was presented."
        if identity.mode == "delegated" and "upstream:call" not in identity.scopes:
            raise PlatformError(
                "forbidden", "missing required scope: 'upstream:call'", status_code=403
            )

        # The audit-relevant proof for a delegated call: sub is the principal, id is the
        # immediate actor, actor_chain is the full nested delegation — see the harness spec's
        # reference flow ("logs + one Jaeger trace show spiffe_id, sub, and the act chain").
        log.info(
            "echo_called",
            mode=identity.mode,
            on_behalf_of=identity.on_behalf_of,
            actor_chain=identity.actor_chain,
        )
        return {
            "upstream": "ok",
            "seen_identity": identity.id,
            "mode": identity.mode,
            "on_behalf_of": identity.on_behalf_of,
            "actor_chain": identity.actor_chain,
            # kept for the header-based, non-token dev flow that some callers still exercise
            "seen_header": request.headers.get("x-service-identity"),
        }

    return app


app = build_app()
