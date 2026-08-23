import httpx
import pytest
from fastapi import FastAPI, Request

from platform_core.auth import CallerIdentity
from platform_core.errors import PlatformError
from platform_core.facade import UpstreamClient, raise_for_upstream


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
