"""Observability harness: confirm the otel-collector's Langfuse fan-out actually works.

No application code changes back this harness (see the design spec) — importing the app is
enough to trigger `configure_tracing` (module-level `app = build_app()` in `main.py`), which wires
the real OTLP exporter when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (`.env.example` default:
http://localhost:4317). A uniquely-named span is emitted directly, force-flushed past the
collector's batching, and polled for via Langfuse's Observations API v2 — proving collector ->
Langfuse ingestion, not just collector -> Jaeger (already covered by Phase 0).
"""

from __future__ import annotations

from uuid import uuid4

from example_service.main import app  # noqa: F401 - import triggers configure_tracing
from opentelemetry import trace


async def test_span_reaches_langfuse(platform_langfuse):
    span_name = f"observability-harness-probe-{uuid4().hex}"
    tracer = trace.get_tracer("test_observability")
    with tracer.start_as_current_span(span_name):
        pass
    trace.get_tracer_provider().force_flush()  # type: ignore[attr-defined]

    observation = await platform_langfuse.wait_for_observation(span_name)
    assert observation is not None, f"span {span_name!r} never reached Langfuse"
    assert observation["name"] == span_name
