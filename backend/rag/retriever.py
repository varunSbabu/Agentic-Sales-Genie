"""High-level retrieval API used by the LangGraph agent (Phase 5)."""

import uuid

from backend.config import settings
from backend.rag.embeddings import embed_query
from backend.rag.vectorstore import query_collection
from backend.utils.logging import logger

EMPTY_KB_MESSAGE = (
    "No frameworks loaded. Please upload scoring documents in settings."
)


def retrieve_frameworks(
    user_id: str | uuid.UUID,
    query: str,
    top_k: int | None = None,
) -> str:
    """Return the user's most relevant KB chunks as a single formatted string.

    Empty collection or empty query returns a clear user-facing message so the
    agent can degrade gracefully instead of silently producing low-quality
    scores from no grounding.
    """
    k = top_k or settings.retrieval_k
    query = (query or "").strip()
    if not query:
        return EMPTY_KB_MESSAGE

    query_vec = embed_query(query)
    hits = query_collection(user_id, query_vec, top_k=k)
    if not hits:
        logger.info("no KB hits for user {} — returning placeholder", user_id)
        return EMPTY_KB_MESSAGE

    blocks = []
    for i, hit in enumerate(hits, start=1):
        meta = hit["metadata"]
        source = meta.get("filename", "unknown")
        chunk_idx = meta.get("chunk_index", "?")
        blocks.append(
            f"[Source {i} — {source}, chunk {chunk_idx}]\n{hit['text'].strip()}"
        )
    return "\n\n".join(blocks)
