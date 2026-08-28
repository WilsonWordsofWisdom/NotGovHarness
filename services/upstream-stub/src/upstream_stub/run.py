"""Entrypoint: launches uvicorn with mTLS when SPIRE is available, plain HTTP otherwise.

Replaces a plain `uvicorn upstream_stub.main:app` CLI invocation because TLS has to be configured
at process startup (uvicorn has no hot-reload for it) — this fetches the SVID *before* uvicorn
starts, rather than from within the ASGI app. Falls back to today's plain HTTP so upstream-stub
keeps working under the `core` profile alone, without SPIRE — see svid.py's module docstring.
"""

from __future__ import annotations

import ssl
import tempfile

import uvicorn

from platform_core.svid import try_x509_source, write_svid_files


def main() -> None:
    source = try_x509_source()
    if source is None:
        uvicorn.run("upstream_stub.main:app", host="0.0.0.0", port=8000)  # noqa: S104
        return

    with tempfile.TemporaryDirectory(prefix="svid-") as tmp:
        cert_path, key_path, ca_path = write_svid_files(source, tmp)
        uvicorn.run(
            "upstream_stub.main:app",
            host="0.0.0.0",  # noqa: S104
            port=8000,
            ssl_keyfile=key_path,
            ssl_certfile=cert_path,
            ssl_ca_certs=ca_path,
            ssl_cert_reqs=ssl.CERT_REQUIRED,  # requires + validates the caller's SVID
        )


if __name__ == "__main__":
    main()
