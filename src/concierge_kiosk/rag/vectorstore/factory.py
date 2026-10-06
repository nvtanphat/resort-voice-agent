"""Create a configured vector backend without contacting a network service."""
from __future__ import annotations

from pathlib import Path
from .base import VectorStore
from .faiss import FaissVectorStore


def open_vector_store(*, path: str | Path, collection: str = "knowledge") -> VectorStore:
    """Open the only supported dense index implementation."""
    return FaissVectorStore(path, name=collection)
