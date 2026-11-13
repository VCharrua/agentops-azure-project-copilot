"""Deterministic stand-in for the LLM, used for local runs, tests and the offline eval tier.

It is a rule-based planner, not a language model: it routes a request to a tool by keyword and
writes extractive answers from the tool results. That makes every run reproducible and free, and
lets the surrounding system (retrieval, guardrails, approval gate, API, telemetry) be tested for
real. It says nothing about how well an actual model reasons; the live eval tier covers that.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any

from copilot.domain import Message, ModelTurn, ToolCall, Usage
from copilot.retrieval.local import tokenize
from copilot.tools.project_data import ProjectRepository
from copilot.tools.registry import ToolSpec

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")

_TASK = re.compile(r"\b(?:create|add|open|raise|make)\b.{0,40}\btask\b", re.I)
_EMAIL_INTENT = re.compile(r"\b(?:draft|write|prepare|send|compose)\b.{0,40}\be-?mail\b", re.I)
_INCIDENT = re.compile(r"\b(?:log|record|register|file|raise)\b.{0,40}\b(?:incident|near miss|accident)\b", re.I)
_REPORT = re.compile(r"\b(?:status|weekly|progress)\s+report\b", re.I)
_RISK = re.compile(r"\brisks?\b", re.I)
_LIST = re.compile(r"\b(?:list|which|what|show)\b.{0,20}\bprojects\b", re.I)
_DOCUMENT = re.compile(
    r"\b(?:why|caus\w*|reason|explain|agreed?|decid\w*|decision|said|contract|clause|report|meeting|minutes|"
    r"polic\w+|who|happened|damages|retention|payment terms)\b",
    re.I,
)
_VARIANCE = re.compile(r"\b(?:varianc\w*|overruns?|over budget|cost codes?|biggest|largest)\b", re.I)
_BUDGET = re.compile(r"\b(?:budget|forecast|actuals?|spen[dt]|costs?)\b", re.I)
_SCHEDULE = re.compile(r"\b(?:schedule|milestones?|delay\w*|late|on track|programme|completion)\b", re.I)


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class OfflineModel:
    def __init__(self, repo: ProjectRepository) -> None:
        self._repo = repo
        self._counter = 0

    # -- ModelClient -------------------------------------------------------------------------------
    def complete(self, messages: list[Message], tools: list[ToolSpec]) -> ModelTurn:
        user_index = max(i for i, m in enumerate(messages) if m.role == "user")
        question = messages[user_index].content
        tool_messages = [m for m in messages[user_index + 1 :] if m.role == "tool"]
        usage = Usage(input_tokens=sum(_estimate_tokens(m.content) for m in messages))

        if tool_messages:
            text = "\n\n".join(self._compose(question, m) for m in tool_messages)
            usage.output_tokens = _estimate_tokens(text)
            return ModelTurn(text=text, usage=usage)

        history = " ".join(m.content for m in messages[:user_index] if m.role == "user")
        available = {tool.name for tool in tools}
        plan = self._plan(question, history)
        if isinstance(plan, str):
            usage.output_tokens = _estimate_tokens(plan)
            return ModelTurn(text=plan, usage=usage)
        name, arguments = plan
        if name not in available:
            return ModelTurn(text="I do not have a tool for that request.", usage=usage)
        self._counter += 1
        usage.output_tokens = _estimate_tokens(json.dumps(arguments))
        return ModelTurn(
            tool_calls=[ToolCall(call_id=f"call_{self._counter:04d}", name=name, arguments=arguments)],
            usage=usage,
        )

    # -- planning ----------------------------------------------------------------------------------
    def _plan(self, question: str, history: str) -> tuple[str, dict[str, Any]] | str:
        project_id = self._repo.find_project_id(question) or self._repo.find_project_id(history)
        needs_project = "Which project do you mean? I know about: " + ", ".join(
            f"{p['project_id']} ({p['name']})" for p in self._repo.list_projects()
        )

        if _TASK.search(question):
            if not project_id:
                return needs_project
            return "create_task", self._task_arguments(question, project_id)
        if _EMAIL_INTENT.search(question):
            return "draft_email", self._email_arguments(question, project_id)
        if _INCIDENT.search(question):
            if not project_id:
                return needs_project
            severity = next((s for s in ("critical", "high", "medium", "low") if s in question.lower()), "medium")
            return "log_incident", {
                "project_id": project_id,
                "severity": severity,
                "description": question.strip(),
            }
        if _LIST.search(question):
            return "list_projects", {}
        if _REPORT.search(question) and not _DOCUMENT.search(_REPORT.sub("", question)):
            return ("generate_status_report", {"project_id": project_id}) if project_id else needs_project
        if _RISK.search(question):
            return ("detect_risks", {"project_id": project_id}) if project_id else needs_project
        if not _DOCUMENT.search(question):
            if _VARIANCE.search(question):
                if not project_id:
                    return needs_project
                return "get_cost_variances", {"project_id": project_id, "min_variance_pct": 0.0}
            if _BUDGET.search(question):
                return ("get_budget_summary", {"project_id": project_id}) if project_id else needs_project
            if _SCHEDULE.search(question):
                return ("get_schedule_status", {"project_id": project_id}) if project_id else needs_project
        return "search_documents", {"query": question.strip(), "project_id": project_id}

    def _task_arguments(self, question: str, project_id: str) -> dict[str, Any]:
        quoted = re.search(r"[\"'“](.+?)[\"'”]", question)
        after_to = re.search(r"\btask\b(?:\s+for\s+[\w-]+(?:\s+[\w-]+)?)?\s+(?:to|for|about|:)\s+(.+)", question, re.I)
        title = quoted.group(1) if quoted else after_to.group(1) if after_to else question
        title = re.split(r"\b(?:assign(?:ed)? to|due|by \d{4})\b", title, maxsplit=1)[0].strip(" .,:;")
        assignee_match = re.search(r"\bassign(?:ed)?(?: it)? to ([A-Z][\w-]+(?: [A-Z][\w-]+)?)", question)
        due_match = _ISO_DATE.search(question)
        default_due = date.fromisoformat(self._repo.reporting_date) + timedelta(days=7)
        return {
            "project_id": project_id,
            "title": title[:200] or "Follow-up task",
            "assignee": assignee_match.group(1)
            if assignee_match
            else str(self._repo.project(project_id)["project_manager"]),
            "due_date": due_match.group(0) if due_match else default_due.isoformat(),
        }

    def _email_arguments(self, question: str, project_id: str | None) -> dict[str, Any]:
        recipient = _EMAIL.search(question)
        about = re.search(r"\b(?:about|regarding|saying|that says|on)\b\s+(.+)", question, re.I)
        topic = (about.group(1) if about else question).strip(" .")
        project_name = self._repo.project(project_id)["name"] if project_id else "project update"
        return {
            "to": recipient.group(0) if recipient else "project.office@alvora-construction.example",
            "subject": f"{project_name}: {topic[:80]}",
            "body": f"Hello,\n\n{topic}\n\nRegards,\nProject team (draft prepared by the Project Delivery Copilot)",
        }

    # -- answer composition ------------------------------------------------------------------------
    def _compose(self, question: str, message: Message) -> str:
        try:
            output: dict[str, Any] = json.loads(message.content)
        except json.JSONDecodeError:
            return "The tool returned an unreadable result."
        if "error" in output:
            return f"I could not complete that step: {output['error']}"
        composer = getattr(self, f"_compose_{message.tool_name}", None)
        return str(composer(question, output)) if composer else json.dumps(output)

    def _compose_search_documents(self, question: str, output: dict[str, Any]) -> str:
        results: list[dict[str, Any]] = output.get("results", [])
        if not results:
            return "I could not find anything about that in the project documents."
        query_tokens = set(tokenize(question))
        # (score, chunk rank, sentence index, sentence, doc_id) for every sentence of every result
        sentences = [
            (len(query_tokens & set(tokenize(sentence))) - 0.1 * rank, rank, index, sentence.strip(), result["doc_id"])
            for rank, result in enumerate(results)
            for index, sentence in enumerate(_SENTENCE.split(result["content"]))
        ]
        top_score = max(score for score, *_ in sentences)
        if top_score <= 0:
            return "I could not find anything about that in the project documents."
        chosen = sorted((s for s in sentences if s[0] > 0 and s[0] >= 0.4 * top_score), reverse=True)[:3]
        if len(chosen) < 3:
            # Add the sentence that follows the best one: it usually carries the explanation.
            _, rank, index, *_ = chosen[0]
            follow_up = next((s for s in sentences if s[1] == rank and s[2] == index + 1), None)
            if follow_up and follow_up not in chosen:
                chosen.append(follow_up)
        ordered = sorted(chosen, key=lambda s: (s[1], s[2]))
        return "From the project documents: " + " ".join(f"{text} [{doc_id}]" for *_, text, doc_id in ordered)

    @staticmethod
    def _compose_list_projects(_question: str, output: dict[str, Any]) -> str:
        return "Projects:\n" + "\n".join(
            f"- {p['project_id']}: {p['name']} for {p['client']}, contract value {p['contract_value_eur']:,} EUR, "
            f"project manager {p['project_manager']}."
            for p in output["projects"]
        )

    @staticmethod
    def _compose_get_budget_summary(_question: str, output: dict[str, Any]) -> str:
        return (
            f"{output['project_name']} ({output['project_id']}) as of {output['as_of']}: budget "
            f"{output['budget']:,} EUR, actual to date {output['actual_to_date']:,} EUR, committed "
            f"{output['committed']:,} EUR, forecast at completion {output['forecast_at_completion']:,} EUR. "
            f"Forecast variance {output['forecast_variance']:+,} EUR ({output['forecast_variance_pct']:+}%), "
            f"rated {output['rating']}."
        )

    @staticmethod
    def _compose_get_cost_variances(_question: str, output: dict[str, Any]) -> str:
        if not output["lines"]:
            return f"No cost codes on {output['project_id']} meet that variance threshold."
        return (
            f"Cost variances for {output['project_id']} as of {output['as_of']}, largest overrun first:\n"
            + "\n".join(
                f"- {line['description']} ({line['cost_code']}): {line['variance']:+,} EUR "
                f"({line['variance_pct']:+}%), "
                f"rated {line['rating']}."
                for line in output["lines"]
            )
        )

    @staticmethod
    def _compose_get_schedule_status(_question: str, output: dict[str, Any]) -> str:
        return f"Schedule for {output['project_id']} as of {output['as_of']}:\n" + "\n".join(
            f"- {m['milestone']}: baseline {m['baseline_date']}, forecast {m['forecast_date']} "
            f"({m['delay_days']:+} days, {m['status']}, rated {m['rating']})."
            for m in output["milestones"]
        )

    @staticmethod
    def _compose_detect_risks(_question: str, output: dict[str, Any]) -> str:
        if not output["risks"]:
            return f"No risks above threshold were detected for {output['project_id']}."
        return f"Risks detected for {output['project_id']} as of {output['as_of']}:\n" + "\n".join(
            f"- [{risk['severity'].upper()}] ({risk['category']}) {risk['title']}. {risk['evidence']}"
            for risk in output["risks"]
        )

    @staticmethod
    def _compose_generate_status_report(_question: str, output: dict[str, Any]) -> str:
        return str(output["report_markdown"])

    @staticmethod
    def _pending(output: dict[str, Any]) -> str:
        return (
            f"I prepared a {output['tool']} proposal ({output['action_id']}). It has not been executed: "
            "it is waiting for human approval."
        )

    def _compose_create_task(self, _question: str, output: dict[str, Any]) -> str:
        return self._pending(output)

    def _compose_draft_email(self, _question: str, output: dict[str, Any]) -> str:
        return self._pending(output)

    def _compose_log_incident(self, _question: str, output: dict[str, Any]) -> str:
        return self._pending(output)
