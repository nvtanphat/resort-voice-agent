"""Versioned and atomic, semantically chunked hotel-manual ingestion."""
from .bundle import ingest_bundle
from .chunking import canonicalize_markdown, chunk_markdown, chunk_sections
from .document import ingest_text
from .policy import CHUNK_POLICY, chunk_policy_hash

__all__ = ["CHUNK_POLICY", "canonicalize_markdown", "chunk_markdown", "chunk_policy_hash",
           "chunk_sections", "ingest_bundle", "ingest_text"]
