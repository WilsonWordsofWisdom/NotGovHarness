"""llm-gateway: façade in front of LiteLLM (Wave 1 LLM Gateway harness, resumed).

Façade / adapter service, per architecture.md's definition — wraps LiteLLM behind our identity +
OpenAPI contract. Our identity-service gates every caller; LiteLLM's own virtual key is a
backend-only secret this service holds (D-058). No local persistence — this is a pure proxy, no
Postgres DB of its own. See docs/superpowers/specs/2026-09-01-llm-gateway-harness-design.md.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI

from platform_core.app import create_app
from platform_core.auth import CallerIdentity, make_require_identity, require_scope
from platform_core.facade import UpstreamClient, lifespan_hook, raise_for_upstream

from .config import Settings


def build_app() -> FastAPI:
    settings = Settings()
    require_identity = make_require_identity(settings)
    require_call_scope = require_scope(require_identity, "llm_gateway:call")
    upstream = UpstreamClient(settings.litellm_url)

    app = create_app(settings, lifespan_hooks=[lifespan_hook(upstream)])

    @app.post("/chat/completions", tags=["llm-gateway"])
    async def chat_completions(
        body: dict[str, Any], _identity: CallerIdentity = Depends(require_call_scope)
    ) -> dict[str, Any]:
        response = await upstream.forward(
            "POST",
            "/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {settings.litellm_virtual_key}"},
        )
        raise_for_upstream(response)
        return response.json()

    return app


app = build_app()
