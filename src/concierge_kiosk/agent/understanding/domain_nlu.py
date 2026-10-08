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




def _patterns(value: Mapping[str, str]) -> dict[str, re.Pattern[str]]:
    return {key: re.compile(pattern) for key, pattern in value.items()}


ROUTING = _POLICY.routing
SLOTS = _POLICY.slots
NORMALIZATION = _POLICY.normalization
NUMERALS = _POLICY.numerals
CLOCK = _POLICY.clock

EMERGENCY_TEXT = dict(_POLICY.intent['emergency_text'])
EMERGENCY_CONTACTS = dict(_POLICY.intent['emergency_contacts'])
EMERGENCY_EVENT_PATTERNS = {language: tuple((re.compile(pattern) for pattern in patterns)) for language, patterns in _POLICY.intent['emergency_event_patterns'].items()}
NEGATION_PATTERNS = _patterns(_POLICY.intent['negation_patterns'])
TIME_EXPRESSIONS = {language: dict(values) for language, values in _POLICY.time_expressions.items()}
_VOICE = voice_policy()
FILLER_TERMS = _terms(_VOICE['filler_terms'])
SELF_CORRECTION_MARKERS = _terms(_VOICE['self_correction_markers'])


ROUTING_STATIC_TEXT = {category: dict(values) for category, values in ROUTING["static_text"].items()}
AFFIRM_TERMS = _terms(ROUTING["affirm_terms"])
DENY_TERMS = _terms(ROUTING["deny_terms"])

NUMBER_WORDS = {language: dict(words) for language, words in SLOTS["number_words"].items()}
NUMBER_CONNECTORS = _terms(SLOTS["number_connectors"])
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


QUALIFIER_PATTERNS = _patterns(_POLICY.qualifier_patterns)
