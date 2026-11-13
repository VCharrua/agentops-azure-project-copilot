"""The agent loop: guardrails -> model -> tools -> (proposals) -> answer.

Deliberately small and explicit. Every control that matters is enforced here in code rather than in
the prompt:

* input, retrieved-content and output screening;
* tool arguments validated against the tool's schema before anything runs;
* write tools are never executed, only handed to the approval gate as proposals;
* a hard step limit;
* a system-generated footer listing pending approvals, so the user never has to trust the model's
  own description of what it did.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from opentelemetry.trace import Status, StatusCode
from pydantic import ValidationError

from copilot.agent.model import ModelClient
from copilot.agent.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from copilot.approvals.audit import AuditLog
from copilot.approvals.store import ApprovalGate
from copilot.domain import AgentResponse, Citation, Message, PendingAction, ToolCall, ToolTrace, Usage
from copilot.guardrails.base import Guardrail
from copilot.observability.telemetry import guardrail_block_counter, token_counter, tool_call_counter, tracer
from copilot.tools.project_data import UnknownProjectError
from copilot.tools.registry import ToolRegistry, ToolSpec

_CITATION = re.compile(r"\[([A-Z]{3}-[\w-]+)\]")
STEP_LIMIT_MESSAGE = "I could not finish this request within the allowed number of steps. Please narrow it down."
OUTPUT_BLOCKED_MESSAGE = "I can't share that answer because it did not pass the output safety check."


class Orchestrator:
    def __init__(
        self,
        *,
        model: ModelClient,
        tools: ToolRegistry,
        guardrail: Guardrail,
        approvals: ApprovalGate,
        audit: AuditLog,
        max_steps: int = 6,
        allowed_email_domains: list[str] | None = None,
    ) -> None:
        self._model = model
        self._tools = tools
        self._guardrail = guardrail
        self._approvals = approvals
        self._audit = audit
        self._max_steps = max_steps
        self._allowed_email_domains = {d.lower() for d in (allowed_email_domains or [])}

    def run(
        self, user_message: str, *, session_id: str | None = None, history: list[Message] | None = None
    ) -> AgentResponse:
        session_id = session_id or f"ses_{uuid.uuid4().hex[:12]}"
        with tracer.start_as_current_span("agent.run") as span:
            span.set_attribute("copilot.session_id", session_id)
            span.set_attribute("copilot.prompt_version", PROMPT_VERSION)
            response = self._run(user_message, session_id, history or [])
            span.set_attribute("copilot.blocked", response.blocked)
            span.set_attribute("copilot.tool_calls", len(response.tool_calls))
            span.set_attribute("copilot.pending_actions", len(response.pending_actions))
            span.set_attribute("gen_ai.usage.input_tokens", response.usage.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", response.usage.output_tokens)
            return response

    # -- loop --------------------------------------------------------------------------------------
    def _run(self, user_message: str, session_id: str, history: list[Message]) -> AgentResponse:
        with tracer.start_as_current_span("guardrail.input") as span:
            verdict = self._guardrail.check_input(user_message)
            span.set_attribute("copilot.guardrail.allowed", verdict.allowed)
        if not verdict.allowed:
            guardrail_block_counter.add(1, {"stage": "input", "category": verdict.category or "unknown"})
            self._audit.append(
                "input_blocked",
                actor="guardrail",
                data={"session_id": session_id, "category": verdict.category},
            )
            return AgentResponse(
                session_id=session_id,
                answer=f"I can't help with that request. {verdict.reason or ''}".strip(),
                blocked=True,
                block_reason=verdict.category,
            )

        messages = [
            Message(role="system", content=SYSTEM_PROMPT),
            *history,
            Message(role="user", content=user_message),
        ]
        specs = self._tools.specs()
        usage = Usage()
        traces: list[ToolTrace] = []
        pending: list[PendingAction] = []
        sources: dict[str, str] = {}
        answer = STEP_LIMIT_MESSAGE

        for _step in range(self._max_steps):
            with tracer.start_as_current_span("agent.model_call") as span:
                turn = self._model.complete(messages, specs)
                span.set_attribute("gen_ai.operation.name", "chat")
                span.set_attribute("gen_ai.usage.input_tokens", turn.usage.input_tokens)
                span.set_attribute("gen_ai.usage.output_tokens", turn.usage.output_tokens)
            usage.add(turn.usage)
            token_counter.add(turn.usage.input_tokens, {"gen_ai.token.type": "input"})
            token_counter.add(turn.usage.output_tokens, {"gen_ai.token.type": "output"})

            if not turn.tool_calls:
                answer = turn.text
                break
            messages.append(Message(role="assistant", content=turn.text, tool_calls=turn.tool_calls))
            for call in turn.tool_calls:
                output, trace_entry, action = self._dispatch(call, session_id, traces, sources)
                traces.append(trace_entry)
                if action is not None:
                    pending.append(action)
                messages.append(
                    Message(
                        role="tool",
                        content=json.dumps(output),
                        tool_call_id=call.call_id,
                        tool_name=call.name,
                    )
                )

        with tracer.start_as_current_span("guardrail.output") as span:
            out_verdict = self._guardrail.check_output(answer)
            span.set_attribute("copilot.guardrail.allowed", out_verdict.allowed)
        if not out_verdict.allowed:
            guardrail_block_counter.add(1, {"stage": "output", "category": out_verdict.category or "unknown"})
            answer = OUTPUT_BLOCKED_MESSAGE

        withheld_count = len({doc_id for entry in traces for doc_id in entry.withheld_doc_ids})
        if withheld_count:
            answer += (
                f"\n\nSecurity notice: {withheld_count} retrieved document(s) were withheld because they contain "
                "instructions addressed to the assistant. Treat them as suspicious."
            )
        if pending:
            ids = ", ".join(action.id for action in pending)
            answer += (
                f"\n\nPending approval: {len(pending)} proposed action(s) ({ids}). Nothing has been executed. "
                "A human must approve or reject each one in the approvals screen."
            )
        cited = [doc_id for doc_id in dict.fromkeys(_CITATION.findall(answer)) if doc_id in sources]
        return AgentResponse(
            session_id=session_id,
            answer=answer,
            citations=[Citation(doc_id=doc_id, title=sources[doc_id]) for doc_id in cited],
            tool_calls=traces,
            pending_actions=pending,
            blocked=not out_verdict.allowed,
            block_reason=out_verdict.category if not out_verdict.allowed else None,
            usage=usage,
        )

    # -- tools -------------------------------------------------------------------------------------
    def _dispatch(
        self, call: ToolCall, session_id: str, traces: list[ToolTrace], sources: dict[str, str]
    ) -> tuple[dict[str, Any], ToolTrace, PendingAction | None]:
        spec = self._tools.get(call.name)
        with tracer.start_as_current_span("agent.tool_call") as span:
            span.set_attribute("gen_ai.tool.name", call.name)
            if spec is None:
                span.set_status(Status(StatusCode.ERROR, "unknown tool"))
                tool_call_counter.add(1, {"tool": "unknown", "outcome": "error"})
                trace_entry = ToolTrace(name=call.name, arguments=call.arguments, kind="read", outcome="error")
                return {"error": f"unknown tool {call.name}"}, trace_entry, None

            span.set_attribute("copilot.tool.kind", spec.kind)
            trace_entry = ToolTrace(name=spec.name, arguments=call.arguments, kind=spec.kind, outcome="ok")
            action: PendingAction | None = None
            try:
                args = spec.args_model.model_validate(call.arguments)
                if spec.kind == "write":
                    output, action = self._propose(spec, args.model_dump(), session_id, traces)
                    trace_entry.outcome = "pending_approval"
                else:
                    if spec.handler is None:  # pragma: no cover - enforced by ToolRegistry
                        raise RuntimeError("read tool without handler")
                    output = spec.handler(args)
                    if spec.name == "search_documents":
                        output = self._screen_documents(output, trace_entry, sources)
            except ValidationError as exc:
                problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
                output, trace_entry.outcome = {"error": f"invalid arguments: {problems}"}, "error"
            except UnknownProjectError as exc:
                output, trace_entry.outcome = {"error": f"unknown project {exc.args[0]}"}, "error"
            except PermissionError as exc:
                output, trace_entry.outcome = {"error": str(exc)}, "error"
            except Exception as exc:
                span.record_exception(exc)
                output, trace_entry.outcome = {"error": f"tool failed ({type(exc).__name__})"}, "error"

            span.set_attribute("copilot.tool.outcome", trace_entry.outcome)
            if trace_entry.outcome == "error":
                span.set_status(Status(StatusCode.ERROR))
            tool_call_counter.add(1, {"tool": spec.name, "outcome": trace_entry.outcome})
            return output, trace_entry, action

    def _propose(
        self, spec: ToolSpec, arguments: dict[str, Any], session_id: str, traces: list[ToolTrace]
    ) -> tuple[dict[str, Any], PendingAction]:
        if spec.name == "draft_email":
            domain = str(arguments["to"]).rpartition("@")[2]
            if domain not in self._allowed_email_domains:
                self._audit.append(
                    "action_refused",
                    actor="policy",
                    data={
                        "session_id": session_id,
                        "tool": spec.name,
                        "reason": "recipient domain not allowed",
                    },
                )
                raise PermissionError(
                    "recipient domain is not on the allow-list; email drafts can only be addressed internally"
                )
        risk_flags: list[str] = []
        if any(trace_entry.withheld_doc_ids for trace_entry in traces):
            risk_flags.append("suspicious_content_seen_in_session")
        if any(trace_entry.retrieved_doc_ids for trace_entry in traces):
            risk_flags.append("proposed_after_reading_documents")
        action = self._approvals.propose(
            tool=spec.name, arguments=arguments, session_id=session_id, risk_flags=risk_flags
        )
        output = {
            "status": "pending_approval",
            "tool": spec.name,
            "action_id": action.id,
            "note": "NOT executed. A human must approve this proposal before anything happens.",
        }
        return output, action

    def _screen_documents(
        self, output: dict[str, Any], trace_entry: ToolTrace, sources: dict[str, str]
    ) -> dict[str, Any]:
        """Drop retrieved chunks that look like indirect prompt injection before the model sees them."""
        results: list[dict[str, Any]] = output["results"]
        with tracer.start_as_current_span("guardrail.documents") as span:
            verdicts = self._guardrail.check_documents([r["content"] for r in results])
            # One flagged chunk taints its whole document: the rest of it is not trusted either.
            flagged = {r["doc_id"] for r, v in zip(results, verdicts, strict=True) if not v.allowed}
            kept = [r for r in results if r["doc_id"] not in flagged]
            withheld = sorted(flagged)
            span.set_attribute("copilot.retrieval.returned", len(results))
            span.set_attribute("copilot.retrieval.withheld", len(withheld))
        if withheld:
            guardrail_block_counter.add(len(withheld), {"stage": "documents", "category": "indirect_prompt_injection"})
            self._audit.append("documents_withheld", actor="guardrail", data={"doc_ids": withheld})
        trace_entry.retrieved_doc_ids = list(dict.fromkeys(r["doc_id"] for r in kept))
        trace_entry.withheld_doc_ids = list(dict.fromkeys(withheld))
        for result in kept:
            sources[result["doc_id"]] = result["title"]
        return {**output, "results": kept, "withheld": len(withheld)}
