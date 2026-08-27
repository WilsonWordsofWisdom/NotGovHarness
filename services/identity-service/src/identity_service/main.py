"""identity-service: the OAuth2 authorization server + trust anchor (Agent Identity harness).

Greenfield, façade-free (unlike example-service, it doesn't wrap an upstream OSS project) — see
docs/superpowers/specs/2026-08-23-agent-identity-harness-design.md for the full design.
"""

from __future__ import annotations

import bcrypt
from fastapi import Depends, FastAPI, Form
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.app import create_app
from platform_core.db import Database, lifespan_hook, session_dependency
from platform_core.errors import PlatformError

from .config import Settings
from .keys import generate_signing_key
from .models import Client
from .tokens import issue_autonomous_token


class ClientIn(BaseModel):
    client_id: str
    client_secret: str
    spiffe_id: str | None = None
    allowed_scopes: str = ""


class ClientOut(BaseModel):
    client_id: str
    spiffe_id: str | None
    allowed_scopes: str


def build_app() -> FastAPI:
    settings = Settings()
    db = Database(settings.database_url)
    signing_key = generate_signing_key(pem=settings.oauth2_signing_key_pem)
    get_session = session_dependency(db)

    app = create_app(
        settings,
        readiness_checks=[db.check],
        lifespan_hooks=[lifespan_hook(db)],
    )

    @app.post("/oauth/token", tags=["oauth2"])
    async def token(
        grant_type: str = Form(...),
        client_id: str = Form(...),
        client_secret: str = Form(...),
        scope: str = Form(""),
        session: AsyncSession = Depends(get_session),
    ) -> JSONResponse:
        if grant_type != "client_credentials":
            raise PlatformError("unsupported_grant_type", f"unsupported grant_type: {grant_type}")

        client = await session.get(Client, client_id)
        if client is None or client.secret_hash is None:
            raise PlatformError("invalid_client", "unknown client", status_code=401)
        if not bcrypt.checkpw(client_secret.encode(), client.secret_hash.encode()):
            raise PlatformError("invalid_client", "bad client credentials", status_code=401)

        allowed = set(client.scopes())
        requested = set(scope.split()) if scope else allowed
        if not requested <= allowed:
            raise PlatformError("invalid_scope", "requested scope exceeds allowed scopes")

        issued = issue_autonomous_token(
            signing_key, settings, client_id=client_id, scope=" ".join(sorted(requested))
        )
        return JSONResponse(jsonable_encoder(issued))

    @app.get("/.well-known/jwks.json", tags=["oauth2"])
    async def jwks() -> dict:
        return {"keys": [signing_key.jwk()]}

    @app.post("/clients", tags=["admin"], response_model=ClientOut)
    async def register_client(
        body: ClientIn, session: AsyncSession = Depends(get_session)
    ) -> Client:
        existing = await session.get(Client, body.client_id)
        if existing is not None:
            raise PlatformError("client_exists", "client_id already registered", status_code=409)

        client = Client(
            client_id=body.client_id,
            spiffe_id=body.spiffe_id,
            allowed_scopes=body.allowed_scopes,
            secret_hash=bcrypt.hashpw(body.client_secret.encode(), bcrypt.gensalt()).decode(),
        )
        session.add(client)
        await session.commit()
        return client

    @app.get("/clients", tags=["admin"], response_model=list[ClientOut])
    async def list_clients(session: AsyncSession = Depends(get_session)) -> list[Client]:
        return list((await session.execute(select(Client))).scalars().all())

    return app


app = build_app()
