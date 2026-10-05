"""Closed understanding commands shared by text, voice and the agent runtime.

Commands describe what the guest appears to mean; they never authorize a
database write.  Service names and slot names are checked against the signed
runtime registry before a command stream is accepted.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import unicodedata
from typing import Any, Iterable

from concierge_kiosk.agent.understanding.semantic import _chat
from concierge_kiosk.core.settings import SLM_NUM_CTX
from concierge_kiosk.domain.service_registry import accepted_slots, service_definition


COMMAND_TYPES = frozenset({
    'StartGoal', 'SetSlot', 'CorrectSlot', 'Cancel', 'Confirm',
    'AskInfo', 'Navigate', 'Handoff', 'ChitChat',
})
MAX_COMMANDS = 8
MAX_SLOTS = 8
MAX_TEXT = 300


@dataclass(frozen=True)
class CommandSlot:
    name: str
    text: str

    def public(self) -> dict[str, str]:
        return {'name': self.name, 'text': self.text}


@dataclass(frozen=True)
class Command:
    """A validated, non-authoritative guest-intent command."""

    type: str
    goal: str | None = None
    slots: tuple[CommandSlot, ...] = ()
    query: str | None = None
    keys: tuple[str, ...] = ()
    field: str | None = None
    value: str | None = None
    confirmed: bool | None = None
    reason: str | None = None

    def public(self) -> dict[str, Any]:
        result: dict[str, Any] = {'type': self.type}
        for key, value in (
                ('goal', self.goal), ('query', self.query), ('field', self.field),
                ('value', self.value), ('confirmed', self.confirmed), ('reason', self.reason)):
            if value is not None:
                result[key] = value
        if self.slots:
            result['slots'] = [slot.public() for slot in self.slots]
        if self.keys:
            result['keys'] = list(self.keys)
        return result


def command_schema() -> dict[str, Any]:
    """Return the closed JSON schema used by an understanding model."""
    slot = {
        'type': 'object', 'additionalProperties': False,
        'required': ['name', 'text'],
        'properties': {
            'name': {'type': 'string', 'minLength': 1, 'maxLength': 64},
            'text': {'type': 'string', 'minLength': 1, 'maxLength': 120},
        },
    }
    item = {
        'type': 'object', 'additionalProperties': False,
        'required': ['type'],
        'properties': {
            'type': {'type': 'string', 'enum': sorted(COMMAND_TYPES)},
            'goal': {'type': ['string', 'null'], 'maxLength': 96},
            'slots': {'type': 'array', 'maxItems': MAX_SLOTS, 'items': slot},
            'query': {'type': ['string', 'null'], 'maxLength': MAX_TEXT},
            'keys': {'type': 'array', 'maxItems': 8,
                     'items': {'type': 'string', 'minLength': 1, 'maxLength': 64}},
            'field': {'type': ['string', 'null'], 'maxLength': 64},
            'value': {'type': ['string', 'null'], 'maxLength': 120},
            'confirmed': {'type': ['boolean', 'null']},
            'reason': {'type': ['string', 'null'], 'maxLength': 160},
        },
    }
    return {
        'type': 'object', 'additionalProperties': False,
        'required': ['commands'],
        'properties': {
            'commands': {'type': 'array', 'minItems': 1, 'maxItems': MAX_COMMANDS,
                         'items': item},
        },
    }


def _surface(value: str) -> str:
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def _verbatim(query: str, value: str) -> bool:
    return bool(value.strip()) and _surface(value) in _surface(query)


def _text(value: object, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = ' '.join(value.split()).strip()
    return cleaned if 1 <= len(cleaned) <= maximum else None


def validate_commands(commands: Iterable[Command], *, query: str,
                      enabled_request_kinds: frozenset[str] = frozenset(),
                      pending_reply: str | None = None) -> tuple[Command, ...] | None:
    """Validate a command stream against guest text and server-owned registry."""
    values = tuple(commands)
    if not query.strip() or not 1 <= len(values) <= MAX_COMMANDS:
        return None
    validated: list[Command] = []
    for command in values:
        if not isinstance(command, Command) or command.type not in COMMAND_TYPES:
            return None
        if len(command.slots) > MAX_SLOTS:
            return None
        for slot in command.slots:
            if (not isinstance(slot, CommandSlot) or not _text(slot.name, 64)
                    or not _text(slot.text, 120) or not _verbatim(query, slot.text)):
                return None

        if command.type == 'StartGoal':
            definition = service_definition(command.goal or '')
            if (definition is None or definition.request_kind not in enabled_request_kinds
                    or definition.request_kind == 'directions'):
                return None
            allowed = set(accepted_slots(command.goal or ''))
            if any(slot.name not in allowed for slot in command.slots):
                return None
            if any(value is not None for value in (command.query, command.field,
                                                    command.value, command.reason,
                                                    command.confirmed)) or command.keys:
                return None
        elif command.type in {'SetSlot', 'CorrectSlot'}:
            if (not _text(command.field, 64) or not _text(command.value, 120)
                    or not _verbatim(query, command.value)):
                return None
            if command.goal is not None or command.query is not None or command.keys:
                return None
        elif command.type == 'AskInfo':
            if (not _text(command.query, MAX_TEXT) or command.goal is not None
                    or command.slots or command.field is not None or command.value is not None
                    or command.confirmed is not None or command.reason is not None):
                return None
        elif command.type == 'Navigate':
            if (not _text(command.query, MAX_TEXT) or command.goal is not None
                    or command.slots or command.keys or command.field is not None
                    or command.value is not None or command.confirmed is not None
                    or command.reason is not None):
                return None
        elif command.type == 'Confirm':
            if (command.confirmed is not True or pending_reply not in {None, 'confirm'}
                    or command.goal is not None or command.slots or command.query is not None
                    or command.keys or command.field is not None or command.value is not None
                    or command.reason is not None):
                return None
        elif command.type == 'Cancel':
            if any(value is not None for value in (command.goal, command.query, command.field,
                                                    command.value, command.confirmed, command.reason)):
                return None
            if command.slots or command.keys:
                return None
        elif command.type == 'Handoff':
            if (not _text(command.reason, 160) or command.goal is not None or command.slots
                    or command.query is not None or command.keys or command.field is not None
                    or command.value is not None or command.confirmed is not None):
                return None
        else:  # ChitChat
            if any(value is not None for value in (command.goal, command.query, command.field,
                                                    command.value, command.confirmed, command.reason)):
                return None
            if command.slots or command.keys:
                return None
        validated.append(command)
    return tuple(validated)


def parse_commands(raw: str, *, query: str,
                   enabled_request_kinds: frozenset[str] = frozenset(),
                   pending_reply: str | None = None) -> tuple[Command, ...] | None:
    """Parse model JSON and fail closed before it reaches runtime routing."""
    if not isinstance(raw, str) or len(raw) > 5000:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or set(payload) != {'commands'}:
        return None
    raw_commands = payload.get('commands')
    if not isinstance(raw_commands, list):
        return None
    commands: list[Command] = []
    for item in raw_commands:
        if not isinstance(item, dict) or 'type' not in item:
            return None
        allowed = {'type', 'goal', 'slots', 'query', 'keys', 'field', 'value', 'confirmed', 'reason'}
        if set(item) - allowed:
            return None
        slots_raw = item.get('slots', [])
        if not isinstance(slots_raw, list):
            return None
        slots: list[CommandSlot] = []
        for slot in slots_raw:
            if not isinstance(slot, dict) or set(slot) != {'name', 'text'}:
                return None
            name, text = slot.get('name'), slot.get('text')
            if not isinstance(name, str) or not isinstance(text, str):
                return None
            slots.append(CommandSlot(name, text))
        keys = item.get('keys', [])
        if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
            return None
        commands.append(Command(
            type=item.get('type'), goal=item.get('goal'), slots=tuple(slots),
            query=item.get('query'), keys=tuple(keys), field=item.get('field'),
            value=item.get('value'), confirmed=item.get('confirmed'), reason=item.get('reason')))
    return validate_commands(commands, query=query,
                             enabled_request_kinds=enabled_request_kinds,
                             pending_reply=pending_reply)


def model_commands(*, query: str, language: str, base_url: str, model: str,
                   enabled_request_kinds: frozenset[str],
                   pending_reply: str | None = None,
                   should_cancel=None, timeout_seconds: float = 1.5
                   ) -> tuple[Command, ...] | None:
    """Ask the local SLM for one closed command stream.

    This is deliberately a proposal boundary: the model receives the
    server-owned service catalog, and :func:`parse_commands` checks every
    service, slot and verb against the original guest utterance before the
    runtime sees it.  A transport, timeout or schema failure returns ``None``
    so deterministic routing remains authoritative.
    """
    if not base_url or not model or not query.strip():
        return None
    from concierge_kiosk.domain.service_registry import SERVICE_DEFINITIONS

    services = [
        {
            'service_mode': code,
            'request_kind': definition.request_kind,
            'accepted_slots': list(accepted_slots(code)),
        }
        for code, definition in SERVICE_DEFINITIONS.items()
        if definition.request_kind in enabled_request_kinds
        and definition.request_kind != 'directions'
    ]
    pending = pending_reply or 'none'
    payload = {
        'model': model,
        'stream': True,
        'keep_alive': '5m',
        'format': command_schema(),
        'messages': [
            {'role': 'system', 'content': (
                'Interpret exactly one hotel concierge guest turn and return only the JSON schema. '
                'Use StartGoal for an explicit service request, AskInfo for a factual hotel question, '
                'Navigate for directions, Confirm or Cancel only for the pending server-owned task, '
                'SetSlot or CorrectSlot only when the guest supplies a missing/corrected slot, '
                'Handoff when the guest asks for staff, and ChitChat for social text. '
                'Select goals only from AVAILABLE_SERVICES. Copy every slot/value/query verbatim from '
                'GUEST_TURN; never invent a room, quantity, booking, price, permission or completion. '
                'A command proposes intent only; the server owns policy, evidence, confirmation and writes.')},
            {'role': 'user', 'content': json.dumps({
                'language': language,
                'guest_turn': query[:500],
                'pending_reply': pending,
                'available_services': services,
            }, ensure_ascii=False)},
        ],
        'options': {'temperature': 0, 'num_predict': 220, 'num_ctx': SLM_NUM_CTX},
    }
    raw = _chat(base_url, payload, min(10.0, max(0.05, timeout_seconds)), should_cancel)
    return parse_commands(
        raw, query=query, enabled_request_kinds=enabled_request_kinds,
        pending_reply=pending_reply,
    ) if raw else None


def commands_from_turn_plan(plan, *, query: str,
                            enabled_request_kinds: frozenset[str],
                            pending_reply: str | None = None) -> tuple[Command, ...] | None:
    """Convert the legacy bounded TurnPlan into the shared command protocol."""
    commands: list[Command] = []
    for intent in getattr(plan, 'intents', ()):
        kind = getattr(intent, 'type', None)
        if kind == 'write':
            commands.append(Command(
                'StartGoal', goal=getattr(intent, 'service_mode', None),
                slots=tuple(CommandSlot(slot.name, slot.text) for slot in intent.slots)))
        elif kind == 'read':
            commands.append(Command('AskInfo', query=getattr(intent, 'question', None)))
        elif kind == 'smalltalk':
            commands.append(Command('ChitChat'))
        elif kind == 'answer_to_pending':
            referent = getattr(intent, 'refers_to', None)
            if _surface(referent or '') == 'confirm':
                commands.append(Command('Confirm', confirmed=True))
            elif referent:
                commands.append(Command('SetSlot', field=referent, value=query))
            else:
                return None
        else:
            return None
    return validate_commands(commands, query=query,
                             enabled_request_kinds=enabled_request_kinds,
                             pending_reply=pending_reply)


__all__ = [
    'COMMAND_TYPES', 'Command', 'CommandSlot', 'command_schema',
    'commands_from_turn_plan', 'model_commands', 'parse_commands', 'validate_commands',
]
