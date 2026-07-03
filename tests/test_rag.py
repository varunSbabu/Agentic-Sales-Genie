"""RAG tests — ingestion, chunking, embeddings, and cross-user isolation.

These use the real local ChromaDB + sentence-transformers model (no network).
The embedding model is cached in the hf_cache volume after first load.
"""

import uuid

import pytest


# ---------------------------------------------------------------------------
# Ingestion + chunking
# ---------------------------------------------------------------------------
def test_extract_txt():
    from backend.rag.ingestion import extract_text
    text, kind = extract_text("f.txt", b"Hello world.\nThis is a scoring framework.")
    assert kind == "txt"
    assert "scoring framework" in text


def test_extract_rejects_unknown_type():
    from backend.rag.ingestion import IngestionError, extract_text
    with pytest.raises(IngestionError):
        extract_text("f.exe", b"nope")


def test_extract_rejects_empty():
    from backend.rag.ingestion import IngestionError, extract_text
    with pytest.raises(IngestionError):
        extract_text("f.txt", b"")


def test_chunking_produces_chunks_with_metadata():
    from backend.rag.chunking import chunk_text
    big = "Dimension one. " * 400  # long enough to split
    chunks = chunk_text(big, filename="f.txt", user_id="u1", doc_id="d1")
    assert len(chunks) >= 1
    assert chunks[0].metadata["user_id"] == "u1"
    assert chunks[0].metadata["doc_id"] == "d1"
    assert chunks[0].metadata["chunk_index"] == 0


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------
def test_embeddings_dimension():
    from backend.rag.embeddings import embed_texts
    vecs = embed_texts(["hello", "world"])
    assert len(vecs) == 2
    # all-MiniLM-L6-v2 → 384 dims
    assert len(vecs[0]) == 384


# ---------------------------------------------------------------------------
# Vectorstore + retriever — the critical isolation property
# ---------------------------------------------------------------------------
def test_add_and_retrieve_roundtrip():
    from backend.rag.chunking import chunk_text
    from backend.rag.retriever import retrieve_frameworks
    from backend.rag.vectorstore import add_chunks, delete_document_chunks

    user_id = f"testuser_{uuid.uuid4().hex[:8]}"
    doc_id = uuid.uuid4().hex
    chunks = chunk_text(
        "Discovery Quality: score 5 if the rep asks about pain, impact, and timeline.",
        filename="framework.txt", user_id=user_id, doc_id=doc_id,
    )
    added = add_chunks(user_id, chunks)
    assert added == len(chunks)

    result = retrieve_frameworks(user_id, "how do I score discovery?", top_k=3)
    assert "Discovery Quality" in result

    # cleanup
    delete_document_chunks(user_id, doc_id)


def test_cross_user_isolation():
    """A user must never see another user's frameworks."""
    from backend.rag.chunking import chunk_text
    from backend.rag.retriever import EMPTY_KB_MESSAGE, retrieve_frameworks
    from backend.rag.vectorstore import add_chunks, delete_document_chunks

    owner = f"owner_{uuid.uuid4().hex[:8]}"
    stranger = f"stranger_{uuid.uuid4().hex[:8]}"
    doc_id = uuid.uuid4().hex

    add_chunks(owner, chunk_text(
        "SECRET FRAMEWORK: proprietary MEDDIC scoring rubric.",
        filename="secret.txt", user_id=owner, doc_id=doc_id,
    ))

    # Owner can see it
    assert "SECRET FRAMEWORK" in retrieve_frameworks(owner, "meddic rubric", top_k=3)
    # Stranger (empty collection) gets the placeholder, never the owner's data
    stranger_view = retrieve_frameworks(stranger, "meddic rubric", top_k=3)
    assert stranger_view == EMPTY_KB_MESSAGE
    assert "SECRET FRAMEWORK" not in stranger_view

    delete_document_chunks(owner, doc_id)
