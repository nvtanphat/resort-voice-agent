"""Bounded, authorized lexical/dense/hybrid retrieval and grounding."""
from __future__ import annotations
import logging
import math
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from threading import BoundedSemaphore
from dataclasses import dataclass
from concierge_kiosk.rag.common import (LANGUAGES, Embedder, LocalReranker, cosine, decoded_embedding,
                     evidence_passage, query_embedding,
                     retrieve_parent_context, searchable, tokens, unsafe_knowledge_text)
from concierge_kiosk.rag.relevance import evidence_relevant, fts_query, query_terms as meaningful_query_terms
from concierge_kiosk.i18n import text as i18n_text
from concierge_kiosk.core.domain_profile import nlu_policy, rag_policy as domain_rag_policy
LOGGER = logging.getLogger(__name__)

# A timed-out native cross-encoder cannot be safely killed in a Python thread.
# Keep at most one outstanding worker; later requests immediately use verified
# RRF retrieval rather than accumulating an unbounded queue of zombie reranks.
_RERANK_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix='rag-rerank')
_RERANK_SLOT = BoundedSemaphore(1)
_EMBED_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix='rag-embed')
_EMBED_SLOT = BoundedSemaphore(1)


def _clock_ns() -> int:
    """High-resolution monotonic clock used for retrieval deadlines."""
    return time.perf_counter_ns()


def _deadline_ns(timeout_ms: int) -> int:
    return _clock_ns() + int(timeout_ms) * 1_000_000


def _remaining_ms(deadline_ns: int) -> int:
    remaining = deadline_ns - _clock_ns()
    if remaining <= 0:
        return 0
    # Future.result accepts seconds; round up so the executor timeout does not
    # expire *before* our authoritative absolute deadline.
    return max(1, (remaining + 999_999) // 1_000_000)


def _bounded_query_embedding(embedder: Embedder, query: str, timeout_ms: int) -> list[float]:
    """Bound native embedding without freeing the permit before its worker ends.

    An inference thread cannot be force-killed. A late result remains isolated;
    later requests fall back to authorized lexical search, not a zombie queue.
    """
    if not _EMBED_SLOT.acquire(blocking=False):
        raise TimeoutError('Embedding engine occupied by an earlier request')
    try:
        future = _EMBED_EXECUTOR.submit(query_embedding, embedder, query)
    except BaseException:
        _EMBED_SLOT.release()
        raise
    future.add_done_callback(lambda _done: _EMBED_SLOT.release())
    try:
        return future.result(timeout=max(0.001, timeout_ms / 1000))
    except FutureTimeout as exc:
        future.cancel()
        raise TimeoutError('Dense query embedding response deadline exceeded') from exc


def _bounded_rerank(reranker: LocalReranker, query: str, bodies: list[str],
                    timeout_ms: int, *, max_length: int) -> list[float]:
    if not _RERANK_SLOT.acquire(blocking=False):
        raise TimeoutError('Reranker occupied by an earlier request')
    try:
        future = _RERANK_EXECUTOR.submit(
            reranker.score, query, bodies, max_length=max_length,
        )
    except Exception:
        _RERANK_SLOT.release()
        raise
    future.add_done_callback(lambda _finished: _RERANK_SLOT.release())
    try:
        return future.result(timeout=timeout_ms / 1000)
    except FutureTimeout as exc:
        future.cancel()
        raise TimeoutError('Reranker hard response deadline exceeded') from exc

@dataclass(frozen=True)
class RAGPolicy:
    """Explicit tunable retrieval policy; defaults require offline benchmark review."""
    lexical_top_k: int = 12
    dense_top_k: int = 12
    fusion_top_k: int = 10
    rrf_k: int = 60
    min_dense_similarity: float = 0.70
    lexical_coverage: float = 0.60
    max_evidence_chars: int = 750
    rerank_top_k: int = 5
    rerank_min_candidates: int = 2
    rerank_max_length: int = 128
    rerank_input: str = 'context_text'
    rerank_fusion_alpha: float = 0.5
    rerank_metadata_bonus: float = 0.2
    rerank_on_failure: str = 'keep_rrf'
    dense_max_rows: int = 5000
    dense_budget_ms: int = 750
    rerank_budget_ms: int = 500
    cross_language_fallback_order: tuple[str, ...] = ()

    def validate(self) -> None:
        # These are SQL LIMITs, evidence string slices and rank offsets. A
        # fractional/bool value can otherwise pass numeric range checks but
        # fail later at the database or slicing boundary.
        integral = (self.lexical_top_k, self.dense_top_k, self.fusion_top_k,
                    self.rrf_k, self.max_evidence_chars, self.rerank_top_k,
                    self.rerank_min_candidates, self.rerank_max_length,
                    self.dense_max_rows,
                    self.dense_budget_ms, self.rerank_budget_ms)
        if any(type(value) is not int for value in integral):
            raise ValueError('RAG limits and budgets must be integers')
        if (not 1 <= self.lexical_top_k <= 100 or not 1 <= self.dense_top_k <= 100
                or not 1 <= self.fusion_top_k <= 100 or not 1 <= self.rrf_k <= 1000
                or not 0.0 <= self.min_dense_similarity <= 1.0
                or not 0.0 < self.lexical_coverage <= 1.0
                or not 100 <= self.max_evidence_chars <= 3000
                or not 2 <= self.rerank_top_k <= 10
                or not 2 <= self.rerank_min_candidates <= 20
                or not 32 <= self.rerank_max_length <= 512
                or not 10 <= self.dense_max_rows <= 100000
                or not 1 <= self.dense_budget_ms <= 30000
                or not 1 <= self.rerank_budget_ms <= 30000):
            raise ValueError('Invalid RAG retrieval policy')
        if self.rerank_input not in {'context_text', 'body'}:
            raise ValueError('Invalid reranker input field')
        if not 0.0 <= self.rerank_fusion_alpha <= 1.0:
            raise ValueError('Invalid reranker fusion alpha')
        if not 0.0 <= self.rerank_metadata_bonus <= 1.0:
            raise ValueError('Invalid reranker metadata bonus')
        if self.rerank_on_failure not in {'keep_rrf', 'abstain'}:
            raise ValueError('Invalid reranker failure policy')
        if len(self.cross_language_fallback_order) != len(set(self.cross_language_fallback_order)):
            raise ValueError('Duplicate RAG cross-language fallback language')


@dataclass
class Retrieval:
    mode: str
    sources: list[dict]
    answer: str
    # Retrieval quality is explicit so callers and telemetry cannot confuse a
    # best-effort RRF ranking with a reranked, verified candidate set.
    rerank_status: str = 'not_configured'
    evidence_quality: str = 'verified'
    rerank_ms: float | None = None


def _fuse_rerank(subset: list[str], rrf: dict[str, float], rerank: dict[str, float],
                 structured_match: set[str], *, alpha: float, bonus: float) -> list[str]:
    """Fuse normalized RRF and cross-encoder scores deterministically."""
    if not subset:
        return []
    if any(key not in rrf for key in subset) or any(key not in rerank for key in subset):
        raise ValueError('Reranker fusion scores do not cover the candidate subset')

    def minmax(values: dict[str, float]) -> dict[str, float]:
        lo, hi = min(values.values()), max(values.values())
        return {key: (value - lo) / (hi - lo) if hi > lo else 1.0
                for key, value in values.items()}

    rrf_normalized = minmax({key: rrf[key] for key in subset})
    rerank_normalized = minmax({key: rerank[key] for key in subset})
    final = {
        key: alpha * rrf_normalized[key]
        + (1.0 - alpha) * rerank_normalized[key]
        + (bonus if key in structured_match else 0.0)
        for key in subset
    }
    return sorted(subset, key=lambda key: (-final[key], -rrf[key], key))



def abstention_answer(language: str) -> str:
    return i18n_text('knowledge.abstain', language)

_EXPLAIN_BY_LANGUAGE = {
    language: re.compile(pattern, re.I)
    for language, pattern in domain_rag_policy().explain_patterns.items()
}
_EXPLAIN = _EXPLAIN_BY_LANGUAGE.get('en', re.compile(r'(?!)'))
_NEGATION = re.compile('|'.join(nlu_policy().intent['negation_patterns'].values()), re.I)


def explain_requested(query: str, language: str) -> bool:
    pattern = _EXPLAIN_BY_LANGUAGE.get(language)
    return bool(pattern and pattern.search(query))


def _policy_conflict(rows: list[dict], query: str) -> bool:
    """Abstain on identical policy assertions with incompatible numbers or polarity.

    This high-precision detector deliberately does not infer that every pair of
    different hotel facts conflicts. It is not a semantic contradiction model.
    """
    signatures: dict[tuple[str, str, str], tuple[tuple[str, ...], bool]] = {}
    structured: dict[tuple[str, str, str], tuple[str, str]] = {}
    query_terms = {term for term in tokens(query, limit=None, stem=True) if not any(c.isdigit() for c in term)}
    for row in rows:
        entity_id, fact_type = row.get('entity_id') or '', row.get('fact_type') or ''
        if entity_id and fact_type:
            # Canonical facts carry their identity. Two assertions conflict only
            # for the same entity, attribute and context; text similarity would
            # equate "Ballroom 1"/"Ballroom 2" once digits are masked.
            key = (entity_id, fact_type, row.get('fact_context') or '')
            fact_id = row.get('canonical_fact_id') or row.get('id') or ''
            value = searchable(row['body'].split('**:', 1)[-1])
            previous_fact = structured.get(key)
            if previous_fact is not None and previous_fact[0] != fact_id and previous_fact[1] != value:
                return True
            structured[key] = (fact_id, value)
            continue
        # Compare *sentences*, not unrelated paragraphs of the same hotel manual.
        for sentence in re.split(r'(?<=[.!?。！？])\s+|\n+', row['body']):
            words = set(tokens(sentence, limit=None, stem=True))
            if query_terms and len(words & query_terms) < min(2, len(query_terms)):
                continue
            values = tuple(re.findall(r'\d+(?:[.,:/-]\d+)*', sentence))
            negative = bool(_NEGATION.search(sentence))
            normalized = searchable(sentence)
            normalized = re.sub(r'\d+(?:[.,:/-]\d+)*', '<number>', normalized)
            normalized = _NEGATION.sub('', normalized)
            normalized = re.sub(r'\s+', ' ', normalized).strip(' .:;')
            # Context qualifiers in the approved label matter. "High season"
            # and "Low season" can legitimately carry different hours even when
            # the sentence body is otherwise identical.  Only compare claims
            # within the same subject/facet scope.
            # Digits stay in the scope: "Ballroom 1" and "Ballroom 2" are
            # different subjects, not one subject with two values.
            scope = searchable(f"{row.get('title', '')} {row.get('heading', '')}")
            scope = re.sub(r'\s+', ' ', scope).strip(' .:;')
            key = (row['domain'], scope, normalized)
            previous = signatures.get(key)
            if previous is not None and (values != previous[0] or negative != previous[1]):
                return True
            signatures[key] = (values, negative)
    return False
