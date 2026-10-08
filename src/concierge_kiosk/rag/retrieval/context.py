"""Context-bound and localized-anchor retrieval."""
from __future__ import annotations
import re
import unicodedata
from concierge_kiosk.persistence.sqlite_store import Store
from concierge_kiosk.rag.documents import LANGUAGES
from concierge_kiosk.rag.retrieval.evidence import evidence_passage
from concierge_kiosk.rag.text.safety import unsafe_knowledge_text
from concierge_kiosk.rag.text.tokenization import tokens
from concierge_kiosk.rag.grounding.relevance import (
    evidence_relevant, normalized_query, query_terms as meaningful_query_terms)
from concierge_kiosk.agent.understanding.domain_nlu import DISCOURSE_TERMS
from concierge_kiosk.core.domain_profile import rag_policy
from .policy import RAGPolicy, Retrieval, abstention_answer

_TOKENIZATION = rag_policy().tokenization
_CJK_QUERY_CLEANUP = _TOKENIZATION.get('cjk_query_cleanup', {})

def retrieve_context(store: Store, *, property_id: str, language: str, query: str,
                     source_id: str, revision: str, chunk_id: str, effective_date: str,
                     policy: RAGPolicy | None = None,
                     heading: str | None = None, section_id: str | None = None) -> Retrieval:
    """Re-read a prior *approved* source for a dependent question.

    Conversation pointers are never evidence by themselves. Every turn rechecks
    property, public classification, publication dates, current revision and
    active status in SQLite; knowledge replacement invalidates old pointers.
    Only scoped evidence is returned, even if the guest says "ignore security".
    """
    policy = policy or RAGPolicy()
    policy.validate()
    if language not in LANGUAGES or not query.strip() or len(query) > 500:
        raise ValueError("Unsupported language or contextual query")
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', effective_date):
        raise ValueError("Explicit effective_date is required for contextual retrieval")
    today = effective_date
    numbers = set(re.findall(r'\d+(?:[.,:/-]\d+)*', query))
    # Discourse markers do not prove that the old source answers a NEW facet.
    # E.g. "And its parking charges?" must not quote dining hours just because
    # the old chunk contains the word "and". Purely deictic "and it?" can
    # still use the exact previous chunk, subject to current-source validation.
    # searchable()/tokens() decompose Hangul for accent folding; normalize
    # back to NFC before comparing Korean nouns and discourse particles.
    terms = {unicodedata.normalize('NFC', token)
             for token in tokens(query, language=language, limit=None, stem=True)} - set(
                 DISCOURSE_TERMS.get(language, ()))

    def evidence_overlap(row: dict) -> int:
        words = {unicodedata.normalize('NFC', token) for token in tokens(
            f"{row['title']} {row['heading']} {row['body']}", language=language,
            limit=None, stem=True)}
        # Korean topic particles attach to the same underlying noun: e.g.
        # 영업시간은요? still matches the documented 영업시간, whereas parking
        # cannot borrow an unrelated dining-hours answer.
        return sum(term in words for term in terms)
    select = ("SELECT id,source,revision,title,heading,body,context_text,parent_id,section_id,section_ordinal,"
              "entity_id,fact_type,fact_context,canonical_fact_id,metadata_json FROM knowledge WHERE ")
    authorized = ("property_id=? AND language=? AND classification='public' AND active=1 "
                  "AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?) "
                  "AND source=? AND revision=?")
    with store.connection() as con:
        # Reauthorize the exact remembered chunk first. Candidate pagination or
        # lexical ID ordering must never make a live anchor disappear.
        exact = con.execute(select + authorized + " AND id=?",
                            (property_id, language, today, today, source_id, revision, chunk_id)).fetchone()
        if exact is None:
            return Retrieval("contextual_revoked", [], "")
        anchor_section = section_id or exact['section_id']
        if section_id and exact['section_id'] != section_id:
            return Retrieval("contextual_revoked", [], "")
        if anchor_section:
            rows = con.execute(select + authorized + " AND section_id=? ORDER BY section_ordinal,id",
                               (property_id, language, today, today, source_id, revision, anchor_section)).fetchall()
        elif heading is not None:
            rows = con.execute(select + authorized + " AND heading=? ORDER BY section_ordinal,id",
                               (property_id, language, today, today, source_id, revision, heading)).fetchall()
        else:
            rows = [exact]
    if not rows:
        return Retrieval("contextual_revoked", [], "")
    rows = [row for row in rows if
            not unsafe_knowledge_text(row['body']) and
            (not meaningful_query_terms(query, language) or
             evidence_relevant(query, language, row['body'], row['title'], row['heading'],
                               threshold=policy.lexical_coverage)) and
            numbers.issubset(set(re.findall(r'\d+(?:[.,:/-]\d+)*', row['body'])))]
    if not rows:
        # An unsupported new predicate is NOT evidence that a prior citation
        # was revoked; retain it for later explicit topic return.
        return Retrieval("contextual_no_match", [], "")
    # Prefer a matching heading/content, else the exact prior chunk if its
    # revision remains active. Do not arbitrarily select a different section.
    scored = sorted(((evidence_overlap(row), row) for row in rows),
                    key=lambda item: (-item[0], item[1]['id']))
    if scored[0][0]:
        selected = scored[0][1]
    elif not terms:
        selected = next((row for row in rows if row['id'] == chunk_id), None)
    else:
        # Do not recycle the previous answer for an unsupported new predicate.
        return Retrieval("contextual_no_match", [], "")
    if selected is None:
        return Retrieval("contextual_no_match", [], "")
    source = {"chunk_id": selected['id'], "source_id": selected['source'],
              "revision": selected['revision'], "title": selected['title'],
              "heading": selected['heading'], "parent_id": selected['parent_id'],
              "section_id": selected['section_id'], "section_ordinal": selected['section_ordinal'],
              "entity_id": selected['entity_id'], "fact_type": selected['fact_type'],
              "fact_context": selected['fact_context'],
              "canonical_fact_id": selected['canonical_fact_id'],
              "context_text": selected['context_text'],
              "content": evidence_passage(selected['body'], query, language=language,
                                           max_chars=policy.max_evidence_chars),
              "score": None}
    return Retrieval("contextual_lexical", [source], source['content']) if source["content"] else Retrieval("contextual_lexical", [], "")


def retrieve_localized_anchor(store: Store, *, property_id: str, language: str,
                              query: str, anchor, effective_date: str,
                              policy: RAGPolicy | None = None) -> Retrieval:
    """Use a reviewed target-language sibling only when uniquely identifiable.

    The previous language's exact evidence must still be approved and current.
    Revisions of translated documents may differ; each target-language row is
    independently checked for property, classification and effective dates.
    Ambiguous multi-section translations abstain instead of guessing a section.
    """
    policy = policy or RAGPolicy()
    policy.validate()
    if language not in LANGUAGES or anchor.language not in LANGUAGES or not query.strip():
        raise ValueError('Invalid localized context request')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', effective_date):
        raise ValueError("Explicit effective_date is required for localized retrieval")
    today = effective_date
    with store.connection() as con:
        old = con.execute(
            "SELECT 1 FROM knowledge WHERE id=? AND property_id=? AND source=? "
            "AND revision=? AND language=? AND classification='public' AND active=1 "
            "AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?)",
            (anchor.chunk_id, property_id, anchor.source_id, anchor.revision,
             anchor.language, today, today)).fetchone()
        if old is None:
            return Retrieval('contextual_revoked', [], abstention_answer(language))
        anchor_section = getattr(anchor, 'section_id', '')
        if anchor_section:
            rows = con.execute(
                "SELECT id,source,revision,title,heading,body,context_text,parent_id,section_id,section_ordinal,"
                "entity_id,fact_type,fact_context,canonical_fact_id,metadata_json FROM knowledge WHERE "
                "property_id=? AND language=? AND source=? AND classification='public' "
                "AND active=1 AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?) "
                "AND section_id=? ORDER BY section_ordinal,id",
                (property_id, language, anchor.source_id, today, today, anchor_section)).fetchall()
        else:
            rows = con.execute(
                "SELECT id,source,revision,title,heading,body,context_text,parent_id,section_id,section_ordinal,"
                "entity_id,fact_type,fact_context,canonical_fact_id,metadata_json FROM knowledge WHERE "
                "property_id=? AND language=? AND source=? AND classification='public' "
                "AND active=1 AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?) "
                "ORDER BY section_ordinal,id",
                (property_id, language, anchor.source_id, today, today)).fetchall()
    if anchor_section:
        if not rows:
            return Retrieval('contextual_translation_unavailable', [], abstention_answer(language))
    else:
        # Legacy anchors without a language-neutral section ID remain fail-closed:
        # only an unambiguous one-chunk translated document can be selected.
        if len(rows) != 1:
            return Retrieval('contextual_translation_unavailable', [], abstention_answer(language))
    rows = [row for row in rows if not unsafe_knowledge_text(row['body'])]
    if not rows:
        return Retrieval('contextual_translation_unavailable', [], abstention_answer(language))
    # A language switch does not prove the new *facet* is answered by the old
    # source. Validate in the target language before using its translation:
    # e.g. restaurant hours -> Chinese parking fees must fall back to a fresh
    # scoped search, not cite restaurant information just because IDs match.
    discourse = set(DISCOURSE_TERMS.get(language, ()))
    terms = {unicodedata.normalize('NFC', term)
             for term in tokens(query, language=language, limit=None, stem=True)} - discourse
    def target_row_matches(candidate) -> bool:
        # Query rewrites deliberately normalize multilingual clock questions to
        # the shared ``operating hours`` facet.  The target-language sibling is
        # already bound to the reviewed anchor's source and section, so an
        # opening-hours fact is the uniquely safe match even when the Chinese
        # or Korean surface tokens do not overlap the translated body.
        opening_hours_request = (
            candidate['fact_type'] == 'opening_hours'
            and 'operating hours' in normalized_query(query, language))
        if opening_hours_request:
            return True
        if terms:
            supported = {unicodedata.normalize('NFC', term)
                         for term in tokens(f"{candidate['title']} {candidate['heading']} {candidate['context_text']} {candidate['body']}",
                                            language=language, limit=None, stem=True)}
            cleanup = _CJK_QUERY_CLEANUP.get(language, {})
            prefixes = '|'.join(re.escape(item) for item in cleanup.get('prefixes', ()))
            suffixes = '|'.join(re.escape(item) for item in cleanup.get('suffixes', ()))
            core = re.sub(rf'^(?:{prefixes})+' if prefixes else r'(?!)', '', query.strip())
            core = re.sub(rf'(?:{suffixes})+$' if suffixes else r'(?!)', '', core)
            for removable in cleanup.get('remove', ()):
                core = core.replace(removable, '')
            cleaned_terms = set(tokens(core, language=language, limit=None, stem=True))
            required_terms = cleaned_terms or terms
            if not required_terms.intersection(supported):
                return False
        return (not meaningful_query_terms(query, language) or evidence_relevant(
            query, language, candidate['body'], candidate['title'], candidate['heading'],
            threshold=policy.lexical_coverage))

    matching_rows = [candidate for candidate in rows if target_row_matches(candidate)]
    if not matching_rows:
        return Retrieval('contextual_no_match', [], '')
    # section_id disambiguates the translated *section*. If that section was
    # split into multiple child chunks, choose deterministically by query overlap
    # rather than treating the translation as ambiguous.
    def overlap(candidate) -> int:
        supported = {unicodedata.normalize('NFC', term) for term in tokens(
            f"{candidate['title']} {candidate['heading']} {candidate['body']}", language=language,
            limit=None, stem=True)}
        return len(terms & supported)
    row = sorted(matching_rows, key=lambda candidate: (-overlap(candidate), candidate['id']))[0]
    passage_query = (row['title'] if (
        row['fact_type'] == 'opening_hours'
        and 'operating hours' in normalized_query(query, language)) else query)
    content = evidence_passage(row['body'], passage_query, language=language,
                               max_chars=policy.max_evidence_chars)
    if not content:
        return Retrieval('contextual_localized', [], abstention_answer(language))
    source = {'chunk_id': row['id'], 'source_id': row['source'],
              'revision': row['revision'], 'title': row['title'],
              'heading': row['heading'], 'parent_id': row['parent_id'],
              'section_id': row['section_id'], 'section_ordinal': row['section_ordinal'],
              'entity_id': row['entity_id'], 'fact_type': row['fact_type'],
              'fact_context': row['fact_context'],
              'canonical_fact_id': row['canonical_fact_id'],
              'context_text': row['context_text'],
              'content': content, 'score': None}
    return Retrieval('contextual_localized', [source], content)
