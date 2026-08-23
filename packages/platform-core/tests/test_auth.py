from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from platform_core.auth import CallerIdentity, make_require_identity
from platform_core.config import PlatformSettings


def _app(auth_mode: str = "dev") -> FastAPI:
    settings = PlatformSettings(service_name="t", auth_mode=auth_mode)
    require_identity = make_require_identity(settings)
    app = FastAPI()

    @app.get("/whoami")
    async def whoami(identity: CallerIdentity = Depends(require_identity)):
        return {"id": identity.id, "kind": identity.kind}

    return app


def test_dev_reads_header():
    r = TestClient(_app()).get("/whoami", headers={"x-service-identity": "builder"})
    assert r.json() == {"id": "builder", "kind": "service"}


def test_dev_defaults_anonymous():
    r = TestClient(_app()).get("/whoami")
    assert r.json()["id"] == "anonymous"
