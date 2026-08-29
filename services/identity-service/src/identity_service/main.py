"""identity-service: the OAuth2 authorization server + trust anchor (Agent Identity harness).

Greenfield, façade-free (unlike example-service, it doesn't wrap an upstream OSS project) — see
docs/superpowers/specs/2026-08-23-agent-identity-harness-design.md for the full design.
"""

from __future__ import annotations

import bcrypt
from fastapi import Depends, FastAPI, Form, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.app import create_app
from platform_core.auth import CallerIdentity, make_require_identity, require_scope
from platform_core.context import current_trace_id
from platform_core.db import Database, lifespan_hook, session_dependency
from platform_core.errors import PlatformError

from .cards import sign_card
from .config import Settings
from .keys import generate_signing_key
from .models import Client
from .tokens import (
    TokenExchangeError,
    issue_autonomous_token,
    issue_delegated_token,
    verify_own_token,
)

TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"


class ClientIn(BaseModel):
    client_id: str
    client_secret: str
    spiffe_id: str | None = None
    allowed_scopes: str = ""


class ClientOut(BaseModel):
    client_id: str
    spiffe_id: str | None
    allowed_scopes: str


class CardSignOut(BaseModel):
    signing_algorithm: str
    signing_key_id: str
    signature_value: str


def build_app() -> FastAPI:
    settings = Settings()
    db = Database(settings.database_url)
    signing_key = generate_signing_key(pem=settings.oauth2_signing_key_pem)
    get_session = session_dependency(db)
    require_identity = make_require_identity(settings)
    require_sign_scope = require_scope(require_identity, "agentcard:sign")

    app = create_app(
        settings,
        readiness_checks=[db.check],
        lifespan_hooks=[lifespan_hook(db)],
    )
    # Exposed for tests that need to mint tokens against this exact running instance's key
    # without going through the DB-backed client_credentials grant.
    app.state.signing_key = signing_key

    @app.post("/oauth/token", tags=["oauth2"])
    async def token(
        request: Request,
        grant_type: str = Form(...),
        client_id: str | None = Form(None),
        client_secret: str | None = Form(None),
        subject_token: str | None = Form(None),
        actor_token: str | None = Form(None),
        scope: str = Form(""),
        session: AsyncSession = Depends(get_session),
    ) -> JSONResponse:
        if grant_type == "client_credentials":
            if not client_id or not client_secret:
                raise PlatformError("invalid_request", "client_id and client_secret are required")

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

        if grant_type == TOKEN_EXCHANGE_GRANT:
            if not subject_token or not actor_token:
                raise PlatformError("invalid_request", "subject_token and actor_token are required")
            try:
                subject_claims = verify_own_token(signing_key, settings, subject_token)
                actor_claims = verify_own_token(signing_key, settings, actor_token)
                issued = issue_delegated_token(
                    signing_key,
                    settings,
                    subject_claims=subject_claims,
                    actor_claims=actor_claims,
                    requested_scope=scope,
                    request_id=request.headers.get("x-request-id"),
                    trace_id=current_trace_id(),
                )
            except TokenExchangeError as exc:
                raise PlatformError(exc.code, exc.message, status_code=401) from exc
            return JSONResponse(jsonable_encoder(issued))

        raise PlatformError("unsupported_grant_type", f"unsupported grant_type: {grant_type}")

    @app.get("/.well-known/jwks.json", tags=["oauth2"])
    async def jwks() -> dict:
        return {"keys": [signing_key.jwk()]}

    @app.post("/cards/sign", tags=["agent-registry"], response_model=CardSignOut)
    async def sign_agent_card(
        card: dict, _identity: CallerIdentity = Depends(require_sign_scope)
    ) -> dict:
        # No second trust root — the same key that signs OAuth2 tokens signs Agent Cards (D-029).
        return sign_card(signing_key, card)

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
