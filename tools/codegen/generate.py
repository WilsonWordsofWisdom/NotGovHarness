"""Export each service's OpenAPI contract and generate a typed client from it.

Services are the source of truth for their own contract. Output:
  services/<svc>/openapi.json     — the published contract
  clients/<pkg>_client/           — a generated typed client (consumed by other services)
"""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# service directory name -> "module:attr" of its FastAPI app
SERVICES = {
    "example-service": "example_service.main:app",
}


def main() -> None:
    (ROOT / "clients").mkdir(exist_ok=True)
    for service, target in SERVICES.items():
        module_name, attr = target.split(":")
        app = getattr(importlib.import_module(module_name), attr)

        openapi_path = ROOT / "services" / service / "openapi.json"
        openapi_path.write_text(json.dumps(app.openapi(), indent=2) + "\n")

        pkg = service.replace("-", "_") + "_client"
        outdir = ROOT / "clients" / pkg
        if outdir.exists():
            shutil.rmtree(outdir)
        subprocess.run(
            [
                "openapi-python-client",
                "generate",
                "--path",
                str(openapi_path),
                "--meta",
                "none",
                "--output-path",
                str(outdir),
            ],
            check=True,
            cwd=ROOT,
        )
        print(f"generated {openapi_path.relative_to(ROOT)} -> clients/{pkg}")


if __name__ == "__main__":
    main()
