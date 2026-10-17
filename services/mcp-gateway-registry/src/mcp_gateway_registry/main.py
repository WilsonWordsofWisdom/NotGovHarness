"""mcp-gateway-registry: façade in front of ContextForge (Wave 3 Agent Gateway harness, renamed
MCP Gateway Registry -- D-105).

Façade / adapter service, per architecture.md's definition — wraps ContextForge behind our
identity + OpenAPI contract. Our identity-service gates every caller; ContextForge's own admin
credential is a backend-only secret this service holds (D-043). No local persistence — this is
a pure proxy, no Postgres DB of its own.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
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


class CallToolIn(BaseModel):
    arguments: dict[str, Any] = {}


def build_app() -> FastAPI:
    settings = Settings()
    require_identity = make_require_identity(settings)
    require_call_scope = require_scope(require_identity, "mcp_gateway_registry:call")
    cf = ContextForgeClient(
        settings.contextforge_url,
        settings.contextforge_admin_email,
        settings.contextforge_admin_password,
    )

    app = create_app(settings)

    @app.post("/mcp-servers", tags=["mcp-gateway-registry"], status_code=201)
    async def register_mcp_server(
        body: RegisterServerIn, _identity: CallerIdentity = Depends(require_call_scope)
    ) -> dict[str, Any]:
        try:
            return await cf.register_gateway(body.name, body.url, body.transport)
        except ContextForgeError as exc:
            raise PlatformError("contextforge_error", exc.detail, status_code=502) from exc

    @app.get("/mcp-servers", tags=["mcp-gateway-registry"])
    async def list_mcp_servers(
        _identity: CallerIdentity = Depends(require_call_scope),
    ) -> list[dict[str, Any]]:
        try:
            return await cf.list_gateways()
        except ContextForgeError as exc:
            raise PlatformError("contextforge_error", exc.detail, status_code=502) from exc

    @app.get("/tools", tags=["mcp-gateway-registry"])
    async def list_tools(
        _identity: CallerIdentity = Depends(require_call_scope),
    ) -> list[dict[str, Any]]:
        try:
            return await cf.list_tools()
        except ContextForgeError as exc:
            raise PlatformError("contextforge_error", exc.detail, status_code=502) from exc

    @app.post("/tools/{name}/call", tags=["mcp-gateway-registry"])
    async def call_tool(
        name: str, body: CallToolIn, _identity: CallerIdentity = Depends(require_call_scope)
    ) -> dict[str, Any]:
        try:
            return await cf.call_tool(name, body.arguments)
        except ContextForgeError as exc:
            raise PlatformError("contextforge_error", exc.detail, status_code=502) from exc

    static_dir = Path(__file__).resolve().parents[2] / "static"
    if static_dir.is_dir():
        # Mounted last, after every API route above: Starlette matches routes in registration
        # order, so /mcp-servers and friends are still handled by the API -- this only catches
        # paths nothing else claimed (/ui, /ui/index.html). Same shape as Skill Registry's /ui
        # (D-037) and Agent Builder's (D-096).
        app.mount("/ui", StaticFiles(directory=static_dir, html=True), name="ui")

    return app


app = build_app()
