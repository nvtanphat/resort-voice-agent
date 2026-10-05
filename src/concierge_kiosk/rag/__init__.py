"""Public interface for property-scoped ingestion, retrieval, and index maintenance."""
from .common import (
    LANGUAGES, DOMAINS, Embedder, LocalEmbedder, OllamaEmbedder, LocalReranker,
    document_domain, unsafe_knowledge_text, semantic_parent_id, searchable,
    tokens, fts_expression, query_embedding, evidence_passage,
    retrieve_parent_context, cosine, validate_ollama_manifest,
)
from .ingestion import chunk_markdown, ingest_text, ingest_bundle
from .retrieval import RAGPolicy, Retrieval, retrieve, retrieve_context, retrieve_localized_anchor
from .index import validate_knowledge_index, rebuild_knowledge_index

__all__ = [
    "LANGUAGES", "DOMAINS", "Embedder", "LocalEmbedder", "OllamaEmbedder", "LocalReranker",
    "document_domain", "unsafe_knowledge_text", "semantic_parent_id",
    "searchable", "tokens", "fts_expression", "query_embedding",
    "evidence_passage", "retrieve_parent_context", "cosine", "validate_ollama_manifest", "chunk_markdown",
    "ingest_text", "ingest_bundle", "RAGPolicy", "Retrieval", "retrieve",
    "retrieve_context", "retrieve_localized_anchor", "validate_knowledge_index", "rebuild_knowledge_index",
]
