"""Structured service-intent slot collection for the concierge agent.

This module never commits business state. It extracts a small allowlisted set
of operational fields and identifies missing blockers. The authority policy
then decides whether a ready action may be autonomously queued, requires guest
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
    CANCEL_TERMS as _CANCEL_TERMS,
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
                                                     required_slots, resolve_service_code, service_definition)

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


def service_mode(query: str, language: str, kind: str) -> str:
    if kind not in _SERVICE_KINDS:
        raise ValueError('Unsupported service kind')
    mode = resolve_service_code(normalize_intent_text(query, language), language, kind)
    if mode is None:
        # The pinned profile is semantically required to provide one default
        # service for every actionable request kind. Fail closed if a caller
        # somehow bypasses that invariant.
        raise ValueError('Service kind has no configured default')
    return mode


def _room_number(text: str, language: str) -> str | None:
    surface = normalize_number_words(text, language).strip()
    for pattern in _ROOM_PATTERNS.get(language, ()):
        match = re.search(pattern, surface, flags=re.IGNORECASE)
        if match:
            return match.group(1).upper()
    # Slot-only continuation after the agent explicitly requested a room.
    if re.fullmatch(r'[A-Za-z]?\d{2,5}', surface):
        return surface.upper()
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


def extract_slots(query: str, language: str, kind: str, *, existing: Mapping[str, str | int] | None = None,
                  mode: str | None = None) -> dict[str, str | int]:
    if kind not in _SERVICE_KINDS:
        raise ValueError('Unsupported service kind')
    selected_mode = mode or service_mode(query, language, kind)
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


def assess_service(query: str, language: str, kind: str, *, existing: Mapping[str, str | int] | None = None,
                   mode: str | None = None) -> ServiceSlotAssessment:
    selected_mode = mode or service_mode(query, language, kind)
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


def _strip_courtesy_tokens(text: str, language: str) -> str:
    """Remove leading/trailing politeness words so "please cancel" == "cancel"."""
    particles = sorted((normalize_intent_text(item) for item in _COURTESY_PARTICLES.get(language, ())),
                       key=len, reverse=True)
    value = text
    changed = True
    while changed and value:
        changed = False
        for particle in particles:
            latin = all(ord(char) < 0x2E80 for char in particle)
            if latin:
                if value.startswith(particle + ' '):
                    value, changed = value[len(particle) + 1:].strip(), True
                elif value.endswith(' ' + particle):
                    value, changed = value[:-len(particle) - 1].strip(), True
            elif value != particle and (value.startswith(particle) or value.endswith(particle)):
                value = (value[len(particle):] if value.startswith(particle)
                         else value[:-len(particle)]).strip()
                changed = True
            if changed:
                break
    return value


def is_cancel_pending(query: str, language: str) -> bool:
    """A cancel command is the WHOLE utterance, not a word inside a question.

    "cancel" and "please cancel" cancel a pending draft; "what is the
    cancellation policy?" or "can I cancel my booking tomorrow?" do not.
    """
    normalized = normalize_intent_text(query, language)
    if '?' in normalized or '？' in normalized:
        return False
    normalized = _strip_courtesy_tokens(normalized.strip(' .!~,。！'), language)
    terms = {normalize_intent_text(term, language) for term in _CANCEL_TERMS.get(language, ())}
    return normalized in terms


def looks_like_slot_reply(query: str, language: str, missing: tuple[str, ...]) -> bool:
    """High-precision continuation detector so unrelated questions are not hijacked."""
    text = query.strip()
    if not text or len(text) > 120:
        return False
    normalized = normalize_number_words(text, language)
    probes = {
        'room_number': _room_number(text, language) is not None,
        'quantity': bool(re.search(r'(?<!\d)\d{1,2}(?!\d)', normalized)) or any(
            re.search(rf'\b{re.escape(word)}\b', normalized)
            for word in _NUMBER_WORDS.get(language, {})),
        'party_size': _party_size(text, language) is not None,
        'preferred_time': _preferred_time(text, language) is not None,
    }
    if any(probes.get(slot, False) for slot in missing):
        return True
    # "2 cái" while the room is still missing adds a slot the guest had not
    # given yet. Accept such a bare fragment (no question, few words) instead
    # of dropping the pending task into knowledge search.
    if '?' in text or '？' in text or len(text.split()) > 4:
        return False
    return probes['quantity'] or probes['preferred_time']


def review_details(details: str, language: str, slots: Mapping[str, str | int]) -> str:
    """Append collected structured fields for legacy review clients.

    New clients may consume ``service_payload`` directly. Existing kiosk builds
    still receive the same information in human-readable details so a resumed
    slot is never lost before proposal preparation.
    """
    labels = _SLOT_LABELS[language]
    ordered = ('room_number', 'quantity', 'preferred_time', 'party_size')
    lines = [details.strip()]
    for key in ordered:
        value = slots.get(key)
        if value not in (None, ''):
            lines.append(f'{labels[key]}: {value}')
    return '\n'.join(lines)[:500]
