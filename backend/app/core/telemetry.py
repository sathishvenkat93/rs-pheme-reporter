"""OpenTelemetry tracing: API → agent → tools → DuckDB/Postgres, plus LLM spans with tokens and cost.

Exporter by ``PHEME_OTEL_EXPORTER`` (none | console | otlp). Langfuse ingests OTLP, so setting
``PHEME_LANGFUSE_HOST`` and its keys adds a second exporter pointed at Langfuse.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SpanExporter
from sqlalchemy import Engine, event

from app.core.config import get_settings

# $ per million tokens (input, output, cache read). Unknown models record tokens but no cost.
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
}

_provider: TracerProvider | None = None


def provider() -> TracerProvider:
    global _provider
    if _provider is None:
        _provider = TracerProvider(resource=Resource.create({"service.name": "pheme-api"}))
        for exporter in _exporters():
            _provider.add_span_processor(BatchSpanProcessor(exporter))
    return _provider


def _exporters() -> list[SpanExporter]:
    s = get_settings()
    out: list[SpanExporter] = []
    if s.otel_exporter == "console":
        out.append(ConsoleSpanExporter())
    elif s.otel_exporter == "otlp":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        out.append(OTLPSpanExporter(endpoint=s.otel_endpoint))
    if s.langfuse_host and s.langfuse_public_key and s.langfuse_secret_key:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        auth = base64.b64encode(
            f"{s.langfuse_public_key}:{s.langfuse_secret_key}".encode()
        ).decode()
        out.append(OTLPSpanExporter(endpoint=f"{s.langfuse_host.rstrip('/')}/api/public/otel/v1/traces",
                                    headers={"Authorization": f"Basic {auth}"}))  # fmt: skip
    return out


def add_processor(processor: SpanProcessor) -> None:
    """Extra processor (tests use an in-memory exporter)."""
    provider().add_span_processor(processor)


def instrument(app: FastAPI) -> None:
    """HTTP spans for the app (SQL spans are added per engine by ``instrument_engine``)."""
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider(), excluded_urls="health")


def instrument_engine(engine: Engine) -> None:
    """SQL spans for an engine; called by ``core.db.get_engine`` when it creates one.

    Done with SQLAlchemy events: the OpenTelemetry SQLAlchemy instrumentation doesn't support
    SQLAlchemy 2.1 yet (it silently instruments nothing).
    """

    @event.listens_for(engine, "before_cursor_execute")
    def _start(
        conn: Any, cursor: Any, statement: str, params: Any, context: Any, executemany: bool
    ) -> None:
        name = statement.strip().split(None, 1)[0].upper() if statement.strip() else "SQL"
        span_ = tracer().start_span(f"postgres.{name.lower()}", attributes={
            "db.system": "postgresql", "db.statement": statement[:2000],
        })  # fmt: skip
        conn.info.setdefault("_otel_spans", []).append(span_)

    @event.listens_for(engine, "after_cursor_execute")
    def _end(
        conn: Any, cursor: Any, statement: str, params: Any, context: Any, executemany: bool
    ) -> None:
        spans = conn.info.get("_otel_spans")
        if spans:
            spans.pop().end()

    @event.listens_for(engine, "handle_error")
    def _error(ctx: Any) -> None:
        spans = ctx.connection.info.get("_otel_spans") if ctx.connection is not None else None
        if spans:
            s = spans.pop()
            s.record_exception(ctx.original_exception)
            s.end()


def tracer() -> trace.Tracer:
    return provider().get_tracer("pheme")


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[trace.Span]:
    with tracer().start_as_current_span(name) as s:
        for k, v in attributes.items():
            if v is not None:
                s.set_attribute(k, v if isinstance(v, str | int | float | bool) else str(v))
        yield s


def llm_cost(
    model: str, input_tokens: int, output_tokens: int, cache_read: int = 0
) -> float | None:
    price = next((p for m, p in PRICES.items() if model.startswith(m)), None)
    if price is None:
        return None
    p_in, p_out, p_cache = price
    return round((input_tokens * p_in + output_tokens * p_out + cache_read * p_cache) / 1e6, 6)


def current_ids() -> tuple[str | None, str | None]:
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return None, None
    return f"{ctx.trace_id:032x}", f"{ctx.span_id:016x}"
