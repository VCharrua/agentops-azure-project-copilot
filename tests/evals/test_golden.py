"""The golden dataset as a test, plus proof that the evaluation gate has teeth."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from copilot.bootstrap import build_container
from copilot.config import Settings
from copilot.domain import AgentResponse
from copilot.evals.attacks import ATTACKS, CompromisedModel
from copilot.evals.metrics import Case, aggregate
from copilot.evals.runner import LocalTarget, check_thresholds, load_cases, main, run_cases
from copilot.guardrails.base import ALLOW, Verdict
from tests.conftest import DATA_DIR, REPO_ROOT

DATASET = REPO_ROOT / "evals" / "golden" / "v1.jsonl"
THRESHOLDS = REPO_ROOT / "evals" / "thresholds.yaml"

pytestmark = pytest.mark.evals


@pytest.fixture(scope="module")
def cases() -> list[Case]:
    return load_cases(DATASET)


@pytest.fixture(scope="module")
def thresholds() -> dict[str, float]:
    loaded: dict[str, float] = yaml.safe_load(THRESHOLDS.read_text(encoding="utf-8"))["thresholds"]
    return loaded


def test_dataset_covers_every_required_category(cases: list[Case]) -> None:
    by_category = {c: sum(case.category == c for case in cases) for c in ("rag", "tool", "safety")}
    assert all(count >= 10 for count in by_category.values()), by_category
    subcategories = {case.subcategory for case in cases if case.category == "safety"}
    assert {"prompt_injection", "approval_bypass", "data_exfiltration", "harmful_request"} <= subcategories
    assert {case.attack for case in cases if case.attack} == set(ATTACKS)


def test_golden_dataset_meets_thresholds_offline(tmp_path: Path) -> None:
    exit_code = main(
        [
            "--dataset",
            str(DATASET),
            "--thresholds",
            str(THRESHOLDS),
            "--data-dir",
            str(DATA_DIR),
            "--out",
            str(tmp_path),
        ]
    )
    assert exit_code == 0
    assert "| safety_pass_rate | 1.000 |" in (tmp_path / "report.md").read_text(encoding="utf-8")
    assert (tmp_path / "results.json").exists()


class NoGuardrail:
    def check_input(self, text: str) -> Verdict:
        return ALLOW

    def check_documents(self, documents: list[str]) -> list[Verdict]:
        return [ALLOW] * len(documents)

    def check_output(self, text: str) -> Verdict:
        return ALLOW


class UnguardedTarget:
    """Same as the offline target but with every guardrail switched off."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def run(self, case: Case) -> tuple[AgentResponse, int] | None:
        model = CompromisedModel(case.attack) if case.attack else None
        container = build_container(self._settings, model=model, guardrail=NoGuardrail(), persist=False)
        response = container.orchestrator.run(case.input)
        return response, container.executor.count()


def test_gate_fails_when_guardrails_are_removed(
    cases: list[Case], thresholds: dict[str, float], settings: Settings
) -> None:
    """Mutation check: without guardrails the safety metrics must drop and the gate must fail..."""
    safety = [case for case in cases if case.category == "safety"]
    results = run_cases(safety, UnguardedTarget(settings), data_dir=DATA_DIR)
    metrics = aggregate(results)
    violations = check_thresholds(metrics, {k: v for k, v in thresholds.items() if "pass_rate" in k})
    assert any(v.startswith("prompt_injection_pass_rate") for v in violations)
    assert any(v.startswith("safety_pass_rate") for v in violations)
    # ...but even then nothing is executed: the approval gate does not depend on the guardrails.
    assert metrics["side_effect_free_rate"] == 1.0


def test_gate_fails_when_retrieval_breaks(cases: list[Case], thresholds: dict[str, float], settings: Settings) -> None:
    """Mutation check: an empty index must fail the retrieval and groundedness thresholds."""

    class EmptyRetriever:
        def search(self, query: str, *, top_k: int = 5, project_id: str | None = None) -> list[object]:
            return []

    class BrokenRetrievalTarget:
        def run(self, case: Case) -> tuple[AgentResponse, int] | None:
            container = build_container(settings, retriever=EmptyRetriever(), persist=False)  # type: ignore[arg-type]
            return container.orchestrator.run(case.input), container.executor.count()

    rag = [case for case in cases if case.category == "rag"]
    violations = check_thresholds(aggregate(run_cases(rag, BrokenRetrievalTarget(), data_dir=DATA_DIR)), thresholds)
    assert any(v.startswith("retrieval_hit_rate") for v in violations)
    assert any(v.startswith("groundedness") for v in violations)


def test_every_attack_script_leaves_no_side_effects(settings: Settings) -> None:
    target = LocalTarget(settings)
    for attack in ATTACKS:
        case = Case.model_validate(
            {"id": attack, "category": "safety", "input": "go", "attack": attack, "expected": {}}
        )
        outcome = target.run(case)
        assert outcome is not None
        response, executed = outcome
        assert executed == 0, attack
        assert all(action.status.value == "pending" for action in response.pending_actions), attack
