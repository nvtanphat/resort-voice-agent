"""Bounded local-model fallback for service intent understanding.

The model may classify an otherwise unsupported utterance, but it never grants
write authority. Service capabilities and enabled property kinds are validated
against the pinned registry before the result can enter ordinary slot filling.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Callable

from concierge_kiosk.agent.understanding.semantic import _chat
from concierge_kiosk.core.domain_profile import nlu_policy
from concierge_kiosk.core.settings import SLM_NUM_CTX
from concierge_kiosk.domain.service_registry import SERVICE_DEFINITIONS, accepted_slots


_INTENTS = frozenset({'service_request', 'ambiguous', 'smalltalk', 'out_of_scope', 'unknown'})
_CONVERSATIONAL = frozenset({'smalltalk', 'out_of_scope'})
# A conversational reply may never carry hotel facts or promises: no digits
# (times, prices, rooms, phone numbers), links, or completion claims.
_UNSAFE_REPLY = re.compile(
    r"\d|https?:|www\.|@|\b(?:booked|reserved|confirmed|sent|dispatched|arranged|guarantee)\b"
    r"|đã (?:đặt|gửi|xác nhận|sắp xếp)|已(?:预订|预约|发送|确认)|예약(?:했|되었)|보냈", re.IGNORECASE)
_COMPLETION_CLAIMS = tuple(
    pattern for patterns in nlu_policy().intent["completion_claims"].values()
    for pattern in patterns
)


def safe_conversational_reply(reply: object) -> str | None:
    """Accept a short, fact-free conversational reply; otherwise None."""
    if not isinstance(reply, str):
        return None
    text = ' '.join(reply.split())
    if (not 2 <= len(text) <= 240 or _UNSAFE_REPLY.search(text)
            or any(re.search(pattern, text, re.IGNORECASE) for pattern in _COMPLETION_CLAIMS)):
        return None
    return text


@dataclass(frozen=True)
class ModelIntent:
    intent: str
    service_mode: str
    slots: dict[str, str | int]
    possible_safety_concern: bool
    reply: str | None = None


def _slot_is_verbatim(query: str, value: str | int) -> bool:
    surface = str(value).strip()
    return bool(surface) and surface.casefold() in query.casefold()


def parse_model_intent(raw: str, *, query: str,
                       enabled_request_kinds: frozenset[str]) -> ModelIntent | None:
    if not isinstance(raw, str) or len(raw) > 1800:
        return None
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    required = {'intent', 'service_mode', 'slots', 'possible_safety_concern'}
    if not isinstance(obj, dict) or not required <= set(obj) <= required | {'reply'}:
        return None
    intent, mode, slots, concern = (obj['intent'], obj['service_mode'], obj['slots'],
                                    obj['possible_safety_concern'])
    # Small local models sometimes echo the label menu ("service_request|...").
    if isinstance(intent, str) and '|' in intent:
        labels = [label.strip() for label in intent.split('|')]
        intent = labels[0] if labels and labels[0] in _INTENTS else intent
    if not isinstance(intent, str) or intent not in _INTENTS:
        return None
    if not isinstance(mode, str) or not isinstance(slots, dict) or type(concern) is not bool:
        return None
    definition = SERVICE_DEFINITIONS.get(mode)
    if intent == 'service_request':
        if definition is None or definition.request_kind not in enabled_request_kinds:
            return None
        allowed = frozenset(accepted_slots(mode))
        if set(slots) - allowed:
            return None
        clean: dict[str, str | int] = {}
        for key, value in slots.items():
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                return None
            # Keep the intent but drop an invented value (e.g. a room the guest
            # never said); ordinary slot filling asks the guest for it.
            if _slot_is_verbatim(query, value):
                clean[key] = value
        return ModelIntent(intent, mode, clean, concern)
    if mode or slots:
        return None
    reply = safe_conversational_reply(obj.get('reply')) if intent in _CONVERSATIONAL else None
    return ModelIntent(intent, '', {}, concern, reply)


def model_service_intent(*, query: str, language: str, base_url: str, model: str,
                         enabled_request_kinds: frozenset[str],
                         should_cancel: Callable[[], bool] | None = None,
                         timeout_seconds: float = 1.5) -> ModelIntent | None:
    if not base_url or not model or not query.strip():
        return None
    available = [
        {'service_mode': code, 'request_kind': definition.request_kind,
         'accepted_slots': list(accepted_slots(code))}
        for code, definition in SERVICE_DEFINITIONS.items()
        if definition.request_kind in enabled_request_kinds
    ]
    payload = {
        'model': model, 'stream': True, 'keep_alive': '5m', 'format': 'json',
        'messages': [
            {'role': 'system', 'content': (
                'Classify one hotel concierge guest turn. Return ONLY JSON with exactly '
                '{"intent":"service_request|ambiguous|smalltalk|out_of_scope|unknown","service_mode":"",'
                '"slots":{},"possible_safety_concern":false,"reply":""}. Choose service_mode only from '
                'AVAILABLE_SERVICES. Copy slot values verbatim from GUEST_TURN; never invent or '
                'normalize them. If the request is vague use ambiguous. Use smalltalk for greetings, thanks, '
                'goodbyes or chit-chat that asks for no hotel fact or service; use out_of_scope for '
                'questions unrelated to the hotel stay. For smalltalk and out_of_scope ONLY, write "reply": '
                'one or two warm, natural sentences in the language of the guest as a resort concierge, then '
                'offer help with hotel information, directions or services. The reply must contain NO '
                'hotel facts, numbers, times, prices or promises. Otherwise reply is "". If there may be danger, '
                'set possible_safety_concern=true, but do not classify or create an emergency alert.')},
            {'role': 'user', 'content': json.dumps({
                'language': language, 'guest_turn': query[:500],
                'available_services': available,
            }, ensure_ascii=False)},
        ],
        'options': {'temperature': 0.3, 'num_predict': 200, 'num_ctx': SLM_NUM_CTX},
    }
    raw = _chat(base_url, payload, min(10.0, max(0.05, timeout_seconds)), should_cancel)
    return parse_model_intent(raw, query=query, enabled_request_kinds=enabled_request_kinds) if raw else None


__all__ = ['ModelIntent', 'model_service_intent', 'parse_model_intent', 'safe_conversational_reply']
