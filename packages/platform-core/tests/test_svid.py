from __future__ import annotations

import datetime

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from platform_core.svid import peer_spiffe_id, try_x509_source


def _self_signed_cert_with_uri_san(uri: str | None) -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.UTC))
        .not_valid_after(datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1))
    )
    if uri:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.UniformResourceIdentifier(uri)]), critical=False
        )
    cert = builder.sign(key, hashes.SHA256())
    return cert.public_bytes(serialization.Encoding.DER)


def test_peer_spiffe_id_extracts_the_uri_san():
    der = _self_signed_cert_with_uri_san("spiffe://notgovharness/example-service")
    spiffe_id = peer_spiffe_id(der)
    assert str(spiffe_id) == "spiffe://notgovharness/example-service"


def test_peer_spiffe_id_rejects_a_cert_with_no_uri_san():
    der = _self_signed_cert_with_uri_san(None)
    with pytest.raises(ValueError, match="no SPIFFE ID"):
        peer_spiffe_id(der)


def test_try_x509_source_degrades_gracefully_without_spire():
    # No SPIRE running in this test process — exactly the condition every service must tolerate
    # under the `core` profile alone. Bounded by a short timeout so the suite doesn't hang.
    assert try_x509_source(timeout_seconds=1.0) is None
