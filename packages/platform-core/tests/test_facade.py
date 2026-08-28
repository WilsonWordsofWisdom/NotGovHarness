import datetime

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import FastAPI, Request

from platform_core.auth import CallerIdentity
from platform_core.errors import PlatformError
from platform_core.facade import UpstreamClient, UpstreamPeerIdentityError, raise_for_upstream


def _cert_der_with_uri_san(uri: str) -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.UTC))
        .not_valid_after(datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.UniformResourceIdentifier(uri)]), False)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.DER)


class _StubSslObject:
    def __init__(self, der_cert: bytes) -> None:
        self._der_cert = der_cert

    def getpeercert(self, binary_form: bool) -> bytes:  # noqa: FBT001 - matches _ssl.SSLSocket
        assert binary_form is True
        return self._der_cert


class _StubNetworkStream:
    def __init__(self, ssl_object: object | None) -> None:
        self._ssl_object = ssl_object

    def get_extra_info(self, name: str) -> object | None:
        assert name == "ssl_object"
        return self._ssl_object


def _stub_upstream() -> FastAPI:
    app = FastAPI()

    @app.get("/echo")
    async def echo(request: Request):
        return {"seen_identity": request.headers.get("x-service-identity")}

    @app.get("/boom")
    async def boom():
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=500, content={"why": "upstream failed"})

    return app


def _client() -> UpstreamClient:
    transport = httpx.ASGITransport(app=_stub_upstream())
    return UpstreamClient("http://upstream", transport=transport)


async def test_forwards_identity():
    client = _client()
    try:
        resp = await client.forward("GET", "/echo", identity=CallerIdentity(id="builder"))
        assert resp.json() == {"seen_identity": "builder"}
    finally:
        await client.aclose()


async def test_raise_for_upstream_maps_error():
    client = _client()
    try:
        resp = await client.forward("GET", "/boom")
        with pytest.raises(PlatformError) as excinfo:
            raise_for_upstream(resp)
        exc = excinfo.value
        assert exc.code == "upstream_error"
        assert exc.status_code == 502
        assert exc.detail == {"why": "upstream failed"}
    finally:
        await client.aclose()


def _response_with_peer_cert(uri: str | None) -> httpx.Response:
    ssl_object = _StubSslObject(_cert_der_with_uri_san(uri)) if uri else None
    return httpx.Response(200, extensions={"network_stream": _StubNetworkStream(ssl_object)})


def test_verify_peer_accepts_a_matching_spiffe_id():
    client = UpstreamClient(
        "http://upstream", expected_peer_spiffe_id="spiffe://notgovharness/upstream-stub"
    )
    client._verify_peer(_response_with_peer_cert("spiffe://notgovharness/upstream-stub"))


def test_verify_peer_rejects_a_mismatched_spiffe_id():
    client = UpstreamClient(
        "http://upstream", expected_peer_spiffe_id="spiffe://notgovharness/upstream-stub"
    )
    with pytest.raises(UpstreamPeerIdentityError, match="!="):
        client._verify_peer(_response_with_peer_cert("spiffe://notgovharness/some-imposter"))


def test_verify_peer_rejects_a_plain_http_response():
    client = UpstreamClient(
        "http://upstream", expected_peer_spiffe_id="spiffe://notgovharness/upstream-stub"
    )
    with pytest.raises(UpstreamPeerIdentityError, match="no TLS connection"):
        client._verify_peer(httpx.Response(200, extensions={}))


async def test_forward_verifies_peer_when_expected_id_is_set():
    transport = httpx.MockTransport(
        lambda request: _response_with_peer_cert("spiffe://notgovharness/upstream-stub")
    )
    client = UpstreamClient(
        "http://upstream",
        transport=transport,
        expected_peer_spiffe_id="spiffe://notgovharness/some-imposter",
    )
    try:
        with pytest.raises(UpstreamPeerIdentityError):
            await client.forward("GET", "/echo")
    finally:
        await client.aclose()
