"""Pluggable, local dense-vector stores for governed retrieval.

The vector index is an acceleration/indexing layer only.  SQLite remains the
source of truth for document content, publication state and citations.
"""

from .base import (
    VectorMatch,
    VectorRecord,
    VectorStore,
    VectorStoreError,
    validate_filter,
)
from .chroma import ChromaVectorStore
from .faiss import FaissVectorStore
from .factory import open_vector_store

__all__ = [
    "ChromaVectorStore",
    "FaissVectorStore",
    "VectorMatch",
    "VectorRecord",
    "VectorStore",
    "VectorStoreError",
    "open_vector_store",
    "validate_filter",
]
