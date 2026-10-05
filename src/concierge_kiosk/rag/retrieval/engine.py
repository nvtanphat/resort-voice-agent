"""Primary lexical/dense/hybrid retrieval execution."""
from __future__ import annotations
import json
import logging
import math
import time
import re
import unicodedata
from concierge_kiosk.persistence.sqlite_store import Store
from concierge_kiosk.rag.documents import LANGUAGES
from concierge_kiosk.rag.embedding.base import Embedder, cosine
from concierge_kiosk.rag.embedding.cache import decoded_embedding
from concierge_kiosk.rag.rerank.local import LocalReranker
from concierge_kiosk.rag.retrieval.evidence import evidence_passage, retrieve_parent_context
from concierge_kiosk.rag.text.safety import unsafe_knowledge_text
from concierge_kiosk.rag.text.tokenization import tokens
from concierge_kiosk.rag.grounding.relevance import (candidate_relevant, concrete_facets_supported, evidence_relevant, fts_query,
                                            is_opening_hours_query, query_terms as meaningful_query_terms)
from concierge_kiosk.core.domain_profile import rag_policy as domain_rag_policy
from .policy import (RAGPolicy, Retrieval, abstention_answer, _bounded_query_embedding,
                     _bounded_rerank, _fuse_rerank, _policy_conflict, _deadline_ns, _remaining_ms,
                     _clock_ns, explain_requested)
LOGGER = logging.getLogger(__name__)


def _domain_review(row: dict) -> dict:
    raw = row.get("metadata_json")
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        metadata = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    review = metadata.get("domain_review") if isinstance(metadata, dict) else None
    return dict(review) if isinstance(review, dict) else {}


def _chunk_metadata(row: dict) -> dict:
    raw = row.get("metadata_json")
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return dict(value) if isinstance(value, dict) else {}


def _record_metric(store, metric: str, language: str) -> None:
    """Best-effort retrieval observability must never alter the answer path."""
    try:
        store.metric(metric, language, int(time.time()))
    except Exception as exc:
        LOGGER.debug("retrieval_metric_unavailable metric=%s type=%s", metric, type(exc).__name__)

def retrieve(store: Store, *, property_id: str, language: str, query: str,
             embedder: Embedder | None = None, reranker: LocalReranker | None = None,
             effective_date: str | None = None, today: str | None = None, top_k: int = 3,
             policy: RAGPolicy | None = None,
             expand_parent: bool = False,
             mode: str = 'hybrid',
             entity_ids: tuple[str, ...] = (),
             fact_types: tuple[str, ...] = (),
             fact_context: tuple[str, ...] = (),
             _allow_language_fallback: bool = True,
             _shared_deadline_ns: int | None = None) -> Retrieval:
    policy = policy or RAGPolicy()
    policy.validate()
    if language not in LANGUAGES or not query.strip() or len(query) > 500 or not 1 <= top_k <= 10:
        raise ValueError("Unsupported language, query, or result limit")
    if mode not in {'lexical', 'dense', 'hybrid'} or (mode == 'dense' and embedder is None):
        raise ValueError('Unsupported retrieval configuration')
    shared_deadline_ns = (_shared_deadline_ns if _shared_deadline_ns is not None else
                          _deadline_ns(max(policy.dense_budget_ms, policy.rerank_budget_ms)))
    today = effective_date or today
    if today is None or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', today):
        raise ValueError("Explicit effective_date is required for retrieval")
    candidates: dict[str, dict] = {}
    conflict_candidates: dict[str, dict] = {}
    rankings: list[list[str]] = []
    dense_succeeded = False
    cross_language_dense = False
    semantic_candidate_ids: set[str] = set()
    structured_succeeded = False
    where = ("k.property_id=? AND k.language=? AND k.classification='public' AND k.active=1 "
             "AND k.effective_from<=? AND (k.effective_to IS NULL OR k.effective_to>=?)")
    params = (property_id, language, today, today)
    expression = fts_query(query, language)
    query_terms = meaningful_query_terms(query, language)
    opening_hours_query = is_opening_hours_query(query, language)
    # Guest-supplied numbers are not a candidate-stage hard filter. Party size,
    # child age and natural times such as ``3pm`` may be absent or formatted as
    # ``15:00`` in the approved policy. Numeric truth is enforced by grounding.
    with store.connection() as con:
        lexical = []
        if entity_ids and fact_types:
            entity_placeholders = ','.join('?' for _ in entity_ids)
            fact_placeholders = ','.join('?' for _ in fact_types)
            lexical = con.execute(
                f"SELECT k.* FROM knowledge k WHERE {where} "  # nosec B608  # fragments are validated; values are bound
                f"AND k.entity_id IN ({entity_placeholders}) "
                f"AND k.fact_type IN ({fact_placeholders}) "
                "ORDER BY k.entity_id,k.fact_type,k.fact_context,k.id",
                (*params, *entity_ids, *fact_types),
            ).fetchall()
            if lexical:
                # An explicit entity + configured facet is a stronger key than
                # a semantic score. It is still cited and reauthorized through
                # the normal source/citation path below.
                structured_succeeded = True
                rankings.append([row['id'] for row in lexical])
                candidates.update({row['id']: dict(row) for row in lexical})
                conflict_candidates.update({row['id']: dict(row) for row in lexical})
        if expression and mode != 'dense' and not structured_succeeded:
            lexical = con.execute(
                f"SELECT k.*,bm25(knowledge_fts) AS lexical_score FROM knowledge_fts "  # nosec B608  # constant SQL fragments; all values are bound parameters
                f"JOIN knowledge k ON k.id=knowledge_fts.doc_id AND k.revision=knowledge_fts.revision "
                f"WHERE knowledge_fts MATCH ? AND {where} ORDER BY lexical_score LIMIT ?",
                (expression, *params, policy.lexical_top_k),
            ).fetchall()
            # OR retrieval is recall-friendly but may match one generic keyword
            # in a multi-part question. Don't present it as sufficient evidence.
            def sufficiently_related(row):
                return candidate_relevant(query, language, row['search_text'], row['body'],
                                          row['title'], row['heading'],
                                          threshold=policy.lexical_coverage)
            # Contradiction checking uses the same dynamic relevance gate as
            # candidate selection.  Long voice utterances should not escape or
            # trigger conflict detection merely because they contain filler words.
            for row in lexical:
                if (not unsafe_knowledge_text(row['body']) and
                        evidence_relevant(query, language, row['body'], row['title'], row['heading'],
                                          threshold=policy.lexical_coverage)):
                    conflict_candidates[row['id']] = dict(row)
            lexical = [row for row in lexical if sufficiently_related(row) and
                       not unsafe_knowledge_text(row['body'])]
            rankings.append([r["id"] for r in lexical])
            candidates.update({r["id"]: dict(r) for r in lexical})
        if embedder is not None and mode != 'lexical' and not structured_succeeded:
            # Every candidate is scoped in SQL *before* Python sees its vector.
            try:
                dense_deadline = _deadline_ns(policy.dense_budget_ms)
                rows = con.execute(f"SELECT k.* FROM knowledge k WHERE {where} AND k.embedding_model=? "  # nosec B608  # constant SQL fragments; all values are bound parameters
                                   "AND k.embedding IS NOT NULL LIMIT ?",
                                   (*params, embedder.model_name, policy.dense_max_rows + 1)).fetchall()
                # A multilingual learned encoder can search the configured
                # fallback locales in the same embedding pass when the
                # requested locale has no indexed dense corpus. This preserves
                # the shared deadline and avoids a second dense/reranker turn;
                # lexical rescue remains the only recursive fallback below.
                if not rows and _allow_language_fallback and _clock_ns() < shared_deadline_ns:
                    fallback_order = policy.cross_language_fallback_order or tuple(
                        domain_rag_policy().cross_language_fallback_order)
                    languages = list(dict.fromkeys(
                        [language, *[item for item in fallback_order if item in LANGUAGES]]))
                    placeholders = ','.join('?' for _ in languages)
                    cross_where = where.replace('k.language=?', f'k.language IN ({placeholders})')
                    rows = con.execute(f"SELECT k.* FROM knowledge k WHERE {cross_where} AND k.embedding_model=? "  # nosec B608
                                       "AND k.embedding IS NOT NULL LIMIT ?",
                                       (property_id, *languages, today, today,
                                        embedder.model_name, policy.dense_max_rows + 1)).fetchall()
                    cross_language_dense = bool(rows)
                if len(rows) > policy.dense_max_rows:
                    raise ValueError('Dense corpus exceeds configured in-memory candidate cap')
                remaining_ms = _remaining_ms(dense_deadline)
                if remaining_ms <= 0:
                    raise TimeoutError('Dense retrieval budget exhausted before embedding')
                vector = _bounded_query_embedding(embedder, query, remaining_ms)
                if _clock_ns() > dense_deadline:
                    raise TimeoutError('Dense retrieval budget exceeded after embedding')
                similarities = []
                learned_dense = bool(getattr(embedder, "is_learned", False))
                # Calibrated learned encoders use the configured similarity floor.
                # The deterministic hash fallback is lexical/fuzzy rather than a
                # semantic cosine model, so its safety gate remains lexical and
                # does not pretend that the learned-model threshold applies.
                dense_threshold = policy.min_dense_similarity if learned_dense else 0.0
                for row in rows:
                    if _clock_ns() > dense_deadline:
                        raise TimeoutError('Dense retrieval budget exceeded while ranking')
                    if unsafe_knowledge_text(row['body']):
                        continue
                    # The deterministic hash fallback is intentionally lexical/fuzzy,
                    # so keep the conservative lexical evidence gate for it. A learned
                    # multilingual encoder must be allowed to retrieve paraphrases and
                    # synonyms that share no literal tokens with the approved passage.
                    if not learned_dense and not candidate_relevant(
                            query, language, row['search_text'], row['body'], row['title'], row['heading'],
                            threshold=policy.lexical_coverage):
                        continue
                    similarities.append((cosine(vector, decoded_embedding(row['embedding'])), row))
                dense = sorted(similarities, key=lambda pair: (-pair[0], pair[1]['id']))[:policy.dense_top_k]
                # Threshold is conservative and must be calibrated with real queries.
                for score, row in dense:
                    # Conflict detection is deliberately stricter than candidate
                    # discovery. A semantically similar sibling document (for
                    # example a promotion for the same restaurant) must not be
                    # treated as a conflicting assertion about opening hours.
                    # Only evidence that is directly relevant to the query facet
                    # participates in the conflict set.
                    if (score > 0 and score >= dense_threshold and
                            evidence_relevant(query, language, row['body'], row['title'], row['heading'],
                                              threshold=policy.lexical_coverage)):
                        conflict_candidates[row['id']] = dict(row)
                valid_dense = [(score, row) for score, row in dense
                               if score > 0 and score >= dense_threshold
                               and concrete_facets_supported(
                                   query, row['body'], row['title'], row['heading'])]
                # sqlite3.Row is immutable; copy before carrying the score into
                # the answerability gate.
                dense_candidates = []
                for score, row in valid_dense:
                    candidate = dict(row)
                    # Keep the calibrated semantic score with the candidate so
                    # the final answerability gate can distinguish a genuine
                    # paraphrase from a merely adjacent lexical topic.
                    candidate['_dense_similarity'] = float(score)
                    dense_candidates.append((score, candidate))
                valid_dense = dense_candidates
                if learned_dense:
                    semantic_candidate_ids.update(row["id"] for score, row in valid_dense)
                rankings.append([row["id"] for score, row in valid_dense])
                candidates.update({row["id"]: dict(row) for score, row in valid_dense})
                dense_succeeded = bool(valid_dense)
            except (RuntimeError, OSError, ValueError, TypeError, TimeoutError) as exc:
                # Degrade to lexical but make the model failure observable.
                LOGGER.warning('Dense retrieval unavailable: %s', type(exc).__name__)
    scores: dict[str, float] = {}
    for rank_list in rankings:
        for rank, key in enumerate(dict.fromkeys(rank_list), start=1):
            scores[key] = scores.get(key, 0) + 1 / (policy.rrf_k + rank)
    ordered = sorted(scores, key=lambda key: (-scores[key], key))[:policy.fusion_top_k]
    # A single candidate cannot benefit from a cross-encoder. A clear lexical
    # winner with no dense competition does not justify an expensive reranker.
    high_confidence_lexical = bool(not dense_succeeded and len(lexical) > 1 and
        len(query_terms) >= 2 and
        query_terms.issubset({unicodedata.normalize('NFC', word) for word in tokens(lexical[0]['search_text'], language=language, limit=None, stem=True)}) and
        len(query_terms.intersection(unicodedata.normalize('NFC', word) for word in tokens(lexical[1]['search_text'], language=language, limit=None, stem=True)))
        <= len(query_terms) - 2) if expression else False
    rerank_status = 'not_run'
    rerank_ms: float | None = None
    # Conflict is a correctness gate, not a ranking feature. Do it before
    # optional cross-encoder work so contradictory facts fail closed cheaply.
    if _policy_conflict(list(conflict_candidates.values()), query):
        LOGGER.warning('Conflicting active knowledge assertions; abstaining')
        return Retrieval('conflict_abstention', [], abstention_answer(language),
                         rerank_status='not_run', evidence_quality='conflicting')
    entity_id_set = set(entity_ids)
    fact_type_set = set(fact_types)
    fact_context_set = set(fact_context)
    structured_match = {
        key for key in ordered
        if entity_id_set and fact_type_set
        and candidates[key].get('entity_id') in entity_id_set
        and candidates[key].get('fact_type') in fact_type_set
        and (not fact_context_set or candidates[key].get('fact_context') in fact_context_set)
    }
    should_rerank = (reranker is not None and len(ordered) >= policy.rerank_min_candidates
                     and not high_confidence_lexical and not structured_succeeded)
    if should_rerank:
        rerank_status = 'error'
        rerank_subset = ordered[:policy.rerank_top_k]
        rerank_started = time.perf_counter()
        try:
            rerank_deadline = _deadline_ns(policy.rerank_budget_ms)

            def rerank_text(row: dict) -> str:
                if policy.rerank_input == 'context_text' and row.get('context_text'):
                    return str(row['context_text'])
                return str(row['body'])

            rerank_scores = _bounded_rerank(
                reranker, query, [rerank_text(candidates[key]) for key in rerank_subset],
                _remaining_ms(rerank_deadline), max_length=policy.rerank_max_length)
            if _clock_ns() > rerank_deadline:
                raise TimeoutError('Reranker response deadline exceeded')
            if (len(rerank_scores) != len(rerank_subset) or
                    not all(math.isfinite(float(value)) for value in rerank_scores)):
                raise ValueError('Invalid reranker output')
            rank_map = dict(zip(rerank_subset, map(float, rerank_scores)))
            ordered = _fuse_rerank(
                rerank_subset, scores, rank_map, structured_match,
                alpha=policy.rerank_fusion_alpha, bonus=policy.rerank_metadata_bonus,
            ) + ordered[len(rerank_subset):]
            rerank_status = 'ok'
        except TimeoutError as exc:
            rerank_status = 'busy' if 'occupied' in str(exc).lower() else 'timeout'
        except Exception:
            rerank_status = 'error'
        finally:
            rerank_ms = (time.perf_counter() - rerank_started) * 1000.0
        if rerank_status != 'ok':
            LOGGER.warning('Reranker %s; keeping fused RRF order', rerank_status)
            _record_metric(store, f'rag.reranker.{rerank_status}', language)
            if policy.rerank_on_failure == 'abstain':
                return Retrieval('rerank_unavailable', [], abstention_answer(language),
                                 rerank_status=rerank_status, evidence_quality='ambiguous',
                                 rerank_ms=rerank_ms)
    sources = [{"chunk_id": key, "source_id": candidates[key]["source"],
                "revision": candidates[key]["revision"], "language": candidates[key]["language"],
                "title": candidates[key]["title"],
                "heading": candidates[key]["heading"], "domain": candidates[key]["domain"],
                "entity_id": candidates[key].get("entity_id", ""),
                "fact_type": candidates[key].get("fact_type", ""),
                "fact_context": candidates[key].get("fact_context", ""),
                "canonical_fact_id": candidates[key].get("canonical_fact_id", ""),
                "chunk_kind": _chunk_metadata(candidates[key]).get("chunk_kind", "fact"),
                "entity_card": bool(_chunk_metadata(candidates[key]).get("entity_card", False)),
                "context_text": candidates[key].get("context_text", ""),
                "dense_similarity": candidates[key].get("_dense_similarity"),
                "domain_review": _domain_review(candidates[key]),
                "parent_id": candidates[key]["parent_id"], "section_id": candidates[key]["section_id"],
                "section_ordinal": candidates[key]["section_ordinal"],
                "content": evidence_passage(
                    candidates[key]["body"], query, language=language,
                    max_chars=policy.max_evidence_chars,
                    # Opening-hours candidates have already passed the strict
                    # relevance gate, which requires a concrete clock range and
                    # a matching subject label. The child fact line may therefore
                    # contain only the locale-native facet label (Schedule/일정/
                    # 活动时间) rather than repeating the guest's subject words.
                    require_term_overlap=(key not in semantic_candidate_ids and
                                          not opening_hours_query)),
                "score": round(scores[key], 6),
                **({"requested_language": language}
                   if candidates[key]["language"] != language else {})}
               for key in ordered[:top_k]]
    sources = [source for source in sources if source['content'] and
               not unsafe_knowledge_text(source['content'])]
    if not sources:
        # Cross-language fallback is deliberately second-pass: the requested
        # locale always wins.  With a learned multilingual embedder this can
        # recover an English-only fact from a Vietnamese/Korean/Chinese query;
        # lexical mode can still recover shared proper nouns such as venue names.
        if _allow_language_fallback:
            if _clock_ns() >= shared_deadline_ns:
                return Retrieval('fallback_deadline', [], abstention_answer(language),
                                 rerank_status=rerank_status, rerank_ms=rerank_ms)
            fallback_order = policy.cross_language_fallback_order or tuple(
                domain_rag_policy().cross_language_fallback_order)
            fallback_languages = [candidate for candidate in fallback_order
                                  if candidate in LANGUAGES and candidate != language]
            for fallback_language in fallback_languages:
                fallback = retrieve(
                    store, property_id=property_id, language=fallback_language, query=query,
                    # Fallback is a lexical rescue under the same deadline;
                    # never multiply dense/rerank work after a miss.
                    embedder=None, reranker=None, effective_date=today, top_k=top_k,
                    policy=policy, expand_parent=expand_parent, mode='lexical',
                    _allow_language_fallback=False, _shared_deadline_ns=shared_deadline_ns,
                )
                if fallback.sources:
                    for source in fallback.sources:
                        source['requested_language'] = language
                    return Retrieval(f'cross_language_{fallback.mode}', fallback.sources, fallback.answer,
                                     rerank_status=fallback.rerank_status,
                                     evidence_quality=fallback.evidence_quality,
                                     rerank_ms=fallback.rerank_ms)
        return Retrieval('structured' if structured_succeeded else
                         (('cross_language_dense' if cross_language_dense else 'dense')
                          if mode == 'dense' else 'hybrid' if dense_succeeded else 'lexical'),
                         [], abstention_answer(language), rerank_status=rerank_status,
                         evidence_quality='unsupported', rerank_ms=rerank_ms)
    if expand_parent and explain_requested(query, language):
        for source in sources:
            # Parent is *optional* context. It cannot change the child citation
            # or create a business/LLM claim; generation still verifies child spans.
            source['parent_context'] = retrieve_parent_context(
                store, property_id=property_id, language=language,
                source_id=source['source_id'], revision=source['revision'],
                effective_date=today, parent_id=source.get('parent_id', ''),
                section_id=source.get('section_id', ''), heading=source['heading'], max_chars=2400)
    return Retrieval('structured' if structured_succeeded else
                     (('cross_language_dense' if cross_language_dense else 'dense')
                      if mode == 'dense' else 'hybrid' if dense_succeeded else 'lexical'),
                     sources, sources[0]['content'], rerank_status=rerank_status,
                     evidence_quality='verified', rerank_ms=rerank_ms)
