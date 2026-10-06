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
    EMERGENCY_EVENT_PATTERNS as _EMERGENCY_EVENT_PATTERNS,
    EMERGENCY_TEXT,
    INFORMATION_FRAME_PATTERNS as _INFORMATION_FRAME,
    INFORMATION_REQUEST_PATTERNS as _INFORMATION_REQUEST,
    FILLER_TERMS as _FILLER_TERMS,
    SELF_CORRECTION_MARKERS as _SELF_CORRECTION_MARKERS,
    NORMALIZATION,
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


def is_information_question(query: str, language: str) -> bool:
    """True when the turn explicitly asks for information.

    Only the profile's explicit information-request and information-frame
    patterns count ("tell me about...", "what are the hours..."). A question
    mark, info-only markers or question openers are not used: polite service
    requests carry them too (measured on the training split, they would block
    4-24% of requests). It names no service, so it can gate the model-free
    understanding fallback without a keyword list.
    """
    text = normalize_intent_text(query, language)
    if not text:
        return False
    for patterns in (_INFORMATION_REQUEST, _INFORMATION_FRAME):
        pattern = patterns.get(language)
        if pattern is not None and pattern.search(text):
            return True
    return False


# Regex/term matching mechanics remain in code; patterns and aliases are profile-owned.


# Compound-clause vocabulary is profile-owned.

