"""Append-only, hash-chained audit log.

Each record stores the SHA-256 of the previous record, so editing or deleting a line breaks the
chain and ``verify()`` reports it. Records are PII-redacted before they are written and are also
emitted as structured log lines, which in Azure land in Log Analytics (a second, off-box copy).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from copilot.guardrails.pii import redact_value

logger = logging.getLogger("copilot.audit")
GENESIS = "0" * 64


def _digest(record: dict[str, Any]) -> str:
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._records: list[dict[str, Any]] = []
        if path is not None and path.exists():
            self._records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def append(self, event: str, *, actor: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            record: dict[str, Any] = {
                "seq": len(self._records) + 1,
                "timestamp": datetime.now(UTC).isoformat(),
                "event": event,
                "actor": actor,
                "data": redact_value(data or {}),
                "prev_hash": self._records[-1]["hash"] if self._records else GENESIS,
            }
            record["hash"] = _digest(record)
            self._records.append(record)
            if self._path is not None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps(record, sort_keys=True) + "\n")
        logger.info("audit %s", event, extra={"audit": record})
        return record

    def records(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(record) for record in self._records]

    def verify(self) -> bool:
        """Recompute the chain. False means a record was altered, removed or reordered."""
        previous = GENESIS
        for record in self.records():
            body = {key: value for key, value in record.items() if key != "hash"}
            if record.get("prev_hash") != previous or _digest(body) != record.get("hash"):
                return False
            previous = record["hash"]
        return True
