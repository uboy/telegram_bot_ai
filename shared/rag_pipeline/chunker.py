from typing import List

from shared.types import LoadedDocument, Chunk
from shared.document_loaders.chunking import (
    split_text_into_chunks,
    split_markdown_section_into_chunks,
    split_text_structurally,
    split_code_into_chunks,
)


def chunk_document(doc: LoadedDocument, doc_class: str) -> List[Chunk]:
    """
    Basic chunker using existing strategies.
    """
    text = doc.content or ""
    if not text:
        return []

    if doc_class == "markdown":
        parts = split_markdown_section_into_chunks(text)
    elif doc_class == "code":
        parts = split_code_into_chunks(text)
    elif doc_class in {"text", "log", "config", "table"}:
        parts = split_text_structurally(text)
    else:
        parts = split_text_into_chunks(text)

    return [Chunk(content=p, metadata={"document_class": doc_class}) for p in parts if p]
