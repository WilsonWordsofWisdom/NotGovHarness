"""OpenTelemetry tracing setup.

Sets a global TracerProvider once, instruments outbound httpx once, and instruments each
FastAPI app. The OTLP exporter is only wired when an endpoint is configured, so unit tests run
without any collector while still producing valid spans (and therefore trace ids).
"""

from __future__ import annotations

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from .config import PlatformSettings

_provider_configured = False


def configure_tracing(settings: PlatformSettings, app: FastAPI) -> None:
    """Ensure a global provider exists and instrument this app's request handling."""
    global _provider_configured
    if not _provider_configured:
        provider = TracerProvider(resource=Resource.create({"service.name": settings.service_name}))
        if settings.otel_exporter_otlp_endpoint:
            # Imported lazily so the gRPC exporter is only required when actually exporting.
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint))
            )
        trace.set_tracer_provider(provider)
        HTTPXClientInstrumentor().instrument()
        _provider_configured = True

    FastAPIInstrumentor.instrument_app(app)
