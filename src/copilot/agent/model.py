"""Model port: the one seam between the agent loop and any LLM backend."""

from __future__ import annotations

from typing import Protocol

from copilot.domain import Message, ModelTurn
from copilot.tools.registry import ToolSpec


class ModelClient(Protocol):
    def complete(self, messages: list[Message], tools: list[ToolSpec]) -> ModelTurn:
        """Return the next turn: final text, or one or more tool calls."""
        ...
