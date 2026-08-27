from __future__ import annotations

import jwt
import pytest
from identity_service.config import Settings
from identity_service.keys import generate_signing_key
from identity_service.tokens import issue_autonomous_token


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
