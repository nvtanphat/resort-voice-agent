"""Compiled, checksum-pinned NLU vocabulary for the concierge domain.

Matching algorithms stay in Python while multilingual/domain
vocabulary to ``agent-domain.json``.  This module is the only adapter that
turns profile strings into immutable tuples and compiled regular expressions.
"""
from __future__ import annotations

import re
from typing import Mapping

from concierge_kiosk.core.domain_profile import nlu_policy, voice_policy
from concierge_kiosk.core.domain_vocab import entity_terms, service_terms

_POLICY = nlu_policy()


def _terms(value: Mapping[str, list[str]]) -> dict[str, tuple[str, ...]]:
    return {key: tuple(items) for key, items in value.items()}


def _nested_terms(value: Mapping[str, Mapping[str, list[str]]]) -> dict[str, dict[str, tuple[str, ...]]]:
    return {outer: {inner: tuple(items) for inner, items in nested.items()}
            for outer, nested in value.items()}


def _patterns(value: Mapping[str, str]) -> dict[str, re.Pattern[str]]:
    return {key: re.compile(pattern) for key, pattern in value.items()}


def _nested_pattern_strings(value: Mapping[str, Mapping[str, list[str]]]) -> dict[str, dict[str, tuple[str, ...]]]:
    return {outer: {inner: tuple(items) for inner, items in nested.items()}
            for outer, nested in value.items()}


INTENT = _POLICY.intent
AUTHORITY = _POLICY.authority
ROUTING = _POLICY.routing
SLOTS = _POLICY.slots
MEMORY_VOCABULARY = _POLICY.memory_vocabulary
READ_INTENT = _POLICY.read_intent
NORMALIZATION = _POLICY.normalization
NUMERALS = _POLICY.numerals
CLOCK = _POLICY.clock

EMERGENCY_TEXT = dict(INTENT["emergency_text"])
EMERGENCY_CONTACTS = dict(INTENT["emergency_contacts"])
EMERGENCY_EVENT_PATTERNS = {
    language: tuple(re.compile(pattern) for pattern in patterns)
    for language, patterns in INTENT["emergency_event_patterns"].items()
}
NEGATION_PATTERNS = _patterns(INTENT["negation_patterns"])
TIME_EXPRESSIONS = {language: dict(values) for language, values in _POLICY.time_expressions.items()}
DISCOURSE_TERMS = _terms({language: list(values) for language, values in _POLICY.discourse_terms.items()})
_VOICE = voice_policy()
FILLER_TERMS = _terms(_VOICE['filler_terms'])
SELF_CORRECTION_MARKERS = _terms(_VOICE['self_correction_markers'])

TENTATIVE_TERMS = _terms(AUTHORITY["tentative_terms"])
EXPLICIT_TERMS = _terms(AUTHORITY["explicit_terms"])
RESTRICTED_TERMS = _terms(AUTHORITY["restricted_terms"])
IMPERATIVE_PATTERNS = _patterns(AUTHORITY["imperative_patterns"])

ROUTING_STATIC_TEXT = {category: dict(values) for category, values in ROUTING["static_text"].items()}
GREETING_TERMS = _terms(ROUTING["greeting_terms"])
THANKS_TERMS = _terms(ROUTING.get("thanks_terms", {}))
COURTESY_PARTICLES = _terms(ROUTING["courtesy_particles"])
CONFIRMATION_TERMS = _terms(ROUTING["confirmation_terms"])
AFFIRM_TERMS = _terms(ROUTING["affirm_terms"])
DENY_TERMS = _terms(ROUTING["deny_terms"])
BARE_TOPIC_TERMS = {
    language: frozenset((*items, *entity_terms(language)))
    for language, items in ROUTING["bare_topic_terms"].items()
}
LANGUAGE_SWITCH_TERMS = _terms(ROUTING["language_switch_terms"])
SWITCH_COMMAND_PATTERNS = _patterns(ROUTING["switch_command_patterns"])
KOREAN_TARGET_FIRST_PATTERN = re.compile(ROUTING["korean_target_first_pattern"])
SEQUENCE_PATTERN = re.compile(ROUTING["sequence_pattern"])

NUMBER_WORDS = {language: dict(words) for language, words in SLOTS["number_words"].items()}
NUMBER_CONNECTORS = _terms(SLOTS["number_connectors"])
ROOM_REFERENCE_TERMS = _terms(SLOTS["room_reference_terms"])
ROOM_PATTERNS = {language: tuple(patterns) for language, patterns in SLOTS["room_patterns"].items()}
TIME_PATTERNS = tuple(SLOTS["time_patterns"])
RELATIVE_TIME_TERMS = _terms(SLOTS["relative_time_terms"])
QUANTITY_NOUNS = {
    language: tuple(dict.fromkeys((*items, *service_terms(language))))
    for language, items in _terms(SLOTS["quantity_nouns"]).items()
}
PARTY_SIZE_PATTERNS = {language: tuple(patterns) for language, patterns in SLOTS["party_size_patterns"].items()}
PARTY_SIZE_FULL_PATTERNS = dict(SLOTS["party_size_full_patterns"])
CLOCK_DAYPARTS = {language: dict(values) for language, values in SLOTS["clock_dayparts"].items()}
CLOCK_DAYPART_PATTERNS = _patterns(SLOTS["clock_daypart_patterns"])
SHORT_TIME_MARKERS = _terms(SLOTS["short_time_markers"])
SLOT_LABELS = {language: dict(values) for language, values in SLOTS["slot_labels"].items()}
CLARIFICATION_TEXT = dict(SLOTS["clarification_text"])
READY_TEXT = dict(SLOTS["ready_text"])

FOLLOWUP_MARKERS = _terms(MEMORY_VOCABULARY["followup_markers"])
ACTION_FOLLOWUP_TERMS = _terms(MEMORY_VOCABULARY["action_followup_terms"])
SUBJECT_HINTS = {name: tuple(values) for name, values in MEMORY_VOCABULARY["subject_aliases"].items()}
FOCUS_HINTS = {name: tuple(values) for name, values in MEMORY_VOCABULARY["focus_aliases"].items()}
AMBIGUOUS_REFERENCE_MARKERS = _terms(MEMORY_VOCABULARY["ambiguous_reference_markers"])
PENDING_QUESTION_START_PATTERNS = _patterns(MEMORY_VOCABULARY["pending_question_start_patterns"])
FACET_ALIASES = {name: tuple(values) for name, values in MEMORY_VOCABULARY["facet_aliases"].items()}
FACET_SEARCH = {name: dict(values) for name, values in MEMORY_VOCABULARY["facet_search"].items()}
FACET_FACT_TYPES = {name: tuple(values) for name, values in MEMORY_VOCABULARY["facet_fact_types"].items()}

QUALIFIER_PATTERNS = _patterns(_POLICY.qualifier_patterns)
