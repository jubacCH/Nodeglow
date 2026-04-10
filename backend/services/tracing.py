"""OpenTelemetry tracing setup.

By default Nodeglow ships with the no-op tracer provider — calling
`tracer.start_as_current_span(...)` has near-zero overhead and produces no
output. To enable real tracing, set the standard OTel env vars in your
deployment:

    OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4318
    OTEL_SERVICE_NAME=nodeglow             # optional, default: nodeglow

Once configured, `init_tracing()` (called from main.py lifespan) wires up:
- An OTLP HTTP span exporter to the configured endpoint
- Auto-instrumentation for SQLAlchemy and httpx
- FastAPI auto-instrumentation (applied separately via `instrument_app(app)`
  because it needs the running app instance)

Manual spans live in three places:
- `services.metrics.instrument_job` — every scheduler job is also a span
- `services.clickhouse_client` — query/insert calls are spans
- `services.integration` / `scheduler.run_integration_checks` — per-poll spans
"""
from __future__ import annotations

import logging
import os

from opentelemetry import trace

log = logging.getLogger("nodeglow.tracing")

# `tracer` is always usable. Without init_tracing() it returns no-op spans
# from the global default provider.
tracer = trace.get_tracer("nodeglow")

_initialized = False


def init_tracing() -> None:
    """Initialise the OTel SDK if an OTLP endpoint is configured.

    Idempotent: safe to call multiple times. If OTEL_EXPORTER_OTLP_ENDPOINT
    is unset, this is a no-op and `tracer.start_as_current_span(...)` will
    quietly produce no spans.
    """
    global _initialized
    if _initialized:
        return
    _initialized = True

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        log.info("OpenTelemetry: no OTLP endpoint configured — spans will be no-ops")
        return

    try:
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        service_name = os.environ.get("OTEL_SERVICE_NAME", "nodeglow")
        resource = Resource.create({SERVICE_NAME: service_name})
        provider = TracerProvider(resource=resource)
        # OTel collectors expect /v1/traces on the OTLP HTTP endpoint
        traces_url = endpoint.rstrip("/") + "/v1/traces"
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=traces_url))
        )
        trace.set_tracer_provider(provider)

        # Auto-instrumentation. SQLAlchemy needs the engine; we pass the
        # async engine's sync wrapper because the instrumentor hooks the
        # underlying DBAPI.
        try:
            from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
            from database import engine as _async_engine
            SQLAlchemyInstrumentor().instrument(
                engine=_async_engine.sync_engine,
                enable_commenter=False,
            )
        except Exception as exc:
            log.warning("SQLAlchemy instrumentation failed: %s", exc)

        try:
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
            HTTPXClientInstrumentor().instrument()
        except Exception as exc:
            log.warning("httpx instrumentation failed: %s", exc)

        log.info("OpenTelemetry initialised: service=%s, endpoint=%s",
                 service_name, traces_url)
    except Exception as exc:
        log.warning("OpenTelemetry init failed: %s", exc)


def instrument_app(app) -> None:
    """Wire FastAPI auto-instrumentation onto the running app instance.

    Called from main.py after app construction. No-op if init_tracing() did
    not actually initialise the SDK.
    """
    if not _initialized:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor().instrument_app(app)
        log.info("FastAPI instrumentation enabled")
    except Exception as exc:
        log.warning("FastAPI instrumentation failed: %s", exc)
