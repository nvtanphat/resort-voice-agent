"""Closed understanding commands shared by text, voice and the agent runtime.

Commands describe what the guest appears to mean; they never authorize a
database write.  Service names and slot names are checked against the signed
runtime registry before a command stream is accepted.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import unicodedata
from typing import Any, Iterable, Mapping, Sequence

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


def command_schema(goal_slots: Mapping[str, Sequence[str]] | None = None,
                   *, slot_reply: bool = True) -> dict[str, Any]:
    """Return the closed JSON schema used by an understanding model.

    Each command type is its own schema variant carrying only that type's
    fields, so grammar-constrained decoding cannot emit a mixed shape such as
    a ``SetSlot`` holding a slot list. ``goal_slots`` maps every
    server-selected service to its accepted slot names, giving each
    ``StartGoal`` variant a closed goal and slot vocabulary. ``slot_reply``
    offers ``SetSlot``/``CorrectSlot`` only while a server-owned question is
    pending. The parser still re-validates everything against the registry.
    """
    text = {'type': 'string', 'minLength': 1, 'maxLength': 120}

    def name_schema(names: Iterable[str]) -> dict[str, Any]:
        values = sorted(set(names))
        return ({'type': 'string', 'enum': values} if values
                else {'type': 'string', 'minLength': 1, 'maxLength': 64})

    def variant(command_type: str, **properties: Any) -> dict[str, Any]:
        return {
            'type': 'object', 'additionalProperties': False,
            'required': ['type', *properties],
            'properties': {'type': {'type': 'string', 'const': command_type}, **properties},
        }

    query = {'type': 'string', 'minLength': 1, 'maxLength': MAX_TEXT}
    variants = []
    for goal, names in (goal_slots or {}).items():
        slot = {
            'type': 'object', 'additionalProperties': False,
            'required': ['name', 'text'],
            'properties': {'name': name_schema(names), 'text': text},
        }
        variants.append(variant(
            'StartGoal', goal={'type': 'string', 'const': goal},
            slots={'type': 'array', 'maxItems': MAX_SLOTS, 'items': slot}))
    if slot_reply:
        reply_name = name_schema(name for names in (goal_slots or {}).values() for name in names)
        variants += [variant('SetSlot', field=reply_name, value=text),
                     variant('CorrectSlot', field=reply_name, value=text)]
    variants += [
        variant('AskInfo', query=query),
        variant('Navigate', query=query),
        variant('Confirm', confirmed={'type': 'boolean', 'const': True}),
        variant('Cancel'),
        variant('Handoff', reason={'type': 'string', 'minLength': 1, 'maxLength': 160}),
        variant('ChitChat'),
    ]
    return {
        'type': 'object', 'additionalProperties': False,
        'required': ['commands'],
        'properties': {
            'commands': {'type': 'array', 'minItems': 1, 'maxItems': MAX_COMMANDS,
                         'items': {'anyOf': variants}},
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
        if any(not isinstance(slot, CommandSlot) for slot in command.slots):
            return None

        if command.type == 'StartGoal':
            definition = service_definition(command.goal or '')
            if (definition is None or definition.request_kind not in enabled_request_kinds
                    or definition.request_kind == 'directions'):
                return None
            if any(value is not None for value in (command.query, command.field,
                                                    command.value, command.reason,
                                                    command.confirmed)) or command.keys:
                return None
            # A slot the guest did not literally state, or one the service
            # does not accept, is dropped rather than trusted. The goal is
            # kept so the runtime asks for the missing value instead of
            # discarding the guest's whole intent.
            allowed = set(accepted_slots(command.goal or ''))
            kept = tuple(slot for slot in command.slots
                         if slot.name in allowed and _text(slot.name, 64)
                         and _text(slot.text, 120) and _verbatim(query, slot.text))
            if kept != command.slots:
                command = replace(command, slots=kept)
        elif command.type in {'SetSlot', 'CorrectSlot'}:
            if command.goal is not None or command.query is not None or command.keys or command.slots:
                return None
            if (not _text(command.field, 64) or not _text(command.value, 120)
                    or not _verbatim(query, command.value)):
                # An unstated value is never applied; the pending question stays open.
                continue
        elif command.slots:
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
    return tuple(validated) or None


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
                   service_candidates: Sequence[Mapping[str, Any]] | None = None,
                   examples: Sequence[Mapping[str, Any]] = (),
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

    if service_candidates is None:
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
    else:
        # The selector is only a retrieval hint. Rebuild every candidate's
        # authority-bearing fields from the registry before it reaches the
        # model prompt; catalog text may describe a service but cannot invent
        # a goal or slot contract.
        services = []
        seen: set[str] = set()
        for raw in service_candidates:
            if not isinstance(raw, Mapping):
                continue
            code = raw.get('service_mode')
            if not isinstance(code, str):
                continue
            definition = service_definition(str(code or ''))
            if (definition is None or code in seen
                    or definition.request_kind not in enabled_request_kinds
                    or definition.request_kind == 'directions'):
                continue
            item: dict[str, Any] = {
                'service_mode': definition.code,
                'request_kind': definition.request_kind,
                'accepted_slots': list(accepted_slots(definition.code)),
            }
            for key in ('catalog_service_id', 'name', 'description'):
                value = raw.get(key)
                if isinstance(value, str) and value.strip():
                    item[key] = value[:320]
            services.append(item)
            seen.add(definition.code)
        if not services:
            return None
    pending = pending_reply or 'none'
    payload = {
        'model': model,
        'stream': True,
        'keep_alive': '5m',
        'format': command_schema(
            {item['service_mode']: item['accepted_slots'] for item in services},
            slot_reply=pending_reply is not None),
        'messages': [
            {'role': 'system', 'content': (
                'Interpret exactly one hotel concierge guest turn and return only the JSON schema. '
                'When the guest wants something done, brought, fixed, booked or arranged, emit StartGoal '
                'with the closest service_mode from AVAILABLE_SERVICES (match by meaning, using each '
                'name and description) and put the details the guest stated into its slots. '
                'Use AskInfo for a factual hotel question, Navigate for directions, Confirm or Cancel '
                'only for the pending server-owned task, SetSlot or CorrectSlot only to answer or '
                'correct PENDING_REPLY, Handoff when the guest asks for staff, and ChitChat for social '
                'text. One utterance may need several commands. Every slot text, value and query must '
                'be an exact substring of GUEST_TURN, in the guest\'s own words and digits; omit any '
                'slot the guest did not state. Never invent a room, '
                'quantity, booking, price, permission or completion. '
                'EXAMPLES are reviewed guest turns with their correct commands; follow their pattern. '
                'A command proposes intent only; the server owns policy, evidence, confirmation and writes.')},
            {'role': 'user', 'content': json.dumps({
                'language': language,
                'guest_turn': query[:500],
                'pending_reply': pending,
                'available_services': services,
                # Nearest reviewed training turns (never evaluation data),
                # limited to goals offered above so they cannot widen the schema.
                'examples': [dict(item) for item in examples
                             if all(command.get('type') != 'StartGoal'
                                    or command.get('goal') in {s['service_mode'] for s in services}
                                    for command in item.get('commands', ()))],
            }, ensure_ascii=False)},
        ],
        'options': {'temperature': 0, 'num_predict': 220, 'num_ctx': SLM_NUM_CTX},
    }
    raw = _chat(base_url, payload, min(10.0, max(0.05, timeout_seconds)), should_cancel)
    return parse_commands(
        raw, query=query, enabled_request_kinds=enabled_request_kinds,
        pending_reply=pending_reply,
    ) if raw else None


__all__ = [
    'COMMAND_TYPES', 'Command', 'CommandSlot', 'command_schema',
    'model_commands', 'parse_commands', 'validate_commands',
]
