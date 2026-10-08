"""Validation of the ``rag`` section of the agent domain profile."""
from __future__ import annotations

from typing import Any

from .common import compile_regex, validate_language_keys


def validate_rag(payload: dict[str, Any], languages: set[str]) -> None:
    rag = payload["rag"]
    fallback_order = rag["cross_language_fallback_order"]
    if len(fallback_order) != len(set(fallback_order)) or not set(fallback_order).issubset(languages):
        raise ValueError("RAG cross-language fallback order contains unsupported or duplicate languages")
    compile_regex(rag["opening_hours"]["time_range_pattern"], label="rag.opening_hours.time_range_pattern")
    seen: dict[str, str] = {}
    for facet, fact_types in rag["facet_fact_types"].items():
        for fact_type in fact_types:
            if seen.setdefault(fact_type, facet) != facet:
                raise ValueError(f"rag.facet_fact_types: {fact_type} is in both {seen[fact_type]} and {facet}")
    tokenization = rag["tokenization"]
    segmentation = tokenization["segmentation"]
    if not set(segmentation).issubset(languages):
        raise ValueError("rag.tokenization.segmentation contains unsupported languages")
    if any(mode not in {"whitespace", "trigram", "kiwi", "jieba"}
           for mode in segmentation.values()):
        raise ValueError("rag.tokenization.segmentation contains an unsupported mode")
    if not isinstance(tokenization["trigram_size"], int) or not 2 <= tokenization["trigram_size"] <= 5:
        raise ValueError("rag.tokenization.trigram_size is invalid")
    for language, suffixes in tokenization["particle_suffixes"].items():
        if language not in languages or any(not isinstance(item, str) or not item.strip()
                                           for item in suffixes):
            raise ValueError("rag.tokenization.particle_suffixes is invalid")
    for language, sizes in tokenization["compatibility_ngram_sizes"].items():
        if (language not in languages or not isinstance(sizes, list) or
                any(isinstance(size, bool) or not isinstance(size, int) or not 2 <= size <= 5
                    for size in sizes)):
            raise ValueError("rag.tokenization.compatibility_ngram_sizes is invalid")
    for language, cleanup in tokenization["cjk_query_cleanup"].items():
        if language not in languages:
            raise ValueError(f"rag.tokenization.cjk_query_cleanup contains unsupported language: {language}")
        if any(not isinstance(item, str) or not item.strip()
               for values in cleanup.values() for item in values):
            raise ValueError(f"rag.tokenization.cjk_query_cleanup.{language} contains an invalid term")
    budgets = rag["grounding_budgets"]
    if budgets["min_quote_chars"] > budgets["max_quote_chars"]:
        raise ValueError("RAG grounding quote bounds are inverted")
    if budgets["short_context_chars"] > budgets["medium_context_chars"]:
        raise ValueError("RAG grounding context tiers are inverted")
    if budgets["medium_context_chars"] > budgets["max_evidence_context_chars"]:
        raise ValueError("RAG grounding medium context exceeds maximum")
    if set(budgets["evidence_budget_chars"]) != set(budgets["output_budget_tokens"]):
        raise ValueError("RAG grounding evidence/output budget types must match")
    if budgets["semantic_claim_text_min_chars"] > budgets["semantic_claim_text_max_chars"]:
        raise ValueError("RAG semantic claim text bounds are inverted")
    if budgets["semantic_quote_min_chars"] > budgets["semantic_quote_max_chars"]:
        raise ValueError("RAG semantic quote bounds are inverted")
