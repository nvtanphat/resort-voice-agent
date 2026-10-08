"""Source-grounded emergency, knowledge and planning answer services."""
from __future__ import annotations

import sqlite3
import time
import re
from dataclasses import dataclass
from typing import Callable

from concierge_kiosk.agent.understanding.intent import (
    emergency_response, normalize_intent_text, EMERGENCY_TEXT,
)
from concierge_kiosk.agent.orchestration.grounding import grounded_response
from concierge_kiosk.agent.understanding.domain_nlu import EMERGENCY_CONTACTS
from concierge_kiosk.agent.understanding.semantic import semantic_grounded_response, SemanticResult
from concierge_kiosk.agent.tools.planning import draft_plan
from concierge_kiosk.agent.tools.scheduling import approved_schedule, ScheduleUnavailable
from concierge_kiosk.domain.entity_resolver import record_alias_matches
from concierge_kiosk.domain.entity_resolver import property_entity_matches
from concierge_kiosk.core.structured_loader import load_structured_dataset
from concierge_kiosk.agent.core.tool_contracts import no_evidence_handoff_details
from concierge_kiosk.core.domain_profile import ui_policy
from concierge_kiosk.core.context_labels import context_terms
from concierge_kiosk.rag.grounding.citations import bind_citations, retain_live_semantic_claims
from concierge_kiosk.rag.grounding.claims import extract_claims
from concierge_kiosk.rag.retrieval import (
    Retrieval, abstention_answer, retrieve, retrieve_context, retrieve_localized_anchor,
)

from concierge_kiosk.rag.grounding.relevance import answerable
from .recovery import load_support_directory, recovery_metadata
from concierge_kiosk.i18n import text as i18n_text

_PRESENTATION_LIMITS = ui_policy().presentation_limits


def _mentioned_contexts(query: str, language: str) -> tuple[str, ...]:
    surface = normalize_intent_text(query, language)
    found = []
    for context, by_language in context_terms().items():
        aliases = by_language.get(language, ())
        if any(normalize_intent_text(alias, language) in surface for alias in aliases):
            found.append(context)
    return tuple(found)


def _filter_context_sources(sources: list[dict], query: str, language: str) -> tuple[list[dict], tuple[str, ...]]:
    """Keep only facts for an explicitly named fact context."""
    contexts = _mentioned_contexts(query, language)
    if not contexts:
        return sources, ()
    allowed = set(contexts)
    return [source for source in sources
            if isinstance(source, dict) and source.get('fact_context') in allowed], contexts



def _no_evidence_handoff_suggestion(query: str, language: str,
                                    recovery_handoff: bool) -> dict | None:
    """Suggest staff review for a verified-looking maintenance report only.

    This is advisory metadata; it never writes business state. The normal fast
    service route should catch these reports first, but this defense prevents a
    no-evidence knowledge fallback from leaving a guest with a broken facility
    and no escalation path if routing/context changes between stages.
    """
    if not recovery_handoff:
        return None
    return {"kind": "human", "details": no_evidence_handoff_details(query, language)}


def _short_single_fact_extract(answer: str, sources: list[dict]) -> bool:
    """True only for a short answer copied from one verified evidence fact."""
    if not sources or not answer or len(answer.strip()) > 250:
        return False
    claims = extract_claims(answer)
    if len(claims) != 1:
        return False
    normalized = ' '.join(answer.casefold().split())
    return any(
        isinstance(source.get('content'), str)
        and normalized in ' '.join(source['content'].casefold().split())
        for source in sources
    )


_FACT_TEMPLATE_TYPES = frozenset({
    'opening_hours', 'service_window', 'activity_schedule', 'price_vnd',
    'capacity', 'address',
})


def _render_source_fact(source: dict, language: str | None = None) -> str | None:
    """Render one structured fact for display/TTS while retaining its proof."""
    content = source.get('content')
    if not isinstance(content, str):
        return None
    match = re.fullmatch(r'\s*-\s*\*\*(?P<label>[^*]+)\*\*:\s*(?P<value>.+?)\s*', content,
                         flags=re.S)
    if match is None:
        return None
    value = match.group('value').strip()
    entity = str(source.get('title') or match.group('label').split('—', 1)[0].strip())
    fact_type = str(source.get('fact_type') or 'default')
    template_type = fact_type if fact_type in _FACT_TEMPLATE_TYPES else 'default'
    rendered = i18n_text(f'knowledge.fact.{template_type}',
                         language or str(source.get('language') or 'en'),
                         entity=entity, value=value)
    # This private server-side marker lets citation binding authorize the
    # locale template against the original child passage. It is removed from
    # the guest-facing source projection by the citation binder.
    source['_rendered_claim'] = rendered
    return rendered


def _extractive_fallback(answer: str, sources: list[dict], language: str | None = None) -> tuple[str, str | None]:
    """Compose a bounded exact-evidence fallback from related retrieved facts.

    The title is metadata, not evidence text: citation binding must continue to
    match every displayed claim against a current child passage. Keep only facts
    sharing the primary source identity so a broad retrieval result cannot turn
    into an unrelated multi-place answer.
    """
    if not sources:
        return answer, None
    primary = sources[0]
    source_id = primary.get('source_id')
    title = primary.get('title') if isinstance(primary.get('title'), str) else None
    related = [row for row in sources[:_PRESENTATION_LIMITS['max_related_topics']]
               if isinstance(row, dict) and row.get('source_id') == source_id]
    if not related:
        related = [primary]
    facts: list[str] = []
    seen: set[str] = set()
    for row in related:
        rendered = _render_source_fact(row, language)
        content = rendered if rendered is not None else row.get('content')
        if not isinstance(content, str):
            continue
        content = content.strip()
        key = ' '.join(content.casefold().split())
        if not content or key in seen or sum(len(item) + 1 for item in facts) + len(content) > 720:
            continue
        seen.add(key)
        facts.append(content)
    candidate = '\n'.join(facts)
    return (candidate if extract_claims(candidate) else answer), title


def _short_fact_preserves_literals(answer: str, sources: list[dict]) -> bool:
    """Do not let a short model answer silently drop an hour/price/number."""
    if not sources or not isinstance(answer, str):
        return True
    content = sources[0].get('content')
    if not isinstance(content, str):
        return True
    literals = re.findall(r'\d+(?:[.,:/-]\d+)*', content)
    return all(literal in answer for literal in literals)


def _pending_domain_reviews(sources: list[dict]) -> tuple[dict, ...]:
    """Return source review gates that must be visible to the guest."""
    pending = []
    for source in sources:
        review = source.get("domain_review")
        if isinstance(review, dict) and review.get("runtime_gate") not in (None, "", "none"):
            pending.append(review)
    return tuple(pending)


@dataclass(frozen=True)
class AnswerServices:
    ensure_active_context_session: Callable[[str], None]
    emergency_answer: Callable[..., dict]
    grounded_answer: Callable[..., dict]
    planning_answer: Callable[..., dict]
    place_anchor_sources: Callable[..., list]

def build_answer_services(*, store, workflows, cfg, conversations, rag_policy, embedder, reranker,
                          vector_store=None,
                          record_metric, speech_metric, slm_permitted, audio_admission,
                          observe_slm) -> AnswerServices:
    support_directory = load_support_directory(cfg.structured_dataset_dir, cfg.property_id)
    if cfg.structured_dataset_dir and support_directory is None:
        record_metric('recovery.directory_unavailable', 'system')
    try:
        structured_dataset = load_structured_dataset(cfg.structured_dataset_dir)
        if structured_dataset.property_id != cfg.property_id:
            structured_dataset = None
    except (FileNotFoundError, OSError, ValueError, TypeError):
        structured_dataset = None


    def structured_context_selector(query: str, language: str) -> tuple[str, ...]:
        """Return one data-owned context when the question names it exactly."""
        contexts = _mentioned_contexts(query, language)
        return contexts if len(contexts) == 1 else ()

    def knowledge_release_snapshot() -> dict:
        # Runtime freshness/lineage signal for the agent. This is metadata only;
        # citation binding remains the authority for individual claims.
        with store.connection() as con:
            row = con.execute(
                'SELECT release_version,applied_at FROM knowledge_releases WHERE property_id=?',
                (cfg.property_id,)).fetchone()
        return ({'release_version': int(row['release_version']), 'applied_at': int(row['applied_at'])}
                if row is not None else {'release_version': None, 'applied_at': None})

    def ensure_active_context_session(session: str) -> None:
        # The cookie was checked by FastAPI, but session-end can race with a
        # queued turn that passed dependency authentication before reset.
        with store.connection() as con:
            row = con.execute(
                "SELECT 1 FROM sessions WHERE id=? AND property_id=? AND expires_at>?",
                (session, cfg.property_id, int(time.time()))).fetchone()
        if row is None:
            conversations.clear(session)
            raise PermissionError("Session ended before conversation turn")

    def emergency_answer(query: str, language: str, session: str, *, source: str = 'dialogue') -> dict:
        urgent = emergency_response(query, language) or EMERGENCY_TEXT.get(language)
        if not urgent:
            raise ValueError("Emergency route requires a recognized safety phrase")
        record_metric('safety.emergency_route', language)
        ensure_active_context_session(session)
        alert = None
        try:
            alert = workflows.queue_emergency_alert(
                session, language, query,
                source='sos' if source == 'sos_button' else 'dialogue')
            if not alert.get('idempotent_replay'):
                record_metric('safety.emergency_staff_alert', language)
        except (sqlite3.Error, OSError, RuntimeError):
            # Safety guidance must still be returned even if the local staff
            # queue is temporarily unavailable. Never claim an alert succeeded.
            record_metric('safety.emergency_staff_alert_failed', language)
        if alert is not None:
            urgent += i18n_text('emergency.alert_queued', language)
        else:
            urgent += i18n_text('emergency.alert_unconfirmed', language)
        # Only the accepted HTTP turn may erase its conversation context.
        return {"answer": urgent, "sources": [], "citations": [], "suggested_action": None,
                "retrieval_mode": "not_used", "generation_mode": "safety_route",
                "request_completed": False, "grounding": "safety_route",
                "requires_staff_review": False,
                "emergency_alert": ({"id": alert["id"], "status": alert["status"],
                                     "priority": alert["priority"], "queued": True}
                                    if alert is not None else {"queued": False}),
                "emergency_ui": {"show_staff_location": True, "normal_request_disabled": True,
                                 "show_sos": True,
                                 "numbers": dict(EMERGENCY_CONTACTS)}}

    def grounded_answer(query: str, language: str, session: str, *, effective_date: str,
                        voice_turn: bool = False, question_type: str = 'fact') -> dict:
        ensure_active_context_session(session)
        q = query.strip()
        # Capture bounded public pointers/facet state under the memory lock.
        # Retrieval and local model inference intentionally run lock-free.
        memory_snapshot = conversations.snapshot(session, q, language)
        anchor, context_mode = memory_snapshot.anchor, memory_snapshot.context_mode
        search_query = memory_snapshot.retrieval_query
        query_rewritten = memory_snapshot.query_rewritten
        if structured_entities and structured_fact_types:
            record_metric('rag.structured_lookup_candidate', language)
        contextual = False
        retrieval_started = time.monotonic()
        compound_queries = (q,)
        if len(compound_queries) > 1 and anchor is None:
            pieces = []
            for part in compound_queries:
                pieces.append(retrieve(
                    store, property_id=cfg.property_id, language=language, query=part,
                    embedder=embedder, reranker=reranker, vector_store=vector_store,
                    effective_date=effective_date, policy=rag_policy, expand_parent=True,
                    entity_ids=part_entities, fact_types=part_fact_types,
                    fact_context=structured_context_selector(part, language)))
            sources = []
            answers = []
            seen = set()
            for piece in pieces:
                if piece.answer and piece.answer not in answers:
                    answers.append(piece.answer)
                for source in piece.sources:
                    key = (source.get('chunk_id'), source.get('source_id'), source.get('revision'))
                    if key not in seen:
                        seen.add(key)
                        sources.append(source)
            result = Retrieval(
                'multi_question', sources,
                '\n'.join(f'{index}. {answer}' for index, answer in enumerate(answers, 1))
                if answers else abstention_answer(language))
            revoked_anchor = None
            contextual = False
        elif anchor is not None:
            if anchor.language != language:
                result = retrieve_localized_anchor(
                    store, property_id=cfg.property_id, language=language, query=search_query,
                    anchor=anchor, effective_date=effective_date, policy=rag_policy)
            else:
                result = retrieve_context(
                    store, property_id=cfg.property_id, language=language, query=search_query,
                    source_id=anchor.source_id, revision=anchor.revision,
                    chunk_id=anchor.chunk_id, heading=anchor.heading, section_id=anchor.section_id,
                    effective_date=effective_date, policy=rag_policy)
            contextual = bool(result.sources)
            revoked_anchor = anchor if result.mode == 'contextual_revoked' else None
            # Any non-revoked contextual miss (no match, missing translation, or
            # an anchor row with no extractable passage) is not an answer.
            if not result.sources and result.mode != 'contextual_revoked':
                # A specific newly named topic may start a fresh authorized
                # search. A bare "and its hours?" must NOT borrow a different
                # document merely because the translated anchor is missing.
                pass
        else:
            revoked_anchor = None
            result = retrieve(store, property_id=cfg.property_id, language=language,
                              query=search_query, embedder=embedder, reranker=reranker,
                              vector_store=vector_store,
                              effective_date=effective_date, policy=rag_policy, expand_parent=True,
                              entity_ids=structured_entities, fact_types=structured_fact_types,
                              fact_context=structured_context_selector(search_query, language))
        answerability_failure = None
        # Context is part of a fact's identity. If the guest names one, do not
        # compose a list from sibling contexts such as banquet + classroom.
        if len(compound_queries) == 1 and result.sources:
            filtered_sources, mentioned_contexts = _filter_context_sources(
                result.sources, q, language)
            if mentioned_contexts:
                result.sources = filtered_sources
                result.answer = (filtered_sources[0].get('content')
                                 if filtered_sources else abstention_answer(language))
                if not filtered_sources:
                    result.mode = 'context_filter_abstention'
                    contextual = False
            if result.sources and (not answerable(keys, q, result.sources[0], language=language, dense_threshold=cfg.rag_min_dense_similarity)):
                answerability_failure = {'requested_facets': list(keys['facets']), 'source_fact_type': result.sources[0].get('fact_type', '')}
                result.sources = []
                result.answer = abstention_answer(language)
                result.mode = 'answerability_abstention'
                contextual = False
        speech_metric("rag", language, retrieval_started,
                      "with_evidence" if result.sources else "no_evidence")
        if not result.sources and revoked_anchor is not None:
            result.answer = i18n_text('knowledge.revoked', language)
        generation_started = time.monotonic()
        generated = None
        semantic = None
        verification = {'dropped': 0, 'composed': False}
        def generation_observation(name: str, value: float) -> None:
            if name == 'slm_claims_repaired':
                verification['dropped'] = int(value)
            elif name == 'slm_evidence_composed':
                verification['composed'] = True
            observe_slm(name, value, language)
        # Deterministic RAG remains available when a high-priority speech job
        # is running. No new SLM starts during STT/TTS or another SLM.
        # An operator may replace an Ollama tag after app boot. In strict
        # mode verify the exact pinned digest again before this guest turn;
        # a failed/changed local runtime simply takes extractive RAG.
        short_extract = _short_single_fact_extract(result.answer, result.sources)
        if short_extract and voice_turn:
            record_metric('slm.short_extract_bypass', language)
            record_metric('slm.voice_short_bypass', language)
        # Text gets a chance to be phrased naturally even for one short fact;
        # voice keeps the old extractive bypass to protect first-audio latency.
        strict_model_current = slm_permitted() if result.sources and not (short_extract and voice_turn) else False
        if cfg.local_ai_strict_mode and result.sources and not strict_model_current:
            record_metric('slm.strict_unavailable', language)
        slm_timeout = min(
            cfg.slm_generation_timeout_seconds,
            cfg.voice_slm_caps["generation"] if voice_turn else cfg.text_generation_timeout_seconds,
        )
        if (result.sources and strict_model_current and
                audio_admission.try_enter_slm(session)):
            try:
                for model in cfg.llm_candidates():
                    if cfg.semantic_generation_enabled:
                        semantic = semantic_grounded_response(
                            base_url=cfg.llm_base_url, model=model, question=q,
                            evidence=result.sources, language=language,
                            question_type=question_type,
                            verifier_model=cfg.semantic_verifier_model,
                            nli_model_path=cfg.nli_model_path,
                            nli_min_confidence=cfg.nli_min_confidence,
                            require_independent_nli=cfg.semantic_require_independent_nli,
                            nli_manifest_path=cfg.nli_manifest_path,
                            nli_require_manifest=cfg.nli_require_manifest,
                            num_gpu=cfg.slm_num_gpu,
                            timeout=slm_timeout,
                            should_cancel=lambda: audio_admission.slm_cancelled(session))
                    if semantic is None:
                        generated = grounded_response(
                            base_url=cfg.llm_base_url, model=model, question=q,
                            evidence=result.sources, language=language,
                            question_type=question_type,
                            num_gpu=cfg.slm_num_gpu,
                            timeout=slm_timeout,
                            on_observation=generation_observation,
                            should_cancel=lambda: audio_admission.slm_cancelled(session))
                    if semantic is not None or generated is not None:
                        break
            finally:
                audio_admission.leave_slm()
        if cfg.llm_base_url and result.sources:
            speech_metric("slm", language, generation_started,
                          "verified" if (generated or semantic) else "extractive_fallback")
        # A model verdict is not evidence authorization. Bind every quote to
        # the live public child before the response can reach text or TTS.
        # Independent claims already verified by the configured semantic
        # policy can survive another claim's source revocation. This is
        # authorization/repair, not an additional NLI verdict. Rebind the
        # complete repaired answer before either display or speech.
        live_repair = (retain_live_semantic_claims(
            store, property_id=cfg.property_id, language=language,
            sources=result.sources, claims=semantic.claims,
            effective_date=effective_date)
            if semantic is not None else None)
        semantic_bound = live_repair.binding if live_repair else None
        if semantic is not None and live_repair is None:
            semantic = None
        elif semantic is not None and live_repair is not None:
            semantic = SemanticResult(live_repair.answer, live_repair.claims,
                                      semantic.omitted_claims + live_repair.omitted)
            verification['dropped'] += semantic.omitted_claims
        model_answer = semantic.answer if semantic is not None else generated
        if short_extract and model_answer and not _short_fact_preserves_literals(
                model_answer, result.sources):
            # A source-backed short fact such as a full opening-hours range is
            # not safe to answer with only its first endpoint.
            semantic = None
            generated = None
            model_answer = None
        fallback_answer, answer_title = _extractive_fallback(result.answer, result.sources, language)
        final_answer = model_answer or fallback_answer
        bound = semantic_bound if semantic is not None else bind_citations(
            store, property_id=cfg.property_id, language=language,
            answer=final_answer, sources=result.sources,
            effective_date=effective_date)
        # A timed-out or unverifiable model answer must not turn a valid live
        # retrieval into an abstention. Retry with exact child passages before
        # the stale/conflicting-evidence fail-closed branch below.
        if result.sources and not bound.citations and model_answer and fallback_answer != model_answer:
            semantic = None
            generated = None
            final_answer = fallback_answer
            bound = bind_citations(
                store, property_id=cfg.property_id, language=language,
                answer=final_answer, sources=result.sources,
                effective_date=effective_date)
        # Revoke answers when an approved document was changed mid-inference,
        # or when no *single* current child actually contains the spoken span.
        if result.sources and not bound.citations:
            # Invalid model prose is not a revoked source. Retire the
            # pointer only if authorization to its original row was lost.
            if anchor is not None:
                with store.connection() as con:
                    live = con.execute(
                        "SELECT 1 FROM knowledge WHERE id=? AND source=? AND revision=? "
                        "AND property_id=? AND language=? AND classification='public' "
                        "AND active=1 AND effective_from<=? "
                        "AND (effective_to IS NULL OR effective_to>=?)",
                        (anchor.chunk_id, anchor.source_id, anchor.revision,
                         cfg.property_id, anchor.language, effective_date, effective_date)).fetchone()
                if live is None:
                    revoked_anchor = anchor
            final_answer = abstention_answer(language)
            result.mode = 'stale_evidence_abstention'
            generated = None
            contextual = False
        result.sources = bound.sources if bound.citations else []
        pending_domain_reviews = _pending_domain_reviews(result.sources)
        if pending_domain_reviews:
            final_answer += "\n" + i18n_text(
                'knowledge.staff_confirmation_required', language)
        # Stage the pointer. The /api/ask boundary commits only after its
        # turn remains current and its speech authorization succeeds.
        suggested = None
        if pending_domain_reviews:
            suggested = {"kind": "human", "details": i18n_text(
                'knowledge.staff_confirmation_required', language)}
        recovery = {'related_topics': [], 'support_contact': None, 'handoff_recommended': False}
        if bound.citations:
            conversations.clear_no_evidence(session)
        # A routine no-evidence knowledge miss is not automatically a front-desk
        # request. Offer bounded recovery metadata instead; reserve a human
        # suggestion for revoked/conflicting evidence where staff review adds value.
        if not bound.citations:
            recovery = recovery_metadata(
                store, support_directory, property_id=cfg.property_id, language=language,
                query=q, effective_date=effective_date, retrieval_mode=result.mode)
            retry_count = conversations.note_no_evidence(session, q)
            # First miss remains self-service. A department extension is exposed
            # only after the guest repeats the same unresolved question.
            if retry_count < 2:
                recovery['support_contact'] = None
            if recovery['related_topics']:
                record_metric('retrieval.recovery_topics', language)
                labels = [item.get('label') for item in recovery['related_topics'][:_PRESENTATION_LIMITS['max_related_topics']]
                          if isinstance(item, dict) and isinstance(item.get('label'), str)]
                if labels:
                    final_answer += '\n' + i18n_text('recovery.related_prompt', language, topics=', '.join(labels))
            if recovery['support_contact'] is not None:
                record_metric('retrieval.recovery_contact', language)
                extensions = recovery['support_contact'].get('extensions') or []
                department = recovery['support_contact'].get('department')
                if extensions and isinstance(department, str):
                    final_answer += '\n' + i18n_text(
                        'recovery.contact_extension', language, department=department, extension=extensions[0])
            suggested = _no_evidence_handoff_suggestion(
                q, language, bool(recovery['handoff_recommended']))
            if suggested is not None and not recovery['handoff_recommended']:
                recovery['handoff_recommended'] = True
        record_metric('ask.total', language)
        record_metric('retrieval.with_evidence' if bound.citations else 'retrieval.no_evidence', language)
        if suggested:
            record_metric('intent.suggested', language)
        action = ({"kind": suggested.kind, "details": suggested.details}
                  if suggested is not None and not isinstance(suggested, dict)
                  else suggested)
        return {"answer": final_answer, "sources": result.sources,
                "citations": bound.citations,
                "suggested_action": action,
                "retrieval_mode": result.mode, "generation_mode": (
                    "local_slm_model_assisted_semantic" if semantic is not None else
                    "local_slm_evidence_composition" if generated and verification['composed'] else
                    "local_slm" if generated else "extractive"),
                "request_completed": False, "grounding": ("model_assisted_semantic" if semantic is not None else
                                                 "extractive" if bound.citations else "no_evidence"),
                "evidence_status": ('PARTIALLY_SUPPORTED' if bound.citations and (verification['dropped'] or pending_domain_reviews)
                                    else 'SUPPORTED' if bound.citations else
                                    'CONFLICTING' if result.mode == 'conflict_abstention' else
                                    'REVOKED_SOURCE' if revoked_anchor is not None else 'UNSUPPORTED'),
                 "knowledge_release": knowledge_release_snapshot(),
                 "answer_title": answer_title if bound.citations else None,
                "omitted_claims": verification['dropped'] if bound.citations else 0,
                "requires_staff_review": bool(suggested) or bool(pending_domain_reviews),
                "domain_review_required": bool(pending_domain_reviews),
                "context_used": contextual, "context_mode": context_mode if contextual else "none",
                "query_rewrite_applied": query_rewritten,
                "answerability_failure": answerability_failure,
                "related_topics": recovery['related_topics'] if not bound.citations else [],
                "support_contact": recovery['support_contact'] if not bound.citations else None,
                "recovery_mode": ("staff_review" if recovery['handoff_recommended'] else
                                  "self_service" if not bound.citations else "not_needed"),
                "_remember_sources": result.sources, "_remember_query": q,
                "_forget_anchor": revoked_anchor,
                        "_memory_version": memory_snapshot.version}

    def planning_answer(query: str, language: str, session: str, *, effective_date: str,
                        preferences: dict | None = None) -> dict:
        """At most three serial source-backed specialist lookups, no tool writes.

        For speech, do not add an unsupported conversational preamble or schedule.
        The response metadata labels the result as advisory for the UI; every
        spoken claim must be an exact current citation in the business DB.
        """
        if not topics:
            raise RuntimeError('Planning route requires an explicit multi-domain request')
        claims, candidate_sources, missing, selected = [], [], [], []
        verified_schedule = {}
        if cfg.planning_release_path:
            try:
                verified_schedule = approved_schedule(
                    store, path=cfg.planning_release_path,
                    expected_sha256=cfg.planning_release_sha256,
                    property_id=cfg.property_id, language=language, as_of=effective_date)
            except (ScheduleUnavailable, OSError):
                record_metric('planning.schedule_unavailable', language)
                # No stale or partially approved clock slots reach the guest.

        for topic in topics[:_PRESENTATION_LIMITS['max_plan_items']]:
            # Prefer a uniquely named, source-bound activity when the guest
            # explicitly mentions it. This prevents a broad dining lookup from
            # selecting breakfast for an evening Italian-dinner request.
            explicit_ids = record_alias_matches(query, verified_schedule, category=topic)
            explicit = [(key, verified_schedule[key]) for key in explicit_ids]
            if len(explicit) == 1:
                _, spec = explicit[0]
                proof = spec['provenance']
                quote = proof['quote'].strip()
                source = {'content': quote, 'source_id': proof['source_id'],
                          'revision': proof['revision'], 'chunk_id': proof['chunk_id']}
                cited = bind_citations(store, property_id=cfg.property_id, language=language,
                                       answer=quote, sources=[source],
                                       effective_date=effective_date)
                if cited.citations:
                    claims.append(quote)
                    selected.append((topic, quote))
                    candidate_sources.extend(cited.sources)
                    continue
            # Use the extractive retrieval specialist, not three serial local
            # SLM generations. This keeps the Edge planning branch bounded.
            if additions:
                search_query = ' '.join((search_query, *additions))
            retrieved = retrieve(store, property_id=cfg.property_id, language=language,
                                 query=search_query,
                                 embedder=embedder, reranker=reranker, vector_store=vector_store,
                                 effective_date=effective_date,
                                 policy=rag_policy, expand_parent=True)
            evidence = bind_citations(store, property_id=cfg.property_id, language=language,
                                      answer=retrieved.answer, sources=retrieved.sources,
                                      effective_date=effective_date)
            evidence_claims = extract_claims(retrieved.answer) if evidence.citations else []
            if not evidence_claims or len(evidence_claims[0].text) > 230:
                missing.append(topic)
                continue
            claims.append(evidence_claims[0].text)
            selected.append((topic, evidence_claims[0].text))
            candidate_sources.extend(evidence.sources)
        mentioned_activity_ids = set(record_alias_matches(query, verified_schedule))
        # explicitly named activities within one broad topic may each
        # contribute an independently source-bound exact claim. Never promote
        # unmentioned release entries or copy an operator label as a hotel fact.
        for activity_id, spec in verified_schedule.items():
            if len(claims) >= 4 or activity_id == spec.get('topic', activity_id):
                continue  # compatibility topic schedule, or bounded answer capacity
            if spec.get('topic') not in topics or activity_id not in mentioned_activity_ids:
                continue
            proof = spec['provenance']
            quote = proof['quote'].strip()
            if (not 12 <= len(quote) <= 230 or quote.startswith('#') or
                    len(extract_claims(quote)) != 1 or quote in claims):
                continue
            source = {'content': quote, 'source_id': proof['source_id'],
                      'revision': proof['revision'], 'chunk_id': proof['chunk_id']}
            cited = bind_citations(store, property_id=cfg.property_id, language=language,
                                   answer=quote, sources=[source],
                                   effective_date=effective_date)
            if cited.citations:
                claims.append(quote)
                selected.append((spec['topic'], quote))
                candidate_sources.extend(cited.sources)
        approved_answer = '\n'.join(claims)
        bound = bind_citations(store, property_id=cfg.property_id, language=language,
                               answer=approved_answer, sources=candidate_sources,
                               effective_date=effective_date)
        if not bound.citations:
            final_answer = abstention_answer(language)
            recovery = recovery_metadata(
                store, support_directory, property_id=cfg.property_id, language=language,
                query=query, effective_date=effective_date, retrieval_mode='planning_no_evidence')
            if recovery['related_topics']:
                record_metric('retrieval.recovery_topics', language)
                labels = [item.get('label') for item in recovery['related_topics'][:_PRESENTATION_LIMITS['max_related_topics']]
                          if isinstance(item, dict) and isinstance(item.get('label'), str)]
                if labels:
                    final_answer += '\n' + i18n_text('recovery.related_prompt', language, topics=', '.join(labels))
            if recovery['support_contact'] is not None:
                record_metric('retrieval.recovery_contact', language)
                extensions = recovery['support_contact'].get('extensions') or []
                department = recovery['support_contact'].get('department')
                if extensions and isinstance(department, str):
                    final_answer += '\n' + i18n_text(
                        'recovery.contact_extension', language, department=department, extension=extensions[0])
            return {'answer': final_answer, 'sources': [], 'citations': [],
                    'suggested_action': None,
                    'retrieval_mode': 'planning_no_evidence', 'generation_mode': 'extractive',
                    'request_completed': False, 'grounding': 'no_evidence',
                    'requires_staff_review': False, 'plan_is_draft': True,
                    'plan_topics': list(topics), 'missing_topics': list(topics),
                    'plan': draft_plan(query, language, topics, [], [], list(topics),
                                       effective_date=effective_date, session_preferences=preferences),
                    'knowledge_release': knowledge_release_snapshot(),
                    'related_topics': recovery['related_topics'],
                    'support_contact': recovery['support_contact'],
                    'recovery_mode': 'self_service',
                    'evidence_status': 'UNSUPPORTED'}
        # The existing TTS proof binder validates all claims again, and the
        # voice turn can invalidate this entire advisory answer before playback.
        return {'answer': approved_answer, 'sources': bound.sources, 'citations': bound.citations,
                'suggested_action': None, 'retrieval_mode': 'planning_verified',
                'generation_mode': 'extractive', 'request_completed': False,
                'grounding': 'extractive', 'requires_staff_review': False,
                'plan_is_draft': True, 'plan_topics': list(topics),
                'missing_topics': missing,
                'plan': draft_plan(query, language, topics, selected, bound.citations, missing,
                                   verified_schedule, effective_date=effective_date,
                                   session_preferences=preferences),
                'knowledge_release': knowledge_release_snapshot(),
                'evidence_status': 'PARTIALLY_SUPPORTED' if missing else 'SUPPORTED'}


    def place_anchor_sources(label: str, language: str, *, effective_date: str) -> list[dict]:
        """Knowledge evidence for a map-verified place, used only as a memory anchor.

        A "where is the spa?" turn answered from the map has no knowledge
        sources, so a follow-up ("what time does it open?") had nothing to
        refer to. A cheap lexical read of the verified destination label gives
        an authorized anchor; it is never shown as an answer.
        """
        label = label.strip()
        if not label:
            return []
        # The map label is the reviewed localized entity title, so match the
        # document by title in the guest's language under the same
        # authorization predicate retrieve_context re-checks on every use.
        try:
            with store.connection() as con:
                row = con.execute(
                    "SELECT id,source,revision,title,heading,section_id FROM knowledge "
                    "WHERE property_id=? AND language=? AND classification='public' AND active=1 "
                    "AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?) "
                    "AND title=? ORDER BY section_ordinal,id LIMIT 1",
                    (cfg.property_id, language, effective_date, effective_date, label)).fetchone()
        except sqlite3.Error:
            return []
        if row is None:
            return []
        return [{'chunk_id': row['id'], 'source_id': row['source'], 'revision': row['revision'],
                 'title': row['title'], 'heading': row['heading'], 'language': language,
                 'section_id': row['section_id'] or ''}]

    return AnswerServices(
        ensure_active_context_session=ensure_active_context_session,
        emergency_answer=emergency_answer,
        grounded_answer=grounded_answer,
        planning_answer=planning_answer,
        place_anchor_sources=place_anchor_sources,
    )
