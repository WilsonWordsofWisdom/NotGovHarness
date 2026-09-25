import os

# agent_role/goal/backstory are required, no default (D-087) — a real deployment always sets
# these via env vars at container start; tests stand in with fixed values before import.
os.environ.setdefault("AGENT_ROLE", "Test Agent")
os.environ.setdefault("AGENT_GOAL", "Answer test questions.")
os.environ.setdefault(
    "AGENT_BACKSTORY", "You are a test agent used only by this service's own tests."
)

from agent_runtime.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def test_healthz():
    assert TestClient(app).get("/healthz").json() == {"status": "ok"}
