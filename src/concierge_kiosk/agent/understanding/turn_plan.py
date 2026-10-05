"""One-turn structured understanding for ambiguous concierge dialogue.

The local model may propose a bounded interpretation, but it cannot create a
service candidate, normalize a guest slot, or grant write authority.  The
parser therefore checks the model output against the original utterance and
the server-owned service registry before returning it to the application.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import unicodedata
from typing import Callable

from concierge_kiosk.agent.understanding.semantic import _chat
from concierge_kiosk.domain.service_registry import (
    accepted_slots, service_definition,
)
from concierge_kiosk.core.settings import SLM_NUM_CTX


TURN_PLAN_TYPES = frozenset({'read', 'write', 'smalltalk', 'answer_to_pending'})
MAX_INTENTS = 4
MAX_SLOTS_PER_INTENT = 8
MAX_RAW_CHARS = 5000


def turn_plan_schema() -> dict:
    """Return the strict Ollama structured-output schema.

    The schema describes protocol shape only.  Property-specific services and
    slot names are checked again by :func:`parse_turn_plan` against the signed
    runtime registry, so a model cannot expand authority by editing JSON.
    """
    span = {
        'type': 'object',
        'additionalProperties': False,
        'required': ['name', 'text'],
        'properties': {
            'name': {'type': 'string', 'minLength': 1, 'maxLength': 40},
            'text': {'type': 'string', 'minLength': 1, 'maxLength': 120},
        },
    }
    intent = {
        'type': 'object',
        'additionalProperties': False,
        'required': ['type', 'service_mode', 'slots', 'question', 'refers_to'],
        'properties': {
            'type': {'type': 'string', 'enum': sorted(TURN_PLAN_TYPES)},
            'service_mode': {'type': ['string', 'null'], 'maxLength': 64},
            'slots': {'type': 'array', 'maxItems': MAX_SLOTS_PER_INTENT, 'items': span},
            'question': {'type': ['string', 'null'], 'maxLength': 240},
            'refers_to': {'type': ['string', 'null'], 'maxLength': 80},
        },
    }
    return {
        'type': 'object',
        'additionalProperties': False,
        'required': ['intents'],
        'properties': {
            'intents': {'type': 'array', 'minItems': 1, 'maxItems': MAX_INTENTS,
                        'items': intent},
        },
    }


@dataclass(frozen=True)
class SlotSpan:
    name: str
    text: str

    def public(self) -> dict:
        return {'name': self.name, 'text': self.text}


@dataclass(frozen=True)
class TurnIntent:
    type: str
    service_mode: str | None
    slots: tuple[SlotSpan, ...]
    question: str | None
    refers_to: str | None

    def public(self) -> dict:
        return {
            'type': self.type,
            'service_mode': self.service_mode,
            'slots': [slot.public() for slot in self.slots],
            'question': self.question,
            'refers_to': self.refers_to,
        }


@dataclass(frozen=True)
class TurnPlan:
    intents: tuple[TurnIntent, ...]

    def public(self) -> dict:
        return {'intents': [intent.public() for intent in self.intents]}

    @property
    def writes(self) -> tuple[TurnIntent, ...]:
        return tuple(item for item in self.intents if item.type == 'write')


def _surface(value: str) -> str:
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def _verbatim_in_query(query: str, value: str) -> bool:
    needle = _surface(value)
    if not needle:
        return False
    return needle in _surface(query)


def _clean_optional(value: object, *, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    value = ' '.join(value.split()).strip()
    return value if 1 <= len(value) <= max_length else None


def parse_turn_plan(raw: str, *, query: str, language: str,
                    enabled_request_kinds: frozenset[str],
                    pending_reply: str | None = None) -> TurnPlan | None:
    """Validate and bind a model proposal to the current server state.

    ``None`` is the only failure result.  Callers must then use their
    deterministic route/fallback; no partially parsed intent is actionable.
    """
    if (not isinstance(raw, str) or not raw.strip() or len(raw) > MAX_RAW_CHARS
            or not query.strip()):
        return None
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(obj, dict) or set(obj) != {'intents'}:
        return None
    raw_intents = obj.get('intents')
    if not isinstance(raw_intents, list) or not 1 <= len(raw_intents) <= MAX_INTENTS:
        return None

    parsed: list[TurnIntent] = []
    for item in raw_intents:
        if not isinstance(item, dict) or set(item) != {
                'type', 'service_mode', 'slots', 'question', 'refers_to'}:
            return None
        intent_type = item.get('type')
        if intent_type not in TURN_PLAN_TYPES:
            return None
        service_mode = item.get('service_mode')
        if service_mode is not None and (
                not isinstance(service_mode, str) or not 1 <= len(service_mode) <= 64):
            return None
        slots_raw = item.get('slots')
        if not isinstance(slots_raw, list) or len(slots_raw) > MAX_SLOTS_PER_INTENT:
            return None
        slots: list[SlotSpan] = []
        seen_names: set[str] = set()
        for slot in slots_raw:
            if not isinstance(slot, dict) or set(slot) != {'name', 'text'}:
                return None
            name, text = slot.get('name'), slot.get('text')
            if (not isinstance(name, str) or not 1 <= len(name) <= 40
                    or not isinstance(text, str) or not 1 <= len(text) <= 120
                    or name in seen_names or not _verbatim_in_query(query, text)):
                return None
            seen_names.add(name)
            slots.append(SlotSpan(name, text))

        question = _clean_optional(item.get('question'), max_length=240)
        refers_to = _clean_optional(item.get('refers_to'), max_length=80)

        if intent_type == 'write':
            definition = service_definition(service_mode) if isinstance(service_mode, str) else None
            if (definition is None or definition.request_kind not in enabled_request_kinds
                    or definition.request_kind == 'directions'):
                return None
            accepted = set(accepted_slots(service_mode))
            if any(slot.name not in accepted for slot in slots):
                return None
            if question is not None or refers_to is not None:
                return None
        elif intent_type == 'read':
            if service_mode is not None or slots or not question:
                return None
            if refers_to is not None:
                return None
        elif intent_type == 'smalltalk':
            if service_mode is not None or slots or question is not None or refers_to is not None:
                return None
        else:  # answer_to_pending
            if pending_reply is None or service_mode is not None or slots:
                return None
            if refers_to is None or _surface(refers_to) != _surface(pending_reply):
                return None
            if question is not None:
                return None

        parsed.append(TurnIntent(intent_type, service_mode,
                                 tuple(slots), question, refers_to))

    # A plan may contain multiple independent reads/writes, but it must not
    # repeat the same semantic operation in one turn.
    signatures = {(item.type, item.service_mode, item.question, item.refers_to)
                  for item in parsed}
    if len(signatures) != len(parsed):
        return None
    return TurnPlan(tuple(parsed))


def model_turn_plan(*, query: str, language: str, base_url: str, model: str,
                    enabled_request_kinds: frozenset[str],
                    pending_reply: str | None = None,
                    candidate_labels: tuple[tuple[str, float], ...] = (),
                    nearest_examples: tuple[dict[str, str], ...] = (),
                    should_cancel: Callable[[], bool] | None = None,
                    timeout_seconds: float = 1.5) -> TurnPlan | None:
    """Ask one local model for a bounded plan and fail closed on any mismatch."""
    if not base_url or not model or not query.strip():
        return None
    pending_instruction = pending_reply or 'none'
    payload = {
        'model': model, 'stream': True, 'keep_alive': '5m',
        'format': turn_plan_schema(),
        'messages': [
            {'role': 'system', 'content': (
                'Interpret exactly one hotel concierge turn. Return only the JSON schema. '
                'Use read for a factual hotel question, write only for an explicitly requested '
                'service, smalltalk for social text, and answer_to_pending only when PENDING_REPLY '
                'matches. service_mode must be selected from AVAILABLE_SERVICES. Every slot text '
                'must be copied verbatim from GUEST_TURN. Never invent a room, quantity, price, '
                'availability, booking, identity or permission. A write is only a proposal; the '
                'server decides authority and confirmation. T2_CANDIDATES and NEAREST_EXAMPLES '
                'are hints only. If they do not clearly fit GUEST_TURN, return a safe read, '
                'smalltalk, or no actionable intent; never guess.')},
            {'role': 'user', 'content': json.dumps({
                'language': language, 'guest_turn': query[:500],
                'pending_reply': pending_instruction,
                't2_candidates': [
                    {'label': label, 'score': round(float(score), 4)}
                    for label, score in candidate_labels[:3]
                ],
                'nearest_examples': list(nearest_examples[:5]),
                'available_services': [
                    {'service_mode': code, 'request_kind': definition.request_kind,
                     'accepted_slots': list(accepted_slots(code))}
                    for code, definition in _available_services(enabled_request_kinds)
                ],
            }, ensure_ascii=False)},
        ],
        'options': {'temperature': 0, 'num_predict': 260, 'num_ctx': SLM_NUM_CTX},
    }
    raw = _chat(base_url, payload, min(10.0, max(0.05, timeout_seconds)), should_cancel)
    return parse_turn_plan(raw, query=query, language=language,
                           enabled_request_kinds=enabled_request_kinds,
                           pending_reply=pending_reply) if raw else None


def _available_services(enabled_request_kinds: frozenset[str]):
    # Local import avoids importing the full registry at module import time in
    # minimal parser/unit-test environments.
    from concierge_kiosk.domain.service_registry import SERVICE_DEFINITIONS
    return tuple((code, definition) for code, definition in SERVICE_DEFINITIONS.items()
                 if definition.request_kind in enabled_request_kinds)


__all__ = [
    'SlotSpan', 'TurnIntent', 'TurnPlan', 'TURN_PLAN_TYPES',
    'model_turn_plan', 'parse_turn_plan', 'turn_plan_schema',
]
