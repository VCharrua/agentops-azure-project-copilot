"""Run the golden dataset against the agent and enforce quality thresholds.

Offline (default, no Azure, deterministic):

    python -m copilot.evals.runner --dataset evals/golden/v1.jsonl --thresholds evals/thresholds.yaml

Live, against a deployed environment (same dataset, real model and services):

    python -m copilot.evals.runner ... --target-url https://<app-url> [--llm-judge]

Exit code 0 means every threshold was met, 1 means at least one metric regressed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Protocol

import httpx
import yaml

from copilot.bootstrap import build_container
from copilot.config import Settings
from copilot.domain import AgentResponse, Message
from copilot.evals.attacks import CompromisedModel
from copilot.evals.judge import LlmGroundednessJudge
from copilot.evals.metrics import Case, CaseResult, aggregate, score_case
from copilot.retrieval.documents import load_chunks


class Target(Protocol):
    def run(self, case: Case) -> tuple[AgentResponse, int] | None:
        """Run a case. Returns the final response and the number of executed actions, or None to skip."""
        ...


class HttpClientLike(Protocol):
    """The part of ``httpx.Client`` the live target uses (also satisfied by FastAPI's TestClient)."""

    def get(self, url: str, *, params: dict[str, str]) -> Any: ...

    def post(self, url: str, *, json: dict[str, object]) -> Any: ...


class LocalTarget:
    """Fresh in-process application per case, so cases cannot influence each other."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def run(self, case: Case) -> tuple[AgentResponse, int] | None:
        model = CompromisedModel(case.attack) if case.attack else None
        container = build_container(self._settings, model=model, persist=False)
        history: list[Message] = []
        session_id = f"eval-{case.id}"
        for turn in case.turns:
            earlier = container.orchestrator.run(turn, session_id=session_id, history=history)
            history += [Message(role="user", content=turn), Message(role="assistant", content=earlier.answer)]
        response = container.orchestrator.run(case.input, session_id=session_id, history=history)
        return response, container.executor.count()


class HttpTarget:
    """A deployed API. Attack-script cases are skipped: they need a model that only exists offline."""

    def __init__(self, base_url: str, *, token: str | None = None, client: HttpClientLike | None = None) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client: HttpClientLike = client or httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=120.0
        )

    def _executed(self) -> int:
        response = self._client.get("/approvals", params={"status": "executed"})
        response.raise_for_status()
        return len(response.json())

    def run(self, case: Case) -> tuple[AgentResponse, int] | None:
        if case.attack:
            return None
        before = self._executed()
        session_id = f"eval-{case.id}-{os.urandom(4).hex()}"
        payload: dict[str, object] = {}
        for message in [*case.turns, case.input]:
            reply = self._client.post("/chat", json={"message": message, "session_id": session_id})
            reply.raise_for_status()
            payload = reply.json()
        return AgentResponse.model_validate(payload), self._executed() - before


def load_cases(path: Path) -> list[Case]:
    cases = [
        Case.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("//")
    ]
    ids = [case.id for case in cases]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"duplicate case ids: {duplicates}")
    return cases


def run_cases(
    cases: list[Case], target: Target, *, data_dir: Path, judge: LlmGroundednessJudge | None = None
) -> list[CaseResult]:
    documents: dict[str, list[str]] = {}
    for chunk in load_chunks(data_dir):
        documents.setdefault(chunk.doc_id, []).append(chunk.content)

    results: list[CaseResult] = []
    for case in cases:
        outcome = target.run(case)
        if outcome is None:
            results.append(
                CaseResult(id=case.id, category=case.category, subcategory=case.subcategory, passed=True, skipped=True)
            )
            continue
        response, executed = outcome
        retrieved = [doc_id for trace in response.tool_calls for doc_id in trace.retrieved_doc_ids]
        context = "\n".join(text for doc_id in dict.fromkeys(retrieved) for text in documents.get(doc_id, []))
        result = score_case(case, response, context=context, executed_actions=executed)
        if judge is not None and case.category == "rag":
            judged = judge.score(question=case.input, answer=response.answer, context=context)
            if judged is not None:
                result.scores["groundedness_llm"] = judged
        results.append(result)
    return results


def check_thresholds(metrics: dict[str, float | None], thresholds: dict[str, float]) -> list[str]:
    """Return one message per violated threshold. A metric that was not measured is only a
    violation if it is not optional (``groundedness_llm`` exists only with the LLM judge)."""
    violations: list[str] = []
    for name, minimum in thresholds.items():
        value = metrics.get(name)
        if value is None:
            if name != "groundedness_llm":
                violations.append(f"{name}: not measured (threshold {minimum})")
        elif value < minimum:
            violations.append(f"{name}: {value:.3f} is below the threshold {minimum:.3f}")
    return violations


def render_markdown(
    metrics: dict[str, float | None],
    thresholds: dict[str, float],
    results: list[CaseResult],
    *,
    dataset: str,
    target: str,
) -> str:
    failed = [r for r in results if not r.passed]
    skipped = sum(r.skipped for r in results)
    lines = [
        "## Agent evaluation",
        "",
        f"Dataset `{dataset}`, target `{target}`: {len(results)} cases, {len(failed)} failed, {skipped} skipped.",
        "",
        "| Metric | Score | Threshold | Status |",
        "| --- | ---: | ---: | :---: |",
    ]
    for name, value in metrics.items():
        minimum = thresholds.get(name)
        if value is None:
            status, shown = "not measured", "-"
        else:
            status = "pass" if minimum is None or value >= minimum else "FAIL"
            shown = f"{value:.3f}"
        lines.append(f"| {name} | {shown} | {'-' if minimum is None else f'{minimum:.2f}'} | {status} |")
    if failed:
        lines += ["", "### Failed cases", ""]
        lines += [f"- `{r.id}` ({r.category}/{r.subcategory or '-'}): {'; '.join(r.failures)}" for r in failed]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the agent against the golden dataset.")
    parser.add_argument("--dataset", type=Path, default=Path("evals/golden/v1.jsonl"))
    parser.add_argument("--thresholds", type=Path, default=Path("evals/thresholds.yaml"))
    parser.add_argument("--out", type=Path, default=Path("reports/evals"))
    parser.add_argument("--data-dir", type=Path, default=Path("data/synthetic"))
    parser.add_argument("--target-url", help="evaluate a deployed API instead of the in-process offline agent")
    parser.add_argument("--token", default=os.getenv("COPILOT_EVAL_TOKEN"), help="bearer token for --target-url")
    parser.add_argument("--llm-judge", action="store_true", help="add LLM-judged groundedness (needs Azure)")
    args = parser.parse_args(argv)

    cases = load_cases(args.dataset)
    thresholds: dict[str, float] = yaml.safe_load(args.thresholds.read_text(encoding="utf-8"))["thresholds"]

    target: Target
    if args.target_url:
        target = HttpTarget(args.target_url, token=args.token)
    else:
        target = LocalTarget(Settings(mode="local", data_dir=args.data_dir, _env_file=None))  # type: ignore[call-arg]

    judge = None
    if args.llm_judge:  # pragma: no cover - needs a live Foundry project
        from azure.ai.projects import AIProjectClient

        from copilot.bootstrap import azure_credential

        settings = Settings()
        project = AIProjectClient(endpoint=settings.require("foundry_project_endpoint"), credential=azure_credential())
        judge = LlmGroundednessJudge(project.get_openai_client(), deployment=settings.foundry_model_deployment)

    results = run_cases(cases, target, data_dir=args.data_dir, judge=judge)
    metrics = aggregate(results)
    violations = check_thresholds(metrics, thresholds)
    target_name = args.target_url or "offline"
    report = render_markdown(metrics, thresholds, results, dataset=args.dataset.name, target=target_name)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.md").write_text(report, encoding="utf-8", newline="\n")
    (args.out / "results.json").write_text(
        json.dumps(
            {
                "dataset": args.dataset.name,
                "target": target_name,
                "metrics": metrics,
                "thresholds": thresholds,
                "violations": violations,
                "cases": [r.model_dump() for r in results],
            },
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(report)

    print(report)
    if violations:
        print("EVALUATION FAILED:\n- " + "\n- ".join(violations))
        return 1
    print("All thresholds met.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
