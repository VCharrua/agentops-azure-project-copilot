"""Create the Azure AI Search index and upload the synthetic documents with their embeddings.

    python scripts/index_documents.py --dry-run      # build the index definition, no Azure calls
    python scripts/index_documents.py                # needs COPILOT_SEARCH_ENDPOINT etc. and az login / OIDC

Idempotent: the index is created or updated and documents are merged by key, so the CD pipeline can
run it on every deployment. Authentication is Entra ID only (no admin keys).

Not yet validated on Azure. The index definition is built against azure-search-documents 12.x in the
unit tests (dry run); the upload path has not been run against a live service.
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING, Any

from copilot.config import Settings
from copilot.retrieval.azure_search import VECTOR_FIELD
from copilot.retrieval.documents import load_chunks

if TYPE_CHECKING:
    from azure.search.documents.indexes.models import SearchIndex

# text-embedding-3-small returns 1536 dimensions by default.
EMBEDDING_DIMENSIONS = 1536
BATCH_SIZE = 16


def build_index(name: str, dimensions: int = EMBEDDING_DIMENSIONS) -> SearchIndex:
    from azure.search.documents.indexes.models import (
        HnswAlgorithmConfiguration,
        SearchableField,
        SearchField,
        SearchFieldDataType,
        SearchIndex,
        SimpleField,
        VectorSearch,
        VectorSearchProfile,
    )

    return SearchIndex(
        name=name,
        fields=[
            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
            SimpleField(name="doc_id", type=SearchFieldDataType.String, filterable=True),
            SearchableField(name="title", type=SearchFieldDataType.String),
            SimpleField(name="doc_type", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SimpleField(name="project_id", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SearchableField(name="content", type=SearchFieldDataType.String),
            SearchField(
                name=VECTOR_FIELD,
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=dimensions,
                vector_search_profile_name="hnsw-profile",
            ),
        ],
        vector_search=VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name="hnsw")],
            profiles=[VectorSearchProfile(name="hnsw-profile", algorithm_configuration_name="hnsw")],
        ),
    )


def search_key(chunk_id: str) -> str:
    """Document keys may only contain letters, digits, underscore, dash and equals."""
    return chunk_id.replace(".", "_")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="build the index definition and stop")
    args = parser.parse_args(argv)

    settings = Settings()
    chunks = load_chunks(settings.data_dir)
    index = build_index(settings.search_index)
    if args.dry_run:
        print(f"Index '{index.name}' with {len(index.fields)} fields; {len(chunks)} chunks ready to upload.")
        return 0

    from azure.ai.projects import AIProjectClient
    from azure.search.documents import SearchClient
    from azure.search.documents.indexes import SearchIndexClient

    from copilot.bootstrap import azure_credential

    credential = azure_credential()
    endpoint = settings.require("search_endpoint")
    SearchIndexClient(endpoint, credential).create_or_update_index(index)

    openai_client = AIProjectClient(
        endpoint=settings.require("foundry_project_endpoint"), credential=credential
    ).get_openai_client()
    search_client = SearchClient(endpoint, settings.search_index, credential)

    uploaded = 0
    for start in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[start : start + BATCH_SIZE]
        embeddings = openai_client.embeddings.create(
            model=settings.foundry_embedding_deployment, input=[f"{c.title}. {c.content}" for c in batch]
        )
        documents: list[dict[str, Any]] = [
            {**chunk.model_dump(), "id": search_key(chunk.id), VECTOR_FIELD: list(item.embedding)}
            for chunk, item in zip(batch, embeddings.data, strict=True)
        ]
        results = search_client.merge_or_upload_documents(documents)
        failed = [r.key for r in results if not r.succeeded]
        if failed:
            print(f"Failed to index: {failed}")
            return 1
        uploaded += len(documents)
    print(f"Indexed {uploaded} chunks into '{settings.search_index}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
