"""Conservative, language-aware evidence relevance gates.

These checks answer a narrower question than semantic entailment: does the
approved *child passage* address the concrete subject/facet being asked about?
A document title or a high vector score must not turn unrelated boilerplate
into cited evidence. Returning no evidence is preferable to false attribution.
"""
from __future__ import annotations

import unicodedata
from ..text.normalize import fold_accents
from ..text.tokenization import compatibility_terms, tokens


def _query_key_value(query_keys, name: str):
    """Read query-key data from either a mapping or a small typed object.

    The answerability gate is deliberately independent of the router's concrete
    QueryKeys implementation.  This keeps the retrieval safety boundary usable
    by probes and by deployments that provide their own query-key adapter.
    """
    if isinstance(query_keys, dict):
        return query_keys.get(name)
    return getattr(query_keys, name, None)




def _text_terms(text: str, language: str) -> set[str]:
    return {unicodedata.normalize('NFC', word) for word in tokens(text, language=language, limit=None, stem=True)}


def query_terms(query: str, language: str) -> set[str]:
    # NFC matters for Hangul: searchable() has NFKD jamo, while its Korean
    # particle stems are composed syllables. Treat them as the same word.
    base = {unicodedata.normalize('NFC', word) for word in tokens(query, language=language, limit=None, stem=True) if not any((char.isdigit() for char in word))}
    return base




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
    requested_types = set(_query_key_value(query_keys, 'fact_types') or ())
    source_type = str(source.get('fact_type') or '')
    # A typed question must be answered by the corresponding typed fact. An
    # untyped/entity-card chunk is not enough to certify a price, location, or
    # opening-hours claim.
    # Legacy/property-swap documents may predate typed canonical metadata. In
    # that case retain the lexical safety check below; typed rows must match
    # the requested facet exactly.
    if requested_types and source_type and source_type not in requested_types:
        return False
    entities = set(_query_key_value(query_keys, 'entity_ids') or ())
    source_entity = str(source.get('entity_id') or '')
    if entities and source_entity:
        if source_entity not in entities:
            return False
        if requested_types and source_type in requested_types:
            # Canonical entity+facet identity supports multilingual lookup
            # without requiring accidental lexical overlap across scripts.
            return True

    source_language = source.get('language') or language
    asked = query_terms(query, language)
    subject_terms = asked
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
    # A geographic token in a passage (or a strong dense score) cannot make
    # an unrelated entity fact answer a question about another subject. For
    # unscoped typed facts require a subject match in the fact's own label.
    # Typed facets are checked above; legacy untyped passages retain the
    # existing lexical gate rather than requiring new metadata.
    labels = _text_terms(' '.join(str(source.get(key) or '')
                                  for key in ('title', 'heading')), source_language)
    if source_type and not requested_types:
        label_overlap = _term_intersection(subject_terms, labels)
        if not label_overlap:
            return False
        # Matching the place name alone cannot answer an unclassified aspect.
        # With no canonical entity identity, require remaining request words
        # in the passage. Canonical unscoped questions retain the lexical
        # recall gate below; only entity+facet identity above certifies a
        # typed aspect independently of surface wording.
        if not (entities and source_entity):
            aspect_terms = subject_terms - label_overlap
            if aspect_terms - _term_intersection(aspect_terms, available):
                return False
    required = 1 if len(subject_terms) <= 2 else 2
    return len(overlap) >= required and len(overlap) / len(subject_terms) >= 0.40





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
    overlap = len(_term_intersection(asked, available))
    if len(asked) == 1:
        return overlap == 1
    required_terms = 2
    effective_threshold = min(threshold, required_terms / len(asked))
    return overlap >= required_terms and overlap / len(asked) >= effective_threshold

def fts_query(query: str, language: str) -> str:
    # FTS stores original inflections (prices), while overlap compares stems
    # (price). Query both rather than silently breaking plural recall.
    words = set(tokens(query, language=language, limit=None, stem=False)) | set(tokens(query, language=language, limit=None, stem=True)) | set(compatibility_terms(query, language))
    return ' OR '.join('"' + word.replace('"', '""') + '"' for word in sorted(words))





def evidence_relevant(query: str, language: str, body: str, title: str = '', heading: str = '',
                      *, threshold: float = 0.60) -> bool:
    """Prevent title-only/topic-only matches from certifying an unsupported facet.

    For a one-term broad navigation request, title/heading can identify a source;
    for a specific question, every candidate must match the child's own body and
    meet the substantive query-term threshold across body and title. This is a
    lexical guardrail, not a semantic proof of the answer.
    """
    asked = query_terms(query, language)
    if not asked:
        return False
    # `normalized_query` maps multilingual opening-hours phrasings onto the
    # canonical facet token ``operating``. The evidence remains locale-native,
    # so do not require the English word "operating" to appear in a Vietnamese,
    # Korean or Chinese fact line. Instead, require a concrete clock range in
    # the child passage and match the remaining subject terms against its label.
    body_words = _text_terms(body, language)
    label_words = _text_terms(f'{title} {heading}', language)
    all_words = body_words | label_words
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
    asked = query_terms(query, language)
    if not asked:
        return False
    # Time questions require a concrete clock range in citable evidence, never
    # merely an alias such as "opening hours".
    if len(asked) == 1:
        direct_words = _text_terms(f'{title} {heading} {body}', language)
        return bool(_term_intersection(asked, direct_words))
    searchable_words = _text_terms(search_text, language)
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
