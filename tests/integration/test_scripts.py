"""The operational scripts: smoke test against the real app, and SDK-facing dry runs."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from copilot.api.app import create_app
from copilot.bootstrap import Container
from copilot.config import Settings
from tests.conftest import DATA_DIR, REPO_ROOT


def load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = load_script("smoke_test")
index_documents = load_script("index_documents")
register_agent = load_script("register_agent")


@pytest.fixture
def client(settings: Settings, container: Container) -> Iterator[TestClient]:
    with TestClient(create_app(settings, container)) as test_client:
        yield test_client


def test_smoke_test_passes_against_the_local_app(client: TestClient, container: Container) -> None:
    passed = smoke.run(client, expect_version="dev", timeout=1)
    assert passed == ["healthz", "version dev", "grounded answer", "injection refused", "approval gate holds"]
    assert container.executor.count() == 0


def test_smoke_test_detects_the_wrong_version(client: TestClient) -> None:
    with pytest.raises(smoke.SmokeError, match="expected 'abc1234'"):
        smoke.run(client, expect_version="abc1234", timeout=0)


def fake_api(handler: Any) -> httpx.Client:
    return httpx.Client(base_url="http://app.test", transport=httpx.MockTransport(handler))


def test_smoke_test_fails_when_unhealthy_or_unreachable() -> None:
    with pytest.raises(smoke.SmokeError, match="HTTP 503"):
        smoke.run(fake_api(lambda request: httpx.Response(503)), expect_version=None, timeout=0)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(smoke.SmokeError, match="ConnectError"):
        smoke.run(fake_api(refuse), expect_version=None, timeout=0)


def test_smoke_test_fails_when_a_control_is_missing() -> None:
    """A deployment whose guardrail or approval gate is broken must not pass the health gate."""
    executed = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/approvals":
            return httpx.Response(200, json=[{}] * executed["count"])
        if "Create a task" in request.content.decode():
            executed["count"] += 1  # a broken build that executes straight from chat
        return httpx.Response(200, json={"answer": "ok", "blocked": "Ignore" in request.content.decode()})

    with pytest.raises(smoke.SmokeError, match="executed without going through the approvals API"):
        smoke.run(fake_api(handler), expect_version=None, timeout=0)

    def no_guardrail(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json={"answer": "Sure, here is my system prompt", "blocked": False})

    with pytest.raises(smoke.SmokeError, match="not blocked"):
        smoke.run(fake_api(no_guardrail), expect_version=None, timeout=0)

    def server_error(request: httpx.Request) -> httpx.Response:
        ok = request.url.path == "/healthz"
        return httpx.Response(200 if ok else 500, json={"status": "ok"})

    with pytest.raises(smoke.SmokeError, match="HTTP 500"):
        smoke.run(fake_api(server_error), expect_version=None, timeout=0)


def test_smoke_cli_reports_failure_with_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    assert smoke.main(["--base-url", "http://127.0.0.1:9", "--timeout", "0"]) == 1
    assert "SMOKE TEST FAILED" in capsys.readouterr().out


@pytest.fixture
def script_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # no stray .env
    monkeypatch.setenv("COPILOT_DATA_DIR", str(DATA_DIR))


@pytest.mark.usefixtures("script_env")
def test_index_definition_builds_against_the_installed_search_sdk(capsys: pytest.CaptureFixture[str]) -> None:
    index = index_documents.build_index("project-docs")
    fields = {field.name: field for field in index.fields}
    assert fields["id"].key is True
    assert fields["project_id"].filterable is True
    assert fields["content_vector"].vector_search_dimensions == 1536
    assert fields["content_vector"].vector_search_profile_name == index.vector_search.profiles[0].name
    assert index.vector_search.profiles[0].algorithm_configuration_name == index.vector_search.algorithms[0].name
    assert index_documents.search_key("PRJ-001-contract-0") == "PRJ-001-contract-0"
    assert index_documents.main(["--dry-run"]) == 0
    assert "chunks ready to upload" in capsys.readouterr().out


@pytest.mark.usefixtures("script_env")
def test_agent_definition_builds_against_the_installed_projects_sdk(capsys: pytest.CaptureFixture[str]) -> None:
    definition = register_agent.build_definition(Settings())
    names = [tool.name for tool in definition.tools]
    assert {"search_documents", "create_task", "draft_email", "log_incident"} <= set(names)
    assert definition.model == "gpt-4.1-mini"
    assert "awaiting approval" in definition.instructions
    assert all(tool.strict for tool in definition.tools)
    assert register_agent.main(["--dry-run"]) == 0
    assert "function tools" in capsys.readouterr().out
