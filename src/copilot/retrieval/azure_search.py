"""Azure AI Search hybrid retriever (keyword + vector in one request, fused server-side with RRF).

Not yet validated on Azure: the request shape follows azure-search-documents 12.x and is covered by
unit tests against a fake client, but it has not been run against a live search service.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any, Protocol

from copilot.domain import Chunk, RetrievedChunk

VECTOR_FIELD = "content_vector"
SELECT_FIELDS = ["id", "doc_id", "title", "doc_type", "project_id", "content"]


class SearchClientLike(Protocol):
    def search(self, search_text: str | None = None, **kwargs: Any) -> Iterable[Mapping[str, Any]]: ...


def project_filter(project_id: str | None) -> str | None:
    """OData filter that keeps one project's documents plus company-wide ones.

    ``project_id`` is validated by the tool layer (``PRJ-nnn``) before it reaches this function;
    quotes are still escaped so the filter can never be broken out of.
    """
    if project_id is None:
        return None
    safe = project_id.replace("'", "''")
    return f"project_id eq '{safe}' or project_id eq null"


class AzureSearchRetriever:
    def __init__(self, client: SearchClientLike, embed: Callable[[str], list[float]]) -> None:
        self._client = client
        self._embed = embed

    def search(self, query: str, *, top_k: int = 5, project_id: str | None = None) -> list[RetrievedChunk]:
        from azure.search.documents.models import VectorizedQuery

        vector_query = VectorizedQuery(vector=self._embed(query), k_nearest_neighbors=50, fields=VECTOR_FIELD)
        results = self._client.search(
            search_text=query,
            vector_queries=[vector_query],
            filter=project_filter(project_id),
            select=SELECT_FIELDS,
            top=top_k,
        )
        return [
            RetrievedChunk(
                chunk=Chunk(
                    id=str(row["id"]),
                    doc_id=str(row["doc_id"]),
                    title=str(row["title"]),
                    doc_type=str(row["doc_type"]),
                    project_id=row.get("project_id") or None,
                    content=str(row["content"]),
                ),
                score=float(row.get("@search.score") or 0.0),
            )
            for row in results
        ]
