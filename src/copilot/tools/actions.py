"""Write-action executor.

This is the only code that changes anything, and it is only ever called by the approval gate after
a human decision. The "systems of record" are synthetic: tasks, email drafts and incidents are
appended to JSON-lines files (or kept in memory in tests). Emails are saved as drafts, never sent.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SINKS = {"create_task": "tasks", "draft_email": "email_drafts", "log_incident": "incidents"}
_PREFIX = {"create_task": "TASK", "draft_email": "DRAFT", "log_incident": "INC"}


class ActionExecutor:
    def __init__(self, state_dir: Path | None = None) -> None:
        self._state_dir = state_dir
        self._lock = threading.Lock()
        self._records: dict[str, list[dict[str, Any]]] = {sink: [] for sink in _SINKS.values()}

    def __call__(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool not in _SINKS:
            raise ValueError(f"{tool} is not an executable action")
        sink = _SINKS[tool]
        record = {
            "id": f"{_PREFIX[tool]}-{uuid.uuid4().hex[:8].upper()}",
            "created_at": datetime.now(UTC).isoformat(),
            **arguments,
        }
        with self._lock:
            self._records[sink].append(record)
            if self._state_dir is not None:
                self._state_dir.mkdir(parents=True, exist_ok=True)
                with (self._state_dir / f"{sink}.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps(record, sort_keys=True) + "\n")
        return {"record_id": record["id"], "sink": sink}

    def records(self, sink: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(record) for record in self._records[sink]]

    def count(self) -> int:
        """Total number of side effects performed. The evals assert this stays at zero."""
        with self._lock:
            return sum(len(records) for records in self._records.values())
