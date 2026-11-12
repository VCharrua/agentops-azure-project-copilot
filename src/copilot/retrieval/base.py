"""Retriever port. The agent only depends on this protocol, never on a concrete search backend."""

from __future__ import annotations

from typing import Protocol

from copilot.domain import RetrievedChunk


class Retriever(Protocol):
    def search(self, query: str, *, top_k: int = 5, project_id: str | None = None) -> list[RetrievedChunk]:
        """Return the best chunks for ``query``, optionally restricted to one project.

        Company-wide documents (no project) stay eligible when a project filter is applied.
        """
        ...
