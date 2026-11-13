"""Load synthetic project documents from disk and split them into retrievable chunks."""

from __future__ import annotations

from pathlib import Path

from copilot.domain import Chunk


def parse_document(text: str) -> tuple[dict[str, str], list[str]]:
    """Split a document into its ``---`` delimited header and its paragraphs."""
    if not text.startswith("---"):
        raise ValueError("document is missing its metadata header")
    _, header, body = text.split("---", 2)
    meta: dict[str, str] = {}
    for line in header.strip().splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    paragraphs = [p.strip() for p in body.strip().split("\n\n") if p.strip()]
    return meta, paragraphs


def load_chunks(data_dir: Path) -> list[Chunk]:
    """One chunk per paragraph. Documents are short, so paragraph chunks keep citations precise."""
    chunks: list[Chunk] = []
    for path in sorted((data_dir / "documents").glob("*.md")):
        meta, paragraphs = parse_document(path.read_text(encoding="utf-8"))
        for index, paragraph in enumerate(paragraphs):
            chunks.append(
                Chunk(
                    id=f"{meta['doc_id']}-{index}",
                    doc_id=meta["doc_id"],
                    title=meta["title"],
                    doc_type=meta["doc_type"],
                    project_id=meta.get("project_id") or None,
                    content=paragraph,
                )
            )
    if not chunks:
        raise FileNotFoundError(f"no documents under {data_dir / 'documents'}; run scripts/generate_synthetic_data.py")
    return chunks
