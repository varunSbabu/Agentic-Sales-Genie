"""ChromaDB persistence layer with per-user collection isolation.

Every user gets a private collection named `user_{user_id}`. The retriever
constructs the name from the authenticated `user_id` — it never accepts an
arbitrary name — so cross-user reads are structurally prevented.

The persistent client writes to disk at `settings.chroma_persist_dir`, which is
mounted as a Docker volume so data survives container restarts.
"""

import threading
import uuid
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings

from backend.config import settings
from backend.rag.chunking import Chunk
from backend.rag.embeddings import embed_texts
from backend.utils.logging import logger

_client: chromadb.api.ClientAPI | None = None
_client_lock = threading.Lock()


def _get_client() -> chromadb.api.ClientAPI:
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            persist_dir = Path(settings.chroma_persist_dir)
            persist_dir.mkdir(parents=True, exist_ok=True)
            _client = chromadb.PersistentClient(
                path=str(persist_dir),
                settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
            )
            logger.info("chroma client ready (path={})", persist_dir)
    return _client


def collection_name_for(user_id: str | uuid.UUID) -> str:
    """Build the per-user collection name. Hyphens in UUIDs are valid in Chroma."""
    return f"user_{user_id}"


def get_collection(user_id: str | uuid.UUID):
    """Return (creating if needed) the user's vector collection."""
    name = collection_name_for(user_id)
    return _get_client().get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},
    )


def add_chunks(user_id: str | uuid.UUID, chunks: list[Chunk]) -> int:
    """Embed + insert chunks for one user. Returns number of chunks added."""
    if not chunks:
        return 0

    collection = get_collection(user_id)
    texts = [c.text for c in chunks]
    metadatas = [c.metadata for c in chunks]
    ids = [f"{c.metadata['doc_id']}_{c.metadata['chunk_index']}" for c in chunks]

    embeddings = embed_texts(texts)
    collection.upsert(
        ids=ids,
        embeddings=embeddings,
        documents=texts,
        metadatas=metadatas,
    )
    logger.info(
        "added {} chunks to collection {}", len(chunks), collection_name_for(user_id)
    )
    return len(chunks)


def delete_document_chunks(user_id: str | uuid.UUID, doc_id: str | uuid.UUID) -> int:
    """Remove all vectors belonging to a single KB document. Returns count removed."""
    collection = get_collection(user_id)
    existing = collection.get(where={"doc_id": str(doc_id)}, include=["metadatas"])
    ids = existing.get("ids", []) or []
    if ids:
        collection.delete(ids=ids)
    logger.info(
        "deleted {} chunks for doc {} from collection {}",
        len(ids),
        doc_id,
        collection_name_for(user_id),
    )
    return len(ids)


def get_collection_count(user_id: str | uuid.UUID) -> int:
    """Return the total number of vectors stored for this user."""
    return get_collection(user_id).count()


def query_collection(
    user_id: str | uuid.UUID,
    query_embedding: list[float],
    top_k: int,
) -> list[dict]:
    """Query the user's collection — never another user's. Returns list of hits."""
    collection = get_collection(user_id)
    if collection.count() == 0:
        return []
    result = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )
    hits: list[dict] = []
    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    dists = (result.get("distances") or [[]])[0]
    for doc, meta, dist in zip(docs, metas, dists):
        hits.append({"text": doc, "metadata": dict(meta or {}), "distance": float(dist)})
    return hits
