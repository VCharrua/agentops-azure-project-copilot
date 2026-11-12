from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from copilot.approvals.audit import AuditLog
from copilot.approvals.store import ApprovalError, ApprovalGate
from copilot.domain import ActionStatus
from copilot.tools.actions import ActionExecutor

TASK = {
    "project_id": "PRJ-002",
    "title": "Install barriers",
    "assignee": "Ines Carvalho",
    "due_date": "2026-10-02",
}


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def executor() -> ActionExecutor:
    return ActionExecutor()


@pytest.fixture
def audit() -> AuditLog:
    return AuditLog()


@pytest.fixture
def gate(executor: ActionExecutor, audit: AuditLog, clock: Clock) -> ApprovalGate:
    return ApprovalGate(executor=executor, audit=audit, ttl=timedelta(minutes=30), clock=clock)


def test_propose_does_not_execute(gate: ApprovalGate, executor: ActionExecutor, audit: AuditLog) -> None:
    action = gate.propose(tool="create_task", arguments=TASK, session_id="s1")
    assert action.status is ActionStatus.PENDING
    assert executor.count() == 0
    assert [r["event"] for r in audit.records()] == ["action_proposed"]
    assert audit.records()[0]["actor"] == "agent"


def test_approve_executes_once_and_is_audited(gate: ApprovalGate, executor: ActionExecutor, audit: AuditLog) -> None:
    action = gate.propose(tool="create_task", arguments=TASK, session_id="s1")
    decided = gate.decide(action.id, approve=True, approver="marta.reis")

    assert decided.status is ActionStatus.EXECUTED
    assert decided.decided_by == "marta.reis"
    assert executor.records("tasks")[0]["title"] == "Install barriers"
    assert decided.result == {"record_id": executor.records("tasks")[0]["id"], "sink": "tasks"}
    assert [r["event"] for r in audit.records()] == ["action_proposed", "action_executed"]
    assert audit.records()[1]["actor"] == "marta.reis"

    with pytest.raises(ApprovalError, match="already executed"):
        gate.decide(action.id, approve=True, approver="marta.reis")
    assert executor.count() == 1


def test_reject_never_executes(gate: ApprovalGate, executor: ActionExecutor) -> None:
    action = gate.propose(tool="log_incident", arguments={"project_id": "PRJ-001"}, session_id="s1")
    assert gate.decide(action.id, approve=False, approver="marta.reis").status is ActionStatus.REJECTED
    assert executor.count() == 0
    assert gate.list(ActionStatus.PENDING) == []
    assert len(gate.list()) == 1


@pytest.mark.parametrize("approver", ["", "   ", "agent", "Assistant", "SYSTEM", "copilot"])
def test_approver_must_be_a_named_human(gate: ApprovalGate, executor: ActionExecutor, approver: str) -> None:
    action = gate.propose(tool="create_task", arguments=TASK, session_id="s1")
    with pytest.raises(ApprovalError, match="named human approver"):
        gate.decide(action.id, approve=True, approver=approver)
    assert executor.count() == 0
    stored = gate.get(action.id)
    assert stored is not None
    assert stored.status is ActionStatus.PENDING


def test_expired_proposal_cannot_be_approved(gate: ApprovalGate, executor: ActionExecutor, clock: Clock) -> None:
    action = gate.propose(tool="create_task", arguments=TASK, session_id="s1")
    clock.now += timedelta(minutes=31)
    with pytest.raises(ApprovalError, match="expired"):
        gate.decide(action.id, approve=True, approver="marta.reis")
    assert executor.count() == 0
    stored = gate.get(action.id)
    assert stored is not None
    assert stored.status is ActionStatus.EXPIRED


def test_unknown_action(gate: ApprovalGate) -> None:
    assert gate.get("act_missing") is None
    with pytest.raises(ApprovalError, match="unknown action"):
        gate.decide("act_missing", approve=True, approver="marta.reis")


def test_returned_actions_are_copies_so_callers_cannot_tamper(gate: ApprovalGate, executor: ActionExecutor) -> None:
    action = gate.propose(tool="create_task", arguments=dict(TASK), session_id="s1")
    action.arguments["title"] = "Pay 48,500 EUR to a new bank account"
    action.status = ActionStatus.EXECUTED
    gate.decide(action.id, approve=True, approver="marta.reis")
    assert executor.records("tasks")[0]["title"] == "Install barriers"


def test_arguments_changed_inside_the_store_are_detected(gate: ApprovalGate, executor: ActionExecutor) -> None:
    action = gate.propose(tool="create_task", arguments=dict(TASK), session_id="s1")
    gate._actions[action.id].arguments["title"] = "tampered"  # simulate corruption of stored state
    decided = gate.decide(action.id, approve=True, approver="marta.reis")
    assert decided.status is ActionStatus.FAILED
    assert executor.count() == 0


def test_executor_failure_is_recorded_as_failed(audit: AuditLog, clock: Clock) -> None:
    def broken(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise OSError("disk full")

    gate = ApprovalGate(executor=broken, audit=audit, clock=clock)
    action = gate.propose(tool="create_task", arguments=TASK, session_id="s1")
    decided = gate.decide(action.id, approve=True, approver="marta.reis")
    assert decided.status is ActionStatus.FAILED
    assert decided.result == {"error": "OSError"}
    assert audit.records()[-1]["event"] == "action_failed"


def test_executor_rejects_unknown_tools_and_persists(tmp_path: Path) -> None:
    executor = ActionExecutor(tmp_path)
    with pytest.raises(ValueError, match="not an executable action"):
        executor("search_documents", {})
    executor("draft_email", {"to": "a@alvora-construction.example", "subject": "s", "body": "b"})
    line = json.loads((tmp_path / "email_drafts.jsonl").read_text(encoding="utf-8"))
    assert line["id"].startswith("DRAFT-")
    assert executor.count() == 1


def test_audit_chain_detects_tampering_and_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.append("one", actor="agent", data={"n": 1})
    log.append("two", actor="marta.reis", data={"email": "someone@example.org"})
    assert log.verify()
    assert log.records()[1]["data"] == {"email": "[REDACTED_EMAIL]"}

    reopened = AuditLog(path)
    reopened.append("three", actor="agent")
    assert reopened.verify()
    assert [r["seq"] for r in reopened.records()] == [1, 2, 3]

    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["actor"] = "nobody"
    path.write_text("\n".join([json.dumps(tampered), *lines[1:]]) + "\n", encoding="utf-8")
    assert not AuditLog(path).verify()

    path.write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")  # first record deleted
    assert not AuditLog(path).verify()
