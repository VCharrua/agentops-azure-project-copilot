from __future__ import annotations

import pytest
from pydantic import ValidationError

from copilot.bootstrap import Container
from copilot.tools.project_data import ProjectRepository, UnknownProjectError
from copilot.tools.registry import (
    CreateTaskArgs,
    DraftEmailArgs,
    EmptyArgs,
    ProjectArgs,
    SearchDocumentsArgs,
    ToolRegistry,
    ToolSpec,
)


def test_budget_summary_adds_up(repo: ProjectRepository) -> None:
    summary = repo.budget_summary("PRJ-001")
    assert summary["budget"] == 18_400_000
    assert summary["forecast_at_completion"] == 19_347_000
    assert summary["forecast_variance"] == 947_000
    assert summary["forecast_variance_pct"] == 5.1
    assert summary["rating"] == "amber"
    assert repo.budget_summary("PRJ-002")["rating"] == "green"


def test_budget_matches_contract_value_for_every_project(repo: ProjectRepository) -> None:
    for project in repo.list_projects():
        assert repo.budget_summary(project["project_id"])["budget"] == project["contract_value_eur"]


def test_cost_variances_are_sorted_and_filtered(repo: ProjectRepository) -> None:
    lines = repo.cost_variances("PRJ-001", 10)["lines"]
    assert [line["cost_code"] for line in lines] == ["06-PRE", "02-STR"]
    assert [line["rating"] for line in lines] == ["red", "red"]
    everything = repo.cost_variances("PRJ-001")["lines"]
    assert len(everything) == 6
    assert everything == sorted(everything, key=lambda line: -line["variance_pct"])


def test_schedule_status_computes_delay(repo: ProjectRepository) -> None:
    milestones = {m["milestone"]: m for m in repo.schedule_status("PRJ-001")["milestones"]}
    assert milestones["Level 9 slab poured"]["delay_days"] == 21
    assert milestones["Level 9 slab poured"]["rating"] == "red"
    assert milestones["Foundations complete"]["rating"] == "green"


def test_detect_risks_covers_cost_schedule_and_hse(repo: ProjectRepository) -> None:
    risks = repo.detect_risks("PRJ-001")["risks"]
    assert risks[0]["severity"] == "red"
    assert {r["category"] for r in risks} == {"cost", "schedule"}
    assert any("Concrete structure" in r["title"] for r in risks)

    harbor = repo.detect_risks("PRJ-002")["risks"]
    assert {r["category"] for r in harbor} == {"schedule", "hse"}
    assert any("INC-0041" in r["title"] for r in harbor)


def test_status_report_follows_the_company_standard(repo: ProjectRepository) -> None:
    report = repo.status_report("PRJ-003")
    assert report["is_draft"] is True
    assert report["overall_rating"] == "red"
    for section in ("## Summary", "## Cost", "## Schedule", "## Risks", "## Decisions needed"):
        assert section in report["report_markdown"]
    assert "DRAFT" in report["report_markdown"]


def test_unknown_project_raises(repo: ProjectRepository) -> None:
    for call in (
        repo.project,
        repo.budget_summary,
        repo.cost_variances,
        repo.schedule_status,
        repo.detect_risks,
    ):
        with pytest.raises(UnknownProjectError):
            call("PRJ-999")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("status of prj-002 please", "PRJ-002"),
        ("How is the Riverside tower doing?", "PRJ-001"),
        ("the school renovation budget", "PRJ-003"),
        ("what is the weather", None),
    ],
)
def test_find_project_id(repo: ProjectRepository, text: str, expected: str | None) -> None:
    assert repo.find_project_id(text) == expected


def test_argument_models_reject_bad_input() -> None:
    with pytest.raises(ValidationError):
        ProjectArgs.model_validate({"project_id": "PRJ-1; DROP"})
    with pytest.raises(ValidationError):
        ProjectArgs.model_validate({"project_id": "PRJ-001", "extra": 1})
    with pytest.raises(ValidationError):
        CreateTaskArgs.model_validate({"project_id": "PRJ-001", "title": "x", "assignee": "A B", "due_date": "soon"})
    with pytest.raises(ValidationError):
        DraftEmailArgs.model_validate({"to": "not an email", "subject": "sub", "body": "body"})
    assert DraftEmailArgs.model_validate({"to": " A@B.example ", "subject": "sub", "body": "body"}).to == "a@b.example"


def test_json_schema_is_strict_function_calling_compatible(container: Container) -> None:
    for spec in container.tools.specs():
        tool = spec.function_tool()
        schema = tool["parameters"]
        assert tool["type"] == "function"
        assert tool["strict"] is True
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert schema["required"] == list(schema["properties"])
    nullable = SearchDocumentsArgs.model_json_schema()["properties"]["project_id"]
    assert {"type": "null"} in nullable["anyOf"]


def test_catalog_separates_read_and_write_tools(container: Container) -> None:
    assert container.tools.write_tool_names() == {"create_task", "draft_email", "log_incident"}
    for spec in container.tools.specs():
        assert (spec.handler is None) == (spec.kind == "write")
    assert container.tools.get("nope") is None


def test_registry_rejects_inconsistent_specs() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        ToolRegistry([ToolSpec("a", "", EmptyArgs, "write"), ToolSpec("a", "", EmptyArgs, "write")])
    with pytest.raises(ValueError, match="write tools must not have one"):
        ToolRegistry([ToolSpec("a", "", EmptyArgs, "write", lambda _a: {})])
    with pytest.raises(ValueError, match="read tools need a handler"):
        ToolRegistry([ToolSpec("a", "", EmptyArgs, "read")])
