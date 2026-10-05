"""High-precision deterministic intent extraction for the concierge agent.

This module identifies candidate service/navigation intent only. It never grants
action authority: the authority policy decides auto-execute/confirm/deny and
authenticated workflows remain the only durable business-write boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from functools import lru_cache

from concierge_kiosk.agent.understanding.domain_nlu import (
    ACTION_PATTERNS as _ACTION_PATTERNS,
    ACTION_CONTEXT_PATTERNS as _ACTION_CONTEXT_PATTERNS,
    ACTION_PHRASES,
    EMERGENCY_EVENT_PATTERNS as _EMERGENCY_EVENT_PATTERNS,
    EMERGENCY_TEXT,
    EXPLICIT_QUESTION_REQUEST_PATTERNS as _EXPLICIT_QUESTION_REQUEST,
    INFORMATION_FRAME_PATTERNS as _INFORMATION_FRAME,
    INFORMATION_REQUEST_PATTERNS as _INFORMATION_REQUEST,
    INFO_ONLY,
    MULTI_CONNECTOR_PATTERNS as _MULTI_CONNECTOR,
    NEGATION_PATTERNS as _NEGATION,
    QUESTION_START_PATTERNS as _QUESTION_START,
    REQUEST_FRAME_PATTERNS as _REQUEST_FRAME,
    SERVICE_CONCEPT_TERMS as _SERVICE_CONCEPT_TERMS,
    FILLER_TERMS as _FILLER_TERMS,
    SELF_CORRECTION_MARKERS as _SELF_CORRECTION_MARKERS,
    NORMALIZATION,
    NUMBER_WORDS,
)
from concierge_kiosk.agent.understanding.normalization import normalize_with_spans
from concierge_kiosk.core.domain_vocab import entity_terms, service_terms


@dataclass(frozen=True)
class Suggestion:
    kind: str
    details: str


def emergency_response(query: str, language: str) -> str | None:
    """Return a deterministic safety response only for an active incident.

    Matching all supported languages is intentional because the selected UI
    language is not proof of the language currently spoken by the guest.
    """
    if language not in EMERGENCY_TEXT:
        return None
    text = _mask_catalog_terms(
        unicodedata.normalize('NFKC', query).casefold().strip(), language)
    if any(pattern.search(text) for patterns in _EMERGENCY_EVENT_PATTERNS.values()
           for pattern in patterns):
        return EMERGENCY_TEXT[language]
    return None


def _mask_catalog_terms(text: str, language: str) -> str:
    """Hide catalog names before T0 event matching."""
    # The spoken language can differ from the selected UI language, just like
    # the event patterns below. Mask every supported catalog locale first.
    for term in _self_triggering_catalog_terms(language):
        if any(unicodedata.name(char, '').startswith(
                ('CJK', 'HANGUL', 'HIRAGANA', 'KATAKANA')) for char in term):
            text = text.replace(term, ' __CATALOG__ ')
        else:
            text = re.sub(r'(?<!\w)' + re.escape(term) + r'(?!\w)',
                          ' __CATALOG__ ', text)
    return ' '.join(text.split())


@lru_cache(maxsize=8)
def _self_triggering_catalog_terms(language: str) -> tuple[str, ...]:
    """Catalog names that would match an emergency pattern on their own.

    Only these are masked ("First Aid & Medical Assistance" is a service name,
    not an incident). A device or place that merely appears in an incident
    report ("air conditioning" in "burning smell near the air conditioning")
    must stay visible to the event patterns.
    """
    codes = tuple(_EMERGENCY_EVENT_PATTERNS) or (language,)
    terms = dict.fromkeys(
        unicodedata.normalize('NFKC', str(term)).casefold().strip()
        for code in codes for term in (*service_terms(code), *entity_terms(code))
        if str(term).strip())
    patterns = [pattern for group in _EMERGENCY_EVENT_PATTERNS.values() for pattern in group]
    triggering = []
    for term in terms:
        if not any(pattern.search(term) for pattern in patterns):
            continue
        # Safety first: a bare incident word that a dataset also lists as a
        # service alias ("cấp cứu", "急救", "first aid") is still an
        # incident. Mask only proper names that carry more than that word
        # ("急救与医疗协助", "Sơ cấp cứu & hỗ trợ y tế").
        remainder = term
        for pattern in patterns:
            remainder = pattern.sub(' ', remainder)
        if _substantial_name(remainder):
            triggering.append(term)
    return tuple(sorted(triggering, key=len, reverse=True))


def _substantial_name(remainder: str) -> bool:
    words = re.findall(r'\w+', remainder)
    cjk = sum(1 for word in words for char in word
              if unicodedata.name(char, '').startswith(('CJK', 'HANGUL', 'HIRAGANA', 'KATAKANA')))
    latin_words = [word for word in words if not any(
        unicodedata.name(char, '').startswith(('CJK', 'HANGUL', 'HIRAGANA', 'KATAKANA')) for char in word)]
    return cjk >= 3 or len(latin_words) >= 2



# multilingual intent vocabulary is checksum-pinned in agent-domain.json.

def _uses_syllabic_or_logographic_script(term: str) -> bool:
    """Whether profile text is normally matched as an internal substring."""
    return any(unicodedata.name(char, '').startswith(('CJK', 'HANGUL', 'HIRAGANA', 'KATAKANA'))
               for char in term)


def _is_word_character(value: str) -> bool:
    return bool(value) and (value.isalnum() or value == '_')


def _remove_term(text: str, term: str) -> str:
    if _uses_syllabic_or_logographic_script(term):
        return text.replace(term, ' ')
    pieces: list[str] = []
    position = 0
    while True:
        found = text.find(term, position)
        if found < 0:
            pieces.append(text[position:])
            break
        before = text[found - 1:found]
        after = text[found + len(term):found + len(term) + 1]
        if not _is_word_character(before) and not _is_word_character(after):
            pieces.append(text[position:found])
            pieces.append(' ')
            position = found + len(term)
        else:
            pieces.append(text[position:found + len(term)])
            position = found + len(term)
    return ''.join(pieces)


def _remove_profile_terms(text: str, terms: tuple[str, ...]) -> str:
    for term in sorted((unicodedata.normalize('NFKC', value).casefold().strip()
                        for value in terms if str(value).strip()), key=len, reverse=True):
        text = _remove_term(text, term)
    return ' '.join(text.split())


@lru_cache(maxsize=4096)
def normalize_intent_text(text: str, language: str | None = None) -> str:
    """Normalize Unicode and profile-owned disfluencies before intent parsing.

    A correction marker means the words after it supersede the earlier attempt;
    this is important for voice turns such as ``room 305, no, I mean 306``.
    The vocabulary is configuration data, not a hidden language-specific rule.
    """
    normalized = normalize_with_spans(text, language).text
    languages = ((language,) if language in _FILLER_TERMS else tuple(_FILLER_TERMS))
    for code in languages:
        markers = sorted((unicodedata.normalize('NFKC', marker).casefold().strip()
                          for marker in _SELF_CORRECTION_MARKERS.get(code, ())),
                         key=len, reverse=True)
        for marker in markers:
            position = normalized.find(marker)
            if position >= 0:
                suffix = normalized[position + len(marker):].strip(' ,:;.-')
                if suffix:
                    normalized = suffix
                    break
        configured_noise = tuple(NORMALIZATION.get("noise_terms", {}).get(code, ()))
        normalized = _remove_profile_terms(
            normalized, (*_FILLER_TERMS.get(code, ()), *configured_noise))
    return ' '.join(normalized.strip(' ,;:.-').split())


def normalize_intent_with_spans(text: str, language: str | None = None):
    """Return the robust transcript normalization and its auditable edits."""
    return normalize_with_spans(text, language)


def _phrase_present(text: str, phrase: str) -> bool:
    """Prevent embedded Latin word matches; allow CJK grammatical suffixes."""
    if _uses_word_boundaries(phrase):
        return bool(re.search(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', text))
    return phrase in text


def _uses_word_boundaries(value: str) -> bool:
    """Return whether a profile phrase belongs to a whitespace-word script."""
    script_markers = ("CJK", "HANGUL", "HIRAGANA", "KATAKANA")
    return not any(any(marker in unicodedata.name(char, "") for marker in script_markers)
                   for char in value)


def _matched_action_pattern_kinds(text: str, language: str) -> set[str]:
    return {
        kind for kind, patterns in _ACTION_PATTERNS.get(language, {}).items()
        if any(re.search(pattern, text) for pattern in patterns)
    }


def matches_action_pattern(query: str, language: str, kind: str) -> bool:
    """Return whether a checksum-pinned action regex recognizes this utterance."""
    return kind in _matched_action_pattern_kinds(normalize_intent_text(query, language), language)


def is_non_action_utterance(query: str, language: str) -> bool:
    """Ambiguous transcripts must never be promoted to an actionable draft."""
    text = normalize_intent_text(query, language)
    if not text or language not in ACTION_PHRASES:
        return True
    # Failure reports such as "AC is not cooling" contain a lexical negation,
    # but an allowlisted action regex can prove that the negation describes the
    # broken facility rather than cancelling a request. Explicit commands such
    # as "don't fix the AC" do not match those anchored failure-report regexes.
    matched_action_patterns = _matched_action_pattern_kinds(text, language)
    if _NEGATION[language].search(text) and not matched_action_patterns:
        return True
    explicit_concept_request = len(_concept_service_kinds(text, language)) == 1
    # "Please tell me how to use the AC" asks for information about a service
    # concept. Only an anchored action regex may override an explicit
    # information-request verb; a bare concept term + politeness frame cannot.
    info_request = _INFORMATION_REQUEST.get(language)
    if info_request is not None and info_request.search(text):
        # An instructional frame such as “tell me how to use…” describes a
        # service, but does not request that service.  Let the profile-owned
        # information pattern win over catalog/action aliases.
        return True
    # A checksum-pinned anchored action regex is high-precision proof of
    # actionable intent. Natural permission-style requests may contain lexical
    # information markers (e.g. Chinese 可以) or end in a question mark without
    # becoming information-only queries.
    explicit_action_pattern = len(matched_action_patterns) == 1
    if (any(_phrase_present(text, phrase) for phrase in INFO_ONLY[language])
            and not (explicit_concept_request or explicit_action_pattern)):
        return True
    info_frame = _INFORMATION_FRAME.get(language)
    if info_frame is not None and info_frame.search(text) and not (explicit_concept_request or explicit_action_pattern):
        return True
    if _QUESTION_START[language].search(text) and not (explicit_concept_request or explicit_action_pattern):
        return True
    if ('?' in text or '？' in text) and not (
            _EXPLICIT_QUESTION_REQUEST[language].search(text) or explicit_concept_request or explicit_action_pattern):
        return True
    return False


# Regex/term matching mechanics remain in code; patterns and aliases are profile-owned.

def _concept_service_kinds(text: str, language: str) -> set[str]:
    frame = _REQUEST_FRAME.get(language)
    if frame is None or frame.search(text) is None:
        return set()
    kinds = {
        kind for kind, aliases in _SERVICE_CONCEPT_TERMS.get(language, {}).items()
        if any(_phrase_present(text, normalize_intent_text(alias, language)) for alias in aliases)
    }
    # The reviewed property vocabulary contains catalog services that are
    # intentionally handled by the generic staff/human workflow (medical
    # centre, lost & found, courier, and similar). Keep those terms data-driven
    # instead of duplicating a growing list in the routing profile.
    if (not kinds and not _matched_action_pattern_kinds(text, language)
            and any(_phrase_present(text, normalize_intent_text(term, language))
                    for term in service_terms(language))):
        kinds.add('human')
    # Catalog names are the source of truth for service identity.  The
    # generic concept vocabulary above intentionally stays small; this second
    # pass projects a catalog selector onto its configured request kind so a
    # newly added service works without another Python branch.
    catalog_kinds: set[str] = set()
    if not _matched_action_pattern_kinds(text, language):
        from concierge_kiosk.core.domain_profile import get_domain_profile
        catalog_text = _remove_profile_terms(
            text, tuple(NUMBER_WORDS.get(language, {}).keys()))
        for item in get_domain_profile().domain_vocab.get('services', ()):
            request_kind = item.get('request_kind') if isinstance(item, dict) else None
            if isinstance(request_kind, str) and any(
                    _phrase_present(catalog_text, normalize_intent_text(term, language))
                    for term in service_terms(language)
                    if term in tuple(item.get('names', {}).get(language, ())) +
                    tuple(item.get('aliases', {}).get(language, ()))):
                catalog_kinds.add(request_kind)
        kinds.update(catalog_kinds)
        # The generic fallback above is only for catalog nouns without a
        # request-kind annotation.  Once a reviewed catalog entry resolves to
        # a non-human kind, do not leave the fallback ``human`` candidate in
        # place and manufacture ambiguity.
        if catalog_kinds and catalog_kinds != {'human'}:
            kinds.discard('human')
        if not catalog_kinds:
            from concierge_kiosk.domain.service_registry import SERVICE_DEFINITIONS
            for definition in SERVICE_DEFINITIONS.values():
                if any(_phrase_present(text, term) for term in definition.match_terms.get(language, ())):
                    kinds.add(definition.request_kind)
    return kinds


def matched_service_kinds(query: str, language: str) -> set[str]:
    """Find *mentions* for ambiguity checks; never commit a tool here."""
    text = normalize_intent_text(query, language)
    return ({kind for kind, phrases in ACTION_PHRASES.get(language, {}).items()
             if any(_phrase_present(text, normalize_intent_text(phrase, language)) for phrase in phrases)} |
            _matched_action_pattern_kinds(text, language) | _concept_service_kinds(text, language))


def ordered_service_kinds(query: str, language: str) -> tuple[str, ...]:
    """Stable utterance order for recognized multi-intent review choices.

    No new action is inferred; only the high-precision authorized action list is
    ordered according to the actual phrase positions, not alphabetic order.
    """
    normalized = normalize_intent_text(query, language)
    matched = matched_service_kinds(query, language)
    positioned = []
    for kind in matched:
        locations = [normalized.find(normalize_intent_text(phrase, language))
                     for phrase in ACTION_PHRASES.get(language, {}).get(kind, ())
                     if _phrase_present(normalized, normalize_intent_text(phrase, language))]
        locations += [re.search(pattern, normalized).start()
                      for pattern in _ACTION_PATTERNS.get(language, {}).get(kind, ())
                      if re.search(pattern, normalized)]
        positioned.append((min(locations) if locations else len(normalized), kind))
    return tuple(kind for _, kind in sorted(positioned))



# Compound-clause vocabulary is profile-owned.

def _service_clause_analysis(query: str, language: str) -> tuple[tuple[tuple[str, str, int], ...], bool]:
    """Split a compound utterance and classify each clause independently.

    The key safety property is that negation is local to a clause. A guest may say
    "do not X, but do Y" without the negative clause cancelling the positive one.
    Conversely, a non-negated residual clause that cannot be classified makes the
    whole compound ambiguous, so the system does not silently execute only the
    part it happened to understand.
    """
    if language not in ACTION_PHRASES or not query or len(query) > 500:
        return (), False
    text = normalize_intent_text(query, language)
    connector = _MULTI_CONNECTOR.get(language)
    if connector is None or connector.search(text) is None:
        matched = matched_service_kinds(query, language)
        if len(matched) == 1 and not is_non_action_utterance(query, language):
            return ((next(iter(matched)), query.strip(), 0),), False
        return (), False

    pieces: list[tuple[int, str]] = []
    start = 0
    for match in connector.finditer(text):
        part = text[start:match.start()].strip(' ,;')
        if part:
            pieces.append((start, part))
        start = match.end()
    tail = text[start:].strip(' ,;')
    if tail:
        pieces.append((start, tail))

    clauses: list[tuple[str, str, int]] = []
    ambiguous_residual = False
    for position, clause in pieces:
        matched = matched_service_kinds(clause, language)
        non_action = is_non_action_utterance(clause, language)
        if len(matched) == 1 and not non_action:
            clauses.append((next(iter(matched)), clause, position))
            continue
        # Clearly negated/informational clauses are safe to ignore. Anything else
        # may be an elliptical or unsupported action and must not be dropped.
        clause_text = normalize_intent_text(clause, language)
        info_frame = _INFORMATION_FRAME.get(language)
        context_frame = any(pattern.search(clause_text)
                            for pattern in _ACTION_CONTEXT_PATTERNS.get(language, ()))
        clearly_non_action = (bool(_NEGATION[language].search(clause_text)) or non_action
                              or bool(info_frame and info_frame.search(clause_text))
                              or context_frame)
        if clause_text and not clearly_non_action:
            ambiguous_residual = True
    return tuple(clauses), ambiguous_residual


def service_clauses(query: str, language: str) -> tuple[tuple[str, str, int], ...]:
    """Return safely decomposed actionable clauses in utterance order."""
    clauses, ambiguous = _service_clause_analysis(query, language)
    return () if ambiguous else clauses


def _has_information_clause(query: str, language: str) -> bool:
    """Detect a separate information clause without promoting it to an action."""
    if language not in ACTION_PHRASES or not query or len(query) > 500:
        return False
    text = normalize_intent_text(query, language)
    connector = _MULTI_CONNECTOR.get(language)
    if connector is None or connector.search(text) is None:
        return False

    pieces: list[str] = []
    start = 0
    for match in connector.finditer(text):
        part = text[start:match.start()].strip(' ,;')
        if part:
            pieces.append(part)
        start = match.end()
    tail = text[start:].strip(' ,;')
    if tail:
        pieces.append(tail)

    info_frame = _INFORMATION_FRAME.get(language)
    info_request = _INFORMATION_REQUEST.get(language)
    question_start = _QUESTION_START.get(language)
    explicit_question = _EXPLICIT_QUESTION_REQUEST.get(language)
    for clause in pieces:
        matched = matched_service_kinds(clause, language)
        if len(matched) == 1 and not is_non_action_utterance(clause, language):
            continue
        if (_NEGATION[language].search(clause)
                and not (info_frame and info_frame.search(clause))):
            continue
        if ((info_frame and info_frame.search(clause))
                or (info_request and info_request.search(clause))
                or (question_start and question_start.search(clause))
                or (explicit_question and explicit_question.search(clause))
                or '?' in clause or '？' in clause):
            return True
    return False


def has_multiple_service_intents(query: str, language: str) -> bool:
    """True for 2+ services or a service plus a separate information task."""
    clauses = service_clauses(query, language)
    return len(clauses) > 1 or (bool(clauses) and _has_information_clause(query, language))


def suggest_service_request(query: str, language: str) -> Suggestion | None:
    text = normalize_intent_text(query, language)
    if not text or len(query) > 500 or language not in ACTION_PHRASES:
        return None
    clauses, ambiguous = _service_clause_analysis(query, language)
    # A recognized safety/human handoff must not be downgraded to knowledge
    # merely because the guest adds an operational context clause (for
    # example, "the safe will not open and I am leaving for the airport").
    # Handoff is non-transactional and remains the safe owner for the turn;
    # unknown residual text is not used to invent a second service.
    if ambiguous and len(clauses) == 1 and clauses[0][0] == 'human':
        return Suggestion('human', clauses[0][1][:500])
    if ambiguous or len(clauses) > 1:
        return None
    if len(clauses) == 1:
        kind, clause, _ = clauses[0]
        return Suggestion(kind, clause[:500])
    if is_non_action_utterance(query, language):
        return None
    matched = matched_service_kinds(query, language)
    if len(matched) != 1:
        return None
    return Suggestion(next(iter(matched)), query[:500])
