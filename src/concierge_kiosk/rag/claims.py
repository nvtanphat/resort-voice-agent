"""Deprecated compatibility shim: import from ``rag.grounding.claims``.

Kept only for ``agent/understanding/semantic.py`` (in-flight work); remove with it.
"""
from .grounding.claims import exact_span, extract_claims

__all__ = ["exact_span", "extract_claims"]
