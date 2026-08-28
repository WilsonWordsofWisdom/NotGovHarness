from __future__ import annotations

import jwt
import pytest
from identity_service.config import Settings
from identity_service.keys import generate_signing_key
from identity_service.tokens import (
    TokenExchangeError,
    issue_autonomous_token,
    issue_delegated_token,
    verify_own_token,
)


@pytest.fixture
def signing_key():
    return generate_signing_key()


@pytest.fixture
def settings():
    return Settings(service_name="identity-service")


def test_generated_key_has_a_kid_and_rs256_jwk(signing_key):
    jwk = signing_key.jwk()
    assert jwk["kty"] == "RSA"
    assert jwk["alg"] == "RS256"
    assert jwk["kid"] == signing_key.kid


def test_provided_pem_is_used_instead_of_generating(signing_key):
    from_pem = generate_signing_key(pem=signing_key.private_pem)
    assert from_pem.private_pem == signing_key.private_pem
    # Same key material, different (freshly generated) kid.
    assert from_pem.jwk()["n"] == signing_key.jwk()["n"]


def test_autonomous_token_verifies_with_the_public_key(signing_key, settings):
    issued = issue_autonomous_token(
        signing_key, settings, client_id="example-service", scope="widgets:read"
    )
    assert issued.token_type == "Bearer"
    assert issued.expires_in == settings.access_token_ttl_seconds

    claims = jwt.decode(
        issued.access_token,
        signing_key.public_key,
        algorithms=["RS256"],
        audience=settings.oauth2_audience,
    )
    assert claims["iss"] == settings.oauth2_issuer
    assert claims["sub"] == "example-service"
    assert claims["scope"] == "widgets:read"
    assert claims["mode"] == "autonomous"
    assert claims["depth"] == 0
    assert "jti" in claims


def test_autonomous_token_header_carries_the_kid(signing_key, settings):
    issued = issue_autonomous_token(signing_key, settings, client_id="c", scope="")
    header = jwt.get_unverified_header(issued.access_token)
    assert header["kid"] == signing_key.kid
    assert header["alg"] == "RS256"


def test_wrong_key_fails_verification(signing_key, settings):
    other_key = generate_signing_key()
    issued = issue_autonomous_token(signing_key, settings, client_id="c", scope="")
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(
            issued.access_token,
            other_key.public_key,
            algorithms=["RS256"],
            audience=settings.oauth2_audience,
        )


def _issue_and_verify(signing_key, settings, *, client_id, scope):
    issued = issue_autonomous_token(signing_key, settings, client_id=client_id, scope=scope)
    return verify_own_token(signing_key, settings, issued.access_token)


def test_delegated_token_carries_principal_as_sub_and_agent_in_act(signing_key, settings):
    subject = _issue_and_verify(signing_key, settings, client_id="alice", scope="widgets:read")
    actor = _issue_and_verify(
        signing_key, settings, client_id="example-service", scope="widgets:read"
    )

    issued = issue_delegated_token(
        signing_key,
        settings,
        subject_claims=subject,
        actor_claims=actor,
        requested_scope="",
        request_id="req-1",
        trace_id="trace-1",
    )
    claims = verify_own_token(signing_key, settings, issued.access_token)

    assert claims["mode"] == "delegated"
    assert claims["sub"] == "alice"  # the principal, not the agent
    assert claims["act"] == {"sub": "example-service"}
    assert claims["prov"] == {"request_id": "req-1", "trace_id": "trace-1"}
    assert claims["depth"] == 1
    assert claims["scope"] == "widgets:read"  # narrowed to the actor/subject intersection


def test_delegated_scope_cannot_exceed_actor_or_subject_scope(signing_key, settings):
    subject = _issue_and_verify(signing_key, settings, client_id="alice", scope="widgets:read")
    actor = _issue_and_verify(signing_key, settings, client_id="agent", scope="widgets:read")

    with pytest.raises(TokenExchangeError) as exc:
        issue_delegated_token(
            signing_key,
            settings,
            subject_claims=subject,
            actor_claims=actor,
            requested_scope="widgets:write",  # neither actor nor subject has this
            request_id=None,
            trace_id=None,
        )
    assert exc.value.code == "invalid_scope"


def test_re_delegation_nests_the_actor_chain_and_increments_depth(signing_key, settings):
    subject = _issue_and_verify(signing_key, settings, client_id="alice", scope="widgets:read")
    agent_a = _issue_and_verify(signing_key, settings, client_id="agent-a", scope="widgets:read")

    hop1 = issue_delegated_token(
        signing_key,
        settings,
        subject_claims=subject,
        actor_claims=agent_a,
        requested_scope="",
        request_id=None,
        trace_id=None,
    )
    hop1_claims = verify_own_token(signing_key, settings, hop1.access_token)

    agent_b = _issue_and_verify(signing_key, settings, client_id="agent-b", scope="widgets:read")
    hop2 = issue_delegated_token(
        signing_key,
        settings,
        subject_claims=hop1_claims,  # agent A hands the delegated token onward as the subject
        actor_claims=agent_b,
        requested_scope="",
        request_id=None,
        trace_id=None,
    )
    hop2_claims = verify_own_token(signing_key, settings, hop2.access_token)

    assert hop2_claims["depth"] == 2
    # Nested: the whole chain of custody is auditable, not just the immediate actor.
    assert hop2_claims["act"] == {"sub": "agent-b", "act": {"sub": "agent-a"}}
    assert hop2_claims["sub"] == "alice"  # the principal survives the whole chain


def test_delegation_depth_limit_is_enforced_at_issuance(signing_key, settings):
    subject = _issue_and_verify(signing_key, settings, client_id="alice", scope="widgets:read")
    current_subject = subject
    for i in range(settings.max_delegation_depth):
        agent = _issue_and_verify(
            signing_key, settings, client_id=f"agent-{i}", scope="widgets:read"
        )
        issued = issue_delegated_token(
            signing_key,
            settings,
            subject_claims=current_subject,
            actor_claims=agent,
            requested_scope="",
            request_id=None,
            trace_id=None,
        )
        current_subject = verify_own_token(signing_key, settings, issued.access_token)

    one_too_many = _issue_and_verify(
        signing_key, settings, client_id="agent-overflow", scope="widgets:read"
    )
    with pytest.raises(TokenExchangeError) as exc:
        issue_delegated_token(
            signing_key,
            settings,
            subject_claims=current_subject,
            actor_claims=one_too_many,
            requested_scope="",
            request_id=None,
            trace_id=None,
        )
    assert exc.value.code == "delegation_depth_exceeded"
