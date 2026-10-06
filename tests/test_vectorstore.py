from __future__ import annotations

from pathlib import Path

import pytest

from concierge_kiosk.rag.vectorstore import (
    ChromaVectorStore,
    FaissVectorStore,
    VectorRecord,
    VectorStoreError,
)
from concierge_kiosk.persistence.sqlite_store import Store
from concierge_kiosk.rag.embedding.cache import decoded_embedding
from concierge_kiosk.rag.ingestion import ingest_text
from concierge_kiosk.rag.retrieval.engine import retrieve


_DOC_A = """---
document_id: doc-a
property_id: P
title: Pasta House
language: en
classification: public
effective_from: 2026-01-01
domain: dining
---
# Pasta House
Italian cuisine is served for dinner.
"""


class _ProbeEmbedder:
    model_name = "probe"
    is_learned = True

    def encode_passage(self, text: str):
        return [1.0, 0.0]

    def encode_query(self, text: str):
        return [1.0, 0.0]


def _records() -> list[VectorRecord]:
    return [
        VectorRecord("doc-a::r1", (1.0, 0.0), {
            "doc_id": "doc-a", "revision": "r1", "property_id": "P",
            "language": "en", "classification": "public", "active": 1,
            "embedding_model": "m", "effective_from": "2026-01-01",
            "effective_to": "", "release_version": 1,
        }),
        VectorRecord("doc-b::r1", (0.0, 1.0), {
            "doc_id": "doc-b", "revision": "r1", "property_id": "P",
            "language": "vi", "classification": "public", "active": 1,
            "embedding_model": "m", "effective_from": "2026-01-01",
            "effective_to": "", "release_version": 2,
        }),
    ]


@pytest.mark.parametrize("backend", ["faiss", "chroma"])
def test_vector_backend_persists_filters_and_delete_release(tmp_path: Path, backend: str):
    path = tmp_path / backend
    if backend == "faiss":
        store = FaissVectorStore(path, name="knowledge")
    else:
        pytest.importorskip("chromadb")
        store = ChromaVectorStore(path, collection="knowledge")
    try:
        assert store.upsert(_records()) == 2
        matches = store.query([1.0, 0.0], k=2, filters={
            "property_id": "P", "language": "en", "active": 1,
            "effective_on": "2026-10-05",
        })
        assert [item.key for item in matches] == ["doc-a::r1"]
        assert store.stats()["count"] == 2
    finally:
        store.close()
    if backend == "faiss":
        reopened = FaissVectorStore(path, name="knowledge")
    else:
        reopened = ChromaVectorStore(path, collection="knowledge")
    try:
        assert reopened.delete_release(1) == 1
        assert reopened.stats()["count"] == 1
    finally:
        reopened.close()


def test_vector_record_rejects_unscoped_metadata():
    with pytest.raises(ValueError, match="Unsupported vector filter"):
        VectorRecord("x", (1.0,), {"guest_name": "must-not-index"})


def test_faiss_rejects_corrupt_sidecar(tmp_path: Path):
    path = tmp_path / "vectors"
    path.mkdir()
    (path / "knowledge.index").write_bytes(b"not-an-index")
    (path / "knowledge.json").write_text('{"schema_version": 1, "records": []}', encoding="utf-8")
    with pytest.raises(VectorStoreError):
        FaissVectorStore(path, name="knowledge")


def test_retrieval_reauthorizes_vector_match_through_sqlite(tmp_path: Path):
    embedder = _ProbeEmbedder()
    sqlite_store = Store(tmp_path / "knowledge.sqlite3")
    ingest_text(sqlite_store, _DOC_A, property_id="P", embedder=embedder)
    with sqlite_store.connection() as con:
        row = con.execute("SELECT * FROM knowledge WHERE source='doc-a'").fetchone()
    vector_store = FaissVectorStore(tmp_path / "vectors", name="knowledge")
    vector_store.upsert([VectorRecord(
        "doc-a::r1", tuple(decoded_embedding(row["embedding"])), {
            "doc_id": row["id"], "revision": row["revision"], "property_id": row["property_id"],
            "language": row["language"], "classification": row["classification"],
            "active": row["active"], "embedding_model": row["embedding_model"],
            "entity_id": row["entity_id"], "fact_type": row["fact_type"],
            "fact_context": row["fact_context"], "effective_from": row["effective_from"],
            "effective_to": row["effective_to"] or "", "release_version": 1,
        })])
    try:
        result = retrieve(sqlite_store, property_id="P", language="en",
                          query="where can I eat pasta?", effective_date="2026-10-05",
                          mode="dense", top_k=3, embedder=embedder,
                          vector_store=vector_store)
        assert result.sources and result.sources[0]["source_id"] == "doc-a"
    finally:
        vector_store.close()
