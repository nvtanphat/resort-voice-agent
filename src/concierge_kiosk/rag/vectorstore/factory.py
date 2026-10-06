"""Create a configured vector backend without contacting a network service."""
from __future__ import annotations

from pathlib import Path
from .base import VectorStore, VectorStoreError
from .chroma import ChromaVectorStore
from .faiss import FaissVectorStore


def open_vector_store(*, backend: str, path: str | Path,
                      collection: str = "knowledge") -> VectorStore:
    value = str(backend or "legacy").strip().lower()
    if value == "chroma":
        return ChromaVectorStore(path, collection=collection)
    if value == "faiss":
        return FaissVectorStore(path, name=collection)
    raise VectorStoreError(f"Unsupported dense vector backend: {backend}")
