"""Lazy singleton wrapper around a sentence-transformers model.

The spec requires `all-MiniLM-L6-v2`, cached at startup, batched for efficiency.
We initialize lazily on first call so unit tests / migrations that never embed
don't pay the ~3-10s model load. After the first call the model stays resident.
"""

import threading
from typing import Iterable

from sentence_transformers import SentenceTransformer

from backend.config import settings
from backend.utils.logging import logger

_model: SentenceTransformer | None = None
_lock = threading.Lock()


def get_embedder() -> SentenceTransformer:
    """Return the singleton SentenceTransformer model."""
    global _model
    if _model is not None:
        return _model
    with _lock:
        if _model is None:
            logger.info("loading embedding model {}", settings.embedding_model)
            _model = SentenceTransformer(settings.embedding_model)
            logger.info("embedding model ready (dim={})", _model.get_sentence_embedding_dimension())
    return _model


def embed_texts(texts: Iterable[str], *, batch_size: int = 32) -> list[list[float]]:
    """Embed a list of strings into vectors. Returns plain Python lists for JSON-friendliness."""
    texts = list(texts)
    if not texts:
        return []
    model = get_embedder()
    vectors = model.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=False,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    return [v.tolist() for v in vectors]


def embed_query(query: str) -> list[float]:
    """Embed a single query string. Convenience wrapper."""
    if not query.strip():
        raise ValueError("cannot embed empty query")
    return embed_texts([query])[0]
