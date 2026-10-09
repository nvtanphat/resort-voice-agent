"""Closed understanding commands shared by text, voice and the agent runtime.

Commands describe what the guest appears to mean; they never authorize a
database write.  Service names and slot names are checked against the signed
runtime registry before a command stream is accepted.
"""
from __future__ import annotations
from concierge_kiosk.runtime.observability import observed, command_event, invocation, update_current

from dataclasses import dataclass, replace
from copy import deepcopy
import json
import unicodedata
from typing import Any, Callable, Iterable, Mapping, Sequence

from concierge_kiosk.agent.understanding.semantic import _chat, capture_chat_failure
from concierge_kiosk.runtime.local_http import slm_turn_expired
from concierge_kiosk.core.domain_profile import preference_policy, rag_policy, supported_languages
from concierge_kiosk.core.settings import SLM_NUM_CTX
from concierge_kiosk.domain.service_registry import accepted_slots, service_definition
from concierge_kiosk.agent.understanding.intent_evidence import command_supported, states_condition


COMMAND_TYPES = frozenset({
    'StartGoal', 'SetSlot', 'CorrectSlot', 'Cancel', 'Modify', 'Confirm',
    'AskInfo', 'Navigate', 'Handoff', 'ChitChat', 'AskStatus', 'SwitchLanguage',
    'Plan', 'SetPreference', 'Clarify', 'CheckAvailability', 'Emergency',
})
# Social sub-kinds select a fixed reply text; they never select an action.
CHITCHAT_KINDS = ('greeting', 'thanks', 'goodbye', 'smalltalk')
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
    conditional: bool = False
    reason: str | None = None
    kind: str | None = None
    target: str | None = None
    # AskInfo only: closed facet name (rag.facet_fact_types key) the guest is asking about.
    facet: str | None = None
    # StartGoal / AskInfo / Navigate: the guest points back at the last verified topic
    # instead of naming one.  A model-made claim; the server honours it only when a verified
    # anchor exists, and the anchor (never the model) supplies the topic.
    refers_to_context: bool = False
    # SetPreference only: the exact words of the guest turn the preference rests on.  The server
    # checks that it is a verbatim span; it is a grounding check, never proof of intent.
    evidence: str | None = None

    def public(self) -> dict[str, Any]:
        result: dict[str, Any] = {'type': self.type}
        for key, value in (
                ('goal', self.goal), ('query', self.query), ('field', self.field),
                ('value', self.value), ('confirmed', self.confirmed),
                ('conditional', self.conditional if self.conditional else None),
                ('refers_to_context', True if self.refers_to_context else None),
                ('evidence', self.evidence),
                ('reason', self.reason), ('kind', self.kind), ('target', self.target),
                ('facet', self.facet)):
            if value is not None:
                result[key] = value
        if self.slots:
            result['slots'] = [slot.public() for slot in self.slots]
        if self.keys:
            result['keys'] = list(self.keys)
        return result


def command_schema(goal_slots: Mapping[str, Sequence[str]] | None = None,
                   *, slot_reply: bool = True, context_topic: bool = False,
                   confirm_pending: bool = False, compact: bool = False) -> dict[str, Any]:
    """Return the closed JSON schema used by an understanding model.

    Each command type is its own schema variant carrying only that type's
    fields, so grammar-constrained decoding cannot emit a mixed shape such as
    a ``SetSlot`` holding a slot list. ``goal_slots`` maps every
    server-selected service to its accepted slot names, giving each
    ``StartGoal`` variant a closed goal and slot vocabulary. ``slot_reply``
    offers ``SetSlot``/``CorrectSlot`` only while a server-owned question is
    pending. The parser still re-validates everything against the registry.
    ``compact=True`` merges service variants into goal and slot-name enums;
    command shapes/state gates stay closed and per-goal slots stay server-owned.
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

    def with_context_flag(item: dict[str, Any]) -> dict[str, Any]:
        """Ask for ``refers_to_context`` only while the server has a verified topic to point at."""
        if not context_topic:
            return item
        # Required, not optional: an optional flag lets a small model skip the decision.
        return {**item, 'required': [*item['required'], 'refers_to_context'],
                'properties': {**item['properties'], 'refers_to_context': {'type': 'boolean'}}}

    variants = []
    for goal, names in (goal_slots or {}).items():
        slot = {
            'type': 'object', 'additionalProperties': False,
            'required': ['name', 'text'],
            'properties': {'name': name_schema(names), 'text': text},
        }
        variants.append(with_context_flag(variant(
            'StartGoal', goal={'type': 'string', 'const': goal},
            slots={'type': 'array', 'maxItems': MAX_SLOTS, 'items': slot},
            conditional={'type': 'boolean'})))
        definition = service_definition(goal)
        if definition is not None and definition.availability_source is not None:
            variants.append({
                'type': 'object', 'additionalProperties': False,
                'required': ['type', 'goal'],
                'properties': {
                    'type': {'type': 'string', 'const': 'CheckAvailability'},
                    'goal': {'type': 'string', 'const': goal},
                    'slots': {'type': 'array', 'maxItems': MAX_SLOTS, 'items': slot},
                },
            })
    if slot_reply:
        reply_name = name_schema(name for names in (goal_slots or {}).values() for name in names)
        variants += [variant('SetSlot', field=reply_name, value=text),
                     variant('CorrectSlot', field=reply_name, value=text)]
    preferences = preference_policy().fields
    variants += [
        with_context_flag({**variant('AskInfo', query=query), 'properties': {
            **variant('AskInfo', query=query)['properties'],
            'facet': {'type': 'string', 'enum': sorted(rag_policy().facet_fact_types)}}}),
        with_context_flag(variant('Navigate', query=query)),
        variant('Cancel'),
        variant('Modify'),
        variant('AskStatus'),
        variant('Clarify'),
        variant('Plan', query=query),
        variant('Handoff', reason={'type': 'string', 'minLength': 1, 'maxLength': 160}),
        variant('ChitChat', kind={'type': 'string', 'enum': list(CHITCHAT_KINDS)}),
        variant('SwitchLanguage', target={'type': 'string', 'enum': sorted(supported_languages())}),
    ]
    if confirm_pending:
        # Offered only while the server is waiting for a confirmation, like slot replies.
        variants.append(variant('Confirm', confirmed={'type': 'boolean', 'const': True}))
    for name, spec in preferences.items():
        value = ({'type': 'string', 'enum': list(spec.values)} if spec.kind == 'enum' else text)
        variants.append(variant('SetPreference', field={'type': 'string', 'const': name}, value=value,
                                evidence=text))
    if compact:
        # Reuse the same command shapes, bounds and state gates. Only merge
        # registry goal alternatives, never unrelated command types. The
        # prompt retains per-goal slots; the server still filters by registry.
        grouped = []
        for command_type in ('StartGoal', 'CheckAvailability'):
            members = [v for v in variants if v['properties']['type']['const'] == command_type]
            if not members:
                continue
            shared = deepcopy(members[0])
            shared['properties']['goal'] = {'type': 'string', 'enum': [
                v['properties']['goal']['const'] for v in members]}
            shared['properties']['slots']['items']['properties']['name'] = name_schema(
                name for v in members
                for name in v['properties']['slots']['items']['properties']['name'].get('enum', ()))
            grouped.append(shared)
        variants = grouped + [v for v in variants
                              if v['properties']['type']['const'] not in {'StartGoal', 'CheckAvailability'}]
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
    if not isinstance(value, str) or len(value) > maximum:
        return None
    cleaned = ' '.join(value.split()).strip()
    return cleaned if 1 <= len(cleaned) <= maximum else None


_OPTIONAL_FIELDS = ('goal', 'query', 'field', 'value', 'confirmed', 'reason', 'kind', 'target', 'facet',
                    'evidence')


def _only(command: Command, *allowed: str) -> bool:
    """True when every optional field outside ``allowed`` is unset."""
    return (not command.slots and not command.keys and not command.conditional
            and not command.refers_to_context
            and all(getattr(command, name) is None for name in _OPTIONAL_FIELDS if name not in allowed))


def _preference_value(command: Command, query: str, language: str | None) -> str | int | None:
    """Return a preference the guest stated, bounded by the profile schema."""
    spec = preference_policy().fields.get(command.field or '')
    if spec is None or not isinstance(command.value, str):
        return None
    if spec.kind == 'enum':
        return command.value if command.value in spec.values else None
    if spec.kind != 'integer' or not _verbatim(query, command.value):
        return None
    from concierge_kiosk.agent.tools.numerals import normalize_number_words

    languages = [language] if language in supported_languages() else sorted(supported_languages())
    digits = next((found for found in (''.join(ch for ch in normalize_number_words(command.value, code)
                                               if ch.isdigit()) for code in languages) if found), '')
    if not digits:
        return None
    value = int(digits)
    if spec.minimum is not None and value < spec.minimum:
        return None
    if spec.maximum is not None and value > spec.maximum:
        return None
    return value


@observed('command_validation')
def validate_commands(commands: Iterable[Command], *, query: str,
                      enabled_request_kinds: frozenset[str] = frozenset(),
                      pending_reply: str | None = None,
                      language: str | None = None,
                      require_evidence: bool = True, context_topic: str | None = None,
                      pending_goal: str | None = None,
                      rejections: list[dict] | None = None) -> tuple[Command, ...] | None:
    """Validate a command stream against guest text and server-owned registry.

    A command that fails validation is dropped alone; the valid commands of the
    same turn are kept, so one malformed clause never discards the guest's
    other intents. ``None`` means no command survived.

    ``require_evidence=False`` is for reviewed training examples, which predate the
    ``SetPreference.evidence`` field; a model proposal always needs it.
    """
    values = tuple(commands)
    if not query.strip() or not 1 <= len(values) <= MAX_COMMANDS:
        return None
    validated: list[Command] = []
    started: set[tuple] = set()
    for command_index, command in enumerate(values):
        original_value = command.value if isinstance(command, Command) else None
        if (not isinstance(command, Command) or not isinstance(command.type, str)
                or command.type not in COMMAND_TYPES
                or type(command.conditional) is not bool or type(command.refers_to_context) is not bool
                or any(value is not None and not isinstance(value, str)
                       for name in _OPTIONAL_FIELDS if name != 'confirmed'
                       for value in (getattr(command, name),))):
            command_event('server_validated', command, index=command_index, outcome='rejected', reason='structural_validation')
            continue
        if len(command.slots) > MAX_SLOTS:
            command_event('server_validated', command, index=command_index, outcome='rejected', reason='structural_validation')
            continue
        if any(not isinstance(slot, CommandSlot) for slot in command.slots):
            command_event('server_validated', command, index=command_index, outcome='rejected', reason='structural_validation')
            continue
        if ((command.kind is not None and command.type != 'ChitChat')
                or (command.target is not None and command.type != 'SwitchLanguage')
                or (command.facet is not None and command.type != 'AskInfo')
                or (command.evidence is not None and command.type != 'SetPreference')
                or (command.refers_to_context and command.type not in {'StartGoal', 'AskInfo', 'Navigate'})):
            command_event('server_validated', command, index=command_index, outcome='rejected', reason='structural_validation')
            continue
        if command.conditional and command.type != 'StartGoal':
            command_event('server_validated', command, index=command_index, outcome='rejected', reason='structural_validation')
            continue

        if command.type == 'StartGoal':
            definition = service_definition(command.goal or '')
            if (definition is None or definition.request_kind not in enabled_request_kinds
                    or definition.request_kind == 'directions'):
                command_event('server_validated', command, index=command_index, outcome='rejected', reason='structural_validation')
                continue
            if any(value is not None for value in (command.query, command.field,
                                                    command.value, command.reason,
                                                    command.confirmed)) or command.keys:
                command_event('server_validated', command, index=command_index, outcome='rejected', reason='structural_validation')
                continue
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
            if command.conditional and (definition.availability_source is None
                                        or not states_condition(query, language)):
                # "If available" cannot be checked for a service without an
                # availability source; the proposal still needs guest consent.
                # A model flag alone is no condition: the guest must state one.
                command = replace(command, conditional=False)
            signature = (command.goal, tuple((s.name, s.text) for s in command.slots),
                         command.conditional, command.refers_to_context)
            if signature in started:
                command_event('server_validated', command, index=command_index, outcome='rejected', reason='structural_validation')
                continue  # the same request stated twice is one request
            started.add(signature)
        elif command.type == 'CheckAvailability':
            definition = service_definition(command.goal or '')
            if (definition is None or definition.availability_source is None
                    or (enabled_request_kinds and definition.request_kind not in enabled_request_kinds)
                    or definition.request_kind == 'directions'):
                command_event('server_validated', command, index=command_index, outcome='rejected', reason='structural_validation')
                continue
            if any(value is not None for value in (command.query, command.field,
                                                    command.value, command.reason,
                                                    command.confirmed)) or command.keys or command.conditional:
                command_event('server_validated', command, index=command_index, outcome='rejected', reason='structural_validation')
                continue
            allowed = set(accepted_slots(command.goal or ''))
            kept = tuple(slot for slot in command.slots
                         if slot.name in allowed and _text(slot.name, 64)
                         and _text(slot.text, 120) and _verbatim(query, slot.text))
            if kept != command.slots:
                command = replace(command, slots=kept)
        elif command.type in {'SetSlot', 'CorrectSlot'}:
            if command.goal is not None or command.query is not None or command.keys or command.slots:
                command_event('server_validated', command, index=command_index, outcome='rejected', reason='structural_validation')
                continue
            if (not _text(command.field, 64) or not _text(command.value, 120)
                    or not _verbatim(query, command.value)):
                # An unstated value is never applied; the pending question stays open.
                command_event('server_validated', command, index=command_index, outcome='rejected', reason='structural_validation')
                continue
        elif command.slots:
            command_event('server_validated', command, index=command_index, outcome='rejected', reason='structural_validation')
            continue
        elif command.type == 'AskInfo':
            if (not _text(command.query, MAX_TEXT) or command.goal is not None
                    or command.slots or command.field is not None or command.value is not None
                    or command.confirmed is not None or command.reason is not None):
                command_event('server_validated', command, index=command_index, outcome='rejected', reason='structural_validation')
                continue
            if not _verbatim(query, command.query):
                command = replace(command, query=query[:MAX_TEXT])
            if command.facet is not None and command.facet not in rag_policy().facet_fact_types:
                # An unknown facet is dropped, not trusted: the read still runs unscoped.
                command = replace(command, facet=None)
        elif command.type == 'Navigate':
            if (not _text(command.query, MAX_TEXT) or command.goal is not None
                    or command.slots or command.keys or command.field is not None
                    or command.value is not None or command.confirmed is not None
                    or command.reason is not None):
                command_event('server_validated', command, index=command_index, outcome='rejected', reason='structural_validation')
                continue
            if not _verbatim(query, command.query):
                command = replace(command, query=query[:MAX_TEXT])
        elif command.type == 'Confirm':
            if pending_reply != 'confirm':
                # Nothing is waiting for a confirmation: drop the claim, keep the rest of the turn.
                command_event('server_validated', command, index=command_index, outcome='rejected', reason='structural_validation')
                continue
            if (command.confirmed is not True
                    or command.goal is not None or command.slots or command.query is not None
                    or command.keys or command.field is not None or command.value is not None
                    or command.reason is not None):
                command_event('server_validated', command, index=command_index, outcome='rejected', reason='structural_validation')
                continue
        elif command.type in {'Cancel', 'Modify'}:
            if any(value is not None for value in (command.goal, command.query, command.field,
                                                    command.value, command.confirmed, command.reason)):
                command_event('server_validated', command, index=command_index, outcome='rejected', reason='structural_validation')
                continue
            if command.slots or command.keys:
                command_event('server_validated', command, index=command_index, outcome='rejected', reason='structural_validation')
                continue
        elif command.type == 'Handoff':
            if (not _text(command.reason, 160) or command.goal is not None or command.slots
                    or command.query is not None or command.keys or command.field is not None
                    or command.value is not None or command.confirmed is not None):
                command_event('server_validated', command, index=command_index, outcome='rejected', reason='structural_validation')
                continue
        elif command.type == 'Plan':
            if not _text(command.query, MAX_TEXT) or not _only(command, 'query'):
                command_event('server_validated', command, index=command_index, outcome='rejected', reason='structural_validation')
                continue
        elif command.type == 'SwitchLanguage':
            if command.target not in supported_languages() or not _only(command, 'target'):
                command_event('server_validated', command, index=command_index, outcome='rejected', reason='structural_validation')
                continue
        elif command.type == 'SetPreference':
            preference = _preference_value(command, query, language)
            grounded = (not require_evidence
                        or (isinstance(command.evidence, str) and len(command.evidence.strip()) >= 2
                            and _verbatim(query, command.evidence)))
            if preference is None or not grounded or not _only(command, 'field', 'value', 'evidence'):
                # An unstated or out-of-range preference is not remembered.
                command_event('server_validated', command, index=command_index, outcome='rejected', reason='structural_validation')
                continue
            command = replace(command, value=str(preference))
        elif command.type in {'AskStatus', 'Clarify', 'Emergency'}:
            if not _only(command):
                command_event('server_validated', command, index=command_index, outcome='rejected', reason='structural_validation')
                continue
        else:  # ChitChat
            if not _only(command, 'kind') or command.kind not in (None, *CHITCHAT_KINDS):
                command_event('server_validated', command, index=command_index, outcome='rejected', reason='structural_validation')
                continue
            if command.kind is None:
                command = replace(command, kind='smalltalk')
        semantic_command = replace(command, value=original_value) if command.type == 'SetPreference' else command
        command_event('server_validated', command, index=command_index)
        supported = True
        if require_evidence:
            supported = command_supported(semantic_command, query, language,
                context_topic=context_topic, pending_goal=pending_goal, pending_reply=pending_reply)
            command_event('semantically_authorized', command, index=command_index,
                          outcome='accepted' if supported else 'rejected',
                          reason='none' if supported else 'unsupported_semantics')
        if not supported:
            if rejections is not None:
                rejections.append({'command': command.public(), 'reason': 'unsupported_semantics'})
            continue
        validated.append(command)
    return tuple(validated) or None


def commands_from_items(raw_commands: object) -> list[Command] | None:
    """Build unvalidated commands from decoded JSON objects (model output or data)."""
    if not isinstance(raw_commands, list) or not 1 <= len(raw_commands) <= MAX_COMMANDS:
        return None
    allowed = {'type', 'goal', 'slots', 'query', 'keys', 'field', 'value',
               'confirmed', 'conditional', 'reason', 'kind', 'target', 'facet', 'refers_to_context', 'evidence'}
    commands: list[Command] = []
    for item in raw_commands:
        if not isinstance(item, dict) or 'type' not in item or set(item) - allowed:
            continue
        if any(key in item and type(item[key]) is not bool for key in ('conditional', 'refers_to_context')):
            continue
        slots_raw = item.get('slots', [])
        if not isinstance(slots_raw, list):
            continue
        slots: list[CommandSlot] = []
        for slot in slots_raw:
            if not isinstance(slot, dict) or set(slot) != {'name', 'text'}:
                break
            name, text = slot.get('name'), slot.get('text')
            if not isinstance(name, str) or not isinstance(text, str):
                break
            slots.append(CommandSlot(name, text))
        if len(slots) != len(slots_raw):
            continue
        keys = item.get('keys', [])
        if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
            continue
        commands.append(Command(
            type=item.get('type'), goal=item.get('goal'), slots=tuple(slots),
            query=item.get('query'), keys=tuple(keys), field=item.get('field'),
            value=item.get('value'), confirmed=item.get('confirmed'),
            conditional=item.get('conditional', False), reason=item.get('reason'),
            kind=item.get('kind'), target=item.get('target'), facet=item.get('facet'),
            refers_to_context=item.get('refers_to_context', False), evidence=item.get('evidence')))
    return commands


def parse_commands(raw: str, *, query: str,
                   enabled_request_kinds: frozenset[str] = frozenset(),
                   pending_reply: str | None = None,
                   language: str | None = None, context_topic: str | None = None,
                   pending_goal: str | None = None,
                   rejections: list[dict] | None = None) -> tuple[Command, ...] | None:
    """Parse model JSON and fail closed before it reaches runtime routing."""
    if not isinstance(raw, str) or len(raw) > 5000:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or set(payload) != {'commands'}:
        return None
    commands = commands_from_items(payload.get('commands'))
    if commands is None:
        return None
    for index, command in enumerate(commands):
        command_event('model_proposed', command, index=index)
    return validate_commands(commands, query=query,
                             enabled_request_kinds=enabled_request_kinds,
                             pending_reply=pending_reply, language=language,
                             context_topic=context_topic, pending_goal=pending_goal, rejections=rejections)


@observed('qwen_nlu')
def model_commands(*, query: str, language: str, base_url: str, model: str,
                   enabled_request_kinds: frozenset[str],
                   service_candidates: Sequence[Mapping[str, Any]] | None = None,
                   examples: Sequence[Mapping[str, Any]] = (),
                   pending_reply: str | None = None,
                   context_topic: str | None = None,
                   pending_goal: str | None = None,
                   should_cancel=None, timeout_seconds: float = 1.5,
                   num_gpu: int = -1,
                   on_outcome: Callable[[str], None] | None = None
                   ) -> tuple[Command, ...] | None:
    """Ask the local SLM for one closed command stream.

    This is deliberately a proposal boundary: the model receives the
    server-owned service catalog, and :func:`parse_commands` checks every
    service, slot and verb against the original guest utterance before the
    runtime sees it.  A transport, timeout or schema failure returns ``None``
    with an explicit outcome so the engine can recover without inventing an intent.

    ``on_outcome`` receives why a proposal was or was not produced, so that an unreachable
    model, an exhausted turn budget, unparseable output and a proposal the validator refused
    are told apart (all of them return ``None``): ``timeout``, ``cancelled``,
    ``unavailable``, ``no_response``,
    ``turn_budget_expired``, ``malformed_output``, ``rejected_by_validation``,
    ``partially_accepted`` (some proposed commands were dropped), ``accepted``.
    """
    def note(outcome: str) -> None:
        update_current(status=outcome)
        if on_outcome is not None:
            on_outcome(outcome)

    if not base_url or not model or not query.strip():
        note('unavailable')
        return None
    from concierge_kiosk.domain.service_registry import SERVICE_DEFINITIONS

    if service_candidates is None:
        services = [
            {
                'service_mode': code,
                'request_kind': definition.request_kind,
                'accepted_slots': list(accepted_slots(code)),
                'description': definition.description,
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
                'description': definition.description,
            }
            for key in ('catalog_service_id', 'name'):
                value = raw.get(key)
                if isinstance(value, str) and value.strip():
                    item[key] = value[:320]
            services.append(item)
            seen.add(definition.code)
    # Retrieval is a ranking hint, not authority to exclude registry services.
    # Keep shortlisted descriptions first and retain every allowed goal. Fields
    # duplicated in the schema or unused by understanding need not be prompt data.
    seen_codes = {item['service_mode'] for item in services}
    services.extend({'service_mode': code, 'accepted_slots': list(accepted_slots(code)),
                     'description': definition.description}
                    for code, definition in SERVICE_DEFINITIONS.items()
                    if code not in seen_codes and definition.request_kind in enabled_request_kinds
                    and definition.request_kind != 'directions')
    services = [{key: value for key, value in item.items()
                 if key in {'service_mode', 'accepted_slots', 'description', 'name'}}
                for item in services]
    pending = pending_reply or 'none'
    payload = {
        'model': model,
        'stream': True,
        'keep_alive': '5m',
        'format': command_schema(
            {item['service_mode']: item['accepted_slots'] for item in services},
            slot_reply=pending_reply is not None, context_topic=bool(context_topic),
            confirm_pending=pending_reply == 'confirm', compact=True),
        'messages': [
            {'role': 'system', 'content': (
                'Interpret exactly one hotel concierge guest turn and return only the JSON schema, written compactly '
                'on a single line with no indentation or line breaks. '
                'When the guest wants something done, brought, fixed, booked or arranged, emit StartGoal '
                'only when the AVAILABLE_SERVICES description supports the explicitly requested action, '
                'with grounded slots; never choose a service by department or default. '
                'Use CheckAvailability when the guest asks whether a slot/table/seat is free, without asking to book. '
                'Use AskInfo for a factual hotel question (including price, opening-hours or policies) and set its facet when the question asks about one aspect; '
                'Navigate for directions, Plan when the guest asks for suggestions or an '
                'itinerary, AskStatus when the guest asks how an earlier request is going, Cancel or Modify '
                'when the guest withdraws or changes a pending or earlier request, Confirm only to approve '
                'the pending task, SetSlot or CorrectSlot only to answer or correct PENDING_REPLY, '
                'SetPreference only when the guest states a lasting preference (diet, group size, children, '
                'mobility, quiet) and put in its evidence the exact words of GUEST_TURN that state it; never emit '
                'SetPreference when the guest said nothing about such a preference, '
                'SwitchLanguage when the guest asks to change the conversation language, '
                'Handoff when the guest asks for staff or complains, ChitChat with kind greeting, thanks, '
                'goodbye or smalltalk for social text, and Clarify when the request is too vague to act on. '
                'A negated, already-arranged or past-tense mention of a service, or a complaint about it, '
                'is not a request for that service. One utterance may need several commands. Emit the smallest command list that '
                'covers the explicit actionable clauses: do not add a service merely because an item '
                'or place is mentioned, do not repeat an overlapping service, and stop once each clause '
                'has one representation (separate quantities/items may remain separate commands). '
                'Every slot text, value and query must '
                'be an exact substring of GUEST_TURN, in the guest\'s own words and digits; omit any '
                'slot the guest did not state. Never invent a room, '
                'Use requested_item for the exact item words and unit for the stated counting unit; '
                'never replace an item with a service catalog name. '
                'quantity, booking, price, permission or completion. '
                'EXAMPLES are reviewed guest turns with their correct commands; follow their pattern. '
                'CONTEXT.last_verified_topic, when present, is the place or topic the guest was just '
                'told about. Set refers_to_context=true on StartGoal, AskInfo or Navigate only when the guest '
                'points back at that topic without naming one again; never otherwise, and never copy the topic into a slot or query. '
                'For a conditional request such as "if available, book it", set conditional=true on '
                'StartGoal so the planner checks availability before the governed proposal; wanting to review or confirm '
                'later is not a condition, so leave conditional false then. '
                'A command proposes intent only; the server owns policy, evidence, confirmation and writes.')},
            {'role': 'user', 'content': json.dumps({
                'language': language,
                'guest_turn': query[:500],
                'pending_reply': pending,
                **({'context': {'last_verified_topic': context_topic[:160]}} if context_topic else {}),
                'available_services': services,
                # Nearest reviewed training turns (never evaluation data),
                # limited to goals offered above so they cannot widen the schema.
                'examples': [dict(item) for item in examples
                             if all(command.get('type') != 'StartGoal'
                                    or command.get('goal') in {s['service_mode'] for s in services}
                                    for command in item.get('commands', ()))],
            }, ensure_ascii=False, separators=(',', ':'))},
        ],
        'options': {'temperature': 0, 'num_predict': 220, 'num_ctx': SLM_NUM_CTX,
                    'num_gpu': num_gpu},
    }
    with capture_chat_failure() as failures, invocation('NLU'):
        raw = _chat(base_url, payload, min(10.0, max(0.05, timeout_seconds)), should_cancel)
    if not raw:
        note('turn_budget_expired' if slm_turn_expired() else
             failures[-1] if failures else 'no_response')
        return None
    rejections: list[dict] = []
    update_current(command_count=_proposed_count(raw))
    parsed = parse_commands(
        raw, query=query, enabled_request_kinds=enabled_request_kinds,
        pending_reply=pending_reply, language=language, context_topic=context_topic,
        pending_goal=pending_goal, rejections=rejections)
    if parsed is None:
        note('unsupported_semantics' if rejections else
             'rejected_by_validation' if _is_json_object(raw) else 'malformed_output')
    else:
        note('partially_accepted' if len(parsed) < _proposed_count(raw) else 'accepted')
    return parsed


def _proposed_count(raw: str) -> int:
    """How many commands the model proposed (before validation)."""
    try:
        items = json.loads(raw).get('commands')
    except (AttributeError, TypeError, ValueError):
        return 0
    return len(items) if isinstance(items, list) else 0


def _is_json_object(raw: str) -> bool:
    try:
        return isinstance(json.loads(raw), dict)
    except (TypeError, ValueError):
        return False


__all__ = [
    'CHITCHAT_KINDS', 'COMMAND_TYPES', 'Command', 'CommandSlot', 'command_schema', 'commands_from_items',
    'model_commands', 'parse_commands', 'validate_commands',
]
