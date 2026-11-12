from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from copilot.bootstrap import Container
from copilot.config import Settings
from copilot.observability.telemetry import PiiRedactionFilter, configure_logging, configure_telemetry

_exporter = InMemorySpanExporter()


@pytest.fixture(scope="module", autouse=True)
def _tracing() -> None:
    # The global tracer provider can only be set once per process, so it is installed for the module.
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_exporter))
    trace.set_tracer_provider(provider)


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    _exporter.clear()
    yield _exporter
    _exporter.clear()


def test_agent_run_emits_spans_with_token_usage_and_tool_names(
    container: Container, spans: InMemorySpanExporter
) -> None:
    container.orchestrator.run("What is the budget status of PRJ-001?", session_id="ses_test")
    finished = {span.name: span for span in spans.get_finished_spans()}

    assert {"agent.run", "agent.model_call", "agent.tool_call", "guardrail.input", "guardrail.output"} <= set(finished)
    run = finished["agent.run"].attributes or {}
    assert run["copilot.session_id"] == "ses_test"
    assert run["copilot.tool_calls"] == 1
    assert int(str(run["gen_ai.usage.input_tokens"])) > 0
    tool = finished["agent.tool_call"].attributes or {}
    assert tool["gen_ai.tool.name"] == "get_budget_summary"
    assert tool["copilot.tool.outcome"] == "ok"
    parent = finished["agent.tool_call"].parent
    assert parent is not None
    assert parent.span_id == finished["agent.run"].context.span_id


def test_spans_never_carry_prompt_or_document_content(container: Container, spans: InMemorySpanExporter) -> None:
    secret = "marta.reis@alvora-construction.example"
    container.orchestrator.run(f"Draft an email to {secret} about the crane delay on PRJ-001")
    container.orchestrator.run("What does the Riverside contract say about liquidated damages?")
    for span in spans.get_finished_spans():
        for value in (span.attributes or {}).values():
            assert secret not in str(value)
            assert "Liquidated damages" not in str(value)


def test_log_filter_redacts_pii(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("copilot.test")
    caplog.handler.addFilter(PiiRedactionFilter())
    with caplog.at_level(logging.INFO, logger="copilot.test"):
        logger.info("contact %s on %s", "ana@example.org", "+351 912 345 678")
    assert caplog.records[0].getMessage() == "contact [REDACTED_EMAIL] on [REDACTED_PHONE]"


def test_configure_telemetry_is_a_no_op_without_connection_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    assert configure_telemetry() is False
    configure_logging()
    handlers = logging.getLogger().handlers
    assert handlers
    assert all(any(isinstance(f, PiiRedactionFilter) for f in h.filters) for h in handlers)


def test_settings_require_reports_the_missing_variable() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    with pytest.raises(RuntimeError, match="COPILOT_SEARCH_ENDPOINT"):
        settings.require("search_endpoint")
    assert settings.require("search_index") == "project-docs"
