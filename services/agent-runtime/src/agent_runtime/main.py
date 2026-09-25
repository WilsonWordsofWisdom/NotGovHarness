"""agent-runtime: a real, deployable single-agent CrewAI runtime (Wave 4 Deployment Pipeline
dependency). One shared, generic image (D-087) — a specific deployment's role/goal/backstory are
baked in via environment variables at container start, not this image's own code. See
docs/superpowers/specs/2026-09-25-deployment-pipeline-harness-design.md and decisions D-087..D-089.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI
from pydantic import BaseModel

from platform_core.app import create_app
from platform_core.auth import CallerIdentity, make_require_identity, require_scope
from platform_core.errors import PlatformError
from platform_core.llm_gateway_auth import TokenClient

from .config import Settings
from .crew_layer import CrewLayer


class InvokeIn(BaseModel):
    input: str


def build_app() -> FastAPI:
    # agent_role/goal/backstory are required with no default (D-087) — pydantic-settings fills
    # them from env vars at runtime, which pyright's static check of the constructor can't see.
    settings = Settings()  # pyright: ignore[reportCallIssue]
    require_identity = make_require_identity(settings)
    require_invoke_scope = require_scope(require_identity, "agent_runtime:invoke")

    token_client = TokenClient(
        identity_url=settings.identity_url,
        client_id=settings.agent_runtime_client_id,
        client_secret=settings.agent_runtime_client_secret,
        scope="llm_gateway:call",
    )
    crew = CrewLayer(settings, token_client)

    app = create_app(settings)

    @app.post("/invoke", tags=["agent-runtime"])
    async def invoke(
        body: InvokeIn, _identity: CallerIdentity = Depends(require_invoke_scope)
    ) -> dict[str, Any]:
        try:
            output = await crew.run(body.input)
        except Exception as exc:  # noqa: BLE001
            raise PlatformError(
                "invoke_failed", f"agent invocation failed: {exc}", status_code=502
            ) from exc
        return {"output": output}

    return app


app = build_app()
