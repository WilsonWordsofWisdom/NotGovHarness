"""agent-gateway: façade in front of ContextForge (Wave 3 Agent Gateway harness).

Façade / adapter service, per architecture.md's definition — wraps ContextForge behind our
identity + OpenAPI contract. Our identity-service gates every caller; ContextForge's own admin
credential is a backend-only secret this service holds (D-043). No local persistence — this is
a pure proxy, no Postgres DB of its own.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI
from pydantic import BaseModel

from platform_core.app import create_app
from platform_core.auth import CallerIdentity, make_require_identity, require_scope
from platform_core.errors import PlatformError

from .config import Settings
from .contextforge import ContextForgeClient, ContextForgeError


class RegisterServerIn(BaseModel):
    name: str
    url: str
    transport: str = "SSE"


def build_app() -> FastAPI:
    settings = Settings()
    require_identity = make_require_identity(settings)
    require_call_scope = require_scope(require_identity, "agent_gateway:call")
    cf = ContextForgeClient(
        settings.contextforge_url,
        settings.contextforge_admin_email,
        settings.contextforge_admin_password,
    )

    app = create_app(settings)

    @app.post("/mcp-servers", tags=["agent-gateway"], status_code=201)
    async def register_mcp_server(
        body: RegisterServerIn, _identity: CallerIdentity = Depends(require_call_scope)
    ) -> dict[str, Any]:
        try:
            return await cf.register_gateway(body.name, body.url, body.transport)
        except ContextForgeError as exc:
            raise PlatformError("contextforge_error", exc.detail, status_code=502) from exc

    @app.get("/mcp-servers", tags=["agent-gateway"])
    async def list_mcp_servers(
        _identity: CallerIdentity = Depends(require_call_scope),
    ) -> list[dict[str, Any]]:
        try:
            return await cf.list_gateways()
        except ContextForgeError as exc:
            raise PlatformError("contextforge_error", exc.detail, status_code=502) from exc

    return app


app = build_app()
