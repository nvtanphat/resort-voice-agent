"""Conservative, language-aware evidence relevance gates.

These checks answer a narrower question than semantic entailment: does the
approved *child passage* address the concrete subject/facet being asked about?
A document title or a high vector score must not turn unrelated boilerplate
into cited evidence. Returning no evidence is preferable to false attribution.
"""
from __future__ import annotations

import re
import unicodedata
from ..text.normalize import fold_accents
from ..text.tokenization import compatibility_terms, tokens
from concierge_kiosk.core.domain_profile import rag_policy
from concierge_kiosk.agent.understanding.domain_nlu import FACET_ALIASES, FACET_FACT_TYPES

# Query normalization/facet vocabulary is domain-profile data. The matching
# mechanics remain conservative code so config cannot bypass citation checks.
_RAG = rag_policy()
_QUERY_REWRITES = {
    language: tuple((item['pattern'], item['replacement']) for item in rewrites)
    for language, rewrites in _RAG.query_rewrites.items()
}
_QUERY_FILLERS = {language: set(values) for language, values in _RAG.query_fillers.items()}
_COMPOUND_TERMS = {language: tuple(values) for language, values in _RAG.compound_terms.items()}
_CONCRETE_FACETS = tuple(
    (item['question_pattern'], item['source_pattern'], bool(item['require_body']))
    for item in _RAG.concrete_facets
)
_EXPLICIT_TOPIC_PATTERNS = tuple(re.compile(pattern, re.I) for pattern in _RAG.explicit_topic_patterns.values())
_OPENING_HOURS = _RAG.opening_hours
_EXPLAIN_PATTERNS = {language: re.compile(pattern, re.I)
                     for language, pattern in _RAG.explain_patterns.items()}


def _query_key_value(query_keys, name: str):
    """Read query-key data from either a mapping or a small typed object.

    The answerability gate is deliberately independent of the router's concrete
    QueryKeys implementation.  This keeps the retrieval safety boundary usable
    by probes and by deployments that provide their own query-key adapter.
    """
    if isinstance(query_keys, dict):
        return query_keys.get(name)
    return getattr(query_keys, name, None)


def _contains_phrase(text: str, phrase: str) -> bool:
    surface = unicodedata.normalize('NFKC', text).casefold()
    target = unicodedata.normalize('NFKC', phrase).casefold().strip()
    if not target:
        return False
    if ' ' in target:
        return target in ' '.join(surface.split())
    if any(0x3400 <= ord(char) <= 0x9fff or 0xac00 <= ord(char) <= 0xd7af
           for char in target):
        return target in surface
    return bool(re.search(r'(?<!\w)' + re.escape(target) + r'(?!\w)', surface))


def requested_facets(query: str, language: str) -> tuple[str, ...]:
    """Return configured question facets explicitly present in ``query``."""
    return tuple(
        facet for facet, aliases in FACET_ALIASES.items()
        if any(_contains_phrase(query, alias) for alias in aliases)
    )


def normalized_query(query: str, language: str) -> str:
    text = query.casefold()
    for pattern, replacement in _QUERY_REWRITES.get(language, ()):
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return ' '.join(text.split())


def question_type(query: str, language: str) -> str:
    """Classify the read goal for evidence budgeting, not answer generation."""
    pattern = _EXPLAIN_PATTERNS.get(language)
    return 'explain' if pattern and pattern.search(query) else 'fact'


def _query_tokens(query: str, language: str, *, stem: bool) -> set[str]:
    text = normalized_query(query, language)
    fillers = _QUERY_FILLERS.get(language, set())
    return {word for word in tokens(text, language=language, limit=None, stem=stem)
            if fold_accents(word) not in fillers}


def _compound_terms(text: str, language: str) -> set[str]:
    """Return profile-owned multi-word terms present in text.

    Vietnamese compounds deliberately preserve diacritics. Folding accents
    would make an unrelated proper name satisfy a service concept.
    """
    normalized = unicodedata.normalize('NFKC', text).casefold()
    found: set[str] = set()
    for phrase in _COMPOUND_TERMS.get(language, ()):
        canonical = unicodedata.normalize('NFKC', phrase).casefold()
        pattern = r'(?<!\w)' + re.escape(canonical).replace(r'\ ', r'\s+') + r'(?!\w)'
        if re.search(pattern, normalized, flags=re.IGNORECASE):
            found.add(canonical)
    return found


def _text_terms(text: str, language: str) -> set[str]:
    return ({unicodedata.normalize('NFC', word) for word in tokens(text, language=language, limit=None, stem=True)}
            | _compound_terms(text, language))


def query_terms(query: str, language: str) -> set[str]:
    # NFC matters for Hangul: searchable() has NFKD jamo, while its Korean
    # particle stems are composed syllables. Treat them as the same word.
    normalized = normalized_query(query, language)
    base = {unicodedata.normalize('NFC', word) for word in _query_tokens(query, language, stem=True)
            if not any(char.isdigit() for char in word)}
    return base | _compound_terms(normalized, language)


def _facet_terms(query: str, language: str, facets: tuple[str, ...]) -> set[str]:
    terms: set[str] = set()
    for facet in facets:
        for alias in FACET_ALIASES.get(facet, ()):
            if _contains_phrase(query, alias):
                terms.update(query_terms(alias, language))
    return terms


def answerable(query_keys, query: str, source: dict, *, language: str,
               dense_threshold: float | None = None) -> bool:
    """Check whether the top retrieved fact can answer the asked question.

    Retrieval relevance is intentionally broader than answerability.  This
    final gate rejects a nearby topic (for example a minibar amenity when the
    guest asks for a price) before generation or citation binding can turn it
    into a supported answer.  All facet-to-fact mappings come from the domain
    profile; the remaining lexical check is a conservative subject-presence
    test over the approved child metadata and passage.
    """
    if not isinstance(source, dict) or not isinstance(query, str) or not query.strip():
        return False
    explicit_facets = tuple(_query_key_value(query_keys, 'facets') or ())
    if not explicit_facets:
        explicit_facets = requested_facets(query, language)
    requested_types = set(_query_key_value(query_keys, 'fact_types') or ())
    if not requested_types:
        for facet in explicit_facets:
            requested_types.update(FACET_FACT_TYPES.get(facet, ()))
    source_type = str(source.get('fact_type') or '')
    # A typed question must be answered by the corresponding typed fact. An
    # untyped/entity-card chunk is not enough to certify a price, location, or
    # opening-hours claim.
    # Legacy/property-swap documents may predate typed canonical metadata. In
    # that case retain the lexical safety check below; typed rows must match
    # the requested facet exactly.
    if requested_types and source_type and source_type not in requested_types:
        return False

    source_language = source.get('language') if source.get('language') in _QUERY_FILLERS else language
    asked = query_terms(query, language)
    subject_terms = asked - _facet_terms(query, language, explicit_facets)
    subject_terms = {term for term in subject_terms
                     if len(term) > 1 and not any(char.isdigit() for char in term)}
    # A facet-only question such as a generic price/location lookup is safe
    # after the typed fact check. Subject presence is needed when the query
    # names a concrete item or place.
    if not subject_terms:
        return True

    available = _text_terms(
        ' '.join(str(source.get(key) or '') for key in
                 ('context_text', 'title', 'heading', 'content')),
        source_language,
    )
    overlap = _term_intersection(subject_terms, available)
    dense_similarity = source.get('dense_similarity')
    try:
        # Runtime profiles own the learned-embedding threshold. Standalone
        # callers without one fail closed to lexical evidence.
        threshold = float('inf') if dense_threshold is None else float(dense_threshold)
        if dense_similarity is not None and float(dense_similarity) >= threshold:
            return True
    except (TypeError, ValueError):
        pass
    required = 1 if len(subject_terms) <= 2 else 2
    return len(overlap) >= required and len(overlap) / len(subject_terms) >= 0.40


def is_opening_hours_query(query: str, language: str) -> bool:
    """Return whether profile rewrites classify this as an opening-hours facet."""
    return _OPENING_HOURS['canonical_token'] in query_terms(query, language)



def _term_intersection(asked: set[str], available: set[str]) -> set[str]:
    direct = asked.intersection(available)
    # Add accent-equivalent matches for the remaining terms instead of returning
    # early when one exact token already matched (e.g. Cafe/Café + Indochine).
    folded_available = {fold_accents(term) for term in available}
    folded = {term for term in asked - direct if fold_accents(term) in folded_available}
    return direct | folded

def _overlap_sufficient(asked: set[str], available: set[str], threshold: float) -> bool:
    """Dynamic lexical gate for natural/voice queries.

    Specific one/two-term queries stay strict.  Longer utterances only need two
    substantive terms after filler removal; otherwise harmless ASR words make a
    fixed 0.60 coverage threshold reject relevant evidence.
    """
    if not asked:
        return False
    # Profile-defined multi-word concepts are checked before Vietnamese syllable
    # overlap. A query about ``điều hòa`` must not pass merely because an
    # unrelated chunk contains ``phòng`` and ``bị`` as separate syllables.
    compound_asked = {term for term in asked if ' ' in term}
    if compound_asked and not compound_asked.intersection(available):
        return False
    overlap = len(_term_intersection(asked, available))
    if len(asked) == 1:
        return overlap == 1
    required_terms = 2
    effective_threshold = min(threshold, required_terms / len(asked))
    return overlap >= required_terms and overlap / len(asked) >= effective_threshold

def fts_query(query: str, language: str) -> str:
    # FTS stores original inflections (prices), while overlap compares stems
    # (price). Query both rather than silently breaking plural recall.
    words = (_query_tokens(query, language, stem=False) |
             _query_tokens(query, language, stem=True) |
             set(compatibility_terms(query, language)))
    return ' OR '.join('"' + word.replace('"', '""') + '"' for word in sorted(words))



def concrete_facets_supported(query: str, body: str, title: str = '', heading: str = '') -> bool:
    """A new named object/qualifier must not be replaced by an old topic."""
    topic_text = f'{title} {heading} {body}'
    for question_pattern, source_pattern, require_body in _CONCRETE_FACETS:
        if re.search(question_pattern, query, flags=re.IGNORECASE):
            if not re.search(source_pattern, body if require_body else topic_text, flags=re.IGNORECASE):
                return False
    return True


def has_explicit_topic(query: str) -> bool:
    """A new configured topic can be searched fresh; a bare deictic facet cannot."""
    return any(pattern.search(query) for pattern in _EXPLICIT_TOPIC_PATTERNS)


def evidence_relevant(query: str, language: str, body: str, title: str = '', heading: str = '',
                      *, threshold: float = 0.60) -> bool:
    """Prevent title-only/topic-only matches from certifying an unsupported facet.

    For a one-term broad navigation request, title/heading can identify a source;
    for a specific question, every candidate must match the child's own body and
    meet the substantive query-term threshold across body and title. This is a
    lexical guardrail, not a semantic proof of the answer.
    """
    if not concrete_facets_supported(query, body, title, heading):
        return False
    asked = query_terms(query, language)
    if not asked:
        return False
    # `normalized_query` maps multilingual opening-hours phrasings onto the
    # canonical facet token ``operating``. The evidence remains locale-native,
    # so do not require the English word "operating" to appear in a Vietnamese,
    # Korean or Chinese fact line. Instead, require a concrete clock range in
    # the child passage and match the remaining subject terms against its label.
    asks_hours = _OPENING_HOURS['canonical_token'] in asked
    body_words = _text_terms(body, language)
    label_words = _text_terms(f'{title} {heading}', language)
    all_words = body_words | label_words
    if asks_hours:
        if not re.search(_OPENING_HOURS['time_range_pattern'], body):
            return False
        subject_terms = asked - {_OPENING_HOURS['canonical_token'], *_OPENING_HOURS['ignored_subject_tokens']}
        if not subject_terms:
            return True
        # Chinese tokenization emits overlapping bigrams; a generic suffix such
        # as 咖啡厅 must not make another venue pass. If the query includes a
        # concrete multi-character CJK subject token, require that exact subject
        # to occur in the chunk label before applying the normal overlap gate.
        cjk_subjects = {term for term in subject_terms
                        if len(term) >= 4 and re.search(r'[\u4e00-\u9fff]', term)}
        if cjk_subjects and not _term_intersection(cjk_subjects, all_words):
            return False
        # Some approved facts put the facet in the child body rather than the
        # title (for example ``Opening hours ... (breakfast)``).  Require the
        # subject and the clock range in the same approved passage, while still
        # allowing the subject to be supplied by either the label or body.
        return _overlap_sufficient(subject_terms, all_words, threshold)
    # The entire query naming a document subject is a legitimate broad lookup.
    # This does NOT permit a specific unsupported facet in its title to pass.
    if asked.issubset(label_words):
        return True
    if len(asked) == 1:
        return bool(_term_intersection(asked, all_words))
    if not asked.intersection(body_words):
        return False
    return _overlap_sufficient(asked, all_words, threshold)

def candidate_relevant(query: str, language: str, search_text: str, body: str, title: str = '', heading: str = '',
                       *, threshold: float = 0.60) -> bool:
    """Decide whether a row is a plausible retrieval candidate.

    ``search_text`` may contain reviewed aliases/synonyms and is intentionally
    broader than the citable child evidence. This function is only a candidate
    gate; downstream claim/citation verification must still bind answers to
    ``body``. Commercial qualifiers such as price/free remain evidence-bound.
    """
    if evidence_relevant(query, language, body, title, heading, threshold=threshold):
        return True
    if not concrete_facets_supported(query, body, title, heading):
        return False
    asked = query_terms(query, language)
    if not asked:
        return False
    # Time questions require a concrete clock range in citable evidence, never
    # merely an alias such as "opening hours".
    if _OPENING_HOURS['canonical_token'] in asked:
        return False
    compound_asked = {term for term in asked if ' ' in term}
    if compound_asked:
        direct_words = _text_terms(f'{title} {heading} {body}', language)
        if not compound_asked.intersection(direct_words):
            return False
    searchable_words = _text_terms(search_text, language)
    overlap = asked.intersection(searchable_words)
    if len(asked) == 1:
        direct_words = _text_terms(f'{title} {heading} {body}', language)
        return bool(_term_intersection(asked, direct_words))
    if _overlap_sufficient(asked, searchable_words, threshold):
        return True
    if any(' ' in term for term in asked):
        return False
    # Candidate discovery is intentionally more recall-friendly than citation
    # verification.  A long natural request can include constraints (party size,
    # child age, desired time) that are not present in a general policy chunk.
    # One substantive term is enough only when it occurs in both the approved
    # label and body; downstream grounding still decides what may be claimed.
    label_words = _text_terms(f'{title} {heading}', language)
    body_words = _text_terms(body, language)
    return len(asked) >= 3 and bool(asked.intersection(label_words).intersection(body_words))
