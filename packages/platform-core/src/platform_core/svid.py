"""X.509 SVIDs from the SPIFFE Workload API, for mTLS between services.

Graceful by design: every entry point here degrades to "SPIRE isn't available" rather than
raising, because example-service/upstream-stub also run under the `core` profile alone (no SPIRE)
— that's Phase 0's already-verified plain-HTTP baseline, and this harness only adds to it.

Server-side note: this module only builds the *client* half of mTLS (a service calling out). The
server half (upstream-stub requiring + validating a client cert) is wired directly with uvicorn's
own `ssl_cert_reqs`/`ssl_ca_certs` flags, not through this module — uvicorn doesn't expose the
peer certificate to application code without reaching past its public ASGI interface into
undocumented internals, which this reference deliberately avoids. See the harness spec's risks.
"""

from __future__ import annotations

import ssl
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtensionOID
from spiffe import SpiffeId, X509Source

from .logging import get_logger

log = get_logger("platform_core.svid")


def try_x509_source(timeout_seconds: float = 3.0) -> X509Source | None:
    """Connect to the Workload API, or return None if SPIRE isn't reachable.

    Reads the socket address from ``SPIFFE_ENDPOINT_SOCKET`` (set unconditionally in compose for
    example-service/upstream-stub) — under `core` alone, nothing is listening on that socket, so
    this fails fast rather than hanging for the full timeout.
    """
    try:
        return X509Source(timeout_in_seconds=timeout_seconds)
    except Exception:  # noqa: BLE001 - any failure here means "no SPIRE", not a fatal error
        log.info("svid_unavailable", detail="Workload API unreachable — falling back to plain HTTP")
        return None


def peer_spiffe_id(cert: bytes) -> SpiffeId:
    """Extract the SPIFFE ID (SAN URI) from a DER-encoded peer certificate."""
    from cryptography import x509

    parsed = x509.load_der_x509_certificate(cert)
    try:
        san = parsed.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
    except x509.ExtensionNotFound:
        raise ValueError("peer certificate has no SPIFFE ID (no URI SAN)") from None
    uris = san.get_values_for_type(x509.UniformResourceIdentifier)  # type: ignore[arg-type]
    if not uris:
        raise ValueError("peer certificate has no SPIFFE ID (no URI SAN)")
    return SpiffeId(uris[0])


def write_svid_files(source: X509Source, directory: str) -> tuple[str, str, str]:
    """Write the current SVID + trust bundle as PEM files into ``directory``; returns their paths.

    A file bridge because Python's ``ssl`` module (and uvicorn's ``ssl_keyfile``/``ssl_certfile``/
    ``ssl_ca_certs`` flags) only load certs/keys from paths, not the ``cryptography`` objects the
    `spiffe` library hands back. SVIDs rotate (1h TTL here) — this snapshots whatever's current at
    call time; a long-running process would need to re-write and reload these to track rotation,
    which this reference doesn't do (proving the mechanism works, not running unattended for
    hours — see the harness spec's risks). Caller owns ``directory``'s lifecycle.
    """
    ctx = source.get_x509_context()
    svid = ctx.default_svid
    bundle = ctx.x509_bundle_set.get_bundle_for_trust_domain(svid.spiffe_id.trust_domain)
    if bundle is None:
        # The Workload API always sends a bundle for the caller's own trust domain alongside its
        # SVID — reaching this means the source is in a broken state, not a normal runtime case.
        raise RuntimeError(f"no trust bundle for {svid.spiffe_id.trust_domain}")

    cert_path = Path(directory) / "svid.pem"
    key_path = Path(directory) / "key.pem"
    ca_path = Path(directory) / "bundle.pem"

    cert_path.write_bytes(
        b"".join(c.public_bytes(serialization.Encoding.PEM) for c in svid.cert_chain)
    )
    key_path.write_bytes(
        svid.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    ca_path.write_bytes(
        b"".join(c.public_bytes(serialization.Encoding.PEM) for c in bundle.x509_authorities)
    )
    return str(cert_path), str(key_path), str(ca_path)


@contextmanager
def _pem_files(source: X509Source) -> Iterator[tuple[str, str, str]]:
    """Same as ``write_svid_files``, but into a self-cleaning temp directory."""
    with tempfile.TemporaryDirectory(prefix="svid-") as tmp:
        yield write_svid_files(source, tmp)


def build_client_ssl_context(source: X509Source) -> ssl.SSLContext:
    """An SSLContext presenting this workload's SVID and trusting the SPIRE trust bundle.

    SPIFFE certs aren't meant to match a DNS hostname, so ``check_hostname`` is off — trust comes
    from the peer's cert chaining to the trust bundle (loaded below) plus, ideally, an explicit
    SPIFFE ID check on the response (``peer_spiffe_id`` + the caller comparing it).
    """
    with _pem_files(source) as (cert_path, key_path, ca_path):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_cert_chain(certfile=cert_path, keyfile=key_path)
        context.load_verify_locations(cafile=ca_path)
        return context
