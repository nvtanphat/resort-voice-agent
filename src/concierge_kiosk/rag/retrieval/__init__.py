"""Bounded RAG retrieval split into policy, engine and contextual lookup.

The public retrieval API is re-exported here so callers do not need to know the
internal package layout. Two bounded inference helpers remain re-exported for
backward-compatible technical regression tests and operational diagnostics.
"""
from .policy import (
    RAGPolicy,
    Retrieval,
    abstention_answer,
    _bounded_query_embedding,
    _bounded_rerank,
    _fuse_rerank,
    _EMBED_SLOT,
    _RERANK_SLOT,
)
from .engine import retrieve
from .context import retrieve_context, retrieve_localized_anchor

__all__ = [
    "RAGPolicy",
    "Retrieval",
    "abstention_answer",
    "retrieve",
    "retrieve_context",
    "retrieve_localized_anchor",
    "_bounded_query_embedding",
    "_bounded_rerank",
    "_fuse_rerank",
    "_EMBED_SLOT",
    "_RERANK_SLOT",
]
