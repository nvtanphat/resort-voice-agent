"""Closed understanding commands shared by text, voice and the agent runtime.

Commands describe what the guest appears to mean; they never authorize a
database write.  Service names and slot names are checked against the signed
runtime registry before a command stream is accepted.
"""
from __future__ import annotations
from concierge_kiosk.runtime.observability import observed, command_event, invocation, update_current

from dataclasses import dataclass, replace
import json
import unicodedata
from typing import Any, Callable, Iterable, Mapping, Sequence

from concierge_kiosk.agent.understanding.semantic import _chat, capture_chat_failure
from concierge_kiosk.runtime.local_http import slm_turn_expired
from concierge_kiosk.core.domain_profile import preference_policy, rag_policy, supported_languages
from concierge_kiosk.core.settings import SLM_KEEP_ALIVE, SLM_NUM_CTX
from concierge_kiosk.agent.tools.service_slots import SERVER_EXTRACTED_SLOTS
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
    ungrounded_reads: set[int] = set()  # positions in ``validated``
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
            if pending_reply is None and pending_goal is None:
                # A slot answer or correction needs something to apply to: a pending server
                # question or an open draft. Without either it is dropped.
                command_event('server_validated', command, index=command_index, outcome='rejected', reason='structural_validation')
                continue
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
                ungrounded_reads.add(len(validated))
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
                ungrounded_reads.add(len(validated))
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
    if len(validated) > 1:
        # Each command of a multi-command turn must stand for its own clause. A read
        # whose query is not guest text names no clause (the model padded the list,
        # often with a slot name), and "too vague to act on" contradicts an action
        # in the same turn. Alone, either still stands: it is then the whole turn.
        actionable = any(command.type not in {'Clarify', 'ChitChat'} for command in validated)
        kept = [command for index, command in enumerate(validated)
                if index not in ungrounded_reads and not (command.type == 'Clarify' and actionable)]
        for command in validated:
            if command not in kept:
                command_event('server_validated', command, outcome='rejected', reason='redundant_in_turn')
        validated = kept or validated
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


# The fields each command type carries; anything else a model adds is ignored.
_TYPE_FIELDS: dict[str, frozenset[str]] = {
    'StartGoal': frozenset({'goal', 'slots', 'conditional', 'refers_to_context'}),
    'CheckAvailability': frozenset({'goal', 'slots'}),
    'SetSlot': frozenset({'field', 'value'}), 'CorrectSlot': frozenset({'field', 'value'}),
    'Confirm': frozenset({'confirmed'}),
    'AskInfo': frozenset({'query', 'facet', 'refers_to_context'}),
    'Navigate': frozenset({'query', 'refers_to_context'}),
    'Plan': frozenset({'query'}), 'Handoff': frozenset({'reason'}),
    'SwitchLanguage': frozenset({'target'}), 'ChitChat': frozenset({'kind'}),
    'SetPreference': frozenset({'field', 'value', 'evidence'}),
}


def _model_items(raw_commands: object) -> object:
    """Reduce model output to each command type's own, well-formed fields.

    Without grammar-constrained decoding a model sometimes adds a field its
    command type does not have, or writes a slot as a bare string. Such a field
    carries no authority, so it is dropped and the rest of the command goes to the
    strict validator, the same way an unstated slot is dropped and its goal kept.
    """
    if not isinstance(raw_commands, list):
        return raw_commands
    items = []
    for item in raw_commands:
        if (not isinstance(item, dict) or not isinstance(item.get('type'), str)
                or item['type'] not in COMMAND_TYPES):
            continue
        fields = _TYPE_FIELDS.get(item['type'], frozenset())
        clean = {'type': item['type'], **{key: value for key, value in item.items() if key in fields}}
        for flag in ('conditional', 'refers_to_context'):
            if flag in clean and type(clean[flag]) is not bool:
                del clean[flag]
        if 'slots' in clean:
            slots = clean['slots'] if isinstance(clean['slots'], list) else []
            clean['slots'] = [slot for slot in slots if isinstance(slot, dict) and set(slot) == {'name', 'text'}
                              and isinstance(slot['name'], str) and isinstance(slot['text'], str)]
        items.append(clean)
    return items


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
    commands = commands_from_items(_model_items(payload.get('commands')))
    if commands is None:
        return None
    for index, command in enumerate(commands):
        command_event('model_proposed', command, index=index)
    return validate_commands(commands, query=query,
                             enabled_request_kinds=enabled_request_kinds,
                             pending_reply=pending_reply, language=language,
                             context_topic=context_topic, pending_goal=pending_goal, rejections=rejections)


_CATALOG_MARKER = '\nAVAILABLE_SERVICES='

_COMMAND_INSTRUCTIONS = (
    'Interpret exactly one hotel concierge guest turn. '
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
    'quantity, booking, price, permission or completion. '
    'Use requested_item for the exact item words and unit for the stated counting unit; '
    'never replace an item with a service catalog name. '
    'LIKELY_SERVICES ranks the AVAILABLE_SERVICES most similar to the turn; it is a hint, '
    'never a reason to choose a service the guest did not ask for. '
    'The earlier user/assistant turns are reviewed guest turns with their correct commands; '
    'follow their pattern and their compact single-line output. '
    'CONTEXT.last_verified_topic, when present, is the place or topic the guest was just '
    'told about. Set refers_to_context=true on StartGoal, AskInfo or Navigate only when the guest '
    'points back at that topic without naming one again; never otherwise, and never copy the topic into a slot or query. '
    'For a conditional request such as "if available, book it", set conditional=true on '
    'StartGoal so the planner checks availability before the governed proposal; wanting to review or confirm '
    'later is not a condition, so leave conditional false then. '
    'OPEN_DRAFT, when present, is the service request drafted for the guest and not yet confirmed: '
    'Cancel when the guest drops it (in any words), CorrectSlot or StartGoal for the same service when '
    'the guest changes a detail of it. '
    'A command proposes intent only; the server owns policy, evidence, confirmation and writes. '
)


def _command_catalog(enabled_request_kinds: frozenset[str]) -> list[dict[str, Any]]:
    """Every enabled registry service, in registry order (the authority for goals and slots)."""
    from concierge_kiosk.domain.service_registry import SERVICE_DEFINITIONS
    # Only the slots a model has to point at; the server reads counts, clock
    # times, dates and party sizes from the guest turn itself.
    return [{'service_mode': code,
             'slots': [name for name in accepted_slots(code) if name not in SERVER_EXTRACTED_SLOTS],
             'description': definition.description}
            for code, definition in SERVICE_DEFINITIONS.items()
            if definition.request_kind in enabled_request_kinds
            and definition.request_kind != 'directions']


def command_output_spec() -> str:
    """The JSON shape of every command, stated in the prompt (vocabularies from config)."""
    preferences = '|'.join(sorted(preference_policy().fields))
    return (
        'Answer with one line of JSON: {"commands":[...]}. Command shapes: '
        '{"type":"StartGoal","goal":SERVICE_MODE,"slots":[{"name":SLOT,"text":GUEST_WORDS}]} '
        '(SLOT is one of the service\'s listed slots; never write a count, time, date or party '
        'size as a slot, the server reads those from GUEST_TURN; add "conditional":true only when the '
        'guest states a condition); '
        '{"type":"CheckAvailability","goal":SERVICE_MODE,"slots":[...]}; '
        '{"type":"AskInfo","query":GUEST_WORDS} with optional "facet":'
        + '|'.join(sorted(rag_policy().facet_fact_types)) + '; '
        '{"type":"Navigate","query":GUEST_WORDS}; {"type":"Plan","query":GUEST_WORDS}; '
        '{"type":"AskStatus"}; {"type":"Cancel"}; {"type":"Modify"}; {"type":"Clarify"}; '
        '{"type":"Confirm","confirmed":true}; '
        '{"type":"SetSlot","field":ACCEPTED_SLOT,"value":GUEST_WORDS} (or CorrectSlot); '
        '{"type":"SetPreference","field":' + preferences + ',"value":VALUE,"evidence":GUEST_WORDS}; '
        '{"type":"SwitchLanguage","target":' + '|'.join(sorted(supported_languages())) + '}; '
        '{"type":"Handoff","reason":TEXT}; '
        '{"type":"ChitChat","kind":' + '|'.join(CHITCHAT_KINDS) + '}. '
        'When CONTEXT is present, StartGoal, AskInfo and Navigate also carry "refers_to_context":true|false.'
    )


def command_system_message(enabled_request_kinds: frozenset[str]) -> dict[str, str]:
    """The static part of every command prompt: instructions plus the enabled catalog.

    It depends only on the deployment, so it is byte-identical on every turn and
    comes first: the local runtime then reuses its KV cache instead of prefilling
    the catalog again on CPU, and startup can warm exactly this prefix. Everything
    that changes per turn goes in the user message, with the guest turn last.
    """
    return {'role': 'system', 'content': _COMMAND_INSTRUCTIONS + command_output_spec() + _CATALOG_MARKER
            + json.dumps(_command_catalog(enabled_request_kinds), ensure_ascii=False, separators=(',', ':'))}


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
    catalog = _command_catalog(enabled_request_kinds)
    offered = {item['service_mode'] for item in catalog}
    # The selector is only a ranking hint: it may order and name catalog
    # entries but cannot add a goal or a slot contract.
    shortlist: list[dict[str, str]] = []
    for raw in service_candidates or ():
        code = raw.get('service_mode') if isinstance(raw, Mapping) else None
        if code not in offered or any(item['service_mode'] == code for item in shortlist):
            continue
        item = {'service_mode': code}
        name = raw.get('name')
        if isinstance(name, str) and name.strip():
            item['name'] = name[:320]
        shortlist.append(item)
    turn: dict[str, Any] = {'language': language, 'pending_reply': pending_reply or 'none'}
    if pending_goal:
        # The server's own unconfirmed draft: the guest may withdraw or correct it.
        turn['open_draft'] = pending_goal
    if context_topic:
        turn['context'] = {'last_verified_topic': context_topic[:160]}
    if shortlist:
        turn['likely_services'] = shortlist
    turn['guest_turn'] = query[:500]
    # Nearest reviewed training turns (never evaluation data), limited to offered
    # goals so they cannot widen the offered services. They are sent as earlier chat turns
    # whose answers are compact JSON: the model then answers the live turn in the
    # same compact form instead of pretty-printing it, which on CPU roughly halves
    # the generated tokens.
    shots: list[dict[str, str]] = []
    for item in examples:
        if not all(command.get('type') != 'StartGoal' or command.get('goal') in offered
                   for command in item.get('commands', ())):
            continue
        # Only what the example shows: per-turn fields would vary with the live
        # turn and add uncached prompt tokens without informing the pattern.
        shot: dict[str, Any] = {}
        if item.get('context'):
            shot['context'] = item['context']
        shot['guest_turn'] = item['guest_turn']
        # Examples show only the slots the model is asked for, so it does not copy rooms or counts.
        shown = [{**command, 'slots': [slot for slot in command['slots']
                                       if slot.get('name') not in SERVER_EXTRACTED_SLOTS]}
                 if isinstance(command.get('slots'), list) else command
                 for command in item['commands']]
        shots += [{'role': 'user', 'content': json.dumps(shot, ensure_ascii=False, separators=(',', ':'))},
                  {'role': 'assistant', 'content': json.dumps(
                      {'commands': shown}, ensure_ascii=False, separators=(',', ':'))}]
    payload = {
        'model': model,
        'stream': True,
        'keep_alive': SLM_KEEP_ALIVE,
        # Plain JSON mode: the command shapes are stated in the system message and
        # every field is re-validated by the server. A full JSON-schema grammar
        # costs seconds per turn on CPU and derails some models.
        'format': 'json',
        'messages': [
            command_system_message(enabled_request_kinds), *shots,
            {'role': 'user', 'content': json.dumps(turn, ensure_ascii=False, separators=(',', ':'))},
        ],
        'options': {'temperature': 0, 'num_predict': 220, 'num_ctx': SLM_NUM_CTX,
                    'num_gpu': num_gpu},
    }
    with capture_chat_failure() as failures, invocation('NLU'):
        raw = _chat(base_url, payload, min(30.0, max(0.05, timeout_seconds)), should_cancel)
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


def prompt_catalog(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The service catalog a command payload offered the model (for diagnostics)."""
    system = payload['messages'][0]['content']
    return json.loads(system.rsplit(_CATALOG_MARKER, 1)[1])


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
    'CHITCHAT_KINDS', 'COMMAND_TYPES', 'Command', 'CommandSlot', 'command_output_spec', 'commands_from_items',
    'command_system_message', 'model_commands', 'parse_commands', 'prompt_catalog', 'validate_commands',
]
