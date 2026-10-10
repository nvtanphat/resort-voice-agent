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

from concierge_kiosk.agent.understanding.domain_nlu import EMERGENCY_EVENT_PATTERNS as _EMERGENCY_EVENT_PATTERNS, EMERGENCY_TEXT, FILLER_TERMS as _FILLER_TERMS, SELF_CORRECTION_MARKERS as _SELF_CORRECTION_MARKERS
from concierge_kiosk.agent.understanding.normalization import normalize_with_spans
from concierge_kiosk.core.domain_vocab import entity_terms, service_terms
from concierge_kiosk.core.domain_profile import nlu_policy, get_domain_profile
from concierge_kiosk.agent.understanding.intent_evidence import unquoted


@dataclass(frozen=True)
class Suggestion:
    kind: str
    details: str


def emergency_response(query: str, language: str) -> str | None:
    """Return a deterministic safety response only for an active incident.

    Matching all supported languages is intentional because the selected UI
    language is not proof of the language currently spoken by the guest.
    """
    if emergency_tier1(query, language) == 'emergency':
        return EMERGENCY_TEXT[language]
    return None


def emergency_tier1(query: str, language: str) -> str | None:
    """Apply bounded active-incident grammar, then the configured review zone.

    Exclusions are scoped to clauses/matches. A negated smoke mention cannot
    hide a different active incident. Full incidents always precede review and
    require neither a model nor confirmation. Vocabulary stays profile-owned.
    """
    if language not in EMERGENCY_TEXT:
        return None
    policy = nlu_policy().intent
    text = _mask_catalog_terms(unicodedata.normalize('NFKC', unquoted(query)).casefold().strip(), language)
    connectors = [term for terms in get_domain_profile().semantic_authorization['clause_connectors'].values() for term in terms]
    separators = [r'[;,.!?\n\u3002\uff0c\uff1b\uff1f]'] + [
        (r'(?<!\w)' + re.escape(term) + r'(?!\w)') if not _uses_syllabic_or_logographic_script(term) else re.escape(term)
        for term in connectors]
    pieces = re.split('|'.join(separators), text)
    # Preserve approved incident relationships spanning a connector, such as
    # underwater + not responding; fragments separately allow a current event
    # after a historical clause to retain priority.
    pieces += re.split(r'[;.!?\n\u3002\uff1b\uff1f]', text)
    review = False
    for branch, groups in (
            ('emergency', policy['emergency_event_patterns']),
            ('emergency_check', policy.get('emergency_review_patterns', {}))):
        for code, patterns in groups.items():
            context = policy.get('emergency_context_patterns', {}).get(code, {})
            for piece in pieces:
                if any(re.search(pattern, piece) for key in ('historical', 'informational')
                       for pattern in context.get(key, ())):
                    continue
                negative = [match.span() for pattern in context.get('negated', ())
                            for match in re.finditer(pattern, piece)]
                # A fire word that also names a burnt-out part ("the bulb has burnt out")
                # is asked about, never ignored: the guest confirms a real fire in one tap.
                fault = [match.span() for pattern in context.get('fault_sense', ())
                         for match in re.finditer(pattern, piece)]
                for pattern in patterns:
                    for match in re.finditer(pattern, piece):
                        if any(start < match.end() and match.start() < end for start, end in negative):
                            continue
                        if branch == 'emergency' and any(start < match.end() and match.start() < end
                                                         for start, end in fault):
                            review = True
                            continue
                        return branch
    return 'emergency_check' if review else None


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
        normalized = _remove_profile_terms(normalized, _FILLER_TERMS.get(code, ()))
    return ' '.join(normalized.strip(' ,;:.-').split())





# Regex/term matching mechanics remain in code; patterns and aliases are profile-owned.


# Compound-clause vocabulary is profile-owned.
