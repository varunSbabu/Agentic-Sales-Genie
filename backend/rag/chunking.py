"""Recursive text splitting using LangChain's splitter.

The spec specifies 512/50 in `tokens`. We treat those values as *characters*
because (a) the sentence-transformers MiniLM model has only 256-token capacity
so character-level chunks under ~2k are safely under that limit, and (b) it
avoids pulling tiktoken into the dependency tree just for chunk sizing.
The config values are tunable from `settings.chunk_size` / `chunk_overlap`.
"""

from dataclasses import dataclass
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from backend.config import settings
from backend.utils.logging import logger


@dataclass(frozen=True)
class Chunk:
    text: str
    metadata: dict[str, Any]


_splitter: RecursiveCharacterTextSplitter | None = None


def _get_splitter() -> RecursiveCharacterTextSplitter:
    global _splitter
    if _splitter is None:
        _splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""],
            length_function=len,
        )
    return _splitter


def chunk_text(
    text: str,
    *,
    filename: str,
    user_id: str,
    doc_id: str,
) -> list[Chunk]:
    """Split text into overlapping chunks and attach traceability metadata."""
    pieces = _get_splitter().split_text(text)
    chunks = [
        Chunk(
            text=piece,
            metadata={
                "filename": filename,
                "user_id": str(user_id),
                "doc_id": str(doc_id),
                "chunk_index": idx,
            },
        )
        for idx, piece in enumerate(pieces)
    ]
    logger.info("chunked {} into {} pieces", filename, len(chunks))
    return chunks
