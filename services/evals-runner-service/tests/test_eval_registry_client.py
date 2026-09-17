"""Infra-free: EvalRegistryClient's request shape and kind-rejection behavior against a stub
Eval Registry server — same ThreadingHTTPServer pattern as agent-builder-service's
test_agent_gateway_tool.py.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from evals_runner_service.eval_registry_client import EvalRegistryClient, EvalRegistryError

SUITE_CASES = {
    "id": 1,
    "name": "demo",
    "version": "0.1.0",
    "description": "x",
    "kind": "cases",
    "applies_to": ["demo"],
    "metrics": [{"engine": "deepeval", "metric_id": "g_eval", "params": {}}],
    "redteam_config": None,
    "case_count": 2,
    "scan_findings": [],
    "published_by": "example-service",
    "created_at": "2026-09-18T00:00:00+00:00",
}

SUITE_REDTEAM = {**SUITE_CASES, "kind": "redteam", "metrics": None}

DATASET = '{"input": "a", "expected_output": "b"}\n{"input": "c", "context": ["d"]}\n'


@pytest.fixture
def stub_eval_registry():
    state = {"paths": [], "suite": SUITE_CASES}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state["paths"].append(self.path)
            if self.path.endswith("/dataset"):
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                self.wfile.write(DATASET.encode())
                return
            if self.path == "/suites/missing":
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(state["suite"]).encode())

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", state
    server.shutdown()


async def test_fetch_returns_parsed_suite_and_goldens(stub_eval_registry):
    base_url, state = stub_eval_registry

    result = await EvalRegistryClient(base_url).fetch("demo")

    assert result.name == "demo"
    assert result.version == "0.1.0"
    assert result.metrics == SUITE_CASES["metrics"]
    assert len(result.goldens) == 2
    assert result.goldens[0].input == "a"
    assert result.goldens[0].expected_output == "b"
    assert result.goldens[1].context == ["d"]
    assert "/suites/demo" in state["paths"]
    assert "/suites/demo/0.1.0/dataset" in state["paths"]


async def test_fetch_rejects_a_redteam_suite(stub_eval_registry):
    base_url, state = stub_eval_registry
    state["suite"] = SUITE_REDTEAM

    with pytest.raises(EvalRegistryError, match="only 'cases' suites are runnable"):
        await EvalRegistryClient(base_url).fetch("demo")


async def test_fetch_raises_on_a_missing_suite(stub_eval_registry):
    base_url, _state = stub_eval_registry

    with pytest.raises(EvalRegistryError, match="no suite published"):
        await EvalRegistryClient(base_url).fetch("missing")
