import json
import logging

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.core.config import settings
from app.core.safety import redact


class JSONFormatter(logging.Formatter):
    def format(self, record):
        context = trace.get_current_span().get_span_context()
        return json.dumps(
            redact(
                {
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                    "trace_id": f"{context.trace_id:032x}",
                    "correlation_id": getattr(record, "correlation_id", None),
                }
            )
        )


def configure_telemetry(service):
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logging.getLogger().handlers = [handler]
    logging.getLogger().setLevel(logging.INFO)
    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    endpoint = settings().otel_exporter_otlp_endpoint
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint + "/v1/traces", timeout=3))
        )
    trace.set_tracer_provider(provider)
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    HTTPXClientInstrumentor().instrument()
