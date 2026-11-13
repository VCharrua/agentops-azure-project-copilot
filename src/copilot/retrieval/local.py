"""Offline hybrid retriever: BM25 keyword ranking fused with a vector ranking.

This mirrors the shape of Azure AI Search hybrid retrieval (keyword + vector, fused with Reciprocal
Rank Fusion) so the agent, tests and evals exercise the same code path locally as in Azure.

The vectors are *not* semantic embeddings: they use the hashing trick over word and character
n-grams. That is enough to make the pipeline deterministic and dependency-free, and it is honest
about being a stand-in. Retrieval quality in Azure depends on the real embedding model.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter

from copilot.domain import Chunk, RetrievedChunk

_TOKEN = re.compile(r"[a-z0-9]+(?:[-.][a-z0-9]+)*")
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
        "what",
        "when",
        "which",
        "who",
        "how",
        "does",
        "do",
        "did",
        "me",
        "my",
        "our",
        "we",
        "you",
        "i",
        "about",
        "any",
        "there",
        "their",
    ]
)
VECTOR_DIMENSIONS = 256
RRF_K = 60


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]


def _bucket(feature: str) -> tuple[int, float]:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    return value % VECTOR_DIMENSIONS, 1.0 if (value >> 63) else -1.0


def embed(text: str) -> list[float]:
    """Deterministic hashing-trick vector over tokens and character trigrams, L2-normalised."""
    vector = [0.0] * VECTOR_DIMENSIONS
    for token in tokenize(text):
        index, sign = _bucket(f"w:{token}")
        vector[index] += sign
        for i in range(len(token) - 2):
            index, sign = _bucket(f"c:{token[i : i + 3]}")
            vector[index] += 0.5 * sign
    norm = math.sqrt(sum(v * v for v in vector))
    return [v / norm for v in vector] if norm else vector


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


class LocalHybridRetriever:
    """In-memory BM25 + vector retriever with Reciprocal Rank Fusion."""

    def __init__(self, chunks: list[Chunk], *, k1: float = 1.5, b: float = 0.75) -> None:
        self._chunks = chunks
        self._k1 = k1
        self._b = b
        # Titles are indexed with the body so "Riverside ... contract" style queries match.
        self._texts = [f"{c.title}. {c.content}" for c in chunks]
        self._tokens = [tokenize(t) for t in self._texts]
        self._term_freqs = [Counter(tokens) for tokens in self._tokens]
        self._avg_len = sum(len(t) for t in self._tokens) / max(len(self._tokens), 1)
        doc_freq: Counter[str] = Counter()
        for tokens in self._tokens:
            doc_freq.update(set(tokens))
        total = len(chunks)
        self._idf = {term: math.log(1 + (total - df + 0.5) / (df + 0.5)) for term, df in doc_freq.items()}
        self._vectors = [embed(t) for t in self._texts]

    def _bm25(self, query_tokens: list[str], index: int) -> float:
        freqs = self._term_freqs[index]
        length_norm = 1 - self._b + self._b * len(self._tokens[index]) / self._avg_len
        score = 0.0
        for term in query_tokens:
            tf = freqs.get(term, 0)
            if tf:
                score += self._idf[term] * tf * (self._k1 + 1) / (tf + self._k1 * length_norm)
        return score

    def search(self, query: str, *, top_k: int = 5, project_id: str | None = None) -> list[RetrievedChunk]:
        candidates = [
            i for i, chunk in enumerate(self._chunks) if project_id is None or chunk.project_id in (project_id, None)
        ]
        query_tokens = tokenize(query)
        if not candidates or not query_tokens:
            return []

        keyword_scores = {i: self._bm25(query_tokens, i) for i in candidates}
        query_vector = embed(query)
        vector_scores = {i: cosine(query_vector, self._vectors[i]) for i in candidates}

        fused: dict[int, float] = dict.fromkeys(candidates, 0.0)
        keyword_ranked = sorted((i for i in candidates if keyword_scores[i] > 0), key=lambda i: -keyword_scores[i])
        vector_ranked = sorted((i for i in candidates if vector_scores[i] > 0), key=lambda i: -vector_scores[i])
        for ranking in (keyword_ranked, vector_ranked):
            for rank, index in enumerate(ranking, start=1):
                fused[index] += 1.0 / (RRF_K + rank)

        # A chunk with no keyword overlap at all is noise for this corpus: require a keyword hit.
        ranked = sorted((i for i in candidates if keyword_scores[i] > 0), key=lambda i: (-fused[i], i))
        return [RetrievedChunk(chunk=self._chunks[i], score=round(fused[i], 6)) for i in ranked[:top_k]]
