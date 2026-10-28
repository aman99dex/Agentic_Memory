"""Traces and metrics (ADR 0018). Free when unused: without an exporter the API calls do nothing.

Set OTEL_EXPORTER_OTLP_ENDPOINT (e.g. http://localhost:4318) and the service exports spans and
metrics over OTLP/HTTP. Spans cover the whole ingest, each write-path graph node, and each read
step. Counters: `llm.tokens` (by role and direction) and `llm.cost` in USD, which needs the model
prices in PRICE_INPUT_PER_MTOK / PRICE_OUTPUT_PER_MTOK (USD per million tokens; unset = no cost).
No message content ever goes into a span or a metric.
"""

import functools
import os
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from opentelemetry import metrics, trace

tracer = trace.get_tracer("agentic_memory")
_meter = metrics.get_meter("agentic_memory")
_tokens = _meter.create_counter("llm.tokens", unit="{token}", description="LLM tokens used")
_cost = _meter.create_counter("llm.cost", unit="USD", description="LLM spend at the set prices")
_started = False


def init_telemetry(service_name: str = "agentic-memory") -> None:
    """Start exporting if an OTLP endpoint is configured. Safe to call more than once."""
    global _started
    if _started or not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    _started = True
    # Imported here so a service that never exports doesn't pay for the SDK at import time.
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({SERVICE_NAME: service_name})
    spans = TracerProvider(resource=resource)
    spans.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(spans)
    reader = PeriodicExportingMetricReader(OTLPMetricExporter())
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))


def traced[**P, R](name: str, fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Wrap an async graph node so its run is one span.

    ParamSpec keeps the node's signature (LangGraph wants a parameter named `state`).
    """

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with tracer.start_as_current_span(name):
            return await fn(*args, **kwargs)

    return wrapper


class UsageCallback(BaseCallbackHandler):
    """Counts the tokens of every call a model makes, tagged by the role it plays."""

    def __init__(self, role: str, model: str) -> None:
        self._attrs = {"role": role, "model": model}

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        for generations in response.generations:
            for g in generations:
                usage = getattr(getattr(g, "message", None), "usage_metadata", None)
                if usage:
                    self._record(int(usage["input_tokens"]), int(usage["output_tokens"]))

    def _record(self, tokens_in: int, tokens_out: int) -> None:
        _tokens.add(tokens_in, {**self._attrs, "direction": "input"})
        _tokens.add(tokens_out, {**self._attrs, "direction": "output"})
        price_in = os.environ.get("PRICE_INPUT_PER_MTOK")
        price_out = os.environ.get("PRICE_OUTPUT_PER_MTOK")
        if price_in and price_out:
            _cost.add(
                (tokens_in * float(price_in) + tokens_out * float(price_out)) / 1_000_000,
                self._attrs,
            )
