"""OpenTelemetry setup and PII-safe logging.

Spans are created through the OpenTelemetry API everywhere, so instrumentation costs nothing when
no exporter is configured (local runs, tests). When ``APPLICATIONINSIGHTS_CONNECTION_STRING`` is
present, the Azure Monitor distro exports traces, metrics and logs to Application Insights.

Span attributes follow the OpenTelemetry GenAI semantic conventions where one exists
(``gen_ai.usage.input_tokens`` etc.). Prompt and completion *content* is never put on spans.
"""

from __future__ import annotations

import logging
import os

from opentelemetry import metrics, trace

from copilot.guardrails.pii import redact

TRACER_NAME = "copilot"
tracer = trace.get_tracer(TRACER_NAME)
_meter = metrics.get_meter(TRACER_NAME)

token_counter = _meter.create_counter("gen_ai.client.token.usage", unit="{token}", description="Model tokens used")
tool_call_counter = _meter.create_counter("copilot.tool.calls", unit="{call}", description="Tool calls by outcome")
guardrail_block_counter = _meter.create_counter(
    "copilot.guardrail.blocks", unit="{block}", description="Requests or documents blocked by guardrails"
)


class PiiRedactionFilter(logging.Filter):
    """Redact email addresses, phone numbers and IBANs from every log record before it is emitted."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        return True


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(handler)
    for existing in root.handlers:
        if not any(isinstance(f, PiiRedactionFilter) for f in existing.filters):
            existing.addFilter(PiiRedactionFilter())


def configure_telemetry() -> bool:
    """Enable Azure Monitor export when a connection string is configured. Returns True if enabled."""
    configure_logging()
    if not os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        return False
    from azure.monitor.opentelemetry import configure_azure_monitor

    configure_azure_monitor(logger_name="copilot")
    # The Azure Monitor log handler is attached to the "copilot" logger: redact there as well.
    for handler in logging.getLogger("copilot").handlers:
        handler.addFilter(PiiRedactionFilter())
    return True
