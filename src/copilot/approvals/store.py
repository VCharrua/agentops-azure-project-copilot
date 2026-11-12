"""Human-in-the-loop approval gate.

The security property of the whole system lives here:

* The agent loop can only call :meth:`ApprovalGate.propose`. It never holds the executor.
* Execution happens only inside :meth:`ApprovalGate.decide`, which needs a named human approver and
  is reachable only through the approvals API, a different channel from the chat.
* A decision is bound to the exact arguments that were proposed (hash check), is single-use and
  expires.

So whatever a model is tricked into "wanting", the worst outcome of a prompt injection is a pending
proposal that a human can read and reject.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from copilot.approvals.audit import AuditLog
from copilot.domain import ActionStatus, PendingAction

Executor = Callable[[str, dict[str, Any]], dict[str, Any]]
NON_HUMAN_ACTORS = frozenset({"agent", "assistant", "system", "copilot", "model"})


class ApprovalError(Exception):
    """A decision could not be applied (unknown action, wrong state, invalid approver)."""


def arguments_hash(tool: str, arguments: dict[str, Any]) -> str:
    canonical = json.dumps({"tool": tool, "arguments": arguments}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ApprovalGate:
    def __init__(
        self,
        *,
        executor: Executor,
        audit: AuditLog,
        ttl: timedelta = timedelta(minutes=60),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._executor = executor
        self._audit = audit
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._actions: dict[str, PendingAction] = {}

    def propose(
        self, *, tool: str, arguments: dict[str, Any], session_id: str, risk_flags: list[str] | None = None
    ) -> PendingAction:
        now = self._clock()
        action = PendingAction(
            id=f"act_{uuid.uuid4().hex[:12]}",
            tool=tool,
            arguments=arguments,
            arguments_hash=arguments_hash(tool, arguments),
            session_id=session_id,
            risk_flags=risk_flags or [],
            requested_at=now,
            expires_at=now + self._ttl,
        )
        with self._lock:
            self._actions[action.id] = action
        self._audit.append(
            "action_proposed",
            actor="agent",
            data={
                "action_id": action.id,
                "tool": tool,
                "arguments": arguments,
                "session_id": session_id,
                "risk_flags": action.risk_flags,
            },
        )
        return action.model_copy(deep=True)

    def get(self, action_id: str) -> PendingAction | None:
        with self._lock:
            action = self._actions.get(action_id)
            return action.model_copy(deep=True) if action else None

    def list(self, status: ActionStatus | None = None) -> list[PendingAction]:
        with self._lock:
            actions = [a.model_copy(deep=True) for a in self._actions.values()]
        return [a for a in actions if status is None or a.status == status]

    def decide(self, action_id: str, *, approve: bool, approver: str) -> PendingAction:
        approver = approver.strip()
        if not approver or approver.lower() in NON_HUMAN_ACTORS:
            raise ApprovalError("a named human approver is required")

        with self._lock:
            action = self._actions.get(action_id)
            if action is None:
                raise ApprovalError(f"unknown action {action_id}")
            if action.status != ActionStatus.PENDING:
                raise ApprovalError(f"action {action_id} is already {action.status.value}")
            now = self._clock()
            action.decided_by = approver
            action.decided_at = now
            if now > action.expires_at:
                action.status = ActionStatus.EXPIRED
            elif not approve:
                action.status = ActionStatus.REJECTED
            elif arguments_hash(action.tool, action.arguments) != action.arguments_hash:
                # The stored proposal no longer matches what was shown to the approver.
                action.status = ActionStatus.FAILED
                action.result = {"error": "arguments changed after proposal"}
            else:
                # Mark as executed before running so a concurrent decision cannot run it twice.
                action.status = ActionStatus.EXECUTED
            snapshot = action.model_copy(deep=True)

        if snapshot.status == ActionStatus.EXECUTED:
            try:
                result = self._executor(snapshot.tool, snapshot.arguments)
                status = ActionStatus.EXECUTED
            except Exception as exc:
                result = {"error": type(exc).__name__}
                status = ActionStatus.FAILED
            with self._lock:
                stored = self._actions[action_id]
                stored.status = status
                stored.result = result
                snapshot = stored.model_copy(deep=True)

        self._audit.append(
            f"action_{snapshot.status.value}",
            actor=approver,
            data={"action_id": action_id, "tool": snapshot.tool, "result": snapshot.result},
        )
        if snapshot.status == ActionStatus.EXPIRED:
            raise ApprovalError(f"action {action_id} expired and was not executed")
        return snapshot
