"""Structured service-intent slot collection for the concierge agent.

This module never commits business state. It extracts a small allowlisted set
of operational fields and identifies missing blockers. The authority policy
then prepares a ready action for guest confirmation, requires guest
confirmation, or must be denied.

The parser is deterministic and conservative. Guest text remains untrusted;
all durable writes still go through authenticated, idempotent application
workflows rather than model output.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping

from concierge_kiosk.agent.understanding.intent import normalize_intent_text
from concierge_kiosk.agent.understanding.domain_nlu import (
    COURTESY_PARTICLES as _COURTESY_PARTICLES,
    CLARIFICATION_TEXT as _CLARIFICATION_TEXT,
    NUMBER_WORDS as _NUMBER_WORDS,
    PARTY_SIZE_FULL_PATTERNS as _PARTY_SIZE_FULL_PATTERNS,
    PARTY_SIZE_PATTERNS as _PARTY_SIZE_PATTERNS,
    QUANTITY_NOUNS as _QUANTITY_NOUNS,
    ROOM_PATTERNS as _ROOM_PATTERNS,
    READY_TEXT as _READY_TEXT,
    SLOT_LABELS as _SLOT_LABELS,
)
from concierge_kiosk.agent.tools.numerals import normalize_number_words, preferred_time
from concierge_kiosk.domain.service_registry import (ACTION_REQUEST_KINDS, SERVICE_SLOTS, accepted_slots,
                                                     required_slots, service_definition)

# Domain-owned slot names and parsing vocabulary come from the
# checksum-pinned profile; deterministic parsing mechanics stay in code.
_ALLOWED_SLOTS = SERVICE_SLOTS
_SERVICE_KINDS = ACTION_REQUEST_KINDS

# slot parsing vocabulary is checksum-pinned in agent-domain.json.

# Required operational slots are defined canonically in domain.service_registry.
@dataclass(frozen=True)
class ServiceSlotAssessment:
    kind: str
    mode: str
    slots: dict[str, str | int]
    missing: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.missing

    def public_state(self) -> dict:
        definition = service_definition(self.mode)
        return {
            'service_kind': self.kind,
            'service_mode': self.mode,
            'department': definition.department if definition is not None else None,
            'collected_slots': dict(self.slots),
            'missing_slots': list(self.missing),
            'action_ready': self.ready,
        }


def _room_number(text: str, language: str) -> str | None:
    surface = normalize_number_words(text, language).strip()
    for pattern in _ROOM_PATTERNS.get(language, ()):
        match = re.search(pattern, surface, flags=re.IGNORECASE)
        if match:
            return match.group(1).upper()
    # Slot-only continuation after the agent explicitly requested a room.
    if re.fullmatch(r'[A-Za-z]?\d{2,5}', surface):
        return surface.upper()
    compact = re.sub(r'(?<=\d)\s+(?=\d)', '', surface)
    if re.fullmatch(r'[A-Za-z]?\d{2,5}', compact):
        return compact.upper()
    return None


def _number_near(text: str, language: str, nouns: tuple[str, ...]) -> int | None:
    normalized = normalize_number_words(text, language)
    escaped = '|'.join(re.escape(noun) for noun in nouns)
    # Number boundaries are intentionally digit-based.  This works for both
    # whitespace-separated Latin phrases and adjacent Han/Hangul transcripts.
    digit = re.search(rf'(?<!\d)(\d{{1,3}})(?!\d)\s*(?:{escaped})', normalized)
    if digit:
        return int(digit.group(1))
    digit = re.search(rf'(?:{escaped})\s*(?<!\d)(\d{{1,3}})(?!\d)', normalized)
    if digit:
        return int(digit.group(1))
    return None


def _quantity(text: str, language: str) -> int | None:
    return _number_near(text, language, _QUANTITY_NOUNS.get(language, ()))


def _party_size(text: str, language: str) -> int | None:
    normalized = normalize_number_words(text, language)
    for pattern in _PARTY_SIZE_PATTERNS.get(language, ()):
        match = re.search(pattern, normalized)
        if match:
            return int(match.group(1))
    full_pattern = _PARTY_SIZE_FULL_PATTERNS.get(language)
    if full_pattern and re.fullmatch(full_pattern, normalized):
        match = re.match(r'\d{1,3}', normalized)
        if match:
            return int(match.group())
    return None


def _preferred_time(text: str, language: str) -> str | None:
    return preferred_time(text, language)


def extract_slots(query: str, language: str, kind: str, *, mode: str,
                  existing: Mapping[str, str | int] | None = None) -> dict[str, str | int]:
    if kind not in _SERVICE_KINDS:
        raise ValueError('Unsupported service kind')
    selected_mode = mode
    supported = frozenset(accepted_slots(selected_mode))
    slots = {key: value for key, value in (existing or {}).items()
             if key in _ALLOWED_SLOTS and key in supported}
    if 'room_number' in supported:
        room = _room_number(query, language)
        if room:
            slots['room_number'] = room
    if 'quantity' in supported:
        qty = _quantity(query, language)
        if qty is not None:
            slots['quantity'] = qty
    if 'party_size' in supported:
        party = _party_size(query, language)
        if party is not None:
            slots['party_size'] = party
    if 'preferred_time' in supported:
        preferred = _preferred_time(query, language)
        if preferred:
            slots['preferred_time'] = preferred
    return slots


def assess_service(query: str, language: str, kind: str, *, mode: str,
                   existing: Mapping[str, str | int] | None = None) -> ServiceSlotAssessment:
    selected_mode = mode
    slots = extract_slots(query, language, kind, existing=existing, mode=selected_mode)
    required = required_slots(selected_mode)
    missing = tuple(name for name in required if not slots.get(name))
    return ServiceSlotAssessment(kind=kind, mode=selected_mode, slots=slots, missing=missing)


def clarification_text(language: str, missing: tuple[str, ...]) -> str:
    labels = _SLOT_LABELS[language]
    names = ', '.join(labels[name] for name in missing)
    return _CLARIFICATION_TEXT[language].format(names=names)


def ready_text(language: str) -> str:
    return _READY_TEXT[language]


