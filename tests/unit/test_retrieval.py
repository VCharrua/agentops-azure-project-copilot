from __future__ import annotations

import math
from typing import Any

import pytest

from copilot.domain import Chunk
from copilot.retrieval.azure_search import AzureSearchRetriever, project_filter
from copilot.retrieval.documents import load_chunks, parse_document
from copilot.retrieval.local import LocalHybridRetriever, embed, tokenize
from tests.conftest import DATA_DIR


@pytest.fixture(scope="module")
def retriever() -> LocalHybridRetriever:
    return LocalHybridRetriever(load_chunks(DATA_DIR))


def test_parse_document_reads_header_and_paragraphs() -> None:
    meta, paragraphs = parse_document("---\ndoc_id: X-1\ntitle: A: B\n---\n\nOne.\n\nTwo.\n")
    assert meta == {"doc_id": "X-1", "title": "A: B"}
    assert paragraphs == ["One.", "Two."]


def test_parse_document_requires_header() -> None:
    with pytest.raises(ValueError, match="metadata header"):
        parse_document("no header")


def test_load_chunks_fails_clearly_when_data_is_missing(tmp_path: Any) -> None:
    (tmp_path / "documents").mkdir()
    with pytest.raises(FileNotFoundError, match="generate_synthetic_data"):
        load_chunks(tmp_path)


def test_tokenize_drops_stopwords_and_keeps_identifiers() -> None:
    assert tokenize("What is the status of PRJ-001 and CO-014?") == ["status", "prj-001", "co-014"]


def test_embedding_is_deterministic_and_normalised() -> None:
    vector = embed("crane breakdown delays slab pour")
    assert vector == embed("crane breakdown delays slab pour")
    assert math.isclose(sum(v * v for v in vector), 1.0, rel_tol=1e-9)
    assert embed("") == [0.0] * len(vector)


@pytest.mark.parametrize(
    ("query", "expected_doc"),
    [
        ("liquidated damages Riverside Residential Tower", "PRJ-001-contract"),
        ("asbestos discovered above ceilings Block B", "PRJ-003-site-report-w31"),
        ("forklift near miss Gate B root cause", "PRJ-002-meeting-2026-09-10"),
        ("change order CO-014 approved", "PRJ-003-meeting-2026-08-05"),
        ("who can approve variations above 50,000 EUR", "ALV-delegation-of-authority"),
    ],
)
def test_expected_document_is_ranked_first(retriever: LocalHybridRetriever, query: str, expected_doc: str) -> None:
    assert retriever.search(query, top_k=3)[0].chunk.doc_id == expected_doc


def test_project_filter_keeps_company_documents(retriever: LocalHybridRetriever) -> None:
    hits = retriever.search("incident reporting severity near miss", top_k=10, project_id="PRJ-002")
    assert hits
    assert {hit.chunk.project_id for hit in hits} <= {"PRJ-002", None}
    assert any(hit.chunk.doc_id == "ALV-hse-policy" for hit in hits)


def test_no_keyword_overlap_returns_nothing(retriever: LocalHybridRetriever) -> None:
    assert retriever.search("zebra xylophone quasar") == []
    assert retriever.search("the of and") == []


class FakeSearchClient:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.kwargs: dict[str, Any] = {}

    def search(self, search_text: str | None = None, **kwargs: Any) -> list[dict[str, Any]]:
        self.kwargs = {"search_text": search_text, **kwargs}
        return self.rows


def test_azure_retriever_sends_a_hybrid_query_and_maps_results() -> None:
    chunk = Chunk(id="D-1-0", doc_id="D-1", title="T", doc_type="policy", project_id=None, content="text")
    client = FakeSearchClient([{**chunk.model_dump(), "@search.score": 0.03}])
    retriever = AzureSearchRetriever(client, embed=lambda _text: [0.1, 0.2])

    hits = retriever.search("variation approval", top_k=4, project_id="PRJ-001")

    assert hits[0].chunk == chunk
    assert hits[0].score == pytest.approx(0.03)
    assert client.kwargs["search_text"] == "variation approval"  # keyword leg
    vector_query = client.kwargs["vector_queries"][0]  # vector leg
    assert vector_query.vector == [0.1, 0.2]
    assert vector_query.fields == "content_vector"
    assert client.kwargs["top"] == 4
    assert client.kwargs["filter"] == "project_id eq 'PRJ-001' or project_id eq null"


def test_project_filter_escapes_quotes() -> None:
    assert project_filter(None) is None
    assert project_filter("x' or 1 eq 1") == "project_id eq 'x'' or 1 eq 1' or project_id eq null"
