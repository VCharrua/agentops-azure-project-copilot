"""Shared fixtures. Everything runs offline against the committed synthetic dataset."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from copilot.bootstrap import Container, build_container
from copilot.config import Settings
from copilot.domain import Message, ModelTurn, ToolCall
from copilot.tools.project_data import ProjectRepository
from copilot.tools.registry import ToolSpec

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data" / "synthetic"


class ScriptedModel:
    """A model that plays back a fixed list of turns. Lets tests act as a misbehaving LLM."""

    def __init__(self, turns: list[ModelTurn]) -> None:
        self._turns = list(turns)
        self.calls: list[list[Message]] = []

    def complete(self, messages: list[Message], tools: list[ToolSpec]) -> ModelTurn:
        self.calls.append(list(messages))
        return self._turns.pop(0) if self._turns else ModelTurn(text="done")


def tool_turn(name: str, arguments: Mapping[str, object], call_id: str = "call_1") -> ModelTurn:
    return ModelTurn(tool_calls=[ToolCall(call_id=call_id, name=name, arguments=dict(arguments))])


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=DATA_DIR, state_dir=tmp_path / "state", _env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def container(settings: Settings) -> Container:
    return build_container(settings)


@pytest.fixture(scope="session")
def repo() -> ProjectRepository:
    return ProjectRepository(DATA_DIR)
