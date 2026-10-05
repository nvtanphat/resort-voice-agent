"""Deprecated compatibility shim: import from the focused ``rag`` submodules.

Kept only for modules owned by in-flight work (voice agent, semantic router).
Remove once they import ``rag.text.safety`` and ``rag.embedding.base`` directly.
"""
from .embedding.base import cosine
from .text.safety import unsafe_knowledge_text

__all__ = ["cosine", "unsafe_knowledge_text"]
