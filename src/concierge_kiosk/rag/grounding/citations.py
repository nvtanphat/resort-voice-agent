"""Response-bound citations over *current* approved child evidence.

A retrieved candidate is not a citation until its exact child/revision is
re-authorized and the final answer is located within that child passage.
This is extractive attribution, not semantic verification of arbitrary prose.
"""
from __future__ import annotations

from dataclasses import dataclass

from concierge_kiosk.persistence.sqlite_store import Store
from ..documents import LANGUAGES
from ..retrieval.evidence import retrieve_parent_context
from ..text.safety import unsafe_knowledge_text
from .claims import extract_claims, exact_span


@dataclass(frozen=True)
class CitationResult:
    sources: list[dict]
    citations: list[dict]


def rebase_citations(answer: str, citations: list[dict]) -> list[dict]:
    """Relocate already verified claims after deterministic observation composition."""
    rebased = []
    for citation in citations:
        claim = citation.get('claim')
        if not isinstance(claim, str) or not claim:
            continue
        start = answer.find(claim)
        if start < 0:
            continue
        offset = start - int(citation.get('claim_start') or 0)
        spans = [{**span, 'claim_start': span['claim_start'] + offset,
                  'claim_end': span['claim_end'] + offset}
                 for span in citation.get('claim_spans', [])
                 if isinstance(span, dict) and type(span.get('claim_start')) is int
                 and type(span.get('claim_end')) is int]
        rebased.append({**citation, 'citation_id': f'C{len(rebased) + 1}',
                        'claim_start': start, 'claim_end': start + len(claim), 'claim_spans': spans})
    return rebased


def _source_claim_span(source: dict, passage: str, claim: str) -> tuple[int, int] | None:
    """Match exact evidence or a server-rendered structured-fact template."""
    span = exact_span(passage, claim)
    if span is not None:
        return span
    if source.get('_rendered_claim') == claim:
        return (0, len(passage))
    return None


def bind_citations(store: Store, *, property_id: str, language: str,
                   answer: str, sources: list[dict], effective_date: str) -> CitationResult:
    """Attach only citations that back the entire final extractive answer.

    A knowledge update/revocation between retrieval and response must not leave
    a stale citation. All source metadata is taken from SQLite, never the client.
    The citation quote is the selected child passage, not an unchecked parent.
    """
    if not answer.strip() or not sources:
        return CitationResult([], [])
    claims = extract_claims(answer)
    if not claims:
        return CitationResult([], [])
    if not effective_date:
        raise ValueError('Explicit effective_date is required for citation binding')
    today = effective_date
    # Reauthorize all candidates before matching *any* claim. There must not be
    # a partially attributed response when one assertion has no current source.
    approved: list[tuple[dict, dict, str]] = []
    with store.connection() as con:
        for source in sources[:10]:
            source_language = source.get('language', language)
            if source_language not in LANGUAGES:
                continue
            row = con.execute(
                "SELECT id,source,revision,title,heading,body,effective_from,effective_to,parent_id,section_id,section_ordinal "
                "FROM knowledge WHERE id=? AND source=? AND revision=? AND "
                "property_id=? AND language=? AND classification='public' AND active=1 "
                "AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?)",
                (source.get('chunk_id'), source.get('source_id'), source.get('revision'),
                 property_id, source_language, today, today),
            ).fetchone()
            if row is None or unsafe_knowledge_text(row['body']):
                continue
            passage = source.get('content', '')
            if not isinstance(passage, str) or not passage.strip() or passage not in row['body']:
                continue
            approved.append((source, dict(row), passage))
        # Prefer one uninterrupted original passage when it supports the entire
        # response, preserving the legacy API and avoiding arbitrary segmentation.
        whole = next(((source, row, passage, span)
                      for source, row, passage in approved
                      if (span := _source_claim_span(source, passage, answer)) is not None), None)
        if whole:
            selected = [(claims[0], *whole)]
        else:
            selected = []
            for claim in claims:
                match = next(((source, row, passage, span)
                              for source, row, passage in approved
                              if (span := _source_claim_span(source, passage, claim.text)) is not None), None)
                if match is None:
                    return CitationResult([], [])  # fail closed, never partial citation
                selected.append((claim, *match))
        verified: list[dict] = []
        citations: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for index, (claim, source, row, passage, span) in enumerate(selected, 1):
            source_language = source.get('language', language)
            clean_source = {key: value for key, value in source.items()
                            if not key.startswith('_')}
            clean_source = {**clean_source, 'chunk_id': row['id'], 'source_id': row['source'],
                            'revision': row['revision'], 'language': source_language,
                            'title': row['title'], 'heading': row['heading'], 'parent_id': row['parent_id'],
                            'section_id': row['section_id'], 'section_ordinal': row['section_ordinal']}
            if 'parent_context' in clean_source:
                clean_source['parent_context'] = retrieve_parent_context(
                    store, property_id=property_id, language=source_language,
                    source_id=row['source'], revision=row['revision'],
                    effective_date=today, parent_id=row['parent_id'],
                    section_id=row['section_id'], heading=row['heading'])
            key = (row['id'], row['revision'])
            if key not in seen:
                seen.add(key)
                verified.append(clean_source)
            # An uninterrupted answer may contain multiple independently
            # testable assertions. Preserve its legacy one-citation display,
            # but expose each assertion's exact proof offsets as well.
            individual_claims = claims if whole else [claim]
            proof_spans = []
            for item in individual_claims:
                proof = _source_claim_span(source, passage, item.text)
                if proof is None:  # defensive; never emit partial proof
                    return CitationResult([], [])
                proof_spans.append({
                    'text': item.text, 'claim_start': item.start,
                    'claim_end': item.end, 'quote_start': proof[0],
                    'quote_end': proof[1],
                })
            citations.append({
                'citation_id': f'C{index}', 'source_id': row['source'],
                'revision': row['revision'], 'chunk_id': row['id'],
                'title': row['title'], 'heading': row['heading'], 'language': source_language,
                'requested_language': language,
                'effective_from': row['effective_from'], 'effective_to': row['effective_to'],
                'quote': passage, 'answer_start': span[0], 'answer_end': span[1],
                'claim': answer if whole else claim.text,
                'claim_start': 0 if whole else claim.start,
                'claim_end': len(answer) if whole else claim.end,
                'claim_spans': proof_spans,
            })
    # Never claim that other merely related retrieved chunks support the answer.
    return CitationResult(verified, citations)


def bind_semantic_citations(store: Store, *, property_id: str, language: str,
                            answer: str, sources: list[dict], claims: tuple,
                            effective_date: str) -> CitationResult:
    """Bind model-assisted sentences to live authoritative *quoted evidence*.

    Does NOT independently prove entailment: semantic inference happens upstream.
    Re-checks every quote's row, property, language, date, revision and active
    state. A single missing/changed source fails the whole response closed.
    The speech proof lease uses the exact quote, never an unchecked paraphrase.
    """
    parsed = extract_claims(answer)
    if not parsed or len(parsed) != len(claims) or len(claims) > 3:
        return CitationResult([], [])
    verified, citations, seen = [], [], set()
    if not effective_date:
        raise ValueError('Explicit effective_date is required for semantic citation binding')
    today = effective_date
    with store.connection() as con:
        for number, (span, proof) in enumerate(zip(parsed, claims), 1):
            index = getattr(proof, 'source_index', -1)
            quote = getattr(proof, 'quote', None)
            claim = getattr(proof, 'text', None)
            if (type(index) is not int or not 0 <= index < min(len(sources), 3)
                    or span.text != claim or not isinstance(quote, str)):
                return CitationResult([], [])
            source = sources[index]
            source_language = source.get('language', language)
            if source_language not in LANGUAGES:
                return CitationResult([], [])
            passage = source.get('content', '')
            if not isinstance(passage, str) or exact_span(passage, quote) is None:
                return CitationResult([], [])
            row = con.execute(
                "SELECT id,source,revision,title,heading,body,effective_from,effective_to,parent_id,section_id,section_ordinal "
                "FROM knowledge WHERE id=? AND source=? AND revision=? AND "
                "property_id=? AND language=? AND classification='public' AND active=1 "
                "AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?)",
                (source.get('chunk_id'), source.get('source_id'), source.get('revision'),
                 property_id, source_language, today, today)).fetchone()
            if row is None or unsafe_knowledge_text(row['body']) or passage not in row['body']:
                return CitationResult([], [])
            match = exact_span(row['body'], quote)
            if match is None:
                return CitationResult([], [])
            safe_source = {**source, 'chunk_id': row['id'], 'source_id': row['source'],
                           'revision': row['revision'], 'language': source_language,
                           'title': row['title'], 'heading': row['heading'],
                           'parent_id': row['parent_id'], 'section_id': row['section_id'],
                           'section_ordinal': row['section_ordinal']}
            if 'parent_context' in safe_source:
                safe_source['parent_context'] = retrieve_parent_context(
                    store, property_id=property_id, language=source_language,
                    source_id=row['source'], revision=row['revision'], effective_date=today,
                    parent_id=row['parent_id'], section_id=row['section_id'], heading=row['heading'])
            key = (row['id'], row['revision'])
            if key not in seen:
                seen.add(key)
                verified.append(safe_source)
            citations.append({
                'citation_id': f'C{number}', 'source_id': row['source'],
                'revision': row['revision'], 'chunk_id': row['id'],
                'title': row['title'], 'heading': row['heading'], 'language': source_language,
                'requested_language': language,
                'effective_from': row['effective_from'], 'effective_to': row['effective_to'],
                'quote': quote, 'answer_start': match[0], 'answer_end': match[1],
                'claim': claim, 'claim_start': span.start, 'claim_end': span.end,
                'verification_method': 'model_assisted_semantic',
                'claim_spans': [{'text': claim, 'claim_start': span.start,
                                 'claim_end': span.end, 'quote_start': match[0],
                                 'quote_end': match[1]}],
            })
    return CitationResult(verified, citations)


@dataclass(frozen=True)
class LiveSemanticRepair:
    """Only already-verified claims that still have a current source."""
    answer: str
    claims: tuple
    binding: CitationResult
    omitted: int


def retain_live_semantic_claims(store: Store, *, property_id: str, language: str,
                                sources: list[dict], claims: tuple,
                                effective_date: str) -> LiveSemanticRepair | None:
    """source-revocation repair inspired by claim-level evidence tracing.

    This does NOT decide semantic entailment. Upstream Generator/NLI has already
    vetted each claim; here we only re-authorize exact quotes against live
    SQLite. A revoked claim is discarded independently. Rebind the WHOLE
    repaired answer at the end so a mid-repair revocation fails closed.
    """
    if not isinstance(claims, tuple) or not 1 <= len(claims) <= 3:
        return None
    kept = []
    for claim in claims:
        if not isinstance(getattr(claim, 'text', None), str):
            continue
        single = bind_semantic_citations(store, property_id=property_id,
                                         language=language, answer=claim.text,
                                         sources=sources, claims=(claim,), effective_date=effective_date)
        if single.citations:
            kept.append(claim)
    if not kept:
        return None
    answer = '\n'.join(claim.text for claim in kept)
    binding = bind_semantic_citations(store, property_id=property_id,
                                      language=language, answer=answer,
                                      sources=sources, claims=tuple(kept), effective_date=effective_date)
    if not binding.citations or len(binding.citations) != len(kept):
        return None
    return LiveSemanticRepair(answer, tuple(kept), binding, len(claims)-len(kept))
