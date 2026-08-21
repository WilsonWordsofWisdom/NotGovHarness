from fastapi import FastAPI
from fastapi.testclient import TestClient

from platform_core.errors import PlatformError, install_error_handlers


def _app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    async def boom():
        raise PlatformError(
            "teapot", "no coffee here", status_code=418, detail={"kind": "beverage"}
        )

    @app.get("/item/{item_id}")
    async def item(item_id: int):
        return {"item_id": item_id}

    return app


def test_platform_error_envelope():
    r = TestClient(_app()).get("/boom")
    assert r.status_code == 418
    err = r.json()["error"]
    assert err["code"] == "teapot"
    assert err["message"] == "no coffee here"
    assert err["detail"] == {"kind": "beverage"}
    assert "trace_id" in err


def test_validation_error_envelope():
    r = TestClient(_app()).get("/item/not-an-int")
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error"
    assert isinstance(err["detail"], list)
