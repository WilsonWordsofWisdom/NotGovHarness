"""Request/trace context helpers shared across the service kit.

Kept dependency-light (only ``opentelemetry.trace``) so both ``logging`` and ``errors`` can
import it without pulling in the FastAPI/OTel-instrumentation modules.
"""

from __future__ import annotations

from opentelemetry import trace


def current_trace_id() -> str | None:
    """Return the active span's trace id as 32-char hex, or ``None`` if there is no valid span."""
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx is not None and ctx.is_valid:
        return format(ctx.trace_id, "032x")
    return None
