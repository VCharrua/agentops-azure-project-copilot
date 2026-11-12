"""Tool registry: the single source of truth for what the agent may call.

Each tool has a Pydantic argument model. The same model produces the JSON schema sent to the model
and validates the arguments the model sends back, so the two can never drift apart. Tools are
either ``read`` (executed immediately) or ``write`` (only ever *proposed*; see ``approvals``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PROJECT_ID_PATTERN = r"^PRJ-\d{3}$"


class ToolArgs(BaseModel):
    """Base for tool arguments: unknown fields are rejected, which strict function calling requires."""

    model_config = ConfigDict(extra="forbid")


class SearchDocumentsArgs(ToolArgs):
    query: str = Field(min_length=3, max_length=500, description="What to look for in the project documents.")
    project_id: str | None = Field(
        pattern=PROJECT_ID_PATTERN, description="Restrict the search to one project, e.g. PRJ-001, or null."
    )


class ProjectArgs(ToolArgs):
    project_id: str = Field(pattern=PROJECT_ID_PATTERN, description="Project identifier, e.g. PRJ-001.")


class CostVariancesArgs(ProjectArgs):
    min_variance_pct: float = Field(
        ge=0, le=1000, description="Only return cost codes whose absolute variance is at least this percent."
    )


class EmptyArgs(ToolArgs):
    pass


class CreateTaskArgs(ProjectArgs):
    title: str = Field(min_length=3, max_length=200)
    assignee: str = Field(min_length=2, max_length=100, description="Name of the person responsible.")
    due_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="Due date, ISO format YYYY-MM-DD.")


class DraftEmailArgs(ToolArgs):
    to: str = Field(max_length=200, description="Recipient email address.")
    subject: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=3, max_length=4000)

    @field_validator("to")
    @classmethod
    def _looks_like_email(cls, value: str) -> str:
        local, _, domain = value.strip().partition("@")
        if not local or "." not in domain or " " in value.strip():
            raise ValueError("must be an email address")
        return value.strip().lower()


class LogIncidentArgs(ProjectArgs):
    severity: Literal["low", "medium", "high", "critical"]
    description: str = Field(min_length=10, max_length=2000)


Handler = Callable[[Any], dict[str, Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[ToolArgs]
    kind: Literal["read", "write"]
    handler: Handler | None = None  # write tools have no handler here: only the approval gate executes them

    def json_schema(self) -> dict[str, Any]:
        """JSON schema compatible with strict function calling (all properties required, no extras)."""
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        schema.setdefault("properties", {})
        for prop in schema["properties"].values():
            prop.pop("title", None)
        schema["required"] = list(schema["properties"])
        schema["additionalProperties"] = False
        return schema

    def function_tool(self) -> dict[str, Any]:
        """Function-tool definition in the Responses API shape."""
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.json_schema(),
            "strict": True,
        }


class ToolRegistry:
    def __init__(self, specs: list[ToolSpec]) -> None:
        names = [spec.name for spec in specs]
        if len(names) != len(set(names)):
            raise ValueError("duplicate tool names")
        for spec in specs:
            if (spec.kind == "read") != (spec.handler is not None):
                raise ValueError(f"{spec.name}: read tools need a handler, write tools must not have one")
        self._specs = {spec.name: spec for spec in specs}

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def write_tool_names(self) -> set[str]:
        return {spec.name for spec in self._specs.values() if spec.kind == "write"}
