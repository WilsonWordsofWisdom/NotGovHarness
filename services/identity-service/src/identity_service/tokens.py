"""JWT issuance: the AIP-shaped claim model from the harness design spec.

Standard claims (iss/aud/exp/iat/jti/scope) plus agent-grade ones: ``sub`` (the principal),
``mode`` (delegated vs autonomous), ``act`` (RFC 8693 actor chain, delegated only), ``prov``
(provenance for Audit, delegated only), ``depth`` (delegation depth, enforced at verify by
platform-core — this module only stamps it).

Verification lives in ``platform-core`` (the ``oauth2`` auth mode), not here — identity-service
only issues.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

import jwt

from .config import Settings
from .keys import SigningKey


class TokenExchangeError(Exception):
    """A domain error during token exchange; ``code`` maps to a PlatformError code in main.py."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class IssuedToken:
    access_token: str
    token_type: str = "Bearer"
    expires_in: int = 0
    scope: str = ""


def issue_autonomous_token(
    signing_key: SigningKey,
    settings: Settings,
    *,
    client_id: str,
    scope: str,
) -> IssuedToken:
    """``grant_type=client_credentials`` — the agent acting as itself, not on anyone's behalf."""
    now = int(time.time())
    claims = {
        "iss": settings.oauth2_issuer,
        "aud": settings.oauth2_audience,
        "iat": now,
        "exp": now + settings.access_token_ttl_seconds,
        "jti": uuid.uuid4().hex,
        "sub": client_id,
        "scope": scope,
        "mode": "autonomous",
        "depth": 0,
    }
    encoded = jwt.encode(
        claims, signing_key.private_pem, algorithm="RS256", headers={"kid": signing_key.kid}
    )
    return IssuedToken(
        access_token=encoded, expires_in=settings.access_token_ttl_seconds, scope=scope
    )


def verify_own_token(signing_key: SigningKey, settings: Settings, token: str) -> dict:
    """Verify a token this identity-service itself issued (self-referential — no JWKS fetch
    needed, unlike a downstream service verifying via platform-core's ``oauth2`` mode).
    """
    try:
        return jwt.decode(
            token,
            signing_key.public_key,
            algorithms=["RS256"],
            audience=settings.oauth2_audience,
        )
    except jwt.InvalidTokenError as exc:
        raise TokenExchangeError("invalid_token", str(exc)) from exc


def issue_delegated_token(
    signing_key: SigningKey,
    settings: Settings,
    *,
    subject_claims: dict,
    actor_claims: dict,
    requested_scope: str,
    request_id: str | None,
    trace_id: str | None,
) -> IssuedToken:
    """``grant_type=urn:ietf:params:oauth:grant-type:token-exchange`` (RFC 8693) — the agent in
    ``actor_claims`` acting on behalf of the principal in ``subject_claims``.
    """
    subject_scope = set(subject_claims.get("scope", "").split())
    actor_scope = set(actor_claims.get("scope", "").split())
    requested = set(requested_scope.split()) if requested_scope else (subject_scope & actor_scope)
    if not (requested <= actor_scope and requested <= subject_scope):
        raise TokenExchangeError(
            "invalid_scope", "requested scope exceeds the actor's or subject's allowed scope"
        )

    # The chain accumulates on the *subject* side: re-delegation feeds the previous hop's
    # resulting token back in as the new subject_token, with a fresh actor added each time.
    depth = int(subject_claims.get("depth", 0)) + 1
    if depth > settings.max_delegation_depth:
        raise TokenExchangeError(
            "delegation_depth_exceeded",
            f"delegation depth {depth} exceeds "
            f"max_delegation_depth={settings.max_delegation_depth}",
        )

    act: dict = {"sub": actor_claims["sub"]}
    if subject_claims.get("act"):
        act["act"] = subject_claims["act"]

    now = int(time.time())
    claims = {
        "iss": settings.oauth2_issuer,
        "aud": settings.oauth2_audience,
        "iat": now,
        "exp": now + settings.access_token_ttl_seconds,
        "jti": uuid.uuid4().hex,
        "sub": subject_claims["sub"],
        "scope": " ".join(sorted(requested)),
        "mode": "delegated",
        "act": act,
        "prov": {"request_id": request_id, "trace_id": trace_id},
        "depth": depth,
    }
    encoded = jwt.encode(
        claims, signing_key.private_pem, algorithm="RS256", headers={"kid": signing_key.kid}
    )
    return IssuedToken(
        access_token=encoded,
        expires_in=settings.access_token_ttl_seconds,
        scope=claims["scope"],
    )
