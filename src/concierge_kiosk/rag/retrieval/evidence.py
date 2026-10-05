"""Scoped evidence passages and same-section parent context."""
from __future__ import annotations
import re
from concierge_kiosk.persistence.sqlite_store import Store
from concierge_kiosk.core.domain_profile import nlu_policy, rag_policy
from ..documents import LANGUAGES
from ..text.normalize import fold_accents
from ..text.safety import unsafe_knowledge_text
from ..text.tokenization import tokens

_RAG_POLICY = rag_policy()
_POLICY_QUALIFIER = re.compile(nlu_policy().qualifier_patterns['grounding'], re.I)


def evidence_passage(body: str, query: str, *, language: str | None = None,
                     max_chars: int | None = None, require_term_overlap: bool = True) -> str:
    """Return a relevant local passage, never arbitrary first-N-character tails.

    User-supplied numbers are ranking hints, not a hard candidate filter.  A guest
    may mention party size, child age or ``3pm`` while the verified policy uses no
    such number (or formats the time as ``15:00``).  Numeric truth is enforced at
    claim generation/verification rather than by throwing away otherwise relevant
    evidence here.
    """
    if max_chars is None:
        max_chars = int((_RAG_POLICY.grounding_budgets.get("evidence_budget_chars") or {})["fact"])
    terms = set(tokens(query, language=language, limit=None, stem=True))
    numbers = set(re.findall(r'\d+(?:[.,:/-]\d+)*', query))
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', body) if p.strip()]
    if not paragraphs:
        return body if len(body) <= max_chars else ''
    spans = []
    for paragraph in paragraphs:
        qualified = bool(_POLICY_QUALIFIER.search(paragraph))
        if len(paragraph) <= max_chars:
            spans.append(paragraph)
            # A compact paragraph can still contain many independent facts.
            # Also rank complete sentence-sized evidence so a focused guest
            # question does not inherit unrelated claims from the same chunk.
            # Never split policy/exception text, where the qualifier must remain
            # attached to the rule it constrains.
            if not qualified:
                sentences = re.split(r'(?<=[.!?。！？])(?:\s+|(?=\S))', paragraph)
                if len(sentences) > 1:
                    spans.extend(sentence.strip() for sentence in sentences
                                 if sentence.strip() and len(sentence.strip()) <= max_chars)
        else:
            # Do not separate a rule from its exception just to satisfy a
            # character budget. A different complete paragraph may still fit.
            if qualified:
                continue
            # Never slice an unfinished policy sentence: an exception/negation
            # could be in its removed tail. Abstain if no complete span fits.
            sentences = re.split(r'(?<=[.!?。！？])(?:\s+|(?=\S))', paragraph)
            for sentence in sentences:
                sentence = sentence.strip()
                if sentence and len(sentence) <= max_chars:
                    spans.append(sentence)
    if not spans:
        return ''
    # Never manufacture a citation passage from an unrelated first/shortest
    # span. At least one substantive query term must occur in the selected span.
    if terms and require_term_overlap:
        folded_terms = {fold_accents(term) for term in terms}
        spans = [span for span in spans
                if (terms.intersection(tokens(span, language=language, limit=None, stem=True))
                     or folded_terms.intersection(
                         fold_accents(term) for term in tokens(span, language=language, limit=None, stem=True)))]
        if not spans:
            return ''
    def rank(span: str) -> tuple[int, int, int]:
        span_terms = set(tokens(span, language=language, limit=None, stem=True))
        found = set(re.findall(r'\d+(?:[.,:/-]\d+)*', span))
        return (int(numbers.issubset(found)), len(terms & span_terms), -len(span))
    selected = max(spans, key=rank)
    return selected


def retrieve_parent_context(store: Store, *, property_id: str, language: str,
                            source_id: str, revision: str, effective_date: str,
                            parent_id: str = '', section_id: str = '',
                            heading: str = '', max_chars: int = 2400) -> str:
    """Expand only the same currently authorized section/revision/property.

    New rows are joined by opaque parent/section identity. Heading fallback exists
    only for migrated legacy rows with no section metadata.
    """
    if language not in LANGUAGES or not 100 <= max_chars <= 4000:
        raise ValueError('Invalid parent context request')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', effective_date):
        raise ValueError('Explicit effective date required')
    with store.connection() as con:
        if parent_id:
            # Reassemble only currently effective children. The parent body is
            # a structural cache and may contain an expired promotion beside a
            # permanent fact for the same entity.
            rows = con.execute(
                "SELECT k.body FROM knowledge k JOIN knowledge_parents p ON p.id=k.parent_id "
                "AND p.revision=k.revision WHERE k.parent_id=? AND k.property_id=? AND k.language=? "
                "AND k.source=? AND k.revision=? AND p.classification='public' AND p.active=1 "
                "AND k.classification='public' AND k.active=1 AND k.effective_from<=? "
                "AND (k.effective_to IS NULL OR k.effective_to>=?) ORDER BY k.section_ordinal,k.id",
                (parent_id, property_id, language, source_id, revision, effective_date, effective_date)
            ).fetchall()
        elif section_id:
            rows = con.execute(
                "SELECT k.body FROM knowledge k JOIN knowledge_parents p ON p.id=k.parent_id "
                "AND p.revision=k.revision WHERE k.property_id=? AND k.language=? AND k.source=? "
                "AND k.revision=? AND p.section_id=? AND p.classification='public' AND p.active=1 "
                "AND k.classification='public' AND k.active=1 AND k.effective_from<=? "
                "AND (k.effective_to IS NULL OR k.effective_to>=?) ORDER BY k.section_ordinal,k.id",
                (property_id, language, source_id, revision, section_id,
                 effective_date, effective_date)
            ).fetchall()
        else:
            rows = []
        if rows:
            combined = '\n\n'.join(row['body'] for row in rows)
            return combined[:max_chars] if not unsafe_knowledge_text(combined) else ''
        # Migrated pre-rows may have no section identity. Their fallback remains
        # property/language/revision/date scoped and cannot cross a live parent.
        if not heading:
            return ''
        rows = con.execute(
            "SELECT body FROM knowledge WHERE property_id=? AND language=? "
            "AND source=? AND revision=? AND heading=? AND parent_id='' AND section_id='' "
            "AND classification='public' AND active=1 AND effective_from<=? "
            "AND (effective_to IS NULL OR effective_to>=?) ORDER BY id LIMIT 16",
            (property_id, language, source_id, revision, heading,
             effective_date, effective_date)
        ).fetchall()
    combined = '\n\n'.join(row['body'] for row in rows)
    return combined[:max_chars] if not unsafe_knowledge_text(combined) else ''
