"""The offline planner and the Foundry Responses adapter."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from copilot.agent.foundry_model import FoundryResponsesModel, to_input_items
from copilot.agent.offline_model import OfflineModel
from copilot.bootstrap import Container
from copilot.domain import Message, ToolCall
from copilot.tools.project_data import ProjectRepository


def plan(repo: ProjectRepository, container: Container, question: str, history: list[Message] | None = None) -> Any:
    turn = OfflineModel(repo).complete(
        [*(history or []), Message(role="user", content=question)], container.tools.specs()
    )
    return (turn.tool_calls[0].name, turn.tool_calls[0].arguments) if turn.tool_calls else turn.text


@pytest.mark.parametrize(
    ("question", "tool"),
    [
        ("What is the budget status of PRJ-001?", "get_budget_summary"),
        ("Which cost codes have the biggest overruns on Riverside?", "get_cost_variances"),
        ("Is the Harbor warehouse on track? Show the milestones", "get_schedule_status"),
        ("What are the main risks on PRJ-003?", "detect_risks"),
        ("Prepare the weekly status report for PRJ-002", "generate_status_report"),
        ("Which projects do we have?", "list_projects"),
        ("Why is the concrete structure over budget on PRJ-001?", "search_documents"),
        ("What did the client decide in the Riverside progress meeting?", "search_documents"),
        ("Log a high severity incident on PRJ-001: scaffold tie missing on east elevation", "log_incident"),
        (
            "Draft an email to marta.reis@alvora-construction.example about the crane delay on PRJ-001",
            "draft_email",
        ),
    ],
)
def test_offline_model_routes_to_the_expected_tool(
    repo: ProjectRepository, container: Container, question: str, tool: str
) -> None:
    assert plan(repo, container, question)[0] == tool


def test_offline_model_extracts_task_arguments(repo: ProjectRepository, container: Container) -> None:
    name, args = plan(
        repo,
        container,
        "Create a task for PRJ-002 to install pedestrian barriers at Gate B, assign to Ines Carvalho, due 2026-10-02",
    )
    assert name == "create_task"
    assert args == {
        "project_id": "PRJ-002",
        "title": "install pedestrian barriers at Gate B",
        "assignee": "Ines Carvalho",
        "due_date": "2026-10-02",
    }
    _, defaults = plan(repo, container, "Add a task on the school project: 'Chase boiler delivery'")
    assert defaults["title"] == "Chase boiler delivery"
    assert defaults["assignee"] == "Sofia Almeida"
    assert defaults["due_date"] == "2026-10-02"  # reporting date + 7 days


def test_offline_model_asks_for_the_project_when_ambiguous(repo: ProjectRepository, container: Container) -> None:
    for question in (
        "What is the budget?",
        "Create a task to chase the supplier",
        "Any risks?",
        "Log an incident",
    ):
        answer = plan(repo, container, question)
        assert isinstance(answer, str)
        assert "Which project do you mean?" in answer


def test_offline_model_uses_conversation_history_for_the_project(repo: ProjectRepository, container: Container) -> None:
    history = [
        Message(role="user", content="Let's talk about PRJ-003"),
        Message(role="assistant", content="Sure."),
    ]
    assert plan(repo, container, "What is the budget?", history) == (
        "get_budget_summary",
        {"project_id": "PRJ-003"},
    )


def test_offline_model_only_uses_offered_tools(repo: ProjectRepository) -> None:
    turn = OfflineModel(repo).complete([Message(role="user", content="budget of PRJ-001")], [])
    assert turn.tool_calls == []
    assert "do not have a tool" in turn.text


@pytest.mark.parametrize(
    "question",
    [
        "What is the budget status of PRJ-001?",
        "Which cost codes have the biggest overruns on Riverside?",
        "Show the milestones for PRJ-002",
        "What are the main risks on PRJ-003?",
        "Prepare the weekly status report for PRJ-002",
        "Which projects do we have?",
        "What are the liquidated damages on the Harbor warehouse contract?",
        "Log a medium incident on PRJ-001: scaffold tie missing on east elevation",
    ],
)
def test_offline_model_composes_an_answer_for_every_tool(container: Container, question: str) -> None:
    response = container.orchestrator.run(question)
    assert response.tool_calls
    assert response.tool_calls[0].outcome in ("ok", "pending_approval")
    assert len(response.answer) > 40
    assert not response.answer.startswith("{")
    assert response.usage.input_tokens > 0


def test_offline_model_composition_edge_cases(repo: ProjectRepository, container: Container) -> None:
    assert "could not find anything" in container.orchestrator.run("zebra xylophone quasar").answer
    assert "No cost codes" in OfflineModel._compose_get_cost_variances("", {"project_id": "PRJ-002", "lines": []})
    assert "No risks" in OfflineModel._compose_detect_risks("", {"project_id": "PRJ-002", "risks": []})
    model = OfflineModel(repo)
    assert "unreadable" in model._compose("q", Message(role="tool", content="not json", tool_name="x"))
    assert "could not complete" in model._compose("q", Message(role="tool", content='{"error": "nope"}', tool_name="x"))
    assert model._compose("q", Message(role="tool", content='{"a": 1}', tool_name="unknown_tool")) == '{"a": 1}'
    no_overlap = {"results": [{"content": "Unrelated sentence.", "doc_id": "D"}], "withheld": 0}
    assert "could not find anything" in model._compose_search_documents("zebra", no_overlap)


# -- Foundry Responses adapter, against a fake OpenAI client ----------------------------------------


class FakeResponses:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.request: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.request = kwargs
        return self.response


class FakeOpenAI:
    def __init__(self, response: Any) -> None:
        self.responses = FakeResponses(response)


CONVERSATION = [
    Message(role="system", content="instructions"),
    Message(role="user", content="budget for PRJ-001?"),
    Message(
        role="assistant",
        tool_calls=[ToolCall(call_id="c1", name="get_budget_summary", arguments={"project_id": "PRJ-001"})],
    ),
    Message(role="tool", content='{"budget": 1}', tool_call_id="c1", tool_name="get_budget_summary"),
]


def test_to_input_items_uses_the_responses_api_shapes() -> None:
    assert to_input_items(CONVERSATION) == [
        {"role": "user", "content": "budget for PRJ-001?"},
        {
            "type": "function_call",
            "call_id": "c1",
            "name": "get_budget_summary",
            "arguments": '{"project_id": "PRJ-001"}',
        },
        {"type": "function_call_output", "call_id": "c1", "output": '{"budget": 1}'},
    ]


def test_foundry_model_inline_mode_sends_definition_and_parses_text(container: Container) -> None:
    response = SimpleNamespace(
        output=[SimpleNamespace(type="message")],
        output_text="Budget is 18.4M EUR.",
        usage=SimpleNamespace(input_tokens=120, output_tokens=9),
    )
    client = FakeOpenAI(response)
    model = FoundryResponsesModel(client, deployment="gpt-4.1-mini", instructions="be helpful")

    turn = model.complete(CONVERSATION, container.tools.specs())

    request = client.responses.request
    assert request["model"] == "gpt-4.1-mini"
    assert request["instructions"] == "be helpful"
    assert request["store"] is False
    assert {tool["name"] for tool in request["tools"]} >= {"search_documents", "create_task"}
    assert turn.text == "Budget is 18.4M EUR."
    assert (turn.usage.input_tokens, turn.usage.output_tokens) == (120, 9)


def test_foundry_model_agent_bound_mode_omits_the_definition(container: Container) -> None:
    client = FakeOpenAI(SimpleNamespace(output=[], output_text="hi", usage=None))
    model = FoundryResponsesModel(client, deployment="d", instructions="i", agent_bound=True)
    turn = model.complete(CONVERSATION, container.tools.specs())
    assert set(client.responses.request) == {"input", "store"}
    assert turn.usage.input_tokens == 0


def test_foundry_model_parses_function_calls_and_survives_bad_json(container: Container) -> None:
    response = SimpleNamespace(
        output=[
            SimpleNamespace(
                type="function_call",
                call_id="c9",
                name="detect_risks",
                arguments=json.dumps({"project_id": "PRJ-002"}),
            ),
            SimpleNamespace(type="function_call", call_id="c10", name="create_task", arguments="{not json"),
            SimpleNamespace(type="function_call", call_id="c11", name="list_projects", arguments="[1]"),
        ],
        output_text="",
        usage=SimpleNamespace(input_tokens=5, output_tokens=5),
    )
    turn = FoundryResponsesModel(FakeOpenAI(response), deployment="d", instructions="i").complete(
        CONVERSATION, container.tools.specs()
    )
    assert [(c.call_id, c.name, c.arguments) for c in turn.tool_calls] == [
        ("c9", "detect_risks", {"project_id": "PRJ-002"}),
        ("c10", "create_task", {}),
        ("c11", "list_projects", {}),
    ]
    assert turn.text == ""
