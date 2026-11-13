"""Core domain types shared by the agent, tools, retrieval, guardrails and API layers."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    """A retrievable piece of a project document."""

    id: str
    doc_id: str
    title: str
    doc_type: str
    project_id: str | None = None
    content: str


class RetrievedChunk(BaseModel):
    chunk: Chunk
    score: float


class ToolCall(BaseModel):
    call_id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


class Message(BaseModel):
    """Provider-neutral conversation message."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None


class ModelTurn(BaseModel):
    """One model completion: either final text or a batch of tool calls."""

    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)


class ActionStatus(StrEnum):
    PENDING = "pending"
    EXECUTED = "executed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FAILED = "failed"


class PendingAction(BaseModel):
    """A write action proposed by the agent. It only runs after an explicit human decision."""

    id: str
    tool: str
    arguments: dict[str, Any]
    arguments_hash: str
    session_id: str
    status: ActionStatus = ActionStatus.PENDING
    risk_flags: list[str] = Field(default_factory=list)
    requested_at: datetime
    expires_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None
    result: dict[str, Any] | None = None


class ToolTrace(BaseModel):
    """What the agent did, returned to the caller for transparency and used by the evals."""

    name: str
    arguments: dict[str, Any]
    kind: Literal["read", "write"]
    outcome: Literal["ok", "error", "pending_approval"]
    retrieved_doc_ids: list[str] = Field(default_factory=list)
    withheld_doc_ids: list[str] = Field(default_factory=list)


class Citation(BaseModel):
    doc_id: str
    title: str


class AgentResponse(BaseModel):
    session_id: str
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    tool_calls: list[ToolTrace] = Field(default_factory=list)
    pending_actions: list[PendingAction] = Field(default_factory=list)
    blocked: bool = False
    block_reason: str | None = None
    usage: Usage = Field(default_factory=Usage)
    ai_disclosure: str = (
        "AI-generated content. Verify against source documents before acting. "
        "Write actions are never executed without human approval."
    )
